"""Que la misma empresa no entre dos veces, venga de donde venga.

Escribirle dos veces a la misma empresa es la forma más rápida de que marquen
los correos como spam, y en el CRM un lead duplicado se lo lleva un comercial
distinto que no sabe que otro ya está hablando con ellos.

Antes había tres controles sueltos —email repetido, nombre+dominio repetidos,
place_id repetido— y cada camino de carga usaba los suyos. Servían para lo
obvio y dejaban pasar lo común:

- El mismo negocio con dos fichas en Google (la casa matriz y la sucursal, o
  una ficha vieja sin reclamar): dos place_id distintos.
- "Viajes ABC S.A.S." y "VIAJES ABC SAS": nombres distintos para el índice.
- Dos personas del mismo dominio cargadas de fuentes distintas: la empresa es
  una sola.

Acá el criterio es uno solo y se aplica a todo lo que entra. Cada empresa
deja TODAS las huellas que tenga, y alcanza con que coincida una:

    · el dominio del sitio      —dos contactos de gaptravel.co son la misma
                                 empresa, se llamen como se llamen
    · el teléfono               —un número publicado no lo comparten dos
                                 negocios distintos
    · el nombre normalizado     —sin acentos, sin S.A.S., sin puntuación,
      + la ciudad                con la ciudad al lado para no fundir dos
                                 "Viajes del Sur" de ciudades distintas

Todas y no la mejor: si cada empresa dejara solo su huella más fuerte, la que
entró con sitio web no quedaría anotada por su teléfono, y la misma empresa
llegando después sin sitio pero con el mismo número entraría como nueva. Es
exactamente lo que pasa con las sucursales que Google lista aparte.

Lo que no se hace a propósito: unir por parecido. "Viajes del Sur" y "Viajes
Sur" pueden ser la misma empresa o dos competidores, y fusionar dos empresas
que no lo son se descubre tarde y mal. Eso se muestra como sospecha para que
lo mire una persona, no se decide solo.
"""
import re
import unicodedata

from .db import get_db

# Lo que va en el nombre de una empresa y no la identifica.
_FORMAS = re.compile(
    r"\b(s\.?a\.?s?|s\.?a\.?s\.?|ltda|limitada|s\.?r\.?l|e\.?i\.?r\.?l|"
    r"c\.?a|s\.?p\.?a|inc|llc|ltd|corp|co|company|group|grupo|holding|"
    r"sociedad|anonima|cia|compania|and|the)\b", re.I)
_RUIDO = re.compile(r"[^a-z0-9 ]+")
_ESPACIOS = re.compile(r"\s+")


def normal(texto: str) -> str:
    """Un texto reducido a lo que lo identifica."""
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("&", " y ")
    t = _RUIDO.sub(" ", t)
    t = _FORMAS.sub(" ", t)
    return _ESPACIOS.sub(" ", t).strip()


def dominio_normal(valor: str) -> str:
    """El dominio, sin www ni protocolo ni barra final."""
    d = (valor or "").strip().lower()
    if "://" in d:
        d = d.split("://", 1)[1]
    d = d.split("/")[0].split("?")[0]
    if d.startswith("www."):
        d = d[4:]
    return d.strip(". ")


def telefono_normal(valor: str) -> str:
    """Solo los dígitos, y los últimos nueve, que son los que distinguen.

    El mismo número aparece escrito de cinco formas: con indicativo, sin él,
    con el cero de larga distancia adelante. Comparando la cola se emparejan
    sin tener que adivinar el país.
    """
    d = re.sub(r"\D", "", valor or "")
    return d[-9:] if len(d) >= 9 else ""


