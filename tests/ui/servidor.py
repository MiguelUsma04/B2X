"""B2K sobre una copia de la base, para las pruebas de pantalla.

Sin Kommo, sin OpenAI, sin Google y sin correo real: nada de lo que se toque
en la prueba sale de esta máquina.

    python tests/ui/servidor.py <copia.db> <puerto>
"""
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from app import db  # noqa: E402
db.DB_PATH = Path(sys.argv[1])
import app.main as m  # noqa: E402

for clave in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "OPENAI_API_KEY"):
    os.environ.pop(clave, None)
os.environ["APP_PASSWORD"] = "prueba"
os.environ["KOMMO_TOKEN"] = ""

from app import mailer  # noqa: E402


def _no_mandar(*a, **k):
    raise RuntimeError("prueba: no se manda correo")


mailer._enviar_sincrono = _no_mandar

import uvicorn  # noqa: E402
uvicorn.run(m.app, host="127.0.0.1", port=int(sys.argv[2]), log_level="warning")
