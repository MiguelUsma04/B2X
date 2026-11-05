# B2K para clientes — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que gmarketing pueda venderle B2K a otras empresas: cada cliente con su
B2K totalmente separada, un panel para controlarlos y bloquearlos, su marca, su
dominio de seguimiento y una forma sencilla de conectar buzones.

**Architecture:** Un solo proceso FastAPI y una base SQLite por cliente
(`data/clientes/<clave>/b2x.db`), elegida por una `ContextVar` que pone el
middleware según la sesión (o según el dominio, en las rutas de rastreo). Un
registro aparte (`data/clientes.db`) guarda los clientes. Los ajustes que hoy
salen del `.env` pasan a la tabla `ajustes` de cada base, con el `.env` como
respaldo solo para gmarketing. Los ciclos de fondo recorren cada base activa.

**Tech Stack:** Python 3.12, FastAPI, SQLite sin ORM, httpx, JS y CSS sin
frameworks, Playwright para las pruebas de pantalla. Sin dependencias nuevas
(contraseñas con `hashlib.scrypt`, DNS por DoH como ya hace `dnscheck.py`).

**Spec:** `docs/superpowers/specs/2026-11-05-b2k-clientes-design.md`

## Global Constraints

- La B2K de gmarketing sigue en `data/b2x.db`, con su `.env`, sus rutas y sus
  enlaces de correos ya enviados (`/t/a/<token>.png`, `/t/c/<token>`,
  `/u/<token>`) andando igual.
- **Para un cliente, el `.env` no es el valor por defecto de nada**, salvo las
  claves de API (Maps, OpenAI, Prospeo, Hunter) cuando su interruptor
  «Usa las claves de gmarketing» está prendido.
- **Ningún correo de un cliente lleva un enlace ni una imagen de un dominio de
  gmarketing.**
- La baja funciona siempre, aunque el cliente esté bloqueado.
- Bloquear frena todo: ingreso, sesiones abiertas, goteo, secuencias, lectura
  de buzones, sincronización con Kommo, tareas a pedido. No borra nada.
- «Desarrollada por gmarketing» al pie de la app y en el ingreso, sin forma de
  quitarlo.
- Migraciones solo agregan (columnas, tablas). Nunca se borra ni se renombra.
- Pruebas sobre copias de `data/b2x.db`, nunca sobre la original. Cada prueba es
  un script en `tests/` que termina en «TODO BIEN» (estilo de los existentes) y
  se corre con `.venv/Scripts/python`.
- Nunca `git add .` ni `-A`: el repo es público y hay un `env` suelto con claves.
- Textos de pantalla en español, en tuteo, y sin emojis (íconos de
  `app/static/iconos.js`).
- Cada tarea que cambia algo visible actualiza `app/templates/manual.html`.

## Review Focus

1. **Una tarea en segundo plano lanzada por un cliente que termina escribiendo
   en otra base**: `asyncio.create_task` y `asyncio.to_thread` copian el
   contexto, pero un hilo o un objeto creado antes (por ejemplo un caché a
   nivel de módulo) no. Se espera que todo lo que lanza un cliente escriba en
   su base. → prueba en Task 5.
2. **Un cliente sin Kommo configurado que manda al CRM**: con el `.env` de
   gmarketing cargado, el envío tiene que fallar diciendo que falta conectar su
   Kommo, no crear leads en el Kommo de gmarketing. → prueba en Task 4.
3. **Un cliente bloqueado a mitad de una redacción de 50 correos**: se corta al
   terminar el contacto en curso. → prueba en Task 6.
4. **Una respuesta «No» o «no, gracias» a un correo sin enlace de baja**: tiene
   que dar de baja sin depender de que la IA la clasifique. → prueba en Task 7.
5. **Un pedido a `/api/...` que entra por el dominio de seguimiento de un
   cliente** (`links.cliente.com/api/contacts`): 404, aunque traiga una cookie
   válida. → prueba en Task 8.

---

## Estructura de archivos

| Archivo | Qué hace |
|---|---|
| `app/db.py` (modificar) | La base sale del cliente actual; migración por archivo. |
| `app/clientes.py` (nuevo) | El registro de clientes: alta, estado, ingreso, dominio, uso. |
| `app/config.py` (nuevo) | De dónde sale cada ajuste según quién sea el cliente. |
| `app/porcliente.py` (nuevo) | Estado en memoria separado por cliente. |
| `app/auth.py` (modificar) | Sesión con cliente, ingreso con correo y contraseña, bloqueo. |
| `app/rastreo_dominio.py` (nuevo) | Verificar el dominio de seguimiento y escribir la config de Traefik. |
| `app/buzones.py` (nuevo) | Detectar el proveedor, guías, probar un buzón. |
| `app/microsoft.py` (nuevo) | OAuth de Microsoft y XOAUTH2. |
| `app/marca.py` (nuevo) | Nombre, color y logo de cada cliente. |
| `app/main.py`, `mailer.py`, `secuencia.py`, `kommo.py`, `places.py`, `ai.py`, `redactor.py`, `respuestas.py`, `enrichment.py`, `providers/`, `telefonos.py`, `respaldo.py`, `actividad.py` (modificar) | Usar lo anterior. |
| `app/templates/index.html`, `login.html`, `app/static/app.js`, `style.css` (modificar) | Panel, marca, ingreso, asistente de buzones. |
| `deploy/DEPLOY-TRAEFIK.md`, `deploy/docker-compose.b2x.yml` (modificar) | El paso único en el servidor. |

---

### Task 1: La base sale del cliente actual

**Files:**
- Modify: `app/db.py:7`, `app/db.py:317-351`
- Test: `tests/test_base_por_cliente.py`

**Interfaces:**
- Produces:
  - `db.CLIENTE: ContextVar[str | None]` (default `None` = gmarketing).
  - `db.ruta_base(clave: str | None = ...) -> Path`. Sin argumento usa
    `CLIENTE.get()`. `None` → `DB_PATH`; si no → `DATA_DIR / "clientes" / clave / "b2x.db"`.
  - `db.DATA_DIR: Path` = `DB_PATH.parent`. Las pruebas que cambian
    `db.DB_PATH` también cambian `db.DATA_DIR`.
  - `db.usar_cliente(clave: str | None)`: context manager que pone y
    restaura `CLIENTE`.
  - `db.carpeta_cliente(clave: str | None = ...) -> Path`: la carpeta de esa base.
  - `connect()` abre `ruta_base()` y migra **cada archivo** la primera vez que
    lo abre (`_listas: set[str]` en vez de `_schema_ready: bool`).

