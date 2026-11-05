"""Prueba de pantalla de B2K, en computador y en teléfono.

Levanta la app sobre una copia de data/b2x.db (tests/ui/servidor.py), la
recorre con Playwright y termina en «TODO BIEN» o con la lista de fallas.

    python tests/ui/smoke.py            # todo
    python tests/ui/smoke.py base       # solo un grupo
"""
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
RAIZ = Path(__file__).resolve().parents[2]
PUERTO = 8781
URL = f"http://127.0.0.1:{PUERTO}"
TAMANOS = {"pc": {"width": 1440, "height": 900}, "cel": {"width": 390, "height": 844}}
EMOJI = re.compile("[←-⯿\U0001f300-\U0001faff]")

fallas: list[str] = []


def ver(cond, que, donde=""):
    print(("  ok  " if cond else "  XX  ") + (f"[{donde}] " if donde else "") + que)
    if not cond:
        fallas.append(f"[{donde}] {que}")


def entrar(pg):
    pg.goto(URL + "/login")
    pg.fill("#pw", "prueba")
    pg.click("button[type=submit]")
    pg.wait_for_url(URL + "/")
    pg.wait_for_timeout(1200)


def sin_desborde(pg, donde, que):
    ancho = pg.evaluate("() => document.documentElement.scrollWidth - innerWidth")
    ver(ancho <= 1, f"{que}: sin desplazamiento horizontal ({ancho}px de más)", donde)


# --------------------------------------------------------------- grupos
def base(pg, donde):
    """Task 1: íconos en vez de emojis, y el ⓘ desplegable."""
    texto_menu = pg.inner_text("nav.steps")
    ver(not EMOJI.search(texto_menu), "el menú no tiene emojis", donde)
    pasos = pg.locator("nav.steps .step")
    con_icono = pg.locator("nav.steps .step .ico svg.ic").count()
    ver(pasos.count() > 0 and con_icono == pasos.count(),
        f"cada sección del menú tiene su ícono ({con_icono}/{pasos.count()})", donde)

    info = pg.locator(".info-btn:visible").first
    ver(info.count() == 1, "hay al menos un ⓘ en la pantalla", donde)
    if info.count():
        info.click()
        ver(info.get_attribute("aria-expanded") == "true"
            and info.locator(".pop").is_visible(), "el ⓘ se abre al tocarlo", donde)
        pg.keyboard.press("Escape")
        ver(info.get_attribute("aria-expanded") == "false", "se cierra con Escape", donde)
        info.click()
        pg.mouse.click(5, TAMANOS["pc"]["height"] - 5 if donde == "pc" else 400)
        ver(info.get_attribute("aria-expanded") == "false", "se cierra tocando afuera", donde)
    sin_desborde(pg, donde, "Contactos")


def ir(pg, panel):
    pg.click(f".step[data-panel={panel}]")
    pg.wait_for_timeout(500)


def general(pg, donde):
    """Task 2: títulos que dicen qué hacer, métricas solo en Contactos, ✓."""
    ir(pg, "contacts")
    ver(pg.locator("#metrics").is_visible(), "métricas visibles en Contactos", donde)
    ver(pg.locator("#metrics .metr").count() == 1, "métricas como una fila de pastillas", donde)
    for panel in ("mail", "results", "settings"):
        ir(pg, panel)
        ver(not pg.locator("#metrics").is_visible(), f"sin métricas en {panel}", donde)
    for panel in ("import", "enrich", "contacts", "mail", "results", "settings"):
        ir(pg, panel)
        cab = pg.locator(f"#panel-{panel} .pantalla-cab").first
        ver(cab.count() == 1 and cab.locator("h1").is_visible(),
            f"{panel}: abre con un título", donde)
        if panel != "settings":
            ver(cab.locator(".info-btn").count() == 1, f"{panel}: el título tiene su ⓘ", donde)
    for panel in ("import", "enrich", "results"):
        ir(pg, panel)
        largos = pg.evaluate(f"""() => [...document.querySelectorAll(
            '#panel-{panel} .lede, #panel-{panel} p.help')].filter(p =>
            p.offsetParent && !p.closest('.pop') && p.textContent.trim().length > 140).length""")
        ver(largos == 0, f"{panel}: sin párrafos largos a la vista ({largos})", donde)
    ir(pg, "enrich")
    angostos = pg.evaluate("""() => [...document.querySelectorAll('.alert')].filter(a =>
        a.offsetParent && a.getBoundingClientRect().width < 120).length""")
    ver(angostos == 0, f"ningún aviso queda encogido ({angostos})", donde)
    ir(pg, "contacts")
    cab = pg.locator("#panel-contacts .pantalla-cab").bounding_box()
    met = pg.locator("#metrics").bounding_box()
    ver(cab and met and met["y"] > cab["y"], "las pastillas van debajo del título", donde)
    ir(pg, "mail")
    primero = pg.locator("#panel-mail .pasos, #mail-subject").first.bounding_box()
    ver(primero is not None and primero["y"] < TAMANOS[donde]["height"],
        "Correos: lo primero que se hace está en la primera pantalla", donde)
    ir(pg, "import")
    ver(pg.locator(".step[data-panel=import] .hecho").count() == 1,
        "Cargar lleva ✓ (hay contactos)", donde)
    ver(pg.locator(".step[data-panel=results] .hecho").count() == 0,
        "Resultados nunca lleva ✓", donde)
    # RF 1: con los correos apagados no queda nada que lleve a Correos.
    pg.evaluate("""async () => { const f = new FormData(); f.append('correos', '0');
        await fetch('/api/funciones', {method: 'POST', body: f}); }""")
    pg.reload(); pg.wait_for_timeout(1200)
    for panel in ("mail", "results"):
        ver(not pg.locator(f".step[data-panel={panel}]").is_visible(),
            f"correos apagados: no se ve el paso {panel}", donde)
    pg.evaluate("""async () => { const f = new FormData(); f.append('correos', '1');
        await fetch('/api/funciones', {method: 'POST', body: f}); }""")
    pg.reload(); pg.wait_for_timeout(1200)
    sin_desborde(pg, donde, "después de recorrer")


