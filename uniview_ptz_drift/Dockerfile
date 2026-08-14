ARG BUILD_FROM=python:3.11-slim-bookworm
FROM ${BUILD_FROM}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN pip install --no-cache-dir \
      numpy==2.1.3 \
      opencv-python-headless==4.10.0.84 \
      requests==2.32.3 \
      paho-mqtt==2.1.0

WORKDIR /app
COPY app.py uniview.py ha_mqtt.py alarm_service.py run.sh healthcheck.py ./
COPY templates ./templates
RUN chmod +x /app/run.sh

HEALTHCHECK --interval=2m --timeout=10s --start-period=30s --retries=3 \
  CMD ["python", "/app/healthcheck.py"]

CMD ["/app/run.sh"]
