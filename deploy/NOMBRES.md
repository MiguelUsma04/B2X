# Por qué algunas cosas siguen diciendo b2x

El proyecto se llamaba **B2X** y ahora se llama **B2K**. Lo que ve una persona
ya dice B2K: el título de la app, el logo, los documentos, el manual.

Lo que sigue diciendo `b2x` son nombres que algo de afuera ya conoce, y
cambiarlos **rompe cosas**. Esta es la lista y qué pasa si se cambia cada una.

| Nombre | Dónde vive | Qué pasa si se cambia |
|---|---|---|
| `b2x_data` | volumen de Docker | **Se pierde la base entera.** Docker no renombra un volumen: crea uno nuevo y vacío, y el viejo queda huérfano. Contactos, campañas, métricas y buzones: todo. |
| `b2x` | servicio del docker-compose | El contenedor viejo queda dando vueltas y el nuevo arranca sin el volumen si el `volumes:` no se ajusta a la vez. |
| `b2x:latest` | imagen | Nada grave, pero hay que reconstruir y limpiar la vieja. |
| `/opt/b2x` | carpeta en el servidor | El `build.context` del compose apunta ahí. |
| `B2X_DOMAIN` | variable del entorno | De ella salen el router de Traefik, el certificado y `PUBLIC_URL`. Si se renombra sin cambiarla en el `.env`, queda vacía: el sitio deja de resolver y los correos salen sin poder medirse. |
| `b2x_session`, `b2x_oauth` | cookies | Cambiarlas **cierra la sesión de todos** en el momento del despliegue. |
| `b2x.db` | archivo de la base | Habría que renombrarlo dentro del volumen, con la app apagada. |

## Si igual se quieren cambiar

Hay que hacerlo con la app apagada y en este orden, no de a uno suelto:

```bash
docker compose stop b2x

# 1. Copiar el contenido del volumen viejo al nuevo.
docker volume create b2k_data
docker run --rm -v b2x_data:/viejo -v b2k_data:/nuevo alpine \
  sh -c "cp -a /viejo/. /nuevo/"

# 2. Comprobar que la copia tiene la base ANTES de tocar nada más.
docker run --rm -v b2k_data:/d alpine ls -lh /d

# 3. Recién ahora, cambiar el compose: servicio, imagen y volumen.
# 4. Levantar y verificar en Ajustes -> Sistema que la base tiene
#    los contactos de siempre y no es una nueva y vacía.
docker compose up -d --build b2x   # con el nombre nuevo, b2k
```

El paso 4 no es opcional: la pantalla de Ajustes → Sistema avisa cuando la
base nació hace un rato y está vacía, que es exactamente el síntoma de haber
arrancado contra un volumen equivocado. Si eso aparece, **no cargues nada**:
apagá, revisá el volumen y volvé al anterior.

Mientras tanto, dejar los nombres como están no cuesta nada: nadie los ve.
