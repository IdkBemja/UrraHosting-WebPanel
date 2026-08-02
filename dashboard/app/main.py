from __future__ import annotations

import os
import time
from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from config.platform_config import load_from_environ

from .blueprints.auth import bp as auth_bp
from .blueprints.console import bp as console_bp
from .blueprints.dashboard import bp as dashboard_bp
from .blueprints.database import bp as database_bp
from .blueprints.deployment import bp as deployment_bp
from .blueprints.environment import bp as environment_bp
from .blueprints.files import bp as files_bp
from .blueprints.overview import bp as overview_bp
from .blueprints.repository import bp as repository_bp
from .blueprints.users import bp as users_bp
from .extensions import csrf, limiter
from .services.activity import ActivityLog
from .services.database import DatabaseService
from .services.deploy_manifest import DeployManifest
from .services.deploy_progress import DeployProgressTracker
from .services.deployment import DeploymentService
from .services.docker_client import InstanceDockerClient
from .services.env_store import EnvStore, derive_key
from .services.orchestrator_client import OrchestratorClient
from .services.repository_state import RepositoryState
from .services.storage import InstanceStorage
from .services.users import UserStore

_DATA_ROOT = Path("/data")


def create_app() -> Flask:
    config, result = load_from_environ(os.environ)
    if config is None:
        raise RuntimeError("Configuracion de instancia invalida: " + "; ".join(result.errors))
    for warning in result.warnings:
        print(f"[dashboard][warn] {warning}")

    app = Flask(__name__, template_folder="templates", static_folder="static")

    # Only trust X-Forwarded-* from exactly the number of reverse proxy
    # hops in front of this container (Traefik == 1). Trusting an
    # unbounded number of hops would let a client spoof its own IP by
    # sending a fake X-Forwarded-For and defeat rate limiting entirely.
    if config.trusted_proxy_count > 0:
        app.wsgi_app = ProxyFix(  # type: ignore[assignment]
            app.wsgi_app,
            x_for=config.trusted_proxy_count,
            x_proto=config.trusted_proxy_count,
            x_host=config.trusted_proxy_count,
        )

    app.config["SECRET_KEY"] = config.app_secret
    app.config["APP_USER"] = config.app_user
    app.config["APP_PASSWORD"] = config.app_password
    app.config["INSTANCE"] = config

    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = config.session_cookie_secure
    app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 12
    app.config["MAX_CONTENT_LENGTH"] = config.max_upload_mb * 1024 * 1024

    csrf.init_app(app)
    limiter.init_app(app)

    state_dir = _DATA_ROOT / "state"
    secrets_dir = _DATA_ROOT / "secrets"
    backups_dir = _DATA_ROOT / "backups"
    source_dir = _DATA_ROOT / "source"
    staging_dir = _DATA_ROOT / "staging"
    panel_dir = _DATA_ROOT / "panel"
    for directory in (state_dir, secrets_dir, backups_dir, source_dir, staging_dir, panel_dir):
        directory.mkdir(parents=True, exist_ok=True)

    storage = InstanceStorage(
        roots={"staging": staging_dir, "source": source_dir},
        max_upload_bytes=app.config["MAX_CONTENT_LENGTH"],
        quota_bytes=config.app_storage_limit_gb * 1024 * 1024 * 1024,
        writable_categories=frozenset({"staging"}),
    )
    app.config["STORAGE"] = storage
    app.config["STATE_DIR"] = state_dir
    app.config["SECRETS_DIR"] = secrets_dir
    app.config["BACKUPS_DIR"] = backups_dir

    app.config["ACTIVITY"] = ActivityLog(state_dir / "activity.log")
    app.config["USERS"] = UserStore(panel_dir / "panel-users.sqlite3", config.app_user, config.app_password)

    app.config["DOCKER_CLIENT"] = InstanceDockerClient(
        docker_host=os.environ["DOCKER_HOST"],
        container_name=f"{config.instance_id}_app",
        instance_id=config.instance_id,
    )

    fernet_key = derive_key(config.app_secret, config.instance_id)
    env_store = EnvStore(state_dir / "app.env.enc", fernet_key)
    app.config["ENV_STORE"] = env_store

    manifest = DeployManifest(state_dir)
    app.config["DEPLOY_MANIFEST"] = manifest

    app.config["REPOSITORY_STATE"] = RepositoryState(state_dir / "repository.json")

    orchestrator = OrchestratorClient(os.environ["ORCHESTRATOR_URL"], os.environ["ORCHESTRATOR_TOKEN"])
    app.config["ORCHESTRATOR"] = orchestrator

    database_service = DatabaseService(state_dir / "db.enc", fernet_key, orchestrator, config.allowed_db_engines)
    app.config["DATABASE_SERVICE"] = database_service

    deploy_progress = DeployProgressTracker()
    app.config["DEPLOY_PROGRESS"] = deploy_progress

    app.config["DEPLOYMENT_SERVICE"] = DeploymentService(
        source_dir=source_dir,
        state_dir=state_dir,
        manifest=manifest,
        env_store=env_store,
        orchestrator=orchestrator,
        activity=app.config["ACTIVITY"],
        db_env_provider=database_service.connection_env,
        progress=deploy_progress,
    )

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(console_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(overview_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(deployment_bp)
    app.register_blueprint(repository_bp)
    app.register_blueprint(environment_bp)
    app.register_blueprint(database_bp)

    _install_security_headers(app, config)

    @app.context_processor
    def inject_current_year():
        return {"current_year": time.strftime("%Y", time.gmtime())}

    return app


def _install_security_headers(app: Flask, config) -> None:
    csp = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "font-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )

    @app.after_request
    def set_security_headers(response):
        response.headers["Content-Security-Policy"] = csp
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), interest-cohort=()"
        if config.session_cookie_secure:
            # No includeSubDomains: this dashboard is reachable under the
            # shared platform base domain (one of many tenant instances)
            # AND under whichever custom domain/subdomain a customer points
            # at it later. Pinning HSTS for every subdomain of either would
            # poison unrelated tenants/subdomains for up to 2 years in any
            # browser that ever loaded this instance once, and would hard
            # -lock out a freshly assigned custom subdomain in any browser
            # if its certificate isn't issued yet when first visited.
            response.headers["Strict-Transport-Security"] = "max-age=63072000"
        return response


app = create_app()


if __name__ == "__main__":
    instance = app.config["INSTANCE"]
    app.run(host="0.0.0.0", port=instance.dashboard_port, debug=instance.debug)
