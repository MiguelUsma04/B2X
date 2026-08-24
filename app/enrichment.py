"""Motor de enriquecimiento en cascada (waterfall).

Recorre los proveedores en orden para cada contacto pendiente. Serial y con
espera entre contactos: el objetivo es no pegarle a los rate limits ni gastar
créditos de más, no la velocidad.
"""
import asyncio
import json
import os
from dataclasses import dataclass, field

import httpx

from .db import get_db
from .providers import build_chain


@dataclass
class EnrichProgress:
    running: bool = False
    total: int = 0
    processed: int = 0
    found: int = 0
    not_found: int = 0
    current_contact: str | None = None
    current_provider: str | None = None
    finished: bool = False
    error: str | None = None
    by_provider: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "running": self.running, "total": self.total, "processed": self.processed,
            "found": self.found, "not_found": self.not_found,
            "current_contact": self.current_contact,
            "current_provider": self.current_provider,
            "finished": self.finished, "error": self.error,
            "by_provider": self.by_provider,
        }


# Estado en memoria de la corrida actual (app de un solo usuario).
PROGRESS = EnrichProgress()
_LOCK = asyncio.Lock()


def _log(conn, contact_id: int, provider: str, result) -> None:
    conn.execute(
        """INSERT INTO enrichment_log
           (contact_id, provider, success, request_payload, response_payload, error_message)
           VALUES (?,?,?,?,?,?)""",
        (contact_id, provider, 1 if result.success else 0,
         json.dumps(result.request_payload, ensure_ascii=False)[:20000],
         json.dumps(result.response_payload, ensure_ascii=False)[:20000]
         if result.response_payload is not None else None,
         result.error_message),
    )


def pending_contacts(limit: int | None = None, batch_id: int | None = None) -> list[dict]:
    q = "SELECT * FROM contacts WHERE email_status='pending'"
    params: list = []
    if batch_id:
        q += " AND import_batch_id=?"
        params.append(batch_id)
    q += " ORDER BY id"
    if limit:
        q += " LIMIT ?"
        params.append(limit)
    with get_db() as conn:
        return [dict(r) for r in conn.execute(q, params)]


async def run_enrichment(limit: int | None = None, batch_id: int | None = None) -> None:
    """Corre el waterfall sobre los contactos pendientes. Un solo run a la vez."""
    global PROGRESS
    if _LOCK.locked():
        return
    async with _LOCK:
        chain = [p for p in build_chain() if p.enabled]
        PROGRESS = EnrichProgress(running=True)

        if not chain:
            PROGRESS.running = False
            PROGRESS.finished = True
            PROGRESS.error = ("No hay proveedores configurados. Poné al menos una API key "
                              "en el archivo .env y reiniciá la app.")
            return

        contacts = pending_contacts(limit=limit, batch_id=batch_id)
        PROGRESS.total = len(contacts)
        delay = float(os.getenv("ENRICH_DELAY_SECONDS", "1.0"))
        # Proveedores con credenciales rotas: se saltan tras el primer fallo fatal.
        disabled: set[str] = set()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                for contact in contacts:
                    PROGRESS.current_contact = contact.get("full_name") or f"#{contact['id']}"
                    resolved = False

                    for provider in chain:
                        if provider.name in disabled:
                            continue
                        PROGRESS.current_provider = provider.name
                        try:
                            result = await provider.find_email(client, contact)
                        except Exception as exc:  # red caída, timeout agotado, etc.
                            from .providers.base import EnrichResult
                            result = EnrichResult(
                                False, error_message=f"{type(exc).__name__}: {exc}"[:500])

                        with get_db() as conn:
                            _log(conn, contact["id"], provider.name, result)
                            if result.success and result.email:
                                conn.execute(
                                    """UPDATE contacts
                                       SET email=?, email_status=?, email_source=?,
                                           updated_at=datetime('now')
                                       WHERE id=?""",
                                    (result.email,
                                     "verified" if result.verified else "unverified",
                                     provider.name, contact["id"]),
                                )
                            # Algunos proveedores devuelven el teléfono de paso:
                            # se guarda si el contacto todavía no tenía uno.
                            if result.phone and not contact.get("phone"):
                                conn.execute(
                                    """UPDATE contacts SET phone=?,
                                       updated_at=datetime('now') WHERE id=?""",
                                    (result.phone, contact["id"]))
                        if result.success and result.email:
                            PROGRESS.found += 1
                            PROGRESS.by_provider[provider.name] = \
                                PROGRESS.by_provider.get(provider.name, 0) + 1
                            resolved = True
                            break
                        if result.fatal:
                            disabled.add(provider.name)

                    if not resolved:
                        with get_db() as conn:
                            conn.execute(
                                """UPDATE contacts SET email_status='not_found',
                                   updated_at=datetime('now') WHERE id=?""",
                                (contact["id"],))
                        PROGRESS.not_found += 1

                    PROGRESS.processed += 1
                    # La pausa es para no pegarle a los rate limits; si el
                    # contacto se resolvió con el primer proveedor no hace
                    # falta esperar tanto.
                    if delay > 0:
                        await asyncio.sleep(delay if not resolved else delay / 2)
        except Exception as exc:
            PROGRESS.error = f"{type(exc).__name__}: {exc}"[:500]
        finally:
            PROGRESS.running = False
            PROGRESS.finished = True
            PROGRESS.current_contact = None
            PROGRESS.current_provider = None


