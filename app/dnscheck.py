"""Si un dominio está listo para mandar correo.

Tres registros deciden si el correo entra a la bandeja o al spam:

- **SPF** dice qué servidores pueden mandar en nombre del dominio.
- **DKIM** es la firma con la que el receptor comprueba que nadie lo tocó.
- **DMARC** dice qué hacer cuando algo no cuadra, y a dónde avisar.

Lo que casi nadie sabe y rompe la mitad de los envíos desde subdominios: **el
SPF no se hereda**. Si mandás desde ventas@mail.midominio.co, el registro de
midominio.co no cubre nada; mail.midominio.co necesita el suyo. DKIM tampoco:
Workspace genera una clave por dominio. DMARC sí se hereda del dominio padre,
salvo que el padre diga lo contrario con `sp=`.

Se consulta por DNS sobre HTTPS para no sumar una dependencia: la app ya
habla HTTP y no todos los servidores dejan salir consultas DNS sueltas.
"""
import asyncio

import httpx

DOH = "https://dns.google/resolve"

# El selector con el que Google Workspace firma. Es el que pone por defecto al
# activar DKIM; quien lo haya cambiado a mano tendrá que mirarlo aparte.
SELECTOR_GOOGLE = "google"


async def _txt(cliente: httpx.AsyncClient, nombre: str) -> list[str]:
    """Los registros TXT de un nombre. Lista vacía si no hay o si falla."""
    try:
        r = await cliente.get(DOH, params={"name": nombre, "type": "TXT"},
                              headers={"accept": "application/dns-json"})
        if r.status_code != 200:
            return []
        datos = r.json()
    except Exception:
        return []
    salida = []
    for a in datos.get("Answer") or []:
        if a.get("type") != 16:      # 16 = TXT
            continue
        # El DNS parte los textos largos en pedazos entre comillas; van unidos.
        texto = (a.get("data") or "").strip()
        salida.append("".join(p for p in texto.split('" "')).strip('"'))
    return salida


def _padre(dominio: str) -> str:
    """El dominio del que cuelga este, si cuelga de alguno."""
    partes = dominio.split(".")
    return ".".join(partes[1:]) if len(partes) > 2 else ""


async def revisar_dominio(dominio: str) -> dict:
    """Mira SPF, DKIM y DMARC de un dominio. No falla nunca: informa."""
    dominio = (dominio or "").strip().lower().lstrip("@")
    if "." not in dominio:
        return {"domain": dominio, "ok": False,
                "spf": {"ok": False, "detail": "Dominio inválido."},
                "dkim": {"ok": False, "detail": ""},
                "dmarc": {"ok": False, "detail": ""}}

    async with httpx.AsyncClient(timeout=12) as cli:
        raiz, dkim, dmarc, dmarc_padre = await asyncio.gather(
            _txt(cli, dominio),
            _txt(cli, f"{SELECTOR_GOOGLE}._domainkey.{dominio}"),
            _txt(cli, f"_dmarc.{dominio}"),
            _txt(cli, f"_dmarc.{_padre(dominio)}") if _padre(dominio) else _nada(),
        )

    return veredicto({
        "domain": dominio,
        "spf": _leer_spf(raiz),
        "dkim": _leer_dkim(dkim),
        "dmarc": _leer_dmarc(dmarc, dmarc_padre, _padre(dominio)),
    })


async def _nada() -> list[str]:
    return []


def _leer_spf(registros: list[str]) -> dict:
    spf = [r for r in registros if r.lower().startswith("v=spf1")]
    if not spf:
        return {"ok": False, "value": "",
                "detail": "No tiene SPF. Sin esto el correo llega marcado como "
                          "sospechoso, o directo no llega."}
    if len(spf) > 1:
        return {"ok": False, "value": " / ".join(spf),
                "detail": "Hay más de un SPF y eso lo invalida entero: tiene "
                          "que ser uno solo, con todo adentro."}
    valor = spf[0]
    if "_spf.google.com" not in valor.lower():
        return {"ok": False, "value": valor,
                "detail": "El SPF no incluye a Google, así que Workspace no "
                          "está autorizado a mandar por este dominio."}
    return {"ok": True, "value": valor, "detail": ""}


def _leer_dkim(registros: list[str]) -> dict:
    firma = [r for r in registros if "p=" in r and "k=" in r.lower()]
    if not firma:
        return {"ok": False, "value": "",
                "detail": "No tiene DKIM. En Workspace hay que generar la clave "
                          "para este dominio y publicarla; no se hereda."}
    return {"ok": True, "value": firma[0][:60] + "…", "detail": ""}


def _leer_dmarc(propios: list[str], del_padre: list[str], padre: str) -> dict:
    mio = [r for r in propios if r.lower().startswith("v=dmarc1")]
    if mio:
        valor = mio[0]
        politica = "p=none" in valor.lower().replace(" ", "")
        return {"ok": True, "value": valor, "heredado": False,
                "detail": ("Está en p=none: solo observa, no protege todavía. "
                           "Sirve para arrancar y mirar los reportes."
                           if politica else "")}

    heredado = [r for r in del_padre if r.lower().startswith("v=dmarc1")]
    if heredado:
        sp = [t for t in heredado[0].lower().replace(" ", "").split(";")
              if t.startswith("sp=")]
        return {"ok": True, "value": heredado[0], "heredado": True,
                "detail": f"Lo hereda de {padre}"
                          + (f", que fija {sp[0]} para los subdominios." if sp
                             else ". Conviene igual poner uno propio para "
                                  "recibir los reportes por separado.")}

    return {"ok": False, "value": "", "heredado": False,
            "detail": "No tiene DMARC ni lo hereda. Sin esto, Gmail y Outlook "
                      "tratan al dominio con desconfianza."}


def veredicto(r: dict) -> dict:
    """Resume los tres en uno solo, que es lo que se muestra."""
    faltan = [k for k in ("spf", "dkim", "dmarc") if not r.get(k, {}).get("ok")]
    r["ok"] = not faltan
    r["falta"] = faltan
    return r
