"""Hunter.io — GET https://api.hunter.io/v2/email-finder, api_key en query. Síncrono.

Doc: https://hunter.io/api-documentation/v2
Requiere domain (o company) + nombre. verification.status: valid|accept_all|unknown.
"""
from .base import Provider, EnrichResult, name_variants, safe_json

URL = "https://api.hunter.io/v2/email-finder"


class HunterProvider(Provider):
    name = "hunter"

    async def find_email(self, client, contact) -> EnrichResult:
        """Prueba las variantes de apellido: Hunter no cobra si no encuentra."""
        domain = contact.get("company_domain")
        company = contact.get("company_name")
        if not (domain or company):
            return EnrichResult(False, error_message="Sin dominio ni empresa para Hunter.")

        variants = name_variants(contact)
        if not variants:
            return EnrichResult(False, error_message="Sin nombre para Hunter.")

        last_result = None
        for first, last in variants:
            params = {"api_key": self.api_key, "first_name": first, "last_name": last}
            if domain:
                params["domain"] = domain
            else:
                params["company"] = company

            safe_params = {k: v for k, v in params.items() if k != "api_key"}
            resp = await self._request_with_retry(client, "GET", URL, params=params)
            body = safe_json(resp)

            if resp.status_code == 200:
                data = body.get("data") or {}
                email = data.get("email")
                if email:
                    status = ((data.get("verification") or {}).get("status") or "").lower()
                    score = data.get("score") or 0
                    return EnrichResult(True, email=email,
                                        verified=(status == "valid" or score >= 90),
                                        phone=data.get("phone_number"),
                                        request_payload=safe_params, response_payload=body)
                last_result = EnrichResult(False, request_payload=safe_params,
                                           response_payload=body,
                                           error_message="Hunter no encontró email.")
                continue

            errors = body.get("errors") or []
            msg = errors[0].get("details") if errors and isinstance(errors[0], dict)                 else f"HTTP {resp.status_code}"
            fatal = resp.status_code in (401, 403)
            last_result = EnrichResult(False, request_payload=safe_params,
                                       response_payload=body,
                                       error_message=str(msg)[:500], fatal=fatal)
            if fatal:
                break

        return last_result or EnrichResult(False, error_message="Hunter no encontró email.")
