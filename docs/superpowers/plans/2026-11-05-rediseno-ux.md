# Rediseño de la experiencia de B2K — plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que B2K se entienda a simple vista —íconos, títulos que son órdenes, explicaciones detrás de ⓘ— con Contactos, Correos y Ajustes reorganizados como en las maquetas aprobadas.

**Architecture:** la misma página (`index.html` + `app.js` + `style.css`), reorganizada. Se agregan componentes chicos y reutilizables —ícono SVG, ⓘ desplegable, menú ⋯, pasos— y se reacomodan las tarjetas existentes sin renombrar los `id` que ya usa el JavaScript. El servidor no cambia, salvo lo que haga falta leer para los estados de Ajustes (todo ya existe en `/api/...`).

**Tech Stack:** FastAPI (sin cambios), JavaScript sin framework, CSS con tokens. Pruebas de pantalla con Playwright (ya instalado en `.venv`).

**Spec:** `docs/superpowers/specs/2026-11-05-rediseno-ux-design.md` y la maqueta aprobada `.superpowers/brainstorm/1275-1793896047/content/rediseno-v2.html`.

## Global Constraints

- **Íconos, no emojis**, en toda la app (incluido el menú: ⬆ ✉ ◆ ✈ 📈 ⚙). SVG de línea dentro de la página, estilo Lucide, sin pedirlos a otro servidor.
- Ningún párrafo explicativo a la vista: el porqué va en un ⓘ que se abre al tocarlo. Lo que hace falta para decidir (cuántos salen, desde qué buzón, con seguimiento) queda a la vista.
- Mobile first: todo se ve y se usa a 360 px de ancho sin zoom ni desplazamiento horizontal.
- Objetivos de toque ≥ 44 px, foco visible, contraste AA, `aria-expanded` en ⓘ y ⋯, cierran con Escape y tocando afuera, `prefers-reduced-motion` respetado.
- Ningún `id` que use `app.js` se renombra. Sin librerías nuevas.
- Interfaz en voseo como hoy; los textos que lee el cliente, en tuteo.
- Lo que no se deshace: separado, en rojo, con confirmación.
- El manual (`app/templates/manual.html`) se actualiza en la misma tarea que cambia la pantalla y se republica en https://claude.ai/artifact/ScNSkWYNGtEWv22BH6sCYQ.
- Commit y push a `main` al cerrar cada tarea (autorización permanente de Pablo). `git add` archivo por archivo, nunca `git add .`.

## Review Focus

1. **Con los correos apagados** (Ajustes → Zona de cuidado): Correos, Resultados, «Escribirles» y las tarjetas de Envío desaparecen, y no queda un paso con ✓ ni un botón que lleve a una pantalla escondida.
2. **Ningún contacto marcado:** la barra de selección no aparece, Correos muestra «Marcá contactos» en el pie y Programar queda deshabilitado — no explota con 0.
3. **Teléfono de 360 px con la barra de selección y la de actividad a la vez:** ninguna tapa a la otra ni al último contacto de la lista.
4. **Kommo sin configurar:** «Mandar a Kommo» abre la ventana con el aviso de que falta configurarlo, en vez de fallar callado.
5. **Pantallas que se recargan solas** (la lista, la actividad, el estado de la campaña): un ⓘ o un ⋯ abierto no se cierra ni se duplica cuando se vuelve a dibujar.

Cada punto tiene su prueba en la tarea dueña del código (marcada «RF»).

---

### Task 1: Base — íconos, ⓘ, banco de pruebas de pantalla

**Files:**
- Create: `app/static/iconos.js` — el juego de íconos y `icono()`.
- Modify: `app/templates/index.html` (cargar `iconos.js` antes de `app.js`; menú y barra de secciones con íconos), `app/static/app.js` (componentes `info()` y `menuMas()`), `app/static/style.css`.
- Create: `tests/ui/servidor.py` (B2K sobre una copia de la base, sin Kommo ni OpenAI ni correo real; patrón de `servidor_prueba.py` del scratchpad), `tests/ui/smoke.py` (Playwright, computador 1440×900 y teléfono 390×844).

