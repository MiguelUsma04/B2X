"""Respaldos de la base, y una forma de ver si la base es la de siempre.

Todo lo que B2K sabe vive en un archivo: `data/b2x.db`. Los contactos, las
campañas, quién abrió cada correo, los buzones con su contraseña. Si ese
archivo desaparece, no queda nada.

Y desaparece más fácil de lo que parece. En el servidor la base tiene que
estar en un volumen de Docker montado en `/app/data`; si el bloque del
docker-compose no se copió con ese volumen, cada `docker compose up --build`
arranca con una base vacía. Desde adentro de la app no se puede saber si el
volumen está montado —el contenedor ve un directorio y nada más—, pero sí se
puede mirar el síntoma: una base que se creó hace diez minutos y no tiene
nada adentro es una base que se acaba de perder.

Por eso acá hay dos cosas: copias automáticas y una ficha de la base para
mirar antes de cargar los buzones de nuevo.
"""
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .db import DB_PATH, get_db

CARPETA = "respaldos"
CUANTOS_GUARDAR = 14      # dos semanas de copias diarias


def carpeta() -> Path:
    d = DB_PATH.parent / CARPETA
    d.mkdir(parents=True, exist_ok=True)
    return d


def copiar(destino: Path | None = None) -> Path:
    """Una copia consistente, aunque la app esté escribiendo.

    No se copia el archivo con el sistema operativo: si justo hay una
    escritura a medias, la copia queda corrupta y nadie se entera hasta que
    hace falta. La API de respaldo de SQLite espera a que la base esté en un
    punto entero.
    """
    destino = destino or (carpeta() /
                          f"b2x-{datetime.now():%Y%m%d-%H%M}.db")
    origen = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        copia = sqlite3.connect(destino)
        try:
            origen.backup(copia)
        finally:
            copia.close()
    finally:
        origen.close()
    return destino


def _limpiar_viejas() -> int:
    """Deja las últimas. Un disco lleno también es perder la base."""
    copias = sorted(carpeta().glob("b2x-*.db"))
    sobran = copias[:-CUANTOS_GUARDAR] if len(copias) > CUANTOS_GUARDAR else []
    for c in sobran:
        try:
            c.unlink()
        except OSError:
            pass
    return len(sobran)


def respaldar_si_toca(cada_horas: int = 24) -> Path | None:
    """Copia si la última es vieja. Devuelve la copia nueva, o nada."""
    copias = sorted(carpeta().glob("b2x-*.db"))
    if copias:
        ultima = datetime.fromtimestamp(copias[-1].stat().st_mtime)
        if datetime.now() - ultima < timedelta(hours=cada_horas):
            return None
    nueva = copiar()
    _limpiar_viejas()
    return nueva


def listar() -> list[dict]:
    salida = []
    for c in sorted(carpeta().glob("b2x-*.db"), reverse=True):
        st = c.stat()
        salida.append({"nombre": c.name, "bytes": st.st_size,
                       "cuando": datetime.fromtimestamp(st.st_mtime)
                       .strftime("%Y-%m-%d %H:%M")})
    return salida


# --------------------------------------------------------------- la ficha
_TABLAS = [
    ("contacts", "contactos"),
    ("smtp_config", "buzones"),
    ("email_campaigns", "campañas"),
    ("email_queue", "correos en cola o enviados"),
    ("suppression", "bajas"),
    ("actividad", "acciones registradas"),
]


def ficha() -> dict:
    """Dónde vive la base, desde cuándo, y qué tiene adentro.

    Es lo que hay que mirar cuando algo "se borró": si la base nació hace un
    rato, no se borró una tabla, se perdió el archivo entero.
    """
    existe = DB_PATH.exists()
    st = DB_PATH.stat() if existe else None
    nacida = datetime.fromtimestamp(st.st_ctime) if st else None
    contenido, total = [], 0
    if existe:
        with get_db() as conn:
            for tabla, nombre in _TABLAS:
                try:
                    n = conn.execute(f'SELECT COUNT(*) c FROM "{tabla}"').fetchone()["c"]
                except sqlite3.Error:
                    continue
                contenido.append({"que": nombre, "cuantos": n})
                total += n

    horas = ((datetime.now() - nacida).total_seconds() / 3600) if nacida else None
    # El aviso: base recién nacida y vacía es el síntoma de que el volumen no
    # está montado y cada despliegue arranca de cero.
    sospecha = None
    if horas is not None and horas < 24 and total <= 1:
        sospecha = ("Esta base se creó hace menos de un día y está casi vacía. "
                    "Si ya habías cargado contactos o buzones, lo más probable "
                    "es que el servidor no tenga montado el volumen de Docker "
                    "en /app/data, y cada despliegue empiece de cero.")

    copias = listar()
    return {
        "ruta": str(DB_PATH),
        "existe": existe,
        "bytes": st.st_size if st else 0,
        "nacida": nacida.strftime("%Y-%m-%d %H:%M") if nacida else None,
        "horas_de_vida": round(horas, 1) if horas is not None else None,
        "contenido": contenido,
        "sospecha": sospecha,
        "respaldos": copias,
        "ultimo_respaldo": copias[0]["cuando"] if copias else None,
    }
