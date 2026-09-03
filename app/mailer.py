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
import random
import re
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
    )
    with get_db() as conn:
        if mid:
            actual = conn.execute("SELECT password FROM smtp_config WHERE id=?",
                                  (mid,)).fetchone()
            password = datos.get("password") or (actual["password"] if actual else "")
            conn.execute(
                """UPDATE smtp_config SET label=?, host=?, port=?, username=?,
                     from_name=?, from_email=?, security=?, active=?, daily_cap=?,
                     password=?, updated_at=datetime('now')
                   WHERE id=?""", campos + (password, mid))
        else:
            cur = conn.execute(
                """INSERT INTO smtp_config
                     (label, host, port, username, from_name, from_email,
                      security, active, daily_cap, password)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                campos + (datos.get("password") or "",))
            mid = cur.lastrowid
    return get_mailbox(mid)


def delete_mailbox(mid: int) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM smtp_config WHERE id=?", (mid,))


def enviados_hoy(conn, mid: int) -> int:
    return conn.execute(
        """SELECT COUNT(*) c FROM email_queue
            WHERE smtp_id=? AND status='sent' AND date(sent_at)=date('now')""",
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
def render(texto: str, contacto: dict) -> str:
    """Reemplaza {{variables}} con lo que se sabe del contacto.

    Una variable sin dato se reemplaza por vacío, nunca por el literal
    '{{nombre}}': mandar eso a un cliente es peor que una frase corta.
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
    salida = _VAR_RE.sub(lambda m: str(valores.get(m.group(1).lower(), "")), texto or "")
    # Si una variable vacía dejó un renglón huérfano o espacios dobles, se limpia.
    salida = re.sub(r"[ \t]{2,}", " ", salida)
    return re.sub(r"\n{3,}", "\n\n", salida).strip()


def variables_desconocidas(texto: str) -> list[str]:
    return sorted({m.group(1).lower() for m in _VAR_RE.finditer(texto or "")}
                  - set(VARIABLES))


# ------------------------------------------------------------------ envío
def _enviar_sincrono(cfg: dict, destino: str, asunto: str, cuerpo: str) -> None:
    """Manda un correo. Bloquea: se llama siempre dentro de un hilo aparte."""
    msg = EmailMessage()
    msg["From"] = formataddr((cfg.get("from_name") or "", cfg["from_email"]))
    msg["To"] = destino
    msg["Subject"] = asunto
    msg["Message-ID"] = make_msgid()
    msg.set_content(cuerpo)

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
                 mailbox_id: int | None = None) -> dict:
    """Manda un correo suelto. Devuelve {"ok": bool, "error": str|None}.

    Sin buzón indicado usa el que toque por rotación.
    """
    cfg = _credenciales(mailbox_id)
    if not cfg:
        return {"ok": False, "error": "Falta configurar el servidor de salida."}
    try:
        await asyncio.to_thread(_enviar_sincrono, cfg, destino, asunto, cuerpo)
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
                   cada_segundos: int, jitter: int, tope_diario: int) -> dict:
    """Arma la campaña y reparte las horas de salida del goteo."""
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO email_campaigns
                 (name, subject, body, every_seconds, jitter_seconds, daily_cap)
               VALUES (?,?,?,?,?,?)""",
            (nombre or None, asunto, cuerpo, cada_segundos, jitter, tope_diario))
        campania = cur.lastrowid

        momento = _ahora()
        enviados_hoy, dia = 0, momento.date()
        filas = []
        for c in contactos:
            if tope_diario and enviados_hoy >= tope_diario:
                # Se corta el día y se sigue mañana a la misma hora.
                momento = datetime.combine(dia + timedelta(days=1), momento.timetz())
                dia, enviados_hoy = momento.date(), 0
            filas.append((campania, c["id"], c["email"],
                          render(asunto, c), render(cuerpo, c), _iso(momento)))
            enviados_hoy += 1
            # El jitter evita el patrón de reloj: mandar exacto cada 180 s es
            # una firma de robot para cualquier filtro de spam.
            momento += timedelta(seconds=cada_segundos + random.randint(0, max(0, jitter)))

        conn.executemany(
            """INSERT OR IGNORE INTO email_queue
                 (campaign_id, contact_id, email, subject, body, send_after)
               VALUES (?,?,?,?,?,?)""", filas)
    return {"campaign_id": campania, "queued": len(filas),
            "termina": filas[-1][5] if filas else None}


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

    try:
        await asyncio.to_thread(_enviar_sincrono, cfg, fila["email"],
                                fila["subject"], fila["body"])
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


async def loop_envio() -> None:
    while True:
        try:
            await _tanda()
        except Exception:
            pass          # un fallo puntual no puede matar el goteo entero
        await asyncio.sleep(INTERVALO)


def arrancar_worker() -> None:
    global _worker
    if _worker is None or _worker.done():
        _worker = asyncio.get_event_loop().create_task(loop_envio())
