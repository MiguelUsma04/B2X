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


def correos(pg, donde):
    """Task 4: Correos en cuatro pasos, con el pie que dice qué va a salir."""
    ir(pg, "contacts")
    pg.evaluate("() => clearSelection()")
    ir(pg, "mail")
    pasos = pg.locator("#panel-mail .pasos .paso")
    ver(pasos.count() == 4, f"Correos tiene 4 pasos ({pasos.count()})", donde)
    ver(pg.locator("#seq-on").is_checked(), "la secuencia de tres correos viene prendida", donde)
    ver("marcá contactos" in pg.inner_text("#pie-correos").lower(),
        "sin marcados, el pie pide marcar contactos (RF 2)", donde)
    ver(pg.locator("#btn-mail").is_disabled() or not pg.locator("#btn-mail").is_visible(),
        "sin marcados no se puede programar (RF 2)", donde)
    ver(not pg.locator("#selbar").is_visible(), "la barra de selección no aparece en Correos", donde)
    # Marcar 3 desde Contactos y entrar con «Escribirles».
    ir(pg, "contacts")
    marcar(pg, 3)
    pg.click("#selbar-mail"); pg.wait_for_timeout(500)
    ver(pg.locator(".paso-bloque[data-paso='1']").is_visible(), "entra en el paso 1", donde)
    pg.click("#seg-texto"); pg.wait_for_timeout(300)
    ver(pg.locator(".paso-bloque[data-paso='2']").is_visible()
        and pg.locator("#mail-subject").is_visible(), "elegir Texto lleva al paso 2 con el asunto", donde)
    pg.fill("#mail-subject", "Una idea para {{empresa}}")
    pg.fill("#mail-body", "Hola {{nombre}}")
    pasos.nth(2).click(); pg.wait_for_timeout(300)
    ver(pg.locator(".paso-bloque[data-paso='3']").is_visible(), "se puede ir directo al paso 3", donde)
    pg.check("#seq-on"); pg.wait_for_timeout(600)
    ver(all(pg.locator(f"#{i}").is_visible() for i in ("seq-d2", "seq-d3", "seq-dfin")),
        "la línea de tiempo trae los tres plazos", donde)
    ver(not pg.locator("#seq-2").is_visible(), "el texto del correo 2 está cerrado", donde)
    pg.click(".tl-nodo[data-seq='2']"); pg.wait_for_timeout(300)
    ver(pg.locator("#seq-2").is_visible(), "tocar «Correo 2» abre su texto", donde)
    pasos.nth(3).click(); pg.wait_for_timeout(300)
    pie = pg.inner_text("#pie-correos")
    ver("3 empresas" in pie, f"el pie cuenta empresas: {pie[:60]!r}", donde)
    ver("hasta 3 correos" in pie, "el pie avisa del seguimiento", donde)
    ver(pg.locator("#btn-mail").is_visible() and not pg.locator("#btn-mail").is_disabled(),
        "con todo listo se puede programar", donde)
    pasos.nth(1).click(); pg.fill("#mail-subject", ""); pasos.nth(3).click(); pg.wait_for_timeout(200)
    ver(pg.locator("#btn-mail").is_disabled() and "asunto" in pg.inner_text("#pie-correos"),
        "sin asunto no se puede, y el pie dice qué falta", donde)
    pasos.nth(1).click(); pg.fill("#mail-subject", "Una idea para {{empresa}}"); pasos.nth(3).click()
    # Lo que viaja al programar sigue siendo lo de siempre.
    crudo = {}
    pg.route("**/api/mail/preview", lambda r: r.fulfill(json={"sendable": 3, "unknown_vars": []}))
    pg.route("**/api/mail/schedule", lambda r: (crudo.update(b=r.request.post_data or ""),
              r.fulfill(json={"started": True, "queued": 3, "secuencia": True})))
    pg.evaluate("() => { ask = async () => true; }")
    pg.click("#btn-mail"); pg.wait_for_timeout(1200)
    for campo in ("secuencia", "cuerpo2", "cuerpo3", "dias2", "dias3", "dias_fin", "subject"):
        ver(f'name="{campo}"' in crudo.get("b", ""), f"el envío lleva {campo}", donde)
    pg.unroute("**/api/mail/preview"); pg.unroute("**/api/mail/schedule")
    if donde == "cel":
        p = pg.locator("#pie-correos").bounding_box()
        t = pg.locator("nav.steps").bounding_box()
        ver(p and t and p["y"] + p["height"] <= t["y"] + 1, "el pie queda arriba de la barra de secciones", donde)
    ver(not EMOJI.search(pg.inner_text("#panel-mail")), "Correos sin emojis", donde)
    sin_desborde(pg, donde, "Correos")
    pg.reload(); pg.wait_for_timeout(1200)


