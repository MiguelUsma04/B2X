"""B2X — app interna de prospección B2B. FastAPI + SQLite."""
import asyncio
import json
import os
from pathlib import Path

from dotenv import dotenv_values
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent


def _load_env() -> None:
    """Carga el .env dándole prioridad, pero sin dejar que una clave vacía
    borre un valor real del entorno.

    Importa en el servidor: ahí las variables llegan por systemd, y una línea
    vacía en el .env (APP_PASSWORD=) apagaría el login sin previo aviso.
    """
    values = dotenv_values(BASE_DIR.parent / ".env")
    for key, value in values.items():
        if value:                      # el .env manda cuando trae algo
            os.environ[key] = value
        elif key not in os.environ:    # vacío: solo si no había nada
            os.environ[key] = ""


_load_env()

from .db import get_db, init_db          # noqa: E402
from .importer import delete_batch, import_contacts, preview_csv  # noqa: E402
from . import auth, enrichment, ghl     # noqa: E402
from .providers import build_chain       # noqa: E402

app = FastAPI(title="B2X", docs_url="/api/docs")
init_db()

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

# Todo pasa por el chequeo de sesión (ver app/auth.py).
app.middleware("http")(auth.auth_middleware)


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return (BASE_DIR / "templates" / "login.html").read_text(encoding="utf-8")


