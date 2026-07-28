#!/bin/sh
# Reference entrypoint (plan.md section 5.2). Applications are NOT required
# to use this file - they bring their own Dockerfile - but it documents the
# contract the platform enforces at deploy time and is a safe default for
# apps that don't already drop privileges themselves:
#
#   1. APP_PORT MUST be set by the platform before this runs. If it is
#      missing, refuse to start instead of silently binding to a
#      guessable/default port.
#   2. Never run the application as root, even if the image's default user
#      is root - `gosu` drops to the fixed non-root uid/gid below.
#   3. Exec (not fork) the real command, so it becomes PID 1 and receives
#      signals (SIGTERM on stop/redeploy) directly instead of a shell
#      swallowing them.
set -eu

if [ -z "${APP_PORT:-}" ]; then
    echo "entrypoint: APP_PORT no esta definido; el contrato de plataforma lo exige" >&2
    exit 1
fi

APP_UID="${APP_UID:-10001}"
APP_GID="${APP_GID:-10001}"

if [ "$(id -u)" = "0" ] && command -v gosu >/dev/null 2>&1; then
    exec gosu "${APP_UID}:${APP_GID}" "$@"
fi

exec "$@"
