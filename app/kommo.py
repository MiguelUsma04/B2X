"""Envío de contactos a Kommo.

Reemplaza a GoHighLevel como destino. Las diferencias que importan:

- En Kommo un prospecto son **dos cosas**: un *contacto* (la persona o el
  negocio) y un *lead* (la oportunidad que avanza por el embudo). Se crean
  juntos, en una sola llamada, para que no quede un contacto suelto si la
  segunda mitad falla.
- El teléfono y el correo no son campos fijos: son **campos personalizados**
  con un id distinto en cada cuenta. Se preguntan una vez al arrancar y se
  guardan en memoria; escribirlos a mano es la causa número uno de contactos
  que entran sin teléfono.
- El WhatsApp tiene su propio campo (código USERNAME, tipo WHATSAPP), que es
  lo que lee el chat de Kommo. Un WhatsApp cargado como teléfono común no le
  sirve a nadie.
"""
import asyncio
import os

import httpx

from . import telefonos
from .db import get_db

TIEMPO = 30.0
# Kommo corta a las siete llamadas por segundo. Con una espera corta entre
# contactos no se llega nunca al límite, y 0,2 s por contacto es invisible al
# lado de lo que tarda la llamada.
ESPERA = 0.2
REINTENTOS = 3


def configured() -> bool:
    return bool(os.getenv("KOMMO_SUBDOMAIN") and os.getenv("KOMMO_TOKEN"))


def base_url() -> str:
    return f"https://{os.getenv('KOMMO_SUBDOMAIN', '').strip()}.kommo.com/api/v4"


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.getenv('KOMMO_TOKEN', '').strip()}",
            "Content-Type": "application/json"}


# Los ids de los campos personalizados de esta cuenta. Se resuelven una vez.
_CAMPOS: dict | None = None


async def campos(client: httpx.AsyncClient, refrescar: bool = False) -> dict:
    """Los ids de teléfono, correo y WhatsApp en esta cuenta de Kommo."""
    global _CAMPOS
    if _CAMPOS is not None and not refrescar:
        return _CAMPOS

    encontrados: dict = {}
    r = await client.get(f"{base_url()}/contacts/custom_fields",
                         params={"limit": 250})
    if r.status_code == 200:
        for f in r.json().get("_embedded", {}).get("custom_fields", []):
            code = (f.get("code") or "").upper()
            if code in ("PHONE", "EMAIL"):
                encontrados[code.lower()] = f["id"]
            elif code == "USERNAME":
                # El campo del chat. Su enum WHATSAPP es el que hace que el
                # número aparezca como conversación y no como dato muerto.
                wa = [e for e in (f.get("enums") or [])
                      if (e.get("value") or "").upper() == "WHATSAPP"]
                if wa:
                    encontrados["whatsapp"] = f["id"]
                    encontrados["whatsapp_enum"] = wa[0]["id"]
    _CAMPOS = encontrados
    return encontrados


# --------------------------------------------------- los campos del lead
# La pestaña propia dentro de la tarjeta del lead, y lo que va adentro. Son
# los datos que B2K averigua y que el comercial necesita ver sin salir de
# Kommo. Se crean solos la primera vez; si ya existen, se reusan.
GRUPO_B2K = "B2K"
CAMPOS_LEAD = [
    ("Resumen del negocio", "textarea"),
    ("Gancho", "textarea"),
    ("Ciudad", "text"),
    ("Rubro", "text"),
    ("Sitio web", "url"),
    ("Calificación en Google", "text"),
    ("De dónde salió", "text"),
]

_CAMPOS_LEAD: dict | None = None