def huellas(c: dict) -> list[str]:
    """Todas las huellas de esta empresa, de la más fuerte a la más débil.

    Cada una lleva su tipo adelante para poder explicar después por qué dos
    contactos son el mismo.
    """
    salida = []
    dom = dominio_normal(c.get("company_domain") or "")
    if dom and "." in dom:
        salida.append(f"dominio:{dom}")

    tel = telefono_normal(c.get("phone") or "")
    if tel:
        salida.append(f"telefono:{tel}")

    nombre = normal(c.get("company_name") or c.get("full_name") or "")
    if nombre:
        direccion = c.get("address") or ""
        ciudad = normal(direccion.split(",")[-2]
                        if direccion.count(",") >= 2 else "")
        salida.append(f"nombre:{nombre}|{ciudad}".rstrip("|"))
    return salida


def huella(c: dict) -> tuple[str, str]:
    """La huella más fuerte, para mostrarla. Ver huellas() para comparar."""
    todas = huellas(c)
    if not todas:
        return "", ""
    tipo, valor = todas[0].split(":", 1)
    return tipo, valor


def huellas_cargadas(conn) -> dict[str, int]:
    """Las huellas de todo lo que ya está en la base -> id del contacto."""
    mapa: dict[str, int] = {}
    for r in conn.execute(
            """SELECT id, company_name, full_name, company_domain, phone,
                      address FROM contacts"""):
        for h in huellas(dict(r)):
            mapa.setdefault(h, r["id"])
    return mapa


def es_repetido(candidato: dict, ya: dict[str, int]) -> int | None:
    """El id del contacto que ya representa a esta empresa, si lo hay."""
    for h in huellas(candidato):
        if h in ya:
            return ya[h]
    return None


def marcar(candidato: dict, ya: dict[str, int], nuevo_id: int) -> None:
    """Deja anotada la huella del que acaba de entrar."""
    for h in huellas(candidato):
        ya.setdefault(h, nuevo_id)


# ------------------------------------------------------------- las sospechas
# Lo que no se une solo, pero conviene que alguien mire.

def sospechas(limite: int = 200) -> list[dict]:
    """Grupos de contactos que probablemente sean la misma empresa.

    Se agrupan por huella exacta —que no debería pasar y pasa cuando algo se
    cargó antes de que existiera este control— y por nombre normalizado, que
    es la que necesita ojo humano: dos "Viajes del Sur" pueden ser la misma
    empresa con dos sedes, o dos empresas distintas con el mismo nombre.
    """
    with get_db() as conn:
        filas = [dict(r) for r in conn.execute(
            """SELECT id, company_name, full_name, company_domain, email,
                      phone, address, crm_lead_id
                 FROM contacts ORDER BY id""")]

    por_huella: dict[str, list[dict]] = {}
    por_nombre: dict[str, list[dict]] = {}
    for c in filas:
        for h in huellas(c):
            por_huella.setdefault(h, []).append(c)
        n = normal(c.get("company_name") or c.get("full_name") or "")
        if len(n) >= 5:
            por_nombre.setdefault(n, []).append(c)

    grupos, vistos = [], set()

    def agregar(items, motivo, seguro):
        ids = tuple(sorted(x["id"] for x in items))
        if len(ids) < 2 or ids in vistos:
            return
        vistos.add(ids)
        grupos.append({
            "motivo": motivo, "seguro": seguro,
            "contactos": [{"id": x["id"],
                           "nombre": x["company_name"] or x["full_name"],
                           "email": x["email"], "dominio": x["company_domain"],
                           "telefono": x["phone"], "direccion": x["address"],
                           "en_crm": bool(x["crm_lead_id"])} for x in items]})

    etiquetas = {"dominio": "Mismo sitio web", "telefono": "Mismo teléfono",
                 "nombre": "Mismo nombre y ciudad"}
    for clave, items in por_huella.items():
        tipo = clave.split(":", 1)[0]
        agregar(items, etiquetas.get(tipo, "Misma huella"), True)
    for items in por_nombre.values():
        agregar(items, "Nombre parecido, hay que mirarlo", False)

    # Primero lo seguro, y dentro de eso los grupos más grandes.
    grupos.sort(key=lambda g: (not g["seguro"], -len(g["contactos"])))
    return grupos[:limite]
