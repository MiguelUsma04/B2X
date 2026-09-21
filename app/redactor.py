"""Escribe un correo distinto para cada empresa, con lo que se sabe de ella.

La plantilla con {{variables}} alcanza para un envío parejo. Esto es otra
cosa: la IA lee la ficha que ya se armó del negocio —qué vende, a quién, el
gancho que encontró en su sitio— y escribe un correo que solo tiene sentido
para ese destinatario.

Tres reglas que no se negocian, porque son las que separan un correo que se
lee de uno que se marca como spam:

**El nombre.** Si no se sabe cómo se llama la persona, no se la saluda por
nombre. Nunca se usa el nombre del negocio como si fuera una persona —
"Hola Agencia de Viajes 07" es la forma más rápida de que borren el correo— y
nunca se inventa uno a partir de un email como gerencia@ o info@.

**El idioma.** El del país de la empresa. Escribirle en español a una agencia
de Miami o en inglés a una de Bogotá se nota en la primera línea.

**Solo lo que el sitio dice.** Si la ficha no trae gancho, el correo se apoya
en el rubro y la ciudad. Inventar un dato sobre la empresa a la que se le
escribe es peor que mandar algo genérico.
"""
import json
import os

import httpx

from . import ai

# Cómo tiene que sonar. Son correos reales que funcionaron, no una
# descripción de estilo: un ejemplo enseña más que diez instrucciones.
EJEMPLOS = """
--- Ejemplo 1 (directo, sin vueltas)
Carlos, ¿cómo va el día?

Tenemos varias cosas en común: ambos somos agencias, pero hacemos cosas algo
distintas.

Ahora mismo tenemos una metodología propia para contactar de forma directa a
empresas a un coste ridículo por llamada. Te hablo de 10-15 euros por reunión
cualificada, en B2B, aunque suene imposible.

Me gustaría enseñarte cómo funciona el sistema. ¿Lo vemos esta semana?

--- Ejemplo 2 (abre con una pregunta incómoda)
Hey, Carlos:

Cuando armás las campañas que mejor rinden para tus clientes… ¿ya resolviste
cómo evitar que los resultados caigan a medida que desaparecen las cookies, o
por ahora lo aceptás y listo?

Ayudamos a agencias a identificar entre el 80 y el 90% de los visitantes
anónimos que llegan desde sus anuncios, y a armar audiencias de retargeting
que no dependen de una cookie.

Identificamos tus próximos 500 visitantes por nuestra cuenta, y después me
decís si les sirve a tus clientes. ¿Te parece?

--- Ejemplo 3 (menciona algo concreto de la empresa)
Hola Carlos:

Estuve mirando lo que hacen en Gmarketing y pensé que valía la pena conversar
sobre cómo conseguir clientes B2B de forma sostenida: entre ocho y diez
potenciales por mes de tu audiencia objetivo.

Probablemente esto parezca un correo más que no vale la pena. Lo que nos
separa del resto es que mostramos clientes que ya pasaron por el proceso.

¿Te parecería mala idea comentarlo en los próximos días?

Si no suma, avisame y freno el seguimiento.

--- Ejemplo 4 (muy corto, con una oferta clara)
Hola Carlos:

Vi lo que están construyendo en Kommo en Español.

¿Querés más llamadas de venta en tu calendario sin contratar vendedores?

Instalamos sistemas que se encargan de la prospección y agendan las llamadas
solas. El último dejó 24 llamadas agendadas en 30 días, y tarda 14 días en
quedar andando.

¿Te mando un video de tres minutos explicándolo?
""".strip()