@app.post("/api/login")
def do_login(password: str = Form(...)):
    if not auth.check_password(password):
        return RedirectResponse("/login?error=1", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    auth.issue_cookie(resp)
    return resp


@app.post("/api/logout")
def do_logout():
    resp = RedirectResponse("/login", status_code=303)
    auth.clear_cookie(resp)
    return resp

# Caché en memoria del archivo subido, entre la vista previa y la confirmación.
_PENDING_UPLOAD: dict = {}


@app.get("/", response_class=HTMLResponse)
def index():
    return (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- importación
@app.post("/api/import/preview")
async def api_preview(file: UploadFile = File(...)):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "El archivo está vacío.")
    try:
        result = preview_csv(raw)
    except Exception as exc:
        raise HTTPException(400, f"No se pudo leer el CSV: {exc}")
    _PENDING_UPLOAD.clear()
    _PENDING_UPLOAD["filename"] = file.filename
    _PENDING_UPLOAD["raw"] = raw
    result["filename"] = file.filename
    return result


@app.post("/api/import/confirm")
async def api_confirm(icp_tag: str = Form("")):
    if "raw" not in _PENDING_UPLOAD:
        raise HTTPException(400, "No hay ningún archivo pendiente. Subí el CSV de nuevo.")
    try:
        with get_db() as conn:
            summary = import_contacts(conn, _PENDING_UPLOAD["filename"],
                                      _PENDING_UPLOAD["raw"], icp_tag)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _PENDING_UPLOAD.clear()
    return summary


# ------------------------------------------------------------------ contactos
@app.get("/api/contacts")
def api_contacts(email_status: str = "", email_source: str = "",
                 ghl_status: str = "", import_batch_id: str = "",
                 reach: str = "", q: str = "", limit: int = 500, offset: int = 0):
    # "Con celular" sigue significando número directo; "contactable" es más
    # amplio: cualquier teléfono sirve para enviarlo al CRM y trabajarlo.
    HAS_EMAIL = "email IS NOT NULL AND email <> ''"
    HAS_PHONE = "phone IS NOT NULL AND phone <> '' AND phone_type = 'personal'"
    HAS_ANY_PHONE = "phone IS NOT NULL AND phone <> ''"
    REACH_SQL = {
        "contactable": f"(({HAS_EMAIL}) OR ({HAS_ANY_PHONE}))",
        "email":       f"({HAS_EMAIL})",
        "phone":       f"({HAS_PHONE})",
        "switchboard": f"(NOT ({HAS_EMAIL}) AND ({HAS_ANY_PHONE}) AND NOT ({HAS_PHONE}))",
        "both":        f"(({HAS_EMAIL}) AND ({HAS_PHONE}))",
        "none":        f"(NOT ({HAS_EMAIL}) AND NOT ({HAS_ANY_PHONE}))",
    }
    where, params = [], []
    if reach in REACH_SQL:
        where.append(REACH_SQL[reach])
    if email_status:
        where.append("email_status=?"); params.append(email_status)
    if email_source:
        where.append("email_source=?"); params.append(email_source)
    if ghl_status:
        where.append("ghl_status=?"); params.append(ghl_status)
    if import_batch_id:
        where.append("import_batch_id=?"); params.append(import_batch_id)
    if q:
        where.append("(full_name LIKE ? OR company_name LIKE ? OR email LIKE ?)")
        params += [f"%{q}%"] * 3
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    with get_db() as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM contacts{clause}", params).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM contacts{clause} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset]).fetchall()
        ids = [r["id"] for r in conn.execute(
            f"SELECT id FROM contacts{clause}", params)]
    return {"total": total, "contacts": [dict(r) for r in rows], "all_ids": ids}


@app.get("/api/contacts/{contact_id}")
def api_contact_detail(contact_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Contacto no encontrado.")
        logs = conn.execute(
            "SELECT * FROM enrichment_log WHERE contact_id=? ORDER BY id",
            (contact_id,)).fetchall()

    def parse(v):
        if not v:
            return None
        try:
            return json.loads(v)
        except Exception:
            return v

    return {
        "contact": dict(row),
        "logs": [{**dict(l),
                  "request_payload": parse(l["request_payload"]),
                  "response_payload": parse(l["response_payload"])} for l in logs],
    }


@app.get("/api/metrics")
def api_metrics():
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM contacts").fetchone()["c"]
        by_status = {r["email_status"]: r["c"] for r in conn.execute(
            "SELECT email_status, COUNT(*) c FROM contacts GROUP BY email_status")}
        by_source = {r["email_source"] or "sin_fuente": r["c"] for r in conn.execute(
            "SELECT email_source, COUNT(*) c FROM contacts GROUP BY email_source")}
        by_ghl = {r["ghl_status"]: r["c"] for r in conn.execute(
            "SELECT ghl_status, COUNT(*) c FROM contacts GROUP BY ghl_status")}
        # "Contactable" = tiene email o algún teléfono. El conmutador de la
        # empresa vale menos que el celular —por eso se cuenta aparte— pero
        # igual permite trabajar el contacto, así que suma y se envía al CRM.
        HAS_EMAIL = "email IS NOT NULL AND email <> ''"
        HAS_PHONE = "phone IS NOT NULL AND phone <> '' AND phone_type = 'personal'"
        HAS_ANY_PHONE = "phone IS NOT NULL AND phone <> ''"
        counts = conn.execute(f"""
            SELECT
              SUM(CASE WHEN {HAS_EMAIL} THEN 1 ELSE 0 END)                          AS with_email,
              SUM(CASE WHEN {HAS_PHONE} THEN 1 ELSE 0 END)                          AS with_phone,
              SUM(CASE WHEN {HAS_EMAIL} AND {HAS_PHONE} THEN 1 ELSE 0 END)          AS with_both,
              SUM(CASE WHEN {HAS_EMAIL} OR  {HAS_ANY_PHONE} THEN 1 ELSE 0 END)      AS contactable,
              SUM(CASE WHEN NOT ({HAS_EMAIL}) AND ({HAS_ANY_PHONE})
                        AND NOT ({HAS_PHONE}) THEN 1 ELSE 0 END)                    AS only_switchboard,
              SUM(CASE WHEN NOT ({HAS_EMAIL}) AND NOT ({HAS_ANY_PHONE})
                        AND mobile_available = 1 THEN 1 ELSE 0 END)                 AS mobile_available
            FROM contacts""").fetchone()
        with_email = counts["with_email"] or 0
        with_phone = counts["with_phone"] or 0
        with_both = counts["with_both"] or 0
        contactable = counts["contactable"] or 0
        only_switchboard = counts["only_switchboard"] or 0
        mobile_avail = counts["mobile_available"] or 0
        batches = [dict(r) for r in conn.execute(
            "SELECT * FROM import_batches ORDER BY id DESC")]
    return {
        "total": total, "with_email": with_email,
        "with_phone": with_phone, "with_both": with_both,
        "contactable": contactable, "only_switchboard": only_switchboard,
        "mobile_available": mobile_avail,
        "pct_with_email": round(with_email / total * 100, 1) if total else 0.0,
        "pct_contactable": round(contactable / total * 100, 1) if total else 0.0,
        "by_status": by_status, "by_source": by_source, "by_ghl": by_ghl,
        "batches": batches,
        "providers": [{"name": p.name, "enabled": p.enabled} for p in build_chain()],
        "ghl_configured": bool(__import__("os").getenv("GHL_API_TOKEN")
                               and __import__("os").getenv("GHL_LOCATION_ID")),
    }


@app.post("/api/batches/{batch_id}/delete")
def api_delete_batch(batch_id: int, delete_contacts: str = Form("")):
    """Borra una carga. Sin delete_contacts solo se quita del historial."""
    wipe = str(delete_contacts).lower() in ("1", "true", "yes", "on")
    try:
        with get_db() as conn:
            return delete_batch(conn, batch_id, wipe)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


# -------------------------------------------------------------- enriquecimiento
@app.post("/api/enrich/start")
async def api_enrich_start(limit: str = Form(""), batch_id: str = Form("")):
    if enrichment.PROGRESS.running:
        raise HTTPException(409, "Ya hay un enriquecimiento en curso.")
    lim = int(limit) if str(limit).strip().isdigit() else None
    bid = int(batch_id) if str(batch_id).strip().isdigit() else None
    pending = len(enrichment.pending_contacts(limit=lim, batch_id=bid))
    if not pending:
        return {"started": False, "message": "No hay contactos pendientes."}
    asyncio.create_task(enrichment.run_enrichment(limit=lim, batch_id=bid))
    return {"started": True, "queued": pending}


@app.get("/api/enrich/progress")
def api_enrich_progress():
    return enrichment.PROGRESS.as_dict()


@app.get("/api/enrich/pending-count")
def api_pending_count():
    nf = enrichment.count_not_found()
    return {"pending": len(enrichment.pending_contacts()),
            "not_found": nf["total"],
            "not_found_new": nf["nuevos"],
            "not_found_retried": nf["reintentados"]}


@app.post("/api/enrich/retry-not-found")
def api_retry_not_found(batch_id: str = Form(""), include_retried: str = Form("")):
    """Vuelve a poner en pendiente los que no se encontraron.

    Por defecto solo los probados una vez; con include_retried también los que
    ya pasaron por la cascada más de una vez.
    """
    bid = int(batch_id) if str(batch_id).strip().isdigit() else None
    todos = str(include_retried).lower() in ("1", "true", "yes", "on")
    n = (enrichment.reset_not_found(bid) if todos
         else enrichment.reset_not_found_new_only(bid))
    return {"reset": n}


@app.post("/api/mobile/start")
async def api_mobile_start(contact_ids: str = Form(...)):
    """Búsqueda de móviles: cuesta 10 créditos por contacto, así que va
    siempre sobre una selección explícita, nunca sobre toda la base."""
    if enrichment.MOBILE_PROGRESS.running:
        raise HTTPException(409, "Ya hay una búsqueda de teléfonos en curso.")
    try:
        ids = [int(i) for i in json.loads(contact_ids)]
    except Exception:
        raise HTTPException(400, "contact_ids debe ser un array JSON de enteros.")
    if not ids:
        raise HTTPException(400, "No se seleccionó ningún contacto.")

    pending = enrichment.contacts_without_phone(ids)
    if not pending:
        return {"started": False,
                "message": "Los contactos seleccionados ya tienen teléfono."}
    asyncio.create_task(enrichment.run_mobile_search(ids))
    return {"started": True, "queued": len(pending)}


@app.get("/api/mobile/progress")
def api_mobile_progress():
    return enrichment.MOBILE_PROGRESS.as_dict()


# ------------------------------------------------------------------------ GHL
@app.get("/api/ghl/pipelines")
async def api_ghl_pipelines():
    """Lista los embudos del sub-account para elegir en la UI."""
    return await ghl.list_pipelines()


@app.get("/api/ghl/settings")
def api_ghl_settings():
    return {"pipeline_id": os.getenv("GHL_PIPELINE_ID", ""),
            "stage_id": os.getenv("GHL_STAGE_ID", ""),
            "default_tag": os.getenv("GHL_DEFAULT_TAG", "")}


@app.post("/api/ghl/settings")
def api_ghl_settings_save(pipeline_id: str = Form(""), stage_id: str = Form("")):
    """Guarda el embudo elegido.

    Se escribe en el entorno del proceso y en el .env cuando existe, para que
    sobreviva a un reinicio. En Docker el .env no está montado: ahí el valor
    dura lo que dure el contenedor, y conviene fijarlo en docker-compose.
    """
    os.environ["GHL_PIPELINE_ID"] = pipeline_id.strip()
    os.environ["GHL_STAGE_ID"] = stage_id.strip()

    persisted = False
    env_path = BASE_DIR.parent / ".env"
    if env_path.exists():
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
            out, seen = [], set()
            for line in lines:
                key = line.split("=", 1)[0].strip() if "=" in line else ""
                if key == "GHL_PIPELINE_ID":
                    out.append(f"GHL_PIPELINE_ID={pipeline_id.strip()}"); seen.add(key)
                elif key == "GHL_STAGE_ID":
                    out.append(f"GHL_STAGE_ID={stage_id.strip()}"); seen.add(key)
                else:
                    out.append(line)
            if "GHL_PIPELINE_ID" not in seen:
                out.append(f"GHL_PIPELINE_ID={pipeline_id.strip()}")
            if "GHL_STAGE_ID" not in seen:
                out.append(f"GHL_STAGE_ID={stage_id.strip()}")
            env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
            persisted = True
        except OSError:
            persisted = False

    return {"saved": True, "persisted_to_env": persisted,
            "pipeline_id": pipeline_id.strip(), "stage_id": stage_id.strip()}


@app.post("/api/ghl/send")
async def api_ghl_send(contact_ids: str = Form(...), tag: str = Form("")):
    try:
        ids = [int(i) for i in json.loads(contact_ids)]
    except Exception:
        raise HTTPException(400, "contact_ids debe ser un array JSON de enteros.")
    if not ids:
        raise HTTPException(400, "No se seleccionó ningún contacto.")
    result = await ghl.send_contacts(ids, tag or None)
    if result.get("error"):
        return JSONResponse(result, status_code=400)
    return result
