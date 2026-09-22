"""B2X — app interna de prospección B2B. FastAPI + SQLite."""
import asyncio
import base64
import datetime
import tempfile
import html as html_mod
import json
import os
import re
from pathlib import Path

from dotenv import dotenv_values
import secrets
from urllib.parse import quote

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, Response)
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

from . import db as db_mod              # noqa: E402
from .db import get_db, init_db          # noqa: E402
from .importer import (delete_batch, import_contacts,      # noqa: E402
                       import_places, preview_csv)
from . import (ai, auth, dnscheck, duplicados, enrichment,  # noqa: E402
               ghl, kommo, mailer, places, redactor, respaldo)
from .providers import build_chain       # noqa: E402

app = FastAPI(title="B2X", docs_url="/api/docs")
init_db()

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """El navegador lo pide solo, sin mirar las etiquetas del HTML."""
    return FileResponse(BASE_DIR / "static" / "img" / "favicon.ico")

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


def _quien(request: Request) -> str:
    """Quién está haciendo esto, para el registro."""
    return ((auth.sesion(request) or {}).get("email") or "").strip() or "el equipo"


def anotar(request: Request, accion: str, detalle: str = "",
           cuantos: int | None = None) -> None:
    """Deja constancia de una acción que gasta plata o sale hacia afuera.

    No se anota todo: un registro de todo no lo mira nadie. Se anotan las
    diez cosas por las que alguien preguntaría después — quién mandó ese
    correo, quién gastó esos créditos, quién subió esos contactos.
    """
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO actividad (quien, accion, detalle, cuantos) "
                "VALUES (?,?,?,?)",
                (_quien(request), accion, detalle or None, cuantos))
    except Exception:
        pass          # el registro nunca puede hacer fallar la acción


_ESTATICOS = ("style.css", "app.js")


def _con_version(html: str) -> str:
    """Le pega a cada archivo estático la fecha en que se modificó.

    Sin esto el navegador reusa el CSS que ya tiene en disco y queda mostrando
    la maqueta nueva con los estilos viejos. Mientras el archivo no cambie la
    dirección es la misma y se sigue reusando; apenas cambia, es otra y se
    vuelve a bajar.
    """
    for nombre in _ESTATICOS:
        archivo = BASE_DIR / "static" / nombre
        if archivo.exists():
            html = html.replace(f"/static/{nombre}",
                                f"/static/{nombre}?v={int(archivo.stat().st_mtime)}")
    return html


# El manual se guarda como un fragmento —sin <html> ni <head>— porque el mismo
# archivo se publica afuera como documento, y allá el envoltorio lo pone el
# host. Acá se lo ponemos nosotros. Un solo archivo para los dos lados: si se
# mantuvieran dos copias, en dos semanas dirían cosas distintas.
# El manual se sirve adentro de la app, que va siempre en oscuro, así que acá
# se le fija el tema. El mismo archivo se publica aparte como documento, y
# ahí conviene que siga el gusto de quien lo lee: por eso el tema se fija en
# el envoltorio y no en el manual.
_ENVOLTORIO = """<!doctype html>
<html lang="es" data-theme="dark"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>{titulo}</title>
<link rel="icon" href="/static/img/favicon.ico" sizes="any">
<style>:root{{color-scheme:dark}} body{{margin:0}} img{{max-width:100%}}</style>
</head><body>
{cuerpo}
</body></html>"""
_TITULO = re.compile(r"(?is)<title>(.*?)</title>\s*")


@app.get("/manual", response_class=HTMLResponse)
def manual():
    """El manual de uso, servido por la app: no depende de ningún sitio ajeno."""
    cuerpo = (BASE_DIR / "templates" / "manual.html").read_text(encoding="utf-8")
    # El título viene adentro del fragmento; acá sube al encabezado, que es
    # donde el navegador lo lee para nombrar la pestaña.
    m = _TITULO.search(cuerpo)
    titulo = m.group(1).strip() if m else "Manual de B2K"
    return _ENVOLTORIO.format(titulo=titulo, cuerpo=_TITULO.sub("", cuerpo, count=1))


@app.get("/", response_class=HTMLResponse)
def index():
    html = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    return _con_version(html)


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
                 crm_status: str = "", import_batch_id: str = "",
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
        # El WhatsApp es el número que la empresa publica para que le
        # escriban: se le llega por ahí sin llamar y sin pedir permiso.
        "whatsapp":    f"({HAS_ANY_PHONE} AND phone_type = 'whatsapp')",
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
    if crm_status:
        where.append("crm_status=?"); params.append(crm_status)
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
        by_ghl = {r["crm_status"]: r["c"] for r in conn.execute(
            "SELECT crm_status, COUNT(*) c FROM contacts GROUP BY crm_status")}
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
              SUM(CASE WHEN {HAS_ANY_PHONE} AND phone_type='whatsapp'
                       THEN 1 ELSE 0 END)                                           AS with_whatsapp,
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
        "with_whatsapp": counts["with_whatsapp"] or 0,
        "mobile_available": mobile_avail,
        "pct_with_email": round(with_email / total * 100, 1) if total else 0.0,
        "pct_contactable": round(contactable / total * 100, 1) if total else 0.0,
        "by_status": by_status, "by_source": by_source, "by_ghl": by_ghl,
        "batches": batches,
        "providers": ([{"name": p.name, "enabled": p.enabled} for p in build_chain()]
                      + [{"name": "maps", "enabled": places.configured()},
                         {"name": "IA", "enabled": ai.configured()}]),
        "ghl_configured": bool(kommo.configured()) or bool(__import__("os").getenv("GHL_API_TOKEN")
                               and __import__("os").getenv("GHL_LOCATION_ID")),
    }


