"""Lectura con IA del sitio del negocio (Gemini).

El scraper de website.py saca lo que se puede sacar con reglas: emails,
teléfonos, WhatsApp. Eso es gratis y no necesita IA. Lo que las reglas no
pueden es entender: qué vende el negocio, a quién, quién manda ahí, hace
cuánto existe. Para eso está este módulo.

No baja nada: recibe el texto que website.py ya leyó, así una pasada de IA no
duplica las visitas al sitio ni el tiempo.

API: POST https://generativelanguage.googleapis.com/v1beta/interactions
con `response_format` para que conteste JSON contra un esquema fijo.
Doc: https://ai.google.dev/gemini-api/docs/structured-output
"""
import json
import os

import httpx

URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
MODELO_POR_DEFECTO = "gemini-3.7-flash"
MAX_CHARS = 40_000          # el texto que se le manda; más que esto no aporta


def configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def modelo() -> str:
    return os.getenv("GEMINI_MODEL", MODELO_POR_DEFECTO)


# Lo que se le pide. Todo opcional: un sitio de una página no tiene la mitad
# de esto, y es preferible un campo vacío a un dato inventado.
ESQUEMA = {
    "type": "object",
    "properties": {
        "resumen": {
            "type": "string",
            "description": "Qué hace el negocio, en una o dos frases, como se lo "
                           "explicarías a un vendedor antes de que llame.",
        },
        "que_vende": {
            "type": "array", "items": {"type": "string"},
            "description": "Servicios o productos concretos que ofrece.",
        },
        "vende_a": {
            "type": "string",
            "enum": ["empresas", "consumidor_final", "ambos", "no_esta_claro"],
            "description": "A quién le vende, según cómo se presenta el sitio.",
        },
        "propuesta_de_valor": {
            "type": "string",
            "description": "Con qué dice diferenciarse. Vacío si el sitio no lo dice.",
        },
        "personas": {
            "type": "array",
            "description": "Personas nombradas en el sitio: dueños, gerentes, "
                           "encargados. Solo las que figuran de verdad.",
            "items": {
                "type": "object",
                "properties": {
                    "nombre": {"type": "string"},
                    "cargo": {"type": "string"},
                    "email": {"type": "string"},
                    "telefono": {"type": "string"},
                },
                "required": ["nombre"],
            },
        },
        "anios_en_el_mercado": {
            "type": "string",
            "description": "Antigüedad o año de fundación, tal como lo dice el sitio.",
        },
        "tamanio": {
            "type": "string",
            "description": "Señales de tamaño: empleados, sucursales, flota, "
                           "volumen. Textual, sin estimar.",
        },
        "ciudades": {
            "type": "array", "items": {"type": "string"},
            "description": "Ciudades o zonas donde opera o tiene sede.",
        },
        "marcas_o_certificaciones": {
            "type": "array", "items": {"type": "string"},
            "description": "Marcas que representa, certificaciones, afiliaciones.",
        },
        "redes": {
            "type": "array", "items": {"type": "string"},
            "description": "URLs de redes sociales que aparezcan.",
        },
        "vende_online": {
            "type": "boolean",
            "description": "Si se puede comprar o reservar desde el propio sitio.",
        },
        "idiomas": {
            "type": "array", "items": {"type": "string"},
            "description": "Idiomas en los que está el sitio.",
        },
        "gancho": {
            "type": "string",
            "description": "Un dato puntual del sitio que sirva para abrir una "
                           "conversación con ellos. Concreto, no genérico.",
        },
        "confianza": {
            "type": "string", "enum": ["alta", "media", "baja"],
            "description": "Qué tan sólido es lo extraído: alta si el sitio lo "
                           "dice claro, baja si hubo que deducir de poco texto.",
        },
    },
    "required": ["resumen", "vende_a", "confianza"],
}

INSTRUCCIONES = (
    "Sos un analista comercial. Te paso el texto del sitio web de un negocio y "
    "tenés que llenar la ficha para que un vendedor sepa con quién va a hablar.\n\n"
    "Reglas:\n"
    "- Solo lo que el sitio dice. Si un dato no está, dejá el campo vacío o la "
    "lista sin elementos. No completes con lo que suele pasar en el rubro.\n"
    "- En 'personas' va únicamente gente nombrada explícitamente. Nunca inventes "
    "un nombre a partir de un email como info@ o ventas@.\n"
    "- Nada de relleno de marketing: si la propuesta de valor del sitio es "
    "'calidad y servicio', ese campo va vacío.\n"
    "- Escribí en español, en el mismo tono en que le hablarías a un colega."
)