def marcar(pg, n):
    cajas = pg.locator("#ctbody input[type=checkbox]")
    for i in range(n):
        cajas.nth(i).check()
    pg.wait_for_timeout(300)


def contactos(pg, donde):
    """Task 3: barra de selección, completar datos y ventana de Kommo."""
    ir(pg, "contacts")
    pg.evaluate("() => clearSelection()")
    ver(not pg.locator("#completar .completar-tiles").is_visible(),
        "sin marcados no se ofrece completar datos (RF 2)", donde)
    marcar(pg, 3)
    bar = pg.locator("#selbar")
    ver("3 marcados" in bar.inner_text(), "la barra dice «3 marcados»", donde)
    n_ok = pg.evaluate("""() => { const n = document.querySelector('#selbar .n');
        return n.scrollWidth <= n.clientWidth + 1; }""")
    ver(n_ok, "«3 marcados» entra entero", donde)
    ver(pg.locator("#selbar-mail").is_visible() and "Escribirles" in pg.inner_text("#selbar-mail"),
        "se ve «Escribirles»", donde)
    ver(pg.locator("#selbar-go").is_visible() and "Kommo" in pg.inner_text("#selbar-go"),
        "se ve «Mandar a Kommo»", donde)
    ver(not pg.locator("#selbar .mas-menu button.peligro").is_visible(),
        "«Borrar» no está a la vista", donde)
    pg.click("#selbar .mas-btn")
    ver(pg.locator("#selbar .mas-menu button.peligro").is_visible(), "⋯ muestra Borrar", donde)
    ver(pg.locator("#selbar .mas-menu button:has-text('detalle')").count() == 0,
        "con 3 marcados no ofrece «Ver el detalle»", donde)
    # RF 5: la lista se vuelve a dibujar con el ⋯ abierto.
    pg.evaluate("() => loadContacts()")
    pg.wait_for_timeout(700)
    ver(pg.locator(".mas-menu").count() == 1, "un solo menú ⋯ después de recargar (RF 5)", donde)
    pg.keyboard.press("Escape")
    ver(pg.locator("#completar .completar-tiles").is_visible(),
        "con marcados se ofrece completar datos", donde)
    ver(pg.locator("#completar .completar-tile").count() == 3,
        "tres formas de completar, cada una con su costo", donde)
    # RF 3: nada tapa el último contacto ni la barra de actividad.
    pg.evaluate("""() => { const t = document.querySelector('.card.list > .tbl-scroll');
        if (t) t.scrollTop = t.scrollHeight; window.scrollTo(0, document.documentElement.scrollHeight); }""")
    pg.wait_for_timeout(300)
    fila = pg.evaluate("""() => { const f = [...document.querySelectorAll('#ctbody tr')].pop();
        const t = document.querySelector('.card.list > .tbl-scroll');
        return f ? Math.min(f.getBoundingClientRect().bottom, t.getBoundingClientRect().bottom) : 0; }""")
    tope = pg.evaluate("() => document.getElementById('selbar').getBoundingClientRect().top")
    ver(fila <= tope + 1, f"el último contacto queda arriba de la barra (RF 3) {fila:.0f}/{tope:.0f}", donde)
    pg.evaluate("""() => { const a = document.getElementById('actividad'); a.hidden = false;
        a.innerHTML = '<div class=act-item><span class=act-titulo>prueba</span></div>'; }""")
    choque = pg.evaluate("""() => { const a = document.getElementById('actividad').getBoundingClientRect();
        const s = document.getElementById('selbar').getBoundingClientRect();
        return !(a.bottom <= s.top || a.top >= s.bottom || a.right <= s.left || a.left >= s.right); }""")
    ver(not choque, "la barra de actividad no tapa la de selección (RF 3)", donde)
    pg.evaluate("() => { document.getElementById('actividad').hidden = true; }")
    # Mandar a Kommo: la ventana con etiqueta, embudo y etapa.
    pg.click("#selbar-go")
    pg.wait_for_timeout(500)
    modal = pg.locator("#kommo")
    ver(modal.is_visible(), "«Mandar a Kommo» abre su ventana", donde)
    ver(pg.locator("#kommo #ghl-tag").is_visible(), "la ventana trae la etiqueta", donde)
    ver(pg.locator("#kommo #kommo-aviso").is_visible(),
        "sin Kommo configurado, la ventana lo avisa (RF 4)", donde)
    pg.keyboard.press("Escape")
    ver(not modal.is_visible(), "la ventana se cierra con Escape", donde)
    # Con Kommo conectado: manda y muestra el resultado en la misma ventana.
    pg.route("**/api/ghl/send", lambda r: r.fulfill(json={"sent": 3, "results": [],
        "failed": 0, "skipped": 0, "already_in_crm": 0, "pipeline_configured": False}))
    pg.evaluate("() => { KOMMO_OK = true; }")
    pg.click("#selbar-go"); pg.wait_for_timeout(400)
    ver("Mandar 3" in pg.inner_text("#btn-ghl"), "el botón dice «Mandar 3»", donde)
    pg.click("#btn-ghl"); pg.wait_for_timeout(800)
    ver("3 contacto(s)" in pg.inner_text("#kommo #ghl-result"),
        "el resultado se ve en la ventana", donde)
    pg.unroute("**/api/ghl/send")
    pg.evaluate("() => { KOMMO_OK = false; cerrarKommo(); }")
    pg.evaluate("() => clearSelection()")
    marcar(pg, 1)
    pg.click("#selbar .mas-btn")
    ver(pg.locator("#selbar .mas-menu button:has-text('detalle')").count() == 1,
        "con 1 marcado ofrece «Ver el detalle»", donde)
    pg.keyboard.press("Escape")
    estados = pg.locator("#ctbody .pill .ic").count()
    ver(estados > 0, f"los estados llevan ícono ({estados})", donde)
    ver(not EMOJI.search(pg.inner_text("#panel-contacts")), "Contactos sin emojis", donde)
    pg.evaluate("() => clearSelection()")
    sin_desborde(pg, donde, "Contactos con marcados")