# --------------------------------------------------------------- móviles
MOBILE_PROGRESS = EnrichProgress()
_MOBILE_LOCK = asyncio.Lock()


def contacts_without_phone(contact_ids: list[int]) -> list[dict]:
    if not contact_ids:
        return []
    marks = ",".join("?" * len(contact_ids))
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            f"""SELECT * FROM contacts
                WHERE id IN ({marks}) AND (phone IS NULL OR phone = '')
                ORDER BY id""", contact_ids)]


async def run_mobile_search(contact_ids: list[int]) -> None:
    """Busca el móvil de los contactos indicados usando Prospeo.

    Va aparte del waterfall de emails porque cuesta 10 créditos por contacto
    en vez de 1: se dispara solo sobre una selección explícita.
    """
    global MOBILE_PROGRESS
    if _MOBILE_LOCK.locked():
        return
    async with _MOBILE_LOCK:
        from .providers import ProspeoProvider

        provider = ProspeoProvider(os.getenv("PROSPEO_API_KEY"))
        MOBILE_PROGRESS = EnrichProgress(running=True)

        if not provider.enabled:
            MOBILE_PROGRESS.running = False
            MOBILE_PROGRESS.finished = True
            MOBILE_PROGRESS.error = ("La búsqueda de teléfonos usa Prospeo y no hay "
                                     "una API key configurada.")
            return

        contacts = contacts_without_phone(contact_ids)
        MOBILE_PROGRESS.total = len(contacts)
        delay = float(os.getenv("ENRICH_DELAY_SECONDS", "1.0"))

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                for contact in contacts:
                    MOBILE_PROGRESS.current_contact = (
                        contact.get("full_name") or f"#{contact['id']}")
                    MOBILE_PROGRESS.current_provider = "prospeo"
                    try:
                        result = await provider.find_mobile(client, contact)
                    except Exception as exc:
                        from .providers.base import EnrichResult
                        result = EnrichResult(
                            False, error_message=f"{type(exc).__name__}: {exc}"[:500])

                    with get_db() as conn:
                        _log(conn, contact["id"], "prospeo_mobile", result)
                        if result.success and result.phone:
                            conn.execute(
                                """UPDATE contacts SET phone=?,
                                   updated_at=datetime('now') WHERE id=?""",
                                (result.phone, contact["id"]))
                            # Si de paso trajo un email y no teníamos, lo guardamos.
                            if result.email and not contact.get("email"):
                                conn.execute(
                                    """UPDATE contacts
                                       SET email=?, email_status=?, email_source='prospeo',
                                           updated_at=datetime('now')
                                       WHERE id=?""",
                                    (result.email,
                                     "verified" if result.verified else "unverified",
                                     contact["id"]))

                    if result.success and result.phone:
                        MOBILE_PROGRESS.found += 1
                        MOBILE_PROGRESS.by_provider["prospeo"] =                             MOBILE_PROGRESS.by_provider.get("prospeo", 0) + 1
                    else:
                        MOBILE_PROGRESS.not_found += 1

                    MOBILE_PROGRESS.processed += 1
                    if delay > 0:
                        await asyncio.sleep(delay)
        except Exception as exc:
            MOBILE_PROGRESS.error = f"{type(exc).__name__}: {exc}"[:500]
        finally:
            MOBILE_PROGRESS.running = False
            MOBILE_PROGRESS.finished = True
            MOBILE_PROGRESS.current_contact = None
            MOBILE_PROGRESS.current_provider = None
