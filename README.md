# B2X — Consola de prospección B2B

App interna de un solo usuario. Toma un CSV de Apollo, enriquece los contactos sin
email probando proveedores en cascada, y los manda a GoHighLevel.

```
CSV Apollo (People) → importar/dedupe → enriquecer (waterfall) → revisar → GHL
```

## ¿Nuevo en el proyecto?

Empezá por [ONBOARDING.md](ONBOARDING.md): qué es, cómo correrlo, y las
trampas que ya encontramos.

## Arranque

```bash
# 1. dependencias (ya instaladas en .venv)
.venv/Scripts/python.exe -m pip install -r requirements.txt

# 2. credenciales — editá .env a mano, NO las pegues en un chat
#    (usá .env.example como plantilla)

# 3. correr
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8077
```

Abrir <http://127.0.0.1:8077>. Los puntos arriba a la derecha muestran qué
proveedores están configurados (verde = activo).

## Variables de entorno (`.env`)

| Variable | Para qué | Dónde se saca |
|---|---|---|
| `PROSPEO_API_KEY` | Enriquecimiento paso 1 | prospeo.io → API |
| `ICYPEAS_API_KEY` | Enriquecimiento paso 2 | icypeas.com → API (solo la KEY; el SECRET y el USER-ID no se usan) |
| `HUNTER_API_KEY` | Enriquecimiento paso 3 | hunter.io/api-keys |
| `GHL_API_TOKEN` | Envío a GHL | Private Integration Token (`pit-…`) |
| `GHL_LOCATION_ID` | Sub-account destino | Settings → Business Profile, o la URL del dashboard |
| `GHL_DEFAULT_TAG` | Tag por defecto | texto libre, ej. `piloto-prospeccion` |
| `ENRICH_DELAY_SECONDS` | Espera entre contactos | default `1.0` |
| `ENRICH_MAX_RETRIES` | Reintentos ante 429/5xx | default `3` |
| `ONLY_VERIFIED_EMAIL` | Prospeo: solo emails verificados | default `false` |
| `PUBLIC_URL` | Dirección pública de B2K | necesaria para medir aperturas y clics |
| `APP_PASSWORD` | Contraseña de acceso | obligatoria para exponer la app |
| `SECRET_KEY` | Firma de la cookie de sesión | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `COOKIE_SECURE` | Cookie solo por HTTPS | default `true` |

La app funciona con las keys que tenga: si falta una, ese proveedor se saltea.

## Acceso

La app pide contraseña (`APP_PASSWORD`), con sesión por cookie de 12 h.

**Sin `APP_PASSWORD` definida, solo acepta conexiones locales.** Es a propósito:
sin esa protección, cualquiera que llegue a la URL podría gastar tus créditos de
enriquecimiento y escribir en tu GHL.

En local podés dejarla vacía y entrar sin contraseña.

## Desplegar en un servidor

- [deploy/DEPLOY.md](deploy/DEPLOY.md) — VPS limpio: systemd + Nginx + certbot.
- [deploy/DEPLOY-TRAEFIK.md](deploy/DEPLOY-TRAEFIK.md) — VPS que ya corre
  Traefik (p. ej. junto a n8n): B2X se suma como contenedor y Traefik le emite
  el certificado.

**Un hosting compartido (tipo Hostinger Business) no sirve**: corre PHP y no
permite procesos de larga duración. **Las plataformas serverless (Vercel,
Lambda) tampoco**: SQLite no persiste en disco efímero y el enriquecimiento
excede el límite de tiempo de las funciones.

## Uso

**Importar** — Pestaña *Importar CSV*. Tiene que ser un export de **People** de
Apollo, no de **Accounts**: un export de empresas no trae nombre ni email de
persona, así que no hay a quién enriquecer. La app lo detecta y lo bloquea.
Las columnas se detectan solas (tolera variaciones en los nombres). Se muestra
el mapeo detectado y 10 filas de vista previa antes de confirmar.

**Deduplicación** — primero por `email`; si el contacto no trae email, por
`full_name` + `company_domain`. Los duplicados se cuentan y se descartan.

**Enriquecer** — Pestaña *Enriquecer*. Es manual y consume créditos: empezá con
un límite de 5–10. Corre serial, con espera entre contactos. Cada intento queda
guardado en `enrichment_log` con payload y respuesta completos.

