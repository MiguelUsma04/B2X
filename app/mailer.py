"""Envío de correos: configuración SMTP, plantilla y goteo.

Es la única parte de la app que sale hacia afuera y toca gente real, así que
está pensada para no sorprender:

- Nada se manda sin que el usuario lo pida y confirme.
- El goteo vive en la base, no en memoria: reiniciar la app no pierde lo que
  faltaba ni reenvía lo ya enviado.
- Un contacto no recibe dos veces el mismo envío, y por defecto tampoco recibe
  uno nuevo si ya se le escribió antes.
"""
import asyncio
import base64
import email as _email
import html as _html
import imaplib
import random
import re
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .db import get_db

# Lo que se puede intercalar en el asunto y el cuerpo.
VARIABLES = {
    "nombre": "Nombre del contacto o del negocio",
    "empresa": "Nombre de la empresa",
    "ciudad": "Ciudad, si se conoce",
    "rubro": "Rubro según Google Maps",
    "sitio": "Dominio del sitio web",
    "resumen": "Resumen del negocio que armó la IA",
    "gancho": "El gancho que encontró la IA en su sitio",
}
_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


# ------------------------------------------------------------------ buzones
# Servidores de Google. Tienen reglas propias que no se ven hasta que un
# correo sale con el remitente cambiado o directo no sale.
_ES_GOOGLE = ("gmail.com", "googlemail.com", "google.com")


def avisos_del_buzon(b: dict) -> list[str]:
    """Lo que va a fallar y todavía no falló.

    Son avisos, no bloqueos: un alias verificado en Workspace es un caso
    legítimo de remitente distinto al usuario, y no hay forma de saberlo
    desde acá.
    """
    avisos = []
    host = (b.get("host") or "").lower()
    if not any(g in host for g in _ES_GOOGLE):
        return avisos

    usuario = (b.get("username") or "").strip().lower()
    remitente = (b.get("from_email") or "").strip().lower()
    if not usuario:
        avisos.append("Gmail no acepta enviar sin usuario: poné la dirección "
                      "completa de la cuenta.")
    elif usuario != remitente:
        avisos.append(f"El remitente ({remitente}) no es la cuenta que se "
                      f"autentica ({usuario}). Google solo deja mandar como la "
                      "cuenta o como un alias verificado en 'Enviar como'; si no, "
                      "reescribe el remitente y el correo sale desde otra dirección.")
    if b.get("has_password") is False:
        avisos.append("Falta la contraseña de aplicación. La contraseña normal "
                      "de la cuenta no funciona para enviar.")
    if (b.get("daily_cap") or 0) > 300:
        avisos.append("Un tope tan alto quema el dominio: en prospección en frío "
                      "conviene arrancar bajo e ir subiendo.")
    return avisos


def _fila_a_buzon(r) -> dict:
    """Un buzón como lo ve la UI: nunca sale la contraseña, solo si hay una."""
    d = dict(r)
    d["has_password"] = bool(d.pop("password", None))
    d["configured"] = bool(d.get("host") and d.get("from_email"))
    d["warnings"] = avisos_del_buzon(d)
    return d


def list_mailboxes() -> list[dict]:
    with get_db() as conn:
        filas = conn.execute(
            "SELECT * FROM smtp_config ORDER BY active DESC, id").fetchall()
    return [_fila_a_buzon(r) for r in filas]


def get_mailbox(mid: int) -> dict | None:
    with get_db() as conn:
        r = conn.execute("SELECT * FROM smtp_config WHERE id=?", (mid,)).fetchone()
    return _fila_a_buzon(r) if r else None


