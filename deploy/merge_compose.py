#!/usr/bin/env python3
"""Inserta el servicio b2x en un docker-compose.yml existente.

Pegar YAML a mano en un editor es donde más fácil se rompe la indentación,
y este archivo es del que depende n8n. El script lo hace por texto (no
reescribe el YAML entero) para no alterar el formato ni los comentarios
del resto del archivo.

    python3 merge_compose.py [ruta]        # por defecto /root/docker-compose.yml

Es idempotente: si b2x ya está, no hace nada.
"""
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

SERVICE = """  b2x:
    build:
      context: /opt/b2x
      dockerfile: Dockerfile
    image: b2x:latest
    restart: always
    ports:
      - "127.0.0.1:8077:8077"
    labels:
      - traefik.enable=true
      - traefik.http.routers.b2x.rule=Host(`${B2X_DOMAIN}`)
      - traefik.http.routers.b2x.tls=true
      - traefik.http.routers.b2x.entrypoints=web,websecure
      - traefik.http.routers.b2x.tls.certresolver=mytlschallenge
      - traefik.http.services.b2x.loadbalancer.server.port=8077
      - traefik.http.middlewares.b2x.headers.SSLRedirect=true
      - traefik.http.middlewares.b2x.headers.STSSeconds=315360000
      - traefik.http.middlewares.b2x.headers.browserXSSFilter=true
      - traefik.http.middlewares.b2x.headers.contentTypeNosniff=true
      - traefik.http.middlewares.b2x.headers.forceSTSHeader=true
      - traefik.http.middlewares.b2x.headers.SSLHost=${B2X_DOMAIN}
      - traefik.http.middlewares.b2x.headers.STSIncludeSubdomains=true
      - traefik.http.middlewares.b2x.headers.STSPreload=true
      - traefik.http.routers.b2x.middlewares=b2x@docker
    environment:
      - APP_PASSWORD=${B2X_PASSWORD}
      - SECRET_KEY=${B2X_SECRET_KEY}
      - COOKIE_SECURE=true
      - PROSPEO_API_KEY=${PROSPEO_API_KEY}
      - ICYPEAS_API_KEY=${ICYPEAS_API_KEY}
      - HUNTER_API_KEY=${HUNTER_API_KEY}
      - GHL_API_TOKEN=${GHL_API_TOKEN}
      - GHL_LOCATION_ID=${GHL_LOCATION_ID}
      - GHL_DEFAULT_TAG=${GHL_DEFAULT_TAG:-piloto-prospeccion}
      - ENRICH_DELAY_SECONDS=1.0
      - ENRICH_MAX_RETRIES=3
    volumes:
      - b2x_data:/app/data
"""


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/docker-compose.yml")
    if not path.exists():
        print(f"ERROR: no encuentro {path}")
        return 1

    text = path.read_text(encoding="utf-8")

    if re.search(r"^\s{2}b2x:", text, re.M):
        print("El servicio b2x ya está en el archivo. No toco nada.")
        return 0

    if not re.search(r"^services:", text, re.M):
        print("ERROR: el archivo no tiene una sección 'services:'.")
        return 1

    backup = path.with_suffix(f".yml.antes-de-b2x-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(path, backup)

    # 1) El servicio va justo después de la línea 'services:'.
    text = re.sub(r"(^services:[ \t]*\n)", r"\1" + SERVICE, text, count=1, flags=re.M)

    # 2) El volumen, en la sección 'volumes:' de nivel superior (sin indentar).
    #    Ojo: cada servicio tiene su propio 'volumes:' indentado — ese no.
    top_volumes = list(re.finditer(r"^volumes:[ \t]*\n", text, re.M))
    if top_volumes:
        m = top_volumes[-1]
        text = text[:m.end()] + "  b2x_data:\n" + text[m.end():]
    else:
        text = text.rstrip("\n") + "\n\nvolumes:\n  b2x_data:\n"

    path.write_text(text, encoding="utf-8")
    print(f"Listo. Copia de seguridad: {backup}")
    print("Verificá con:  cd /root && docker compose config >/dev/null && echo OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
