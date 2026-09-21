"""Recorre el sitio del negocio entero y se trae lo que dice.

Google Maps da el dominio pero nunca el email. Acá se entra al sitio y se lee:
el contacto y el equipo —de donde salen el correo, el teléfono y el nombre de
quien maneja cada casilla— y también las novedades, los casos, los servicios y
los destinos, que son los que hacen que un correo se note escrito para ellos y
no para cualquiera.

Se recorre el sitio completo, pero con presupuesto: un techo de páginas, uno de
texto y uno de tiempo. Un sitio grande no puede dejar colgada a toda la tanda.
Las páginas se visitan por orden de utilidad, así que si el presupuesto se
acaba, lo que se leyó es lo que más servía.

Es scraping cortés: se respeta robots.txt, se dice quién es el bot, se bajan de
a pocas a la vez y se espera entre tandas. No cuesta créditos, así que puede
correr sobre toda la lista sin pensarlo.
"""
import asyncio
import os
import re
import time
import urllib.parse
from html.parser import HTMLParser
from urllib.robotparser import RobotFileParser

import httpx

UA = "B2X/1.0 (prospeccion B2B; contacto por el sitio)"
TIMEOUT = 12.0
PAGE_BYTES = 600_000     # más que esto no es una página, es una descarga
A_LA_VEZ = 4             # cuántas se bajan en paralelo
ESPERA = 0.5             # segundos entre tandas: el sitio no es nuestro


def max_paginas() -> int:
    """Techo de páginas por sitio. Con 40 entra un sitio corporativo entero."""
    try:
        return max(1, min(300, int(os.getenv("SITIO_MAX_PAGINAS") or "40")))
    except ValueError:
        return 40


def presupuesto_texto() -> int:
    """Cuánto texto se guarda en total. Es lo que después lee la IA."""
    try:
        return max(10_000, min(400_000,
                               int(os.getenv("SITIO_MAX_TEXTO") or "120000")))
    except ValueError:
        return 120_000


def presupuesto_tiempo() -> float:
    """Cuántos segundos como mucho en un sitio, pase lo que pase."""
    try:
        return max(10.0, min(600.0, float(os.getenv("SITIO_MAX_SEGUNDOS") or "75")))
    except ValueError:
        return 75.0


TEXT_PER_PAGE = 8_000    # por página: una sola no se come el presupuesto
MAX_PAGES = 40           # el valor por defecto, para quien llame sin decir nada

# Páginas donde vive el contacto y el nombre de quien atiende cada casilla.
PISTAS_CONTACTO = re.compile(
    r"contact|contacto|contactenos|contáctenos|contactanos|escribinos|"
    r"about|nosotros|quienes|quiénes|empresa|equipo|team|staff|directorio|"
    r"atencion|atención|soporte|ayuda|reservas|sucursal|oficina", re.I)

# Páginas que cuentan algo propio de esta empresa: lo que hace que un correo
# no parezca una plantilla. Es lo que se buscaba al abrir el sitio entero.
PISTAS_JUGOSAS = re.compile(
    r"blog|noticia|novedad|news|prensa|press|actualidad|articulo|artículo|"
    r"caso|case|exito|éxito|testimoni|cliente|portfolio|portafolio|proyecto|"
    r"servicio|service|producto|solucion|solución|destino|destination|paquete|"
    r"tour|circuito|crucero|experiencia|precio|tarifa|plan|promo|oferta|"
    r"catalogo|catálogo|agencia|corporativ|empresarial|incentivo|mayorista",
    re.I)

# Lo que nunca aporta y llena el presupuesto: legales, carrito, buscador,
# paginación y los archivos por fecha o etiqueta de los blogs.
PISTAS_INUTILES = re.compile(
    r"/(privacidad|privacy|terminos|términos|terms|cookies|legal|aviso-legal|"
    r"politica|política|carrito|cart|checkout|login|ingresar|registro|signup|"
    r"mi-cuenta|my-account|wp-admin|wp-login|feed|rss|sitemap|buscar|search|"
    r"tag|etiqueta|category|categoria|categoría|author|autor|page|pagina|"
    r"página)(/|$|\?|\.)|/20\d\d/\d\d?/?$", re.I)