async def campos_lead(client: httpx.AsyncClient, refrescar: bool = False) -> dict:
    """Los ids de los campos de B2K en la tarjeta del lead.

    Crea la pestaña y los campos que falten. Es idempotente a propósito: la
    cuenta ya tiene decenas de campos de otros procesos y duplicarlos sería
    ensuciar el CRM de todo el equipo.
    """
    global _CAMPOS_LEAD
    if _CAMPOS_LEAD is not None and not refrescar:
        return _CAMPOS_LEAD

    r = await client.get(f"{base_url()}/leads/custom_fields/groups")
    if r.status_code != 200:
        _CAMPOS_LEAD = {}
        return _CAMPOS_LEAD
    grupos = r.json().get("_embedded", {}).get("custom_field_groups", [])
    mio = next((g for g in grupos
                if (g.get("name") or "").strip().upper() == GRUPO_B2K), None)

    if not mio:
        rc = await client.post(f"{base_url()}/leads/custom_fields/groups",
                               json=[{"name": GRUPO_B2K}])
        if rc.status_code not in (200, 201):
            _CAMPOS_LEAD = {}
            return _CAMPOS_LEAD
        creados = rc.json().get("_embedded", {}).get("custom_field_groups", [])
        mio = creados[0] if creados else None
        if not mio:
            _CAMPOS_LEAD = {}
            return _CAMPOS_LEAD

    grupo_id = mio["id"]

    r = await client.get(f"{base_url()}/leads/custom_fields",
                         params={"limit": 250})
    existentes = {}
    if r.status_code == 200:
        for f in r.json().get("_embedded", {}).get("custom_fields", []):
            existentes[(f.get("name") or "").strip().lower()] = f["id"]

    faltan = [{"name": n, "type": t, "group_id": grupo_id}
              for n, t in CAMPOS_LEAD if n.lower() not in existentes]
    if faltan:
        rc = await client.post(f"{base_url()}/leads/custom_fields", json=faltan)
        if rc.status_code in (200, 201):
            for f in rc.json().get("_embedded", {}).get("custom_fields", []):
                existentes[(f.get("name") or "").strip().lower()] = f["id"]

    _CAMPOS_LEAD = {n: existentes.get(n.lower()) for n, _ in CAMPOS_LEAD
                    if existentes.get(n.lower())}
    return _CAMPOS_LEAD


def datos_del_lead(c: dict) -> dict:
    """Lo que B2K sabe de esta empresa, con el nombre de cada campo."""
    perfil = {}
    if c.get("ai_profile"):
        try:
            import json
            perfil = json.loads(c["ai_profile"]) or {}
        except Exception:
            perfil = {}

    ciudades = perfil.get("ciudades") or []
    calificacion = ""
    if c.get("rating"):
        calificacion = str(c["rating"]).replace(".", ",")
        if c.get("rating_count"):
            calificacion += f" · {c['rating_count']} reseñas"

    sitio = c.get("company_domain") or ""
    if sitio and not sitio.startswith("http"):
        sitio = "https://" + sitio

    return {
        "Resumen del negocio": (perfil.get("resumen") or c.get("ai_summary") or ""),
        "Gancho": perfil.get("gancho") or "",
        "Ciudad": (ciudades[0] if ciudades else "") or "",
        "Rubro": c.get("category") or "",
        "Sitio web": sitio,
        "Calificación en Google": calificacion,
        "De dónde salió": "Google Maps" if c.get("place_id") else "Archivo de Apollo",
    }


def con_indicativo(numero: str, region: str | None = None) -> str:
    """El número en formato internacional, con la regla del país que sea.

    La cuenta la hace la librería de numeración de Google, que sabe cuántos
    dígitos tiene un número en cada país y cómo se escribe. Antes acá había
    una regla a mano para Colombia, que con prospección afuera convertía un
    número mexicano en uno colombiano que existe y es de otra persona.
    """
    return telefonos.normalizar(numero, region)


def _valor(campo_id: int, valor: str, enum_code: str | None = None,
           enum_id: int | None = None) -> dict:
    v: dict = {"value": valor}
    if enum_code:
        v["enum_code"] = enum_code
    if enum_id:
        v["enum_id"] = enum_id
    return {"field_id": campo_id, "values": [v]}


