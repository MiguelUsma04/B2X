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
import time

import httpx

from . import telefonos
from .db import get_db

TIEMPO = 30.0
# Kommo permite unas 7 consultas por segundo por cuenta. Se va a 5 a
# propósito: el techo es de la cuenta entera, no de esta app, y si alguien
# está trabajando en Kommo al mismo tiempo el límite se comparte.
#
# El ritmo es de la cuenta y no de cada llamada, así que el control tiene que
# ser uno solo para toda la app: si el envío de contactos y el obrero que
# mueve las etapas corren a la vez, cada uno respetando su propia pausa, entre
# los dos se pasan del límite igual. Por eso hay un solo portero acá.
ESPERA = float(os.getenv("KOMMO_ESPERA") or 0.2)
REINTENTOS = 3

_ultimo = 0.0
_castigo = 0.0          # cuánto se está yendo más lento por haber recibido 429
_turno = asyncio.Lock()


async def _esperar_turno() -> None:
    """Deja pasar una consulta y anota cuándo, para espaciar la siguiente."""
    global _ultimo
    async with _turno:
        ahora = time.monotonic()
        falta = (ESPERA + _castigo) - (ahora - _ultimo)
        if falta > 0:
            await asyncio.sleep(falta)
        _ultimo = time.monotonic()


def _frenar() -> None:
    """Kommo dijo que vamos muy rápido: se baja el ritmo un rato.

    Sin esto, un 429 hacía esperar solo a esa consulta y la siguiente salía
    al mismo ritmo que la provocó: se entra en una pelea con el servidor que
    termina en más 429 y más lentitud que si se hubiera bajado de una.
    """
    global _castigo
    _castigo = min(2.0, (_castigo or ESPERA) * 2)


def _aflojar() -> None:
    """Después de un rato bien, se vuelve al ritmo normal de a poco."""
    global _castigo
    if _castigo:
        _castigo = _castigo / 2 if _castigo > 0.05 else 0.0