@app.post("/api/batches/{batch_id}/delete")
def api_delete_batch(request: Request, batch_id: int,
                     delete_contacts: str = Form("")):
    """Borra una carga. Sin delete_contacts solo se quita del historial."""
    wipe = str(delete_contacts).lower() in ("1", "true", "yes", "on")
    try:
        with get_db() as conn:
            r = delete_batch(conn, batch_id, wipe)
        anotar(request, "Borró una carga",
               "con sus contactos" if wipe else "solo del historial", batch_id)
        return r
    except ValueError as exc:
        raise HTTPException(404, str(exc))


# -------------------------------------------------------------- enriquecimiento
@app.post("/api/enrich/start")
async def api_enrich_start(request: Request, limit: str = Form(""),
                           batch_id: str = Form("")):
    if enrichment.PROGRESS.running:
        raise HTTPException(409, "Ya hay un enriquecimiento en curso.")
    lim = int(limit) if str(limit).strip().isdigit() else None
    bid = int(batch_id) if str(batch_id).strip().isdigit() else None
    pending = len(enrichment.pending_contacts(limit=lim, batch_id=bid))
    if not pending:
        return {"started": False, "message": "No hay contactos pendientes."}
    asyncio.create_task(enrichment.run_enrichment(limit=lim, batch_id=bid))
    anotar(request, "Buscó emails", "consume créditos", pending)
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
async def api_places_search(request: Request, query: str = Form(...),
                            max_results: str = Form("20")):
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
    # La búsqueda le cuesta plata a la cuenta de Google aunque después nadie
    # guarde los resultados: se anota igual.
    anotar(request, "Buscó en Google Maps", query.strip()[:80], len(r["places"]))
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
def api_places_import(request: Request, icp_tag: str = Form("")):
    if not _PENDING_PLACES.get("places"):
        raise HTTPException(400, "No hay una búsqueda pendiente. Buscá de nuevo.")
    with get_db() as conn:
        resumen = import_places(conn, _PENDING_PLACES["query"],
                                _PENDING_PLACES["places"], icp_tag)
    consulta = _PENDING_PLACES.get("query", "")
    _PENDING_PLACES.clear()
    anotar(request, "Guardó negocios de Maps", consulta[:80],
           resumen.get("new_contacts"))
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
    asyncio.create_task(_respaldo_diario())


async def _respaldo_diario():
    """Una copia por día, sin que nadie se acuerde de pedirla.

    Vive en el mismo disco que la base, así que no salva de perder el disco:
    para eso está el botón de descargar, que se la lleva afuera. Sirve para
    lo otro, que pasa más seguido: alguien borró algo y hace falta la versión
    de ayer.
    """
    while True:
        try:
            copia = respaldo.respaldar_si_toca()
            if copia:
                print(f"[respaldo] {copia.name}", flush=True)
        except Exception as exc:
            print(f"[respaldo] no se pudo: {type(exc).__name__}", flush=True)
        await asyncio.sleep(3600)


@app.get("/api/mail/config")
def api_mail_config():
    """Los buzones configurados y con cuánto margen cuenta cada uno hoy."""
    buzones = mailer.list_mailboxes()
    disp = {b["id"]: b for b in mailer.buzones_disponibles()}
    for b in buzones:
        d = disp.get(b["id"])
        b["sent_today"] = d["sent_today"] if d else 0
        b["remaining"] = d["remaining"] if d else 0
    return {"mailboxes": buzones,
            "configured": any(b["configured"] and b["active"] for b in buzones),
            "capacity_today": sum(b["remaining"] for b in buzones),
            "domains": mailer.resumen_por_dominio(),
            "variables": mailer.VARIABLES}


@app.post("/api/mail/config")
def api_mail_config_save(id: str = Form(""), label: str = Form(""),
                         host: str = Form(""), port: str = Form("587"),
                         username: str = Form(""), password: str = Form(""),
                         from_name: str = Form(""), from_email: str = Form(""),
                         security: str = Form("starttls"),
                         active: str = Form("1"), daily_cap: str = Form("50"),
                         imap_host: str = Form(""), imap_port: str = Form("993")):
    if security not in ("starttls", "ssl", "none"):
        raise HTTPException(400, "Modo de seguridad desconocido.")
    try:
        return mailer.save_mailbox({
            "id": id, "label": label, "host": host, "port": port,
            "username": username, "password": password, "from_name": from_name,
            "from_email": from_email, "security": security,
            "active": active, "daily_cap": daily_cap,
            "imap_host": imap_host, "imap_port": imap_port})
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/mail/config/{mailbox_id}/delete")
def api_mail_config_delete(mailbox_id: int):
    mailer.delete_mailbox(mailbox_id)
    return {"deleted": mailbox_id}


