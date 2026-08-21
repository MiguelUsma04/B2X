"""Icypeas — asíncrono en dos pasos.

Doc: https://api-doc.icypeas.com/getting-started
  1) POST https://app.icypeas.com/api/email-search
     headers: Authorization: <API_KEY>  (la key sola, sin 'Bearer')
     body: {firstname, lastname, domainOrCompany}
     -> {"success":true,"item":{"status":"NONE","_id":"..."}}
  2) POST https://app.icypeas.com/api/bulk-single-searchs/read  body {"id": "<_id>"}
     hasta que status deje de ser NONE/SCHEDULED/IN_PROGRESS.
     DEBITED = encontrado. results.emails[].certainty: ultra_sure|very_probable|probable|...
"""
import asyncio
from .base import Provider, EnrichResult, safe_json

SEARCH_URL = "https://app.icypeas.com/api/email-search"
READ_URL = "https://app.icypeas.com/api/bulk-single-searchs/read"

PENDING_STATUSES = {"NONE", "SCHEDULED", "IN_PROGRESS"}
SUCCESS_STATUSES = {"DEBITED", "FREE"}
VERIFIED_CERTAINTY = {"ultra_sure", "very_probable"}

POLL_DELAYS = [2, 3, 4, 5, 6, 8, 10]  # ~38s de espera máxima


class IcypeasProvider(Provider):
    name = "icypeas"

    def _headers(self) -> dict:
        return {"Authorization": self.api_key, "Content-Type": "application/json"}

    async def find_email(self, client, contact) -> EnrichResult:
        company = contact.get("company_domain") or contact.get("company_name")
        if not company:
            return EnrichResult(False, error_message="Sin dominio ni empresa para Icypeas.")

        first = contact.get("first_name")
        last = contact.get("last_name")
        if not (first and last) and contact.get("full_name"):
            parts = contact["full_name"].split()
            if len(parts) >= 2:
                first, last = parts[0], " ".join(parts[1:])
        if not (first and last):
            return EnrichResult(False, error_message="Icypeas requiere nombre y apellido.")

        payload = {"firstname": first, "lastname": last, "domainOrCompany": company}

        resp = await self._request_with_retry(client, "POST", SEARCH_URL,
                                              json=payload, headers=self._headers())
        body = safe_json(resp)
        if resp.status_code not in (200, 201) or not body.get("success"):
            msg = body.get("message") or body.get("error") or f"HTTP {resp.status_code}"
            return EnrichResult(False, request_payload=payload, response_payload=body,
                                error_message=str(msg)[:500],
                                fatal=resp.status_code in (401, 403))

        search_id = (body.get("item") or {}).get("_id")
        if not search_id:
            return EnrichResult(False, request_payload=payload, response_payload=body,
                                error_message="Icypeas no devolvió un id de búsqueda.")

        # Polling hasta que el worker resuelva.
        last_body = body
        for delay in POLL_DELAYS:
            await asyncio.sleep(delay)
            r = await self._request_with_retry(client, "POST", READ_URL,
                                               json={"id": search_id},
                                               headers=self._headers())
            rb = safe_json(r)
            last_body = rb
            items = rb.get("items") or []
            if not items:
                continue
            item = items[0]
            status = str(item.get("status", "")).upper()
            if status in PENDING_STATUSES:
                continue

            results = item.get("results") or {}
            emails = results.get("emails") or []
            # Icypeas a veces incluye teléfonos sin costo extra; los aprovechamos.
            phones = results.get("phones") or []
            phone = None
            if phones:
                first = phones[0]
                phone = first.get("phone") if isinstance(first, dict) else first
            if status in SUCCESS_STATUSES and emails:
                best = emails[0]
                certainty = str(best.get("certainty", "")).lower()
                return EnrichResult(True, email=best.get("email"),
                                    verified=certainty in VERIFIED_CERTAINTY,
                                    phone=phone,
                                    request_payload=payload, response_payload=rb)
            return EnrichResult(False, request_payload=payload, response_payload=rb,
                                error_message=f"Icypeas sin resultado (status={status}).")

        return EnrichResult(False, request_payload=payload, response_payload=last_body,
                            error_message="Icypeas: timeout esperando el resultado.")