- [ ] **Step 1: Write the failing test** — `tests/test_base_por_cliente.py`, sobre
  una copia de `data/b2x.db` en un temporal (`db.DB_PATH` y `db.DATA_DIR`
  apuntando ahí):

```python
with db.usar_cliente("uno"):
    with db.get_db() as c:
        c.execute("INSERT INTO ajustes (clave, valor) VALUES ('x', 'de uno')")
with db.usar_cliente("dos"):
    ver(db.ajuste("x") is None, "el cliente dos no ve el ajuste del uno")
    ver(db.ruta_base().parent.name == "dos", "la base del dos está en su carpeta")
with db.usar_cliente("uno"):
    ver(db.ajuste("x") == "de uno", "el uno sí lo ve")
ver(db.ajuste("x") is None, "gmarketing tampoco lo ve")
ver(db.ruta_base() == db.DB_PATH, "gmarketing sigue en su archivo")
with db.usar_cliente("dos"):
    with db.get_db() as c:
        tablas = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    ver({"contacts", "email_queue", "ajustes", "secuencias"} <= tablas,
        "la base nueva nace con todas las tablas")
```

- [ ] **Step 2: Run it** — `.venv/Scripts/python tests/test_base_por_cliente.py`.
  Expected: falla en `db.usar_cliente` (no existe).
- [ ] **Step 3: Implement** lo de Interfaces. `connect()` hace `mkdir(parents=True)`
  de la carpeta de la base.
- [ ] **Step 4: Run** el test y todos los de `tests/test_*.py` y `tests/ui/smoke.py`.
  Expected: TODO BIEN en todos.
- [ ] **Step 5: Commit** — `git add app/db.py tests/test_base_por_cliente.py`,
  mensaje «Cada cliente con su propia base».

---

### Task 2: El registro de clientes

**Files:**
- Create: `app/clientes.py`
- Test: `tests/test_clientes.py`

**Interfaces:**
- Consumes: `db.DATA_DIR`, `db.usar_cliente`, `db.get_db`.
- Produces (`app/clientes.py`, base `db.DATA_DIR / "clientes.db"`):
  - Tabla `clientes(clave TEXT PK, nombre, correo_contacto, usuario_email UNIQUE,
    clave_hash, estado CHECK IN ('activo','bloqueado'), claves_gmarketing INTEGER
    DEFAULT 1, dominio_rastreo, dominio_ok INTEGER DEFAULT 0, creado, ultimo_ingreso,
    bloqueado_at, bloqueado_por)`.
  - Tabla `uso_claves(clave, servicio, mes TEXT 'YYYY-MM', llamadas, PK(clave, servicio, mes))`.
  - `crear(nombre: str, clave: str, correo_contacto: str, usuario_email: str) -> tuple[dict, str]`:
    devuelve el cliente y la contraseña en claro (una sola vez). Crea su base
    (abre `db.get_db()` con `usar_cliente(clave)`). `ValueError` con mensaje
    para pantalla si la clave no cumple `^[a-z0-9]{3,30}$`, está reservada
    (`gmarketing`, `admin`, `api`, `static`, `www`) o ya existe, o si el correo
    de ingreso ya está usado.
  - `listar() -> list[dict]`, `obtener(clave: str) -> dict | None`.
  - `esta_activo(clave: str | None) -> bool` (`None` → `True`).
  - `activos() -> list[str]`.
  - `bloquear(clave: str, por: str) -> None`, `desbloquear(clave: str, por: str) -> None`.
  - `nueva_contrasena(clave: str) -> str`.
  - `verificar_ingreso(email: str, contrasena: str) -> str | None`: la clave
    del cliente, o `None`. Anota `ultimo_ingreso`. Un bloqueado devuelve `None`.
  - `poner(clave: str, **campos) -> None`: solo `claves_gmarketing`,
    `dominio_rastreo`, `dominio_ok`.
  - `por_dominio(host: str) -> str | None`: el cliente cuyo `dominio_rastreo`
    es ese host (sin puerto, en minúsculas).
  - `anotar_uso(clave: str, servicio: str) -> None`, `uso_del_mes(clave: str) -> dict[str, int]`.
  - Contraseña: 14 caracteres de `secrets.token_urlsafe`; hash con
    `hashlib.scrypt(n=2**14, r=8, p=1)` y sal aleatoria, guardado como
    `scrypt$<sal hex>$<hash hex>`; comparación con `hmac.compare_digest`.

- [ ] **Step 1: Write the failing test** `tests/test_clientes.py`:
  - crear `("Viajes ABC", "viajesabc", "a@x.co", "ana@viajesabc.com")` → la base
    `clientes/viajesabc/b2x.db` existe y la contraseña devuelta entra con
    `verificar_ingreso`;
  - una contraseña equivocada → `None`;
  - `crear` con `"Viajes ABC"` como clave, con `"admin"`, o repetida → `ValueError`;
  - `bloquear` → `verificar_ingreso` da `None`, `esta_activo` da `False`,
    `activos()` no la trae; `desbloquear` lo revierte;
  - `nueva_contrasena` → la vieja ya no entra y la nueva sí;
  - `poner(clave, dominio_rastreo="links.viajesabc.com")` → `por_dominio("Links.ViajesABC.com:443")`
    da `"viajesabc"`;
  - `anotar_uso` dos veces → `uso_del_mes` da `{"maps": 2}`;
  - el hash guardado no contiene la contraseña.
- [ ] **Step 2: Run** — Expected: `ModuleNotFoundError: app.clientes`.
- [ ] **Step 3: Implement** `app/clientes.py`.
- [ ] **Step 4: Run** — Expected: TODO BIEN.
- [ ] **Step 5: Commit** «Registro de clientes».

---

### Task 3: Ingreso de clientes, sesión con cliente y bloqueo

**Files:**
- Modify: `app/auth.py:196-262`, `app/main.py:67-95` (login), `app/templates/login.html`
- Test: `tests/test_ingreso_clientes.py`

