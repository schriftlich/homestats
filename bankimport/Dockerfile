FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BANKIMPORT_DATA_DIR=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bankimport ./bankimport

RUN mkdir -p /data && chown 1000:1000 /data
USER 1000:1000
VOLUME /data
EXPOSE 8080

ARG VERSION=dev
ENV BANKIMPORT_VERSION=${VERSION}

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"

CMD ["waitress-serve", "--port=8080", "bankimport.app:app"]
