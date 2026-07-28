"""Single source of truth for environment variable names/prefixes the user
can never set through `app.env` (plan.md section 4.1). Both the UI-facing
validation (env_store.py) and the deploy-time defense in depth check
(deployment.py) import this list instead of keeping their own copies, so a
reserved name can never be blocked in one place and accepted in the other.
"""

from __future__ import annotations

# Exact variable names that are always rejected in app.env, regardless of
# case. These are platform-owned listen/identity contracts or would leak
# platform secrets/paths into user-controlled process environment.
RESERVED_EXACT_NAMES: frozenset[str] = frozenset(
    {
        # Listen/port contract (section 4.1): only the platform's APP_PORT
        # may set the port the app must bind to.
        "PORT",
        "APP_PORT",
        "HOST",
        "BIND",
        "LISTEN_ADDR",
        "LISTEN_PORT",
        # Platform/orchestrator identity and secrets.
        "INSTANCE_ID",
        "APP_SECRET",
        "APP_USER",
        "APP_PASSWORD",
        "DATA_DIR",
        # Runtime/process control that could be used to break out of the
        # locked-down entrypoint or hijack the app's own process.
        "PATH",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "NODE_OPTIONS",
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        # Managed database connection: injected by the platform after
        # provisioning (section 8.4); the user cannot set or override it.
        "DATABASE_URL",
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
        "DB_ENGINE",
    }
)

# Variable *prefixes* that are always rejected, matched case-insensitively.
RESERVED_PREFIXES: tuple[str, ...] = (
    "DOCKER_",
    "COMPOSE_",
    "TRAEFIK_",
    "MYSQL_",
    "POSTGRES_",
    "PGDATA",
    "SESSION_",
    "FLASK_",
    "GUNICORN_",
    "APP_PORT_",
)


def is_reserved(name: str) -> bool:
    upper = name.strip().upper()
    if not upper:
        return True
    if upper in RESERVED_EXACT_NAMES:
        return True
    return any(upper.startswith(prefix) for prefix in RESERVED_PREFIXES)


def reserved_reason(name: str) -> str:
    return (
        f"'{name}' es una variable reservada por la plataforma y no puede "
        "definirse desde app.env."
    )