CLAVES_AJUSTES = ("buzones", "horario", "salud", "embudo", "etapas", "repetidos",
                  "historial", "base", "ayuda")


def ajustes(pg, donde):
    """Task 5: Ajustes en tarjetas que dicen cómo está cada cosa."""
    ir(pg, "settings")
    pg.wait_for_timeout(1500)
    tarjetas = pg.locator("#ajustes-grilla .aj")
    ver(tarjetas.count() == 9, f"9 tarjetas ({tarjetas.count()})", donde)
    ver(pg.locator("#ajustes-grilla .aj-grupo").count() == 3, "en 3 grupos", donde)
    ver(pg.locator("#ajustes-grilla .aj svg.ic").count() == 9, "cada tarjeta con su ícono", donde)
    vacias = pg.evaluate("""() => [...document.querySelectorAll('#ajustes-grilla .aj .est')]
        .filter(e => !e.textContent.trim()).length""")
    ver(vacias == 0, f"cada tarjeta dice cómo está ({vacias} vacías)", donde)
    ver("1 de 1" in pg.inner_text(".aj[data-aj='buzones'] .est"),
        "Buzones cuenta el buzón que hay", donde)
    izq = pg.evaluate("""() => { const t = document.querySelector('.aj[data-aj=buzones]');
        return t.querySelector('b').getBoundingClientRect().left - t.getBoundingClientRect().left; }""")
    ver(izq < 30, f"el contenido de la tarjeta va alineado a la izquierda ({izq:.0f}px)", donde)
    ver(pg.locator("#panel-settings .segmented").count() == 0, "ya no hay pestañas", donde)
    ver(not pg.locator("#f-correos").is_visible(), "la Zona de cuidado empieza cerrada", donde)
    for clave in CLAVES_AJUSTES:
        pg.click(f".aj[data-aj='{clave}']"); pg.wait_for_timeout(500)
        abierta = pg.locator(f"[data-vista='{clave}']").first
        ver(abierta.is_visible() and not pg.locator("#ajustes-grilla").is_visible()
            and pg.locator("#aj-volver").is_visible(), f"«{clave}» se abre con «← Ajustes»", donde)
        pg.click("#aj-volver"); pg.wait_for_timeout(300)
    ver(pg.locator("#ajustes-grilla").is_visible(), "«← Ajustes» vuelve a la grilla", donde)
    pg.click(".aj[data-aj='horario']"); pg.wait_for_timeout(400)
    ver(pg.locator("#v-desde").is_visible(), "Horario muestra sus campos", donde)
    pg.click("#aj-volver")
    pg.click("#zona-cuidado summary"); pg.wait_for_timeout(300)
    ver(pg.locator("#f-correos").is_visible()
        and pg.locator("text=Olvidar lo enviado al CRM").is_visible(), "la Zona de cuidado se abre", donde)
    # RF 1: apagar pide confirmar y esconde todo lo de correos; prender no pregunta.
    pg.click("#f-correos"); pg.wait_for_timeout(400)
    ver(pg.locator("#ask.open").count() == 1, "apagar los correos pide confirmar", donde)
    pg.click("#ask-foot button.danger, #ask-foot button.primary"); pg.wait_for_timeout(800)
    ver(not pg.locator(".step[data-panel=mail]").is_visible()
        and not pg.locator(".aj[data-aj='buzones']").is_visible(),
        "apagados: sin Correos ni tarjetas de envío (RF 1)", donde)
    pg.click("#f-correos"); pg.wait_for_timeout(600)
    ver(pg.locator("#ask.open").count() == 0 and pg.locator(".step[data-panel=mail]").is_visible(),
        "prenderlos no pregunta y vuelve Correos", donde)
    ver(not EMOJI.search(pg.inner_text("#panel-settings")), "Ajustes sin emojis", donde)
    sin_desborde(pg, donde, "Ajustes")