def save_mailbox(datos: dict) -> dict:
    """Crea o actualiza un buzón.

    Una contraseña vacía no borra la guardada: el formulario nunca la muestra,
    así que mandarla vacía es lo normal al editar.
    """
    mid = datos.get("id")
    mid = int(mid) if str(mid or "").strip().isdigit() else None
    host = (datos.get("host") or "").strip()
    from_email = (datos.get("from_email") or "").strip()
    if not host or not from_email:
        raise ValueError("Faltan el servidor y el correo del remitente.")

    campos = (
        (datos.get("label") or from_email).strip(),
        host,
        int(datos.get("port") or 587),
        (datos.get("username") or "").strip(),
        (datos.get("from_name") or "").strip(),
        from_email,
        datos.get("security", "starttls"),
        1 if str(datos.get("active", "1")).lower() not in ("0", "false", "off") else 0,
        int(datos.get("daily_cap") or 50),
        # Por dónde se entra a leer las respuestas. Vacío = se deduce del
        # servidor de salida.
        (datos.get("imap_host") or "").strip() or None,
        int(datos.get("imap_port") or 993),
    )
    with get_db() as conn:
        if mid:
            actual = conn.execute("SELECT password FROM smtp_config WHERE id=?",
                                  (mid,)).fetchone()
            password = datos.get("password") or (actual["password"] if actual else "")
            conn.execute(
                """UPDATE smtp_config SET label=?, host=?, port=?, username=?,
                     from_name=?, from_email=?, security=?, active=?, daily_cap=?,
                     imap_host=?, imap_port=?,
                     password=?, updated_at=datetime('now')
                   WHERE id=?""", campos + (password, mid))
        else:
            cur = conn.execute(
                """INSERT INTO smtp_config
                     (label, host, port, username, from_name, from_email,
                      security, active, daily_cap, imap_host, imap_port,
                      password)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                campos + (datos.get("password") or "",))
            mid = cur.lastrowid
    return get_mailbox(mid)


def delete_mailbox(mid: int) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM smtp_config WHERE id=?", (mid,))


def enviados_hoy(conn, mid: int) -> int:
    """Cuántos salieron hoy por este buzón.

    El día se mide en hora local, no en UTC: con UTC el tope se reiniciaba a
    las siete de la tarde en Colombia y el goteo podía mandar otra tanda
    entera la misma noche, que es justo lo que el tope existe para evitar.
    """
    return conn.execute(
        """SELECT COUNT(*) c FROM email_queue
            WHERE smtp_id=? AND status='sent'
              AND date(sent_at, 'localtime') = date('now', 'localtime')""",
        (mid,)).fetchone()["c"]


def buzones_disponibles() -> list[dict]:
    """Buzones activos y bien configurados, con lo que les queda hoy."""
    with get_db() as conn:
        filas = conn.execute(
            """SELECT * FROM smtp_config
                WHERE active=1 AND host<>'' AND from_email<>''
                ORDER BY id""").fetchall()
        salida = []
        for r in filas:
            d = dict(r)
            d["sent_today"] = enviados_hoy(conn, r["id"])
            d["remaining"] = max(0, (r["daily_cap"] or 0) - d["sent_today"])
            salida.append(d)
    return salida


def elegir_buzon() -> dict | None:
    """El próximo buzón a usar: el que más margen tiene hoy.

    Repartir así, en vez de vaciar uno y pasar al siguiente, mantiene a todos
    con un volumen parejo y bajo — que es lo que evita que los marquen.
    """
    libres = [b for b in buzones_disponibles() if b["remaining"] > 0]
    if not libres:
        return None
    libres.sort(key=lambda b: (-b["remaining"], b["last_used"] or "", b["id"]))
    return libres[0]


# Compatibilidad con el código que asumía un solo buzón.
def get_config() -> dict:
    buzones = list_mailboxes()
    if not buzones:
        return {"host": "", "port": 587, "username": "", "from_name": "",
                "from_email": "", "security": "starttls", "configured": False,
                "has_password": False}
    return buzones[0]


def save_config(datos: dict) -> dict:
    return save_mailbox(datos)


def _credenciales(mid: int | None = None) -> dict | None:
    """Credenciales del buzón pedido, o del que toque por rotación."""
    if mid:
        with get_db() as conn:
            r = conn.execute("SELECT * FROM smtp_config WHERE id=?", (mid,)).fetchone()
        return dict(r) if r and r["host"] and r["from_email"] else None
    return elegir_buzon()


# ------------------------------------------------------------------ plantilla
def render(texto: str, contacto: dict, para_html: bool = False) -> str:
    """Reemplaza {{variables}} con lo que se sabe del contacto.

    Una variable sin dato se reemplaza por vacío, nunca por el literal
    '{{nombre}}': mandar eso a un cliente es peor que una frase corta.

    Dentro de HTML el dato se escapa: una empresa que se llama "Ruiz & Cía"
    o "<Nombre>" rompe la maqueta del correo si entra crudo.
    """
    perfil = {}
    if contacto.get("ai_profile"):
        try:
            import json
            perfil = json.loads(contacto["ai_profile"]) or {}
        except Exception:
            perfil = {}

    ciudades = perfil.get("ciudades") or []
    valores = {
        "nombre": contacto.get("first_name") or contacto.get("full_name") or "",
        "empresa": contacto.get("company_name") or contacto.get("full_name") or "",
        "ciudad": ciudades[0] if ciudades else "",
        "rubro": contacto.get("category") or "",
        "sitio": contacto.get("company_domain") or "",
        "resumen": perfil.get("resumen") or contacto.get("ai_summary") or "",
        "gancho": perfil.get("gancho") or "",
    }
    def poner(m):
        dato = str(valores.get(m.group(1).lower(), ""))
        return _html.escape(dato, quote=True) if para_html else dato

    salida = _VAR_RE.sub(poner, texto or "")
    if para_html:
        # Acá los espacios y los renglones no se ven: los pone la maqueta.
        return salida.strip()
    # Si una variable vacía dejó un renglón huérfano o espacios dobles, se limpia.
    salida = re.sub(r"[ \t]{2,}", " ", salida)
    return re.sub(r"\n{3,}", "\n\n", salida).strip()


# Lo que separa párrafos cuando el HTML se pasa a texto.
_CORTES = re.compile(r"(?i)</?(?:p|div|tr|h[1-6]|li|table|blockquote)\b[^>]*>|<br\s*/?>")
_INVISIBLE = re.compile(r"(?is)<(script|style|head)\b.*?</\1>")
_ETIQUETA = re.compile(r"<[^>]+>")


# Un enlace con su rótulo: <a href="X">Y</a>
_ENLACE = re.compile(r"(?is)<a\b[^>]*?\shref=(\"|')(.*?)\1[^>]*>(.*?)</a>")


def _enlace_en_texto(m) -> str:
    """El rótulo seguido de la dirección, salvo que sean lo mismo."""
    destino = _html.unescape(m.group(2)).strip()
    rotulo = _ETIQUETA.sub("", m.group(3)).strip()
    if not destino.lower().startswith(("http://", "https://")):
        return rotulo or destino
    if not rotulo or rotulo == destino:
        return destino
    return f"{rotulo}: {destino}"


def html_a_texto(cuerpo_html: str) -> str:
    """La versión de texto de un correo diseñado.

    Todo correo sale con las dos versiones. La de texto no es un trámite: hay
    quien lee con las imágenes y el HTML apagados, y un correo que solo trae
    HTML puntúa peor en los filtros de spam. Que la escriba la máquina evita
    que alguien la olvide.
    """
    t = _INVISIBLE.sub(" ", cuerpo_html or "")
    # Los enlaces primero: en texto plano un botón que dice "Agendar" sin la
    # dirección al lado no sirve para nada.
    t = _ENLACE.sub(_enlace_en_texto, t)
    t = _CORTES.sub("\n", t)
    t = _ETIQUETA.sub("", t)
    t = _html.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    t = "\n".join(linea.strip() for linea in t.splitlines())
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def variables_desconocidas(texto: str) -> list[str]:
    return sorted({m.group(1).lower() for m in _VAR_RE.finditer(texto or "")}
                  - set(VARIABLES))


# ------------------------------------------------------------------ rastreo
# Enlaces que no se tocan: no llevan a ninguna página que se pueda medir, y
# reescribirlos rompería lo que hacen.
_NO_RASTREAR = ("mailto:", "tel:", "#", "{{")
_HREF = re.compile(r"(?i)(<a\b[^>]*?\shref=)(\"|')(.*?)\2")
_PIXEL = ('<img src="{url}" width="1" height="1" alt="" '
          'style="display:none;width:1px;height:1px">')


def _url_rastreo(base: str, tipo: str, token: str, destino: str = "") -> str:
    """La dirección por la que pasa el correo antes de llegar a su destino."""
    if tipo == "click":
        codigo = base64.urlsafe_b64encode(destino.encode()).decode().rstrip("=")
        return f"{base}/t/c/{token}?u={codigo}"
    return f"{base}/t/a/{token}.png"


def descifrar_destino(codigo: str) -> str:
    """Devuelve la dirección original de un enlace rastreado."""
    relleno = "=" * (-len(codigo) % 4)
    return base64.urlsafe_b64decode(codigo + relleno).decode(errors="replace")


def marcar_html(cuerpo_html: str, base: str, token: str) -> str:
    """Deja el correo listo para medirse: enlaces por el desvío y el pixel.

    El pixel es una imagen de un punto que el programa de correo baja al
    abrir el mensaje. Es la única forma de estimar una apertura, y es una
    estimación: quien lee con las imágenes apagadas no aparece, y Apple Mail
    las baja solo aunque nadie haya abierto nada. El clic, en cambio, es un
    hecho.
    """
    if not (cuerpo_html or "").strip() or not base:
        return cuerpo_html

    def desviar(m):
        # El href está escrito en HTML: un & aparece como &amp;. Se vuelve a
        # texto antes de guardarlo, o el clic terminaría en una dirección con
        # el "&amp;" adentro, que no lleva a ningún lado.
        destino = _html.unescape(m.group(3))
        if not destino or destino.lower().startswith(_NO_RASTREAR):
            return m.group(0)
        return f'{m.group(1)}{m.group(2)}{_html.escape(_url_rastreo(base, "click", token, destino), quote=True)}{m.group(2)}'

    salida = _HREF.sub(desviar, cuerpo_html)
    pixel = _PIXEL.format(url=_html.escape(_url_rastreo(base, "open", token), quote=True))
    # Al final del cuerpo si lo hay, y si no al final de todo.
    if "</body>" in salida.lower():
        i = salida.lower().rindex("</body>")
        return salida[:i] + pixel + salida[i:]
    return salida + pixel


def registrar_evento(token: str, kind: str, url: str = "", agent: str = "") -> dict:
    """Anota una apertura o un clic. Devuelve a dónde seguir, si hay dónde."""
    with get_db() as conn:
        fila = conn.execute(
            "SELECT id, campaign_id, contact_id, sent_at FROM email_queue "
            "WHERE token=?", (token,)).fetchone()
        if not fila:
            return {"ok": False}

        conn.execute(
            """INSERT INTO email_events
                 (queue_id, campaign_id, contact_id, kind, url, agent, bot)
               VALUES (?,?,?,?,?,?,?)""",
            (fila["id"], fila["campaign_id"], fila["contact_id"], kind,
             url or None, (agent or "")[:200], int(_es_maquina(agent, fila["sent_at"]))))
    return {"ok": True}


# Aparatos que abren el correo sin que nadie lo lea: antivirus del servidor
# del destinatario, filtros de la empresa, robots sueltos.
_MAQUINAS = ("proofpoint", "barracuda", "mimecast", "symantec", "forcepoint",
             "trendmicro", "sophos", "bitdefender", "curl/", "wget",
             "python-requests", "bot", "spider", "crawler")


def _es_maquina(agent: str, sent_at: str | None) -> bool:
    """Si esto lo abrió un filtro y no una persona.

    Dos señales. Una, el nombre del programa. Dos, el reloj: un correo que se
    'abre' en los primeros segundos lo abrió el antivirus del servidor que lo
    recibió, porque nadie lee tan rápido.

    El proxy de imágenes de Gmail NO entra acá: Gmail baja la imagen recién
    cuando la persona abre el mensaje, así que ahí sí hubo alguien.
    """
    a = (agent or "").lower()
    if any(m in a for m in _MAQUINAS):
        return True
    if sent_at:
        try:
            salida = datetime.fromisoformat(sent_at).replace(tzinfo=timezone.utc)
            return (_ahora() - salida).total_seconds() < 5
        except ValueError:
            pass
    return False


# ------------------------------------------------------- leer el buzón
# Un Message-ID tal como aparece escrito en una cabecera.
_MSGID = re.compile(r"<[^<>@\s]+@[^<>@\s]+>")

# Quién manda un rebote. No hay un estándar que todos respeten, así que se
# mira de dónde viene y qué dice.
# Solo los que existen para avisar de un fallo. Un "noreply@" cualquiera no
# entra: hay mucho correo automático legítimo que sale de una dirección así,
# y contarlo como rebote diría que una dirección buena está rota.
_DAEMONS = ("mailer-daemon@", "postmaster@")
_ASUNTO_REBOTE = re.compile(
    r"(?i)undeliver|delivery status|delivery failure|failure notice|returned mail"
    r"|no se pudo entregar|devuelto|mail delivery")


def _host_imap(b: dict) -> str:
    """Por dónde se entra a leer el buzón.

    Casi siempre es el mismo servidor con otro nombre: si manda por
    smtp.gmail.com, se lee por imap.gmail.com. Se puede escribir a mano
    cuando no sigue esa costumbre.
    """
    if (b.get("imap_host") or "").strip():
        return b["imap_host"].strip()
    host = (b.get("host") or "").strip().lower()
    if host.startswith("smtp."):
        return "imap." + host[5:]
    if "smtp" in host:
        return host.replace("smtp", "imap", 1)
    return ""


def _es_rebote(msg) -> bool:
    """Si esto es un aviso de que el correo no llegó."""
    tipo = (msg.get_content_type() or "").lower()
    if tipo == "multipart/report":
        return "delivery-status" in (msg.get("Content-Type") or "").lower()
    de = (msg.get("From") or "").lower()
    if any(d in de for d in _DAEMONS):
        return True
    return bool(_ASUNTO_REBOTE.search(msg.get("Subject") or ""))


def _es_automatico(msg) -> bool:
    """Una respuesta que escribió un programa, no una persona.

    El 'estoy de vacaciones' no es una respuesta: contarlo como interés
    llevaría a llamar a alguien que ni leyó el correo.
    """
    auto = (msg.get("Auto-Submitted") or "").lower()
    if auto and auto != "no":
        return True
    return bool(msg.get("X-Autoreply") or msg.get("X-Autorespond")
                or (msg.get("Precedence") or "").lower() in ("auto_reply", "bulk"))


def _correo_original(conn, ids: list[str]):
    """Cuál de nuestros envíos corresponde a los identificadores citados."""
    ids = [i for i in ids if i][:60]
    if not ids:
        return None
    marcas = ",".join("?" * len(ids))
    return conn.execute(
        f"SELECT id, campaign_id, contact_id FROM email_queue "
        f"WHERE message_id IN ({marcas}) ORDER BY id DESC LIMIT 1", ids).fetchone()


def _anotar_respuesta(conn, fila, kind: str, ref: str, de: str) -> bool:
    """Anota la respuesta si no estaba ya anotada."""
    if ref and conn.execute("SELECT 1 FROM email_events WHERE ref=? AND kind=?",
                            (ref, kind)).fetchone():
        return False
    conn.execute(
        """INSERT INTO email_events
             (queue_id, campaign_id, contact_id, kind, ref, agent, bot)
           VALUES (?,?,?,?,?,?,0)""",
        (fila["id"], fila["campaign_id"], fila["contact_id"], kind, ref or None,
         (de or "")[:200]))
    return True


def _leer_buzon_sincrono(b: dict, limite: int = 300) -> dict:
    """Entra al buzón y anota lo que volvió. Bloquea: va en un hilo aparte."""
    host = _host_imap(b)
    if not host:
        return {"error": "No se sabe por dónde leer este buzón: escribí el "
                         "servidor IMAP a mano."}
    usuario = (b.get("username") or b.get("from_email") or "").strip()
    if not usuario or not b.get("password"):
        return {"error": "Falta el usuario o la contraseña del buzón."}

    try:
        M = imaplib.IMAP4_SSL(host, int(b.get("imap_port") or 993), timeout=30)
    except Exception as exc:
        return {"error": f"No se pudo conectar a {host}: {type(exc).__name__}"}

    respuestas = rebotes = automaticos = 0
    ultimo = int(b.get("imap_last_uid") or 0)
    visto = ultimo
    try:
        M.login(usuario, b["password"])
        M.select("INBOX", readonly=True)
        typ, datos = M.uid("search", None, f"(UID {ultimo + 1}:*)")
        if typ != "OK":
            return {"error": "El servidor no aceptó la búsqueda."}
        uids = [int(u) for u in (datos[0] or b"").split() if int(u) > ultimo]
        uids = uids[-limite:]

        with get_db() as conn:
            for uid in uids:
                visto = max(visto, uid)
                typ, cuerpo = M.uid("fetch", str(uid), "(BODY.PEEK[])")
                if typ != "OK" or not cuerpo or not isinstance(cuerpo[0], tuple):
                    continue
                msg = _email.message_from_bytes(cuerpo[0][1])

                # Una respuesta trae el identificador en la cabecera. Un
                # rebote lo cita adentro, en el correo original que devuelve.
                cabeceras = " ".join(filter(None, [msg.get("In-Reply-To"),
                                                   msg.get("References")]))
                fila = _correo_original(conn, _MSGID.findall(cabeceras))
                rebote = _es_rebote(msg)
                if not fila and rebote:
                    crudo = cuerpo[0][1].decode("utf-8", "replace")
                    fila = _correo_original(conn, _MSGID.findall(crudo))
                if not fila:
                    continue

                ref = (msg.get("Message-ID") or "").strip()
                de = msg.get("From") or ""
                if rebote:
                    rebotes += int(_anotar_respuesta(conn, fila, "bounce", ref, de))
                elif _es_automatico(msg):
                    automaticos += 1
                else:
                    respuestas += int(_anotar_respuesta(conn, fila, "reply", ref, de))
    except imaplib.IMAP4.error as exc:
        return {"error": _explicar_imap(exc)}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}
    finally:
        try:
            M.logout()
        except Exception:
            pass

    with get_db() as conn:
        conn.execute("UPDATE smtp_config SET imap_last_uid=?, imap_error=NULL, "
                     "imap_checked=datetime('now') WHERE id=?", (visto, b["id"]))
    return {"respuestas": respuestas, "rebotes": rebotes,
            "automaticos": automaticos, "revisados": len(uids)}


def _explicar_imap(exc: Exception) -> str:
    texto = str(exc).lower()
    if "auth" in texto or "credential" in texto or "login" in texto:
        return ("El servidor rechazó usuario o contraseña. Con Gmail o Workspace "
                "va la misma contraseña de aplicación del envío, y el IMAP tiene "
                "que estar habilitado en la cuenta.")
    return f"IMAP: {exc}"[:200]


async def revisar_buzones(mailbox_id: int | None = None) -> dict:
    """Lee los buzones y devuelve qué encontró en cada uno."""
    with get_db() as conn:
        if mailbox_id:
            filas = conn.execute("SELECT * FROM smtp_config WHERE id=?",
                                 (mailbox_id,)).fetchall()
        else:
            filas = conn.execute("SELECT * FROM smtp_config WHERE active=1 "
                                 "AND host IS NOT NULL AND host <> ''").fetchall()

    salida = []
    for f in filas:
        b = dict(f)
        r = await asyncio.to_thread(_leer_buzon_sincrono, b)
        if r.get("error"):
            with get_db() as conn:
                conn.execute("UPDATE smtp_config SET imap_error=?, "
                             "imap_checked=datetime('now') WHERE id=?",
                             (r["error"], b["id"]))
        salida.append({"id": b["id"],
                       "label": b.get("label") or b.get("from_email"), **r})
    return {"buzones": salida,
            "respuestas": sum(x.get("respuestas", 0) for x in salida),
            "rebotes": sum(x.get("rebotes", 0) for x in salida)}


# ------------------------------------------------------------------ envío
def nuevo_message_id(cfg: dict, token: str = "") -> str:
    """El identificador con el que sale un correo.

    Lleva la marca del correo adentro: cuando alguien responde, su programa
    devuelve este identificador en In-Reply-To, y ahí se sabe a qué envío
    corresponde. El dominio es el del remitente y no el de la máquina, que
    además es lo que esperan los filtros de spam.
    """
    dominio = (cfg.get("from_email") or "").split("@")[-1].strip() or None
    return make_msgid(idstring=token or None, domain=dominio)


def _enviar_sincrono(cfg: dict, destino: str, asunto: str, cuerpo: str,
                     cuerpo_html: str | None = None,
                     message_id: str | None = None) -> None:
    """Manda un correo. Bloquea: se llama siempre dentro de un hilo aparte."""
    msg = EmailMessage()
    msg["From"] = formataddr((cfg.get("from_name") or "", cfg["from_email"]))
    msg["To"] = destino
    msg["Subject"] = asunto
    msg["Message-ID"] = message_id or nuevo_message_id(cfg)
    # Primero el texto y después el HTML: el orden importa, cada programa
    # muestra la última versión que sabe leer.
    msg.set_content(cuerpo or html_a_texto(cuerpo_html or ""))
    if (cuerpo_html or "").strip():
        msg.add_alternative(cuerpo_html, subtype="html")

    contexto = ssl.create_default_context()
    if cfg["security"] == "ssl":
        servidor = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=30,
                                    context=contexto)
    else:
        servidor = smtplib.SMTP(cfg["host"], cfg["port"], timeout=30)
    try:
        servidor.ehlo()
        if cfg["security"] == "starttls":
            servidor.starttls(context=contexto)
            servidor.ehlo()
        if cfg.get("username"):
            servidor.login(cfg["username"], cfg.get("password") or "")
        servidor.send_message(msg)
    finally:
        try:
            servidor.quit()
        except Exception:
            servidor.close()


def _explicar(exc: Exception) -> str:
    """Traduce el error de SMTP a algo que se pueda accionar."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return ("El servidor rechazó usuario o contraseña. Con Gmail o Workspace "
                "hay que usar una contraseña de aplicación, no la del correo.")
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "El servidor rechazó la dirección de destino."
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "El servidor no acepta ese remitente. Revisá el correo 'De'."
    if isinstance(exc, (smtplib.SMTPConnectError, OSError)):
        return f"No se pudo conectar al servidor ({type(exc).__name__}). Revisá host y puerto."
    return f"{type(exc).__name__}: {exc}"[:300]


