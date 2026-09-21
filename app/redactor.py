"""Escribe un correo distinto para cada empresa, con lo que se sabe de ella.

La plantilla con {{variables}} alcanza para un envío parejo. Esto es otra
cosa: la IA lee la ficha que ya se armó del negocio —qué vende, a quién, el
gancho que encontró en su sitio— y escribe un correo que solo tiene sentido
para ese destinatario.

Tres reglas que no se negocian, porque son las que separan un correo que se
lee de uno que se marca como spam:

**El nombre.** Se busca en el sitio a quién corresponde la dirección a la que
se escribe: si publican "gerencia@" y nombran a su gerente, ese es. Si no se
puede atar un nombre a esa dirección, se saluda a la empresa entera —"Hola
señores de ..."— en vez de arriesgar el nombre equivocado, y nunca se inventa
uno a partir del email.

**El idioma.** El del país de la empresa. Escribirle en español a una agencia
de Miami o en inglés a una de Bogotá se nota en la primera línea.

**Solo lo que el sitio dice.** Si la ficha no trae gancho, el correo se apoya
en el rubro y la ciudad. Inventar un dato sobre la empresa a la que se le
escribe es peor que mandar algo genérico.
"""
import asyncio
import json
import os
from dataclasses import dataclass

import httpx

from . import ai
from .db import get_db

# Cómo tiene que sonar. Son correos reales que funcionaron, no una
# descripción de estilo: un ejemplo enseña más que diez instrucciones.
#
# Dos cosas que no se copian de acá: el idioma —están todos en español y el
# correo sale en el de la empresa— y el largo, que en varios pasa del tope.
# Lo que sí se copia es el tono y el orden de los cuatro tramos.
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

--- Ejemplo 5 (problema, agitación, solución y una pregunta que incomoda)
Asunto: ¿Sigues vendiendo como en 2019?

Hola, Carlos. El turismo sigue creciendo, pero vender viajes cambió. Hoy los
clientes cotizan por WhatsApp, comparan varias agencias y esperan respuestas
rápidas.

Si el seguimiento depende del asesor, los leads se enfrían y ventas que costó
dinero generar terminan en otra agencia.

CRM, automatización y agentes de IA ya permiten operar distinto.

¿Sigues vendiendo como en 2019 o eres una de las agencias que ya están
completamente digitalizadas y automatizadas con IA?
""".strip()

PLANTILLA_INSTRUCCIONES = """
Actuás como estratega senior de outbound B2B, copywriter de respuesta directa
y especialista en venta consultiva de CRM, WhatsApp, automatización comercial
e inteligencia artificial.

Tu única misión es escribir correos de prospección en frío para KOMMO10X,
dirigidos a dueños, gerentes, directores comerciales y directores de marketing
de AGENCIAS DE VIAJES.

=== QUÉ VENDEMOS ===
KOMMO10X no vende un CRM. Ayudamos a empresas con equipos comerciales a
convertir una operación basada en WhatsApp, seguimiento manual y esfuerzo
individual de cada asesor en un sistema comercial organizado, medible y
automatizado: Kommo CRM + WhatsApp + automatización + agentes de IA +
procesos + seguimiento + analítica.
En el primer correo NO se venden funcionalidades. Se vende la conversación
alrededor del problema que esa tecnología resuelve.

=== LA REALIDAD DE UNA AGENCIA DE VIAJES ===
Una agencia invierte plata para generar interesados en destinos, paquetes,
tiquetes, hoteles, planes familiares o lunas de miel. Casi todos terminan
hablando con un asesor por WhatsApp. El problema comercial aparece cuando:
entran más consultas de las que los asesores alcanzan a responder rápido;
alguien pide una cotización y después nadie hace el seguimiento; los
interesados quedan repartidos entre varios WhatsApp; el seguimiento depende
de que cada asesor se acuerde; se paga publicidad para conseguir interesados
que después se enfrían; el cliente está comparando varias agencias al mismo
tiempo; hay bases de viajeros anteriores que podrían volver a comprar y no se
trabajan; el director no sabe cuántos entraron, cuántos se atendieron, se
cotizaron y se vendieron; se quiere crecer en volumen pero la operación sigue
dependiendo del trabajo manual.

OJO: eso son dolores POSIBLES del rubro, no hechos sobre esta empresa. Nunca
afirmes que a esta agencia le pasa, salvo que la ficha lo diga. Se escribe
"Cuando…", "Muchas agencias…", "Si…", "Uno de los retos al crecer es…".
Nunca "Vi que ustedes están perdiendo clientes…".

=== LA IDEA CENTRAL ===
"Vender viajes hoy no es como vender viajes en 2019." Cambió el comprador y
cambiaron las herramientas. El contraste es entre
   lead → WhatsApp → asesor → cotización → seguimiento a pulmón
y
   lead → CRM → WhatsApp → automatización/IA → asesor → seguimiento
   estructurado → medición → reactivación.
La tecnología no reemplaza al vendedor: es la infraestructura para que el
equipo comercial maneje mejor sus oportunidades.

=== EL OBJETIVO ===
No cerrar una implementación, no explicar todo KOMMO10X, ni siquiera sacar
una reunión. El objetivo es CONSEGUIR UNA RESPUESTA: que el que lee tenga la
curiosidad suficiente para contestar.