SIN_CONFIGURAR = (
    "Kommo no está configurado en este servidor: faltan KOMMO_SUBDOMAIN y "
    "KOMMO_TOKEN. Van en el archivo .env que está al lado del "
    "docker-compose.yml, y el bloque del compose tiene que nombrarlas. "
    "Después hay que volver a desplegar: cambiar el .env no alcanza mientras "
    "el contenedor siga levantado con la configuración vieja."
)


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
            if code in ("PHONE", "EMAIL", "POSITION"):
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
# Los campos de la pestaña B2K, en el orden en que se leen: primero lo que
# sirve para abrir una conversación, después los datos duros, y al final de
# dónde salió. Los que faltan se crean solos la primera vez.
CAMPOS_LEAD = [
    ("Resumen del negocio", "textarea"),
    ("Gancho", "textarea"),
    ("Novedades", "textarea"),
    ("Ciudad", "text"),
    ("Dirección", "textarea"),
    ("Rubro", "text"),
    ("Especialidad", "text"),
    ("Qué vende", "textarea"),
    ("A quién le vende", "text"),
    ("Antigüedad", "text"),
    ("Sitio web", "url"),
    ("Calificación en Google", "text"),
    ("De dónde salió", "text"),
    ("Ficha en Google Maps", "url"),
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


def _ficha_maps(c: dict) -> str:
    """El enlace a la ficha del negocio en Google Maps.

    Google devuelve la dirección hecha, pero no siempre: cuando falta se arma
    con el identificador del lugar, que sí viene en todos. Sin esto, la mitad
    de las tarjetas decía "salió de Google Maps" sin forma de ir a mirarla, y
    el comercial terminaba buscando el negocio a mano.
    """
    url = (c.get("maps_url") or "").strip()
    if url:
        return url
    pid = (c.get("place_id") or "").strip()
    if pid:
        return f"https://www.google.com/maps/place/?q=place_id:{pid}"
    return ""


def _ciudad_de(direccion: str) -> str:
    """La ciudad de una dirección de Google, que viene por comas.

    Google devuelve "Cra 7 #1-2, Chapinero, Bogotá, Colombia": el país va
    último y la ciudad justo antes. Con menos partes no se puede afirmar
    nada, y una ciudad inventada en la tarjeta es peor que una vacía.
    """
    partes = [p.strip() for p in (direccion or "").split(",") if p.strip()]
    if len(partes) < 3:
        return ""
    ciudad = partes[-2]
    # Un pedazo que es solo números es un código postal, no una ciudad.
    return "" if ciudad.replace(" ", "").isdigit() else ciudad


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

    # La ciudad sale de la ficha de IA si la hay, y si no de la dirección.
    # Antes solo salía de la ficha: un contacto de Maps sin ficha llegaba con
    # la ciudad vacía teniendo la dirección completa al lado.
    ciudad = ciudades[0] if ciudades else _ciudad_de(c.get("address") or "")

    novedades = perfil.get("novedades") or []
    especialidad = perfil.get("especialidad") or ""
    if especialidad in ("no_esta_claro", "otro"):
        especialidad = ""

    return {
        "Resumen del negocio": (perfil.get("resumen") or c.get("ai_summary") or ""),
        "Gancho": perfil.get("gancho") or "",
        "Novedades": " · ".join(n for n in novedades if n),
        "Ciudad": ciudad,
        "Dirección": c.get("address") or "",
        "Rubro": c.get("category") or "",
        "Especialidad": especialidad,
        "Qué vende": ", ".join(perfil.get("que_vende") or []),
        "A quién le vende": {"empresas": "A empresas",
                             "consumidor_final": "Al consumidor final",
                             "ambos": "A empresas y a consumidor final"}
                            .get(perfil.get("vende_a") or "", ""),
        "Antigüedad": perfil.get("anios_en_el_mercado") or "",
        "Sitio web": sitio,
        "Calificación en Google": calificacion,
        "De dónde salió": "Google Maps" if c.get("place_id") else "Archivo de Apollo",
        "Ficha en Google Maps": _ficha_maps(c),
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
    """El contacto tal como lo espera Kommo.

    Cuando se sabe quién es la persona, el contacto es la persona y el
    negocio queda como compañía (ver armar_empresa). Cuando no se sabe, el
    contacto lleva el nombre del negocio: es lo único que hay, y un contacto
    sin nombre no lo trabaja nadie.
    """
    persona = persona_del_contacto(c)
    if persona.get("nombre"):
        nombre = f"{persona['nombre']} {persona['apellido']}".strip()
    else:
        nombre = (c.get("full_name") or c.get("company_name") or c.get("email")
                  or "Sin nombre").strip()
    cuerpo: dict = {"name": nombre[:250]}
    if persona.get("nombre"):
        cuerpo["first_name"] = persona["nombre"][:100]
        if persona.get("apellido"):
            cuerpo["last_name"] = persona["apellido"][:100]

    responsable = (os.getenv("KOMMO_RESPONSIBLE_ID") or "").strip()
    if responsable.isdigit():
        cuerpo["responsible_user_id"] = int(responsable)

    campos_valores = []
    if persona.get("cargo") and ids.get("position"):
        campos_valores.append({"field_id": ids["position"],
                               "values": [{"value": persona["cargo"][:250]}]})
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


# ================== la persona, la empresa, y quién es quién ==================
# Un lead de Maps trae el nombre del negocio y nada más: el contacto terminaba
# llamándose igual que la empresa y la sección Compañía quedaba vacía. Cuando
# el sitio nombra a alguien, esa persona es el contacto y el negocio pasa a
# ser la compañía, que es como está pensado Kommo y como lo usa un comercial:
# le escribe a una persona que trabaja en una empresa.

_CAMPOS_EMPRESA: dict | None = None


async def campos_empresa(client: httpx.AsyncClient,
                         refrescar: bool = False) -> dict:
    """Los ids de teléfono, email, web y dirección de las compañías.

    Son campos que Kommo trae de fábrica: se buscan por su código y no se
    crean, porque ya existen en toda cuenta.
    """
    global _CAMPOS_EMPRESA
    if _CAMPOS_EMPRESA is not None and not refrescar:
        return _CAMPOS_EMPRESA
    ids = {}
    try:
        r = await client.get(f"{base_url()}/companies/custom_fields",
                             params={"limit": 250})
        if r.status_code == 200:
            por_codigo = {"PHONE": "phone", "EMAIL": "email",
                          "WEB": "web", "ADDRESS": "address"}
            for f in r.json().get("_embedded", {}).get("custom_fields", []):
                clave = por_codigo.get((f.get("code") or "").upper())
                if clave:
                    ids[clave] = f["id"]
    except Exception:
        pass
    _CAMPOS_EMPRESA = ids
    return ids


def persona_del_contacto(c: dict) -> dict:
    """Quién es la persona detrás de este contacto, si se sabe.

    Devuelve {"nombre", "apellido", "cargo", "email"} o vacío. Se apoya en el
    redactor, que ya resuelve lo difícil: atar un nombre publicado en el sitio
    a la dirección a la que se escribe, sin inventar.
    """
    nombre = (c.get("first_name") or "").strip()
    apellido = (c.get("last_name") or "").strip()
    if nombre:
        return {"nombre": nombre, "apellido": apellido,
                "cargo": (c.get("job_title") or "").strip(),
                "email": (c.get("email") or "").strip()}

    from . import redactor
    p = redactor.persona_del_correo(c)
    if not p.get("nombre"):
        # Sin correo al que atarlo, solo vale si el sitio nombra a una sola
        # persona: con dos no hay forma de saber cuál es la que atiende.
        perfil = redactor._perfil(c)
        gente = [x for x in (perfil.get("personas") or [])
                 if (x.get("nombre") or "").strip()]
        empresa = redactor._normal(c.get("company_name") or "")
        gente = [x for x in gente if redactor._normal(x["nombre"]) != empresa]
        if len(gente) != 1:
            return {}
        p = gente[0]

    partes = p["nombre"].split()
    return {"nombre": partes[0],
            "apellido": " ".join(partes[1:]),
            "cargo": (p.get("cargo") or "").strip(),
            "email": (p.get("email") or c.get("email") or "").strip()}


def armar_empresa(c: dict, ids: dict) -> dict:
    """La compañía: el negocio, con sus datos de negocio."""
    nombre = (c.get("company_name") or c.get("full_name") or "").strip()
    if not nombre:
        return {}
    empresa: dict = {"name": nombre[:250]}

    sitio = (c.get("company_domain") or "").strip()
    if sitio and not sitio.startswith("http"):
        sitio = "https://" + sitio

    valores = []
    # El teléfono y el correo van en la compañía cuando son del negocio y no
    # de una persona: un info@ o un conmutador es de la empresa.
    persona = persona_del_contacto(c)
    tel = (c.get("phone") or "").strip()
    if tel and ids.get("phone") and not persona:
        valores.append({"field_id": ids["phone"],
                        "values": [{"value": tel}]})
    correo = (c.get("email") or "").strip()
    if correo and ids.get("email") and not persona.get("email"):
        valores.append({"field_id": ids["email"],
                        "values": [{"value": correo}]})
    if sitio and ids.get("web"):
        valores.append({"field_id": ids["web"], "values": [{"value": sitio}]})
    if c.get("address") and ids.get("address"):
        valores.append({"field_id": ids["address"],
                        "values": [{"value": str(c["address"])[:500]}]})
    if valores:
        empresa["custom_fields_values"] = valores
    return empresa


async def _con_reintento(client: httpx.AsyncClient, metodo: str, url: str,
                         **kw) -> httpx.Response:
    """Reintenta cuando Kommo pide esperar o se cae un momento.

    Un 429 no es un error del dato: es "vas muy rápido". Fallar ahí dejaría
    contactos sin subir por un motivo que se resuelve esperando un segundo.
    """
    espera = 1.0
    r = None
    for intento in range(REINTENTOS):
        await _esperar_turno()
        r = await client.request(metodo, url, **kw)
        if r.status_code not in (429, 502, 503, 504):
            _aflojar()
            return r
        if r.status_code == 429:
            _frenar()
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


async def actualizar_ficha(client: httpx.AsyncClient, c: dict,
                           ids_lead: dict, ids_empresa: dict) -> bool:
    """Pone al día la pestaña B2K de un lead que ya existe.

    Lo decidió el equipo: cuando B2K encuentra un dato nuevo, ese dato gana.
    Es lo razonable acá porque los campos de la pestaña B2K los llena B2K y
    nadie más: son lo que se leyó del sitio, no notas de un comercial.

    Lo que sigue sin tocarse es el nombre del lead, su etapa y su responsable
    —eso sí lo mueve gente— y los campos vacíos, porque escribir vacío en
    Kommo borra.
    """
    lead_id = (c.get("crm_lead_id") or "").strip()
    if not lead_id or not ids_lead:
        return False

    valores = []
    for nombre, texto in datos_del_lead(c).items():
        fid = ids_lead.get(nombre)
        if fid and str(texto).strip():
            valores.append({"field_id": fid,
                            "values": [{"value": str(texto)[:2000]}]})
    if not valores:
        return False

    try:
        r = await _con_reintento(client, "PATCH", f"{base_url()}/leads/{lead_id}",
                                 json={"custom_fields_values": valores})
        return r.status_code in (200, 201)
    except Exception:
        return False


async def send_contacts(contact_ids: list[int], tag: str | None = None) -> dict:
    """Sube los contactos marcados a Kommo, de a uno.

    Sin reintento automático: lo que falla queda en crm_status='error' con el
    motivo, y quien mira decide si reintenta.
    """
    if not configured():
        return {"error": SIN_CONFIGURAR,
                "sent": 0, "failed": 0, "results": []}
    if not contact_ids:
        return {"sent": 0, "failed": 0, "skipped": 0, "results": []}

    marcas = ",".join("?" * len(contact_ids))
    with get_db() as conn:
        filas = [dict(r) for r in conn.execute(
            f"SELECT * FROM contacts WHERE id IN ({marcas})", contact_ids)]

    enviados = fallidos = saltados = ya = sin_confirmar = rehechos = 0
    actualizados = 0
    resultados = []

    async with httpx.AsyncClient(timeout=TIEMPO, headers=_headers()) as client:
        ids = await campos(client)
        ids_lead = await campos_lead(client)
        ids_empresa = await campos_empresa(client)
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
                        # No se vuelve a crear, pero sí se pone al día la
                        # ficha: si B2K leyó el sitio después de haberlo
                        # subido, el resumen y el gancho existen de este lado
                        # y en Kommo sigue la tarjeta vacía.
                        puesto = await actualizar_ficha(
                            client, c, ids_lead, ids_empresa)
                        if puesto:
                            actualizados += 1
                        resultados.append({
                            "id": cid, "status": "already",
                            "message": ("Ya estaba en Kommo; se puso al día la "
                                        "ficha." if puesto else
                                        "Ya estaba en Kommo.") if hay else
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
                # La compañía va en la misma llamada: si fuera aparte y
                # fallara, quedaría un lead con contacto y sin empresa, que es
                # peor que no tenerla, porque nadie sabe que falta.
                empresa = armar_empresa(c, ids_empresa)
                if empresa:
                    dentro["companies"] = [empresa]
                lead["_embedded"] = dentro
                r = await _con_reintento(client, "POST",
                                         f"{base_url()}/leads/complex",
                                         json=[lead])

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
            "already_in_crm": ya,
            "updated": actualizados, "not_verified": sin_confirmar,
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
        return {"error": SIN_CONFIGURAR,
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

# ======================================================================
# Mover el lead de etapa según lo que pasa con el correo
# ======================================================================
# B2K ya sabe quién abrió, quién tocó un enlace, quién contestó, a quién le
# rebotó y quién pidió no recibir más. Lo que faltaba era contárselo a Kommo.
#
# Dos reglas que no se negocian, porque el que pierde si están mal es el
# comercial:
#
# 1. B2K nunca mueve un lead hacia atrás. Si alguien del equipo ya lo avanzó
#    —lo llamó, lo cotizó, lo cerró— que una apertura de correo lo devuelva a
#    "contactado" sería borrar trabajo de una persona con un evento
#    automático. Para eso está el orden del embudo: solo se avanza.
#
# 2. Un lead ganado o perdido no se toca. Esos son estados finales que puso
#    alguien a propósito.
#
# La única excepción a la primera regla es la baja: quien pide no recibir más
# se va a "cerrado perdido" venga de donde venga, porque ahí no hay nada que
# seguir trabajando.

# Qué eventos se pueden mapear, en orden de qué tan lejos llegó la relación.
EVENTOS = [
    ("enviado", "Se le envió el correo"),
    ("abierto", "Abrió el correo"),
    ("click", "Tocó un enlace del correo"),
    ("respondido", "Contestó el correo"),
    ("rebote", "El correo rebotó: esa dirección no existe"),
    ("baja", "Pidió no recibir más correos"),
]


def _mapa() -> dict:
    """Qué etapa le corresponde a cada evento. Vacío = no mover nada."""
    from .db import ajuste
    import json as _json
    try:
        return _json.loads(ajuste("kommo_etapas", "") or "{}") or {}
    except ValueError:
        return {}


def guardar_mapa(mapa: dict) -> dict:
    """Guarda el mapa, quedándose solo con los eventos conocidos."""
    from .db import poner_ajuste
    import json as _json
    limpio = {k: str(v).strip() for k, v in (mapa or {}).items()
              if k in dict(EVENTOS) and str(v).strip()}
    poner_ajuste("kommo_etapas", _json.dumps(limpio))
    return limpio


async def _orden_de_etapas(client: httpx.AsyncClient) -> dict:
    """Posición de cada etapa dentro de su embudo, para saber qué es avanzar.

    Kommo entrega las etapas con un campo `sort`. Sin eso no se puede decidir
    si un cambio es adelante o atrás, y mover a ciegas es peor que no mover.
    """
    r = await client.get(f"{base_url()}/leads/pipelines")
    if r.status_code != 200:
        return {}
    orden = {}
    for pipe in r.json().get("_embedded", {}).get("pipelines", []):
        for e in pipe.get("_embedded", {}).get("statuses", []):
            orden[int(e["id"])] = int(e.get("sort") or 0)
    return orden


async def mover_por_evento(lead_id: str, evento: str,
                           detalle: str = "") -> dict:
    """Lleva el lead a la etapa que corresponde a ese evento.

    Devuelve {"movido": bool, "motivo": str} — el motivo dice por qué no se
    movió, que es lo que hay que poder mirar después.
    """
    if not configured():
        return {"movido": False, "motivo": "Kommo no está configurado."}
    destino = _mapa().get(evento)
    if not destino:
        return {"movido": False, "motivo": f"Sin etapa asignada a «{evento}»."}
    if not lead_id:
        return {"movido": False, "motivo": "Ese contacto no tiene lead."}

    try:
        destino_id = int(destino)
    except (TypeError, ValueError):
        return {"movido": False, "motivo": "La etapa configurada no es válida."}

    try:
        async with httpx.AsyncClient(timeout=TIEMPO, headers=_headers()) as c:
            r = await _con_reintento(c, "GET", f"{base_url()}/leads/{lead_id}")
            if r.status_code == 404:
                return {"movido": False, "motivo": "Ese lead ya no está en Kommo."}
            if r.status_code != 200:
                return {"movido": False, "motivo": _explicar(r)}
            lead = r.json()
            actual = int(lead.get("status_id") or 0)

            if actual == destino_id:
                return {"movido": False, "motivo": "Ya estaba en esa etapa."}

            # La baja pasa por encima de todo: no hay nada que seguir
            # trabajando con quien pidió no recibir más.
            if evento != "baja":
                if actual in (GANADO, PERDIDO_ID):
                    return {"movido": False,
                            "motivo": "El lead ya está cerrado; no se toca."}
                orden = await _orden_de_etapas(c)
                if orden and orden.get(destino_id, 0) < orden.get(actual, 0):
                    return {"movido": False,
                            "motivo": "Iría hacia atrás: alguien del equipo ya "
                                      "lo avanzó más que esto."}

            r2 = await _con_reintento(
                c, "PATCH", f"{base_url()}/leads/{lead_id}",
                json={"status_id": destino_id})
            if r2.status_code not in (200, 201):
                return {"movido": False, "motivo": _explicar(r2)}
    except Exception as exc:
        return {"movido": False, "motivo": f"No se pudo hablar con Kommo: "
                                           f"{type(exc).__name__}"}

    etiqueta = dict(EVENTOS).get(evento, evento)
    await anotar(lead_id, f"🔀 B2K movió el lead de etapa\n"
                          f"Motivo: {etiqueta}"
                          + (f"\n{detalle}" if detalle else ""))
    return {"movido": True, "motivo": f"Movido por: {etiqueta}"}


async def mover_por_email(email: str, evento: str, detalle: str = "") -> dict:
    """Igual que el anterior, pero buscando el lead por la dirección.

    Los eventos del correo —una baja, un rebote— llegan con una dirección y
    no con un contacto: el mismo correo puede estar en dos contactos
    cargados de fuentes distintas.
    """
    from .db import get_db
    if not (email or "").strip():
        return {"movido": False, "motivo": "Sin dirección."}
    with get_db() as conn:
        filas = [dict(r) for r in conn.execute(
            """SELECT crm_lead_id FROM contacts
                WHERE LOWER(email)=LOWER(?) AND crm_lead_id IS NOT NULL
                  AND crm_lead_id <> ''""", (email.strip(),))]
    if not filas:
        return {"movido": False, "motivo": "Esa dirección no tiene lead en Kommo."}
    resultados = [await mover_por_evento(f["crm_lead_id"], evento, detalle)
                  for f in filas]
    movidos = sum(1 for r in resultados if r["movido"])
    return {"movido": bool(movidos),
            "motivo": (f"{movidos} lead(s) movidos" if movidos
                       else resultados[0]["motivo"])}
