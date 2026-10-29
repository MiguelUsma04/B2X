"""La secuencia de tres correos.

A quien no contesta, B2K le escribe tres veces, espaciadas en días hábiles, y
con cada correo lo mueve en Kommo: CORREO #1, #2 y #3. Si después del tercero
sigue sin contestar, pasa a NO INTERESADO. Si contesta —a cualquiera de los
tres— la secuencia se corta y el lead pasa a IA CONVERSANDO, diga lo que diga:
así lo decidió el equipo el 29 de octubre de 2026.

Lo que no se negocia, porque es lo que más daño hace a la marca: **no
escribirle el correo siguiente a alguien que ya contestó**. Por eso, ante
cualquier duda, se frena: si no se pudo leer el buzón después del último
envío, no se sabe si contestó, y el siguiente correo espera.

Los correos 2 y 3 son una plantilla con {{variables}}, distinta para cada
paso, que se escribe al lanzar la secuencia. Salen del mismo buzón que el 1 y
como respuesta en el mismo hilo: un seguimiento que llega de otra dirección
parece otra empresa.
"""
import json
import secrets
import time
import unicodedata
from datetime import datetime, timedelta, timezone

from . import kommo, respuestas
from .db import get_db

PASOS = 3
PLAZOS = {"2": 3, "3": 3, "fin": 5}       # días hábiles, desde el envío anterior

TEXTO_2 = """Hola de nuevo,

Te escribí hace unos días y no quería que el correo se perdiera entre tantos otros.

Hay una pregunta que suele aclarar mucho: hoy, ¿cuántos clientes nuevos le llegan a {{empresa}} cada mes sin tener que salir a buscarlos? En la mayoría de las empresas con las que hablamos, ese número depende casi todo del boca a boca.

Si te sirve, te preparo un diagnóstico corto de dónde vemos oportunidades para ustedes. ¿Te interesa?"""

TEXTO_3 = """Hola,

Este es mi último correo sobre el tema, para no llenarte la bandeja.

Si en algún momento quieren que {{empresa}} consiga clientes nuevos de forma constante, nos encantaría mostrarles cómo lo hacemos en una llamada de 20 minutos, sin compromiso.

Si no es el momento, no hay problema: con que me respondas «ahora no», dejo de escribirte."""


# ------------------------------------------------------------ configuración
def nueva_config(cuerpo2: str, cuerpo3: str, dias2=None, dias3=None,
                 dias_fin=None) -> str:
    """Lo que se guarda en la campaña: los textos y los plazos."""
    def dias(v, x):
        try:
            return max(1, min(30, int(v)))
        except (TypeError, ValueError):
            return x
    return json.dumps({
        "cuerpo2": (cuerpo2 or "").strip() or TEXTO_2,
        "cuerpo3": (cuerpo3 or "").strip() or TEXTO_3,
        "dias2": dias(dias2, PLAZOS["2"]), "dias3": dias(dias3, PLAZOS["3"]),
        "dias_fin": dias(dias_fin, PLAZOS["fin"]),
    }, ensure_ascii=False)


def config_de(texto: str | None) -> dict | None:
    if not texto:
        return None
    try:
        return json.loads(texto) or None
    except ValueError:
        return None


