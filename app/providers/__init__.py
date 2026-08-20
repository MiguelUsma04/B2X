import os
from .prospeo import ProspeoProvider
from .icypeas import IcypeasProvider
from .hunter import HunterProvider

# Orden del waterfall: Prospeo -> Icypeas -> Hunter.
def build_chain() -> list:
    return [
        ProspeoProvider(os.getenv("PROSPEO_API_KEY")),
        IcypeasProvider(os.getenv("ICYPEAS_API_KEY")),
        HunterProvider(os.getenv("HUNTER_API_KEY")),
    ]

__all__ = ["build_chain", "ProspeoProvider", "IcypeasProvider", "HunterProvider"]