=== ESTRUCTURA OBLIGATORIA: PAS + CTA ===
PROBLEMA. Abrí con una situación que un director de agencia reconozca.
AGITACIÓN. Mostrá la consecuencia. Sin exagerar, sin manipular y sin inventar
   pérdidas de plata.
SOLUCIÓN. Primero el resultado —organización, velocidad, seguimiento
   estructurado, reactivación, visibilidad, poder crecer—; la tecnología
   después y solo si hace falta. Nunca una lista de funcionalidades.
CTA. Una sola pregunta al final, que busque un microcompromiso.

Después, en los campos problema, agitacion y solucion, copiá la frase del
cuerpo que hace cada papel. No las inventes de nuevo: tienen que ser las
mismas que escribiste.

=== EL ÁNGULO DE ESTE CORREO ===
El tema de este correo es: {angulo}.
Trabajá ese tema y ninguno más. Un correo, una idea.

Reglas que no se rompen:

1. SALUDO. En la ficha te paso, siempre, el saludo con el que arranca el
   correo. Usá ese y ninguno otro, tal cual, en el primer renglón.
   Cuando se sabe quién lee, es el nombre de pila de esa persona. Cuando no
   se sabe, es un saludo a la empresa entera: tampoco ahí inventes un nombre
   ni trates a la empresa como si fuera una persona.

2. IDIOMA. Escribí en el idioma que te indiquen, entero: asunto y cuerpo.

3. SOLO LO QUE SE SABE, PERO ÚSALO. Si en la ficha viene un DATO PROPIO DE
   ESTA EMPRESA, el correo tiene que mencionarlo de forma natural: en la
   primera frase o en el cierre, con las palabras justas. Es lo único que
   diferencia este correo de una plantilla, y el que lo lee lo nota.
   Mencionarlo no es adularlos ni describirles su propio negocio: es el punto
   de apoyo para plantear el problema.
   Si no viene ningún dato propio, personalizá por el contexto del rubro y la
   ciudad. NUNCA des a entender que investigaste algo que no sabés, y nunca
   inventes cifras, clientes, casos, premios ni años en el mercado.

4. LARGO. El cuerpo entero —saludo y cierre incluidos— va entre {piso} y
   {tope} caracteres, y NO SE PASA DE {tope} NI POR UNO. Eso son más o menos
   entre {palabras_min} y {palabras} palabras: contá palabras, que es más
   fácil que contar letras. El asunto no cuenta.
   El asunto: máximo 6 palabras.

5. ESTILO. Escribí como un empresario hablándole a otro empresario: directo,
   ejecutivo, humano, seguro, curioso, consultivo. Que NO suene corporativo,
   robótico, desesperado, exageradamente vendedor, ni a plantilla masiva, ni
   a texto hecho por una IA.
   Nada de "Espero que estés muy bien". No te presentes en las primeras
   líneas. Nada de "revolucionario", "increíble", "potenciá tu negocio" ni
   "llevá tu empresa al siguiente nivel".

6. NADA DE PROMESAS QUE NO PODÉS SOSTENER: ni porcentajes, ni garantías, ni
   precios, ni cantidad de casos, salvo que te los pasen explícitamente.

7. ESCRIBÍ VOS. Nada de este texto se copia literal en el correo. Todo lo
   de arriba es para que entiendas el tema y el tono: las frases del correo
   las escribís vos, con tus palabras. Si una oración tuya aparece igual acá
   arriba, cambiala.
   Y el cuerpo va en frases completas y terminadas: nada de arrancar con
   "Cuando entran más consultas…" y cortar ahí, sin decir qué pasa.

8. NADA DE MARCADORES. El cuerpo es el correo final, listo para mandar. No
   pongas corchetes, ni [NOMBRE], ni "PROBLEMA:", ni "(insertar aquí)", ni
   nada para completar después.

Antes de entregarlo, preguntate: ¿un director de agencia de viajes leería
esto y pensaría que entendemos cómo funciona comercialmente su industria? Si
la respuesta es no, reescribilo.