def armar_contacto(c: dict, ids: dict) -> dict:
    """El contacto tal como lo espera Kommo."""
    nombre = (c.get("full_name") or c.get("company_name") or c.get("email")
              or "Sin nombre").strip()
    cuerpo: dict = {"name": nombre[:250]}

    responsable = (os.getenv("KOMMO_RESPONSIBLE_ID") or "").strip()
    if responsable.isdigit():
        cuerpo["responsible_user_id"] = int(responsable)

    campos_valores = []
    if c.get("email") and ids.get("email"):
        campos_valores.append(_valor(ids["email"], c["email"], "WORK"))

    # El país sale de lo que Google informó para esa empresa, o de su
    # dirección. Recién si no hay nada de eso se usa el país por defecto.
    region = telefonos.region_del_contacto(c)
    telefono = con_indicativo(c.get("phone") or "", region)
    if telefono and ids.get("phone"):
        # Un celular es MOB y el conmutador es WORK: en Kommo eso cambia el
        # ícono y, en las cuentas con telefonía, a qué número marca. Se
        # pregunta por el número, que es más confiable que lo que quedó
        # guardado cuando se importó.
        clase = telefonos.tipo(telefono, region)
        movil = (clase == telefonos.CELULAR
                 or c.get("phone_type") in ("personal", "whatsapp"))
        campos_valores.append(_valor(ids["phone"], telefono,
                                     "MOB" if movil else "WORK"))

    # El WhatsApp va además en el campo del chat, no en lugar del teléfono.
    if (telefono and c.get("phone_type") == "whatsapp"
            and ids.get("whatsapp") and ids.get("whatsapp_enum")):
        campos_valores.append(
            _valor(ids["whatsapp"], telefono, enum_id=ids["whatsapp_enum"]))

    if campos_valores:
        cuerpo["custom_fields_values"] = campos_valores
    return cuerpo


def armar_lead(c: dict, tag: str | None, campos_ids: dict | None = None) -> dict:
    """El lead: la oportunidad que va a avanzar por el embudo."""
    nombre = (c.get("company_name") or c.get("full_name")
              or c.get("email") or "Prospecto").strip()
    lead: dict = {"name": nombre[:250]}

    for var, clave in (("KOMMO_PIPELINE_ID", "pipeline_id"),
                       ("KOMMO_STATUS_ID", "status_id")):
        v = (os.getenv(var) or "").strip()
        if v.isdigit():
            lead[clave] = int(v)

    responsable = (os.getenv("KOMMO_RESPONSIBLE_ID") or "").strip()
    if responsable.isdigit():
        lead["responsible_user_id"] = int(responsable)

    etiquetas = [t.strip() for t in
                 (tag or os.getenv("KOMMO_DEFAULT_TAG") or "").split(",")
                 if t.strip()]
    if etiquetas:
        lead["_embedded"] = {"tags": [{"name": t} for t in etiquetas]}

    # La pestaña B2K de la tarjeta. Un campo vacío no se manda: en Kommo
    # escribir vacío borra lo que un comercial pudo haber puesto a mano.
    if campos_ids:
        valores = []
        for nombre, texto in datos_del_lead(c).items():
            cid = campos_ids.get(nombre)
            if cid and str(texto).strip():
                valores.append({"field_id": cid,
                                "values": [{"value": str(texto)[:2000]}]})
        if valores:
            lead["custom_fields_values"] = valores
    return lead


async def _con_reintento(client: httpx.AsyncClient, metodo: str, url: str,
                         **kw) -> httpx.Response:
    """Reintenta cuando Kommo pide esperar o se cae un momento.

    Un 429 no es un error del dato: es "vas muy rápido". Fallar ahí dejaría
    contactos sin subir por un motivo que se resuelve esperando un segundo.
    """
    espera = 1.0
    r = None
    for intento in range(REINTENTOS):
        r = await client.request(metodo, url, **kw)
        if r.status_code not in (429, 502, 503, 504):
            return r
        if intento < REINTENTOS - 1:
            # Si el servidor dice cuánto esperar, se le hace caso.
            dice = r.headers.get("Retry-After")
            try:
                pausa = float(dice) if dice else espera
            except ValueError:
                pausa = espera
            await asyncio.sleep(min(pausa, 10))
            espera *= 2
    return r


