"""Las instrucciones de la IA dependen de la empresa y de lo que ofrecemos.

    python tests/test_redactor_rubro.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")
from app import redactor as r  # noqa: E402

fallas = []


def ver(c, q):
    print(("ok  " if c else "XX  ") + q)
    if not c:
        fallas.append(q)


viajes = {"id": 1, "category": "Agencia de viajes", "company_name": "Viajes Andinos"}
marketing = {"id": 2, "category": "Agencia de marketing", "company_name": "Gmarketing",
             "ai_profile": '{"resumen": "Agencia de marketing digital y partner de Kommo."}'}
ver(r.es_turismo(viajes), "una agencia de viajes es turismo")
ver(not r.es_turismo(marketing), "una agencia de marketing no es turismo")
ver("AGENCIAS DE VIAJES" not in r.instrucciones(marketing).upper(),
    "a una empresa que no es de turismo no le llega el contexto de agencias de viaje")
ver("cotizaci" not in r.instrucciones(marketing).lower(),
    "ni lo de las cotizaciones")
ver("agencia" in r.instrucciones(viajes).lower(), "a una de turismo sí")
p = r._pedido(marketing, "Licencias de Kommo CRM", "Carlos", "es", indicaciones="Persuasivo")
todo = " ".join(m["content"] for m in p["input"])
ver("Licencias de Kommo CRM" in todo and "LO ÚNICO QUE VENDE ESTE CORREO" in todo,
    "lo que ofrecemos va como lo único que vende el correo")
ficha = r._ficha(dict(marketing, ai_profile='{"gancho": "garantía de reembolso en 35 días"}'),
                 "Carlos", "es")
ver("TIENE" not in ficha and "de ELLOS" in ficha,
    "el dato propio es de ellos y no es obligatorio usarlo")
print("TODO BIEN" if not fallas else f"{len(fallas)} FALLAS")
sys.exit(1 if fallas else 0)
