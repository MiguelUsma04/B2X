# B2X — Consola de prospección B2B

App interna de un solo usuario. Toma un CSV de Apollo, enriquece los contactos sin
email probando proveedores en cascada, y los manda a GoHighLevel.

```
CSV Apollo (People) → importar/dedupe → enriquecer (waterfall) → revisar → GHL
```

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

La app funciona con las keys que tenga: si falta una, ese proveedor se saltea.

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
  static/           style.css · app.js
  templates/        index.html
data/b2x.db         SQLite (gitignored)
```

## Fuera de alcance (MVP)

Envío de email/WhatsApp (lo hace GHL), scoring de ICP, login/multi-usuario,
búsqueda de teléfono (el campo `phone` existe y se llena desde el CSV si viene,
pero no lo buscamos vía API).

## Notas

- `data/b2x.db` y `.env` están en `.gitignore`. No commitees credenciales.
- Los custom fields de GHL se mandan por `key` (`company_name`, `job_title`,
  `company_domain`, `email_source`, `import_batch_id`). Tienen que existir en
  el sub-account, si no GHL los ignora. Se crean en Settings → Custom Fields.
