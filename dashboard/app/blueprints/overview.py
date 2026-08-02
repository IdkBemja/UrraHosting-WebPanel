from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from config.runtime_profiles import list_profiles

from ..services.docker_client import DockerControlError
from .auth import login_required

bp = Blueprint("overview", __name__, url_prefix="/api")


@bp.route("/overview")
@login_required
def overview():
    config = current_app.config["INSTANCE"]
    storage = current_app.config["STORAGE"]
    docker_client = current_app.config["DOCKER_CLIENT"]
    manifest = current_app.config["DEPLOY_MANIFEST"]
    db_service = current_app.config["DATABASE_SERVICE"]

    try:
        state = docker_client.status()
    except DockerControlError as exc:
        state = {"status": "unknown", "running": False, "started_at": "", "health": None}
        docker_error = str(exc)
    else:
        docker_error = None

    deploy_state = manifest.read()

    usage = {category: storage.usage_bytes(category) for category in storage.categories}

    connection = {
        "kind": "domain" if config.public_base_domain else "ip",
        "address": f"{config.instance_id}.{config.public_base_domain}" if config.public_base_domain else _fallback_address(config),
    }

    return jsonify(
        {
            "instance_id": config.instance_id,
            "running": state["running"],
            "container_status": state["status"],
            "health": state.get("health"),
            "docker_error": docker_error,
            "deployment": {
                "current": deploy_state.current.__dict__ if deploy_state.current else None,
                "previous_available": deploy_state.previous is not None,
            },
            "database": db_service.status(),
            "resources": {
                "app_cpu_limit": config.app_cpu_limit,
                "app_memory_limit": config.app_memory_limit,
                "app_storage_limit_gb": config.app_storage_limit_gb,
                "dashboard_cpu_limit": config.dashboard_cpu_limit,
                "dashboard_memory_limit": config.dashboard_memory_limit,
            },
            "storage": {
                "usage_by_category": usage,
                "usage_bytes": sum(usage.values()),
                "quota_bytes": storage.quota_bytes,
            },
            "connection": connection,
        }
    )


def _fallback_address(config) -> str:
    forwarded_host = request.headers.get("X-Forwarded-Host", request.host).split(",", 1)[0].strip()
    return config.public_server_address or forwarded_host.rsplit(":", 1)[0]


@bp.route("/config")
@login_required
def config_view():
    config = current_app.config["INSTANCE"]
    return jsonify(
        {
            "instance_id": config.instance_id,
            "app_port": config.app_port,
            "dashboard_port": config.dashboard_port,
            "app_user": config.app_user,
            "max_upload_mb": config.max_upload_mb,
            "session_cookie_secure": config.session_cookie_secure,
            "debug": config.debug,
            "allowed_git_hosts": list(config.allowed_git_hosts),
            "allowed_db_engines": list(config.allowed_db_engines),
        }
    )


@bp.route("/profiles")
@login_required
def profiles():
    return jsonify(
        {
            "profiles": [
                {
                    "id": profile.id,
                    "label": profile.label,
                    "description": profile.description,
                    "allowed_base_images": list(profile.allowed_base_images),
                    "example_dockerfile": profile.example_dockerfile,
                }
                for profile in list_profiles()
            ]
        }
    )


@bp.route("/activity")
@login_required
def activity():
    activity_log = current_app.config["ACTIVITY"]
    return jsonify({"entries": activity_log.recent(limit=200)})


@bp.route("/version")
@login_required
def version():
    return jsonify({"version": "v1.0.0-Beta"})