@app.post("/api/mail/test")
async def api_mail_test(request: Request, to: str = Form(""),
                        mailbox_id: str = Form(""), subject: str = Form(""),
                        body: str = Form(""), body_html: str = Form("")):
    """Manda una prueba a una casilla propia. Es el paso previo obligado:
    probar la configuración contra un cliente real no es una opción.

    Sin destinatario va a la cuenta con la que entraste, que es la casilla que
    tenés abierta ahora mismo y donde vas a poder revisar de verdad cómo llegó.

    Con asunto y cuerpo manda ese correo tal cual, sin tocar la lista de
    contactos: es la única forma de ver un diseño como lo va a ver quien lo
    reciba, porque Gmail y Outlook recortan CSS y la vista previa del
    navegador no lo hace.
    """
    destino = (to or "").strip() or ((auth.sesion(request) or {}).get("email") or "")
    if "@" not in destino:
        raise HTTPException(
            400, "No hay a quién mandarle la prueba: entraste con la contraseña "
                 "del equipo, así que escribí una dirección.")

    mid = int(mailbox_id) if str(mailbox_id).strip().isdigit() else None
    buzon = mailer.get_mailbox(mid) if mid else None
    quien = (buzon or {}).get("label") or (buzon or {}).get("from_email") or "B2K"

    if subject.strip() or body.strip() or body_html.strip():
        r = await mailer.enviar(destino, subject.strip() or f"Prueba — {quien}",
                                body, mid, cuerpo_html=body_html)
        return {**r, "to": destino}

    r = await mailer.enviar(
        destino, f"Prueba de envío — {quien}",
        "Si estás leyendo esto, el buzón quedó bien configurado.\n\n"
        f"Salió desde: {(buzon or {}).get('from_email', 'el buzón configurado')}\n"
        f"Servidor: {(buzon or {}).get('host', '—')}\n\n"
        "Abrí 'Mostrar original' en Gmail y fijate que SPF, DKIM y DMARC digan "
        "PASS: eso confirma que un receptor real ve bien el dominio.\n\n"
        "Lo generó B2K desde la pantalla de buzones.", mid)
    return {**r, "to": destino}


@app.post("/api/mail/preview")
def api_mail_preview(contact_ids: str = Form(...), subject: str = Form(""),
                     body: str = Form(""), repeat: str = Form(""),
                     body_html: str = Form("")):
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
        # Con diseño, la versión de texto la escribe la máquina a partir del
        # HTML: se muestra tal cual va a salir, no una aproximación.
        "body": (mailer.render(body, c) if body
                 else mailer.html_a_texto(mailer.render(body_html, c, para_html=True))),
        "body_html": mailer.render(body_html, c, para_html=True) if body_html else "",
    } for c in destinos[:3]]

    return {
        "selected": marcados,
        "sendable": len(destinos),
        "no_email": sin_email,
        "already_written": marcados - sin_email - len(destinos),
        "unknown_vars": mailer.variables_desconocidas(subject + " " + body),
        "samples": muestras,
    }


# Direcciones que solo existen en esta máquina. Un correo con enlaces
# apuntando acá le llega al cliente roto, así que ahí no se rastrea nada.
_LOCALES = ("localhost", "127.0.0.1", "0.0.0.0", "::1")


def _base_publica(request: Request) -> str:
    """La dirección por la que se llega a B2K desde afuera.

    Es la que se escribe adentro del correo, así que tiene que ser la de
    internet y no la que ve el servidor: detrás de Traefik la app se cree en
    http y en otro puerto. Sin una dirección pública no se rastrea: mejor un
    correo sin métricas que un correo con enlaces rotos.
    """
    fijo = (os.getenv("PUBLIC_URL") or "").strip().rstrip("/")
    if fijo:
        return fijo
    cab = request.headers
    proto = (cab.get("x-forwarded-proto", "").split(",")[0].strip()
             or request.url.scheme)
    host = (cab.get("x-forwarded-host", "").split(",")[0].strip()
            or cab.get("host", ""))
    if not host or any(host.startswith(l) for l in _LOCALES):
        return ""
    return f"{proto}://{host}"