async def existe_contacto(client: httpx.AsyncClient, cid: str) -> bool | None:
    """Si el contacto sigue estando en Kommo.

    Devuelve None cuando no se pudo averiguar: ahí conviene no reenviar, para
    no duplicar por una caída de red.
    """
    try:
        r = await client.get(f"{base_url()}/contacts/{cid}")
    except Exception:
        return None
    if r.status_code == 200:
        return True
    if r.status_code in (204, 404):
        return False
    return None


def _explicar(r: httpx.Response) -> str:
    """El error de Kommo en algo accionable."""
    if r.status_code == 401:
        return ("Kommo rechazó el token. Revisá que sea el de larga duración "
                "y que la integración siga activa.")
    if r.status_code == 403:
        return ("El token no tiene permisos sobre leads o contactos. "
                "Revisá los scopes de la integración.")
    if r.status_code == 402:
        return "La cuenta de Kommo no tiene plan activo."
    try:
        d = r.json()
    except Exception:
        return f"HTTP {r.status_code}: {r.text[:200]}"
    # Kommo devuelve el detalle adentro de validation-errors.
    detalles = []
    for v in (d.get("validation-errors") or []):
        for e in (v.get("errors") or []):
            detalles.append(f"{e.get('path', '')}: {e.get('detail', '')}".strip(": "))
    if detalles:
        return f"HTTP {r.status_code}: " + " · ".join(detalles[:3])
    return f"HTTP {r.status_code}: {(d.get('title') or r.text)[:200]}"


async def send_contacts(contact_ids: list[int], tag: str | None = None) -> dict:
    """Sube los contactos marcados a Kommo, de a uno.

    Sin reintento automático: lo que falla queda en crm_status='error' con el
    motivo, y quien mira decide si reintenta.
    """
    if not configured():
        return {"error": "Faltan KOMMO_SUBDOMAIN y KOMMO_TOKEN en el .env",
                "sent": 0, "failed": 0, "results": []}
    if not contact_ids:
        return {"sent": 0, "failed": 0, "skipped": 0, "results": []}

    marcas = ",".join("?" * len(contact_ids))
    with get_db() as conn:
        filas = [dict(r) for r in conn.execute(
            f"SELECT * FROM contacts WHERE id IN ({marcas})", contact_ids)]

    enviados = fallidos = saltados = ya = sin_confirmar = rehechos = 0
    resultados = []

    async with httpx.AsyncClient(timeout=TIEMPO, headers=_headers()) as client:
        ids = await campos(client)
        ids_lead = await campos_lead(client)
        if not ids.get("phone") and not ids.get("email"):
            return {"error": "No se pudieron leer los campos de Kommo. "
                             "Revisá el token.", "sent": 0, "failed": 0,
                    "results": []}

        for c in filas:
            cid = c["id"]
            try:
                # Ya está en Kommo: no se vuelve a subir. Antes de saltearlo se
                # confirma allá, porque si lo borraron la marca local miente y
                # el contacto quedaría afuera para siempre.
                if c.get("crm_contact_id"):
                    hay = await existe_contacto(client, c["crm_contact_id"])
                    if hay is not False:
                        ya += 1
                        if hay is None:
                            sin_confirmar += 1
                        resultados.append({
                            "id": cid, "status": "already",
                            "message": "Ya estaba en Kommo." if hay else
                                       "Figura en Kommo pero no se pudo "
                                       "confirmar; no se reenvía para no "
                                       "duplicarlo."})
                        continue
                    rehechos += 1
                    with get_db() as conn:
                        conn.execute(
                            """UPDATE contacts SET crm_contact_id=NULL,
                                 crm_lead_id=NULL, crm_status='pending'
                               WHERE id=?""", (cid,))

                if not c.get("email") and not c.get("phone"):
                    saltados += 1
                    resultados.append({
                        "id": cid, "status": "skipped",
                        "message": "Sin email ni teléfono: no hay por dónde "
                                   "contactarlo."})
                    continue

                # Lead y contacto juntos: si se crearan por separado y la
                # segunda llamada fallara, quedaría un contacto huérfano que
                # nadie va a trabajar.
                lead = armar_lead(c, tag, ids_lead)
                dentro = lead.pop("_embedded", {})
                dentro["contacts"] = [armar_contacto(c, ids)]
                lead["_embedded"] = dentro
                r = await _con_reintento(client, "POST",
                                         f"{base_url()}/leads/complex",
                                         json=[lead])
                await asyncio.sleep(ESPERA)

                if r.status_code in (200, 201):
                    d = r.json()
                    item = d[0] if isinstance(d, list) and d else {}
                    lead_id = str(item.get("id") or "")
                    # Este endpoint devuelve el id del contacto arriba de todo,
                    # no adentro de _embedded. Sin esto el contacto queda sin
                    # marca y el próximo envío lo sube duplicado.
                    contacto_id = str(item.get("contact_id") or "")
                    if not contacto_id:
                        for ct in (item.get("_embedded", {}).get("contacts") or []):
                            contacto_id = str(ct.get("id") or "")
                            break
                    with get_db() as conn:
                        conn.execute(
                            """UPDATE contacts SET crm_status='sent',
                                 crm_contact_id=?, crm_lead_id=?, crm_error=NULL,
                                 updated_at=datetime('now') WHERE id=?""",
                            (contacto_id or None, lead_id or None, cid))
                    enviados += 1
                    resultados.append({"id": cid, "status": "sent",
                                       "message": f"Lead {lead_id} creado."})
                else:
                    msg = _explicar(r)
                    with get_db() as conn:
                        conn.execute(
                            """UPDATE contacts SET crm_status='error', crm_error=?,
                               updated_at=datetime('now') WHERE id=?""", (msg, cid))
                    fallidos += 1
                    resultados.append({"id": cid, "status": "error", "message": msg})

            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"[:400]
                with get_db() as conn:
                    conn.execute(
                        """UPDATE contacts SET crm_status='error', crm_error=?,
                           updated_at=datetime('now') WHERE id=?""", (msg, cid))
                fallidos += 1
                resultados.append({"id": cid, "status": "error", "message": msg})

    return {"sent": enviados, "failed": fallidos, "skipped": saltados,
            "already_in_crm": ya, "not_verified": sin_confirmar,
            "recreated": rehechos,
            "pipeline_configured": bool((os.getenv("KOMMO_PIPELINE_ID") or "").strip()),
            "results": resultados}