async def enviar(destino: str, asunto: str, cuerpo: str,
                 mailbox_id: int | None = None,
                 cuerpo_html: str | None = None) -> dict:
    """Manda un correo suelto. Devuelve {"ok": bool, "error": str|None}.

    Sin buzón indicado usa el que toque por rotación.
    """
    cfg = _credenciales(mailbox_id)
    if not cfg:
        return {"ok": False, "error": "Falta configurar el servidor de salida."}
    try:
        await asyncio.to_thread(_enviar_sincrono, cfg, destino, asunto, cuerpo,
                                cuerpo_html)
        return {"ok": True, "error": None}
    except Exception as exc:
        return {"ok": False, "error": _explicar(exc)}


# ------------------------------------------------------------------ goteo
def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def contactos_enviables(contact_ids: list[int], repetir: bool = False) -> list[dict]:
    """Los marcados que tienen email y a los que se les puede escribir.

    Sin 'repetir', se saltea a quien ya recibió un correo de cualquier campaña:
    escribirle dos veces por descuido es la forma más rápida de que marquen
    esto como spam.
    """
    if not contact_ids:
        return []
    ph = ",".join("?" * len(contact_ids))
    extra = "" if repetir else """
        AND c.id NOT IN (SELECT contact_id FROM email_queue WHERE status = 'sent')"""
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            f"""SELECT c.* FROM contacts c
                 WHERE c.id IN ({ph})
                   AND c.email IS NOT NULL AND c.email <> ''
                   {extra}
                 ORDER BY c.id""", contact_ids)]


