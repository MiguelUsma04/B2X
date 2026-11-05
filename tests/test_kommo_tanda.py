"""El embudo y la etapa elegidos en la ventana de Kommo valen para esa tanda.

    python tests/test_kommo_tanda.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
tmp = Path(tempfile.mkdtemp())
shutil.copy(RAIZ / "data" / "b2x.db", tmp / "b.db")
from app import db  # noqa: E402
db.DB_PATH = tmp / "b.db"
import app.main as m  # noqa: E402
from app import kommo  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

os.environ["APP_PASSWORD"] = "prueba"
for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
    os.environ.pop(k, None)
visto = {}


async def mandar_falso(ids, tag=None):
    visto["destino"] = kommo.destino_del_lead()
    return {"sent": len(ids), "results": []}

kommo.send_contacts = mandar_falso
t = TestClient(m.app, base_url="https://testserver")
t.post("/api/login", data={"password": "prueba"})
r = t.post("/api/ghl/send", data={"contact_ids": "[1]", "tag": "",
                                   "pipeline_id": "555", "stage_id": "777"})
ok1 = visto.get("destino") == {"pipeline_id": 555, "status_id": 777}
print(("ok  " if ok1 else "XX  ") + f"la tanda usa el embudo de la ventana: {visto.get('destino')}")
fuera = kommo.destino_del_lead()
ok2 = fuera != {"pipeline_id": 555, "status_id": 777}
print(("ok  " if ok2 else "XX  ") + "después de la tanda vuelve el de fábrica")
shutil.rmtree(tmp, ignore_errors=True)
print("TODO BIEN" if ok1 and ok2 else "FALLAS")
sys.exit(0 if ok1 and ok2 else 1)