def dias_habiles(desde: datetime, n: int) -> datetime:
    """`desde` más n días de lunes a viernes, a la misma hora."""
    d = desde
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _fecha(texto: str) -> datetime:
    return datetime.strptime(texto[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

# -------------------------------------------------------- etapas en Kommo
# Se buscan por nombre en el embudo al que entran los leads. Así no hay que
# configurar nada si el embudo se llama como el de gmarketing, y si el equipo
# renombra una etapa con otro número, sigue encontrándola por el principio.
NOMBRES = {"1": "CORREO #1", "2": "CORREO #2", "3": "CORREO #3",
           "respuesta": "IA CONVERSANDO"}

_cache: dict = {"at": 0.0, "etapas": {}}


def _normal(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c for c in t if not unicodedata.combining(c)).upper().strip()


async def etapas() -> dict:
    """{"1": {"id", "name"}, ..., "respuesta": ..., "sin_respuesta": ...}

    Vacío si no hay embudo configurado: entonces los correos salen igual,
    pero no se mueve nada.
    """
    if time.time() - _cache["at"] < 600 and _cache["etapas"]:
        return _cache["etapas"]
    embudo = kommo.destino_del_lead().get("pipeline_id")
    lista = await kommo.etapas_del_embudo(embudo) if embudo else []
    salida = {}
    for clave, nombre in NOMBRES.items():
        for e in lista:
            if _normal(e["name"]).startswith(_normal(nombre)):
                salida[clave] = {"id": e["id"], "name": e["name"]}
                break
    # El perdido de Kommo es el mismo número en todos los embudos.
    perdido = next((e for e in lista if e["id"] == kommo.PERDIDO_ID), None)
    if perdido:
        salida["sin_respuesta"] = {"id": perdido["id"], "name": perdido["name"]}
    _cache.update(at=time.time(), etapas=salida)
    return salida


def _nombres_de_etapa(et: dict) -> list[str]:
    return [v["name"] for v in et.values()]

# --------------------------------------------------------------- inscribir
def inscribir(conn, campania: int, filas: list[tuple]) -> None:
    """Anota a cada contacto que entró en la campaña. (contact_id, asunto)"""
    for contact_id, asunto in filas:
        lead = conn.execute("SELECT crm_lead_id FROM contacts WHERE id=?",
                            (contact_id,)).fetchone()
        conn.execute(
            """INSERT OR IGNORE INTO secuencias (campaign_id, contact_id, asunto, lead_id)
               VALUES (?,?,?,?)""",
            (campania, contact_id, asunto, (lead["crm_lead_id"] if lead else None) or None))


def _familia(conn, raiz: int) -> list[int]:
    return [raiz] + [r["id"] for r in conn.execute(
        "SELECT id FROM email_campaigns WHERE padre_id=?", (raiz,))]


def _de_la_fila(conn, campaign_id: int, contact_id: int):
    """La fila de secuencia a la que pertenece un correo de la cola."""
    camp = conn.execute("SELECT id, padre_id, paso FROM email_campaigns WHERE id=?",
                        (campaign_id,)).fetchone()
    if not camp:
        return None, 1
    raiz = camp["padre_id"] or camp["id"]
    s = conn.execute("SELECT * FROM secuencias WHERE campaign_id=? AND contact_id=?",
                     (raiz, contact_id)).fetchone()
    return (dict(s) if s else None), (camp["paso"] or 1)


def _cancelar_pendientes(conn, s: dict) -> None:
    """Saca de la cola lo que iba a salir: la secuencia ya terminó."""
    fam = _familia(conn, s["campaign_id"])
    marcas = ",".join("?" * len(fam))
    conn.execute(
        f"""UPDATE email_queue SET status='cancelled'
             WHERE status='pending' AND contact_id=? AND campaign_id IN ({marcas})""",
        [s["contact_id"], *fam])


def _contesto(conn, s: dict):
    fam = _familia(conn, s["campaign_id"])
    marcas = ",".join("?" * len(fam))
    return conn.execute(
        f"""SELECT e.id, r.id rid, r.texto, r.clase, r.confianza, r.porque
              FROM email_events e LEFT JOIN respuestas r ON r.event_id = e.id
             WHERE e.contact_id=? AND e.kind='reply' AND e.bot=0
               AND e.campaign_id IN ({marcas})
             ORDER BY e.id LIMIT 1""", [s["contact_id"], *fam]).fetchone()


def puede_salir(fila: dict) -> bool:
    """El último control antes de mandar un correo 2 o 3.

    El vigilante corre cada minuto, pero el buzón se lee cada diez y el envío
    cada veinte segundos: una respuesta puede llegar entre que el correo se
    encoló y que sale. Acá se vuelve a mirar, justo antes.
    """
    with get_db() as conn:
        s, paso = _de_la_fila(conn, fila["campaign_id"], fila["contact_id"])
        if not s or paso == 1:
            return True
        if s["estado"] != "activa" or _contesto(conn, s):
            conn.execute("UPDATE email_queue SET status='cancelled' WHERE id=?",
                         (fila["id"],))
            return False
    return True

# ------------------------------------------------------------- en Kommo
async def _llevar(s: dict, clave: str, motivo: str, detalle: str = "") -> dict:
    """Mueve el lead a la etapa del paso y le cambia la etiqueta.

    Devuelve {"ok": bool, "cortar": str|None}: `cortar` dice por qué la
    secuencia no puede seguir para este lead.
    """
    if not s.get("lead_id") or not kommo.configured():
        return {"ok": False, "cortar": None}
    et = await etapas()
    destino = et.get(clave)
    if not destino:
        return {"ok": False, "cortar": None}

    r = await kommo.mover_a(s["lead_id"], destino["id"], motivo, detalle)
    if r.get("status_id") != destino["id"]:
        if "status_id" in r:
            # Kommo contestó, pero el lead no puede ir ahí: está cerrado o
            # alguien del equipo lo llevó más lejos.
            return {"ok": False, "cortar": r["motivo"]}
        return {"ok": False, "cortar": None, "error": r.get("motivo")}

    await kommo.poner_etiqueta_de_etapa(s["lead_id"], destino["name"],
                                        _nombres_de_etapa(et))
    with get_db() as conn:
        conn.execute("UPDATE secuencias SET etapa_b2k=? WHERE id=?",
                     (destino["id"], s["id"]))
    return {"ok": True, "cortar": None}


def _cortar(s: dict, motivo: str, estado: str = "cortada") -> None:
    with get_db() as conn:
        conn.execute("UPDATE secuencias SET estado=?, motivo=? WHERE id=?",
                     (estado, motivo, s["id"]))
        _cancelar_pendientes(conn, s)

# ---------------------------------------------------- cuando sale un correo
async def al_enviar(fila: dict, message_id: str, buzon: int | None) -> None:
    """Anota el paso que salió y mueve el lead. Lo llama el goteo."""
    with get_db() as conn:
        s, paso = _de_la_fila(conn, fila["campaign_id"], fila["contact_id"])
        if not s or s["estado"] != "activa":
            return
        hilo = " ".join(filter(None, [s["hilo"], message_id]))
        # El lead puede haberse creado en Kommo después de lanzar la campaña.
        if not s["lead_id"]:
            c = conn.execute("SELECT crm_lead_id FROM contacts WHERE id=?",
                             (s["contact_id"],)).fetchone()
            s["lead_id"] = (c["crm_lead_id"] if c else None) or None
        conn.execute(
            """UPDATE secuencias SET paso=?, ultimo_envio=?, hilo=?, motivo=NULL,
                 buzon=COALESCE(buzon, ?), lead_id=? WHERE id=?""",
            (paso, _iso(_ahora()), hilo, buzon, s["lead_id"], s["id"]))
        s.update(paso=paso, hilo=hilo)

    if paso == 1 and s.get("lead_id") and kommo.configured():
        # Un lead que está en otro embudo lo puso ahí alguien: no se lo
        # arrastra al de B2K. Recibe este correo y nada más.
        lead = await kommo.leer_lead(s["lead_id"])
        embudo = kommo.destino_del_lead().get("pipeline_id")
        if lead.get("no_existe"):
            with get_db() as conn:
                conn.execute("UPDATE secuencias SET lead_id=NULL WHERE id=?", (s["id"],))
            return
        if embudo and lead.get("pipeline_id") and lead["pipeline_id"] != embudo:
            _cortar(s, "El lead está en otro embudo de Kommo: recibió solo el "
                       "primer correo.")
            return

    r = await _llevar(s, str(paso), f"Secuencia: salió el correo {paso}")
    if r["cortar"]:
        _cortar(s, r["cortar"])

# ------------------------------------------------------------- el vigilante
async def _respondio(s: dict, ev) -> None:
    """Contestó: se corta la secuencia y el lead pasa a IA CONVERSANDO."""
    from . import mailer
    texto = (ev["texto"] or "").strip()
    clase = {"clase": ev["clase"], "confianza": ev["confianza"],
             "porque": ev["porque"]}
    if ev["rid"] and not ev["clase"] and texto:
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            res = await respuestas.clasificar(client, texto)
        if not res["error"]:
            clase = res
            with get_db() as conn:
                conn.execute(
                    "UPDATE respuestas SET clase=?, confianza=?, porque=?, "
                    "clasificada=datetime('now') WHERE id=?",
                    (res["clase"], res["confianza"], res["porque"], ev["rid"]))

    # Quien pide que no le escriban más no recibe ningún correo más, de
    # ninguna campaña, aunque el lead siga en IA CONVERSANDO.
    if clase["clase"] == "no_escribir" and respuestas.es_confiable(clase["confianza"] or ""):
        with get_db() as conn:
            c = conn.execute("SELECT email FROM contacts WHERE id=?",
                             (s["contact_id"],)).fetchone()
        if c:
            mailer.suprimir(c["email"], "pidió no recibir más (en su respuesta)",
                            s["contact_id"])

    with get_db() as conn:
        conn.execute("UPDATE secuencias SET estado='respondio', motivo=? WHERE id=?",
                     ("Contestó el correo " + str(s["paso"] or 1), s["id"]))
        _cancelar_pendientes(conn, s)

    detalle = ""
    if texto:
        detalle = f"\nLo que escribió:\n{texto[:2000]}"
    if clase["clase"]:
        detalle += (f"\n\nLa IA lo leyó como: {clase['clase']} "
                    f"(confianza {clase['confianza']}). {clase['porque'] or ''}")
    await _llevar(s, "respuesta", "Contestó el correo de la secuencia", detalle)


async def _revisar(s: dict, ahora: datetime) -> None:
    from . import mailer
    with get_db() as conn:
        raiz = conn.execute("SELECT * FROM email_campaigns WHERE id=?",
                            (s["campaign_id"],)).fetchone()
        if not raiz:
            return
        if raiz["status"] == "cancelled":
            _cortar(s, "Se canceló la campaña.")
            return
        if raiz["status"] == "paused":
            return

        # Primero la hora de la última lectura del buzón y recién después las
        # respuestas: una lectura anota las respuestas antes de anotar la
        # hora, así que si la hora es posterior al envío, lo leído está acá.
        buzon = conn.execute(
            "SELECT imap_checked, imap_error FROM smtp_config WHERE id=?",
            (s["buzon"],)).fetchone() if s["buzon"] else None
        ev = _contesto(conn, s)
        fam = _familia(conn, s["campaign_id"])
        marcas = ",".join("?" * len(fam))
        malo = conn.execute(
            f"""SELECT kind FROM email_events WHERE contact_id=?
                  AND kind IN ('bounce', 'unsub') AND campaign_id IN ({marcas})
                LIMIT 1""", [s["contact_id"], *fam]).fetchone()
        suprimido = conn.execute(
            """SELECT 1 FROM suppression s JOIN contacts c
                 ON LOWER(c.email) = s.email WHERE c.id=?""",
            (s["contact_id"],)).fetchone()
        envios = {r["paso"]: r["status"] for r in conn.execute(
            f"""SELECT k.paso, q.status FROM email_queue q
                  JOIN email_campaigns k ON k.id = q.campaign_id
                 WHERE q.contact_id=? AND q.campaign_id IN ({marcas})""",
            [s["contact_id"], *fam])}

    if ev:
        await _respondio(s, ev)
        return
    if malo:
        _cortar(s, "El correo rebotó." if malo["kind"] == "bounce"
                else "Pidió no recibir más correos.")
        return
    if suprimido:
        _cortar(s, "La dirección está en la lista de no escribir.")
        return

    paso = s["paso"] or 0
    ultimo = envios.get(paso or 1)
    if ultimo in ("error", "cancelled"):
        _cortar(s, f"El correo {paso or 1} no salió.")
        return
    if paso == 0 or envios.get(paso + 1) == "pending":
        return                       # esperando a que salga lo que ya está en cola

    cfg = config_de(raiz["secuencia"]) or {}
    dias = {1: cfg.get("dias2"), 2: cfg.get("dias3")}.get(paso, cfg.get("dias_fin"))
    vence = dias_habiles(_fecha(s["ultimo_envio"]), int(dias or PLAZOS["fin"]))
    if ahora < vence:
        return

    # Sin leer el buzón después del último envío no se sabe si contestó.
    if not buzon or buzon["imap_error"] or not buzon["imap_checked"] \
            or buzon["imap_checked"] <= s["ultimo_envio"]:
        with get_db() as conn:
            conn.execute("UPDATE secuencias SET motivo=? WHERE id=?",
                         ("Esperando poder leer el buzón para saber si contestó.",
                          s["id"]))
        return

    # Si alguien del equipo movió el lead, ya lo está trabajando él.
    if s["lead_id"] and s["etapa_b2k"] and kommo.configured():
        lead = await kommo.leer_lead(s["lead_id"])
        if lead.get("error") and not lead.get("no_existe"):
            return                   # Kommo no contestó: se prueba en un minuto
        if not lead.get("no_existe") and lead["status_id"] != s["etapa_b2k"]:
            _cortar(s, "Alguien del equipo movió el lead en Kommo.")
            return

    if paso >= PASOS:
        with get_db() as conn:
            conn.execute("UPDATE secuencias SET estado='sin_respuesta', motivo=? "
                         "WHERE id=?", ("No contestó ninguno de los tres.", s["id"]))
        await _llevar(s, "sin_respuesta", "Secuencia: no contestó ninguno de los tres")
        return

    _encolar(s, paso + 1, raiz, cfg, ahora, mailer)


def _encolar(s: dict, n: int, raiz, cfg: dict, ahora: datetime, mailer) -> None:
    """Pone el correo n en la cola, en la campaña hija de ese paso."""
    with get_db() as conn:
        hija = conn.execute(
            "SELECT id, status FROM email_campaigns WHERE padre_id=? AND paso=?",
            (raiz["id"], n)).fetchone()
        if hija:
            hija_id = hija["id"]
            if hija["status"] == "done":
                conn.execute("UPDATE email_campaigns SET status='running' WHERE id=?",
                             (hija_id,))
        else:
            hija_id = conn.execute(
                """INSERT INTO email_campaigns
                     (name, subject, body, track_base, every_seconds,
                      jitter_seconds, daily_cap, padre_id, paso)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (f"{raiz['name'] or 'Secuencia'} · correo {n}", raiz["subject"],
                 cfg.get(f"cuerpo{n}") or "", raiz["track_base"],
                 raiz["every_seconds"], raiz["jitter_seconds"], raiz["daily_cap"],
                 raiz["id"], n)).lastrowid

        contacto = dict(conn.execute("SELECT * FROM contacts WHERE id=?",
                                     (s["contact_id"],)).fetchone())
        cuerpo = mailer.render(cfg.get(f"cuerpo{n}") or "", contacto)
        marca = secrets.token_urlsafe(16)
        if raiz["track_base"]:
            cuerpo += mailer._pie_de_baja(raiz["track_base"], marca, html=False)
        asunto = s["asunto"] or raiz["subject"] or ""
        if not asunto.lower().startswith(("re:", "re :")):
            asunto = "Re: " + asunto
        conn.execute(
            """INSERT OR IGNORE INTO email_queue
                 (campaign_id, contact_id, email, subject, body, token,
                  send_after, buzon_fijo, responde_a)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (hija_id, s["contact_id"], contacto["email"], asunto, cuerpo, marca,
             _iso(ahora), s["buzon"], s["hilo"]))
        conn.execute("UPDATE secuencias SET motivo=NULL WHERE id=?", (s["id"],))


async def avanzar() -> dict:
    """Una vuelta del vigilante sobre todas las secuencias activas."""
    ahora = _ahora()
    with get_db() as conn:
        activas = [dict(r) for r in conn.execute(
            "SELECT * FROM secuencias WHERE estado='activa' ORDER BY id")]
    for s in activas:
        try:
            await _revisar(s, ahora)
        except Exception as exc:
            print(f"[secuencia] {s['id']}: {type(exc).__name__}: {exc}", flush=True)
    return {"mirados": len(activas)}


def resumen(campania: int) -> dict:
    """Cómo va la secuencia de una campaña, para la pantalla."""
    with get_db() as conn:
        filas = [dict(r) for r in conn.execute(
            """SELECT s.paso, s.estado, s.motivo, c.full_name, c.email
                 FROM secuencias s LEFT JOIN contacts c ON c.id = s.contact_id
                WHERE s.campaign_id=? ORDER BY s.id""", (campania,))]
    cuenta = {"activa": 0, "respondio": 0, "sin_respuesta": 0, "cortada": 0}
    por_paso = {0: 0, 1: 0, 2: 0, 3: 0}
    for f in filas:
        cuenta[f["estado"]] = cuenta.get(f["estado"], 0) + 1
        if f["estado"] == "activa":
            por_paso[f["paso"] or 0] = por_paso.get(f["paso"] or 0, 0) + 1
    return {"total": len(filas), **cuenta,
            "en_paso": por_paso,
            "salidas": [f for f in filas if f["estado"] != "activa"][-30:],
            "esperando": [f for f in filas if f["estado"] == "activa" and f["motivo"]][-10:]}