**Interfaces:**
- Consumes: `clientes.verificar_ingreso`, `clientes.esta_activo`, `db.CLIENTE`.
- Produces:
  - `auth.issue_cookie(response, email: str | None = None, cliente: str | None = None, como: str | None = None)`.
    `como` es el correo de la persona de gmarketing que entró como el cliente.
  - `auth.sesion(request) -> dict | None` con las claves `email`, `cliente`, `como`.
  - `auth_middleware`, para una sesión válida:
    - `cliente is None` → `db.CLIENTE = None`;
    - cliente activo, o `como` presente → pone `db.CLIENTE = cliente`;
    - cliente bloqueado sin `como` → borra la cookie y responde como sin sesión.
    Lo pone con `db.CLIENTE.set` antes de `call_next` y lo restaura después.
  - `POST /api/login` acepta `email` opcional:
    - con `email`, usa `clientes.verificar_ingreso` y emite la cookie con `cliente`;
    - sin `email`, sigue como hoy (contraseña de gmarketing).
  - `GET /api/yo -> {"email", "cliente", "nombre_cliente", "es_gmarketing": bool, "como": str|None}`.
    `es_gmarketing` es `cliente is None`.

- [ ] **Step 1: Write the failing test** con `TestClient` sobre copia de la base
  (como `tests/test_dashboard.py`). Se crea un cliente con `clientes.crear`:
  - ingreso con su correo y contraseña → `/api/yo` da `cliente == "viajesabc"`
    y `es_gmarketing is False`;
  - con esa sesión, `/api/contacts` devuelve 0 contactos aunque la base de
    gmarketing tenga;
  - `clientes.bloquear` → el siguiente `/api/yo` con la misma cookie da 401;
  - contraseña equivocada → redirige a `/login?error=1`;
  - ingreso con la contraseña de gmarketing → `es_gmarketing is True`.
- [ ] **Step 2: Run** — Expected: falla porque `/api/yo` no existe o el login ignora `email`.
- [ ] **Step 3: Implement.** En `login.html`, el bloque de contraseña gana un
  campo «Correo» opcional y el texto «Desarrollada por gmarketing» queda fijo
  al pie (ver Task 10).
- [ ] **Step 4: Run** el test, la suite y `tests/ui/smoke.py`. Expected: TODO BIEN.
- [ ] **Step 5: Commit** «Los clientes entran con su correo y quedan en su base».

---

### Task 4: Los ajustes de cada cliente

**Files:**
- Create: `app/config.py`
- Modify: `app/kommo.py` (líneas 83-91, 331, 373-398, 413-418, 908, 1136),
  `app/places.py:74-80`, `app/ai.py:41,292`, `app/redactor.py:492,509,929,958,1253`,
  `app/respuestas.py:187`, `app/providers/__init__.py:14-15`,
  `app/enrichment.py:562`, `app/telefonos.py:44-45`, `app/mailer.py:768-769`,
  `app/main.py:454-455,1840`
- Test: `tests/test_config_cliente.py`

**Interfaces:**
- Consumes: `db.ajuste`, `db.CLIENTE`, `clientes.obtener`, `clientes.anotar_uso`.
- Produces (`app/config.py`):
  - `valor(nombre: str, x: str | None = None) -> str | None`: lee el ajuste
    `nombre.lower()` de la base actual. Si está vacío: para gmarketing devuelve
    `os.getenv(nombre)`; para un cliente devuelve `x`.
  - `clave_api(servicio: str) -> str | None`. `servicio` es `maps`, `openai`,
    `prospeo` o `hunter`; el ajuste es `GOOGLE_MAPS_API_KEY`, `OPENAI_API_KEY`,
    `PROSPEO_API_KEY` o `HUNTER_API_KEY`.
    - Para gmarketing, como `valor`.
    - Para un cliente: su ajuste; si está vacío y `claves_gmarketing` vale 1,
      el `.env`, y además `clientes.anotar_uso(cliente, servicio)`; si no, `None`.
    - Se llama **justo antes de cada llamada** al servicio, para que el conteo
      sea por llamada.
  - `es_gmarketing() -> bool`.
  - Los ajustes por cliente, con el mismo nombre que la variable de entorno:
    `KOMMO_SUBDOMAIN`, `KOMMO_TOKEN`, `KOMMO_RESPONSIBLE_ID`, `KOMMO_DEFAULT_TAG`,
    `KOMMO_COUNTRY_REGION`, `EMPRESA_NOMBRE`, `EMPRESA_DIRECCION`, `PROPUESTA_B2K`,
    `IDIOMA_FORZADO`, `IDIOMA_POR_DEFECTO`, `PAIS_POR_DEFECTO`, más las cuatro claves.
  - Para un cliente, `PROPUESTA_B2K` vacía devuelve `""`. El texto de KOMMO10X de
    `redactor.py:929` queda solo como respaldo de gmarketing.
  - `GHL_*` solo para gmarketing: para un cliente, `ghl` se comporta como no
    configurado.
  - `GET/POST /api/ajustes/conexiones`: lee y guarda esos ajustes en la base
    actual. Las claves y el token se devuelven enmascarados (`••••` + últimos 4).
  - Ajustes → nueva tarjeta **Conexiones** con Kommo, empresa (nombre y dirección
    del pie), idioma y país, y claves de API. Para un cliente, cada campo vacío
    dice qué deja de andar. Para gmarketing dice «usa la del servidor».

- [ ] **Step 1: Write the failing test** `tests/test_config_cliente.py`, con
  `os.environ` cargado con valores falsos (`KOMMO_TOKEN=tok-gm`,
  `GOOGLE_MAPS_API_KEY=maps-gm`, `PROPUESTA_B2K=oferta-gm`):
  - gmarketing: `valor("KOMMO_TOKEN") == "tok-gm"`;
  - cliente sin ajustes: `valor("KOMMO_TOKEN") is None`,
    `valor("PROPUESTA_B2K", "") == ""`;
  - cliente con `claves_gmarketing=1`: `clave_api("maps") == "maps-gm"` y
    `uso_del_mes` suma 1;
  - con `claves_gmarketing=0`: `None`;
  - cliente con su propia clave: la suya, y no suma uso;
  - **Review Focus 2:** con sesión de cliente sin Kommo, `POST /api/ghl/send`
    (la ruta de envío al CRM, `main.py:1862`, con `httpx` interceptado) no hace
    ninguna llamada con `tok-gm` y devuelve un error que dice que falta
    conectar Kommo.
