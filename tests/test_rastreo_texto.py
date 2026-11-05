"""Los correos sin diseño —los de la IA y los de texto— también se miden.

Antes solo se medía el «Diseño HTML»: el pixel y los enlaces medibles viven en
la versión HTML, y los correos de texto no tenían. Ahora se arma una versión
HTML del texto, con el pixel y los enlaces por el desvío; el texto plano queda
igual.

    python tests/test_rastreo_texto.py
"""
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.stdout.reconfigure(encoding="utf-8")
tmp = Path(tempfile.mkdtemp())
shutil.copy(RAIZ / "data" / "b2x.db", tmp / "b.db")
from app import db  # noqa: E402
db.DB_PATH = tmp / "b.db"
from app import mailer, secuencia  # noqa: E402

fallas = []


def ver(cond, que):
    print(("ok  " if cond else "XX  ") + que)
    if not cond:
        fallas.append(que)


BASE = "https://b2k.gmarketing.co"
with db.get_db() as c:
    contactos = [dict(r) for r in c.execute(
        "SELECT * FROM contacts WHERE email IS NOT NULL AND email<>'' ORDER BY id LIMIT 2")]
ia, plano = contactos

escritos = {ia["id"]: {"asunto": "Una idea", "cuerpo": "Hola Ana,\n\nMira https://gmarketing.co/kommo\n\n¿Hablamos?",
                       "cuerpo2": "Dos", "cuerpo3": "Tres"}}
r = mailer.crear_campania("Prueba", "Asunto {{empresa}}", "Hola {{nombre}}\n\nTexto.",
                          contactos, 60, 0, 0, base_rastreo=BASE, escritos=escritos,
                          secuencia=secuencia.nueva_config("", ""))
with db.get_db() as c:
    filas = {f["contact_id"]: dict(f) for f in c.execute(
        "SELECT * FROM email_queue WHERE campaign_id=?", (r["campaign_id"],))}

for quien, cid in (("IA", ia["id"]), ("texto", plano["id"])):
    f = filas[cid]
    html = f["body_html"] or ""
    ver(f"{BASE}/t/a/{f['token']}.png" in html, f"el correo de {quien} lleva el pixel de apertura")
    ver(f'href="{BASE}/u/{f["token"]}"' in html, f"el de {quien}: la baja es un enlace directo, sin desvío")
    ver("/t/" not in f["body"], f"el de {quien}: el texto plano no lleva pixel ni desvíos")
    ver("<p>" in html and "respóndeme" in html, f"el de {quien}: el HTML trae los párrafos y el pie")

html_ia = filas[ia["id"]]["body_html"]
ver(f"{BASE}/t/c/{filas[ia['id']]['token']}?u=" in html_ia,
    "un enlace escrito en el correo pasa por el desvío y cuenta el clic")
ver("https://gmarketing.co/kommo" in filas[ia["id"]]["body"],
    "en el texto plano el enlace queda tal cual")
ver(mailer.metricas(r["campaign_id"])["medible"], "la campaña figura como medible")

# El correo 2 de la secuencia también.
with db.get_db() as c:
    raiz = c.execute("SELECT * FROM email_campaigns WHERE id=?", (r["campaign_id"],)).fetchone()
    s = dict(c.execute("SELECT * FROM secuencias WHERE campaign_id=? AND contact_id=?",
                       (r["campaign_id"], ia["id"])).fetchone())
s["buzon"], s["hilo"] = s.get("buzon"), s.get("hilo")
secuencia._encolar(s, 2, raiz, json.loads(raiz["secuencia"]), datetime.now(timezone.utc), mailer)
with db.get_db() as c:
    f2 = dict(c.execute("""SELECT q.* FROM email_queue q JOIN email_campaigns k ON k.id=q.campaign_id
                           WHERE k.padre_id=? AND k.paso=2""", (r["campaign_id"],)).fetchone())
ver(f"{BASE}/t/a/{f2['token']}.png" in (f2["body_html"] or ""), "el correo 2 lleva su pixel")
ver(f'href="{BASE}/u/{f2["token"]}"' in (f2["body_html"] or ""), "y su enlace de baja")

shutil.rmtree(tmp, ignore_errors=True)
print("TODO BIEN" if not fallas else f"{len(fallas)} FALLAS")
sys.exit(1 if fallas else 0)
