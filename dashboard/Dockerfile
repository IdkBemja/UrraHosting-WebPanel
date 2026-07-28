FROM python:3.12-slim

ARG DASHBOARD_PORT=5230
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client \
	&& rm -rf /var/lib/apt/lists/* \
	&& groupadd -g 10001 dashboard \
	&& useradd -u 10001 -g dashboard -m -d /home/dashboard -s /usr/sbin/nologin dashboard

COPY dashboard/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Shared config validator (see config/platform_config.py) so the dashboard
# and the orchestrator validate the same .env contract identically.
COPY config /app/config
COPY dashboard/app /app/app

RUN chown -R dashboard:dashboard /app

EXPOSE ${DASHBOARD_PORT}

# USER is baked into the image (not dropped at runtime via gosu/su) on
# purpose: the container runs with cap_drop: ALL (compose.yml), which
# removes CAP_SETUID/CAP_SETGID needed for any root->non-root privilege
# drop at start time. app.main:create_app() creates its own subdirectories
# under /data on first run, so nothing here needs to run as root at all -
# see dockerhost-fixes.md for the corresponding host-side requirement that
# /data/<instance>/{state,secrets,backups,source,staging,panel} be owned
# by (or writable by) uid/gid 10001 on the real Docker host.
USER dashboard

# Single worker on purpose: Flask-Limiter's in-memory storage and the
# deploy-manifest/db-state file locks (services/deploy_manifest.py,
# services/database.py) are process-local, so this stays a single-worker,
# multi-threaded process rather than pretending to scale across workers
# that wouldn't share that state.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${DASHBOARD_PORT:-5230} --workers 1 --threads 8 --timeout 1900 app.main:app"]