- [ ] **Step 2: Run** — Expected: `ModuleNotFoundError: app.config`.
- [ ] **Step 3: Implement** `config.py` y reemplazar cada `os.getenv` listado en
  Files por `config.valor` o `config.clave_api`. Los de ajuste fino
  (`KOMMO_ESPERA`, `SITIO_*`, `OPENAI_MODEL*`, etc.) quedan en el entorno: son
  del servidor.
- [ ] **Step 4: Run** el test, la suite y el smoke. Expected: TODO BIEN.
- [ ] **Step 5: Commit** «Cada cliente con su Kommo, sus claves y su texto
  comercial»; actualizar el manual (Ajustes → Conexiones).

---

### Task 5: Lo que vive en memoria, separado por cliente

**Files:**
- Create: `app/porcliente.py`
- Modify: `app/enrichment.py:47,260,386,529`, `app/redactor.py:1346`,
  `app/actividad.py:40`, `app/main.py:178-181`,
  `app/kommo.py:39-41,96,150,444`, `app/secuencia.py:154`
- Test: `tests/test_memoria_por_cliente.py`

**Interfaces:**
- Consumes: `db.CLIENTE`.
- Produces: `porcliente.PorCliente(fabrica: Callable[[], T])`, un proxy que
  delega cada atributo, `__getitem__`, `__setitem__`, `__contains__`, `get`,
  `pop`, `items`, `clear` y `__len__` en `self.actual()`. `actual()` crea y guarda
  una instancia por valor de `db.CLIENTE`.
  - `PROGRESS = PorCliente(EnrichProgress)` y las demás. Los usos de
    `PROGRESS.algo` no cambian.
  - `PorCliente.reemplazar(obj: T) -> None` cambia la instancia del cliente
    actual. Hoy cada corrida hace `global PROGRESS; PROGRESS = EnrichProgress(running=True)`
    (`enrichment.py:143-148, 292-299, 416-423, 556-563`, `redactor.py:1428-1433`).
    Eso pisaría el proxy para todos los clientes, así que pasa a ser
    `PROGRESS.reemplazar(EnrichProgress(running=True))` y se va el `global`.
  - Locks de asyncio: uno por cliente (van dentro de la instancia o en su
    propio `PorCliente(asyncio.Lock)`).
  - El control de ritmo de Kommo (`_ultimo`, `_castigo`, `_turno`) pasa a un
    objeto `_Ritmo` dentro de un `PorCliente`.

- [ ] **Step 1: Write the failing test:**
  - con `usar_cliente("uno")`,
    `enrichment.PROGRESS.reemplazar(EnrichProgress(running=True, total=5))`;
    con `usar_cliente("dos")`, `PROGRESS.as_dict()["running"] is False`; y
    `enrichment.PROGRESS` sigue siendo el mismo objeto `PorCliente`;
  - lo mismo con `actividad._TAREAS`;
  - **Review Focus 1:** dentro de `usar_cliente("uno")`, una tarea creada con
    `asyncio.create_task` que hace `await asyncio.to_thread(lambda: db.poner_ajuste("t", "1"))`
    deja el ajuste en la base de `uno` y no en la de gmarketing.
- [ ] **Step 2: Run** — Expected: el progreso de `uno` se ve desde `dos`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** el test, la suite y el smoke. Expected: TODO BIEN.
- [ ] **Step 5: Commit** «El progreso y los cachés, por cliente».

---

### Task 6: Lo que corre solo, cliente por cliente

**Files:**
- Modify: `app/mailer.py:1825-1960` (los cuatro `loop_*`), `app/main.py:732-760`
  (`_respaldo_diario`), `app/respaldo.py:23-70,108-134`, `app/enrichment.py`
  y `app/redactor.py` (los bucles por contacto)
- Test: `tests/test_ciclos_por_cliente.py`

**Interfaces:**
- Consumes: `clientes.activos`, `clientes.esta_activo`, `db.usar_cliente`.
- Produces:
  - `mailer.para_cada_cliente(fn: Callable[[], Awaitable]) -> None` (async).
    Recorre `[None] + clientes.activos()`, corre `await fn()` dentro de
    `usar_cliente(c)`, y atrapa y registra el error de cada cliente sin cortar
    a los demás.
  - Cada `loop_*` llama a su vuelta a través de `para_cada_cliente`.
  - `respaldo.respaldar_si_toca()` usa `db.ruta_base()` y guarda en
    `db.carpeta_cliente() / "respaldos"`. Para gmarketing la ruta queda en
    `data/respaldos/`, como hoy.
  - Los bucles por contacto de `enrichment` y `redactor` revisan
    `clientes.esta_activo(db.CLIENTE.get())` antes de cada contacto y cortan si
    da `False`.

- [ ] **Step 1: Write the failing test.** Dos clientes, cada uno con una fila
  vencida en `email_queue` y un buzón falso, y `mailer._enviar_sincrono`
  reemplazado por uno que anota a quién mandó.
  - Una vuelta de `para_cada_cliente(mailer._tanda)` manda las dos filas.
  - Bloquear a uno y repetir con filas nuevas: solo sale la del activo.
  - **Review Focus 3:** `redactor.redactar_muchos` sobre 5 contactos con la IA
    falsa, bloqueando al cliente después del segundo, escribe 2 o 3 borradores
    y no 5.
  - `respaldo.respaldar_si_toca()` dentro de `usar_cliente("uno")` deja la copia
    en `clientes/uno/respaldos/`.
- [ ] **Step 2: Run** — Expected: solo sale la fila de gmarketing.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** el test, la suite, el smoke, y los scripts de secuencia
  (`prueba_secuencia.py` y `prueba_arreglos.py`, con la raíz del repo y una
  copia de la base como argumentos). Expected: TODO BIEN.
- [ ] **Step 5: Commit** «El goteo, los buzones, las secuencias y el respaldo, por cliente».

---

### Task 7: Los correos de un cliente no llevan dominios de gmarketing