**Interfaces:**
- Produces: `icono(nombre: string, clase = ''): string` → `<svg class="ic ${clase}" aria-hidden="true"><use href="#i-${nombre}"/></svg>`. Nombres: `subir, sobre, rombo, avion, grafico, engranaje, info, mas, chequeo, cruz, basura, exportar, ojo, kommo, chispa, lapiz, paleta, reloj, buzon, pulso, flechas, lista, historial, disco, libro, alerta, flecha-izq, flecha-der, telefono, whatsapp, sitio, ia`.
- Produces: `info(texto_html: string): string` → `<button class="info" aria-expanded="false" aria-label="Más información">…<span class="pop" role="tooltip">…</span></button>`; abre/cierra con un solo manejador delegado en `document` (clic, Escape, clic afuera).
- Produces: `menuMas(items: {label, icono, onclick, peligro?}[]): string` — mismo manejo delegado.
- Produces: `tests/ui/smoke.py` con `def revisar(pg, nombre)` por pantalla; corre con `python tests/ui/smoke.py` y termina en `TODO BIEN` o lista de fallas; falla si hay errores de consola o desplazamiento horizontal (`document.documentElement.scrollWidth > innerWidth`).

- [ ] **Step 1:** Escribir `tests/ui/smoke.py` con las verificaciones de esta tarea: el menú no contiene ningún carácter de emoji (regex `[←-⯿\U0001F300-\U0001FAFF]` sobre `nav.steps` texto) y cada `.step` tiene un `svg.ic`; un `.info` se abre al clic (`aria-expanded="true"`, `.pop` visible), se cierra con Escape y con clic afuera; sin desplazamiento horizontal en el teléfono.
- [ ] **Step 2:** Correrlo: `python tests/ui/smoke.py` → falla en «emoji en el menú».
- [ ] **Step 3:** Implementar `iconos.js` (sprite `<svg>` insertado al cargar), `icono()`, `info()`, `menuMas()` y su CSS (`.ic` 18 px, `currentColor`; `.info` 28 px de toque con área de 44 px; `.pop` de 260 px máximo, que no se salga de la pantalla en el teléfono). Reemplazar los emojis del menú y de la barra de secciones.
- [ ] **Step 4:** `python tests/ui/smoke.py` → `TODO BIEN`.
- [ ] **Step 5:** Commit «Íconos en vez de emojis, ⓘ desplegable y pruebas de pantalla».

### Task 2: Reglas generales en todas las pantallas

**Files:** Modify `app/templates/index.html`, `app/static/app.js` (`loadMetrics()` en ~364, `goToStep()` en ~129, `aplicarFunciones()` en ~639), `app/static/style.css`, `app/templates/manual.html`. Test: `tests/ui/smoke.py`.

**Interfaces:**
- Consumes: `icono()`, `info()` (Task 1).
- Produces: `marcarPasosHechos(stats)` — pone `.hecho` (✓ con `icono('chequeo')`) en `.step[data-panel]`: `import` si hay contactos; `enrich` si `falta_buscar == 0` o hay al menos un email encontrado; `contacts` si hay alguno en Kommo; `mail` si hay al menos una campaña. `results` y `settings` nunca.
- Produces: `#metrics` se dibuja solo con el panel Contactos activo, como fila de pastillas `.metr` (contactables, con email, con celular, con WhatsApp, en Kommo, falta buscar); el desglose y «quién encontró cada email» pasan al ⓘ de esa fila.

- [ ] **Step 1:** Agregar a `smoke.py`: `#metrics` visible en Contactos y oculto en Correos, Resultados y Ajustes; en el teléfono, en Correos el primer control del paso (`.pasos` o, hasta la Task 4, el asunto) está dentro de la primera pantalla (`boundingBox().y < 844`); cada `.panel` abre con un título corto y un `.info` al lado; ningún `.lede` ni `.help` de más de 140 caracteres visible en Cargar, Emails y Resultados (pasan al ⓘ). **RF 1:** con `correos_activos` apagado, no hay `.step` visible ni con ✓ para `mail`/`results`.
- [ ] **Step 2:** Correr → falla.
- [ ] **Step 3:** Implementar: métricas en pastillas y solo en Contactos; títulos-orden («Traé empresas», «Encontrá sus emails», «Elegí a quién escribirle», «Escribiles», «Mirá cómo fue», «Ajustes») con su ⓘ; los párrafos de Cargar, Emails y Resultados pasan recortados al ⓘ de su tarjeta; `marcarPasosHechos()` llamado desde `loadMetrics()`.
- [ ] **Step 4:** Correr → `TODO BIEN`. Actualizar el manual (títulos nuevos, ⓘ, ✓ del menú) y republicarlo.
- [ ] **Step 5:** Commit «Títulos que dicen qué hacer, métricas solo en Contactos y ✓ en el menú».

### Task 3: Contactos — barra de selección, chips, completar datos, ventana de Kommo

**Files:** Modify `app/templates/index.html` (`#selbar` ~826, tarjetas «Envío al CRM», «Ficha del negocio con IA», «Leer los sitios web», «Buscar teléfonos» ~169-220), `app/static/app.js` (`loadContacts()` ~564, `updateSelInfo()` ~764, `sendToGHL()` ~778, `loadPipelines()` ~2402), `app/static/style.css`, `app/templates/manual.html`. Test: `tests/ui/smoke.py`.

