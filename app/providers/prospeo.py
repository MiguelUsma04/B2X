"""Prospeo — POST https://api.prospeo.io/enrich-person, header X-KEY. Síncrono.

Doc: https://prospeo.io/api-docs/enrich-person
Mínimo para match: first_name + last_name + (company_name|company_website|company_linkedin_url),
o full_name + company, o linkedin_url solo.
"""
import os
from .base import Provider, EnrichResult, safe_json

URL = "https://api.prospeo.io/enrich-person"


class ProspeoProvider(Provider):
    name = "prospeo"

    async def find_email(self, client, contact) -> EnrichResult:
        data: dict = {}
        if contact.get("first_name") and contact.get("last_name"):
            data["first_name"] = contact["first_name"]
            data["last_name"] = contact["last_name"]
        elif contact.get("full_name"):
            data["full_name"] = contact["full_name"]
        if contact.get("company_domain"):
            data["company_website"] = contact["company_domain"]
        if contact.get("company_name"):
            data["company_name"] = contact["company_name"]
        if contact.get("linkedin_url"):
            data["linkedin_url"] = contact["linkedin_url"]

        # La doc exige nombre + alguna señal de empresa, o linkedin_url solo.
        has_name = "first_name" in data or "full_name" in data
        has_company = any(k in data for k in
                          ("company_website", "company_name", "company_linkedin_url"))
        if not ((has_name and has_company) or "linkedin_url" in data):
            return EnrichResult(False, request_payload={"data": data},
                                error_message="Datos insuficientes para Prospeo "
                                              "(requiere nombre + empresa, o linkedin_url).")

        payload = {"data": data}
        if os.getenv("ONLY_VERIFIED_EMAIL", "false").lower() == "true":
            payload["only_verified_email"] = True

        resp = await self._request_with_retry(
            client, "POST", URL, json=payload,
            headers={"X-KEY": self.api_key, "Content-Type": "application/json"},
        )
        body = safe_json(resp)

        if resp.status_code == 200 and not body.get("error"):
            resp_data = body.get("response") or {}
            email_obj = resp_data.get("email")
            # El campo email puede venir como string o como objeto {email,status}
            if isinstance(email_obj, dict):
                email = email_obj.get("email")
                verified = str(email_obj.get("status", "")).upper() == "VERIFIED"
            else:
                email = email_obj
                verified = str(resp_data.get("email_status", "")).upper() == "VERIFIED"
            if email:
                return EnrichResult(True, email=email, verified=verified,
                                    request_payload=payload, response_payload=body)
            return EnrichResult(False, request_payload=payload, response_payload=body,
                                error_message="Sin email en la respuesta.")

        msg = body.get("message") or body.get("error") or f"HTTP {resp.status_code}"
        code = str(body.get("error_code") or body.get("code") or "").upper()
        # Problemas de cuenta: no tiene sentido insistir con este proveedor.
        fatal = code in {"INVALID_API_KEY", "INSUFFICIENT_CREDITS"} or resp.status_code in (401, 403)
        if code == "NO_MATCH":
            msg = "Sin coincidencia (NO_MATCH)."
            fatal = False
        return EnrichResult(False, request_payload=payload, response_payload=body,
                            error_message=str(msg)[:500], fatal=fatal)