async def anotar(lead_id: str, texto: str) -> bool:
    """Deja una nota en la tarjeta del lead.

    Es lo que hace que la conversación se vea en Kommo: quien abre el lead lee
    lo que B2K le escribió y lo que contestaron, sin tener que ir a buscar a
    otro lado. Un comercial que no ve el hilo no sabe con qué está entrando.
    """
    if not configured() or not lead_id or not (texto or "").strip():
        return False
    try:
        async with httpx.AsyncClient(timeout=TIEMPO, headers=_headers()) as c:
            r = await _con_reintento(
                c, "POST", f"{base_url()}/leads/{lead_id}/notes",
                json=[{"note_type": "common",
                       "params": {"text": texto[:10000]}}])
        return r.status_code in (200, 201)
    except Exception:
        # Que no se pueda anotar no puede frenar un envío: el correo ya salió.
        return False


def _nota_correo(asunto: str, cuerpo: str, desde: str) -> str:
    return (f"📤 B2K envió un correo\n"
            f"Desde: {desde}\n"
            f"Asunto: {asunto}\n\n{(cuerpo or '').strip()[:4000]}")


def _nota_respuesta(de: str, cuando: str) -> str:
    return (f"📥 Respondieron el correo de B2K\n"
            f"De: {de}\n"
            f"Cuándo: {cuando}\n\n"
            "El contenido está en el buzón desde el que se mandó.")


# Los dos estados que Kommo reserva en todos los embudos. Aunque el equipo los
# renombre —"SESIÓN EJECUTIVA AGENDADA", "Logrado con éxito"— el número no
# cambia, así que contar por número funciona en cualquier embudo.
GANADO = 142
PERDIDO_ID = 143


