"""Lectura con IA del sitio del negocio (OpenAI).

El scraper de website.py saca lo que se puede sacar con reglas: emails,
teléfonos, WhatsApp. Eso es gratis y no necesita IA. Lo que las reglas no
pueden es entender: qué vende el negocio, a quién, quién manda ahí, hace
cuánto existe. Para eso está este módulo.

No baja nada: recibe el texto que website.py ya leyó, así una pasada de IA no
duplica las visitas al sitio ni el tiempo.

API: POST https://api.openai.com/v1/responses con text.format de tipo
json_schema, que obliga a la respuesta a cumplir el esquema de acá abajo.
Doc: https://developers.openai.com/api/docs/guides/structured-outputs
"""
import json
import os

import httpx

URL = "https://api.openai.com/v1/responses"
MODELO_POR_DEFECTO = "gpt-5-nano"
MAX_CHARS = 40_000          # el texto que se le manda; más que esto no aporta
MAX_SALIDA = 4_000          # techo de la respuesta: la ficha no necesita más
# Esto no es una tarea de razonamiento, es de extracción. Sin bajarle el
# esfuerzo, gpt-5-nano se gastó los 3.000 tokens pensando y se quedó sin
# espacio para escribir la ficha: 2.944 de razonamiento y cero de respuesta.
ESFUERZO_POR_DEFECTO = "minimal"


def configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def modelo() -> str:
    return os.getenv("OPENAI_MODEL", MODELO_POR_DEFECTO)


def esfuerzo() -> str:
    return os.getenv("OPENAI_EFFORT", ESFUERZO_POR_DEFECTO)


def _texto(desc: str) -> dict:
    """Campo de texto que puede venir vacío.

    En modo estricto OpenAI exige que TODOS los campos estén en 'required':
    lo opcional se expresa aceptando null, no sacándolo de la lista.
    """
    return {"type": ["string", "null"], "description": desc}


def _lista(desc: str) -> dict:
    return {"type": "array", "items": {"type": "string"}, "description": desc}


ESQUEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "resumen": _texto("Qué hace el negocio, en una o dos frases, como se lo "
                          "explicarías a un vendedor antes de que llame."),
        "que_vende": _lista("Servicios o productos concretos que ofrece."),
        "vende_a": {
            "type": "string",
            "enum": ["empresas", "consumidor_final", "ambos", "no_esta_claro"],
            "description": "A quién le vende, según cómo se presenta el sitio.",
        },
        "propuesta_de_valor": _texto("Con qué dice diferenciarse. null si el sitio "
                                     "no lo dice o si es relleno de marketing."),
        "personas": {
            "type": "array",
            "description": "Personas nombradas en el sitio: dueños, gerentes, "
                           "encargados. Solo las que figuran de verdad.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "nombre": {"type": "string"},
                    "cargo": _texto("Su cargo, si el sitio lo dice."),
                    "email": _texto("Su email personal, si figura."),
                    "telefono": _texto("Su teléfono, si figura."),
                },
                "required": ["nombre", "cargo", "email", "telefono"],
            },
        },
        "anios_en_el_mercado": _texto("Antigüedad o año de fundación, tal como lo "
                                      "dice el sitio."),
        "tamanio": _texto("Señales de tamaño: empleados, sucursales, flota, "
                          "volumen. Textual, sin estimar."),
        "ciudades": _lista("Ciudades o zonas donde opera o tiene sede."),
        "marcas_o_certificaciones": _lista("Marcas que representa, certificaciones, "
                                           "afiliaciones gremiales."),
        "redes": _lista("URLs de redes sociales que aparezcan."),
        "vende_online": {
            "type": ["boolean", "null"],
            "description": "Si se puede comprar o reservar desde el propio sitio. "
                           "null si no se puede saber.",
        },
        "idiomas": _lista("Idiomas en los que está el sitio."),
        "gancho": _texto("Un dato puntual del sitio que sirva para abrir una "
                         "conversación con ellos. Concreto, no genérico."),
        "confianza": {
            "type": "string",
            "enum": ["alta", "media", "baja"],
            "description": "Qué tan sólido es lo extraído: alta si el sitio lo dice "
                           "claro, baja si hubo que deducir de muy poco texto.",
        },
    },
    "required": [
        "resumen", "que_vende", "vende_a", "propuesta_de_valor", "personas",
        "anios_en_el_mercado", "tamanio", "ciudades", "marcas_o_certificaciones",
        "redes", "vende_online", "idiomas", "gancho", "confianza",
    ],
}

