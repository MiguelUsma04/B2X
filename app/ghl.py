"""Cliente de GoHighLevel API v2 — upsert de contactos.

Doc: https://marketplace.gohighlevel.com/docs/ghl/contacts/upsert-contact/
  POST https://services.leadconnectorhq.com/contacts/upsert
  Headers: Authorization: Bearer <PIT>, Version: 2021-07-28, Content-Type: application/json
  Body: locationId (requerido), firstName, lastName, email, tags[],
        customFields[{key, fieldValue}]
El upsert respeta la config 'Allow Duplicate Contact' del sub-account.
"""
import json
import os

import httpx

from .db import get_db

BASE = "https://services.leadconnectorhq.com"
UPSERT_URL = f"{BASE}/contacts/upsert"
PIPELINES_URL = f"{BASE}/opportunities/pipelines"
OPPORTUNITY_URL = f"{BASE}/opportunities/"
API_VERSION = "2021-07-28"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('GHL_API_TOKEN', '')}",
        "Version": API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def build_payload(contact: dict, tag: str | None = None) -> dict:
    location_id = os.getenv("GHL_LOCATION_ID", "")
    tags = [t.strip() for t in (tag or os.getenv("GHL_DEFAULT_TAG", "")).split(",") if t.strip()]

    custom_fields = []
    for key, value in (
        ("company_name", contact.get("company_name")),
        ("job_title", contact.get("job_title")),
        ("company_domain", contact.get("company_domain")),
        ("email_source", contact.get("email_source")),
        ("import_batch_id", contact.get("import_batch_id")),
    ):
        if value not in (None, ""):
            custom_fields.append({"key": key, "fieldValue": str(value)})

    payload = {
        "locationId": location_id,
        "firstName": contact.get("first_name") or "",
        "lastName": contact.get("last_name") or "",
        "name": contact.get("full_name") or "",
        "email": contact.get("email"),
        "source": "B2X",
        "tags": tags,
        "customFields": custom_fields,
    }
    if contact.get("company_name"):
        payload["companyName"] = contact["company_name"]
    if contact.get("phone"):
        payload["phone"] = contact["phone"]
    if contact.get("linkedin_url"):
        payload["website"] = contact["linkedin_url"]
    return {k: v for k, v in payload.items() if v not in (None, "")}


async def send_contacts(contact_ids: list[int], tag: str | None = None) -> dict:
    """Envía contactos a GHL uno por uno. Sin reintento automático: si falla,
    queda en ghl_status='error' y el usuario decide reintentar."""
    if not os.getenv("GHL_API_TOKEN"):
        return {"error": "Falta GHL_API_TOKEN en el .env", "sent": 0, "failed": 0, "results": []}
    if not os.getenv("GHL_LOCATION_ID"):
        return {"error": "Falta GHL_LOCATION_ID en el .env", "sent": 0, "failed": 0, "results": []}

    placeholders = ",".join("?" * len(contact_ids))
    with get_db() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM contacts WHERE id IN ({placeholders})", contact_ids)]

    sent = failed = skipped = opportunities = 0
    results = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for contact in rows:
            # Alcanza con email O teléfono personal: un contacto con celular
            # se puede trabajar por llamada o WhatsApp desde el CRM.
            has_phone = (contact.get("phone")
                         and contact.get("phone_type") == "personal")
            if not contact.get("email") and not has_phone:
                skipped += 1
                results.append({"id": contact["id"], "status": "skipped",
                                "message": "Sin email ni celular — no se envía a GHL."})
                continue

            payload = build_payload(contact, tag)
            try:
                resp = await client.post(UPSERT_URL, json=payload, headers=_headers())
                try:
                    body = resp.json()
                except Exception:
                    body = {"raw_text": resp.text[:1000]}

                if resp.status_code in (200, 201):
                    ghl_contact = body.get("contact") or {}
                    ghl_id = ghl_contact.get("id") or body.get("id")

                    # El contacto ya está en el CRM; si la oportunidad falla,
                    # se reporta pero el contacto sigue contando como enviado.
                    opp = {"ok": False, "id": None, "error": None}
                    if ghl_id:
                        opp = await create_opportunity(client, contact, ghl_id)
                    if opp["ok"]:
                        opportunities += 1

                    with get_db() as conn:
                        conn.execute(
                            """UPDATE contacts SET ghl_status='sent', ghl_contact_id=?,
                               ghl_opportunity_id=?, ghl_error_message=?,
                               updated_at=datetime('now')
                               WHERE id=?""",
                            (ghl_id, opp["id"], opp["error"], contact["id"]))
                    sent += 1
                    results.append({"id": contact["id"], "status": "sent",
                                    "ghl_contact_id": ghl_id,
                                    "opportunity_id": opp["id"],
                                    "opportunity_error": opp["error"],
                                    "new": body.get("new")})
                else:
                    msg = (body.get("message") or body.get("error")
                           or json.dumps(body)[:300] or f"HTTP {resp.status_code}")
                    if isinstance(msg, list):
                        msg = "; ".join(str(m) for m in msg)
                    msg = f"HTTP {resp.status_code}: {msg}"[:500]
                    with get_db() as conn:
                        conn.execute(
                            """UPDATE contacts SET ghl_status='error', ghl_error_message=?,
                               updated_at=datetime('now') WHERE id=?""", (msg, contact["id"]))
                    failed += 1
                    results.append({"id": contact["id"], "status": "error", "message": msg})
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"[:500]
                with get_db() as conn:
                    conn.execute(
                        """UPDATE contacts SET ghl_status='error', ghl_error_message=?,
                           updated_at=datetime('now') WHERE id=?""", (msg, contact["id"]))
                failed += 1
                results.append({"id": contact["id"], "status": "error", "message": msg})

    return {"sent": sent, "failed": failed, "skipped": skipped,
            "opportunities": opportunities,
            "pipeline_configured": bool(os.getenv("GHL_PIPELINE_ID")),
            "results": results}


