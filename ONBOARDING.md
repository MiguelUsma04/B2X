# Empezar a trabajar en B2X

Guía para quien agarra el proyecto por primera vez.

## Qué es esto

Una app interna que procesa leads B2B antes de mandarlos a GoHighLevel.

```
CSV de Apollo  →  importar  →  buscar los emails que faltan  →  revisar  →  CRM
```

El problema que resuelve: Apollo no tiene el email de todos los prospectos.
En una prueba real con 25 contactos, Apollo dio 15 y los otros 10 no tenían
email a ningún precio. B2X consulta otros tres servicios para recuperar parte
de esos, y también busca celulares.

**Ya está en producción**: <https://b2x.gmarketing.co>

---

## Arrancar en tu máquina

```bash
git clone https://github.com/MiguelUsma04/B2X.git
cd B2X
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# .venv/bin/pip install -r requirements.txt                   # Mac/Linux
```

Después necesitás un `.env`. Copiá `.env.example` y pedile los valores a
Miguel — **nunca por chat ni email**, y nunca los subas al repo.

```bash
cp .env.example .env
```

Correr:

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8077
```

En <http://127.0.0.1:8077>. Sin `APP_PASSWORD` en el `.env` no pide login,
pero solo acepta conexiones desde la propia máquina.

---

## Dónde está cada cosa

| Archivo | Qué hace |
|---|---|
| `app/main.py` | Rutas de la API |
| `app/db.py` | Esquema SQLite y migraciones |
| `app/csv_import.py` | Parseo del CSV y detección de columnas |
| `app/importer.py` | Inserción con deduplicación |
| `app/enrichment.py` | Motor de búsqueda en cascada |
| `app/providers/` | Un archivo por servicio: prospeo, icypeas, hunter |
| `app/ghl.py` | Cliente de GoHighLevel |
| `app/auth.py` | Login por contraseña |
| `app/static/app.js` | Todo el frontend (sin build step) |
| `deploy/` | Despliegue en el VPS |

---

## Lo que hay que saber antes de tocar nada

Estas son cosas que costaron encontrar. Están todas en el historial de git
(`git log`), pero acá va el resumen.

**El CSV de Apollo parte mal los nombres latinos.** "Gerardo Javier Salinas"
sale como First="Gerardo", Last="Javier Salinas", donde "Javier" es el
segundo nombre. Los proveedores buscan patrones sobre el apellido real, así
que `name_variants()` en `providers/base.py` genera las combinaciones a
probar. En español el apellido paterno va primero y es el que usan los emails
corporativos.

**Apollo también confunde `Company Name` con el nombre de la persona** si el
detector de columnas no lo previene. Por eso ninguna columna que empiece con
"Company" puede mapearse a un campo de persona.

**Prospeo devuelve el email en `person.email.email`**, no en `response.email`.
Y respeta el flag `revealed`: cuando es `false` el dato viene enmascarado con
asteriscos y no sirve.

**Icypeas es asíncrono.** Encola la búsqueda y hay que consultar
`bulk-single-searchs/read` hasta que el status deje de ser `IN_PROGRESS`.
Cortar antes desperdicia una búsqueda que igual se cobró.

**El `.env` no debe pisar el entorno con valores vacíos.** En el servidor las
variables llegan por Docker; una línea `APP_PASSWORD=` vacía apagaría el login
sin avisar. Ver `_load_env()` en `main.py`.

**El teléfono de la empresa no es el del contacto.** Apollo suele llenar solo
`Corporate Phone`, que es el conmutador. Se guarda con `phone_type='company'`
y no cuenta como contactable.

**Los contactos ya enviados al CRM nunca se borran**, ni al eliminar su carga.
Ese registro es lo único que evita mandar duplicados a GoHighLevel.

---

## Costos: cada llamada gasta plata

- Buscar un email: 1 crédito
- Buscar un celular: **10 créditos**
- Hunter no cobra si no encuentra

Por eso el enriquecimiento es un botón manual y nunca automático, los
teléfonos van en un botón aparte, y los contactos que ya fallaron no se
reintentan salvo que se pida explícitamente.

**Cuando pruebes, usá el campo "¿Cuántos buscar?" con 5.** No lances la lista
completa hasta confirmar que funciona.

---

## Desplegar un cambio

El servidor corre en Docker, junto a n8n detrás de Traefik.

```bash
ssh root@31.220.108.70
cd /opt/b2x && git pull
cd /root && docker compose up -d --build b2x
```

Ver qué pasa:
```bash
docker compose logs -f b2x
```

Guía completa: [deploy/DEPLOY-TRAEFIK.md](deploy/DEPLOY-TRAEFIK.md).

**No instales nginx ni certbot en ese servidor**: Traefik ya ocupa los puertos
80 y 443, y romperías n8n.

---

## Estado actual

**Funciona y está probado con datos reales**: importación, deduplicación,
cascada de búsqueda, envío a GoHighLevel con creación de oportunidad,
búsqueda de teléfonos, login.

**Medido en 25 contactos reales** (agencias de viajes en LATAM):
Apollo aportó 15 emails, la cascada 2 más, y 8 no tienen email en ninguna
base. Son empresas de 1 a 18 empleados: ese segmento no está cubierto por
ningún proveedor.

**Fuera de alcance**: envío de emails o WhatsApp (lo hace GHL), scoring
automático de ICP, multi-usuario real (hay una sola contraseña compartida).

**Ideas pendientes**: sumar el sitio web de la empresa como cuarto paso de la
cascada, para las empresas chicas que publican su email de contacto pero no
están en ninguna base B2B.

---

## Reglas

- **Nunca commitees el `.env`.** Está en `.gitignore`; que siga así.
- Antes de tocar los proveedores, leé su documentación oficial: los formatos
  cambian y ya nos mordió tres veces.
- Probá con pocos contactos antes de correr una lista entera.
