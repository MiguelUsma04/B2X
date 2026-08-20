# B2X — imagen para correr detrás de Traefik.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencias primero: se cachean mientras requirements.txt no cambie.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY app ./app

# La base vive en un volumen; el resto de la imagen es de solo lectura.
RUN mkdir -p /app/data && \
    useradd --system --uid 1001 b2x && \
    chown -R b2x:b2x /app
USER b2x

EXPOSE 8077

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        r=urllib.request.urlopen('http://127.0.0.1:8077/login',timeout=4); \
        sys.exit(0 if r.status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8077"]