GRUPOS = {"base": base, "general": general, "contactos": contactos}


def main():
    elegidos = sys.argv[1:] or list(GRUPOS)
    tmp = Path(tempfile.mkdtemp(prefix="b2k-ui-"))
    copia = tmp / "b2x.db"
    shutil.copy(RAIZ / "data" / "b2x.db", copia)
    srv = subprocess.Popen([sys.executable, str(RAIZ / "tests" / "ui" / "servidor.py"),
                            str(copia), str(PUERTO)], cwd=RAIZ)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            for donde, vp in TAMANOS.items():
                pg = b.new_page(viewport=vp)
                errores = []
                pg.on("pageerror", lambda e: errores.append(str(e)))
                pg.on("console", lambda m: m.type == "error" and errores.append(m.text))
                for _ in range(40):
                    try:
                        entrar(pg)
                        break
                    except Exception:
                        time.sleep(0.5)
                print(f"== {donde}")
                for g in elegidos:
                    GRUPOS[g](pg, donde)
                ver(not errores, f"sin errores de consola {errores[:2]}", donde)
                pg.close()
            b.close()
    finally:
        srv.terminate()
        srv.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + ("TODO BIEN" if not fallas else f"{len(fallas)} FALLAS"))
    sys.exit(1 if fallas else 0)


if __name__ == "__main__":
    main()
