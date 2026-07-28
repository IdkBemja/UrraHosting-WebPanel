FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd -g 10002 orchestrator \
    && useradd -u 10002 -g orchestrator -m -d /home/orchestrator -s /usr/sbin/nologin orchestrator

COPY orchestrator/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Shares the platform config contract with the dashboard so both validate
# the same .env identically (see dashboard/Dockerfile for the same copy).
COPY config /app/config
COPY orchestrator/app.py /app/app.py

RUN chown -R orchestrator:orchestrator /app
USER orchestrator

# Internal only - never published through Traefik or bound to the host;
# only reachable from the dashboard over the `control` network.
CMD ["gunicorn", "--bind", "0.0.0.0:9000", "--workers", "1", "--threads", "4", "--timeout", "1900", "app:app"]