PLANTILLA_INSTRUCCIONES = """
Escribís el primer correo en frío de una campaña B2B. Un correo por empresa,
escrito para esa empresa y para ninguna otra.

Cómo suena (mirá los ejemplos): frases cortas, una idea por párrafo, tono de
persona a persona. Nada de "esperamos que se encuentre bien", "en el marco
de", "soluciones integrales" ni mayúsculas de más.

ESTRUCTURA. El cuerpo son CUATRO frases, cada una en su propio párrafo, en
este orden. Las cuatro tienen que estar: si falta alguna, el correo no
funciona. Los nombres de los tramos son para vos y no se escriben en el
correo: nunca pongas "PROBLEMA —", "AGITACIÓN —" ni nada parecido como
encabezado. El lector tiene que leer cuatro frases seguidas, no un formulario.

  PROBLEMA — Una frase con algo que a ESA empresa le cuesta, deducido de su
  rubro y de lo que hace. Mejor si es una pregunta que le duele contestar.
  No un problema genérico del mundo: el de ellos.

  AGITACIÓN — Una frase corta sobre lo que pasa si eso sigue igual. Sin
  dramatizar y sin miedo inventado.

  SOLUCIÓN — Una frase con lo que hacemos contra ese problema. Nada de
  catálogo de servicios.

  CIERRE — UNA pregunta corta y fácil de contestar.

El saludo va aparte y no cuenta como tramo. El lector no tiene que darse
cuenta de que hay una estructura: tiene que leerse como alguien que escribe
porque notó algo.

Después, en los campos problema, agitacion y solucion, copiá la frase del
cuerpo que hace cada papel. No las inventes de nuevo: tienen que ser las
mismas que escribiste.

Reglas que no se rompen:

1. NOMBRE. Si te paso el nombre de la persona, saludala por su nombre de pila.
   Si NO te lo paso, NO saludes por nombre: arrancá directo o con un "Hola,".
   Nunca uses el nombre de la empresa como si fuera una persona. Nunca
   inventes un nombre a partir de un email.

2. IDIOMA. Escribí en el idioma que te indiquen, entero: asunto y cuerpo.

3. SOLO LO QUE SE SABE. Usá el gancho, el rubro, la ciudad y el resumen que te
   paso. Si no hay gancho, apoyate en el rubro y la ciudad. NO inventes datos
   sobre la empresa: ni cifras, ni clientes, ni premios, ni años en el mercado
   que no te haya pasado.

4. LARGO. Apuntá a {objetivo} caracteres en el cuerpo entero, saludo y cierre
   incluidos. El techo duro es {tope} y no se pasa. Eso es una pantalla de
   celular sin scroll: cuatro líneas cortas. Contá los caracteres antes de
   responder y, si te pasaste, sacá adjetivos y subordinadas, no ideas: los
   cuatro tramos tienen que quedar igual. Un correo en frío que se lee entero
   sin scroll se contesta más.
   El asunto, menos de 55 caracteres, sin la palabra que suena a publicidad.

5. NADA DE PROMESAS QUE NO PODÉS SOSTENER: nada de porcentajes, garantías ni
   precios, salvo que te los pasen explícitamente.

No firmes el correo: la firma se agrega después.
""".strip()

def largo_maximo() -> int:
    """Cuántos caracteres puede tener el cuerpo del correo."""
    v = (os.getenv("CORREO_MAX_CARACTERES") or "300").strip()
    try:
        return max(120, min(1200, int(v)))
    except ValueError:
        return 300


def instrucciones() -> str:
    # Se le pide menos que el techo: un modelo al que se le da el máximo
    # apunta al máximo y se pasa. Con un objetivo más abajo, aterriza cerca.
    tope = largo_maximo()
    return PLANTILLA_INSTRUCCIONES.format(tope=tope, objetivo=max(120, tope - 60))


ESQUEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["asunto", "cuerpo", "problema", "agitacion", "solucion",
                 "usó_nombre", "idioma"],
    "properties": {
        "asunto": {"type": "string",
                   "description": "Menos de 60 caracteres, sin sonar a publicidad."},
        "cuerpo": {"type": "string",
                   "description": "El correo entero, en texto plano, con "
                                  "renglones en blanco entre párrafos. Incluye "
                                  "el saludo, los tres tramos y el cierre. "
                                  "Sin firma."},
        "problema": {"type": "string",
                     "description": "La frase del cuerpo que plantea el "
                                    "problema, copiada tal cual."},
        "agitacion": {"type": "string",
                      "description": "La frase del cuerpo que dice qué pasa "
                                     "si sigue igual, copiada tal cual."},
        "solucion": {"type": "string",
                     "description": "La frase del cuerpo que dice qué "
                                    "hacemos, copiada tal cual."},
        "usó_nombre": {"type": "boolean",
                       "description": "Si se saludó a la persona por su nombre."},
        "idioma": {"type": "string",
                   "description": "Código del idioma en que quedó escrito: es, "
                                  "en, pt, fr, it, de."},
    },
}

# El idioma que se habla donde está la empresa. No es la lista del mundo: es
# la de los países donde se prospecta, y se amplía cuando haga falta.
IDIOMAS = {
    "CO": "es", "MX": "es", "AR": "es", "CL": "es", "PE": "es", "EC": "es",
    "BO": "es", "UY": "es", "PY": "es", "VE": "es", "CR": "es", "PA": "es",
    "GT": "es", "HN": "es", "NI": "es", "SV": "es", "DO": "es", "CU": "es",
    "PR": "es", "ES": "es",
    "BR": "pt", "PT": "pt",
    "US": "en", "GB": "en", "CA": "en", "AU": "en", "IE": "en",
    "FR": "fr", "IT": "it", "DE": "de", "AT": "de", "CH": "de",
}
NOMBRE_IDIOMA = {"es": "español", "en": "inglés", "pt": "portugués",
                 "fr": "francés", "it": "italiano", "de": "alemán"}


# Palabras que solo aparecen seguido en un idioma. No es un detector de
# verdad: alcanza para saber si el modelo escribió en el idioma que se le
# pidió, que es lo único que hay que decidir acá.
_MARCAS = {
    "es": (" que ", " para ", " con ", " los ", " una ", " está ", " cómo ",
           " hola", " gracias", " tu ", " te "),
    "en": (" the ", " your ", " and ", " with ", " for ", " you ", " we ",
           " that ", " hi ", " hey "),
    "pt": (" você ", " para ", " com ", " uma ", " não ", " está ", " olá",
           " obrigado", " seu "),
    "fr": (" vous ", " pour ", " avec ", " votre ", " nous ", " des ",
           " bonjour", " merci "),
    "it": (" che ", " per ", " con ", " una ", " tuo ", " sono ", " ciao",
           " grazie "),
    "de": (" und ", " für ", " mit ", " die ", " der ", " ihre ", " hallo",
           " danke "),
}


def parece_idioma(texto: str, idioma: str) -> bool:
    """Si el texto está escrito en el idioma que se pidió.

    El modelo dice en qué idioma escribió, pero no siempre acierta: con los
    ejemplos de estilo en español, alguna vez devuelve 'en' y escribe en
    español. Por eso se mira el texto y no lo que dice de sí mismo.
    """
    t = " " + (texto or "").lower().replace("\n", " ") + " "
    puntajes = {k: sum(t.count(m) for m in marcas) for k, marcas in _MARCAS.items()}
    if not any(puntajes.values()):
        return True                      # texto muy corto: no se puede afirmar
    gana = max(puntajes, key=puntajes.get)
    return gana == idioma


def idioma_de(contacto: dict) -> str:
    """En qué idioma escribirle a esta empresa."""
    forzado = (os.getenv("IDIOMA_FORZADO") or "").strip().lower()
    if forzado in NOMBRE_IDIOMA:
        return forzado

    # Lo que diga el sitio manda sobre el país: una agencia en Miami que
    # atiende en español es cliente de un correo en español.
    perfil = _perfil(contacto)
    del_sitio = (perfil.get("idioma") or "").strip().lower()[:2]
    if del_sitio in NOMBRE_IDIOMA:
        return del_sitio

    pais = (contacto.get("country") or "").strip().upper()
    if pais in IDIOMAS:
        return IDIOMAS[pais]

    from . import telefonos
    return IDIOMAS.get(telefonos.region_de_direccion(contacto.get("address")),
                       (os.getenv("IDIOMA_POR_DEFECTO") or "es").strip().lower())