**Enviar a GHL** — Seleccionás filas en la tabla (o "seleccionar todos los
filtrados") y usás *Aprobar y enviar a GHL*. Los que fallan quedan en
`ghl_status = error` con el mensaje; **no hay reintento automático** — reintentás
vos filtrando por `ghl_status = error`.

**Escribirles** — Pestaña *Correos*. El correo se escribe en texto o en
*Diseño HTML*, con vista de cómo queda y un botón para mandarse una prueba a
uno mismo. Sale en goteo, rotando entre los buzones activos y respetando el
tope diario de cada uno.

**Medir** — Pestaña *Resultados*. Aperturas y clics salen del correo mismo:
cada mensaje lleva una marca al azar, los enlaces pasan por B2K y al final va
un pixel. Necesita `PUBLIC_URL` (o un proxy que mande `X-Forwarded-Host`) y
que el correo vaya en HTML; sin eso no se rastrea nada, a propósito, para no
mandar enlaces rotos.

Las **respuestas y los rebotes** salen de leer el buzón por IMAP, con la misma
contraseña de aplicación del envío. Con Gmail o Workspace el servidor se
deduce (`smtp.gmail.com` → `imap.gmail.com`); si no sigue esa costumbre se
escribe a mano en el buzón. Se revisa solo cada 10 minutos y hay un botón para
no esperar. Un "estoy de vacaciones" no cuenta como respuesta.

**Varios dominios** — Los buzones se agrupan por dominio, que es la unidad
que mira quien recibe el correo: tres casillas de un mismo dominio son, para
Gmail, un solo remitente. El goteo elige primero el dominio que menos mandó
hoy y recién adentro de ese, el buzón con más margen.

*Revisar el DNS* consulta SPF, DKIM y DMARC de cada dominio. Para
**subdominios** (ventas@mail.tudominio.co) hay que saber que **el SPF no se
hereda**: cada subdominio necesita su propio registro y su propia clave DKIM.
El DMARC sí se hereda del dominio padre.

## La cascada

| Orden | Proveedor | Endpoint | Auth | Notas |
|---|---|---|---|---|
| 1 | Prospeo | `POST api.prospeo.io/enrich-person` | header `X-KEY` | Síncrono. Necesita nombre + empresa, o `linkedin_url` |
| 2 | Icypeas | `POST app.icypeas.com/api/email-search` | header `Authorization` | **Asíncrono**: devuelve `_id` y se consulta `bulk-single-searchs/read` hasta `DEBITED` |
| 3 | Hunter | `GET api.hunter.io/v2/email-finder` | `api_key` en query | Síncrono. 15 req/s, 500/min |

Reglas: 429/5xx reintenta con backoff exponencial + jitter (respeta `Retry-After`),
máximo 3 intentos, después pasa al siguiente. Si un proveedor devuelve
`INVALID_API_KEY` o `INSUFFICIENT_CREDITS`, se desactiva por el resto de la corrida
para no quemar llamadas. Si ninguno resuelve → `email_status = not_found`.

El email queda como `verified` o `unverified` según lo que reporte el proveedor
(Prospeo `status=VERIFIED`; Icypeas `certainty` ultra_sure/very_probable;
Hunter `verification.status=valid` o score ≥ 90).

## Estructura

```
app/
  main.py           rutas FastAPI
  db.py             esquema SQLite + conexión
  csv_import.py     parseo y detección de columnas
  importer.py       inserción con dedupe
  enrichment.py     motor waterfall + progreso
  ghl.py            cliente GoHighLevel v2
  providers/        prospeo.py · icypeas.py · hunter.py · base.py
  auth.py           login por contraseña + sesión
  static/           style.css · app.js
  templates/        index.html · login.html
deploy/             systemd, nginx, instalador y guía
data/b2x.db         SQLite (gitignored)
```

## Fuera de alcance (MVP)

Envío de email/WhatsApp (lo hace GHL), scoring de ICP, multi-usuario
(hay login, pero con una sola contraseña compartida), búsqueda de teléfono (el campo `phone` existe y se llena desde el CSV si viene,
pero no lo buscamos vía API).

## Notas

- `data/b2x.db` y `.env` están en `.gitignore`. No commitees credenciales.
- Los custom fields de GHL se mandan por `key` (`company_name`, `job_title`,
  `company_domain`, `email_source`, `import_batch_id`). Tienen que existir en
  el sub-account, si no GHL los ignora. Se crean en Settings → Custom Fields.