@app.post("/api/mail/schedule")
def api_mail_schedule(request: Request,
                      contact_ids: str = Form(...), subject: str = Form(...),
                      body: str = Form(""), name: str = Form(""),
                      limit: str = Form(""), every_seconds: str = Form("180"),
                      jitter_seconds: str = Form("60"), daily_cap: str = Form("50"),
                      repeat: str = Form(""), body_html: str = Form(""),
                      ia: str = Form("")):
    """Arma la campaña y deja la cola lista. El obrero la va soltando."""
    activa = mailer.estado()
    if activa.get("campaign") and activa["campaign"]["status"] == "running" \
            and activa.get("pending"):
        raise HTTPException(409, "Ya hay un envío en curso. Pausalo o cancelalo antes.")

    try:
        ids = [int(i) for i in json.loads(contact_ids)]
    except Exception:
        raise HTTPException(400, "contact_ids debe ser un array JSON de enteros.")
    # Con la IA cada contacto lleva su propio asunto y su propio cuerpo, ya
    # aprobados a mano. Lo que va en la campaña es solo el respaldo.
    con_ia = str(ia).lower() in ("1", "true", "on", "yes")
    escritos = redactor.aprobados() if con_ia else None
    if con_ia and not escritos:
        return {"started": False,
                "message": "No hay ningún correo aprobado todavía. Revisalos y "
                           "aprobá los que quieras mandar."}
    if not con_ia:
        if not subject.strip():
            raise HTTPException(400, "Falta el asunto del correo.")
        if not body.strip() and not body_html.strip():
            raise HTTPException(400, "Falta el cuerpo del correo.")
    if not mailer.get_config()["configured"]:
        raise HTTPException(400, "Configurá primero el servidor de salida.")

    def entero(v, x, minimo=0):
        try:
            return max(minimo, int(str(v).strip() or x))
        except ValueError:
            return x

    destinos = mailer.contactos_enviables(ids, str(repeat).lower() in ("1", "true", "on"))
    if con_ia:
        # Solo sale lo aprobado. Un contacto marcado cuyo correo nadie miró no
        # entra, por más que esté en la selección.
        destinos = [d for d in destinos if d["id"] in escritos]
        if not destinos:
            return {"started": False,
                    "message": "Los correos aprobados son de contactos a los "
                               "que ya se les escribió o que pidieron la baja."}
    tope = entero(limit, 0)
    if tope:
        destinos = destinos[:tope]
    if not destinos:
        return {"started": False,
                "message": "Ninguno de los marcados tiene email o a todos ya se "
                           "les escribió."}

    r = mailer.crear_campania(
        name.strip() or ("Escritos por IA" if con_ia else ""),
        subject or "Correo escrito por IA",
        body or "(cada contacto lleva su propio texto)", destinos,
        cada_segundos=entero(every_seconds, 180, 10),
        jitter=entero(jitter_seconds, 60),
        tope_diario=entero(daily_cap, 50),
        cuerpo_html="" if con_ia else body_html,
        base_rastreo=_base_publica(request),
        escritos=escritos)
    mailer.arrancar_worker()
    if con_ia:
        # Ya salieron: el borrador cumplió su función y solo estorbaría en la
        # próxima tanda.
        redactor.limpiar_borradores([d["id"] for d in destinos])
    anotar(request, "Lanzó una campaña de correo",
           ("escritos por IA" if con_ia else subject[:80]), r.get("queued"))
    return {"started": True, **r}


# ------------------------------------------------- correos escritos por IA
# Mientras estamos en pruebas, la IA escribe y una persona aprueba. El
# borrador se guarda: cerrar la pestaña no puede hacer que se pierda lo que
# ya se pagó en tokens.

@app.post("/api/mail/redactar")
async def api_mail_redactar(request: Request, contact_ids: str = Form(...),
                            propuesta: str = Form("")):
    """Le pide a la IA un correo distinto para cada marcado."""
    if redactor.PROGRESO.corriendo:
        raise HTTPException(409, "Ya se están escribiendo correos.")
    try:
        ids = [int(i) for i in json.loads(contact_ids)]
    except Exception:
        raise HTTPException(400, "contact_ids debe ser un array JSON de enteros.")
    if not ai.configured():
        raise HTTPException(400, "Falta OPENAI_API_KEY en el .env.")

    pendientes = redactor.contactos_a_redactar(ids)
    if not pendientes:
        return {"started": False,
                "message": "Ninguno de los marcados tiene email, o todos "
                           "pidieron no recibir más correos."}
    asyncio.create_task(redactor.redactar_muchos(ids, propuesta))
    anotar(request, "Le pidió a la IA que escribiera correos",
           "cuesta por contacto", len(pendientes))
    return {"started": True, "queued": len(pendientes)}


@app.get("/api/mail/redactar/progress")
def api_mail_redactar_progress():
    return redactor.PROGRESO.as_dict()


@app.get("/api/mail/borradores")
def api_mail_borradores():
    """Los correos escritos, con el tope para poder mostrarlo al editar."""
    filas = redactor.borradores()
    return {"items": filas, "max_caracteres": redactor.largo_maximo(),
            "pendientes": sum(1 for f in filas if f["estado"] == "pendiente"),
            "aprobados": sum(1 for f in filas if f["estado"] == "aprobado"),
            "con_error": sum(1 for f in filas if f["error"])}


@app.post("/api/mail/borradores/todos")
def api_mail_borradores_todos(request: Request, estado: str = Form(...)):
    """Aprueba o descarta de una todos los que están esperando."""
    try:
        cuantos = redactor.marcar_todos(estado)
    except ValueError:
        raise HTTPException(400, "Estado inválido.")
    if estado == "aprobado":
        anotar(request, "Aprobó correos escritos por IA", "de una vez", cuantos)
    return {"ok": True, "cuantos": cuantos}


@app.post("/api/mail/borradores/{contact_id}")
def api_mail_borrador_marcar(contact_id: int, estado: str = Form(...)):
    try:
        redactor.marcar(contact_id, estado)
    except ValueError:
        raise HTTPException(400, "Estado inválido.")
    return {"ok": True}


