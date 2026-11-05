"""Ninguna ruta del servidor puede estar dos veces: la segunda queda tapada.

    python tests/test_rutas.py
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app.main as m  # noqa: E402

vistas = Counter((metodo, r.path) for r in m.app.routes
                 for metodo in (getattr(r, "methods", None) or ()))
repetidas = [k for k, n in vistas.items() if n > 1]
print(("ok  " if not repetidas else "XX  ") + f"rutas repetidas: {repetidas or 'ninguna'}")
print("TODO BIEN" if not repetidas else "FALLAS")
sys.exit(1 if repetidas else 0)