def barras(pg, donde):
    """Final: las barras de abajo no tapan nada, en ninguna pantalla."""
    if donde != "cel":
        return
    ir(pg, "contacts")
    pg.evaluate("() => clearSelection()")
    marcar(pg, 2)
    # Important 1: en otra pantalla, con marcados, queda el lugar de las pestañas.
    ir(pg, "settings")
    abajo = pg.evaluate("() => parseFloat(getComputedStyle(document.querySelector('.wrap')).paddingBottom)")
    ver(abajo >= 64, f"Ajustes con marcados deja lugar abajo ({abajo:.0f}px)", donde)
    # Important 2: la selección cambia estando en otra pantalla.
    ir(pg, "enrich")
    pg.evaluate("() => { SELECTED.add(ALL_IDS[2]); updateSelInfo(); }")
    pg.wait_for_timeout(300)
    ir(pg, "contacts")
    pg.wait_for_timeout(300)
    h = pg.evaluate("() => parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--selbar-h'))")
    ver(h > 0, f"la altura de la barra no queda en cero ({h})", donde)
    # Important 3: con la barra de actividad a la vista, el último contacto se ve.
    pg.evaluate("""() => { clearTimeout(ACT_TIMER); ACT_TIMER = null;
        const a = document.getElementById('actividad'); a.hidden = false;
        a.innerHTML = '<div class=act-item><span class=act-titulo>prueba</span>'
          + '<span class=act-pie><span class=act-barra></span></span></div>'; medirBarras(); }""")
    pg.wait_for_timeout(300)
    pg.evaluate("""() => { const t = document.querySelector('.card.list > .tbl-scroll');
        if (t) t.scrollTop = t.scrollHeight; window.scrollTo(0, document.documentElement.scrollHeight); }""")
    pg.wait_for_timeout(300)
    r = pg.evaluate("""() => { const f = [...document.querySelectorAll('#ctbody tr')].pop();
        const t = document.querySelector('.card.list > .tbl-scroll');
        const fin = Math.min(f.getBoundingClientRect().bottom, t.getBoundingClientRect().bottom);
        const a = document.getElementById('actividad').getBoundingClientRect().top;
        const s = document.getElementById('selbar').getBoundingClientRect().top;
        return [fin, Math.min(a, s)]; }""")
    ver(r[0] <= r[1] + 1, f"el último contacto queda arriba de las dos barras {r[0]:.0f}/{r[1]:.0f}", donde)
    pg.evaluate("() => { document.getElementById('actividad').hidden = true; medirBarras(); clearSelection(); refrescarActividad(); }")


def kommo_tanda(pg, donde):
    """Final: el embudo y la etapa de la ventana viajan con el envío."""
    ir(pg, "contacts")
    pg.evaluate("() => clearSelection()")
    marcar(pg, 2)
    crudo = {}
    pg.route("**/api/ghl/send", lambda r: (crudo.update(b=r.request.post_data or ""),
              r.fulfill(json={"sent": 2, "results": []})))
    pg.evaluate("() => { KOMMO_OK = true; }")
    pg.click("#selbar-go"); pg.wait_for_timeout(400)
    pg.evaluate("""() => { document.getElementById('pipeline-box').innerHTML =
        '<select id=pipe-sel><option value=11 selected>A</option></select>'
        + '<select id=stage-sel><option value=22 selected>B</option></select>'; }""")
    pg.click("#btn-ghl"); pg.wait_for_timeout(600)
    b = crudo.get("b", "")
    ver('name="pipeline_id"' in b and "11" in b and 'name="stage_id"' in b and "22" in b,
        "el envío lleva el embudo y la etapa de la ventana", donde)
    pg.unroute("**/api/ghl/send")
    pg.evaluate("() => { KOMMO_OK = false; cerrarKommo(); clearSelection(); }")


