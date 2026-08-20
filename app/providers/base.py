"""Contrato común de los proveedores de enriquecimiento."""
import asyncio
import os
import random
from dataclasses import dataclass, field

import httpx


@dataclass
class EnrichResult:
    """Resultado normalizado de un intento contra un proveedor."""
    success: bool
    email: str | None = None
    verified: bool = False
    request_payload: dict = field(default_factory=dict)
    response_payload: dict | None = None
    error_message: str | None = None
    # True => el error es del proveedor/credenciales, no del contacto.
    # Sirve para no quemar la cadena entera si la key está mal.
    fatal: bool = False


class Provider:
    name = "base"
    # Errores que justifican reintento con backoff.
    RETRY_STATUS = {429, 500, 502, 503, 504}

    def __init__(self, api_key: str | None):
        self.api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def find_email(self, client: httpx.AsyncClient, contact: dict) -> EnrichResult:
        raise NotImplementedError

    async def _request_with_retry(self, client, method, url, *, max_retries=None, **kw):
        """Ejecuta el request reintentando 429/5xx con backoff exponencial + jitter."""
        max_retries = max_retries or int(os.getenv("ENRICH_MAX_RETRIES", "3"))
        last_exc = None
        for attempt in range(max_retries):
            try:
                resp = await client.request(method, url, **kw)
                if resp.status_code in self.RETRY_STATUS and attempt < max_retries - 1:
                    delay = self._backoff(resp, attempt)
                    await asyncio.sleep(delay)
                    continue
                return resp
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                if attempt < max_retries - 1:
                    await asyncio.sleep((2 ** attempt) + random.uniform(0, 0.5))
                    continue
                raise
        if last_exc:
            raise last_exc
        return resp

    @staticmethod
    def _backoff(resp: httpx.Response, attempt: int) -> float:
        """Respeta Retry-After si el proveedor lo manda; si no, exponencial."""
        ra = resp.headers.get("Retry-After")
        if ra:
            try:
                return min(float(ra), 60.0)
            except ValueError:
                pass
        return (2 ** attempt) + random.uniform(0, 0.5)


def safe_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"raw": data}
    except Exception:
        return {"raw_text": resp.text[:2000]}