_ROTULOS = ("problema", "agitación", "agitacion", "solución", "solucion",
            "cierre")


def limpiar(cuerpo: str) -> str:
    """Saca los rótulos de la estructura si el modelo los escribió.

    Se le pide que no los ponga, pero a veces los pone igual, y un correo que
    dice "PROBLEMA —" arriba no lo manda nadie. Acá se sacan sin tocar el
    texto que sigue.
    """
    salida = []
    for linea in (cuerpo or "").splitlines():
        limpia = linea.lstrip()
        for r in _ROTULOS:
            for sep in ("—", "-", ":"):
                marca = f"{r} {sep}"
                if limpia.lower().startswith(marca):
                    limpia = limpia[len(marca):].lstrip()
                    break
                if limpia.lower().startswith(f"{r}{sep}"):
                    limpia = limpia[len(r) + len(sep):].lstrip()
                    break
        # Una línea que sea SOLO el rótulo se va entera.
        if limpia.strip().lower().strip("—-: ") in _ROTULOS:
            continue
        salida.append(limpia if limpia != linea.lstrip() else linea)
    return "\n".join(salida).strip()


def _perfil(contacto: dict) -> dict:
    if not contacto.get("ai_profile"):
        return {}
    try:
        return json.loads(contacto["ai_profile"]) or {}
    except (ValueError, TypeError):
        return {}


def nombre_de_persona(contacto: dict) -> str:
    """El nombre de pila de quien va a leer el correo, si se sabe.

    Se devuelve vacío cuando no se sabe, y eso es una respuesta: es preferible
    un correo sin nombre a uno que saluda a una empresa como si fuera una
    persona.
    """
    propio = (contacto.get("first_name") or "").strip()
    if propio:
        return propio.split()[0]

    # Lo que la IA encontró en el sitio. Ya viene filtrado de buzones y de
    # frases, pero se vuelve a mirar: acá el costo de equivocarse es alto.
    for p in (_perfil(contacto).get("personas") or []):
        nombre = (p.get("nombre") or "").strip()
        if not nombre or "@" in nombre or len(nombre) < 3:
            continue
        if nombre.lower() == (contacto.get("company_name") or "").strip().lower():
            continue
        return nombre.split()[0]
    return ""


def _ficha(contacto: dict, nombre: str, idioma: str) -> str:
    perfil = _perfil(contacto)
    ciudades = perfil.get("ciudades") or []
    lineas = [
        ("Empresa", contacto.get("company_name") or contacto.get("full_name")),
        ("Nombre de la persona", nombre or "NO SE SABE — no la saludes por nombre"),
        ("Rubro", contacto.get("category")),
        ("Ciudad", ciudades[0] if ciudades else None),
        ("Sitio", contacto.get("company_domain")),
        ("Qué hace", perfil.get("resumen") or contacto.get("ai_summary")),
        ("Qué vende", ", ".join(perfil.get("que_vende") or [])),
        ("A quién le vende", perfil.get("a_quien_le_vende")),
        ("Gancho del sitio", perfil.get("gancho")),
        ("Antigüedad", perfil.get("anios_en_el_mercado")),
    ]
    ficha = "\n".join(f"{k}: {v}" for k, v in lineas if v)
    return (f"{ficha}\n\n"
            f"Idioma del correo: {NOMBRE_IDIOMA.get(idioma, 'español')}")