INSTRUCCIONES = (
    "Sos un analista comercial. Te paso el texto del sitio web de un negocio y "
    "tenés que llenar la ficha para que un vendedor sepa con quién va a hablar.\n\n"
    "Reglas:\n"
    "- Solo lo que el sitio dice. Si un dato no está, poné null o dejá la lista "
    "vacía. No completes con lo que suele pasar en el rubro.\n"
    "- En 'personas' va únicamente gente nombrada explícitamente, con nombre "
    "de persona. Si el sitio no nombra a nadie, devolvé la lista vacía: no "
    "pongas 'null', ni 'ninguno', ni una frase explicando que no hay. Nunca "
    "armes un nombre a partir de un email como info@, ventas@ o gerencia@.\n"
    "- Nada de relleno de marketing: si la propuesta de valor del sitio es "
    "'calidad y servicio', ese campo va en null.\n"
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
        "input": [
            {"role": "system", "content": INSTRUCCIONES},
            {"role": "user", "content": (f"=== Datos que ya tenemos ===\n{ficha}\n\n"
                                         f"=== Texto del sitio ===\n"
                                         f"{texto[:MAX_CHARS]}")},
        ],
        "max_output_tokens": MAX_SALIDA,
        "reasoning": {"effort": esfuerzo()},
        "text": {
            "format": {
                "type": "json_schema",
                "name": "ficha_negocio",
                "strict": True,
                "schema": ESQUEMA,
            }
        },
    }


def _texto_de(body: dict) -> str | None:
    """Saca el JSON generado de la respuesta.

    La forma esperada es output[].content[].text, pero se recorre el árbol en
    vez de indexar posiciones fijas: los modelos con razonamiento intercalan
    bloques propios antes del mensaje, y una posición fija se rompe con ellos.
    """
    if not isinstance(body, dict):
        return None
    directo = body.get("output_text")
    if isinstance(directo, str) and directo.strip():
        return directo

    pila, textos = [body.get("output") or body.get("content") or []], []
    while pila:
        nodo = pila.pop(0)
        if isinstance(nodo, list):
            pila.extend(nodo)
        elif isinstance(nodo, dict):
            if nodo.get("type") in (None, "output_text", "text", "message"):
                t = nodo.get("text")
                if isinstance(t, str) and t.strip():
                    textos.append(t)
            for v in nodo.values():
                if isinstance(v, (list, dict)):
                    pila.append(v)
    return "\n".join(textos) if textos else None


def _tokens_de(body: dict) -> dict:
    uso = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(uso, dict):
        return {"entrada": 0, "salida": 0, "total": 0}

    def num(*nombres):
        for n in nombres:
            v = uso.get(n)
            if isinstance(v, (int, float)):
                return int(v)
        return 0

    return {
        "entrada": num("input_tokens", "prompt_tokens"),
        "salida": num("output_tokens", "completion_tokens"),
        "total": num("total_tokens"),
    }


# Lo que el modelo devuelve cuando en realidad no encontró a nadie. El esquema
# obliga a que 'nombre' sea texto, así que en vez de dejar la lista vacía a
# veces mete ahí la explicación de que no hay nadie.
_NO_ES_NOMBRE = {
    "null", "none", "ninguno", "ninguna", "n/a", "na", "no aplica", "-", "--",
    "sin datos", "sin nombre", "no disponible", "desconocido", "no especificado",
}
_ES_BUZON = {
    "info", "contacto", "ventas", "gerencia", "gerenciageneral", "administracion",
    "admin", "soporte", "comercial", "recepcion", "atencion", "reservas",
    "marketing", "rrhh", "facturacion", "gerente", "equipo", "staff",
}