# Un enlace a un archivo no es una página.
NO_ES_PAGINA = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|zip|rar|7z|mp[34]|avi|mov|wmv|png|jpe?g|gif|"
    r"svg|webp|ico|css|js|xml|json|woff2?|ttf|eot|apk|dmg|exe)$", re.I)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Buzones de área, no de persona: sirven igual, pero valen menos.
BUZONES = {
    "info", "contacto", "contact", "ventas", "sales", "hola", "hello", "mail",
    "correo", "administracion", "administración", "admin", "soporte", "support",
    "atencion", "atencionalcliente", "reservas", "reservations", "booking",
    "comercial", "gerencia", "recepcion", "recepción", "marketing", "rrhh",
    "facturacion", "facturación", "cobranzas", "pedidos", "citas", "turnos",
    "prensa", "press", "jobs", "empleo", "trabajo", "legal", "privacidad",
    "privacy", "help", "ayuda", "team", "equipo", "oficina", "office",
    "general", "pqr", "sac", "servicioalcliente", "notificaciones",
    "newsletter", "noticias", "donate", "donaciones", "campaigns", "campanas",
}
# Nunca sirven: son buzones que rebotan o basura de plantillas.
NUNCA = re.compile(r"^(no-?reply|noresponder|donotreply|postmaster|abuse|"
                   r"webmaster|hostmaster|mailer-daemon)", re.I)
DOMINIOS_BASURA = re.compile(
    r"(sentry|wixpress|wix\.com|example|dominio|yourdomain|yoursite|domain\.com|"
    r"email\.com|test\.com|sentry\.io|godaddy|squarespace|cloudflare|w3\.org|"
    r"schema\.org|jquery|bootstrap|googleapis|gstatic)", re.I)
EXTENSIONES = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js|ico|woff2?|ttf)$", re.I)

TEL_LIMPIO = re.compile(r"[^\d+]")
# Un teléfono suelto en el texto tiene que anunciarse: o trae código de país
# con +, o el indicativo entre paréntesis. Sin esa marca no se toma, porque
# cualquier versión, fecha o número de factura entra como teléfono. Lo demás
# se saca de los links tel:, que son inequívocos.
TEL_TEXTO = re.compile(
    r"(?<![\d/])(?:\+\d{1,3}[\s.\-]?\(?\d{1,4}\)?|\(\d{2,4}\))"
    r"[\s.\-]?\d{2,4}[\s.\-]?\d{2,4}(?:[\s.\-]?\d{2,4})?(?![\d/])")


def prioridad(url: str, texto_link: str = "") -> int:
    """Qué tan arriba en la fila va esta página. 0 significa no visitarla.

    El orden importa de verdad: si el presupuesto se acaba a mitad de camino,
    lo que quedó leído tiene que ser lo que más servía, no las primeras que
    aparecieron en el menú.
    """
    if NO_ES_PAGINA.search(url) or PISTAS_INUTILES.search(url):
        return 0
    junto = url + " " + (texto_link or "")
    if PISTAS_CONTACTO.search(junto):
        return 3          # ahí están el correo y el nombre de quien atiende
    if PISTAS_JUGOSAS.search(junto):
        return 2          # ahí está lo que hace propio al correo
    if url.count("/") <= 4:
        return 1          # una página de primer nivel: puede ser cualquier cosa
    return 1