def _pedido(contacto: dict, propuesta: str, nombre: str, idioma: str,
            insistir: bool = False, mas_corto: bool = False) -> dict:
    lengua = NOMBRE_IDIOMA.get(idioma, "español")
    # El idioma va primero y aparte. Los ejemplos de estilo están en español,
    # y sin esto el modelo escribe en español aunque se le pida otro idioma:
    # arrastra el idioma de lo que está leyendo.
    exigencia = (f"IDIOMA OBLIGATORIO: {lengua.upper()} ({idioma}).\n"
                 f"El asunto y el cuerpo van enteros en {lengua}. Los ejemplos "
                 f"de más abajo están en español, pero son ejemplos de TONO, "
                 f"no de idioma: no copies el idioma, copiá la forma.")
    if insistir:
        exigencia += (f"\n\nEl intento anterior salió en el idioma equivocado. "
                      f"Escribí en {lengua} y en ningún otro idioma.")
    if mas_corto:
        exigencia += (f"\n\nEl intento anterior salió demasiado largo. El cuerpo "
                      f"entero tiene que entrar en {largo_maximo()} caracteres, "
                      f"con los cuatro tramos igual.")
    return {
        "model": ai.modelo(),
        "input": [
            {"role": "system",
             "content": f"{exigencia}\n\n{instrucciones()}\n\n"
                        f"=== Cómo suenan nuestros correos ===\n{EJEMPLOS}"},
            {"role": "user",
             "content": (f"=== Qué ofrecemos nosotros ===\n{propuesta.strip()}\n\n"
                         f"=== A quién le escribimos ===\n"
                         f"{_ficha(contacto, nombre, idioma)}")},
        ],
        "max_output_tokens": 2_000,
        "reasoning": {"effort": ai.esfuerzo()},
        "text": {"format": {"type": "json_schema", "name": "correo",
                            "strict": True, "schema": ESQUEMA}},
    }


def propuesta_por_defecto() -> str:
    return (os.getenv("PROPUESTA_B2K") or
            "Somos gmarketing.co. Ayudamos a empresas a conseguir reuniones "
            "con clientes potenciales: armamos la lista, escribimos y hacemos "
            "el seguimiento, y el equipo comercial solo atiende a los que "
            "contestan.").strip()