def _pedido(negocio: dict, texto: str) -> dict:
    ficha = "\n".join(
        f"{k}: {v}" for k, v in [
            ("Negocio", negocio.get("full_name")),
            ("Rubro según Google", negocio.get("category")),
            ("Dirección", negocio.get("address")),
            ("Sitio", negocio.get("company_domain")),
        ] if v
    )
    return {
        "model": modelo(),
        "input": (f"{INSTRUCCIONES}\n\n=== Datos que ya tenemos ===\n{ficha}\n\n"
                  f"=== Texto del sitio ===\n{texto[:MAX_CHARS]}"),
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": ESQUEMA,
        },
    }


def _texto_de(body: dict) -> str | None:
    """Saca el texto generado, sea cual sea la envoltura que use la API.

    La forma de la respuesta no está documentada en la guía pública y cambió
    con el endpoint nuevo, así que se prueban las que existen en vez de
    apostar a una sola y romper con la próxima versión.
    """
    if not isinstance(body, dict):
        return None
    directo = body.get("output_text") or body.get("text")
    if isinstance(directo, str) and directo.strip():
        return directo

    # Formas anidadas conocidas: output[].content[].text y candidates[].content.parts[].text
    for clave in ("output", "outputs", "content", "candidates"):
        rama = body.get(clave)
        if not rama:
            continue
        pila, textos = [rama], []
        while pila:
            nodo = pila.pop(0)
            if isinstance(nodo, str):
                continue
            if isinstance(nodo, list):
                pila.extend(nodo)
            elif isinstance(nodo, dict):
                t = nodo.get("text")
                if isinstance(t, str) and t.strip():
                    textos.append(t)
                for v in nodo.values():
                    if isinstance(v, (list, dict)):
                        pila.append(v)
        if textos:
            return "\n".join(textos)
    return None


def _tokens_de(body: dict) -> dict:
    uso = {}
    for clave in ("usage", "usageMetadata", "usage_metadata"):
        u = body.get(clave)
        if isinstance(u, dict):
            uso = u
            break
    def num(*nombres):
        for n in nombres:
            v = uso.get(n)
            if isinstance(v, (int, float)):
                return int(v)
        return 0
    return {
        "entrada": num("input_tokens", "promptTokenCount", "prompt_tokens"),
        "salida": num("output_tokens", "candidatesTokenCount", "completion_tokens"),
        "total": num("total_tokens", "totalTokenCount"),
    }


async def analizar(client: httpx.AsyncClient, negocio: dict, texto: str) -> dict:
    """Devuelve {"perfil": dict|None, "tokens": dict, "error": str|None}."""
    if not configured():
        return {"perfil": None, "tokens": {}, "error": "Falta GEMINI_API_KEY en el .env."}
    if not (texto or "").strip():
        return {"perfil": None, "tokens": {}, "error": "No hay texto del sitio para leer."}

    try:
        resp = await client.post(
            URL, json=_pedido(negocio, texto),
            headers={"x-goog-api-key": os.getenv("GEMINI_API_KEY", ""),
                     "Content-Type": "application/json"})
    except Exception as exc:
        return {"perfil": None, "tokens": {},
                "error": f"No se pudo hablar con Gemini: {type(exc).__name__}"}

    try:
        body = resp.json()
    except Exception:
        body = {}
    # La API a veces envuelve la respuesta en un array (los errores vienen así).
    if isinstance(body, list):
        body = next((x for x in body if isinstance(x, dict)), {})

    if resp.status_code != 200:
        msg = ((body.get("error") or {}).get("message")
               or resp.text[:200] or f"HTTP {resp.status_code}")
        if resp.status_code == 403:
            msg = ("Gemini rechazó la key. Revisá que la 'Generative Language API' "
                   "esté habilitada y que la key la tenga permitida.")
        elif resp.status_code == 429:
            # El 429 tapa dos cosas muy distintas: quedarse sin crédito y
            # pegarle demasiado rápido. El mensaje de Google distingue, el mío
            # no, así que se conserva el suyo cuando dice algo.
            msg = msg or "Gemini está limitando las consultas. Probá más tarde."
        return {"perfil": None, "tokens": {}, "error": str(msg)[:300]}

    crudo = _texto_de(body)
    if not crudo:
        return {"perfil": None, "tokens": _tokens_de(body),
                "error": "Gemini contestó en un formato que no se pudo leer.",
                "body": body}

    try:
        perfil = json.loads(crudo)
    except Exception:
        # A veces envuelve el JSON en ```json ... ```
        limpio = crudo.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        try:
            perfil = json.loads(limpio.strip())
        except Exception:
            return {"perfil": None, "tokens": _tokens_de(body),
                    "error": "Gemini no devolvió JSON válido.", "body": crudo[:500]}

    if not isinstance(perfil, dict):
        return {"perfil": None, "tokens": _tokens_de(body),
                "error": "El JSON de Gemini no es una ficha."}
    return {"perfil": perfil, "tokens": _tokens_de(body), "error": None}