**Files:**
- Modify: `app/main.py:912-925` (`_base_publica`), `app/mailer.py:822-845`
  (`_pie_de_baja`), `mailer.py:1119-1150` (`_enviar_sincrono`),
  `mailer.py:1355-1370` (`crear_campania`), `app/secuencia.py:566-575`
  (`_encolar`), `app/respuestas.py` y `secuencia.py:419`, y las rutas
  `/t/` y `/u/` de `main.py:1552-1650`
- Test: `tests/test_correo_cliente.py`

**Interfaces:**
- Consumes: `clientes.obtener`, `clientes.por_dominio`.
- Produces:
  - `main._base_publica(request)`:
    - para gmarketing, como hoy;
    - para un cliente con `dominio_ok`, `https://<dominio_rastreo>`;
    - para un cliente sin dominio, `""`.
  - `mailer._pie_de_baja(base: str, token: str, html: bool)`. Con `base == ""`
    devuelve el pie sin enlace:
    - texto: `"\n\n—\nSi prefieres que no te escriba más, respóndeme «no»."`;
    - HTML: el mismo `<p>` sin `<a>`.
  - `crear_campania` y `secuencia._encolar` ponen **siempre** el pie para un
    cliente. El pixel y `marcar_html` solo van si `base` no está vacía.
  - `_enviar_sincrono(..., baja_mailto: str | None = None)`: sin `baja_url`
    y con `baja_mailto`, el header es `List-Unsubscribe: <mailto:{baja_mailto}?subject=baja>`
    y no se agrega `List-Unsubscribe-Post`. El obrero pasa el `from_email` del buzón.
  - `respuestas.es_baja_directa(texto: str) -> bool`: `True` si la primera línea
    con texto (sin la cita) es una de estas, ignorando mayúsculas, signos y
    acentos: `no`, `no gracias`, `no, gracias`, `baja`, `darme de baja`,
    `unsubscribe`, `stop`. En `secuencia.py:419` y donde se procesa una
    respuesta fuera de una secuencia, va a supresión sin pasar por la IA.
  - Rutas `/t/` y `/u/`:
    - `clientes.por_dominio(request host)` decide la base; si no es de un
      cliente, la de gmarketing (los enlaces viejos siguen andando);
    - si el cliente está bloqueado, `/t/a` devuelve el pixel sin anotar, `/t/c`
      redirige sin anotar y `/u/` da de baja igual.

- [ ] **Step 1: Write the failing test:**
  - cliente sin dominio: `crear_campania` con `base_rastreo=""`. Ni el `body` ni
    el `body_html` contienen `gmarketing`, `/t/` ni `/u/`, y el pie dice
    «respóndeme «no»»;
  - el mensaje armado por `_enviar_sincrono` (SMTP falso, como en
    `tests/test_pie_baja.py`) trae `List-Unsubscribe: <mailto:ventas@cliente.co?subject=baja>`;
  - `es_baja_directa`:
    - `True` para `"No"`, `"no, gracias.\n\n> El mar 5 escribió…"` y `"BAJA"`;
    - `False` para `"No sé, ¿cuánto cuesta?"` y `"Sí"`;
  - cliente con `dominio_ok` y `dominio_rastreo="links.cli.co"`: `GET /u/<token>`
    con header `Host: links.cli.co` da de baja en **su** base. Bloqueado, sigue
    dando de baja;
  - un `/u/<token>` de gmarketing sin header de cliente sigue andando.
- [ ] **Step 2: Run** — Expected: el pie trae el enlace con el dominio de gmarketing.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** el test y la suite (en especial `test_pie_baja.py` y
  `test_rastreo_texto.py`, que cubren gmarketing). Expected: TODO BIEN.
- [ ] **Step 5: Commit** «Los correos de un cliente miden con su dominio o no
  miden»; manual: la baja por respuesta.

---

### Task 8: Conectar el dominio de seguimiento

**Files:**
- Create: `app/rastreo_dominio.py`
- Modify: `app/auth.py` (middleware), `app/main.py` (rutas),
  `app/static/app.js` y `index.html` (Ajustes → tarjeta «Dominio de seguimiento»),
  `deploy/docker-compose.b2x.yml`, `deploy/DEPLOY-TRAEFIK.md`
- Test: `tests/test_dominio_rastreo.py`

**Interfaces:**
- Consumes: `clientes.poner`, `clientes.listar`, `clientes.por_dominio`.
- Produces:
  - `rastreo_dominio.destino() -> str`: el host al que apunta el CNAME. Es el
    host de `PUBLIC_URL`, por ejemplo `b2k.gmarketing.co`.
  - `async verificar(dominio: str) -> dict`. Resuelve CNAME y A por DoH
    (`https://dns.google/resolve`, como `dnscheck._txt`). Devuelve
    `{"ok": bool, "apunta_a": str | None, "falta": str}`. `ok` si el CNAME es
    `destino()` (con o sin punto final) o si los A coinciden con los de
    `destino()`. `falta` es el mensaje para pantalla.
  - `escribir_traefik() -> Path`. Escribe `TRAEFIK_DINAMICO` (env; por defecto
    `data/traefik/clientes.yml`) con un router por cliente con `dominio_ok`:
    `rule: Host(\`<dominio>\`)`, `entryPoints: [web, websecure]`,
    `service: b2x@docker`, `tls.certResolver: mytlschallenge`. Lo escribe en un
    temporal y lo renombra, así Traefik nunca lee un archivo a medias.
  - Rutas (solo para un cliente):
    - `GET /api/dominio` → `{dominio, ok, destino}`;
    - `POST /api/dominio` (form `dominio`) → guarda, verifica, y si da `ok`
      pone `dominio_ok=1` y llama a `escribir_traefik()`.
    Para gmarketing, 404.
  - Middleware: si `clientes.por_dominio(host)` da un cliente, solo pasan
    `/t/` y `/u/`; todo lo demás, 404, con o sin cookie.
  - El dominio se valida con `^(?=.{4,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,}$`.
    No puede terminar en `gmarketing.co` ni ser el de otro cliente.
  - En el servidor: `docker-compose.b2x.yml` monta `./data/traefik` donde
    Traefik lo lea. `DEPLOY-TRAEFIK.md` explica el paso único: el provider
    `file` de Traefik con `watch: true` apuntando a ese directorio.

