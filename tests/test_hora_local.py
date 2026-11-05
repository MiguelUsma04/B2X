"""La hora del próximo envío se guarda en UTC y se muestra en hora de Colombia.

    python tests/test_hora_local.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from app.actividad import hora_local  # noqa: E402

casos = [
    ("2026-11-05 19:03:00", "2:03 p. m."),   # lo que vio Pablo: 19:03 UTC
    ("2026-11-05 13:30:00", "8:30 a. m."),
    ("2026-11-06 04:15:00", "11:15 p. m."),  # cambia de día en UTC
    ("2026-11-05 17:00:00", "12:00 p. m."),
    ("", ""),
    (None, ""),
]
fallas = [(e, hora_local(e), q) for e, q in casos if hora_local(e) != q]
for e, obtenido, q in fallas:
    print(f"XX {e!r}: {obtenido!r} en vez de {q!r}")
print("TODO BIEN" if not fallas else f"{len(fallas)} FALLAS")
sys.exit(1 if fallas else 0)
