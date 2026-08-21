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
from .importer import import_contacts, preview_csv  # noqa: E402
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
                 q: str = "", limit: int = 500, offset: int = 0):
    where, params = [], []
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
        with_email = conn.execute(
            "SELECT COUNT(*) c FROM contacts WHERE email IS NOT NULL AND email<>''"
        ).fetchone()["c"]
        batches = [dict(r) for r in conn.execute(
            "SELECT * FROM import_batches ORDER BY id DESC")]
    return {
        "total": total, "with_email": with_email,
        "pct_with_email": round(with_email / total * 100, 1) if total else 0.0,
        "by_status": by_status, "by_source": by_source, "by_ghl": by_ghl,
        "batches": batches,
        "providers": [{"name": p.name, "enabled": p.enabled} for p in build_chain()],
        "ghl_configured": bool(__import__("os").getenv("GHL_API_TOKEN")
                               and __import__("os").getenv("GHL_LOCATION_ID")),
    }


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
    return {"pending": len(enrichment.pending_contacts())}


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