def crear_campania(nombre: str, asunto: str, cuerpo: str, contactos: list[dict],
                   cada_segundos: int, jitter: int, tope_diario: int,
                   cuerpo_html: str = "", base_rastreo: str = "") -> dict:
    """Arma la campaña y reparte las horas de salida del goteo."""
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO email_campaigns
                 (name, subject, body, body_html, every_seconds, jitter_seconds,
                  daily_cap)
               VALUES (?,?,?,?,?,?,?)""",
            (nombre or None, asunto, cuerpo, cuerpo_html or None,
             cada_segundos, jitter, tope_diario))
        conn.execute("UPDATE email_campaigns SET track_base=? WHERE id=?",
                     (base_rastreo or None, cur.lastrowid))
        campania = cur.lastrowid

        momento = _ahora()
        enviados_hoy, dia = 0, momento.date()
        filas = []
        for c in contactos:
            if tope_diario and enviados_hoy >= tope_diario:
                # Se corta el día y se sigue mañana a la misma hora.
                momento = datetime.combine(dia + timedelta(days=1), momento.timetz())
                dia, enviados_hoy = momento.date(), 0
            # El correo se arma acá, contacto por contacto, y queda guardado:
            # así lo que sale es exactamente lo que se vio en la vista previa.
            html_armado = render(cuerpo_html, c, para_html=True) if cuerpo_html else ""
            texto = render(cuerpo, c) if cuerpo else html_a_texto(html_armado)
            # La versión de texto se saca ANTES de marcar: si no, el enlace
            # que se lee en texto plano sería el del desvío y no el real.
            marca = secrets.token_urlsafe(16)
            if html_armado and base_rastreo:
                html_armado = marcar_html(html_armado, base_rastreo, marca)
            filas.append((campania, c["id"], c["email"], render(asunto, c),
                          texto, html_armado or None, marca, _iso(momento)))
            enviados_hoy += 1
            # El jitter evita el patrón de reloj: mandar exacto cada 180 s es
            # una firma de robot para cualquier filtro de spam.
            momento += timedelta(seconds=cada_segundos + random.randint(0, max(0, jitter)))

        conn.executemany(
            """INSERT OR IGNORE INTO email_queue
                 (campaign_id, contact_id, email, subject, body, body_html,
                  token, send_after)
               VALUES (?,?,?,?,?,?,?,?)""", filas)
    return {"campaign_id": campania, "queued": len(filas),
            "termina": filas[-1][7] if filas else None}


def campanias() -> list[dict]:
    """Las campañas, de la más nueva a la más vieja, con lo básico de cada una."""
    with get_db() as conn:
        filas = conn.execute(
            """SELECT c.id, c.name, c.subject, c.status, c.created_at,
                      COUNT(q.id) total,
                      SUM(q.status='sent') enviados,
                      SUM(q.status='pending') pendientes
                 FROM email_campaigns c
                 LEFT JOIN email_queue q ON q.campaign_id = c.id
                GROUP BY c.id
                ORDER BY c.id DESC""").fetchall()
    return [dict(f) for f in filas]


def metricas(campania: int) -> dict:
    """Qué pasó con una campaña: cuántos salieron, cuántos se abrieron, quién.

    Las aperturas se cuentan por persona, no por vez: un correo que alguien
    deja abierto en una pestaña suma una sola. Y no se cuentan las de las
    máquinas, que son las que inflan el número sin que nadie haya leído nada.
    """
    with get_db() as conn:
        c = conn.execute("SELECT * FROM email_campaigns WHERE id=?",
                         (campania,)).fetchone()
        if not c:
            return {}

        env = conn.execute(
            """SELECT COUNT(*) total,
                      SUM(status='sent') enviados,
                      SUM(status='pending') pendientes,
                      SUM(status='error') errores,
                      SUM(body_html IS NOT NULL AND body_html <> '') con_diseno,
                      MIN(sent_at) primero, MAX(sent_at) ultimo
                 FROM email_queue WHERE campaign_id=?""", (campania,)).fetchone()

        # Personas distintas, no hechos: lo que importa es a cuántos les llegó
        # de verdad, no cuántas veces se abrió el mismo correo.
        gente = conn.execute(
            """SELECT SUM(abrio) abrieron, SUM(clic) clicaron,
                      SUM(resp) respondieron, SUM(reb) rebotaron FROM (
                 SELECT queue_id,
                        MAX(kind='open'   AND bot=0) abrio,
                        MAX(kind='click'  AND bot=0) clic,
                        MAX(kind='reply'  AND bot=0) resp,
                        MAX(kind='bounce' AND bot=0) reb
                   FROM email_events WHERE campaign_id=? GROUP BY queue_id)""",
            (campania,)).fetchone()

        maquinas = conn.execute(
            "SELECT COUNT(*) n FROM email_events WHERE campaign_id=? AND bot=1",
            (campania,)).fetchone()["n"]

        enlaces = conn.execute(
            """SELECT url, COUNT(*) veces, COUNT(DISTINCT queue_id) personas
                 FROM email_events
                WHERE campaign_id=? AND kind='click' AND bot=0 AND url IS NOT NULL
                GROUP BY url ORDER BY personas DESC, veces DESC""",
            (campania,)).fetchall()

        # Quién: es lo único de todo esto que se puede accionar hoy.
        quienes = conn.execute(
            """SELECT q.email, q.status, q.sent_at, q.error,
                      ct.full_name, ct.company_name, ct.id contact_id,
                      MAX(CASE WHEN e.kind='open'   AND e.bot=0 THEN e.at END) abrio,
                      MAX(CASE WHEN e.kind='click'  AND e.bot=0 THEN e.at END) clico,
                      MAX(CASE WHEN e.kind='reply'  AND e.bot=0 THEN e.at END) respondio,
                      MAX(CASE WHEN e.kind='bounce' AND e.bot=0 THEN e.at END) reboto,
                      SUM(e.kind='click' AND e.bot=0) clics
                 FROM email_queue q
                 LEFT JOIN contacts ct ON ct.id = q.contact_id
                 LEFT JOIN email_events e ON e.queue_id = q.id
                WHERE q.campaign_id=?
                GROUP BY q.id
                ORDER BY (respondio IS NULL), respondio DESC,
                         (clico IS NULL), clico DESC,
                         (abrio IS NULL), abrio DESC,
                         q.sent_at DESC""", (campania,)).fetchall()

    enviados = env["enviados"] or 0
    abrieron = (gente["abrieron"] or 0) if gente else 0
    clicaron = (gente["clicaron"] or 0) if gente else 0
    respondieron = (gente["respondieron"] or 0) if gente else 0
    rebotaron = (gente["rebotaron"] or 0) if gente else 0

    def parte(n):
        return round(100 * n / enviados, 1) if enviados else 0.0

    return {
        "campaign": dict(c),
        "total": env["total"] or 0,
        "enviados": enviados,
        "pendientes": env["pendientes"] or 0,
        "errores": env["errores"] or 0,
        "abrieron": abrieron,
        "clicaron": clicaron,
        "respondieron": respondieron,
        "rebotaron": rebotaron,
        "pct_abrieron": parte(abrieron),
        "pct_clicaron": parte(clicaron),
        "pct_respondieron": parte(respondieron),
        "maquinas": maquinas,
        "primero": env["primero"],
        "ultimo": env["ultimo"],
        # Sin diseño no hay pixel ni enlaces que desviar: no hay nada que medir.
        "medible": bool(c["track_base"]) and bool(env["con_diseno"]),
        "enlaces": [dict(e) for e in enlaces],
        "gente": [dict(g) for g in quienes],
    }


def estado(campania: int | None = None) -> dict:
    """Cómo va el goteo."""
    with get_db() as conn:
        if campania is None:
            # La que está corriendo; y si no hay ninguna, la última, para que
            # al terminar la pantalla siga mostrando cómo salió en vez de
            # quedarse en blanco justo cuando se quiere ver el resultado.
            r = conn.execute(
                """SELECT id FROM email_campaigns
                    ORDER BY (status IN ('running', 'paused')) DESC, id DESC
                    LIMIT 1""").fetchone()
            campania = r["id"] if r else None
        if campania is None:
            return {"campaign": None}

        camp = conn.execute("SELECT * FROM email_campaigns WHERE id=?",
                            (campania,)).fetchone()
        cuenta = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) n FROM email_queue WHERE campaign_id=? GROUP BY status",
            (campania,))}
        prox = conn.execute(
            """SELECT send_after FROM email_queue
                WHERE campaign_id=? AND status='pending'
                ORDER BY send_after LIMIT 1""", (campania,)).fetchone()
        ultimos = [dict(r) for r in conn.execute(
            """SELECT q.email, q.status, q.error, q.sent_at, c.full_name
                 FROM email_queue q LEFT JOIN contacts c ON c.id = q.contact_id
                WHERE q.campaign_id=? AND q.status IN ('sent','error')
                ORDER BY q.sent_at DESC LIMIT 8""", (campania,))]

    return {
        "campaign": dict(camp) if camp else None,
        "pending": cuenta.get("pending", 0),
        "sent": cuenta.get("sent", 0),
        "error": cuenta.get("error", 0),
        "cancelled": cuenta.get("cancelled", 0),
        "next_at": prox["send_after"] if prox else None,
        "last": ultimos,
    }


def cambiar_estado(campania: int, nuevo: str) -> dict:
    with get_db() as conn:
        conn.execute("UPDATE email_campaigns SET status=? WHERE id=?", (nuevo, campania))
        if nuevo == "cancelled":
            conn.execute(
                """UPDATE email_queue SET status='cancelled'
                    WHERE campaign_id=? AND status='pending'""", (campania,))
    return estado(campania)


# ------------------------------------------------------------------ obrero
INTERVALO = 20          # cada cuánto mira la cola
_worker: asyncio.Task | None = None
_lector: asyncio.Task | None = None


async def _tanda() -> None:
    """Manda lo que ya venció. Uno por vuelta: el goteo no se acelera solo."""
    cfg = _credenciales()
    if not cfg:
        return
    with get_db() as conn:
        fila = conn.execute(
            """SELECT q.* FROM email_queue q
                 JOIN email_campaigns c ON c.id = q.campaign_id
                WHERE q.status='pending' AND c.status='running'
                  AND q.send_after <= ?
                ORDER BY q.send_after LIMIT 1""", (_iso(_ahora()),)).fetchone()
    if not fila:
        return

    msg_id = nuevo_message_id(cfg, fila["token"] or "")
    with get_db() as conn:
        conn.execute("UPDATE email_queue SET message_id=? WHERE id=?",
                     (msg_id, fila["id"]))

    try:
        await asyncio.to_thread(_enviar_sincrono, cfg, fila["email"],
                                fila["subject"], fila["body"], fila["body_html"],
                                msg_id)
        ok, error = True, None
    except Exception as exc:
        ok, error = False, _explicar(exc)

    with get_db() as conn:
        # Queda anotado por qué buzón salió: así se reparte el tope diario y
        # después se puede ver cuál viene rebotando.
        conn.execute(
            """UPDATE email_queue SET status=?, error=?, smtp_id=?,
                 sent_at=datetime('now')
                WHERE id=?""",
            ("sent" if ok else "error", error, cfg.get("id"), fila["id"]))
        if ok and cfg.get("id"):
            conn.execute("UPDATE smtp_config SET last_used=datetime('now') WHERE id=?",
                         (cfg["id"],))
        # Sin pendientes, la campaña se da por terminada.
        quedan = conn.execute(
            """SELECT COUNT(*) n FROM email_queue
                WHERE campaign_id=? AND status='pending'""",
            (fila["campaign_id"],)).fetchone()["n"]
        if not quedan:
            conn.execute("UPDATE email_campaigns SET status='done' WHERE id=? "
                         "AND status='running'", (fila["campaign_id"],))


# Cada cuánto se entra al buzón. Diez minutos: una respuesta no es urgente
# y entrar cada minuto es una forma de que el servidor te corte el acceso.
_CADA_BUZON = 600


async def loop_buzones() -> None:
    """Revisa los buzones cada tanto, sin que nadie tenga que apretar nada."""
    while True:
        try:
            await revisar_buzones()
        except Exception:
            # Que no se caiga el ciclo: el próximo intento es en diez minutos.
            pass
        await asyncio.sleep(_CADA_BUZON)


async def loop_envio() -> None:
    while True:
        try:
            await _tanda()
        except Exception:
            pass          # un fallo puntual no puede matar el goteo entero
        await asyncio.sleep(INTERVALO)


def arrancar_worker() -> None:
    global _worker, _lector
    lazo = asyncio.get_event_loop()
    if _worker is None or _worker.done():
        _worker = lazo.create_task(loop_envio())
    # El que lee el buzón va aparte: no depende de que haya una campaña en
    # curso, porque una respuesta puede llegar días después del último envío.
    if _lector is None or _lector.done():
        _lector = lazo.create_task(loop_buzones())