- [ ] **Step 1: Write the failing test** con DoH falso (`httpx.MockTransport`):
  - un CNAME a `b2k.gmarketing.co.` → `ok`;
  - un CNAME a otra cosa → no `ok`, y `falta` nombra el registro que hay que poner;
  - el yml escrito tiene un router por cada cliente con `dominio_ok`;
  - `links.cli.co` → `POST /api/dominio` rechaza `algo.gmarketing.co`;
  - **Review Focus 5:** `GET /api/contacts` con `Host: links.cli.co` y una cookie
    válida de gmarketing → 404.
- [ ] **Step 2: Run** — Expected: `ModuleNotFoundError: app.rastreo_dominio`.
- [ ] **Step 3: Implement.** La tarjeta muestra:
  - el registro como una tabla `Tipo / Nombre / Apunta a`, con botón de copiar;
  - el botón «Verificar»;
  - el estado (verificado / todavía no apunta), en el estilo de las tarjetas
    de Ajustes.
  El Dashboard y Resultados de un cliente sin dominio muestran un aviso con un
  botón a esa tarjeta.
- [ ] **Step 4: Run** el test, la suite y el smoke. Expected: TODO BIEN.
- [ ] **Step 5: Commit** «Dominio de seguimiento de cada cliente»; manual y
  documento de despliegue.

---

### Task 9: El panel de Clientes y «Entrar como este cliente»

**Files:**
- Modify: `app/main.py` (rutas `/api/admin/...`), `app/auth.py`,
  `app/templates/index.html` (pantalla `panel-clientes`, franja de soporte),
  `app/static/app.js`, `app/static/style.css`
- Test: `tests/test_panel_clientes.py`, `tests/ui/smoke.py` (grupo `clientes`)

**Interfaces:**
- Consumes: todo `clientes.*`, `auth.issue_cookie(…, como=…)`, `main.anotar`.
- Produces. Todas las rutas son solo para sesiones con `es_gmarketing` y sin
  `como`; si no, 403:
  - `GET /api/admin/clientes`. Cada cliente con: `clave, nombre, estado, creado,
    ultimo_ingreso, empresas` (contactos en su base), `correos_30d` (enviados
    en 30 días) y `uso_mes`.
  - `POST /api/admin/clientes` (`nombre, clave, correo_contacto, usuario_email`)
    → `{cliente, contrasena}`.
  - `POST /api/admin/clientes/{clave}/bloquear`, `/desbloquear`, `/contrasena`
    (→ `{contrasena}`) y `/claves` (form `si: "1"|"0"`).
  - `POST /api/admin/clientes/{clave}/entrar`:
    - emite la cookie con `cliente=clave`, `como=<email de la sesión>` y
      `email=<email de la sesión>`;
    - anota `entró a dar soporte` en el `actividad` del cliente (con
      `usar_cliente`) y en el de gmarketing;
    - redirige a `/`.
  - `POST /api/admin/volver`: con `como` presente, emite la cookie de
    gmarketing con `email=como`.
  - Cada alta, bloqueo, desbloqueo y contraseña nueva se anota con
    `main.anotar` en el Historial de gmarketing.
  - Pantalla **Clientes**, en el menú solo si `es_gmarketing`:
    - la lista como tabla, que en el teléfono pasa a tarjetas;
    - el botón «Nuevo cliente», que abre una ventana propia;
    - la contraseña se muestra una vez, con un botón de copiar;
    - las acciones de cada cliente van en el menú `menuMas()`;
    - bloquear pide confirmación con `ask()`.
  - Franja fija arriba cuando hay `como`: «Estás viendo la B2K de
    <nombre_cliente>», con el botón «Volver a gmarketing».

- [ ] **Step 1: Write the failing test** `tests/test_panel_clientes.py`:
  - con sesión de gmarketing, crear un cliente → aparece en la lista con
    `estado == "activo"`;
  - con sesión del cliente, `GET /api/admin/clientes` → 403;
  - `entrar` → `/api/yo` da `cliente == clave` y `como == email`;
  - la `actividad` del cliente tiene la entrada;
  - `admin/clientes` estando como el cliente → 403;
  - `volver` → `es_gmarketing`;
  - `entrar` a un bloqueado funciona y `para_cada_cliente` lo sigue salteando.
  Grupo `clientes` en `smoke.py`, en el teléfono y en el computador:
  - la pantalla abre;
  - «Nuevo cliente» crea uno y muestra la contraseña;
  - bloquear pide confirmación y cambia el estado;
  - no hay desplazamiento horizontal;
  - no hay emojis.
- [ ] **Step 2: Run** — Expected: 404 en `/api/admin/clientes`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** el test, la suite y el smoke. Expected: TODO BIEN.
- [ ] **Step 5: Commit** «Panel de Clientes: alta, bloqueo y soporte»; manual.

---

### Task 10: La marca de cada cliente

**Files:**
- Create: `app/marca.py`
- Modify: `app/main.py` (rutas `/api/marca`, `/marca/logo`), `index.html`,
  `login.html`, `app.js`, `style.css`, `manual.html`
- Test: `tests/test_marca.py`, `tests/ui/smoke.py` (grupo `marca`)

**Interfaces:**
- Consumes: `db.carpeta_cliente`, `db.ajuste`, `db.poner_ajuste`.
- Produces:
  - `marca.actual() -> {"nombre": str, "color": str | None, "logo": str | None}`.
    - `nombre`: el ajuste `marca_nombre`, o «B2K».
    - `color`: el ajuste `marca_color`, en formato `#rrggbb`.
    - `logo`: `"/marca/logo"` si existe `carpeta_cliente()/logo.<ext>`.
    - Para gmarketing, `logo` y `color` son `None` (usa los suyos de siempre).
  - `marca.validar_color(hex: str) -> str | None`. `None` si es válido; si no,
    el mensaje. Es válido si el contraste de ese color contra `#ffffff` o contra
    `#0b0817` es al menos 4.5 (WCAG) **y** contra el fondo de la app
    (`--bg`, el oscuro) es al menos 3.
  - `marca.guardar_logo(datos: bytes, tipo: str) -> None`. Solo `image/png`,
    `image/jpeg` o `image/webp`, hasta 1 MB, verificando la firma de los bytes
    (no SVG: puede llevar código).
  - `GET /api/marca`, `POST /api/marca` (`nombre`, `color`), `POST /api/marca/logo`.
    Para gmarketing solo lectura.
  - `GET /marca/logo`: sirve el logo del cliente de la sesión, con
    `Cache-Control: private`.
  - En `index.html` y `login.html`, el pie dice siempre «Desarrollada por
    gmarketing», con un enlace a gmarketing.co. No sale de un ajuste.
  - Para un cliente, `app.js` cambia `--brand` por su color, pone su logo y su
    nombre en la cabecera y en el `<title>`, y saca los textos de ejemplo que
    nombran a gmarketing o a Kommo10X (los marcadores de `app.js:1629-1907` y
    `3014` pasan a ejemplos genéricos).
  - El manual pasa a ser genérico: dice «tu empresa» donde hoy dice gmarketing,
    salvo en la nota de quién la desarrolló.