@app.post("/api/mail/borradores/{contact_id}/editar")
def api_mail_borrador_editar(contact_id: int, asunto: str = Form(...),
                             cuerpo: str = Form(...)):
    try:
        return {"ok": True, **redactor.editar(contact_id, asunto, cuerpo)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/mail/borradores/{contact_id}/rehacer")
async def api_mail_borrador_rehacer(contact_id: int, propuesta: str = Form("")):
    """Vuelve a pedirle ese correo a la IA. Es lo que se hace con los fallados."""
    if not ai.configured():
        raise HTTPException(400, "Falta OPENAI_API_KEY en el .env.")
    with get_db() as conn:
        fila = conn.execute("SELECT * FROM contacts WHERE id=?",
                            (contact_id,)).fetchone()
    if not fila:
        raise HTTPException(404, "Ese contacto no existe.")
    contacto = dict(fila)
    async with httpx.AsyncClient(timeout=180.0) as client:
        contacto = await redactor._ficha_al_dia(client, contacto)
        r = await redactor.escribir(client, contacto, propuesta)
    redactor._guardar(contacto, r)
    return {"ok": not r.get("error"), **r}


@app.delete("/api/mail/borradores")
def api_mail_borradores_borrar():
    return {"ok": True, "cuantos": redactor.limpiar_borradores()}


@app.get("/api/mail/errores")
def api_mail_errores():
    """Lo que no salió, agrupado por causa, con qué hacer en cada caso."""
    return mailer.errores()


@app.post("/api/mail/errores/reintentar")
def api_mail_reintentar(request: Request, ids: str = Form("")):
    """Vuelve a poner en la cola lo que falló y todavía tiene chance."""
    lista = None
    if ids.strip():
        try:
            lista = [int(i) for i in json.loads(ids)]
        except Exception:
            raise HTTPException(400, "ids debe ser un array JSON de enteros.")
    r = mailer.reintentar(lista)
    if r.get("encolados"):
        anotar(request, "Reintentó correos que habían fallado", "",
               r["encolados"])
    return r


# ------------------------------------------------- duplicados y CRM de cero

@app.get("/api/duplicados")
def api_duplicados():
    """Empresas que parecen estar cargadas dos veces. Ver duplicados.py."""
    grupos = duplicados.sospechas()
    return {"grupos": grupos,
            "seguros": sum(1 for g in grupos if g["seguro"]),
            "a_mirar": sum(1 for g in grupos if not g["seguro"])}


@app.post("/api/duplicados/descartar")
def api_duplicados_descartar(request: Request, ids: str = Form(...)):
    """Borra los contactos que sobran de un grupo repetido.

    Solo borra lo que todavía no salió hacia afuera: un contacto que ya está
    en Kommo o al que ya se le escribió no se borra en silencio, porque su
    historia vive también del otro lado.
    """
    try:
        lista = [int(i) for i in json.loads(ids)]
    except Exception:
        raise HTTPException(400, "ids debe ser un array JSON de enteros.")
    if not lista:
        return {"borrados": 0}

    marcas = ",".join("?" * len(lista))
    with get_db() as conn:
        protegidos = [dict(r) for r in conn.execute(
            f"""SELECT c.id, c.company_name, c.full_name, c.crm_lead_id,
                       (SELECT COUNT(*) FROM email_queue q
                         WHERE q.contact_id = c.id AND q.status='sent') enviados
                  FROM contacts c WHERE c.id IN ({marcas})""", lista)]
    no_tocar = [p for p in protegidos if p["crm_lead_id"] or p["enviados"]]
    borrables = [p["id"] for p in protegidos if not (p["crm_lead_id"] or p["enviados"])]

    if borrables:
        marcas2 = ",".join("?" * len(borrables))
        with get_db() as conn:
            conn.execute(f"DELETE FROM contacts WHERE id IN ({marcas2})", borrables)
        anotar(request, "Borró contactos repetidos", "", len(borrables))
    return {"borrados": len(borrables),
            "protegidos": [{"id": p["id"],
                            "nombre": p["company_name"] or p["full_name"],
                            "motivo": ("ya está en Kommo" if p["crm_lead_id"]
                                       else "ya se le escribió")}
                           for p in no_tocar]}


@app.get("/api/crm/estado")
def api_crm_estado():
    """Cuántos contactos figuran como enviados al CRM."""
    with get_db() as conn:
        f = conn.execute(
            """SELECT COUNT(*) total,
                      SUM(crm_lead_id IS NOT NULL AND crm_lead_id <> '') con_lead,
                      SUM(crm_status = 'error') con_error
                 FROM contacts""").fetchone()
    return {"total": f["total"], "en_crm": f["con_lead"] or 0,
            "con_error": f["con_error"] or 0,
            "configurado": kommo.configured()}


@app.post("/api/crm/empezar-de-cero")
def api_crm_reset(request: Request, confirmar: str = Form("")):
    """Olvida lo que B2K mandó a Kommo, para arrancar limpio.

    Borra el vínculo de este lado: a qué contacto corresponde qué lead. NO
    borra nada en Kommo —su API no deja borrar leads, devuelve 405— así que
    los que ya están allá hay que eliminarlos desde Kommo a mano. Lo que esto
    consigue es que B2K deje de considerarlos enviados y no les vuelva a
    escribir ni los cuente como suyos.
    """
    if str(confirmar).lower() not in ("1", "true", "si", "sí", "on"):
        raise HTTPException(400, "Falta confirmar.")
    with get_db() as conn:
        n = conn.execute(
            """SELECT COUNT(*) c FROM contacts
                WHERE crm_contact_id IS NOT NULL OR crm_lead_id IS NOT NULL
                   OR crm_status IS NOT NULL""").fetchone()["c"]
        conn.execute(
            """UPDATE contacts SET crm_contact_id=NULL, crm_lead_id=NULL,
                   crm_status=NULL, crm_error=NULL""")
    anotar(request, "Empezó de cero con el CRM",
           "se olvidaron los vínculos con Kommo", n)
    return {"ok": True, "olvidados": n,
            "aviso": "En Kommo siguen existiendo: su API no permite borrarlos. "
                     "Eliminalos desde Kommo si no los querés ahí."}


# ------------------------------------------------- etapas de Kommo
# Qué hecho del correo lleva el lead a qué etapa. Sin esto B2K sabe todo lo
# que pasa y Kommo no se entera de nada.

@app.get("/api/crm/etapas")
async def api_crm_etapas():
    datos = await kommo.listar_embudos()
    with get_db() as conn:
        pend = conn.execute(
            "SELECT COUNT(*) n FROM email_events WHERE COALESCE(crm,0)=0"
        ).fetchone()["n"]
    return {**datos,
            "eventos": [{"clave": k, "que": q} for k, q in kommo.EVENTOS],
            "mapa": kommo._mapa(),
            "pendientes_de_avisar": pend}


@app.post("/api/crm/etapas")
async def api_crm_etapas_guardar(request: Request, mapa: str = Form(...)):
    try:
        crudo = json.loads(mapa)
    except ValueError:
        raise HTTPException(400, "El mapa tiene que ser un objeto JSON.")
    if not isinstance(crudo, dict):
        raise HTTPException(400, "El mapa tiene que ser un objeto JSON.")
    guardado = kommo.guardar_mapa(crudo)
    anotar(request, "Cambió a qué etapa lleva cada evento del correo",
           ", ".join(f"{k}->{v}" for k, v in guardado.items())[:200])
    return {"ok": True, "mapa": guardado}


@app.post("/api/crm/etapas/sincronizar")
async def api_crm_sincronizar(request: Request):
    """Empuja ahora lo que quedó pendiente, sin esperar al obrero."""
    r = await mailer.sincronizar_crm(limite=200)
    anotar(request, "Sincronizó las etapas con Kommo", "", r.get("hechos"))
    return r


# ------------------------------------------------- horario de envío
# Se guarda en la base y no en el entorno: cambiar a qué hora salen los
# correos no puede exigir entrar al servidor y redesplegar.

@app.get("/api/mail/ventana")
def api_ventana():
    desde, hasta, fines = mailer.ventana()
    return {"desde": desde, "hasta": hasta, "fines_de_semana": fines,
            "ahora_puede": mailer.en_horario(),
            "zona": os.getenv("TZ") or "la del servidor",
            "hora_del_servidor": datetime.datetime.now().strftime("%H:%M")}


@app.post("/api/mail/ventana")
def api_ventana_guardar(request: Request, desde: str = Form(...),
                        hasta: str = Form(...), fines: str = Form("")):
    try:
        d, h = int(desde), int(hasta)
    except ValueError:
        raise HTTPException(400, "Las horas van en números, de 0 a 24.")
    if not (0 <= d <= 23 and 1 <= h <= 24):
        raise HTTPException(400, "Las horas van de 0 a 24.")
    if h <= d:
        raise HTTPException(400, "La hora de fin tiene que ser posterior a la "
                                 "de inicio.")
    db_mod.poner_ajuste("envio_desde", d)
    db_mod.poner_ajuste("envio_hasta", h)
    db_mod.poner_ajuste("envio_fin_de_semana",
                        "1" if str(fines).lower() in ("1", "true", "on") else "0")
    anotar(request, "Cambió el horario de envío",
           f"{d}:00 a {h}:00{', con fines de semana' if fines else ''}")
    return {"ok": True, **api_ventana()}


# ------------------------------------------------- la base y sus respaldos

@app.get("/api/base")
def api_base():
    """Dónde vive la base, desde cuándo y qué tiene. Ver respaldo.py."""
    return respaldo.ficha()


@app.post("/api/base/respaldo")
def api_base_respaldar(request: Request):
    try:
        copia = respaldo.copiar()
    except Exception as exc:
        raise HTTPException(500, f"No se pudo copiar: {type(exc).__name__}")
    anotar(request, "Hizo un respaldo de la base", copia.name)
    return {"ok": True, "nombre": copia.name, "respaldos": respaldo.listar()}


@app.get("/api/base/descargar")
def api_base_descargar(request: Request):
    """Se lleva una copia fuera del servidor, que es el único respaldo real.

    Una copia que vive en el mismo disco que la base no protege de lo que más
    pasa: que el volumen no esté montado y el disco entero se rehaga.
    """
    destino = Path(tempfile.gettempdir()) / (
        f"b2k-{datetime.datetime.now():%Y%m%d-%H%M}.db")
    respaldo.copiar(destino)
    anotar(request, "Descargó una copia de la base", destino.name)
    return FileResponse(destino, filename=destino.name,
                        media_type="application/octet-stream")


@app.get("/api/mail/status")
def api_mail_status():
    return mailer.estado()


# ------------------------------------------------------------------ rastreo
# Un punto transparente. Va escrito acá y no como archivo para que no dependa
# de nada del disco: si esto falla, falla el correo de alguien.
_PUNTO = base64.b64decode(
    b"R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


@app.get("/t/a/{token}.png")
def track_open(token: str, request: Request):
    """Alguien abrió el correo. Devuelve la imagen pase lo que pase."""
    try:
        mailer.registrar_evento(token, "open",
                                agent=request.headers.get("user-agent", ""))
    except Exception:
        # Un error acá no puede romper el correo de nadie.
        pass
    return Response(_PUNTO, media_type="image/gif",
                    headers={"Cache-Control": "no-store, no-cache, must-revalidate",
                             "Pragma": "no-cache"})


@app.get("/t/c/{token}")
def track_click(token: str, request: Request, u: str = ""):
    """Alguien tocó un enlace. Se anota y se lo manda a donde iba."""
    try:
        destino = mailer.descifrar_destino(u)
    except Exception:
        destino = ""
    # Solo direcciones de internet: sin esto, un enlace armado a mano podría
    # usar la app para mandar gente a donde quiera.
    if not destino.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "Enlace inválido.")
    try:
        mailer.registrar_evento(token, "click", url=destino,
                                agent=request.headers.get("user-agent", ""))
    except Exception:
        pass
    return RedirectResponse(destino, status_code=302)


# --------------------------------------------------------------- la baja
_BAJA = """<!doctype html>
<html lang="es"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titulo}</title>
<style>
  :root{{color-scheme:light dark}}
  body{{margin:0;min-height:100vh;display:grid;place-items:center;
       background:#f4f4f7;color:#1a1a2e;
       font-family:system-ui,-apple-system,"Segoe UI",sans-serif;padding:24px}}
  @media (prefers-color-scheme:dark){{
    body{{background:#14101f;color:#eceafa}}
    .caja{{background:#1d1730 !important;border-color:#2e2650 !important}}
    .sub{{color:#9a92bd !important}}
  }}
  .caja{{background:#fff;border:1px solid #e2def0;border-radius:14px;
        padding:32px;max-width:460px;width:100%;
        box-shadow:0 10px 40px -24px rgba(0,0,0,.4)}}
  h1{{margin:0 0 10px;font-size:22px;letter-spacing:-.02em}}
  p{{margin:0 0 18px;line-height:1.55;font-size:15px}}
  .sub{{color:#6b6b8a;font-size:14px}}
  button{{background:#241a5c;color:#fff;border:0;border-radius:9px;
         padding:13px 22px;font-size:15px;font-weight:600;cursor:pointer;
         font-family:inherit}}
  button:hover{{opacity:.9}}
  .ok{{color:#03875a;font-weight:600}}
</style></head><body>
<div class="caja">{cuerpo}</div>
</body></html>"""


@app.get("/u/{token}", response_class=HTMLResponse)
def baja_pagina(token: str):
    """Muestra el pedido de baja, pero NO lo aplica todavía.

    Los antivirus y los filtros corporativos abren todos los enlaces de un
    correo para revisarlos. Si esto diera de baja con solo abrirlo, media
    lista se daría de baja sola sin que ninguna persona lo haya pedido. Por
    eso hace falta el botón: dar de baja es una acción, no una visita.
    """
    cuerpo = f"""
      <h1>¿Dejamos de escribirte?</h1>
      <p>Si confirmás, no vas a volver a recibir correos nuestros.</p>
      <form method="post" action="/u/{html_mod.escape(token)}">
        <button type="submit">Sí, no quiero recibir más</button>
      </form>
      <p class="sub" style="margin-top:18px">Si llegaste acá sin querer,
        cerrá esta página: no pasa nada.</p>"""
    return _BAJA.format(titulo="Dejar de recibir correos", cuerpo=cuerpo)


@app.post("/u/{token}", response_class=HTMLResponse)
def baja_confirmar(token: str):
    """Aplica la baja. Es también lo que llama el botón de Gmail."""
    r = mailer.baja_por_marca(token)
    if not r.get("ok"):
        cuerpo = """
          <h1>No encontramos ese correo</h1>
          <p>El enlace puede haber vencido. Si querés que dejemos de
            escribirte, respondé al correo que recibiste y lo hacemos a
            mano.</p>"""
        return HTMLResponse(_BAJA.format(titulo="No se pudo", cuerpo=cuerpo),
                            status_code=404)
    cuerpo = f"""
      <h1>Listo</h1>
      <p class="ok">{html_mod.escape(r["email"])}</p>
      <p>No vas a volver a recibir correos nuestros. Perdón por la
        molestia.</p>"""
    return _BAJA.format(titulo="Baja confirmada", cuerpo=cuerpo)


@app.get("/api/mail/suppression")
def api_suppression():
    """A quiénes no hay que volver a escribirles."""
    return {"items": mailer.suprimidos()}


@app.post("/api/mail/suppression")
def api_suppression_add(email: str = Form(...), reason: str = Form("agregado a mano")):
    if not mailer.suprimir(email, reason):
        raise HTTPException(400, "Eso no parece una dirección de correo.")
    return {"ok": True, "email": email.strip().lower()}


@app.get("/api/contacts/{contact_id}/compras")
async def api_contacto_compras(contact_id: int):
    """Cuántas veces compró este contacto, según Kommo."""
    with get_db() as conn:
        f = conn.execute("SELECT crm_contact_id FROM contacts WHERE id=?",
                         (contact_id,)).fetchone()
    if not f:
        raise HTTPException(404, "Ese contacto no existe.")
    if not f["crm_contact_id"]:
        return {"en_crm": False, "compras": 0, "leads": []}
    r = await kommo.historial_del_contacto(f["crm_contact_id"])
    return {"en_crm": True, **r}


@app.get("/api/actividad")
def api_actividad(limit: int = 100):
    """Quién hizo qué, de lo más reciente a lo más viejo."""
    with get_db() as conn:
        filas = conn.execute(
            "SELECT * FROM actividad ORDER BY at DESC, id DESC LIMIT ?",
            (max(1, min(500, limit)),)).fetchall()
    return {"items": [dict(f) for f in filas]}


@app.get("/api/mail/health")
def api_mail_health(days: str = "30"):
    """La salud de cada buzón: rebotes, rechazos, respuestas y ritmo."""
    try:
        dias = max(7, min(90, int(days)))
    except (TypeError, ValueError):
        dias = 30
    return mailer.salud(dias)


@app.post("/api/mail/dns/check")
async def api_mail_dns(domain: str = Form("")):
    """Revisa SPF, DKIM y DMARC de los dominios desde los que se manda.

    Se consulta a pedido y el resultado queda guardado: el DNS no cambia solo
    y preguntarlo en cada carga de pantalla sería ruido.
    """
    if domain.strip():
        dominios = [domain.strip().lower()]
    else:
        dominios = sorted({mailer.dominio_de(b) for b in mailer.list_mailboxes()
                           if mailer.dominio_de(b)})
    if not dominios:
        return {"domains": []}

    salida = []
    for d in dominios:
        r = await dnscheck.revisar_dominio(d)
        with get_db() as conn:
            conn.execute(
                """INSERT INTO domain_dns (domain, ok, detail, checked_at)
                   VALUES (?,?,?, datetime('now'))
                   ON CONFLICT(domain) DO UPDATE SET
                     ok=excluded.ok, detail=excluded.detail,
                     checked_at=excluded.checked_at""",
                (d, 1 if r["ok"] else 0, json.dumps(r, ensure_ascii=False)))
        salida.append(r)
    return {"domains": salida}


@app.post("/api/mail/inbox/scan")
async def api_mail_inbox_scan(mailbox_id: str = Form("")):
    """Entra a los buzones y anota lo que volvió: respuestas y rebotes.

    Se hace solo cada diez minutos; esto es para no esperar.
    """
    mid = int(mailbox_id) if str(mailbox_id).strip().isdigit() else None
    return await mailer.revisar_buzones(mid)


@app.get("/api/mail/campaigns")
def api_mail_campaigns():
    return {"campaigns": mailer.campanias()}


@app.get("/api/mail/metrics")
def api_mail_metrics(request: Request, campaign: str = ""):
    """Los resultados de una campaña. Sin indicar cuál, la más reciente."""
    lista = mailer.campanias()
    base = _base_publica(request)
    if not lista:
        return {"campaigns": [], "metrics": {}, "base": base}
    try:
        cid = int(campaign)
    except (TypeError, ValueError):
        cid = lista[0]["id"]
    return {"campaigns": lista, "metrics": mailer.metricas(cid), "base": base}


@app.post("/api/mail/control")
def api_mail_control(request: Request, campaign_id: str = Form(...),
                     action: str = Form(...)):
    acciones = {"pause": "paused", "resume": "running", "cancel": "cancelled"}
    if action not in acciones:
        raise HTTPException(400, "Acción desconocida.")
    try:
        cid = int(campaign_id)
    except ValueError:
        raise HTTPException(400, "campaign_id inválido.")
    r = mailer.cambiar_estado(cid, acciones[action])
    anotar(request, {"pause": "Pausó un envío", "resume": "Reanudó un envío",
                     "cancel": "Canceló un envío"}[action], f"campaña {cid}")
    return r


# ------------------------------------------------------------------ ficha IA
@app.post("/api/ai/start")
async def api_ai_start(request: Request, contact_ids: str = Form(...),
                       redo: str = Form("")):
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
    anotar(request, "Armó fichas con IA", "cuesta por contacto", len(pendientes))
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
    return await kommo.listar_embudos()


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
async def api_ghl_send(request: Request, contact_ids: str = Form(...),
                       tag: str = Form("")):
    try:
        ids = [int(i) for i in json.loads(contact_ids)]
    except Exception:
        raise HTTPException(400, "contact_ids debe ser un array JSON de enteros.")
    if not ids:
        raise HTTPException(400, "No se seleccionó ningún contacto.")
    result = await kommo.send_contacts(ids, tag or None)
    anotar(request, "Subió contactos a Kommo",
           f"{result.get('sent', 0)} nuevos · {result.get('already_in_crm', 0)} ya estaban",
           len(ids))
    if result.get("error"):
        return JSONResponse(result, status_code=400)
    return result