def dashboard(pg, donde):
    """El dashboard: la base, los correos, el embudo, las secuencias y los buzones."""
    pg.reload(); pg.wait_for_timeout(1500)
    primero = pg.locator("nav.steps .step").first.get_attribute("data-panel")
    ver(primero == "dashboard", f"el dashboard es lo primero del menú ({primero})", donde)
    ver(pg.locator("#panel-dashboard").is_visible(), "la app abre en el dashboard", donde)
    m = pg.evaluate("async () => (await (await fetch('/api/metrics')).json()).contactable")
    ver(str(m) in pg.inner_text("#dash-base .hero .v"), f"el número grande es el de contactables ({m})", donde)
    for sec in ("#dash-base", "#dash-correos", "#dash-embudo", "#dash-seq", "#dash-buzones"):
        ver(pg.locator(sec).is_visible(), f"se ve {sec}", donde)
    ver(pg.locator(".periodo button").count() == 3, "tres períodos para elegir", donde)
    pg.click(".periodo button[data-dias='7']"); pg.wait_for_timeout(800)
    ver(pg.locator("#dash-chart [data-dia]").count() == 7, "con 7 días, el gráfico tiene 7 días", donde)
    ver(pg.locator(".periodo button[data-dias='7']").get_attribute("aria-pressed") == "true",
        "el período elegido queda marcado", donde)
    ver(pg.locator("#dash-correos .leyenda").count() == 1, "el gráfico tiene leyenda", donde)
    pg.locator("#dash-chart svg").scroll_into_view_if_needed(); pg.wait_for_timeout(200)
    caja = pg.locator("#dash-chart svg").bounding_box()
    pg.mouse.move(caja["x"] + caja["width"] * 0.6, caja["y"] + caja["height"] / 2)
    pg.wait_for_timeout(300)
    ver(pg.locator("#dash-tip").is_visible() and "Enviados" in pg.inner_text("#dash-tip"),
        "al pasar por el gráfico aparece el detalle del día", donde)
    pg.click("#dash-ver-tabla"); pg.wait_for_timeout(300)
    ver(pg.locator("#dash-tabla tbody tr").count() == 7, "el gráfico también se ve como tabla", donde)
    ver(pg.locator("#dash-embudo .emb-fila").count() == 6, "el embudo tiene sus 6 pasos", donde)
    pg.evaluate("""() => dashBuzones({por_buzon: [{from_email: 'gerencia.comercial@gaptravelcrm.com',
        enviados: 90, rebotes: 3, active: 1}]})""")
    corte = pg.evaluate("""() => { const t = document.querySelector('#dash-buzones-cuerpo table');
        return t ? t.scrollWidth - t.parentElement.clientWidth : 0; }""")
    ver(corte <= 1, f"la tabla de buzones entra entera ({corte}px de más)", donde)
    pg.click("#dash-base .tile:has-text('Con email')"); pg.wait_for_timeout(800)
    ver(pg.locator("#panel-contacts").is_visible() and pg.input_value("#f-reach") == "email",
        "tocar «Con email» lleva a Contactos filtrado", donde)
    ir(pg, "dashboard")
    ver(not EMOJI.search(pg.inner_text("#panel-dashboard")), "el dashboard sin emojis", donde)
    sin_desborde(pg, donde, "Dashboard")
    pg.evaluate("""async () => { const f = new FormData(); f.append('correos', '0');
        await fetch('/api/funciones', {method: 'POST', body: f}); }""")
    pg.reload(); pg.wait_for_timeout(1500)
    ver(not pg.locator("#dash-correos").is_visible() and pg.locator("#dash-base").is_visible(),
        "con los correos apagados quedan la base y el embudo, sin lo de correos", donde)
    pg.evaluate("""async () => { const f = new FormData(); f.append('correos', '1');
        await fetch('/api/funciones', {method: 'POST', body: f}); }""")
    pg.reload(); pg.wait_for_timeout(1200)


