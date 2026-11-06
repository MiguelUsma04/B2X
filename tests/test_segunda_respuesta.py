"""Cada respuesta nueva del cliente se lee, no solo la primera.

El 06/11/2026 un cliente contestó «Me interesa» y después «Ya no me interesa,
saca mi correo»: el campo seguía diciendo «Le interesa» y no se le dio de
baja. Ahora la última respuesta pisa la lectura, y si pide que no le
escriban, sale de la lista aunque el lead ya esté en IA CONVERSANDO.

    python tests/test_segunda_respuesta.py
"""
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.stdout.reconfigure(encoding="utf-8")
tmp = Path(tempfile.mkdtemp())
shutil.copy(RAIZ / "data" / "b2x.db", tmp / "b.db")
from app import db  # noqa: E402
db.DB_PATH = tmp / "b.db"
from app import kommo, mailer, respuestas, secuencia  # noqa: E402

fallas = []


def ver(cond, que):
    print(("ok  " if cond else "XX  ") + que)
    if not cond:
        fallas.append(que)


with db.get_db() as c:
    contacto = dict(c.execute("SELECT * FROM contacts WHERE email IS NOT NULL "
                              "AND email<>'' ORDER BY id LIMIT 1").fetchone())
    c.execute("DELETE FROM suppression WHERE email=?", (contacto["email"].lower(),))
r = mailer.crear_campania("Prueba", "Hola", "Hola", [contacto], 60, 0, 0,
                          secuencia=secuencia.nueva_config("", ""))
camp = r["campaign_id"]
with db.get_db() as c:
    c.execute("UPDATE secuencias SET lead_id='777' WHERE campaign_id=?", (camp,))
    s = dict(c.execute("SELECT * FROM secuencias WHERE campaign_id=?", (camp,)).fetchone())
    cola = c.execute("SELECT id FROM email_queue WHERE campaign_id=?", (camp,)).fetchone()["id"]


def llega(texto, clase=None, confianza=None, porque=None, bot=0):
    with db.get_db() as c:
        ev = c.execute(
            "INSERT INTO email_events (queue_id, campaign_id, contact_id, kind, bot) "
            "VALUES (?,?,?,'reply',?)", (cola, camp, contacto["id"], bot)).lastrowid
        c.execute("INSERT INTO respuestas (event_id, contact_id, texto, clase, confianza, porque) "
                  "VALUES (?,?,?,?,?,?)", (ev, contacto["id"], texto, clase, confianza, porque))
    return ev


movidas, campos, clasificadas = [], [], []


async def mover_a(lead_id, destino, motivo, detalle="", saltar_reglas=False):
    movidas.append(destino)
    return {"movido": True, "status_id": destino}


async def etapas():
    return {"respuesta": {"id": 55, "name": "IA CONVERSANDO"}}


async def nada(*a, **k):
    return True


async def poner_campo_lead(lead_id, nombre, texto):
    campos.append((lead_id, nombre, texto))
    return True


async def clasificar(client, texto, asunto=""):
    clasificadas.append(texto)
    return {"clase": "no_escribir", "confianza": "alta",
            "porque": "Pide que saquen su correo.", "error": None}


kommo.configured = lambda: True
kommo.mover_a = mover_a
kommo.poner_etiqueta_de_etapa = nada
kommo.poner_campo_lead = poner_campo_lead
respuestas.clasificar = clasificar
secuencia.etapas = etapas

# La primera respuesta: entra por el camino de siempre.
llega("Hola extraño. Me interesa, ¿cuál sería el paso a seguir?",
      "interesado", "alta", "Pregunta el paso a seguir.")
asyncio.run(secuencia.avanzar())
with db.get_db() as c:
    estado = c.execute("SELECT estado FROM secuencias WHERE id=?", (s["id"],)).fetchone()[0]
ver(estado == "respondio", "con la primera respuesta la secuencia queda en «respondió»")
ver(len(movidas) == 1, "y el lead se movió una vez")
ver(campos and campos[-1][2].startswith("Le interesa"), "el campo dice «Le interesa»")

# Una base de antes del arreglo no sabe cuál fue la última que se leyó: la
# primera ya se atendió, así que no se vuelve a leer.
with db.get_db() as c:
    c.execute("UPDATE secuencias SET ultima_respuesta=NULL WHERE id=?", (s["id"],))
antes = len(campos)
asyncio.run(secuencia.avanzar())
ver(len(campos) == antes, "sin respuestas nuevas no se toca el campo")

# Un aviso automático («estoy de vacaciones») no es una respuesta.
llega("Estoy fuera de la oficina.", bot=1)
asyncio.run(secuencia.avanzar())
ver(len(campos) == antes, "un aviso automático no pisa la lectura")

# La segunda respuesta, todavía sin clasificar.
llega("Ya no me interesa, ladrones. Saca mi correo de tus servidores")
asyncio.run(secuencia.avanzar())
ver(clasificadas == ["Ya no me interesa, ladrones. Saca mi correo de tus servidores"],
    "la IA lee la respuesta nueva")
ver(len(campos) == antes + 1 and campos[-1][:2] == ("777", "Lectura de la IA"),
    "y su lectura va al campo del lead")
ver(campos and campos[-1][2].startswith("Pide que no le escriban más"),
    f"pisando la anterior: {campos[-1][2] if campos else ''!r}")
correo = contacto["email"].strip().lower()
ver(any(x["email"] == correo for x in mailer.suprimidos()),
    "pidió que no le escriban: queda en la lista de bajas")
ver(len(movidas) == 1, "el lead no se mueve de etapa: esa conversación ya es del equipo")
with db.get_db() as c:
    fila = c.execute("SELECT clase FROM respuestas WHERE texto LIKE 'Ya no me%'").fetchone()
ver(fila and fila["clase"] == "no_escribir", "la clase queda guardada para Resultados")

# Otra vuelta del vigilante no la vuelve a leer.
asyncio.run(secuencia.avanzar())
ver(len(clasificadas) == 1 and len(campos) == antes + 1, "cada respuesta se lee una sola vez")

shutil.rmtree(tmp, ignore_errors=True)
print("TODO BIEN" if not fallas else f"{len(fallas)} FALLAS")
sys.exit(1 if fallas else 0)
