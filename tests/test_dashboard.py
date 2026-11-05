"""Los números del dashboard, contra una copia de la base con datos armados.

    python tests/test_dashboard.py
"""
import os
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
import app.main as m  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

os.environ["APP_PASSWORD"] = "prueba"
for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
    os.environ.pop(k, None)

fallas = []


def ver(cond, que):
    print(("ok  " if cond else "XX  ") + que)
    if not cond:
        fallas.append(que)


# Cuatro contactos: a los cuatro se les escribió, dos abrieron (uno, dos veces,
# y un antivirus que no cuenta), uno contestó, uno rebotó. Uno de los envíos es
# de hace 100 días: queda fuera de los 30.
with db.get_db() as c:
    ids = [r[0] for r in c.execute("SELECT id FROM contacts ORDER BY id LIMIT 4")]
    c.execute("DELETE FROM email_events")
    c.execute("DELETE FROM email_queue")
    camp = c.execute("INSERT INTO email_campaigns(subject, body) VALUES ('s','b')").lastrowid
    q = []
    for n, cid in enumerate(ids):
        cuando = "datetime('now', '-100 days')" if n == 3 else f"datetime('now', '-{n} days')"
        q.append(c.execute(
            f"""INSERT INTO email_queue(campaign_id, contact_id, email, subject, body,
                  send_after, status, sent_at)
                VALUES (?,?,?,'s','b',{cuando},'sent',{cuando})""",
            (camp, cid, f"c{n}@x.co")).lastrowid)
    ev = [(q[0], ids[0], "open", 0), (q[0], ids[0], "open", 0), (q[1], ids[1], "open", 0),
          (q[2], ids[2], "open", 1), (q[0], ids[0], "reply", 0), (q[2], ids[2], "bounce", 0)]
    for qid, cid, kind, bot in ev:
        c.execute("INSERT INTO email_events(queue_id, campaign_id, contact_id, kind, bot) "
                  "VALUES (?,?,?,?,?)", (qid, camp, cid, kind, bot))

t = TestClient(m.app, base_url="https://testserver")
t.post("/api/login", data={"password": "prueba"})
d = t.get("/api/dashboard?dias=30").json()
tot = d.get("correos", {})
ver(tot.get("enviados") == 3, f"3 enviados en 30 días ({tot.get('enviados')})")
ver(tot.get("abrieron") == 2, f"2 abrieron, sin contar al antivirus ni repetir ({tot.get('abrieron')})")
ver(tot.get("respondieron") == 1, f"1 respondió ({tot.get('respondieron')})")
ver(tot.get("rebotaron") == 1, f"1 rebotó ({tot.get('rebotaron')})")
dias = d.get("por_dia", [])
ver(len(dias) == 30, f"una fila por día ({len(dias)})")
ver(sum(x["enviados"] for x in dias) == 3, "los envíos por día suman 3")
ver(sum(x["respondieron"] for x in dias) == 1, "las respuestas por día suman 1")
emb = {e["clave"]: e["n"] for e in d.get("embudo", [])}
# El embudo es de siempre, no del período: cuenta también el envío de hace 100 días.
ver(emb.get("recibieron") == 4 and emb.get("abrieron") == 2 and emb.get("respondieron") == 1,
    f"el embudo cuenta empresas, de siempre ({emb})")
ver(list(emb)[:2] == ["contactos", "con_email"], "el embudo va de la base a las respuestas")
ver(set(d.get("secuencias", {})) >= {"activa", "respondio", "sin_respuesta", "cortada"},
    "trae el estado de las secuencias")
ver("por_buzon" in d and "en_cola" in d, "trae los buzones y lo que espera en la cola")
d7 = t.get("/api/dashboard?dias=7").json()
ver(len(d7.get("por_dia", [])) == 7, "con 7 días, 7 filas")

shutil.rmtree(tmp, ignore_errors=True)
print("TODO BIEN" if not fallas else f"{len(fallas)} FALLAS")
sys.exit(1 if fallas else 0)
