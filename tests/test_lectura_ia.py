"""Lo que la IA leyó en una respuesta va al campo «Lectura de la IA» de la
pestaña B2K, no a la nota con lo que escribió el cliente.

    python tests/test_lectura_ia.py
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
r = mailer.crear_campania("Prueba", "Hola", "Hola", [contacto], 60, 0, 0,
                          secuencia=secuencia.nueva_config("", ""))
with db.get_db() as c:
    c.execute("UPDATE secuencias SET lead_id='777' WHERE campaign_id=?", (r["campaign_id"],))
    s = dict(c.execute("SELECT * FROM secuencias WHERE campaign_id=?",
                       (r["campaign_id"],)).fetchone())

notas, campos = [], []


async def mover_a(lead_id, destino, motivo, detalle="", saltar_reglas=False):
    notas.append(detalle)
    return {"movido": True, "status_id": destino}


async def etapas():
    return {"respuesta": {"id": 55, "name": "IA CONVERSANDO"}}


async def nada(*a, **k):
    return True


async def poner_campo_lead(lead_id, nombre, texto):
    campos.append((lead_id, nombre, texto))
    return True


kommo.configured = lambda: True
kommo.mover_a = mover_a
kommo.poner_etiqueta_de_etapa = nada
kommo.poner_campo_lead = poner_campo_lead
secuencia.etapas = etapas

ev = {"texto": "No me interesa, gracias.", "rid": None, "clase": "no_interesado",
      "confianza": "alta", "porque": "Dice que no le interesa."}
asyncio.run(secuencia._respondio(s, ev))

ver(notas and "No me interesa" in notas[0], "la nota trae lo que escribió el cliente")
ver(notas and "IA" not in notas[0] and "no_interesado" not in notas[0],
    "la nota ya no trae la lectura de la IA")
ver(len(campos) == 1 and campos[0][:2] == ("777", "Lectura de la IA"),
    "la lectura va al campo «Lectura de la IA» del lead")
ver(campos and campos[0][2].startswith("No le interesa (confianza alta)"),
    f"en palabras comunes: {campos[0][2] if campos else ''!r}")
ver(campos and "Dice que no le interesa." in campos[0][2], "con el porqué")
ver(("Lectura de la IA", "textarea") in kommo.CAMPOS_LEAD, "el campo se crea solo en la pestaña B2K")
ver(set(respuestas.ETIQUETAS) == set(respuestas.CLASES), "cada clase tiene su nombre en palabras comunes")
ver(mailer._CADA_BUZON <= 120, f"los buzones se leen cada {mailer._CADA_BUZON} s")

shutil.rmtree(tmp, ignore_errors=True)
print("TODO BIEN" if not fallas else f"{len(fallas)} FALLAS")
sys.exit(1 if fallas else 0)
