"""Lo que contestó el cliente, y qué quiso decir.

Hasta acá B2K sabía *que* alguien contestó, por la cabecera del correo, pero
no *qué* contestó: el cuerpo se descartaba. Así todas las respuestas valían lo
mismo, y un "no me escriban más" movía el lead igual que un "¿cuánto sale?".

Este módulo hace dos cosas:

1. Saca del correo lo que escribió la persona, sin el historial citado de
   abajo —que es nuestro propio correo repetido— y lo guarda.
2. Le pide a la IA que diga qué clase de respuesta es. La IA solo clasifica:
   no le contesta a nadie y no mueve nada. Qué etapa le toca a cada clase lo
   decide la configuración, no este módulo.
"""
import json
import os
import re

import httpx

from . import ai
from .db import get_db

# Cuánto se guarda de cada respuesta. Una persona que contesta un correo de
# prospección escribe unas líneas; lo que pasa de esto es una firma con
# avisos legales o un hilo que no se pudo cortar.
MAX_GUARDADO = 8_000
MAX_SALIDA = 600

# ============================ sacar el texto ============================

# Dónde empieza lo citado. Cada cliente de correo lo marca a su manera, y en
# español e inglés según cómo tenga configurada la cuenta la otra persona.
_CORTES = [
    # Gmail y Apple Mail: "El jue, 29 oct 2026 a las 10:00, Ana <a@b.co>
    # escribió:". Gmail parte la línea en dos cuando la dirección es larga.
    re.compile(r"(?im)^[ \t]*(?:el|on)\b[^\n]{0,200}(?:\n[^\n]{0,200})?"
               r"\b(?:escribió|wrote|a écrit)\s*:[ \t]*$"),
    re.compile(r"(?im)^[ \t]*-{2,}\s*(?:mensaje original|original message)\s*-{2,}"),
    # Outlook no dice "escribió": repite la cabecera del correo citado.
    re.compile(r"(?im)^[ \t]*\**(?:de|from)\s*:\**[^\n]*\n(?:[^\n]*\n){0,3}?"
               r"[ \t]*\**(?:enviado|sent|fecha|date)\s*:"),
    re.compile(r"(?m)^[ \t]*_{10,}[ \t]*$"),
]


def _partes_de_texto(msg):
    """El texto plano del correo y, si no hay, el HTML."""
    plano = html = None
    for parte in msg.walk() if msg.is_multipart() else [msg]:
        if parte.is_multipart():
            continue
        if (parte.get("Content-Disposition") or "").lower().startswith("attachment"):
            continue
        tipo = (parte.get_content_type() or "").lower()
        if tipo not in ("text/plain", "text/html"):
            continue
        crudo = parte.get_payload(decode=True) or b""
        texto = crudo.decode(parte.get_content_charset() or "utf-8", "replace")
        if tipo == "text/plain" and plano is None:
            plano = texto
        elif tipo == "text/html" and html is None:
            html = texto
    return plano, html


def sin_lo_citado(texto: str) -> str:
    """Corta el texto donde empieza el correo citado."""
    texto = (texto or "").replace("\r\n", "\n").replace("\r", "\n")
    corte = len(texto)
    for patron in _CORTES:
        m = patron.search(texto)
        if m:
            corte = min(corte, m.start())
    lineas = [l for l in texto[:corte].split("\n") if not l.lstrip().startswith(">")]
    limpio = "\n".join(lineas).strip()
    return re.sub(r"\n{3,}", "\n\n", limpio)


def texto_de_respuesta(msg) -> str:
    """Lo que escribió la persona, listo para guardar y para la IA."""
    from .mailer import html_a_texto
    plano, html = _partes_de_texto(msg)
    texto = plano if (plano or "").strip() else html_a_texto(html or "")
    return sin_lo_citado(texto)[:MAX_GUARDADO]


def guardar(conn, event_id: int, contact_id: int | None, texto: str) -> None:
    """Anota el texto de una respuesta. Sin clasificar todavía."""
    conn.execute(
        "INSERT OR IGNORE INTO respuestas (event_id, contact_id, texto) "
        "VALUES (?,?,?)", (event_id, contact_id, texto or ""))

# ============================== clasificar ==============================

# Qué puede ser una respuesta. Proponer y confirmar una reunión van separados
# a propósito: la etapa de "agendada" cierra el lead como ganado, y un "¿el
# martes a las 3?" todavía no es una reunión.
CLASES = {
    "interesado": "Le interesa, pregunta precio, pide más información o "
                  "que lo llamen.",
    "propone_reunion": "Propone un día u hora para hablar, o pide agendar, "
                       "pero todavía no hay nada confirmado.",
    "confirma_reunion": "Confirma una reunión con día y hora ya acordados.",
    "no_interesado": "No le interesa, ya tiene proveedor, no es el momento, "
                     "'no gracias'.",
    "no_escribir": "Pide explícitamente que no le escriban más, que lo saquen "
                   "de la lista, o se queja del correo.",
    "otra_persona": "No es la persona indicada, ya no trabaja ahí, o deriva a "
                    "otra persona o dirección.",
    "no_entiende": "Cualquier otra cosa, o no se puede saber qué quiere.",
}

CONFIANZAS = ("alta", "media", "baja")

ESQUEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "clase": {"type": "string", "enum": list(CLASES),
                  "description": "Qué clase de respuesta es."},
        "confianza": {"type": "string", "enum": list(CONFIANZAS),
                      "description": "Qué tan seguro estás de la clase."},
        "porque": {"type": "string",
                   "description": "Una frase, en español, que diga por qué."},
    },
    "required": ["clase", "confianza", "porque"],
}

INSTRUCCIONES = (
    "Clasificás la respuesta de una empresa a un correo de prospección "
    "comercial de una agencia de marketing. Leé solo lo que escribió la "
    "persona y elegí una clase:\n"
    + "\n".join(f"- {k}: {v}" for k, v in CLASES.items())
    + "\n\nReglas:\n"
    "- Ante la duda entre dos clases, bajá la confianza. Una respuesta mal "
    "leída como 'no_interesado' pierde un cliente.\n"
    "- Una pregunta, aunque sea escéptica ('¿y eso cuánto cuesta?'), es "
    "'interesado'.\n"
    "- Proponer un horario no es confirmar: 'confirma_reunion' es solo cuando "
    "acepta un día y una hora concretos.\n"
    "- Si pide no recibir más correos, es 'no_escribir' aunque además diga "
    "que no le interesa."
)

# Por debajo de esto, la clase no se usa para mover nada: se manda a revisar.
CONFIANZA_MINIMA = "media"


def es_confiable(confianza: str) -> bool:
    orden = {c: i for i, c in enumerate(reversed(CONFIANZAS))}
    return orden.get(confianza, -1) >= orden[CONFIANZA_MINIMA]


def _pedido(texto: str, asunto: str = "") -> dict:
    return {
        "model": ai.modelo(),
        "input": [
            {"role": "system", "content": INSTRUCCIONES},
            {"role": "user", "content": (f"Asunto: {asunto}\n\n" if asunto else "")
                                        + f"Respuesta:\n{texto[:MAX_GUARDADO]}"},
        ],
        "max_output_tokens": MAX_SALIDA,
        "reasoning": {"effort": ai.esfuerzo()},
        "text": {"format": {"type": "json_schema", "name": "clase_respuesta",
                            "strict": True, "schema": ESQUEMA}},
    }


async def clasificar(client: httpx.AsyncClient, texto: str,
                     asunto: str = "") -> dict:
    """Devuelve {"clase", "confianza", "porque", "error"}.

    Si algo falla, la clase es 'no_entiende' con confianza baja: el error
    termina en la lista de revisar, nunca en un movimiento de etapa.
    """
    dudosa = {"clase": "no_entiende", "confianza": "baja"}
    if not ai.configured():
        return {**dudosa, "porque": "", "error": "Falta OPENAI_API_KEY en el .env."}
    if not (texto or "").strip():
        return {**dudosa, "porque": "La respuesta llegó vacía.", "error": None}

    cabeceras = {"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '')}",
                 "Content-Type": "application/json"}
    pedido = _pedido(texto, asunto)
    try:
        r = await client.post(ai.URL, json=pedido, headers=cabeceras)
        if r.status_code == 400 and "reasoning" in r.text.lower():
            pedido.pop("reasoning", None)
            r = await client.post(ai.URL, json=pedido, headers=cabeceras)
        body = r.json() if r.content else {}
    except Exception as exc:
        return {**dudosa, "porque": "",
                "error": f"No se pudo hablar con OpenAI: {type(exc).__name__}"}

    if r.status_code != 200:
        msg = ((body.get("error") or {}).get("message") if isinstance(body, dict)
               else None) or f"HTTP {r.status_code}"
        return {**dudosa, "porque": "", "error": str(msg)[:300]}

    try:
        datos = json.loads(ai._texto_de(body) or "")
    except ValueError:
        return {**dudosa, "porque": "", "error": "OpenAI no devolvió JSON válido."}
    if datos.get("clase") not in CLASES or datos.get("confianza") not in CONFIANZAS:
        return {**dudosa, "porque": "", "error": "OpenAI devolvió una clase que no existe."}
    return {"clase": datos["clase"], "confianza": datos["confianza"],
            "porque": (datos.get("porque") or "")[:300], "error": None}


async def clasificar_pendientes(limite: int = 20) -> dict:
    """Clasifica las respuestas guardadas que todavía no tienen clase."""
    with get_db() as conn:
        filas = [dict(r) for r in conn.execute(
            """SELECT r.id, r.texto, q.subject
                 FROM respuestas r
                 JOIN email_events e ON e.id = r.event_id
                 LEFT JOIN email_queue q ON q.id = e.queue_id
                WHERE r.clase IS NULL
                ORDER BY r.id LIMIT ?""", (limite,))]
    hechas = errores = 0
    async with httpx.AsyncClient(timeout=60) as client:
        for f in filas:
            res = await clasificar(client, f["texto"], f.get("subject") or "")
            if res["error"]:
                # Un error de OpenAI no se anota como clase: se reintenta en
                # la vuelta siguiente en vez de quedar como "no entiende".
                errores += 1
                continue
            with get_db() as conn:
                conn.execute(
                    "UPDATE respuestas SET clase=?, confianza=?, porque=?, "
                    "clasificada=datetime('now') WHERE id=?",
                    (res["clase"], res["confianza"], res["porque"], f["id"]))
            hechas += 1
    return {"clasificadas": hechas, "errores": errores, "mirados": len(filas)}