No firmes el correo: la firma se agrega después.
""".strip()

# Los cinco ángulos del brief. Cada correo trabaja uno solo, y se reparten
# entre los contactos: si todos salieran con el mismo ángulo, una tanda de
# cien correos sería cien veces el mismo correo con otro nombre.
# Son etiquetas de TEMA, no frases para copiar: escritas como oraciones, el
# modelo las pegaba tal cual en el cuerpo del correo.
ANGULOS = [
    "cómo cambió la venta de viajes desde 2019",
    "la publicidad que trae interesados que después se enfrían",
    "la velocidad de respuesta por WhatsApp y las cotizaciones sin contestar",
    "lo que el director no ve de su propio embudo",
    "la automatización y la IA como evolución del equipo comercial",
]


def angulo_de(contacto: dict) -> str:
    """Qué ángulo le toca a este contacto.

    Va por el id y no al azar para que volver a pedir el mismo correo no
    cambie de tema cada vez, y para que dos contactos seguidos de la misma
    tanda no salgan iguales.
    """
    try:
        return ANGULOS[int(contacto.get("id") or 0) % len(ANGULOS)]
    except (TypeError, ValueError):
        return ANGULOS[0]


def largo_maximo() -> int:
    """El techo del cuerpo del correo. Del brief: nunca más de 500."""
    v = (os.getenv("CORREO_MAX_CARACTERES") or "500").strip()
    try:
        return max(150, min(1200, int(v)))
    except ValueError:
        return 500


def largo_minimo() -> int:
    """El piso. Del brief: entre 350 y 500.

    Un correo de 180 caracteres no tiene lugar para los cuatro tramos: sale
    un problema y un saludo, y eso no es lo que se pidió.
    """
    v = (os.getenv("CORREO_MIN_CARACTERES") or "350").strip()
    tope = largo_maximo()
    try:
        piso = int(v)
    except ValueError:
        piso = 350
    return max(80, min(piso, int(tope * 0.85)))


def instrucciones(contacto: dict | None = None) -> str:
    tope, piso = largo_maximo(), largo_minimo()
    return PLANTILLA_INSTRUCCIONES.format(
        tope=tope, piso=piso,
        palabras=max(20, int(tope / 6.2)),
        palabras_min=max(12, int(piso / 6.2)),
        angulo=angulo_de(contacto or {}))


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
        # Y también la que sea solo un marcador sin completar, tipo
        # "[PROBLEMA: copiar la frase...]".
        nuda = limpia.strip()
        if nuda.startswith("[") and nuda.endswith("]"):
            continue
        salida.append(limpia if limpia != linea.lstrip() else linea)
    return "\n".join(salida).strip()


# Pedazos del brief que el modelo, cuando se cansa, pega tal cual dentro del
# correo. Pedirle que no lo haga ayuda pero no alcanza: esto se mide.
_DEL_BRIEF = (
    "la tecnología no reemplaza al vendedor",
    "es la infraestructura para que el equipo comercial",
    "organización, velocidad, seguimiento estructurado, reactivación",
    "la tecnología después y solo si hace falta",
    "entran más consultas de las que los asesores alcanzan a responder",
    "el seguimiento depende de que cada asesor se acuerde",
    "la plata que se pone en publicidad para conseguir interesados",
    "sistema comercial organizado, medible y automatizado",
    "un correo, una idea",
    "microcompromiso",
    "el objetivo es conseguir una respuesta",
    "copiá la frase del cuerpo",
    "director comercial hablándole a otro",
)


def repite_el_brief(cuerpo: str) -> str:
    """Qué frase de las instrucciones se coló en el correo, si es que alguna."""
    t = _normal(cuerpo).replace("\n", " ")
    while "  " in t:
        t = t.replace("  ", " ")
    for frase in _DEL_BRIEF:
        if _normal(frase) in t:
            return frase
    return ""


def tiene_marcadores(cuerpo: str) -> str:
    """Corchetes o rótulos sin completar. Nunca pueden salir."""
    import re
    m = re.search(r"\[[^\]]{2,80}\]|\{\{?[A-ZÁÉÍÓÚÑ_ ]{3,40}\}?\}"
                  r"|\(insertar[^)]*\)", cuerpo or "")
    return m.group(0) if m else ""


def _perfil(contacto: dict) -> dict:
    if not contacto.get("ai_profile"):
        return {}
    try:
        return json.loads(contacto["ai_profile"]) or {}
    except (ValueError, TypeError):
        return {}


# Qué cargo sugiere cada buzón de área. Es lo que permite atar un
# "gerencia@" con la persona que el sitio nombra como gerente.
_BUZON_A_CARGO = {
    "gerencia": ("gerent", "general manager", "ceo", "direct"),
    "gerente": ("gerent", "ceo", "direct"),
    "direccion": ("direct", "ceo", "gerent"),
    "director": ("direct", "ceo"),
    "ceo": ("ceo", "direct", "gerent", "founder", "fundador"),
    "ventas": ("vent", "comercial", "sales"),
    "comercial": ("comercial", "vent", "sales"),
    "sales": ("sales", "vent", "comercial"),
    "marketing": ("marketing", "mercadeo"),
    "administracion": ("administra", "contab"),
    "reservas": ("reserva", "booking"),
}

# Buzones que no son de nadie en particular: por más que el sitio nombre a
# alguien, no hay forma de saber que lo lee esa persona.
_BUZON_DE_NADIE = {"info", "contacto", "contact", "hola", "hello", "mail",
                   "correo", "soporte", "support", "ayuda", "help",
                   "administrador", "webmaster", "no-reply", "noreply"}


def _normal(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def persona_del_correo(contacto: dict) -> dict:
    """Quién es, de los nombrados en el sitio, el que lee ESE correo.

    No alcanza con que el sitio nombre a alguien: hay que poder atarlo a la
    dirección a la que se escribe. Se mira, en orden:

    1. Que el sitio publique esa misma dirección junto a un nombre.
    2. Que la dirección esté armada con el nombre (juan.perez@, jperez@).
    3. Que la dirección sea un buzón de área —gerencia@, ventas@— y el sitio
       nombre a alguien con ese cargo.

    Si nada de eso se cumple, se devuelve vacío. Saludar por nombre al que
    no es peor que no saludar por nombre.
    """
    correo = (contacto.get("email") or "").strip().lower()
    personas = [p for p in (_perfil(contacto).get("personas") or [])
                if (p.get("nombre") or "").strip()]
    if not personas:
        return {}

    empresa = _normal(contacto.get("company_name") or "")
    personas = [p for p in personas
                if _normal(p["nombre"]) != empresa and len(p["nombre"]) >= 3]
    if not personas:
        return {}

    if not correo or "@" not in correo:
        return {}
    local = _normal(correo.split("@")[0])

    # 1. El sitio publica esa dirección al lado del nombre.
    for p in personas:
        if (p.get("email") or "").strip().lower() == correo:
            return {**p, "como": "el sitio publica ese correo junto al nombre"}

    # 2. La dirección está hecha con el nombre de la persona.
    solo_letras = "".join(c for c in local if c.isalpha())
    for p in personas:
        partes = [_normal(x) for x in p["nombre"].split() if len(x) > 2]
        if not partes or not solo_letras:
            continue
        armados = {"".join(partes)}
        # Con nombres y apellidos compuestos —"Juan Pablo Acosta Cuero"— no se
        # sabe dónde termina el nombre: se prueban todos los cortes, contra
        # cada apellido posible. jacosta@, jpacosta@, juan.acosta@, jcuero@.
        for corte in range(1, len(partes)):
            pila = partes[:corte]
            iniciales = "".join(x[0] for x in pila)
            junto = "".join(pila)
            for apellido in partes[corte:]:
                armados |= {junto + apellido, iniciales + apellido,
                            junto + "." + apellido, apellido, junto}
        if solo_letras in armados:
            return {**p, "como": "la dirección está armada con su nombre"}

    # 3. Es un buzón de área y alguien del sitio tiene ese cargo.
    if local in _BUZON_DE_NADIE:
        return {}
    pistas = _BUZON_A_CARGO.get(local)
    if pistas:
        for p in personas:
            cargo = _normal(p.get("cargo") or "")
            if cargo and any(pista in cargo for pista in pistas):
                return {**p, "como": f"el sitio nombra a su {p.get('cargo')}"}
    return {}


def nombre_de_persona(contacto: dict) -> str:
    """El nombre de pila de quien va a leer el correo, si se sabe.

    Se devuelve vacío cuando no se sabe, y eso es una respuesta: es preferible
    saludar a la empresa que saludar por nombre a la persona equivocada.
    """
    propio = (contacto.get("first_name") or "").strip()
    if propio:
        return propio.split()[0]
    p = persona_del_correo(contacto)
    return p["nombre"].split()[0] if p.get("nombre") else ""


# Cómo se saluda a una empresa entera cuando no se sabe quién lee. Es lo
# que pidió Pablo: "Hola señores de (nombre de la empresa)".
_SALUDO_EMPRESA = {
    "es": "Hola señores de {e}",
    "en": "Hello team at {e}",
    "pt": "Olá, equipe da {e}",
    "fr": "Bonjour à l’équipe de {e}",
    "it": "Salve, team di {e}",
    "de": "Hallo Team von {e}",
}
_ARRANQUES = ("hola", "hello", "hi ", "hey", "olá", "ola ", "bonjour", "salve",
              "ciao", "hallo", "buenas", "buenos días", "good ")


def saludo_de(contacto: dict, nombre: str, idioma: str) -> str:
    """Con qué renglón arranca el correo, sin coma final."""
    if nombre:
        return {"en": "Hi {n}", "de": "Hallo {n}", "fr": "Bonjour {n}",
                "pt": "Olá {n}", "it": "Ciao {n}"}.get(
                    idioma, "Hola {n}").format(n=nombre)
    empresa = (contacto.get("company_name") or contacto.get("full_name")
               or "").strip()
    if not empresa:
        return {"en": "Hello", "pt": "Olá", "fr": "Bonjour",
                "it": "Salve", "de": "Hallo"}.get(idioma, "Hola")
    plantilla = _SALUDO_EMPRESA.get(idioma, _SALUDO_EMPRESA["es"])
    return plantilla.format(e=empresa)


def con_saludo(cuerpo: str, saludo: str) -> str:
    """Deja el correo arrancando con el saludo que corresponde.

    El modelo casi siempre lo pone, pero "casi siempre" no alcanza cuando
    el saludo equivocado es justo el error que se ve en el primer renglón.
    Así que se verifica y, si hace falta, se corrige acá.
    """
    lineas = (cuerpo or "").split('\n')
    if not lineas or not (cuerpo or "").strip():
        return cuerpo
    primera = lineas[0].strip()
    if _normal(saludo) in _normal(primera):
        return cuerpo
    # Si lo que escribió es otro saludo, se reemplaza; si es la primera
    # frase del correo, el saludo se antepone y no se pierde nada.
    if primera and len(primera) <= 70 and _normal(primera).startswith(_ARRANQUES):
        lineas[0] = saludo + ("" if saludo.endswith((",", ":", "!")) else ",")
        return '\n'.join(lineas)
    return saludo + ',\n\n' + cuerpo.lstrip()


def _ficha(contacto: dict, nombre: str, idioma: str) -> str:
    perfil = _perfil(contacto)
    ciudades = perfil.get("ciudades") or []
    lineas = [
        ("Empresa", contacto.get("company_name") or contacto.get("full_name")),
        ("Nombre de la persona", nombre or "NO SE SABE"),
        ("SALUDO OBLIGATORIO (primer renglón, tal cual)",
         saludo_de(contacto, nombre, idioma)),
        ("Rubro", contacto.get("category")),
        ("Ciudad", ciudades[0] if ciudades else None),
        ("Sitio", contacto.get("company_domain")),
        ("Qué hace", perfil.get("resumen") or contacto.get("ai_summary")),
        ("Qué vende", ", ".join(perfil.get("que_vende") or [])),
        ("A quién le vende", perfil.get("a_quien_le_vende")),
        ("Gancho del sitio", perfil.get("gancho")),
        ("Novedades que cuenta el sitio",
         "; ".join(perfil.get("novedades") or [])),
        ("En qué se especializa",
         (perfil.get("especialidad") or "")
         if perfil.get("especialidad") not in (None, "no_esta_claro", "otro")
         else None),
        ("Antigüedad", perfil.get("anios_en_el_mercado")),
    ]
    ficha = "\n".join(f"{k}: {v}" for k, v in lineas if v)

    # El dato propio va aparte y con nombre y apellido. Dentro de la lista se
    # pierde entre los demás campos: medido contra un sitio real, con el
    # gancho en la ficha el correo salía genérico igual.
    propio = (perfil.get("gancho") or "").strip()
    novedades = [n for n in (perfil.get("novedades") or []) if (n or "").strip()]
    if novedades:
        propio = f"{propio} {novedades[0]}".strip() if propio else novedades[0]
    aparte = ""
    if propio:
        aparte = ("\n\nDATO PROPIO DE ESTA EMPRESA, que el correo TIENE "
                  f"que mencionar: {propio}")

    return (f"{ficha}{aparte}\n\n"
            f"Idioma del correo: {NOMBRE_IDIOMA.get(idioma, 'español')}")


def _pedido(contacto: dict, propuesta: str, nombre: str, idioma: str,
            insistir: bool = False, mas_corto: bool = False) -> dict:
    lengua = NOMBRE_IDIOMA.get(idioma, "español")
    # El idioma va primero y aparte. Los ejemplos de estilo están en español,
    # y sin esto el modelo escribe en español aunque se le pida otro idioma:
    # arrastra el idioma de lo que está leyendo.
    exigencia = (f"IDIOMA OBLIGATORIO: {lengua.upper()} ({idioma}).\n"
                 f"El asunto y el cuerpo van enteros en {lengua}. Los ejemplos "
                 f"de más abajo están en español y varios son largos, pero "
                 f"son ejemplos de TONO y de estructura: no copies ni el "
                 f"idioma ni el largo, copiá la forma.")
    if insistir:
        exigencia += (f"\n\nEl intento anterior salió en el idioma equivocado. "
                      f"Escribí en {lengua} y en ningún otro idioma.")
    if mas_corto:
        exigencia += (f"\n\nEl intento anterior salió demasiado largo. El cuerpo "
                      f"entero tiene que entrar entre {largo_minimo()} y "
                      f"{largo_maximo()} caracteres, "
                      f"con los cuatro tramos igual.")
    return {
        "model": modelo_redaccion(),
        "input": [
            {"role": "system",
             "content": f"{exigencia}\n\n{instrucciones(contacto)}\n\n"
                        f"=== Cómo suenan nuestros correos ===\n{EJEMPLOS}"},
            {"role": "user",
             "content": (f"=== Qué ofrecemos nosotros ===\n{propuesta.strip()}\n\n"
                         f"=== A quién le escribimos ===\n"
                         f"{_ficha(contacto, nombre, idioma)}")},
        ],
        "max_output_tokens": 3_000,
        "reasoning": {"effort": ESFUERZO_REDACCION},
        "text": {"format": {"type": "json_schema", "name": "correo",
                            "strict": True, "schema": ESQUEMA}},
    }


# Con qué esfuerzo piensa el modelo al redactar. Medido: en "minimal" no
# recorta —devuelve el mismo texto— y en "medium" el razonamiento se come el
# presupuesto de tokens y la respuesta sale vacía. "low" es el que anda.
ESFUERZO_REDACCION = "low"

# Y con qué modelo. El de las fichas (gpt-5-nano) alcanza para leer un sitio
# y llenar campos, pero escribiendo copia el brief adentro del correo en vez
# de redactar: medido, seis de cada diez en español. Para esto se usa uno más
# grande, que es una llamada por contacto y una sola vez.
def modelo_redaccion() -> str:
    return (os.getenv("OPENAI_MODEL_REDACCION") or "gpt-5-mini").strip()


def _pedido_acortar(cuerpo: str, asunto: str, idioma: str, saludo: str,
                    tope: int) -> dict:
    """Le pide al modelo que achique su propio correo hasta el tope.

    Componer y contar a la vez le sale mal: pidiéndole 300 devuelve 330. Pero
    recortar un texto que ya está escrito es una tarea mucho más chica, y con
    el sobrante dicho en números acierta.
    """
    sobra = len(cuerpo) - tope
    piso = largo_minimo()
    lengua = NOMBRE_IDIOMA.get(idioma, "español")
    palabras = max(20, int(tope / 6.2))
    reglas = [
        f"Te paso un correo en {lengua} que quedó largo. Devolvelo más corto.",
        "",
        f"- El cuerpo tiene {len(cuerpo)} caracteres y el máximo es {tope}: "
        f"sobran {sobra}. Sacá por lo menos {sobra + 25}.",
        f"- Son {palabras} palabras como máximo.",
        f"- Pero no lo dejes por debajo de {piso} caracteres: tienen que "
        f"seguir entrando los cuatro tramos.",
        f"- Primer renglón: «{saludo},», tal cual.",
        "- Tienen que quedar los cuatro tramos: problema, agitación, solución "
        "y una pregunta al final.",
        "- Sacá adjetivos, rodeos y subordinadas. NO saques ideas, no agregues "
        "nada nuevo y no cambies de idioma.",
        "- No lo firmes.",
    ]
    return {
        "model": modelo_redaccion(),
        "input": [
            {"role": "system",
             "content": "Reescribís correos más cortos sin perder las ideas."},
            {"role": "user",
             "content": "\n".join(reglas) + "\n\n=== Correo a acortar ==="
                        f"\nAsunto: {asunto}\n\n{cuerpo}"},
        ],
        "max_output_tokens": 3_000,
        "reasoning": {"effort": ESFUERZO_REDACCION},
        "text": {"format": {"type": "json_schema", "name": "correo",
                            "strict": True, "schema": ESQUEMA}},
    }


def propuesta_por_defecto() -> str:
    return (os.getenv("PROPUESTA_B2K") or
            "Somos KOMMO10X. Ayudamos a empresas con equipo comercial a "
            "convertir una operación que vive en WhatsApp, con seguimiento "
            "manual y a pulmón de cada asesor, en un sistema comercial "
            "organizado, medible y automatizado: Kommo CRM + WhatsApp + "
            "automatización + agentes de IA + procesos + seguimiento + "
            "analítica.").strip()


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
    # El tope es estricto —lo pidió Pablo así— y se mide DESPUÉS de acomodar
    # el saludo, porque el saludo también ocupa.
    tope, piso = largo_maximo(), largo_minimo()
    saludo = saludo_de(contacto, nombre, idioma)
    cuerpo = con_saludo(cuerpo, saludo)

    def sirve(texto: str) -> bool:
        # Un saludo suelto no es un correo: tiene que tener cuerpo y cerrar
        # con algo. El modelo a veces devuelve una sola línea.
        return bool(texto) and len(texto) >= piso and parece_idioma(texto, idioma)

    # Hasta tres pasadas de recorte. No se corta el texto por las bravas: un
    # correo cortado a la mitad se nota más que uno que no salió, y ahora hay
    # una pantalla de aprobación donde el que no salió se ve y se vuelve a
    # pedir con un clic.
    async def acortar_una_vez() -> bool:
        """Devuelve si consiguió achicarlo. Modifica cuerpo y asunto."""
        nonlocal cuerpo, asunto, body
        try:
            pedido = _pedido_acortar(cuerpo, asunto, idioma, saludo, tope)
            r2 = await client.post(ai.URL, json=pedido, headers=cabeceras)
            if r2.status_code == 400:
                pedido.pop("reasoning", None)
                r2 = await client.post(ai.URL, json=pedido, headers=cabeceras)
            if r2.status_code != 200:
                return False
            body2 = r2.json() if r2.content else {}
            crudo2 = ai._texto_de(body2)
            d2 = json.loads(crudo2) if crudo2 else {}
            nuevo = con_saludo(limpiar((d2.get("cuerpo") or "").strip()), saludo)
            # Solo se acepta si de verdad achicó y sigue siendo un correo:
            # recortar no puede significar devolver media frase.
            if not (sirve(nuevo) and len(nuevo) < len(cuerpo)):
                return False
            cuerpo, body = nuevo, body2
            asunto = (d2.get("asunto") or asunto).strip()
            # Los tramos se actualizan solo si el recorte los trajo: si no,
            # quedan los del borrador original, que son las mismas ideas.
            for k in ("problema", "agitacion", "solucion"):
                if (d2.get(k) or "").strip():
                    d[k] = d2[k]
            return True
        except Exception:
            return False

    # Dos borradores como mucho, y a cada uno se le pide recortar hasta que
    # deje de achicar. Cuando un borrador arranca muy largo no hay recorte que
    # lo salve, pero el siguiente suele nacer bien.
    for vuelta in range(2):
        while len(cuerpo) > tope and await acortar_una_vez():
            pass
        if len(cuerpo) <= tope or vuelta:
            break
        try:
            r2, body2 = await pedir(mas_corto=True)
            if r2.status_code != 200:
                break
            d2 = json.loads(ai._texto_de(body2) or "{}")
            nuevo = con_saludo(limpiar((d2.get("cuerpo") or "").strip()), saludo)
            if sirve(nuevo) and len(nuevo) < len(cuerpo):
                d, body, cuerpo = d2, body2, nuevo
                asunto = (d2.get("asunto") or asunto).strip()
        except Exception:
            break

    if len(cuerpo) > tope:
        return {"error": f"No logró entrar en {tope} caracteres "
                         f"(quedó en {len(cuerpo)}). Probá de nuevo."}

    if len(cuerpo) < piso:
        return {"error": f"El correo salió demasiado corto ({len(cuerpo)} "
                         f"caracteres): no alcanza para decir nada."}

    marcador = tiene_marcadores(cuerpo)
    copiado = repite_el_brief(cuerpo)
    if marcador or copiado:
        # Una vuelta más, por si fue un tropezón. Si vuelve a pasar se
        # rechaza: es de los errores que no se pueden mandar.
        try:
            r2, body2 = await pedir()
            if r2.status_code == 200:
                d2 = json.loads(ai._texto_de(body2) or "{}")
                nuevo = con_saludo(limpiar((d2.get("cuerpo") or "").strip()),
                                   saludo)
                if (sirve(nuevo) and len(nuevo) <= tope
                        and not tiene_marcadores(nuevo)
                        and not repite_el_brief(nuevo)):
                    d, body, cuerpo = d2, body2, nuevo
                    asunto = (d2.get("asunto") or asunto).strip()
                    marcador = copiado = ""
        except Exception:
            pass
    if marcador:
        return {"error": f"La IA dejó algo sin completar en el correo: "
                         f"{marcador[:60]}"}
    if copiado:
        return {"error": "La IA copió las instrucciones dentro del correo en "
                         "vez de escribirlo."}

    # El asunto va a seis palabras, como pide el brief: en el celular solo se
    # ve el principio y el resto no existe.
    palabras_asunto = asunto.split()
    if len(palabras_asunto) > 6:
        asunto = " ".join(palabras_asunto[:6]).rstrip(" ,;:-")
    if len(asunto) > 70:
        asunto = (asunto[:70].rsplit(" ", 1)[0] or asunto[:70]).rstrip(" ,;:-")

    return {"asunto": asunto[:200], "cuerpo": cuerpo, "nombre": nombre,
            "saludo": saludo,
            "a_quien": ("a " + nombre) if nombre
                       else "a la empresa (no se encontró la persona)",
            "problema": (d.get("problema") or "").strip(),
            "agitacion": (d.get("agitacion") or "").strip(),
            "solucion": (d.get("solucion") or "").strip(),
            "idioma": d.get("idioma") or idioma,
            "uso_nombre": bool(d.get("usó_nombre")),
            "caracteres": len(cuerpo),
            "tokens": ai._tokens_de(body), "error": None}


# --------------------------------------------------------------- borradores
# Nada sale sin que alguien lo lea antes. La IA escribe, el borrador queda
# guardado, y recién cuando está aprobado entra en la cola de envío.

@dataclass
class ProgresoRedaccion:
    corriendo: bool = False
    total: int = 0
    hechos: int = 0
    listos: int = 0
    fallados: int = 0
    empresa: str | None = None
    terminado: bool = False
    error: str | None = None

    def as_dict(self) -> dict:
        return {"running": self.corriendo, "total": self.total,
                "processed": self.hechos, "found": self.listos,
                "not_found": self.fallados, "current_contact": self.empresa,
                "finished": self.terminado, "error": self.error}


PROGRESO = ProgresoRedaccion()
_LOCK = asyncio.Lock()


def contactos_a_redactar(ids: list[int]) -> list[dict]:
    """Los marcados a los que se les puede escribir un correo con IA."""
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            f"""SELECT * FROM contacts
                 WHERE id IN ({ph}) AND email IS NOT NULL AND email <> ''
                   AND LOWER(email) NOT IN (SELECT email FROM suppression)
                 ORDER BY company_name""", ids)]


def _guardar(contacto: dict, r: dict) -> None:
    """Deja el borrador listo para que alguien lo mire.

    Se reemplaza el anterior del mismo contacto: el que vale es el último, y
    volver a pedirlo tiene que volver a dejarlo pendiente aunque el viejo ya
    estuviera aprobado.
    """
    with get_db() as conn:
        conn.execute(
            """INSERT INTO borradores (contact_id, asunto, cuerpo, nombre,
                   saludo, idioma, caracteres, a_quien, error, estado,
                   editado, at)
               VALUES (?,?,?,?,?,?,?,?,?,'pendiente',0,datetime('now'))
               ON CONFLICT(contact_id) DO UPDATE SET
                   asunto=excluded.asunto, cuerpo=excluded.cuerpo,
                   nombre=excluded.nombre, saludo=excluded.saludo,
                   idioma=excluded.idioma, caracteres=excluded.caracteres,
                   a_quien=excluded.a_quien, error=excluded.error,
                   estado='pendiente', editado=0, at=datetime('now')""",
            (contacto["id"], r.get("asunto"), r.get("cuerpo"), r.get("nombre"),
             r.get("saludo"), r.get("idioma"), r.get("caracteres"),
             r.get("a_quien"), r.get("error")))


async def _ficha_al_dia(client: httpx.AsyncClient, contacto: dict) -> dict:
    """Se asegura de haber leído el sitio antes de escribirle.

    El nombre de la persona sale de ahí. Si nunca se leyó el sitio no hay de
    dónde sacarlo, y el correo saldría saludando a la empresa por no haber
    mirado, que no es lo mismo que por no haber encontrado.
    """
    if _perfil(contacto) or not (contacto.get("company_domain") or "").strip():
        return contacto
    from . import website
    leido = await website.scrape(client, contacto["company_domain"])
    if not (leido.get("text") or "").strip():
        return contacto
    r = await ai.analizar(client, contacto, leido["text"])
    perfil = r.get("perfil")
    tok = r.get("tokens") or {}
    with get_db() as conn:
        conn.execute(
            """INSERT INTO ai_usage (contact_id, model, tokens_in, tokens_out,
               ok) VALUES (?,?,?,?,?)""",
            (contacto["id"], ai.modelo_ficha(), tok.get("entrada", 0),
             tok.get("salida", 0), 1 if perfil else 0))
        if not perfil:
            return contacto
        conn.execute(
            """UPDATE contacts SET ai_profile=?, ai_summary=?,
               ai_updated_at=datetime('now'), updated_at=datetime('now')
             WHERE id=?""",
            (json.dumps(perfil, ensure_ascii=False),
             (perfil.get("resumen") or "")[:400], contacto["id"]))
        fila = conn.execute("SELECT * FROM contacts WHERE id=?",
                            (contacto["id"],)).fetchone()
    return dict(fila) if fila else contacto


async def redactar_muchos(ids: list[int], propuesta: str = "") -> None:
    """Escribe el correo de cada contacto marcado y lo deja para aprobar."""
    global PROGRESO
    if _LOCK.locked():
        return
    async with _LOCK:
        contactos = contactos_a_redactar(ids)
        PROGRESO = ProgresoRedaccion(corriendo=True, total=len(contactos))
        if not ai.configured():
            PROGRESO.corriendo, PROGRESO.terminado = False, True
            PROGRESO.error = "Falta OPENAI_API_KEY en el .env."
            return
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                for c in contactos:
                    PROGRESO.empresa = (c.get("company_name")
                                        or c.get("full_name") or f"#{c['id']}")
                    try:
                        c = await _ficha_al_dia(client, c)
                        r = await escribir(client, c, propuesta)
                    except Exception as exc:
                        r = {"error": f"Se cayó al escribirlo: {type(exc).__name__}"}
                    _guardar(c, r)
                    PROGRESO.hechos += 1
                    if r.get("error"):
                        PROGRESO.fallados += 1
                    else:
                        PROGRESO.listos += 1
        except Exception as exc:
            PROGRESO.error = f"Se cortó: {type(exc).__name__}"
        finally:
            PROGRESO.corriendo, PROGRESO.terminado = False, True
            PROGRESO.empresa = None


def borradores(ids: list[int] | None = None) -> list[dict]:
    """Los borradores con los datos del contacto, para la pantalla."""
    where, args = "", []
    if ids:
        marcas = ",".join("?" * len(ids))
        where = f" WHERE b.contact_id IN ({marcas})"
        args = list(ids)
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            f"""SELECT b.*, c.email, c.company_name, c.full_name,
                       c.company_domain
                  FROM borradores b JOIN contacts c ON c.id = b.contact_id
                {where}
                 ORDER BY CASE b.estado WHEN 'pendiente' THEN 0
                                        WHEN 'aprobado' THEN 1 ELSE 2 END,
                          b.error IS NULL, c.company_name""", args)]


def aprobados() -> dict[int, dict]:
    """Lo que se puede mandar: {contact_id: {asunto, cuerpo}}."""
    with get_db() as conn:
        return {r["contact_id"]: {"asunto": r["asunto"], "cuerpo": r["cuerpo"]}
                for r in conn.execute(
                    """SELECT contact_id, asunto, cuerpo FROM borradores
                        WHERE estado = 'aprobado' AND error IS NULL
                          AND cuerpo IS NOT NULL AND cuerpo <> ''""")}


def marcar(contact_id: int, estado: str) -> None:
    if estado not in ("pendiente", "aprobado", "descartado"):
        raise ValueError(estado)
    with get_db() as conn:
        conn.execute("UPDATE borradores SET estado=? WHERE contact_id=?",
                     (estado, contact_id))


def marcar_todos(estado: str) -> int:
    """Aprueba o descarta de una todos los que están esperando."""
    if estado not in ("aprobado", "descartado"):
        raise ValueError(estado)
    with get_db() as conn:
        cur = conn.execute(
            """UPDATE borradores SET estado=?
                WHERE estado='pendiente' AND error IS NULL
                  AND cuerpo IS NOT NULL AND cuerpo <> ''""", (estado,))
        return cur.rowcount


def editar(contact_id: int, asunto: str, cuerpo: str) -> dict:
    """Guarda la corrección a mano. El tope sigue valiendo."""
    cuerpo, asunto = (cuerpo or "").strip(), (asunto or "").strip()
    if not cuerpo or not asunto:
        raise ValueError("El correo no puede quedar vacío.")
    tope = largo_maximo()
    if len(cuerpo) > tope:
        raise ValueError(f"El cuerpo tiene {len(cuerpo)} caracteres y el "
                         f"máximo es {tope}.")
    with get_db() as conn:
        conn.execute(
            """UPDATE borradores SET asunto=?, cuerpo=?, caracteres=?,
                   error=NULL, editado=1 WHERE contact_id=?""",
            (asunto, cuerpo, len(cuerpo), contact_id))
    return {"caracteres": len(cuerpo)}


def limpiar_borradores(ids: list[int] | None = None) -> int:
    """Saca los borradores que ya no sirven."""
    with get_db() as conn:
        if ids:
            marcas = ",".join("?" * len(ids))
            cur = conn.execute(
                f"DELETE FROM borradores WHERE contact_id IN ({marcas})", ids)
        else:
            cur = conn.execute("DELETE FROM borradores")
        return cur.rowcount
