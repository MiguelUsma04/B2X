"""Números de teléfono de cualquier país.

Antes esto era una regla escrita a mano para Colombia: diez dígitos que
arrancan en 3 son celular, los que arrancan en 60 son fijo. Sirve mientras se
prospecte en un solo país y miente apenas se sale de ahí — un número mexicano
de diez dígitos habría salido con +57 adelante, que es el número de otra
persona.

Acá se usa la librería de numeración de Google, la misma que usa Android para
decidir cómo marcar. Sabe la regla de cada país: cuántos dígitos tiene un
número, si es móvil o fijo, y cómo se escribe en formato internacional.

De dónde sale el país, en orden:

1. El número ya lo trae (empieza con +). Es el caso de todo lo que llega de
   Google Maps, que devuelve el teléfono en formato internacional.
2. El país que Google informó para la dirección de la empresa.
3. La dirección escrita, si menciona un país que se reconozca.
4. El país por defecto del .env.

Si con todo eso no se puede, el número se deja como vino. Un número mal
convertido no es un número incompleto: es el número de otro.
"""
import os
import re
import unicodedata

import phonenumbers
from phonenumbers import PhoneNumberFormat, PhoneNumberType

# Lo que la app entiende por cada tipo de número.
CELULAR = "celular"
FIJO = "fijo"
DESCONOCIDO = ""

_MOVILES = {PhoneNumberType.MOBILE}
_FIJOS = {PhoneNumberType.FIXED_LINE}
# Hay países —Estados Unidos, México— donde el número no dice si es móvil o
# fijo: la numeración es la misma. Ahí no se puede afirmar nada.
_AMBIGUOS = {PhoneNumberType.FIXED_LINE_OR_MOBILE}


def pais_por_defecto() -> str:
    return (os.getenv("PAIS_POR_DEFECTO")
            or os.getenv("KOMMO_COUNTRY_REGION") or "CO").strip().upper()


# ------------------------------------------------------- el país de un texto
def _sin_tildes(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


# Los nombres de país como aparecen al final de una dirección de Google, en
# español y en inglés. No es la lista del mundo entero: es la de los países
# donde se prospecta, y se amplía cuando haga falta.
_PAISES = {
    "colombia": "CO", "mexico": "MX", "méxico": "MX", "peru": "PE",
    "chile": "CL", "argentina": "AR", "ecuador": "EC", "bolivia": "BO",
    "uruguay": "UY", "paraguay": "PY", "venezuela": "VE", "brasil": "BR",
    "brazil": "BR", "panama": "PA", "costa rica": "CR", "guatemala": "GT",
    "honduras": "HN", "nicaragua": "NI", "el salvador": "SV",
    "republica dominicana": "DO", "dominican republic": "DO",
    "puerto rico": "PR", "cuba": "CU",
    "espana": "ES", "spain": "ES", "portugal": "PT",
    "estados unidos": "US", "united states": "US", "usa": "US",
    "canada": "CA", "reino unido": "GB", "united kingdom": "GB",
    "francia": "FR", "france": "FR", "italia": "IT", "italy": "IT",
    "alemania": "DE", "germany": "DE",
}


def region_de_direccion(direccion: str | None) -> str:
    """El código de país que se pueda leer de una dirección escrita.

    Google pone el país al final ("…, Bogotá, Colombia"), así que se mira de
    atrás hacia adelante: el último pedazo que coincida es el bueno.
    """
    if not direccion:
        return ""
    partes = [p.strip() for p in _sin_tildes(direccion).split(",") if p.strip()]
    for parte in reversed(partes):
        if parte in _PAISES:
            return _PAISES[parte]
    # Algunas direcciones no separan el país con coma.
    limpio = _sin_tildes(direccion)
    for nombre, iso in _PAISES.items():
        if re.search(rf"\b{re.escape(nombre)}\b", limpio):
            return iso
    return ""


def region_del_contacto(c: dict) -> str:
    """El país de este contacto, con las pistas que haya."""
    directo = (c.get("country") or "").strip().upper()
    if len(directo) == 2:
        return directo
    return region_de_direccion(c.get("address")) or pais_por_defecto()


# ------------------------------------------------------------- el número
def _leer(numero: str, region: str | None):
    crudo = (numero or "").strip()
    if not crudo:
        return None
    # Con el + adelante el país ya viene en el número: la región no se usa.
    intento = None if crudo.startswith("+") else (region or pais_por_defecto())
    try:
        p = phonenumbers.parse(crudo, intento)
    except phonenumbers.NumberParseException:
        return None
    return p if phonenumbers.is_valid_number(p) else None


def normalizar(numero: str, region: str | None = None) -> str:
    """El número en formato internacional: +525512345678.

    Es el formato que WhatsApp y Kommo necesitan. Si el número no se puede
    entender con el país que se le dio, se devuelve tal como vino: dejarlo
    incompleto es mejor que convertirlo en el de otra persona.
    """
    p = _leer(numero, region)
    if not p:
        return (numero or "").strip()
    return phonenumbers.format_number(p, PhoneNumberFormat.E164)


def tipo(numero: str, region: str | None = None) -> str:
    """Si es celular, fijo, o no se puede saber.

    Importa para dos cosas: filtrar a quién se le puede escribir por WhatsApp,
    y marcar en Kommo si el número es móvil o de oficina.
    """
    p = _leer(numero, region)
    if not p:
        return DESCONOCIDO
    t = phonenumbers.number_type(p)
    if t in _MOVILES:
        return CELULAR
    if t in _FIJOS:
        return FIJO
    return DESCONOCIDO      # ambiguo o de otro tipo: no se afirma nada


def pais_del_numero(numero: str, region: str | None = None) -> str:
    """De qué país es el número, una vez entendido."""
    p = _leer(numero, region)
    return phonenumbers.region_code_for_number(p) if p else ""


def valido(numero: str, region: str | None = None) -> bool:
    return _leer(numero, region) is not None