def borradores(pg, donde):
    """Con 45 borradores la pantalla no puede ser un scroll de 45 correos."""
    import json
    lista = [{"contact_id": 9000 + i, "company_name": f"Empresa {i:02d}",
              "email": f"x{i}@e{i}.co", "asunto": f"Asunto {i}", "cuerpo": f"Hola {i}\n\nTexto",
              "cuerpo2": "Dos", "cuerpo3": "Tres", "caracteres": 300, "a_quien": "a Ana",
              "con_ficha": True, "estado": "pendiente", "error": None} for i in range(45)]
    lista[3]["estado"] = "aprobado"
    lista[4]["error"] = "no salió"

    def get(r):
        r.fulfill(json={"items": lista, "max_caracteres": 450})

    def marcar(r):
        cid = int(r.request.url.rstrip("/").split("/")[-1])
        estado = re.search(r'name="estado"\r\n\r\n(\w+)', r.request.post_data or "").group(1)
        next(b for b in lista if b["contact_id"] == cid)["estado"] = estado
        r.fulfill(json={"ok": True})

    pg.route("**/api/mail/borradores", get)
    pg.route(re.compile(r".*/api/mail/borradores/\d+$"), marcar)
    ir(pg, "mail")
    pg.click("#seg-ia"); pg.wait_for_timeout(300)
    pg.evaluate("() => cargarBorradores()"); pg.wait_for_timeout(500)

    filas = pg.locator("#ia-lista .borrador")
    ver(filas.count() == 20, f"se ven 20 por página, no los 45 ({filas.count()})", donde)
    ver(pg.locator("#ia-lista .borrador-cuerpo:visible").count() == 1,
        "solo un correo está abierto a la vez", donde)
    ver(pg.locator("#ia-lista .borrador.abierto").first.inner_text().startswith("Empresa 00"),
        "abre el primero sin revisar", donde)
    pg.locator("#ia-lista .borrador-fila").nth(1).click(); pg.wait_for_timeout(200)
    ver(pg.locator("#ia-lista .borrador.abierto").count() == 1
        and "Empresa 01" in pg.locator("#ia-lista .borrador.abierto").inner_text(),
        "tocar otro renglón lo abre y cierra el anterior", donde)
    pg.click("#ia-lista .borrador.abierto .btn-aprobar-sig"); pg.wait_for_timeout(600)
    ver(lista[1]["estado"] == "aprobado", "«Aprobar y seguir» lo aprueba", donde)
    ver("Empresa 02" in pg.locator("#ia-lista .borrador.abierto").inner_text(),
        "y abre el siguiente sin revisar", donde)

    pg.click("#ia-filtros [data-filtro=aprobado]"); pg.wait_for_timeout(300)
    ver(filas.count() == 2, f"el filtro Aprobados deja los 2 ({filas.count()})", donde)
    pg.click("#ia-filtros [data-filtro=error]"); pg.wait_for_timeout(300)
    ver(filas.count() == 1, "el filtro No salieron deja 1", donde)
    pg.click("#ia-filtros [data-filtro=todos]"); pg.wait_for_timeout(300)
    pg.fill("#ia-busca", "Empresa 33"); pg.wait_for_timeout(400)
    ver(filas.count() == 1, f"buscar por empresa encuentra 1 ({filas.count()})", donde)
    pg.fill("#ia-busca", ""); pg.wait_for_timeout(400)
    pg.click("#ia-pag .pag-sig"); pg.wait_for_timeout(300)
    ver("Empresa 20" in filas.first.inner_text(), "la página 2 empieza en la 21", donde)
    ver(not EMOJI.search(pg.inner_text("#ia-lista")), "la lista sin emojis", donde)
    sin_desborde(pg, donde, "Borradores")
    pg.unroute("**/api/mail/borradores")
    pg.unroute(re.compile(r".*/api/mail/borradores/\d+$"))
    pg.reload(); pg.wait_for_timeout(1200)


GRUPOS = {"base": base, "general": general, "contactos": contactos, "correos": correos,
          "ajustes": ajustes, "barras": barras, "kommo_tanda": kommo_tanda,
          "dashboard": dashboard, "borradores": borradores}


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
