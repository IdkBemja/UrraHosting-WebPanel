"""Typed loader/validator for the platform-owned global `.env` contract
(plan.md section 4). This is the ONLY place that reads `os.environ` for
platform-level settings; every service receives a `PlatformConfig` instance
instead of touching the environment directly, so the contract can't drift
between the dashboard, the orchestrator and the entrypoint.

Mirrors the shape of the pre-migration `config/instance_config.py` module
(`load_from_environ(environ) -> (config | None, ValidationResult)`) so the
app factory's error-handling stays the same.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Values that must never be accepted as real secrets - lifted from
# .env.example so a deployment that forgot to edit the template fails
# loudly instead of shipping with a public, grep-able secret.
_KNOWN_PLACEHOLDER_SECRETS = frozenset(
    {
        "cambia-esta-contrasena-unica",
        "cambia-este-secreto-largo-y-aleatorio",
        "admin",
        "password",
        "changeme",
        "change-me",
        "secret",
    }
)

_MIN_APP_SECRET_LENGTH = 32
_MIN_APP_PASSWORD_LENGTH = 10

_DEFAULT_ALLOWED_GIT_HOSTS = ("github.com", "gitlab.com", "bitbucket.org")
_DEFAULT_MYSQL_IMAGE = "mysql:8.4"
_DEFAULT_POSTGRES_IMAGE = "postgres:16-alpine"
_DEFAULT_ALLOWED_DB_ENGINES = ("mysql", "postgres")


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class PlatformConfig:
    # Identity / filesystem
    instance_id: str
    data_dir: Path

    # Network (immutable port contract - section 4.1)
    dashboard_port: int
    app_port: int

    # Public access / Traefik
    public_base_domain: str
    public_server_address: str
    traefik_public_network: str
    traefik_web_entrypoint: str
    trusted_proxy_count: int

    # Dashboard auth
    app_user: str
    app_password: str
    app_secret: str
    session_cookie_secure: bool
    debug: bool
    max_upload_mb: int

    # Resources: application container
    app_cpu_limit: str
    app_cpu_reservation: str
    app_memory_limit: str
    app_memory_reservation: str
    app_storage_limit_gb: int

    # Resources: dashboard container
    dashboard_cpu_limit: str
    dashboard_memory_limit: str

    # Deployment policy
    allowed_git_hosts: tuple[str, ...]
    allowed_db_engines: tuple[str, ...]
    mysql_image: str
    postgres_image: str


def _get(environ: dict, name: str, default: str | None = None) -> str | None:
    value = environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _require_int(environ: dict, name: str, errors: list[str], *, minimum: int | None = None) -> int | None:
    raw = _get(environ, name)
    if raw is None:
        errors.append(f"Falta la variable obligatoria {name}")
        return None
    try:
        value = int(raw)
    except ValueError:
        errors.append(f"{name} debe ser un entero, recibido: {raw!r}")
        return None
    if minimum is not None and value < minimum:
        errors.append(f"{name} debe ser >= {minimum}")
        return None
    return value


def _require_str(environ: dict, name: str, errors: list[str]) -> str | None:
    value = _get(environ, name)
    if not value:
        errors.append(f"Falta la variable obligatoria {name}")
        return None
    return value


def _bool(environ: dict, name: str, default: bool) -> bool:
    raw = _get(environ, name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _split_csv(raw: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not raw:
        return default
    items = tuple(item.strip().lower() for item in raw.split(",") if item.strip())
    return items or default


def load_from_environ(environ: dict) -> tuple[PlatformConfig | None, ValidationResult]:
    result = ValidationResult()

    instance_id = _require_str(environ, "INSTANCE_ID", result.errors)
    data_dir_raw = _require_str(environ, "DATA_DIR", result.errors)
    dashboard_port = _require_int(environ, "DASHBOARD_PORT", result.errors, minimum=1)
    app_port = _require_int(environ, "APP_PORT", result.errors, minimum=1)
    app_user = _require_str(environ, "APP_USER", result.errors)
    app_password = _require_str(environ, "APP_PASSWORD", result.errors)
    app_secret = _require_str(environ, "APP_SECRET", result.errors)
    max_upload_mb = _require_int(environ, "MAX_UPLOAD_MB", result.errors, minimum=1)
    app_storage_limit_gb = _require_int(environ, "APP_STORAGE_LIMIT_GB", result.errors, minimum=1)

    if dashboard_port is not None and app_port is not None and dashboard_port == app_port:
        result.errors.append("DASHBOARD_PORT y APP_PORT no pueden ser el mismo puerto")

    if app_secret and (
        len(app_secret) < _MIN_APP_SECRET_LENGTH or app_secret.strip().lower() in _KNOWN_PLACEHOLDER_SECRETS
    ):
        result.errors.append(
            f"APP_SECRET es demasiado corto o es un valor de ejemplo; usa al menos {_MIN_APP_SECRET_LENGTH} "
            "caracteres aleatorios (python -c \"import secrets; print(secrets.token_hex(32))\")"
        )

    if app_password and (
        len(app_password) < _MIN_APP_PASSWORD_LENGTH or app_password.strip().lower() in _KNOWN_PLACEHOLDER_SECRETS
    ):
        result.errors.append(
            f"APP_PASSWORD es demasiado corto o es un valor de ejemplo; usa al menos {_MIN_APP_PASSWORD_LENGTH} caracteres"
        )

    if app_user and app_user.strip().lower() in {"root", "administrator"}:
        result.warnings.append(f"APP_USER='{app_user}' es un nombre de usuario predecible")

    trusted_proxy_count = _require_int(environ, "TRUSTED_PROXY_COUNT", [], minimum=0) or 1

    session_cookie_secure = _bool(environ, "SESSION_COOKIE_SECURE", True)
    if not session_cookie_secure:
        result.warnings.append("SESSION_COOKIE_SECURE=false expone la cookie de sesion sin HTTPS; solo usar en desarrollo local")

    debug = _bool(environ, "DEBUG", False)
    if debug:
        result.warnings.append("DEBUG=true no debe usarse en produccion")

    allowed_git_hosts = _split_csv(_get(environ, "ALLOWED_GIT_HOSTS"), _DEFAULT_ALLOWED_GIT_HOSTS)
    allowed_db_engines = _split_csv(_get(environ, "ALLOWED_DB_ENGINES"), _DEFAULT_ALLOWED_DB_ENGINES)
    for engine in allowed_db_engines:
        if engine not in {"mysql", "postgres"}:
            result.errors.append(f"ALLOWED_DB_ENGINES contiene un motor no soportado: {engine}")

    if not result.ok:
        return None, result

    config = PlatformConfig(
        instance_id=instance_id,
        data_dir=Path(data_dir_raw),
        dashboard_port=dashboard_port,
        app_port=app_port,
        public_base_domain=_get(environ, "PUBLIC_BASE_DOMAIN", "") or "",
        public_server_address=_get(environ, "PUBLIC_SERVER_ADDRESS", "") or "",
        traefik_public_network=_get(environ, "TRAEFIK_PUBLIC_NETWORK", "traefik-public") or "traefik-public",
        traefik_web_entrypoint=_get(environ, "TRAEFIK_WEB_ENTRYPOINT", "websecure") or "websecure",
        trusted_proxy_count=trusted_proxy_count,
        app_user=app_user,
        app_password=app_password,
        app_secret=app_secret,
        session_cookie_secure=session_cookie_secure,
        debug=debug,
        max_upload_mb=max_upload_mb,
        app_cpu_limit=_get(environ, "APP_CPU_LIMIT", "1.0") or "1.0",
        app_cpu_reservation=_get(environ, "APP_CPU_RESERVATION", "0.25") or "0.25",
        app_memory_limit=_get(environ, "APP_MEMORY_LIMIT", "512M") or "512M",
        app_memory_reservation=_get(environ, "APP_MEMORY_RESERVATION", "128M") or "128M",
        app_storage_limit_gb=app_storage_limit_gb,
        dashboard_cpu_limit=_get(environ, "DASHBOARD_CPU_LIMIT", "0.50") or "0.50",
        dashboard_memory_limit=_get(environ, "DASHBOARD_MEMORY_LIMIT", "256M") or "256M",
        allowed_git_hosts=allowed_git_hosts,
        allowed_db_engines=allowed_db_engines,
        mysql_image=_get(environ, "MYSQL_IMAGE", _DEFAULT_MYSQL_IMAGE) or _DEFAULT_MYSQL_IMAGE,
        postgres_image=_get(environ, "POSTGRES_IMAGE", _DEFAULT_POSTGRES_IMAGE) or _DEFAULT_POSTGRES_IMAGE,
    )
    return config, result


__all__ = ["PlatformConfig", "ValidationResult", "load_from_environ"]
