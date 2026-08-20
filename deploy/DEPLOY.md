# Desplegar B2X en tu VPS de Hostinger

Guía paso a paso. Los comandos se corren por SSH en el VPS.

> **El hosting Business (compartido) no sirve** para esta app: corre PHP y no
> permite procesos Python de larga duración. Tiene que ser el VPS.
>
> **Si tu VPS ya tiene Traefik o nginx sirviendo otra cosa** (por ejemplo n8n),
> NO sigas esta guía: instalar nginx dejaría ese servicio caído. Usá
> [DEPLOY-TRAEFIK.md](DEPLOY-TRAEFIK.md).

---

## Paso 0 — Averiguar qué tiene el VPS

Entrá por SSH (Hostinger te da IP y contraseña en hPanel → VPS → Overview):

```bash
ssh root@TU_IP_DEL_VPS
```

Y corré esto para saber con qué estamos trabajando:

```bash
echo "== OS ==";      cat /etc/os-release | head -2
echo "== Python ==";  python3 --version 2>/dev/null || echo "sin python3"
echo "== Nginx ==";   nginx -v 2>&1 || echo "sin nginx"
echo "== Apache ==";  apache2 -v 2>&1 | head -1 || echo "sin apache"
echo "== Panel ==";   ls -d /usr/local/CyberPanel /usr/local/lsws /opt/psa /www/server 2>/dev/null || echo "sin panel"
echo "== Puertos =="; ss -tlnp 2>/dev/null | grep -E ':(80|443|8077)' || echo "80/443 libres"
```

Pegame la salida si algo no cuadra. Lo que sigue asume **Ubuntu/Debian sin panel**.

- **Si tiene CyberPanel/aaPanel/Plesk**: no corras el instalador todavía —
  el panel ya maneja el puerto 80 y hay que configurar el proxy desde ahí.
- **Si aparece Apache** en vez de nginx: se puede, pero cambia el paso 6.

---

## Paso 1 — Apuntar el subdominio al VPS

En el panel de tu DNS (donde administrás `gmarketing.co`), creá un registro:

| Tipo | Nombre | Valor            | TTL  |
|------|--------|------------------|------|
| A    | `b2x`  | `TU_IP_DEL_VPS`  | 3600 |

Hacelo ahora: la propagación tarda entre minutos y un par de horas, y el
certificado HTTPS (paso 7) no funciona hasta que resuelva.

Verificar:
```bash
dig +short b2x.gmarketing.co
```

---

## Paso 2 — Instalar

```bash
cd /tmp
git clone https://github.com/MiguelUsma04/B2X.git b2x-src
sudo bash b2x-src/deploy/install.sh
```

El script instala dependencias, crea el usuario `b2x`, clona el repo en
`/opt/b2x`, arma el virtualenv, genera el `SECRET_KEY` y levanta el servicio.

---

## Paso 3 — Cargar las credenciales

```bash
sudo nano /opt/b2x/.env
```

Completá:

```
APP_PASSWORD=<una contraseña larga y única>
PROSPEO_API_KEY=...
ICYPEAS_API_KEY=...
HUNTER_API_KEY=...
GHL_API_TOKEN=...
GHL_LOCATION_ID=921uZB5rqWlxF2pJ8imV
```

`SECRET_KEY` ya quedó generado por el instalador. Guardá con `Ctrl+O`, salí
con `Ctrl+X`, y reiniciá:

```bash
sudo systemctl restart b2x
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8077/
```

**303** = andando y pidiendo login. Si da 000, mirá los logs (abajo).

> Sin `APP_PASSWORD`, la app rechaza toda conexión que no sea local. Es a
> propósito: evita que quede expuesta sin protección por un olvido.

---

## Paso 4 — Firewall

Solo 22, 80 y 443. El 8077 **no** se abre: se llega por nginx.

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo ufw status
```

---

## Paso 5 — Nginx

```bash
sudo cp /opt/b2x/deploy/nginx-b2x.conf /etc/nginx/sites-available/b2x
sudo nano /etc/nginx/sites-available/b2x     # cambiá el server_name si usás otro subdominio
sudo ln -sf /etc/nginx/sites-available/b2x /etc/nginx/sites-enabled/b2x
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Probá `http://b2x.gmarketing.co` — debería aparecer el login (todavía sin HTTPS).

---

## Paso 6 — HTTPS

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d b2x.gmarketing.co
```

Elegí la opción de redirigir todo a HTTPS. Certbot renueva solo.

**Recién ahora entrá a `https://b2x.gmarketing.co`.** La cookie de sesión
viaja con el flag `Secure`, así que el login solo funciona por HTTPS.

> Si necesitás entrar por HTTP sin certificado (no recomendado), poné
> `COOKIE_SECURE=false` en el `.env` y reiniciá.

---

## Actualizar a una versión nueva

```bash
cd /opt/b2x
sudo -u b2x git pull
sudo /opt/b2x/.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart b2x
```

## Operación diaria

```bash
sudo systemctl status b2x        # estado
sudo journalctl -u b2x -f        # logs en vivo
sudo journalctl -u b2x -n 100    # últimas 100 líneas
sudo systemctl restart b2x       # reiniciar
```

## Respaldo de la base

La base es un solo archivo. Copialo periódicamente:

```bash
sudo sqlite3 /opt/b2x/data/b2x.db ".backup '/root/b2x-$(date +%F).db'"
```

Para bajarlo a tu máquina, desde Windows:
```
scp root@TU_IP:/root/b2x-*.db .
```

---

## Si algo falla

**El servicio no arranca**
```bash
sudo journalctl -u b2x -n 50 --no-pager
```
Casi siempre es el `.env` (una comilla de más) o un permiso.

**502 Bad Gateway** — nginx anda pero la app no:
```bash
sudo systemctl status b2x
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8077/
```

**503 con "APP_PASSWORD no está configurada"** — falta la contraseña en el
`.env`, o quedó vacía. Cargala y reiniciá.

**El login rebota al formulario** — pasa si entrás por HTTP con
`COOKIE_SECURE=true`: el navegador descarta la cookie. Usá HTTPS.

**Los proveedores salen en gris** — las API keys no llegaron. Verificá:
```bash
sudo grep -c '^PROSPEO_API_KEY=.\+' /opt/b2x/.env   # 1 = tiene valor
sudo systemctl restart b2x
```
