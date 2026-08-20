# Desplegar B2X en tu VPS (junto a n8n, detrás de Traefik)

Guía para **tu servidor concreto**: Ubuntu 24.04 en `31.220.108.70`, con
Traefik ya sirviendo n8n en los puertos 80/443.

> **No instales nginx ni certbot.** Traefik ya ocupa 80/443 y emite los
> certificados. Instalar nginx dejaría n8n caído. Esta guía reemplaza a
> `DEPLOY.md` (que es para un servidor sin Traefik).

B2X se agrega como un contenedor más de tu `docker-compose.yml`, igual que n8n.

---

## Paso 1 — Cloudflare en gris (importante)

Tu Traefik usa el resolver **`mytlschallenge`** (TLS-ALPN-01), que valida por
el puerto 443. **Con el proxy naranja de Cloudflare encendido, esa validación
falla** porque Cloudflare corta el TLS antes de llegar a tu servidor.

En Cloudflare → DNS → registro `b2x`: hacé clic en la nube naranja para dejarla
**gris** (solo DNS).

Verificá que resuelva a tu IP real:

```bash
dig +short b2x.gmarketing.co
```

Tiene que devolver `31.220.108.70`. Si devuelve otra cosa (IPs de Cloudflare
como 104.x o 172.x), el proxy sigue activo — esperá unos minutos.

> Una vez que el certificado esté emitido y funcionando, podés volver a
> encender la nube naranja si querés. Pero probá primero en gris.

---

## Paso 2 — Traer el código

```bash
git clone https://github.com/MiguelUsma04/B2X.git /opt/b2x
```

Si ya lo habías clonado:
```bash
cd /opt/b2x && git pull
```

---

## Paso 3 — Agregar B2X a tu docker-compose.yml

Primero, un respaldo por las dudas:

```bash
cp /root/docker-compose.yml /root/docker-compose.yml.bak
```

Abrí el archivo:

```bash
nano /root/docker-compose.yml
```

**3a.** Dentro de `services:`, al mismo nivel que `n8n:` y `postgres:`, pegá
el bloque `b2x:` que está en `/opt/b2x/deploy/docker-compose.b2x.yml`.
Para verlo y copiarlo:

```bash
cat /opt/b2x/deploy/docker-compose.b2x.yml
```

**3b.** Al final del archivo, en la sección `volumes:` (donde ya están
`traefik_data` y `n8n_data`), agregá:

```yaml
  b2x_data:
```

> Cuidado con la indentación: YAML es estricto. `b2x:` va con 2 espacios,
> igual que `n8n:`.

---

## Paso 4 — Cargar las credenciales

Las variables van en el `.env` que Docker Compose ya lee (el mismo de donde
salen `DOMAIN_NAME` y `SUBDOMAIN` para n8n):

```bash
nano /root/.env
```

Agregá al final:

```
B2X_DOMAIN=b2x.gmarketing.co
B2X_PASSWORD=elegí-una-contraseña-larga-y-única
B2X_SECRET_KEY=PEGAR_ACÁ_LA_CLAVE_GENERADA
PROSPEO_API_KEY=...
ICYPEAS_API_KEY=...
HUNTER_API_KEY=...
GHL_API_TOKEN=...
GHL_LOCATION_ID=921uZB5rqWlxF2pJ8imV
GHL_DEFAULT_TAG=piloto-prospeccion
```

Para generar `B2X_SECRET_KEY`:

```bash
openssl rand -base64 32
```

Protegé el archivo:

```bash
chmod 600 /root/.env
```

> **No uses `DOMAIN_NAME`**: en este servidor vale `srv802743.hstgr.cloud`
> (el hostname de Hostinger), no tu dominio real. Por eso B2X usa su propia
> variable `B2X_DOMAIN` con el dominio completo.

---

## Paso 5 — Levantar

```bash
cd /root
docker compose config >/dev/null && echo "YAML OK"
docker compose up -d --build b2x
```

El primer build tarda un par de minutos. Verificá:

```bash
docker compose ps
docker compose logs -f b2x
```

Buscá `Application startup complete`. Salí de los logs con `Ctrl+C`.

Probá localmente (todavía sin pasar por Traefik):

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8077/
```

**303** = la app anda y está pidiendo login.

---

## Paso 6 — Entrar

Esperá ~1 minuto a que Traefik emita el certificado, y abrí:

**https://b2x.gmarketing.co**

Debería aparecer la pantalla de login. Entrá con lo que pusiste en
`B2X_PASSWORD`.

---

## Actualizar cuando haya cambios

```bash
cd /opt/b2x && git pull
cd /root && docker compose up -d --build b2x
```

## Comandos útiles

```bash
docker compose logs -f b2x         # logs en vivo
docker compose restart b2x         # reiniciar
docker compose ps                  # estado de todo
```

## Respaldo de la base

La base vive en el volumen `b2x_data`:

```bash
docker run --rm -v root_b2x_data:/data -v /root:/backup alpine \
  tar czf /backup/b2x-$(date +%F).tar.gz -C /data .
```

(Si el volumen tiene otro nombre, mirá `docker volume ls | grep b2x`.)

---

## Si algo falla

**El certificado no se emite / "Not Secure"**

Casi siempre es Cloudflare en naranja. Verificá:
```bash
dig +short b2x.gmarketing.co        # tiene que dar 31.220.108.70
docker compose logs traefik | grep -i b2x | tail -20
```

**404 page not found de Traefik**

Traefik no matcheó la regla. Revisá que `DOMAIN_NAME` esté bien:
```bash
grep DOMAIN_NAME /root/.env
docker compose config | grep -A3 "routers.b2x.rule"
```

**El contenedor reinicia en loop**
```bash
docker compose logs --tail=50 b2x
```

**503 "APP_PASSWORD no está configurada"**

La variable no llegó al contenedor:
```bash
docker compose exec b2x printenv | grep -c APP_PASSWORD   # 1 = llegó
```
Si da 0, revisá que `B2X_PASSWORD` esté en `/root/.env` y reiniciá.

**Rompí el docker-compose.yml y ahora n8n no arranca**
```bash
cp /root/docker-compose.yml.bak /root/docker-compose.yml
cd /root && docker compose up -d
```
