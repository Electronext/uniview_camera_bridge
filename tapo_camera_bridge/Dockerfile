ARG BUILD_FROM=python:3.11-slim-bookworm
FROM ${BUILD_FROM}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN pip install --no-cache-dir \
      requests==2.32.3 \
      paho-mqtt==2.1.0

WORKDIR /app
COPY app.py onvif_camera.py run.sh ./
RUN chmod +x /app/run.sh

CMD ["/app/run.sh"]
