"""Sacar lo que hay en pantalla a un archivo que Excel abra bien.

Tres decisiones que parecen detalles y no lo son:

1. **Punto y coma, no coma.** Excel en español parte las columnas por el
   separador de lista del sistema, que acá es `;`. Con comas, el archivo se
   abre con todo apretado en la columna A y la primera impresión es que la
   exportación salió mal. El importador de B2K olfatea el separador, así que
   el archivo que sale de acá se puede volver a cargar sin tocarlo.

2. **BOM al principio** (`utf-8-sig`). Sin él Excel lee el archivo como si
   fuera latin-1 y "Bogotá" sale "BogotÃ¡".

3. **Teléfono con apóstrofo adelante.** Un número como +573001234567 lo
   toma Excel como fórmula o como número y le come el signo. El apóstrofo lo
   fuerza a texto y no se ve en la celda.
"""
import csv
import io
import re
import unicodedata
from datetime import datetime

from fastapi.responses import Response

# Columnas del archivo, en el orden en que alguien las quiere leer: primero
# a quién llamar, después con qué datos, al final de dónde salió.
COLUMNAS_MAPS = [
    ("Negocio", "name"),
    ("Categoría", "category"),
    ("Teléfono", "phone"),
    ("Sitio web", "website"),
    ("Dominio", "domain"),
    ("Redes", "social_url"),
    ("Dirección", "address"),
    ("País", "country"),
    ("Calificación", "rating"),
    ("Reseñas", "rating_count"),
    ("Estado", "status"),
    ("Ficha en Google Maps", "maps_url"),
    ("ID de Google", "place_id"),
]

COLUMNAS_CONTACTOS = [
    ("Empresa", "company_name"),
    ("Persona", "full_name"),
    ("Cargo", "job_title"),
    ("Email", "email"),
    ("Estado del email", "email_status"),
    ("De dónde salió el email", "email_source"),
    ("Teléfono", "phone"),
    ("Tipo de teléfono", "phone_type"),
    ("Sitio web", "company_domain"),
    ("Dirección", "address"),
    ("País", "country"),
    ("Categoría", "category"),
    ("Calificación", "rating"),
    ("Reseñas", "rating_count"),
    ("Ficha en Google Maps", "maps_url"),
    ("En el CRM", "crm_status"),
    ("Resumen de la IA", "ai_summary"),
    ("Cargado el", "created_at"),
]


def _celda(valor) -> str:
    """El valor tal como tiene que entrar en la celda."""
    if valor is None:
        return ""
    texto = str(valor)
    # Un teléfono que empieza con + lo trata Excel como fórmula.
    if texto.startswith(("+", "=", "-", "@")):
        return "'" + texto
    return texto


def nombre_archivo(base: str) -> str:
    """Un nombre que Windows acepte y que diga de cuándo es."""
    limpio = unicodedata.normalize("NFKD", base or "export")
    limpio = "".join(c for c in limpio if not unicodedata.combining(c))
    limpio = re.sub(r"[^A-Za-z0-9 _-]+", "", limpio).strip().replace(" ", "-")
    limpio = re.sub(r"-{2,}", "-", limpio)[:60] or "export"
    return f"{limpio}-{datetime.now():%Y-%m-%d}.csv"


def csv_respuesta(filas: list[dict], columnas: list[tuple[str, str]],
                  nombre: str) -> Response:
    buf = io.StringIO()
    escritor = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL,
                          lineterminator="\r\n")
    escritor.writerow([titulo for titulo, _ in columnas])
    for f in filas:
        escritor.writerow([_celda(f.get(clave)) for _, clave in columnas])

    # El BOM va a mano porque la respuesta se arma en bytes.
    datos = b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")
    return Response(
        content=datos,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="{nombre_archivo(nombre)}"'})
