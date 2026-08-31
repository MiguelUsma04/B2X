"""B2X — app interna de prospección B2B. FastAPI + SQLite."""
import asyncio
import datetime
import json
import os
from pathlib import Path

from dotenv import dotenv_values
import secrets
from urllib.parse import quote

import httpx
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
from .importer import (delete_batch, import_contacts,      # noqa: E402
                       import_places, preview_csv)
from . import ai, auth, enrichment, ghl, mailer, places   # noqa: E402
from .providers import build_chain       # noqa: E402

app = FastAPI(title="B2X", docs_url="/api/docs")
init_db()

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

# Todo pasa por el chequeo de sesión (ver app/auth.py).
app.middleware("http")(auth.auth_middleware)


@app.get("/login", response_class=HTMLResponse)
def login_page():
    """La pantalla se arma según lo que esté configurado: no tiene sentido
    ofrecer un botón de Google sin credenciales, ni pedir una contraseña que
    nadie definió."""
    html = (BASE_DIR / "templates" / "login.html").read_text(encoding="utf-8")
    if not auth.google_configured():
        html = _sacar_bloque(html, "GOOGLE")
    if not auth.password():
        html = _sacar_bloque(html, "PASSWORD")
    return html.replace("{{DOMINIO}}", auth.dominio_permitido() or "tu equipo")


def _sacar_bloque(html: str, nombre: str) -> str:
    inicio, fin = f"<!--{nombre}-->", f"<!--/{nombre}-->"
    while inicio in html and fin in html:
        a, b = html.index(inicio), html.index(fin) + len(fin)
        html = html[:a] + html[b:]
    return html


