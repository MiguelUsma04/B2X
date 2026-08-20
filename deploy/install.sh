#!/usr/bin/env bash
# Instalador de B2X en un VPS Ubuntu/Debian.
#
#   sudo bash install.sh
#
# Es idempotente: se puede volver a correr sin romper nada.
set -euo pipefail

APP_DIR=/opt/b2x
APP_USER=b2x
REPO=https://github.com/MiguelUsma04/B2X.git

say() { printf "\n\033[1;33m==> %s\033[0m\n" "$1"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Corrélo con sudo: sudo bash install.sh" >&2
  exit 1
fi

say "1/7 Paquetes del sistema"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git nginx curl

say "2/7 Usuario de servicio ($APP_USER)"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"

say "3/7 Código en $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO" "$APP_DIR"
fi
mkdir -p "$APP_DIR/data"

say "4/7 Entorno virtual y dependencias"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

say "5/7 Archivo .env"
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  # SECRET_KEY se genera solo; la contraseña y las API keys las ponés vos.
  SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$SECRET|" "$APP_DIR/.env"
  echo "   .env creado. FALTA cargar APP_PASSWORD y las API keys."
else
  echo "   .env ya existe, no lo toco."
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"

say "6/7 Servicio systemd"
cp "$APP_DIR/deploy/b2x.service" /etc/systemd/system/b2x.service
systemctl daemon-reload
systemctl enable b2x >/dev/null 2>&1
systemctl restart b2x

say "7/7 Estado"
sleep 2
systemctl --no-pager --lines=5 status b2x || true

cat <<'FIN'

------------------------------------------------------------------
Instalado. Falta lo que solo podés hacer vos:

1. Editar las credenciales:
      sudo nano /opt/b2x/.env
   Cargá APP_PASSWORD (elegí una fuerte) y las API keys.

2. Reiniciar:
      sudo systemctl restart b2x

3. Nginx + dominio (ver deploy/DEPLOY.md, pasos 6 y 7).

La app escucha solo en 127.0.0.1:8077 — se llega a ella a través de
nginx, no directo. Verificar:
      curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8077/
   303 = andando y pidiendo login.
------------------------------------------------------------------
FIN
