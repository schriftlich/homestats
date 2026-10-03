FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GEMEINDE_DATA_DIR=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gemeinde ./gemeinde

# Läuft als UID 1000 (wie von Umbrel erwartet), funktioniert aber mit jeder UID,
# die in /data schreiben darf.
RUN mkdir -p /data && chown 1000:1000 /data
USER 1000:1000
VOLUME /data
EXPOSE 8080

ARG VERSION=dev
ENV GEMEINDE_VERSION=${VERSION}

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"

CMD ["uvicorn", "gemeinde.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
