"""Prospeo — POST https://api.prospeo.io/enrich-person, header X-KEY. Síncrono.

Doc: https://prospeo.io/api-docs/enrich-person

Forma de la respuesta (confirmada en la doc):
    {"error": false,
     "person": {"email":  {"status":"VERIFIED","revealed":true,"email":"a@b.com", ...},
                "mobile": {"status":"VERIFIED","revealed":false,"mobile":"+1 415-3**-****", ...},
                ...},
     "company": {...}}

Cuidado con `revealed`: si es false, el dato viene enmascarado (con asteriscos)
y no sirve. El móvil solo se revela pidiendo enrich_mobile=true, que cuesta
10 créditos en vez de 1.

Mínimo para match: first_name + last_name + (company_name|company_website|
company_linkedin_url), o full_name + company, o linkedin_url solo.
"""
import os

from .base import Provider, EnrichResult, safe_json

URL = "https://api.prospeo.io/enrich-person"


def _build_data(contact: dict) -> dict:
    """Arma los datapoints de identificación.

    El linkedin_url va primero porque identifica a la persona sin ambigüedad;
    con nombres latinos (donde Apollo mete el segundo nombre dentro del
    apellido) el par nombre+dominio falla seguido.
    """
    data: dict = {}
    if contact.get("linkedin_url"):
        data["linkedin_url"] = contact["linkedin_url"]
    if contact.get("first_name") and contact.get("last_name"):
        data["first_name"] = contact["first_name"]
        data["last_name"] = contact["last_name"]
    elif contact.get("full_name"):
        data["full_name"] = contact["full_name"]
    if contact.get("company_domain"):
        data["company_website"] = contact["company_domain"]
    if contact.get("company_name"):
        data["company_name"] = contact["company_name"]
    return data


def _has_minimum(data: dict) -> bool:
    has_name = "first_name" in data or "full_name" in data
    has_company = any(k in data for k in
                      ("company_website", "company_name", "company_linkedin_url"))
    return (has_name and has_company) or "linkedin_url" in data


def _pick(obj: dict | None, key: str) -> tuple[str | None, bool]:
    """Extrae (valor, verificado) de un sub-objeto email/mobile de Prospeo.

    Devuelve None si el dato no fue revelado: en ese caso viene enmascarado
    con asteriscos y guardarlo sería peor que no tener nada.
    """
    if not isinstance(obj, dict):
        return None, False
    if obj.get("revealed") is False:
        return None, False
    value = obj.get(key)
    if not value or "*" in str(value):
        return None, False
    return value, str(obj.get("status", "")).upper() == "VERIFIED"


class ProspeoProvider(Provider):
    name = "prospeo"

    async def find_email(self, client, contact) -> EnrichResult:
        return await self._enrich(client, contact, want_mobile=False)

    async def find_mobile(self, client, contact) -> EnrichResult:
        """Revela el móvil. Cuesta 10 créditos (el email viene incluido)."""
        return await self._enrich(client, contact, want_mobile=True)

    async def _enrich(self, client, contact, want_mobile: bool) -> EnrichResult:
        data = _build_data(contact)
        if not _has_minimum(data):
            return EnrichResult(False, request_payload={"data": data},
                                error_message="Datos insuficientes para Prospeo "
                                              "(requiere nombre + empresa, o linkedin_url).")

        payload: dict = {"data": data}
        if want_mobile:
            payload["enrich_mobile"] = True
        elif os.getenv("ONLY_VERIFIED_EMAIL", "false").lower() == "true":
            payload["only_verified_email"] = True

        resp = await self._request_with_retry(
            client, "POST", URL, json=payload,
            headers={"X-KEY": self.api_key, "Content-Type": "application/json"},
        )
        body = safe_json(resp)

        if resp.status_code == 200 and not body.get("error"):
            person = body.get("person") or {}
            email, email_verified = _pick(person.get("email"), "email")
            mobile, _ = _pick(person.get("mobile"), "mobile")

            if want_mobile:
                if mobile:
                    return EnrichResult(True, email=email, verified=email_verified,
                                        phone=mobile, request_payload=payload,
                                        response_payload=body)
                return EnrichResult(False, request_payload=payload, response_payload=body,
                                    error_message="Prospeo no tiene móvil para este contacto.")

            if email:
                return EnrichResult(True, email=email, verified=email_verified,
                                    phone=mobile, request_payload=payload,
                                    response_payload=body)
            return EnrichResult(False, request_payload=payload, response_payload=body,
                                error_message="Prospeo no devolvió un email utilizable.")

        msg = body.get("message") or body.get("error") or f"HTTP {resp.status_code}"
        code = str(body.get("error_code") or body.get("code") or "").upper()
        # Problemas de cuenta: no tiene sentido insistir con este proveedor.
        fatal = code in {"INVALID_API_KEY", "INSUFFICIENT_CREDITS"} or resp.status_code in (401, 403)
        if code == "NO_MATCH":
            msg = "Sin coincidencia (NO_MATCH)."
            fatal = False
        return EnrichResult(False, request_payload=payload, response_payload=body,
                            error_message=str(msg)[:500], fatal=fatal)
