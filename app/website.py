"""Scraping del sitio del negocio para sacarle emails y teléfonos.

Google Maps da el dominio pero nunca el email. Acá se entra al sitio, se buscan
las páginas donde suele estar el contacto y se extrae lo publicado.

Es scraping cortés: se respeta robots.txt, se dice quién es el bot, se leen
pocas páginas por sitio y se espera entre una y otra. No cuesta créditos, así
que puede correr sobre toda la lista sin pensarlo.
"""
import asyncio
import re
import urllib.parse
from html.parser import HTMLParser
from urllib.robotparser import RobotFileParser

import httpx

UA = "B2X/1.0 (prospeccion B2B; contacto por el sitio)"
TIMEOUT = 12.0
MAX_PAGES = 4            # la home + 3 candidatas
TEXT_PER_PAGE = 12_000   # texto que se guarda por página para que lo lea la IA
PAGE_BYTES = 600_000     # más que esto no es una página, es una descarga

# Páginas donde vive el contacto, en español y en inglés.
PISTAS_CONTACTO = re.compile(
    r"contact|contacto|contactenos|contáctenos|contactanos|escribinos|"
    r"about|nosotros|quienes|quiénes|empresa|equipo|team|staff|directorio|"
    r"atencion|atención|soporte|ayuda|reservas", re.I)

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


async def scrape(client: httpx.AsyncClient, dominio: str, max_pages: int = MAX_PAGES) -> dict:
    """Recorre el sitio y devuelve lo encontrado, ordenado por utilidad.

    {"emails":[{email,score,kind}], "phones":[{value,kind}], "people":[str],
     "pages":[url], "error": str|None}
    """
    dominio = (dominio or "").strip().lower().rstrip("/")
    if not dominio:
        return {"text": "", "emails": [], "phones": [], "people": [], "pages": [],
                "error": "El contacto no tiene sitio web."}
    if "://" in dominio:
        dominio = urllib.parse.urlparse(dominio).netloc or dominio
    dominio = dominio.replace("www.", "")

    cache_robots: dict = {}
    textos: list[str] = []                     # lo leído, para que lo analice la IA
    emails: dict[str, tuple[int, bool]] = {}   # email -> (puntaje, en un mailto)
    telefonos: dict[str, str] = {}     # valor -> tipo
    visitadas: list[str] = []
    pendientes: list[str] = []

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

    def procesar(url: str, html: str) -> None:
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
                limpia = p._replace(fragment="", query="").geturl()
                if (limpia not in visitadas and limpia not in pendientes
                        and PISTAS_CONTACTO.search(limpia + " " + (_txt or ""))):
                    pendientes.append(limpia)

        texto = pg.texto
        if texto.strip():
            textos.append("--- " + url + chr(10) + texto[:TEXT_PER_PAGE])
        for e in EMAIL_RE.findall(texto):
            if _email_valido(e, dominio) and e.lower() not in emails:
                emails[e.lower()] = (_puntaje(e, dominio), False)
        for crudo in TEL_TEXTO.findall(texto):
            t = _tel_valido(crudo)
            if t and t not in telefonos:
                telefonos[t] = "company"

    procesar(home, home_html)

    while pendientes and len(visitadas) < max_pages:
        url = pendientes.pop(0)
        if rp and not rp.can_fetch(UA, url):
            continue
        await asyncio.sleep(0.4)
        html = await _bajar(client, url)
        if html:
            procesar(url, html)

    ordenados = sorted(emails.items(), key=lambda kv: (-kv[1][0], not kv[1][1], kv[0]))
    personas = []
    for e, _v in ordenados:
        n = _nombre_desde_email(e)
        if n and n not in personas:
            personas.append(n)

    return {
        "text": (chr(10) * 2).join(textos)[:40_000],
        "emails": [{"email": e, "score": s, "explicit": expl,
                    "kind": "persona" if s == 4 else ("area" if s == 3 else "externo")}
                   for e, (s, expl) in ordenados],
        "phones": [{"value": v, "kind": k} for v, k in
                   sorted(telefonos.items(), key=lambda kv: kv[1] != "whatsapp")],
        "people": personas,
        "pages": visitadas,
        "error": None if (emails or telefonos) else "No se encontró contacto publicado.",
    }