@app.post("/api/login")
def do_login(password: str = Form(...)):
    if not auth.check_password(password):
        return RedirectResponse("/login?error=1", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    auth.issue_cookie(resp)
    return resp


# ------------------------------------------------------------ login Google
@app.get("/auth/google/start")
def google_start(request: Request):
    """Manda al usuario a Google con un state firmado para evitar CSRF."""
    if not auth.google_configured():
        return RedirectResponse("/login?error=nogoogle", status_code=303)
    estado, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(16)
    resp = RedirectResponse(auth.url_de_google(request, estado, nonce), status_code=303)
    auth.guardar_estado(resp, estado, nonce)
    return resp


@app.get("/auth/google/callback", name="google_callback")
async def google_callback(request: Request, code: str = "", state: str = "",
                          error: str = ""):
    """Vuelta de Google: se canjea el código y se decide si esa cuenta entra."""
    if error or not code:
        return RedirectResponse(f"/login?error={quote(error or 'cancelado')}",
                                status_code=303)

    guardado = auth.leer_estado(request)
    # Sin este chequeo, cualquiera podría hacerle abrir a un usuario un
    # callback armado por otro y dejarlo logueado con una cuenta ajena.
    if not guardado or not secrets.compare_digest(guardado.get("estado", ""), state):
        return RedirectResponse("/login?error=estado", status_code=303)

    datos = {
        "code": code,
        "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
        "redirect_uri": auth.redirect_uri(request),
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(auth.GOOGLE_TOKEN, data=datos)
        cuerpo = r.json()
    except Exception as exc:
        return RedirectResponse(f"/login?error={quote(type(exc).__name__)}",
                                status_code=303)

    if r.status_code != 200 or not cuerpo.get("id_token"):
        detalle = cuerpo.get("error_description") or cuerpo.get("error") or "token"
        return RedirectResponse(f"/login?error={quote(str(detalle)[:120])}",
                                status_code=303)

    try:
        email = auth.validar_id_token(auth.payload_del_id_token(cuerpo["id_token"]),
                                      guardado.get("nonce", ""))
    except PermissionError as exc:
        return RedirectResponse(f"/login?error={quote(str(exc)[:160])}", status_code=303)
    except Exception:
        return RedirectResponse("/login?error=token", status_code=303)

    resp = RedirectResponse("/", status_code=303)
    auth.issue_cookie(resp, email)
    resp.delete_cookie(auth.ESTADO_COOKIE, path="/")
    return resp


@app.get("/api/me")
def api_me(request: Request):
    """Quién está usando la app, para mostrarlo en el encabezado."""
    s = auth.sesion(request) or {}
    return {"email": s.get("email"),
            "google": auth.google_configured(),
            "domain": auth.dominio_permitido()}


@app.post("/api/logout")
def do_logout():
    resp = RedirectResponse("/login", status_code=303)
    auth.clear_cookie(resp)
    return resp

# Caché en memoria del archivo subido, entre la vista previa y la confirmación.
_PENDING_UPLOAD: dict = {}
# La búsqueda de Maps se guarda acá entre el "buscar" y el "guardar": repetir
# la consulta para confirmar la cobraría dos veces.
_PENDING_PLACES: dict = {}


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
    HAS_PHONE = "phone IS NOT NULL AND phone <> '' AND phone_type IN ('personal','whatsapp')"
    HAS_ANY_PHONE = "phone IS NOT NULL AND phone <> ''"
    # phone_type quedó en NULL en los contactos cargados antes de que existiera
    # la columna. 'phone_type <> personal' daría NULL para ellos y los dejaría
    # fuera de todos los cortes: IS NOT compara sin arrastrar el NULL.
    NOT_PERSONAL = ("phone_type IS NOT 'personal' "
                    "AND phone_type IS NOT 'whatsapp'")
    REACH_SQL = {
        "contactable": f"(({HAS_EMAIL}) OR ({HAS_ANY_PHONE}))",
        "email":       f"({HAS_EMAIL})",
        "phone":       f"({HAS_PHONE})",
        "switchboard": f"(NOT ({HAS_EMAIL}) AND ({HAS_ANY_PHONE}) AND {NOT_PERSONAL})",
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
        HAS_PHONE = "phone IS NOT NULL AND phone <> '' AND phone_type IN ('personal','whatsapp')"
        HAS_ANY_PHONE = "phone IS NOT NULL AND phone <> ''"
        NOT_PERSONAL = ("phone_type IS NOT 'personal' "
                        "AND phone_type IS NOT 'whatsapp'")
        counts = conn.execute(f"""
            SELECT
              SUM(CASE WHEN {HAS_EMAIL} THEN 1 ELSE 0 END)                          AS with_email,
              SUM(CASE WHEN {HAS_PHONE} THEN 1 ELSE 0 END)                          AS with_phone,
              SUM(CASE WHEN {HAS_EMAIL} AND {HAS_PHONE} THEN 1 ELSE 0 END)          AS with_both,
              SUM(CASE WHEN {HAS_EMAIL} OR  {HAS_ANY_PHONE} THEN 1 ELSE 0 END)      AS contactable,
              SUM(CASE WHEN NOT ({HAS_EMAIL}) AND ({HAS_ANY_PHONE})
                        AND {NOT_PERSONAL} THEN 1 ELSE 0 END)                       AS only_switchboard,
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
        "providers": ([{"name": p.name, "enabled": p.enabled} for p in build_chain()]
                      + [{"name": "maps", "enabled": places.configured()},
                         {"name": "IA", "enabled": ai.configured()}]),
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


# ------------------------------------------------------------- Google Maps
# Tramo gratis y precio del SKU que usa nuestra búsqueda: "Text Search
# Enterprise + Atmosphere" (pide teléfono, sitio y calificación).
# https://developers.google.com/maps/billing-and-pricing/pricing
PLACES_FREE = 1000
PLACES_USD_1000 = 40.0


def _places_usage() -> dict:
    """Cuánto se consumió este mes calendario.

    Es una estimación propia: cuenta lo que esta app le pidió a Google. La
    cifra que factura Google está en su consola, y puede diferir si la misma
    key se usa desde otro lado.
    """
    with get_db() as conn:
        r = conn.execute("""
            SELECT COALESCE(SUM(requests), 0) AS consultas,
                   COALESCE(SUM(results), 0)  AS negocios,
                   COUNT(*)                   AS busquedas
              FROM places_usage
             WHERE strftime('%Y-%m', timestamp, 'localtime')
                   = strftime('%Y-%m', 'now', 'localtime')""").fetchone()

    consultas = r["consultas"] or 0
    cobrables = max(0, consultas - PLACES_FREE)
    hoy = datetime.date.today()
    # El tramo gratis se renueva el 1: no son 30 días desde la primera consulta.
    proximo = (hoy.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
    return {
        "month": hoy.strftime("%Y-%m"),
        "resets_on": proximo.isoformat(),
        "searches": r["busquedas"] or 0,
        "requests": consultas,
        "results": r["negocios"] or 0,
        "free_limit": PLACES_FREE,
        "remaining": max(0, PLACES_FREE - consultas),
        "billable": cobrables,
        "estimated_cost": round(cobrables * PLACES_USD_1000 / 1000, 2),
        "usd_per_request": round(PLACES_USD_1000 / 1000, 3),
    }


@app.get("/api/places/usage")
def api_places_usage():
    return _places_usage()
@app.post("/api/places/search")
async def api_places_search(query: str = Form(...), max_results: str = Form("20")):
    """Busca negocios por ubicación. No guarda nada: primero se miran.

    Repetir la misma búsqueda tiene que traer negocios nuevos. Google contesta
    siempre lo mismo para el mismo texto, así que se le saltean los que ya
    aparecieron antes —o que ya están en la base— y se piden más páginas.
    """
    n = int(max_results) if str(max_results).strip().isdigit() else 20
    clave = places.query_key(query)

    with get_db() as conn:
        ya_vistos = {row["p"] for row in conn.execute(
            "SELECT lower(place_id) p FROM places_seen WHERE query_key=?", (clave,))}
        en_base = {row["p"] for row in conn.execute(
            "SELECT lower(place_id) p FROM contacts WHERE place_id IS NOT NULL")}

    r = await places.search(query, max_results=n, skip_ids=ya_vistos | en_base)
    if r.get("error") and not r["places"]:
        return JSONResponse({"error": r["error"], "places": []}, status_code=400)

    # Se anota lo que Google efectivamente respondió: las páginas que
    # fallaron no se facturan, así que tampoco se cuentan.
    if r.get("pages"):
        with get_db() as conn:
            conn.execute(
                "INSERT INTO places_usage (query, requests, results) VALUES (?,?,?)",
                (query.strip()[:200], r["pages"], len(r["places"])))
            # Todo lo que vino queda registrado contra esta búsqueda, se guarde
            # o no: la próxima vez arranca donde terminó esta.
            conn.executemany(
                "INSERT OR IGNORE INTO places_seen (query_key, place_id) VALUES (?,?)",
                [(clave, p["place_id"]) for p in (r["places"] + r.get("seen", []))
                 if p.get("place_id")])

    _PENDING_PLACES.clear()
    _PENDING_PLACES.update({"query": query.strip(), "places": r["places"]})

    conocidos = r.get("seen", [])
    return {
        "query": query.strip(),
        "total": len(r["places"]),
        "with_site": sum(1 for p in r["places"] if p.get("domain")),
        "with_phone": sum(1 for p in r["places"] if p.get("phone")),
        # Cuántos salteó por conocidos, y de esos cuántos ya son contactos.
        "already": len(conocidos),
        "already_saved": sum(1 for p in conocidos
                             if (p.get("place_id") or "").lower() in en_base),
        "seen": conocidos,
        # Google se quedó sin resultados para este texto: pedirlo otra vez no
        # va a traer nada nuevo, hay que cambiar la búsqueda.
        "exhausted": bool(r.get("exhausted")) ,
        "repeat": bool(ya_vistos),
        "places": r["places"],
        "warning": r.get("error"),
        "requests": r.get("pages", 0),
        "usage": _places_usage(),
    }


@app.post("/api/places/import")
def api_places_import(icp_tag: str = Form("")):
    if not _PENDING_PLACES.get("places"):
        raise HTTPException(400, "No hay una búsqueda pendiente. Buscá de nuevo.")
    with get_db() as conn:
        resumen = import_places(conn, _PENDING_PLACES["query"],
                                _PENDING_PLACES["places"], icp_tag)
    _PENDING_PLACES.clear()
    return resumen


# ------------------------------------------------------- sitio web del negocio
@app.post("/api/website/start")
async def api_website_start(contact_ids: str = Form(...)):
    """Visita el sitio de los contactos marcados. Gratis: no usa proveedores."""
    if enrichment.WEB_PROGRESS.running:
        raise HTTPException(409, "Ya hay una lectura de sitios en curso.")
    try:
        ids = [int(i) for i in json.loads(contact_ids)]
    except Exception:
        raise HTTPException(400, "contact_ids debe ser un array JSON de enteros.")
    if not ids:
        raise HTTPException(400, "No se seleccionó ningún contacto.")

    con_sitio = enrichment.contacts_with_site(ids)
    if not con_sitio:
        return {"started": False,
                "message": "Ninguno de los marcados tiene sitio web para visitar."}
    asyncio.create_task(enrichment.run_website_scrape(ids))
    return {"started": True, "queued": len(con_sitio)}


@app.get("/api/website/progress")
def api_website_progress():
    return enrichment.WEB_PROGRESS.as_dict()


# --------------------------------------------------------------- correos
@app.on_event("startup")
async def _arrancar_goteo():
    """El goteo tiene que seguir solo: si la app se reinicia a mitad de una
    campaña, la cola sigue en la base y este obrero la retoma."""
    mailer.arrancar_worker()


@app.get("/api/mail/config")
def api_mail_config():
    cfg = mailer.get_config()
    cfg["variables"] = mailer.VARIABLES
    return cfg


@app.post("/api/mail/config")
def api_mail_config_save(host: str = Form(""), port: str = Form("587"),
                         username: str = Form(""), password: str = Form(""),
                         from_name: str = Form(""), from_email: str = Form(""),
                         security: str = Form("starttls")):
    if security not in ("starttls", "ssl", "none"):
        raise HTTPException(400, "Modo de seguridad desconocido.")
    return mailer.save_config({
        "host": host, "port": port, "username": username, "password": password,
        "from_name": from_name, "from_email": from_email, "security": security})


@app.post("/api/mail/test")
async def api_mail_test(to: str = Form(...)):
    """Manda una prueba a una casilla propia. Es el paso previo obligado:
    probar la configuración contra un cliente real no es una opción."""
    if "@" not in to:
        raise HTTPException(400, "Escribí una dirección válida.")
    r = await mailer.enviar(
        to, "Prueba de configuración — B2K",
        "Si estás leyendo esto, el servidor de salida quedó bien configurado.\n\n"
        "Este mensaje lo generó B2K desde la pantalla de correos.")
    return r


@app.post("/api/mail/preview")
def api_mail_preview(contact_ids: str = Form(...), subject: str = Form(""),
                     body: str = Form(""), repeat: str = Form("")):
    """Cómo le va a llegar a los primeros, y a cuántos se le va a escribir."""
    try:
        ids = [int(i) for i in json.loads(contact_ids)]
    except Exception:
        raise HTTPException(400, "contact_ids debe ser un array JSON de enteros.")

    repetir = str(repeat).lower() in ("1", "true", "yes", "on")
    destinos = mailer.contactos_enviables(ids, repetir)
    with get_db() as conn:
        marcados = len(ids)
        sin_email = conn.execute(
            f"""SELECT COUNT(*) n FROM contacts
                 WHERE id IN ({",".join("?" * len(ids))})
                   AND (email IS NULL OR email = '')""", ids).fetchone()["n"] if ids else 0

    muestras = [{
        "email": c["email"],
        "name": c.get("full_name"),
        "subject": mailer.render(subject, c),
        "body": mailer.render(body, c),
    } for c in destinos[:3]]

    return {
        "selected": marcados,
        "sendable": len(destinos),
        "no_email": sin_email,
        "already_written": marcados - sin_email - len(destinos),
        "unknown_vars": mailer.variables_desconocidas(subject + " " + body),
        "samples": muestras,
    }


@app.post("/api/mail/schedule")
def api_mail_schedule(contact_ids: str = Form(...), subject: str = Form(...),
                      body: str = Form(...), name: str = Form(""),
                      limit: str = Form(""), every_seconds: str = Form("180"),
                      jitter_seconds: str = Form("60"), daily_cap: str = Form("50"),
                      repeat: str = Form("")):
    """Arma la campaña y deja la cola lista. El obrero la va soltando."""
    activa = mailer.estado()
    if activa.get("campaign") and activa["campaign"]["status"] == "running" \
            and activa.get("pending"):
        raise HTTPException(409, "Ya hay un envío en curso. Pausalo o cancelalo antes.")

    try:
        ids = [int(i) for i in json.loads(contact_ids)]
    except Exception:
        raise HTTPException(400, "contact_ids debe ser un array JSON de enteros.")
    if not subject.strip() or not body.strip():
        raise HTTPException(400, "Falta el asunto o el cuerpo del correo.")
    if not mailer.get_config()["configured"]:
        raise HTTPException(400, "Configurá primero el servidor de salida.")

    def entero(v, x, minimo=0):
        try:
            return max(minimo, int(str(v).strip() or x))
        except ValueError:
            return x

    destinos = mailer.contactos_enviables(ids, str(repeat).lower() in ("1", "true", "on"))
    tope = entero(limit, 0)
    if tope:
        destinos = destinos[:tope]
    if not destinos:
        return {"started": False,
                "message": "Ninguno de los marcados tiene email o a todos ya se "
                           "les escribió."}

    r = mailer.crear_campania(
        name.strip(), subject, body, destinos,
        cada_segundos=entero(every_seconds, 180, 10),
        jitter=entero(jitter_seconds, 60),
        tope_diario=entero(daily_cap, 50))
    mailer.arrancar_worker()
    return {"started": True, **r}


@app.get("/api/mail/status")
def api_mail_status():
    return mailer.estado()


@app.post("/api/mail/control")
def api_mail_control(campaign_id: str = Form(...), action: str = Form(...)):
    acciones = {"pause": "paused", "resume": "running", "cancel": "cancelled"}
    if action not in acciones:
        raise HTTPException(400, "Acción desconocida.")
    try:
        cid = int(campaign_id)
    except ValueError:
        raise HTTPException(400, "campaign_id inválido.")
    return mailer.cambiar_estado(cid, acciones[action])


# ------------------------------------------------------------------ ficha IA
@app.post("/api/ai/start")
async def api_ai_start(contact_ids: str = Form(...), redo: str = Form("")):
    """Arma la ficha del negocio leyendo su sitio con IA. Cuesta por contacto."""
    if enrichment.AI_PROGRESS.running:
        raise HTTPException(409, "Ya hay un análisis en curso.")
    try:
        ids = [int(i) for i in json.loads(contact_ids)]
    except Exception:
        raise HTTPException(400, "contact_ids debe ser un array JSON de enteros.")
    if not ids:
        raise HTTPException(400, "No se seleccionó ningún contacto.")

    rehacer = str(redo).lower() in ("1", "true", "yes", "on")
    pendientes = enrichment.contacts_for_ai(ids, rehacer)
    if not pendientes:
        return {"started": False,
                "message": "Los marcados ya tienen ficha, o no tienen sitio web."}
    asyncio.create_task(enrichment.run_ai_profile(ids, rehacer))
    return {"started": True, "queued": len(pendientes)}


@app.get("/api/ai/progress")
def api_ai_progress():
    return enrichment.AI_PROGRESS.as_dict()


@app.get("/api/ai/usage")
def api_ai_usage():
    """Cuánto se leyó con IA este mes, en fichas y en tokens."""
    with get_db() as conn:
        r = conn.execute("""
            SELECT COUNT(*) AS fichas,
                   COALESCE(SUM(tokens_in), 0)  AS entrada,
                   COALESCE(SUM(tokens_out), 0) AS salida
              FROM ai_usage
             WHERE ok = 1
               AND strftime('%Y-%m', timestamp, 'localtime')
                   = strftime('%Y-%m', 'now', 'localtime')""").fetchone()
    return {"month": datetime.date.today().strftime("%Y-%m"),
            "profiles": r["fichas"] or 0,
            "tokens_in": r["entrada"] or 0,
            "tokens_out": r["salida"] or 0,
            "model": ai.modelo(),
            "configured": ai.configured()}


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