- [ ] **Step 1: Write the failing test:**
  - `validar_color`:
    - `"#3987e5"` y `"#ffff00"` son válidos (el amarillo, con texto oscuro);
    - `"#1a1030"` es inválido (no se distingue del fondo oscuro);
    - `"rojo"` es inválido;
  - `guardar_logo`:
    - un PNG de 1 KB se guarda;
    - un SVG se rechaza;
    - un PNG de 2 MB se rechaza;
    - un `.png` que en realidad es texto se rechaza;
  - con sesión de un cliente, `GET /api/marca` trae su nombre y su color;
  - con la de otro cliente, no;
  - `GET /` contiene «Desarrollada por gmarketing» para gmarketing y para un
    cliente con marca propia.
  En `smoke.py`, grupo `marca`:
  - con sesión de un cliente con color `#3987e5`, el botón principal se pinta
    de ese color;
  - el pie dice «Desarrollada por gmarketing»;
  - el texto de la página no contiene `Kommo10X`.
- [ ] **Step 2: Run** — Expected: `ModuleNotFoundError: app.marca`.
- [ ] **Step 3: Implement.** En Ajustes, la tarjeta **Tu marca** (solo para un
  cliente): nombre, color con vista previa del botón, y subir logo.
- [ ] **Step 4: Run** el test, la suite y el smoke. Expected: TODO BIEN.
- [ ] **Step 5: Commit** «La marca de cada cliente, siempre desarrollada por gmarketing».

---

### Task 11: Conectar un buzón con un asistente

**Files:**
- Create: `app/buzones.py`
- Modify: `app/main.py` (rutas nuevas junto a `/api/mail/config`), `app.js` y
  `index.html` (Ajustes → Buzones), `manual.html`
- Test: `tests/test_buzones.py`, `tests/ui/smoke.py` (grupo `buzon`)

**Interfaces:**
- Produces:
  - `PROVEEDORES`. Cada uno con `nombre, mx (sufijos), smtp_host, smtp_port,
    security, imap_host, imap_port, guia (lista de pasos), enlace`:

| clave | MX termina en | SMTP | IMAP | enlace de la contraseña de aplicación |
|---|---|---|---|---|
| `google` | `google.com`, `googlemail.com` | `smtp.gmail.com:465 ssl` | `imap.gmail.com:993` | `https://myaccount.google.com/apppasswords` |
| `microsoft` | `mail.protection.outlook.com`, `outlook.com` | `smtp.office365.com:587 starttls` | `outlook.office365.com:993` | `https://account.microsoft.com/security` |
| `hostinger` | `hostinger.com` | `smtp.hostinger.com:465 ssl` | `imap.hostinger.com:993` | `https://hpanel.hostinger.com/emails` |
| `zoho` | `zoho.com`, `zoho.eu` | `smtp.zoho.com:465 ssl` | `imap.zoho.com:993` | `https://accounts.zoho.com/home#security/app_password` |

  - `async detectar(email: str) -> dict`: MX por DoH →
    `{"proveedor": clave | "otro", "nombre", "guia", "enlace", "config": {...}}`.
    Con `"otro"` no hay `config`, y la pantalla abre la configuración manual.
  - `async probar(cfg: dict) -> dict`: prueba el envío (SMTP login, sin mandar
    nada) y la lectura (IMAP login) en hilos, con 20 s cada uno. Devuelve
    `{"envio": "ok"|"error", "lectura": "ok"|"error", "consejo": str}`. El
    `consejo` sale de `traducir_error(proveedor, exc)`:
    - contraseña rechazada en Google → «Esa no es una contraseña de aplicación…»;
    - rechazada en Microsoft → «Microsoft no deja entrar con contraseña: usa
      Conectar con Microsoft»;
    - no se conecta al servidor → «No se pudo llegar al servidor…»;
    - IMAP apagado → cómo prenderlo en ese proveedor.
  - Rutas: `POST /api/mail/buzon/detectar` (`email`) y
    `POST /api/mail/buzon/conectar` (`email, nombre, contrasena`). `conectar`
    detecta, prueba y, si las dos pruebas dan `ok`, guarda en `smtp_config` igual
    que `POST /api/mail/config`. Si no, no guarda y devuelve el consejo.
  - Pantalla: en Buzones, el botón «Conectar otra cuenta» abre el asistente en
    tres pasos (correo → la guía de su proveedor y la contraseña → resultado).
    El formulario de hoy queda dentro de un `<details>` «Configuración manual».

- [ ] **Step 1: Write the failing test** con DoH falso y `smtplib` e `imaplib`
  falsos:
  - un MX `aspmx.l.google.com.` → `google` con `smtp.gmail.com:465`;
  - `x.mail.protection.outlook.com.` → `microsoft`;
  - un MX desconocido → `otro`;
  - SMTP que lanza `SMTPAuthenticationError` con Google → `envio == "error"`
    y el `consejo` menciona «contraseña de aplicación»;
  - `conectar` con las dos pruebas bien → la fila queda en `smtp_config`;
  - con una mal → no queda.
  En `smoke.py`, grupo `buzon` (con `detectar` y `conectar` interceptados por
  `page.route`):
  - el asistente avanza los tres pasos;
  - muestra la guía de Google;
  - con error muestra el consejo;
  - «Configuración manual» está cerrada.
- [ ] **Step 2: Run** — Expected: `ModuleNotFoundError: app.buzones`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** el test, la suite y el smoke. Expected: TODO BIEN.
- [ ] **Step 5: Commit** «Conectar un buzón en tres pasos»; manual.

