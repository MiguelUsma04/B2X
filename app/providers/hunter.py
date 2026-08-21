"""Hunter.io — GET https://api.hunter.io/v2/email-finder, api_key en query. Síncrono.

Doc: https://hunter.io/api-documentation/v2
Requiere domain (o company) + nombre. verification.status: valid|accept_all|unknown.
"""
from .base import Provider, EnrichResult, safe_json

URL = "https://api.hunter.io/v2/email-finder"


class HunterProvider(Provider):
    name = "hunter"

    async def find_email(self, client, contact) -> EnrichResult:
        params = {"api_key": self.api_key}
        if contact.get("company_domain"):
            params["domain"] = contact["company_domain"]
        elif contact.get("company_name"):
            params["company"] = contact["company_name"]
        else:
            return EnrichResult(False, error_message="Sin dominio ni empresa para Hunter.")

        if contact.get("first_name") and contact.get("last_name"):
            params["first_name"] = contact["first_name"]
            params["last_name"] = contact["last_name"]
        elif contact.get("full_name"):
            params["full_name"] = contact["full_name"]
        else:
            return EnrichResult(False, error_message="Sin nombre para Hunter.")

        safe_params = {k: v for k, v in params.items() if k != "api_key"}
        resp = await self._request_with_retry(client, "GET", URL, params=params)
        body = safe_json(resp)

        if resp.status_code == 200:
            data = body.get("data") or {}
            email = data.get("email")
            if email:
                status = ((data.get("verification") or {}).get("status") or "").lower()
                score = data.get("score") or 0
                # 'valid' es verificado; accept_all/unknown con score alto lo tratamos
                # como no verificado pero utilizable.
                return EnrichResult(True, email=email,
                                    verified=(status == "valid" or score >= 90),
                                    phone=data.get("phone_number"),
                                    request_payload=safe_params, response_payload=body)
            return EnrichResult(False, request_payload=safe_params, response_payload=body,
                                error_message="Hunter no encontró email.")

        errors = body.get("errors") or []
        msg = errors[0].get("details") if errors and isinstance(errors[0], dict) \
            else f"HTTP {resp.status_code}"
        fatal = resp.status_code in (401, 403)
        return EnrichResult(False, request_payload=safe_params, response_payload=body,
                            error_message=str(msg)[:500], fatal=fatal)