async def escribir(client: httpx.AsyncClient, contacto: dict,
                   propuesta: str = "") -> dict:
    """Escribe el correo para este contacto.

    Devuelve {"asunto", "cuerpo", "nombre", "idioma", "tokens", "error"}.
    """
    if not ai.configured():
        return {"error": "Falta OPENAI_API_KEY en el .env."}

    nombre = nombre_de_persona(contacto)
    idioma = idioma_de(contacto)
    cabeceras = {"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '')}",
                 "Content-Type": "application/json"}
    texto_propuesta = propuesta or propuesta_por_defecto()

    async def pedir(insistir: bool = False, mas_corto: bool = False):
        pedido = _pedido(contacto, texto_propuesta, nombre, idioma, insistir,
                         mas_corto)
        r = await client.post(ai.URL, json=pedido, headers=cabeceras)
        body = r.json() if r.content else {}
        if r.status_code == 400:
            msg = ((body.get("error") or {}).get("message") or "").lower()
            if "effort" in msg or "reasoning" in msg:
                pedido.pop("reasoning", None)
                r = await client.post(ai.URL, json=pedido, headers=cabeceras)
                body = r.json() if r.content else {}
        return r, body

    try:
        r, body = await pedir(False)
    except Exception as exc:
        return {"error": f"No se pudo hablar con OpenAI: {type(exc).__name__}"}

    if r.status_code != 200:
        err = (body.get("error") or {}) if isinstance(body, dict) else {}
        msg = err.get("message") or f"HTTP {r.status_code}"
        if r.status_code == 401:
            msg = "OpenAI rechazó la key. Revisá OPENAI_API_KEY en el .env."
        elif r.status_code == 429:
            msg = "OpenAI está limitando las consultas. Probá en un rato."
        return {"error": msg[:200]}

    crudo = ai._texto_de(body)
    if not crudo:
        return {"error": "OpenAI no devolvió nada legible."}
    try:
        d = json.loads(crudo)
    except ValueError:
        return {"error": "OpenAI devolvió algo que no es un correo."}

    cuerpo = limpiar((d.get("cuerpo") or "").strip())
    asunto = (d.get("asunto") or "").strip()
    if not cuerpo or not asunto:
        return {"error": "El correo salió vacío."}

    # Si salió en otro idioma se pide una vez más. Mandar un correo en el
    # idioma equivocado es peor que no mandarlo: se nota en el primer renglón.
    if not parece_idioma(cuerpo, idioma):
        try:
            r2, body2 = await pedir(True)
            if r2.status_code == 200:
                crudo2 = ai._texto_de(body2)
                d2 = json.loads(crudo2) if crudo2 else {}
                if d2.get("cuerpo") and parece_idioma(d2["cuerpo"], idioma):
                    d, cuerpo = d2, d2["cuerpo"].strip()
                    asunto = (d2.get("asunto") or asunto).strip()
                    body = body2
        except Exception:
            pass
        if not parece_idioma(cuerpo, idioma):
            return {"error": f"La IA escribió en otro idioma, no en "
                             f"{NOMBRE_IDIOMA.get(idioma, idioma)}."}

    # El largo se mide acá: no se confía en que el modelo cuente caracteres.
    # Si se pasó por poco se deja —cortar un correo a la mitad es peor que dos
    # renglones de más—; si se pasó mucho, se pide de nuevo más corto.
    tope = largo_maximo()
    piso = max(120, int(tope * 0.45))

    def sirve(texto: str) -> bool:
        # Un saludo suelto no es un correo: tiene que tener cuerpo y cerrar
        # con algo. El modelo a veces devuelve una sola línea.
        return bool(texto) and len(texto) >= piso and parece_idioma(texto, idioma)

    if len(cuerpo) > tope * 1.15:
        try:
            r2, body2 = await pedir(mas_corto=True)
            if r2.status_code == 200:
                crudo2 = ai._texto_de(body2)
                d2 = json.loads(crudo2) if crudo2 else {}
                nuevo = limpiar((d2.get("cuerpo") or "").strip())
                # Se queda con el más corto de los dos que sirva: acortar no
                # puede significar devolver media frase.
                if sirve(nuevo) and len(nuevo) < len(cuerpo):
                    d, body, cuerpo = d2, body2, nuevo
                    asunto = (d2.get("asunto") or asunto).strip()
        except Exception:
            pass

    if len(cuerpo) < piso:
        return {"error": f"El correo salió demasiado corto ({len(cuerpo)} "
                         f"caracteres): no alcanza para decir nada."}

    # El asunto largo se corta en la última palabra entera: en el celular solo
    # se ven los primeros sesenta caracteres y el resto no existe.
    if len(asunto) > 70:
        corte = asunto[:70].rsplit(" ", 1)[0]
        asunto = (corte or asunto[:70]).rstrip(" ,;:-")

    # La última barrera: si no se sabía el nombre y el correo igual saluda con
    # el de la empresa, se corta. Es el error que más caro sale.
    empresa = (contacto.get("company_name") or contacto.get("full_name") or "").strip()
    if not nombre and empresa and cuerpo[:120].lower().startswith(
            ("hola " + empresa.lower(), "hey " + empresa.lower(),
             empresa.lower() + ",")):
        return {"error": "La IA saludó a la empresa como si fuera una persona."}

    return {"asunto": asunto[:200], "cuerpo": cuerpo, "nombre": nombre,
            "problema": (d.get("problema") or "").strip(),
            "agitacion": (d.get("agitacion") or "").strip(),
            "solucion": (d.get("solucion") or "").strip(),
            "idioma": d.get("idioma") or idioma,
            "uso_nombre": bool(d.get("usó_nombre")),
            "caracteres": len(cuerpo),
            "tokens": ai._tokens_de(body), "error": None}