**Interfaces:**
- Consumes: `icono()`, `info()`, `menuMas()`.
- Produces: `#selbar` = «N marcados» + `#selbar-mail` (texto «Escribirles», principal, `goToStep('mail')`) + `#selbar-go` (texto «Mandar a Kommo», abre `ventanaKommo()`) + ⋯ con Ver el detalle (solo si hay 1), Exportar a Excel (`exportarMarcados()`), Desmarcar (`clearSelection()`), Borrar (rojo, con confirmación, la función de borrado actual).
- Produces: `ventanaKommo()` — usa `ask()` con etiqueta (`ghl-tag`), embudo y etapa (`pipeline-box`, valores de Ajustes preseleccionados) y «Mandar N»; al confirmar llama a la lógica actual de `sendToGHL()` y muestra en la misma ventana cuántos entraron y cuáles fallaron con su motivo. Sin Kommo configurado: la ventana muestra el aviso y no el botón (**RF 4**).
- Produces: `chipsDe(c)` — chips con ícono: email (verificado / sin verificar / falta buscar / no se encontró / rebotó), Kommo (sin enviar / en Kommo / falló, con el error en su ⓘ), celular, WhatsApp. Computador: tabla; teléfono: tarjetas, como hoy.
- Produces: `#completar` — con marcados, tres tarjetas chicas bajo la lista: Ficha con IA («créditos de IA»), Leer sitios («gratis»), Buscar celulares («10× créditos»), cada una dispara la función que hoy usa su tarjeta.

- [ ] **Step 1:** Agregar a `smoke.py`: al marcar 3, `#selbar` muestra «3 marcados», «Escribirles» y «Mandar a Kommo» visibles, «Borrar» no visible hasta abrir ⋯; ⋯ abre y «Ver el detalle» no está con 3 y sí con 1; «Mandar a Kommo» abre `#ask` con etiqueta, embudo y etapa (con Kommo sin configurar, el aviso); `#completar` aparece con marcados y no sin ellos (**RF 2**); con la barra abierta, el último contacto de la lista queda por encima de la barra (`row.bottom <= selbar.top`) en computador y teléfono, también con la barra de actividad presente (**RF 3**); tras una recarga de la lista con ⋯ abierto, hay un solo `.menu-mas` (**RF 5**).
- [ ] **Step 2:** Correr → falla.
- [ ] **Step 3:** Implementar. La página reserva abajo el alto de la barra (`padding-bottom` en `.wrap` mientras `#selbar` está abierta). Las tarjetas sueltas de Envío al CRM / Ficha / Sitios / Teléfonos dejan de verse como tarjetas: su contenido pasa a `ventanaKommo()` y a `#completar`, conservando los `id`.
- [ ] **Step 4:** Correr → `TODO BIEN`. Manual: Contactos con la barra nueva y la ventana de Kommo; republicar.
- [ ] **Step 5:** Commit «Contactos: barra con lo que se hace, completar datos a la vista y ventana de Kommo».

### Task 4: Correos en cuatro pasos

**Files:** Modify `app/templates/index.html` (`#panel-mail` ~316-515), `app/static/app.js` (`switchFormato()` ~1719, `scheduleMail()` ~1965, `programarEnvioIA()`, `toggleSecuencia()`, `agregarSecuencia()`, `seqModo()`), `app/static/style.css`, `app/templates/manual.html`. Test: `tests/ui/smoke.py`.

**Interfaces:**
- Consumes: `icono()`, `info()`.
- Produces: `irAPaso(n: 1|2|3|4)` — muestra el bloque del paso, marca `.paso.ahora` / `.paso.hecho`. Paso 1: tres `.tile` (Lo escribe la IA «recomendado», Texto, Con diseño) que llaman a `switchFormato('ia'|'texto'|'html')`. Paso 2: el editor o los borradores de hoy. Paso 3: interruptor `#seq-on` + línea de tiempo con `#seq-d2`, `#seq-d3`, `#seq-dfin` en línea; tocar «Correo 2»/«Correo 3» despliega `#seq-2`/`#seq-3` (modo Texto/Diseño) o lleva a los borradores (modo IA). Paso 4: el ritmo (`#mail-limit`, `#mail-every`, `#mail-jitter`, `#mail-rotate`).
- Produces: `pieCorreos()` — pie fijo: «N empresas reciben el primer correo» (IA: aprobadas; Texto/Diseño: marcadas, recortadas por `#mail-limit`), «· hasta 3 correos cada una» con seguimiento, «· desde K buzones»; lo que falta en amarillo («falta el asunto», «ningún correo aprobado», «marcá contactos»); botones Atrás / Siguiente; en el paso 4, `#btn-mail` «Programar el envío», deshabilitado mientras falte algo. Se recalcula al cambiar cualquier campo.
- La tarjeta «Paso 4 — Escribirle a los contactos» desaparece: su contenido es el pie.