def _limpiar_personas(perfil: dict) -> dict:
    """Saca de 'personas' lo que no es una persona.

    Pedirlo en el prompt no alcanza: el modelo igual devuelve 'null' o frases
    como 'no hay nombres identificables' metidas en el campo del nombre, y eso
    llegaba a la pantalla como si fuera un contacto real.
    """
    limpias = []
    for q in perfil.get("personas") or []:
        if not isinstance(q, dict):
            continue
        nombre = (q.get("nombre") or "").strip()
        plano = nombre.lower().strip(" .:-")
        if not nombre or plano in _NO_ES_NOMBRE:
            continue
        if plano.replace(" ", "") in _ES_BUZON or "@" in nombre:
            continue
        # Una explicación, no un nombre: nadie se llama con siete palabras.
        if len(nombre) > 60 or len(nombre.split()) > 6:
            continue
        if plano.startswith(("no hay", "no se", "sin ", "no figura", "no aparece")):
            continue
        limpias.append(q)
    perfil["personas"] = limpias
    return perfil


async def analizar(client: httpx.AsyncClient, negocio: dict, texto: str) -> dict:
    """Devuelve {"perfil": dict|None, "tokens": dict, "error": str|None}."""
    if not configured():
        return {"perfil": None, "tokens": {}, "error": "Falta OPENAI_API_KEY en el .env."}
    if not (texto or "").strip():
        return {"perfil": None, "tokens": {}, "error": "No hay texto del sitio para leer."}

    cabeceras = {"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '')}",
                 "Content-Type": "application/json"}
    pedido = _pedido(negocio, texto)

    async def enviar(cuerpo):
        r = await client.post(URL, json=cuerpo, headers=cabeceras)
        try:
            b = r.json()
        except Exception:
            b = {}
        if isinstance(b, list):
            b = next((x for x in b if isinstance(x, dict)), {})
        return r, b

    try:
        resp, body = await enviar(pedido)
        # No todos los modelos aceptan el mismo nivel de esfuerzo, y los que no
        # razonan no aceptan el parámetro. Si lo rechaza, se reintenta sin él en
        # vez de obligar a tocar el .env para cambiar de modelo.
        if resp.status_code == 400:
            msg = ((body.get("error") or {}).get("message") or "").lower()
            if "effort" in msg or "reasoning" in msg or esfuerzo() in msg:
                pedido.pop("reasoning", None)
                resp, body = await enviar(pedido)
    except Exception as exc:
        return {"perfil": None, "tokens": {},
                "error": f"No se pudo hablar con OpenAI: {type(exc).__name__}"}

    if resp.status_code != 200:
        err = body.get("error") if isinstance(body, dict) else None
        msg = (err or {}).get("message") or resp.text[:200] or f"HTTP {resp.status_code}"
        if resp.status_code == 401:
            msg = "OpenAI rechazó la key. Revisá OPENAI_API_KEY en el .env."
        elif resp.status_code == 429:
            # El 429 tapa dos cosas distintas: quedarse sin saldo y pegarle
            # demasiado rápido. El mensaje de OpenAI las distingue, el mío no.
            msg = msg or "OpenAI está limitando las consultas. Probá más tarde."
        return {"perfil": None, "tokens": _tokens_de(body), "error": str(msg)[:300]}

    # Un modelo puede cortarse por el techo de tokens antes de cerrar el JSON.
    if body.get("status") == "incomplete":
        motivo = (body.get("incomplete_details") or {}).get("reason") or "sin detalle"
        return {"perfil": None, "tokens": _tokens_de(body),
                "error": f"La respuesta quedó incompleta ({motivo})."}

    crudo = _texto_de(body)
    if not crudo:
        return {"perfil": None, "tokens": _tokens_de(body),
                "error": "OpenAI contestó en un formato que no se pudo leer.",
                "body": body}

    try:
        perfil = json.loads(crudo)
    except Exception:
        limpio = crudo.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        try:
            perfil = json.loads(limpio.strip())
        except Exception:
            return {"perfil": None, "tokens": _tokens_de(body),
                    "error": "OpenAI no devolvió JSON válido.", "body": crudo[:500]}

    if not isinstance(perfil, dict):
        return {"perfil": None, "tokens": _tokens_de(body),
                "error": "El JSON no es una ficha."}
    return {"perfil": _limpiar_personas(perfil), "tokens": _tokens_de(body),
            "error": None}
