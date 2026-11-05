"""Los números del dashboard: cómo va toda la app en un período.

Todo se cuenta por **empresa**, no por evento: una empresa que abrió el correo
cinco veces es una empresa que abrió. Y las aperturas o clics de un antivirus
(bot=1) no cuentan: son el falso positivo más común del correo.

Los porcentajes del período se calculan sobre los correos que **salieron** en
el período, así una respuesta de hoy a un correo de hace tres meses no infla
la tasa de los últimos siete días.
"""
from datetime import date, timedelta

from .db import get_db

PERIODOS = (7, 30, 90)


def _dias(n: int) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return 30
    return n if n in PERIODOS else 30


def datos(dias=30) -> dict:
    dias = _dias(dias)
    desde = f"-{dias - 1} days"
    with get_db() as c:
        # Los correos que salieron en el período y qué pasó con cada uno.
        fila = c.execute(
            """WITH env AS (
                 SELECT id FROM email_queue
                  WHERE status='sent' AND date(sent_at) >= date('now', ?))
               SELECT
                 (SELECT COUNT(*) FROM env) enviados,
                 (SELECT COUNT(DISTINCT e.queue_id) FROM email_events e
                    WHERE e.queue_id IN (SELECT id FROM env) AND e.kind='open' AND e.bot=0) abrieron,
                 (SELECT COUNT(DISTINCT e.queue_id) FROM email_events e
                    WHERE e.queue_id IN (SELECT id FROM env) AND e.kind='click' AND e.bot=0) clics,
                 (SELECT COUNT(DISTINCT e.queue_id) FROM email_events e
                    WHERE e.queue_id IN (SELECT id FROM env) AND e.kind='reply' AND e.bot=0) respondieron,
                 (SELECT COUNT(DISTINCT e.queue_id) FROM email_events e
                    WHERE e.queue_id IN (SELECT id FROM env) AND e.kind='bounce') rebotaron,
                 (SELECT COUNT(DISTINCT e.queue_id) FROM email_events e
                    WHERE e.queue_id IN (SELECT id FROM env) AND e.kind='unsub') bajas""",
            (desde,)).fetchone()
        correos = dict(fila)

        # Día por día: lo que salió ese día, y las aperturas y respuestas que
        # llegaron ese día (de cualquier correo).
        enviados_dia = dict(c.execute(
            """SELECT date(sent_at), COUNT(*) FROM email_queue
                WHERE status='sent' AND date(sent_at) >= date('now', ?)
                GROUP BY 1""", (desde,)).fetchall())
        eventos_dia = {}
        for d, kind, n in c.execute(
                """SELECT date(at), kind, COUNT(DISTINCT queue_id) FROM email_events
                    WHERE bot=0 AND kind IN ('open', 'reply')
                      AND date(at) >= date('now', ?)
                    GROUP BY 1, 2""", (desde,)):
            eventos_dia.setdefault(d, {})[kind] = n
        hoy = date.fromisoformat(c.execute("SELECT date('now')").fetchone()[0])

        # El camino de una empresa, de punta a punta (de siempre, no del período).
        base = c.execute(
            """SELECT COUNT(*) total,
                      SUM(email IS NOT NULL AND email <> '') con_email,
                      SUM(crm_status = 'sent') en_kommo
                 FROM contacts""").fetchone()
        camino = c.execute(
            """SELECT
                 (SELECT COUNT(DISTINCT contact_id) FROM email_queue WHERE status='sent') recibieron,
                 (SELECT COUNT(DISTINCT contact_id) FROM email_events
                    WHERE kind='open' AND bot=0) abrieron,
                 (SELECT COUNT(DISTINCT contact_id) FROM email_events
                    WHERE kind='reply' AND bot=0) respondieron""").fetchone()

        secuencias = {"activa": 0, "respondio": 0, "sin_respuesta": 0, "cortada": 0}
        en_paso = {0: 0, 1: 0, 2: 0, 3: 0}
        try:
            for estado, paso, n in c.execute(
                    "SELECT estado, paso, COUNT(*) FROM secuencias GROUP BY 1, 2"):
                secuencias[estado] = secuencias.get(estado, 0) + n
                if estado == "activa":
                    en_paso[paso or 0] = en_paso.get(paso or 0, 0) + n
        except Exception:
            pass                       # una base sin la tabla todavía

        por_buzon = [dict(r) for r in c.execute(
            """SELECT b.id, COALESCE(b.label, b.from_email) nombre, b.from_email, b.active,
                      (SELECT COUNT(*) FROM email_queue q WHERE q.smtp_id = b.id
                         AND q.status='sent' AND date(q.sent_at) >= date('now', ?)) enviados,
                      (SELECT COUNT(DISTINCT e.queue_id) FROM email_events e
                         JOIN email_queue q ON q.id = e.queue_id
                        WHERE q.smtp_id = b.id AND e.kind='bounce'
                          AND date(q.sent_at) >= date('now', ?)) rebotes
                 FROM smtp_config b ORDER BY b.id""", (desde, desde))]
        en_cola = c.execute(
            "SELECT COUNT(*) FROM email_queue WHERE status='pending'").fetchone()[0]

    por_dia = []
    for i in range(dias - 1, -1, -1):
        d = (hoy - timedelta(days=i)).isoformat()
        ev = eventos_dia.get(d, {})
        por_dia.append({"dia": d, "enviados": enviados_dia.get(d, 0),
                        "abrieron": ev.get("open", 0), "respondieron": ev.get("reply", 0)})

    embudo = [
        {"clave": "contactos", "nombre": "Empresas en la base", "n": base["total"] or 0},
        {"clave": "con_email", "nombre": "Con email", "n": base["con_email"] or 0},
        {"clave": "en_kommo", "nombre": "En Kommo", "n": base["en_kommo"] or 0},
        {"clave": "recibieron", "nombre": "Recibieron un correo", "n": camino["recibieron"] or 0},
        {"clave": "abrieron", "nombre": "Lo abrieron", "n": camino["abrieron"] or 0},
        {"clave": "respondieron", "nombre": "Contestaron", "n": camino["respondieron"] or 0},
    ]
    return {"dias": dias, "correos": correos, "por_dia": por_dia, "embudo": embudo,
            "secuencias": secuencias, "en_paso": en_paso, "por_buzon": por_buzon,
            "en_cola": en_cola}
