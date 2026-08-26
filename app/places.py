"""Cliente de Google Places API (New) — búsqueda de negocios por texto.

Doc: https://developers.google.com/maps/documentation/places/web-service/text-search
  POST https://places.googleapis.com/v1/places:searchText
  Headers: X-Goog-Api-Key, X-Goog-FieldMask (obligatorio: define qué campos
  vienen y, con eso, cuánto cobra Google por la consulta).

Devuelve el negocio, nunca personas: nombre, teléfono, sitio, dirección y
calificación. El email sale después, del sitio web (ver website.py).
"""
import asyncio
import os

import httpx

from .csv_import import clean_domain

# "Sitios" que en realidad son un perfil ajeno: no son el dominio del negocio
# y entrar a scrapearlos no da nada (piden login o son un muro genérico).
REDES = ("facebook.com", "fb.me", "instagram.com", "linkedin.com", "twitter.com",
         "x.com", "tiktok.com", "youtube.com", "wa.me", "whatsapp.com", "t.me",
         "linktr.ee", "business.site", "sites.google.com", "google.com",
         "tripadvisor.", "booking.com", "airbnb.")


def _es_red(dominio: str | None) -> bool:
    d = (dominio or "").lower()
    return any(r in d for r in REDES)

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Cada campo que se pide encarece la consulta. Estos son los que el vendedor
# necesita para decidir a quién llamar; nada de fotos ni horarios.
FIELDS = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.websiteUri",
    "places.rating",
    "places.userRatingCount",
    "places.businessStatus",
    "places.primaryTypeDisplayName",
    "places.googleMapsUri",
    "nextPageToken",
])

PAGE_SIZE = 20          # máximo que acepta la API por página
MAX_RESULTS = 60        # máximo que la API entrega para una búsqueda


def configured() -> bool:
    return bool(os.getenv("GOOGLE_MAPS_API_KEY"))


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": os.getenv("GOOGLE_MAPS_API_KEY", ""),
        "X-Goog-FieldMask": FIELDS,
    }


def _explicar(status: int, body: dict) -> str:
    """Traduce el error de Google a algo accionable.

    El 403 es el que más aparece y su mensaje ("The caller does not have
    permission") no dice qué falta, que casi siempre es una de tres cosas.
    """
    msg = ((body.get("error") or {}).get("message") or "").strip()
    if status == 403:
        return ("Google rechazó la API key. Revisá en Google Cloud, sobre el mismo "
                "proyecto de la key: 1) que la facturación esté activada, "
                "2) que 'Places API (New)' esté habilitada, y 3) que si la key "
                "tiene restricción de APIs, esa esté en la lista.")
    if status == 400 and "field" in msg.lower():
        return f"Google no aceptó los campos pedidos: {msg}"
    if status == 429:
        return "Google está limitando las consultas (429). Probá de nuevo en un rato."
    return f"Google respondió {status}: {msg or 'sin detalle'}"


def normalize(place: dict) -> dict:
    """Deja el negocio con los mismos nombres de campo que usa la app."""
    web = place.get("websiteUri") or None
    phone = place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber")
    dominio = clean_domain(web) if web else None
    red = _es_red(dominio)
    return {
        "place_id": place.get("id"),
        "name": (place.get("displayName") or {}).get("text") or "",
        "address": place.get("formattedAddress") or None,
        "phone": phone or None,
        "website": web,
        "domain": None if red else dominio,
        # El perfil de red queda guardado igual: para un negocio chico suele
        # ser el único canal público que tiene.
        "social_url": web if red else None,
        "rating": place.get("rating"),
        "rating_count": place.get("userRatingCount"),
        "category": (place.get("primaryTypeDisplayName") or {}).get("text") or None,
        "maps_url": place.get("googleMapsUri") or None,
        "status": place.get("businessStatus") or None,
    }


async def search(query: str, max_results: int = MAX_RESULTS,
                 language: str = "es", region: str | None = None) -> dict:
    """Busca negocios por texto libre, paginando hasta max_results.

    Devuelve {"places": [...], "error": str|None, "pages": int}. Los errores se
    devuelven, no se levantan: la búsqueda es una acción del usuario y el
    mensaje tiene que llegarle a la pantalla.
    """
    if not configured():
        return {"places": [], "error": "Falta GOOGLE_MAPS_API_KEY en el .env.", "pages": 0}
    query = (query or "").strip()
    if not query:
        return {"places": [], "error": "Escribí qué querés buscar.", "pages": 0}

    max_results = max(1, min(int(max_results or MAX_RESULTS), MAX_RESULTS))
    encontrados: list[dict] = []
    vistos: set[str] = set()
    token: str | None = None
    paginas = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        while len(encontrados) < max_results:
            cuerpo: dict = {
                "textQuery": query,
                "languageCode": language,
                "pageSize": min(PAGE_SIZE, max_results - len(encontrados)),
            }
            if region:
                cuerpo["regionCode"] = region
            if token:
                cuerpo["pageToken"] = token

            try:
                resp = await client.post(SEARCH_URL, json=cuerpo, headers=_headers())
            except Exception as exc:
                return {"places": encontrados,
                        "error": f"No se pudo hablar con Google: {type(exc).__name__}",
                        "pages": paginas}

            try:
                body = resp.json()
            except Exception:
                body = {}

            if resp.status_code != 200:
                return {"places": encontrados, "error": _explicar(resp.status_code, body),
                        "pages": paginas}

            paginas += 1
            for p in body.get("places") or []:
                n = normalize(p)
                if n["place_id"] and n["place_id"] not in vistos and n["name"]:
                    vistos.add(n["place_id"])
                    encontrados.append(n)

            token = body.get("nextPageToken")
            if not token:
                break
            await asyncio.sleep(0.4)   # cortesía entre páginas

    return {"places": encontrados[:max_results], "error": None, "pages": paginas}
