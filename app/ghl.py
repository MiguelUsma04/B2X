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

UPSERT_URL = "https://services.leadconnectorhq.com/contacts/upsert"
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

    sent = failed = skipped = 0
    results = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for contact in rows:
            if not contact.get("email"):
                skipped += 1
                results.append({"id": contact["id"], "status": "skipped",
                                "message": "Sin email — no se envía a GHL."})
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
                    with get_db() as conn:
                        conn.execute(
                            """UPDATE contacts SET ghl_status='sent', ghl_contact_id=?,
                               ghl_error_message=NULL, updated_at=datetime('now')
                               WHERE id=?""", (ghl_id, contact["id"]))
                    sent += 1
                    results.append({"id": contact["id"], "status": "sent",
                                    "ghl_contact_id": ghl_id,
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

    return {"sent": sent, "failed": failed, "skipped": skipped, "results": results}
