#!/usr/bin/env bash
# Qué le falta al .env del servidor para que B2K funcione entero.
#
# No imprime ningún valor: solo dice si cada variable está o no. Se puede
# correr delante de quien sea sin mostrar una sola clave.
#
#   bash /opt/b2x/deploy/revisar-env.sh
#
# El archivo es el que lee Docker Compose, que está al lado del
# docker-compose.yml y NO es el del repositorio.

ENV="${1:-/root/.env}"

if [ ! -f "$ENV" ]; then
  echo "No existe $ENV"
  echo "Pasá la ruta como argumento: bash revisar-env.sh /ruta/al/.env"
  exit 1
fi

# Sin estas la app no levanta.
IMPRESCINDIBLES="B2X_DOMAIN B2X_PASSWORD B2X_SECRET_KEY"

# Sin estas la app levanta pero la función correspondiente queda apagada.
# Al lado va para qué sirve cada una, que es lo que uno quiere saber cuando
# ve que algo no anda.
POR_FUNCION="
KOMMO_SUBDOMAIN|mandar contactos a Kommo
KOMMO_TOKEN|mandar contactos a Kommo
KOMMO_PIPELINE_ID|en qué embudo caen los leads
KOMMO_STATUS_ID|en qué etapa caen los leads
GOOGLE_MAPS_API_KEY|buscar empresas por ubicación
PROSPEO_API_KEY|buscar los correos que faltan
HUNTER_API_KEY|buscar los correos que faltan
OPENAI_API_KEY|leer los sitios y escribir los correos
GOOGLE_CLIENT_ID|entrar con la cuenta de Google
GOOGLE_CLIENT_SECRET|entrar con la cuenta de Google
"

falta_grave=0
falta_suave=0

echo "Revisando $ENV"
echo
echo "--- Sin esto la app no arranca ---"
for v in $IMPRESCINDIBLES; do
  if grep -qE "^[[:space:]]*${v}=[^[:space:]]" "$ENV"; then
    echo "  OK     $v"
  else
    echo "  FALTA  $v"
    falta_grave=$((falta_grave + 1))
  fi
done

echo
echo "--- Cada una enciende una función ---"
echo "$POR_FUNCION" | while IFS='|' read -r v para; do
  [ -z "$v" ] && continue
  if grep -qE "^[[:space:]]*${v}=[^[:space:]]" "$ENV"; then
    printf "  OK     %-22s %s\n" "$v" "$para"
  else
    printf "  FALTA  %-22s %s\n" "$v" "$para"
  fi
done

echo
echo "--- Que el compose las nombre ---"
COMPOSE="$(dirname "$ENV")/docker-compose.yml"
if [ -f "$COMPOSE" ]; then
  if grep -q "KOMMO_TOKEN" "$COMPOSE"; then
    echo "  OK     el docker-compose.yml nombra las de Kommo"
  else
    echo "  FALTA  el docker-compose.yml NO nombra las de Kommo."
    echo "         Tenerlas en el .env no alcanza: si el compose no las"
    echo "         nombra, el contenedor no las ve. Copiá el bloque de"
    echo "         deploy/docker-compose.b2x.yml."
  fi
else
  echo "  ?      no encontré $COMPOSE"
fi

echo
if [ "$falta_grave" -gt 0 ]; then
  echo "Hay $falta_grave variable(s) imprescindible(s) sin poner."
else
  echo "Lo imprescindible está."
fi
echo "Después de tocar el .env o el compose hay que redesplegar:"
echo "  cd $(dirname "$ENV") && docker compose up -d --build b2x"