# ------------------------------------------------------------------ embudos
async def list_pipelines() -> dict:
    """Trae los embudos del sub-account con sus etapas."""
    location_id = os.getenv("GHL_LOCATION_ID", "")
    if not os.getenv("GHL_API_TOKEN") or not location_id:
        return {"error": "Falta GHL_API_TOKEN o GHL_LOCATION_ID en el .env",
                "pipelines": []}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(PIPELINES_URL, headers=_headers(),
                                    params={"locationId": location_id})
            body = resp.json() if resp.content else {}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:300], "pipelines": []}

    if resp.status_code != 200:
        msg = body.get("message") or f"HTTP {resp.status_code}"
        if isinstance(msg, list):
            msg = "; ".join(str(m) for m in msg)
        return {"error": str(msg)[:300], "pipelines": []}

    pipelines = []
    for p in body.get("pipelines") or []:
        pipelines.append({
            "id": p.get("id"),
            "name": p.get("name"),
            "stages": [{"id": st.get("id"), "name": st.get("name")}
                       for st in (p.get("stages") or [])],
        })
    return {"pipelines": pipelines}


async def create_opportunity(client, contact: dict, ghl_contact_id: str) -> dict:
    """Crea la oportunidad del contacto en el embudo configurado.

    Devuelve {"ok": bool, "id": str|None, "error": str|None}. Un fallo acá no
    invalida el contacto: ya quedó creado en el CRM.
    """
    pipeline_id = os.getenv("GHL_PIPELINE_ID", "")
    stage_id = os.getenv("GHL_STAGE_ID", "")
    if not pipeline_id:
        return {"ok": False, "id": None, "error": None}  # no configurado: se omite

    name = contact.get("company_name") or contact.get("full_name") or "Lead"
    payload = {
        "pipelineId": pipeline_id,
        "locationId": os.getenv("GHL_LOCATION_ID", ""),
        "name": name,
        "status": os.getenv("GHL_OPPORTUNITY_STATUS", "open"),
        "contactId": ghl_contact_id,
    }
    if stage_id:
        payload["pipelineStageId"] = stage_id

    try:
        resp = await client.post(OPPORTUNITY_URL, json=payload, headers=_headers())
        body = resp.json() if resp.content else {}
    except Exception as exc:
        return {"ok": False, "id": None, "error": f"{type(exc).__name__}: {exc}"[:300]}

    if resp.status_code in (200, 201):
        opp = body.get("opportunity") or body
        return {"ok": True, "id": opp.get("id"), "error": None}

    msg = body.get("message") or body.get("error") or f"HTTP {resp.status_code}"
    if isinstance(msg, list):
        msg = "; ".join(str(m) for m in msg)
    return {"ok": False, "id": None, "error": f"Oportunidad: {msg}"[:300]}
