"""El voseo se pasa a tuteo sin tocar palabras que no son verbos.

    python tests/test_voseo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")
from app.redactor import sin_voseo  # noqa: E402

casos = [
    ("Con Kommo centralizás leads y mantenés el pipeline activo.",
     "Con Kommo centralizas leads y mantienes el pipeline activo."),
    ("Recuperás oportunidades y sabés qué funciona.", "Recuperas oportunidades y sabes qué funciona."),
    ("Si vivís en Medellín, entendés el mercado.", "Si vives en Medellín, entiendes el mercado."),
    ("Además, después de más pruebas, el interés en inglés y francés creció en el país.",
     "Además, después de más pruebas, el interés en inglés y francés creció en el país."),
    ("Tendrás más clientes y podrás verlo; ¿estás disponible?",
     "Tendrás más clientes y podrás verlo; ¿estás disponible?"),
    ("Te escribí hace unos días. Decime si querés.", "Te escribí hace unos días. Dime si quieres."),
]
fallas = 0
for entrada, esperado in casos:
    salida = sin_voseo(entrada)
    ok = salida == esperado
    fallas += not ok
    print(("ok  " if ok else "XX  ") + salida + ("" if ok else f"   (esperado: {esperado})"))
print("TODO BIEN" if not fallas else f"{fallas} FALLAS")
sys.exit(1 if fallas else 0)
