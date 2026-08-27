import os
from .prospeo import ProspeoProvider
from .icypeas import IcypeasProvider
from .hunter import HunterProvider

# Orden del waterfall: Prospeo -> Hunter.
#
# Icypeas quedó fuera: en la prueba con contactos reales no encontró ninguno,
# es el único asíncrono (obliga a esperar hasta ~80 s por contacto) y cobra la
# búsqueda aunque no resuelva. El cliente sigue en icypeas.py: para volver a
# activarlo alcanza con sumarlo de nuevo a esta lista.
def build_chain() -> list:
    return [
        ProspeoProvider(os.getenv("PROSPEO_API_KEY")),
        HunterProvider(os.getenv("HUNTER_API_KEY")),
    ]

__all__ = ["build_chain", "ProspeoProvider", "IcypeasProvider", "HunterProvider"]