class Pagina(HTMLParser):
    """Saca de una página lo único que importa acá: links y texto visible."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []   # (href, texto del link)
        self.textos: list[str] = []
        self._href: str | None = None
        self._texto_link: list[str] = []
        self._ignorar = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._ignorar += 1
        elif tag == "a":
            d = dict(attrs)
            self._href = d.get("href") or ""
            self._texto_link = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._ignorar = max(0, self._ignorar - 1)
        elif tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._texto_link).strip()))
            self._href, self._texto_link = None, []

    def handle_data(self, data):
        if self._ignorar:
            return
        self.textos.append(data)
        if self._href is not None:
            self._texto_link.append(data)

    @property
    def texto(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.textos))


def _email_valido(email: str, dominio: str | None) -> bool:
    email = email.lower()
    if EXTENSIONES.search(email) or DOMINIOS_BASURA.search(email):
        return False
    local, _, host = email.partition("@")
    if not local or NUNCA.match(local) or len(email) > 120:
        return False
    if re.fullmatch(r"[0-9a-f]{16,}", local):     # hashes de plantillas
        return False
    return True


def _puntaje(email: str, dominio: str | None) -> int:
    """Cuanto más alto, mejor candidato. Persona del dominio propio primero."""
    local, _, host = email.lower().partition("@")
    base = local.split("+")[0].replace(".", "").replace("_", "").replace("-", "")
    es_buzon = local.split("+")[0] in BUZONES or base in BUZONES
    propio = bool(dominio) and (host == dominio or host.endswith("." + dominio))
    if propio and not es_buzon:
        return 4        # juan@empresa.com
    if propio:
        return 3        # info@empresa.com
    if not es_buzon:
        return 2        # juan@gmail.com
    return 1            # info@gmail.com


def _nombre_desde_email(email: str) -> str | None:
    """Deduce el nombre cuando el email lo lleva: juan.perez@ -> Juan Perez."""
    local = email.split("@")[0].split("+")[0]
    partes = [p for p in re.split(r"[._\-]+", local) if p]
    if len(partes) < 2 or any(len(p) < 2 for p in partes[:2]):
        return None
    if any(re.search(r"\d", p) for p in partes) or partes[0].lower() in BUZONES:
        return None
    return " ".join(p.capitalize() for p in partes[:2])


def _tel_valido(crudo: str) -> str | None:
    """Normaliza y descarta lo que claramente no es un teléfono."""
    t = TEL_LIMPIO.sub("", crudo or "")
    if t.startswith("00"):
        t = "+" + t[2:]
    digitos = re.sub(r"\D", "", t)
    if not (7 <= len(digitos) <= 15):
        return None
    if len(set(digitos)) <= 2:                 # 111111111, 000000000
        return None
    if digitos in "01234567890123456789" or digitos in "98765432109876543210":
        return None                            # 12345678: relleno de plantilla
    if re.match(r"^(19|20)\d{6}$", digitos):   # una fecha, no un teléfono
        return None
    return t


async def _robots_permite(client: httpx.AsyncClient, base: str, cache: dict) -> RobotFileParser | None:
    if base in cache:
        return cache[base]
    rp = RobotFileParser()
    try:
        r = await client.get(urllib.parse.urljoin(base, "/robots.txt"),
                             headers={"User-Agent": UA})
        rp.parse(r.text.splitlines() if r.status_code == 200 else [])
    except Exception:
        rp.parse([])          # sin robots legible, se sigue con lo básico
    cache[base] = rp
    return rp


async def _bajar(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        r = await client.get(url, headers={"User-Agent": UA, "Accept-Language": "es,en"},
                             follow_redirects=True)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    tipo = (r.headers.get("content-type") or "").lower()
    if "html" not in tipo and "text" not in tipo:
        return None
    return r.text[:PAGE_BYTES]


async def _sumar_sitemap(client: httpx.AsyncClient, base: str, dominio: str,
                         encolar) -> None:
    """Mete en la fila lo que el propio sitio declara que existe.

    Un sitio armado con un constructor de páginas suele tener el menú en
    JavaScript, y desde el HTML no se ve ni un enlace. El sitemap sí las
    lista: es la diferencia entre leer una página y leer el sitio.
    """
    for nombre in ("/sitemap.xml", "/sitemap_index.xml", "/page-sitemap.xml"):
        try:
            r = await client.get(urllib.parse.urljoin(base, nombre),
                                 headers={"User-Agent": UA},
                                 follow_redirects=True)
        except Exception:
            continue
        if r.status_code != 200 or "<loc" not in r.text[:4000].lower():
            continue
        urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text[:400_000], re.I)
        anidados = [u for u in urls if u.lower().endswith(".xml")][:8]
        for u in urls:
            if u.lower().endswith(".xml"):
                continue
            pp = urllib.parse.urlparse(u)
            if pp.netloc.replace("www.", "") == dominio:
                encolar(pp._replace(fragment="", query="").geturl(), "")
        # Un índice de sitemaps: se abre un nivel más y se corta ahí.
        for sub in anidados:
            try:
                r2 = await client.get(sub, headers={"User-Agent": UA},
                                      follow_redirects=True)
            except Exception:
                continue
            if r2.status_code != 200:
                continue
            for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>",
                                r2.text[:400_000], re.I):
                pp = urllib.parse.urlparse(u)
                if (pp.netloc.replace("www.", "") == dominio
                        and not u.lower().endswith(".xml")):
                    encolar(pp._replace(fragment="", query="").geturl(), "")
        return


async def scrape(client: httpx.AsyncClient, dominio: str,
                 max_pages: int | None = None) -> dict:
    """Recorre el sitio entero y devuelve lo encontrado, por orden de utilidad.

    {"emails":[{email,score,kind}], "phones":[{value,kind}], "people":[str],
     "pages":[url], "text": str, "truncado": bool, "error": str|None}
    """
    dominio = (dominio or "").strip().lower().rstrip("/")
    if not dominio:
        return {"text": "", "emails": [], "phones": [], "people": [], "pages": [],
                "error": "El contacto no tiene sitio web."}
    if "://" in dominio:
        dominio = urllib.parse.urlparse(dominio).netloc or dominio
    dominio = dominio.replace("www.", "")

    if max_pages is None:
        max_pages = max_paginas()
    tope_texto = presupuesto_texto()
    hasta = time.monotonic() + presupuesto_tiempo()

    cache_robots: dict = {}
    textos: list[str] = []                     # lo leído, para que lo analice la IA
    largo_texto = 0
    emails: dict[str, tuple[int, bool]] = {}   # email -> (puntaje, en un mailto)
    telefonos: dict[str, str] = {}     # valor -> tipo
    visitadas: list[str] = []
    conocidas: set[str] = set()        # todo lo que ya se vio, visitado o no
    # La fila, por prioridad: {3: [...], 2: [...], 1: [...]}
    pendientes: dict[int, list[str]] = {3: [], 2: [], 1: []}

    home = None
    for candidata in (f"https://{dominio}/", f"https://www.{dominio}/", f"http://{dominio}/"):
        html = await _bajar(client, candidata)
        if html:
            home, home_html = candidata, html
            break
    if not home:
        return {"text": "", "emails": [], "phones": [], "people": [], "pages": [],
                "error": f"No se pudo abrir {dominio}."}

    base = f"{urllib.parse.urlparse(home).scheme}://{urllib.parse.urlparse(home).netloc}"
    rp = await _robots_permite(client, base, cache_robots)

    # Cuántas páginas se leyeron ya de cada sección. Sin esto, un sitio con
    # doscientos paquetes se lleva el presupuesto entero en /paquetes y no se
    # entera de que la empresa tiene una división corporativa.
    por_seccion: dict[str, int] = {}
    TOPE_SECCION = 6

    def _canonica(url: str) -> str:
        """La misma página escrita siempre igual, y siempre sobre el host
        que se sabe que responde."""
        pp = urllib.parse.urlparse(url)
        camino = pp.path or "/"
        if camino.endswith("/") and camino.count("/") > 1:
            camino = camino.rstrip("/")
        return urllib.parse.urljoin(base, camino)

    def _seccion(url: str) -> str:
        partes = [x for x in urllib.parse.urlparse(url).path.split("/") if x]
        return partes[0].lower() if partes else ""

    def encolar(url: str, texto_link: str) -> None:
        url = _canonica(url)
        pri = prioridad(url, texto_link)
        if not pri or url in conocidas:
            return
        seccion = _seccion(url)
        # El tope de sección no aplica a las páginas de contacto: esas son
        # pocas y son justamente las que hay que leer.
        if pri < 3 and seccion and por_seccion.get(seccion, 0) >= TOPE_SECCION:
            return
        por_seccion[seccion] = por_seccion.get(seccion, 0) + 1
        conocidas.add(url)
        pendientes[pri].append(url)

    def procesar(url: str, html: str) -> None:
        nonlocal largo_texto
        visitadas.append(url)
        pg = Pagina()
        try:
            pg.feed(html)
        except Exception:
            pass

        for href, _txt in pg.links:
            h = (href or "").strip()
            bajo = h.lower()
            if bajo.startswith("mailto:"):
                for e in EMAIL_RE.findall(urllib.parse.unquote(h[7:])):
                    if _email_valido(e, dominio):
                        # Estar en un mailto no lo vuelve el email de una
                        # persona, solo lo pone primero en la fila: el tipo
                        # sale del puntaje y la publicación explícita desempata.
                        emails[e.lower()] = (_puntaje(e, dominio), True)
            elif bajo.startswith("tel:"):
                t = _tel_valido(urllib.parse.unquote(h[4:]))
                if t:
                    telefonos.setdefault(t, "company")
            elif "wa.me/" in bajo or "api.whatsapp.com" in bajo or "web.whatsapp.com" in bajo:
                crudo = re.search(r"(?:wa\.me/|phone=)(\+?\d[\d\s\-]{6,})", h)
                if crudo:
                    t = _tel_valido(crudo.group(1))
                    if t:
                        telefonos[t] = "whatsapp"     # pisa: es mejor dato
            elif h and not bajo.startswith(("javascript:", "#", "tel:", "mailto:")):
                absoluta = urllib.parse.urljoin(url, h)
                p = urllib.parse.urlparse(absoluta)
                if p.netloc.replace("www.", "") != dominio or p.scheme not in ("http", "https"):
                    continue
                encolar(p._replace(fragment="", query="").geturl(), _txt)

        texto = pg.texto
        if texto.strip() and largo_texto < tope_texto:
            trozo = "--- " + url + chr(10) + texto[:TEXT_PER_PAGE]
            trozo = trozo[:max(0, tope_texto - largo_texto)]
            textos.append(trozo)
            largo_texto += len(trozo)
        for e in EMAIL_RE.findall(texto):
            if _email_valido(e, dominio) and e.lower() not in emails:
                emails[e.lower()] = (_puntaje(e, dominio), False)
        for crudo in TEL_TEXTO.findall(texto):
            t = _tel_valido(crudo)
            if t and t not in telefonos:
                telefonos[t] = "company"

    conocidas.add(home)
    conocidas.add(urllib.parse.urljoin(base, "/"))
    procesar(home, home_html)
    await _sumar_sitemap(client, base, dominio, encolar)

    def siguientes(cuantas: int) -> list[str]:
        """Saca de la fila las más útiles que estén permitidas."""
        salida = []
        for pri in (3, 2, 1):
            while pendientes[pri] and len(salida) < cuantas:
                url = pendientes[pri].pop(0)
                if rp and not rp.can_fetch(UA, url):
                    continue
                salida.append(url)
            if len(salida) >= cuantas:
                break
        return salida

    truncado = False
    while len(visitadas) < max_pages:
        if time.monotonic() > hasta or largo_texto >= tope_texto:
            truncado = True
            break
        tanda = siguientes(min(A_LA_VEZ, max_pages - len(visitadas)))
        if not tanda:
            break
        bajadas = await asyncio.gather(*[_bajar(client, u) for u in tanda])
        for url, html in zip(tanda, bajadas):
            if html:
                procesar(url, html)
        await asyncio.sleep(ESPERA)
    else:
        truncado = bool(pendientes[3] or pendientes[2] or pendientes[1])

    ordenados = sorted(emails.items(), key=lambda kv: (-kv[1][0], not kv[1][1], kv[0]))
    personas = []
    for e, _v in ordenados:
        n = _nombre_desde_email(e)
        if n and n not in personas:
            personas.append(n)

    return {
        "text": (chr(10) * 2).join(textos),
        "truncado": truncado,
        "emails": [{"email": e, "score": s, "explicit": expl,
                    "kind": "persona" if s == 4 else ("area" if s == 3 else "externo")}
                   for e, (s, expl) in ordenados],
        "phones": [{"value": v, "kind": k} for v, k in
                   sorted(telefonos.items(), key=lambda kv: kv[1] != "whatsapp")],
        "people": personas,
        "pages": visitadas,
        "error": None if (emails or telefonos) else "No se encontró contacto publicado.",
    }