---

### Task 12: Conectar con Microsoft

**Files:**
- Create: `app/microsoft.py`
- Modify: `app/db.py` (columnas nuevas de `smtp_config`, solo agregar),
  `app/mailer.py:971-985` (IMAP) y `mailer.py:1160-1172` (SMTP), `app/main.py`
  (rutas `/auth/microsoft/*`), `app/auth.py` (`PUBLIC_PATHS`), `app.js`,
  `deploy/DEPLOY.md`
- Test: `tests/test_microsoft.py`

**Interfaces:**
- Produces:
  - Columnas nuevas en `smtp_config`: `auth TEXT DEFAULT 'password'`
    (`'password'` o `'oauth_ms'`), `oauth_refresh TEXT`, `oauth_access TEXT`,
    `oauth_expira TEXT`.
  - `microsoft.configurado() -> bool`: `MS_CLIENT_ID` y `MS_CLIENT_SECRET` están
    en el entorno. Es la app de gmarketing, del servidor.
  - `microsoft.url_de_ingreso(redirect: str, estado: str) -> str`:
    `https://login.microsoftonline.com/common/oauth2/v2.0/authorize`, con
    `scope = "offline_access openid email https://outlook.office.com/SMTP.Send https://outlook.office.com/IMAP.AccessAsUser.All"`
    y `prompt=select_account`.
  - `async microsoft.canjear(code: str, redirect: str) -> dict`: devuelve
    `{email, access, refresh, expira}`. El email sale del `id_token`.
  - `async microsoft.token_vigente(buzon: dict) -> str`: si vence en menos de
    5 minutos, renueva con el `refresh_token` y guarda el nuevo en la fila.
  - `microsoft.xoauth2(email: str, token: str) -> str`:
    `f"user={email}\x01auth=Bearer {token}\x01\x01"`.
  - `_enviar_sincrono` y el lector IMAP:
    - con `auth == 'oauth_ms'`, en SMTP hacen
      `servidor.auth("XOAUTH2", lambda x=None: xoauth2(...))`;
    - en IMAP hacen `M.authenticate("XOAUTH2", lambda _: xoauth2(...).encode())`;
    - antes piden el token con `token_vigente`.
  - Rutas:
    - `GET /auth/microsoft/start` guarda en la cookie firmada `b2x_oauth`:
      el estado, el cliente de la sesión y `como`;
    - `GET /auth/microsoft/callback` valida el estado y vuelve a poner
      `usar_cliente` con el cliente guardado. Crea el buzón con:
      - `host smtp.office365.com:587 starttls`;
      - `imap_host outlook.office365.com`;
      - `auth 'oauth_ms'`;
      - `from_email` y `username` con el email.
      Después redirige a Ajustes → Buzones con un aviso.
    Las dos van en `PUBLIC_PATHS` solo para el callback; el start exige sesión.
  - El botón «Conectar con Microsoft» se ve en el asistente cuando
    `detectar` da `microsoft` o cuando la prueba con contraseña fue rechazada,
    y solo si `configurado()`.
  - `deploy/DEPLOY.md`: cómo registrar la app en Azure:
    - cuentas de cualquier organización y personales;
    - redirect `https://b2k.gmarketing.co/auth/microsoft/callback`;
    - los dos permisos delegados de Office 365 Exchange Online;
    - el secreto;
    - la verificación de editor.

- [ ] **Step 1: Write the failing test** con `httpx.MockTransport` para el
  endpoint de token:
  - `xoauth2("a@b.co", "T") == "user=a@b.co\x01auth=Bearer T\x01\x01"`;
  - `url_de_ingreso` trae los dos scopes y `offline_access`;
  - `canjear` guarda el email del `id_token`;
  - `token_vigente` con `oauth_expira` en el pasado pide uno nuevo y lo guarda;
    con uno vigente no llama;
  - el callback con un estado que no coincide → redirige a error y no crea nada;
  - el callback de un cliente crea el buzón en **su** base;
  - `_enviar_sincrono` con un buzón `oauth_ms` y SMTP falso llama a
    `auth("XOAUTH2", …)` y no a `login`.
- [ ] **Step 2: Run** — Expected: `ModuleNotFoundError: app.microsoft`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** el test, la suite y el smoke. Expected: TODO BIEN.
- [ ] **Step 5: Commit** «Conectar con Microsoft»; manual y despliegue.

---

### Task 13: Cierre — la prueba de punta a punta y los documentos

**Files:**
- Create: `tests/test_clientes_punta_a_punta.py`
- Modify: `app/templates/manual.html`, tablero
  (https://claude.ai/artifact/NXcLTn4nF9z9KRcFhdqyfH) y decisiones
  (https://claude.ai/artifact/7CDjrGRVs6S3vgUv53WaX5)

- [ ] **Step 1: Write the test.** Corre sobre una copia, con dos clientes y
  gmarketing; correo, Kommo y OpenAI falsos:
  - cada uno carga contactos, arma una campaña con secuencia y deja correr una
    vuelta de los ciclos;
  - ninguna base tiene filas de otra;
  - ningún correo de un cliente contiene `gmarketing`;
  - el `actividad` de cada uno tiene solo lo suyo;
  - bloquear a uno con todo en marcha frena su envío, y su baja sigue andando;
  - desbloquear deja que su secuencia siga sin mandar una tanda de golpe
    (sale uno por vuelta, como el goteo de siempre);
  - todas las pruebas existentes de gmarketing siguen pasando.
- [ ] **Step 2: Run** — Expected: TODO BIEN. Si algo falla, el arreglo va en la
  tarea dueña de ese código, con su prueba.
- [ ] **Step 3: Documentos.**
  - Manual: una sección «Para clientes» (ingreso, conexiones, marca, dominio,
    buzones) y una «Panel de Clientes» (solo gmarketing).
  - Tablero: mover «B2K para varios clientes» a Funcionando y agregar los pasos
    del equipo (Traefik una vez, app de Microsoft, quién paga las APIs).
  - Decisiones: el pago sigue abierto, con el interruptor como respuesta
    provisoria.
- [ ] **Step 4: Commit** «B2K para clientes: prueba de punta a punta y
  documentos»; push; republicar el manual, el tablero y las decisiones.
