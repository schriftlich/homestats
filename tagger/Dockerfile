FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TAGGER_DATA_DIR=/data \
    TAGGER_DOWNLOADS=/downloads

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tagger ./tagger

# Läuft als UID 1000 (wie von Umbrel erwartet) – derselbe Benutzer, dem der Download-Ordner gehört.
RUN mkdir -p /data /downloads && chown 1000:1000 /data
USER 1000:1000
VOLUME /data
EXPOSE 8080

ARG VERSION=dev
ENV TAGGER_VERSION=${VERSION}

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"

CMD ["uvicorn", "tagger.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