async def historial_del_contacto(crm_contact_id: str) -> dict:
    """Cuántas veces le compró este contacto, según Kommo.

    Una compra es un lead que llegó a ventas ganadas. Se cuenta por el estado
    y no por el nombre de la etapa: cada embudo la llama distinto y el equipo
    la puede renombrar mañana.
    """
    vacio = {"compras": 0, "abiertos": 0, "perdidos": 0, "monto": 0,
             "ultima": None, "leads": []}
    if not configured() or not crm_contact_id:
        return vacio

    async with httpx.AsyncClient(timeout=TIEMPO, headers=_headers()) as c:
        r = await c.get(f"{base_url()}/contacts/{crm_contact_id}",
                        params={"with": "leads"})
        if r.status_code != 200:
            return {**vacio, "error": _explicar(r)}
        ids = [str(x.get("id")) for x
               in (r.json().get("_embedded", {}).get("leads") or [])
               if x.get("id")]
        if not ids:
            return vacio

        # Se piden todos de una: uno por uno serían N llamadas por ficha.
        params = [("filter[id][]", i) for i in ids[:200]]
        params.append(("limit", "250"))
        rl = await c.get(f"{base_url()}/leads", params=params)
        if rl.status_code != 200:
            return {**vacio, "error": _explicar(rl)}
        leads = rl.json().get("_embedded", {}).get("leads", [])

    compras, abiertos, perdidos, monto, ultima = 0, 0, 0, 0, None
    detalle = []
    for l in leads:
        estado = l.get("status_id")
        precio = l.get("price") or 0
        cerrado = l.get("closed_at")
        if estado == GANADO:
            compras += 1
            monto += precio
            if cerrado and (ultima is None or cerrado > ultima):
                ultima = cerrado
        elif estado == PERDIDO_ID:
            perdidos += 1
        else:
            abiertos += 1
        detalle.append({"id": l.get("id"), "nombre": l.get("name"),
                        "precio": precio, "cerrado": cerrado,
                        "ganado": estado == GANADO,
                        "perdido": estado == PERDIDO_ID})

    # Lo ganado primero y lo más reciente arriba: es el orden en que alguien
    # lee esto antes de llamar.
    detalle.sort(key=lambda d: (not d["ganado"], -(d["cerrado"] or 0)))
    return {"compras": compras, "abiertos": abiertos, "perdidos": perdidos,
            "monto": monto, "ultima": ultima, "leads": detalle}


async def listar_embudos() -> dict:
    """Los embudos con sus etapas, para elegir a dónde caen los contactos."""
    if not configured():
        return {"error": "Faltan KOMMO_SUBDOMAIN y KOMMO_TOKEN en el .env",
                "pipelines": []}
    async with httpx.AsyncClient(timeout=TIEMPO, headers=_headers()) as client:
        r = await client.get(f"{base_url()}/leads/pipelines")
        if r.status_code != 200:
            return {"error": _explicar(r), "pipelines": []}
        salida = []
        for p in r.json().get("_embedded", {}).get("pipelines", []):
            salida.append({
                "id": p["id"], "name": p.get("name"),
                "is_main": bool(p.get("is_main")),
                "statuses": [{"id": e["id"], "name": e.get("name")}
                             for e in p.get("_embedded", {}).get("statuses", [])]})

        usuarios = []
        ru = await client.get(f"{base_url()}/users", params={"limit": 250})
        if ru.status_code == 200:
            usuarios = [{"id": u["id"], "name": u.get("name") or u.get("email")}
                        for u in ru.json().get("_embedded", {}).get("users", [])]

    return {"pipelines": salida, "users": usuarios,
            "selected_pipeline": (os.getenv("KOMMO_PIPELINE_ID") or "").strip(),
            "selected_status": (os.getenv("KOMMO_STATUS_ID") or "").strip(),
            "selected_user": (os.getenv("KOMMO_RESPONSIBLE_ID") or "").strip()}