- [ ] **Step 1:** Agregar a `smoke.py`: hay 4 `.paso`; se puede ir directo al 3 tocándolo; en el 1, tocar «Texto» muestra en el 2 el asunto; en el 3, con `#seq-on`, la línea de tiempo tiene los tres campos de días y tocar «Correo 2» muestra `#seq-2`; el pie dice «3 empresas» con 3 marcados y `#btn-mail` habilitado solo con asunto y cuerpo; sin marcados, el pie dice «marcá contactos» y el botón está deshabilitado (**RF 2**); en el teléfono el pie y la barra de secciones no se tapan. Volver a correr las verificaciones de la secuencia del lanzamiento (la de `prueba_pantalla.py`: los campos `secuencia`, `cuerpo2`, `cuerpo3`, `dias2`, `dias3`, `dias_fin` siguen viajando en el POST a `/api/mail/schedule`).
- [ ] **Step 2:** Correr → falla.
- [ ] **Step 3:** Implementar los pasos sobre los bloques existentes, sin renombrar `id`.
- [ ] **Step 4:** Correr → `TODO BIEN`. Manual: Correos en cuatro pasos, la línea de tiempo y el pie; republicar.
- [ ] **Step 5:** Commit «Correos en cuatro pasos con la secuencia como línea de tiempo».

### Task 5: Ajustes en tarjetas

**Files:** Modify `app/templates/index.html` (`#panel-settings` ~564-825), `app/static/app.js` (`switchAjustes()` y las cargas de cada sección), `app/static/style.css`, `app/templates/manual.html`. Test: `tests/ui/smoke.py`.

**Interfaces:**
- Consumes: `icono()`, `info()`.
- Produces: `#ajustes-grilla` con grupos **Envío** (Buzones → `set-buzones`, Horario → `card-horario`, Salud del envío → `set-salud`), **Kommo** (Embudo y etapa, Etapas automáticas → la tarjeta de etapas, Repetidos → la tarjeta de repetidos), **Cuenta** (Historial → `set-historial`, Copia de la base → la tarjeta de la base, Cómo usar B2K → `set-ayuda`) y la **Zona de cuidado** cerrada (Empezar de cero con Kommo, el interruptor `#f-correos`; apagar pide confirmar, prender no).
- Produces: `estadoAjustes()` — la línea de estado de cada tarjeta con punto de color, leída de lo que ya cargan las secciones: buzones activos de total, horario, salud (peor estado), embudo y etapa elegidos, reglas de etapas elegidas o «ninguna», repetidos, último respaldo.
- Produces: `abrirAjuste(clave)` — muestra solo esa sección con «← Ajustes» arriba; `switchAjustes()` queda como alias para no romper llamadas.

- [ ] **Step 1:** Agregar a `smoke.py`: Ajustes muestra 9 tarjetas en 3 grupos y la Zona de cuidado cerrada; cada tarjeta tiene ícono y una línea de estado no vacía; tocar Horario muestra los campos `#v-desde`/`#v-hasta` y «← Ajustes» vuelve a la grilla; abrir la Zona de cuidado muestra «Olvidar lo enviado al CRM» y `#f-correos`; apagar `#f-correos` pide confirmar (**RF 1**: después no hay paso Correos ni tarjetas de Envío). Ya no hay pestañas `.seg` en Ajustes.
- [ ] **Step 2:** Correr → falla.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** Correr → `TODO BIEN`. Manual: Ajustes en tarjetas y la Zona de cuidado (las rutas del manual que dicen «Ajustes → Sistema → …» pasan a la tarjeta nueva); republicar.
- [ ] **Step 5:** Commit «Ajustes en tarjetas que dicen cómo está cada cosa».

### Task 6: Cierre

**Files:** los de arriba, solo arreglos.

- [ ] **Step 1:** Correr `python tests/ui/smoke.py` completo y las pruebas de la secuencia (`prueba_secuencia.py`, `prueba_arreglos.py` del scratchpad, sobre copias limpias) → `TODO BIEN` en las tres.
- [ ] **Step 2:** Capturas de cada pantalla en 390 px y 1440 px, revisadas contra la maqueta aprobada.
- [ ] **Step 3:** Commit y push; avisarle a Pablo los comandos de despliegue.
