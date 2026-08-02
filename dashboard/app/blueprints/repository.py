from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, session

from ..extensions import limiter
from ..services import git_repository
from ..services.deployment import DeploymentError
from ..services.git_repository import GitRepositoryError
from ..services.repository_state import RepositoryConfig
from .auth import login_required

bp = Blueprint("repository", __name__, url_prefix="/api/repository")


def _redacted_config(config: RepositoryConfig) -> dict:
    return {
        "url": config.url,
        "ref": config.ref,
        "is_ssh": config.is_ssh,
        "has_deploy_key": bool(config.deploy_public_key),
        "deploy_public_key": config.deploy_public_key,
        "known_host_pending": bool(config.known_host_pending),
        "known_host_pending_fingerprints": config.known_host_pending_fingerprints or [],
        "known_host_confirmed": config.known_host_confirmed,
        "last_checked_sha": config.last_checked_sha,
        "last_checked_at": config.last_checked_at,
    }


@bp.route("", methods=["GET"])
@login_required
def get_repository():
    state = current_app.config["REPOSITORY_STATE"]
    return jsonify(_redacted_config(state.load()))


@bp.route("", methods=["PUT"])
@login_required
def set_repository():
    state = current_app.config["REPOSITORY_STATE"]
    platform_config = current_app.config["INSTANCE"]
    payload = request.get_json(silent=True) or {}
    url = str(payload.get("url", ""))
    ref = str(payload.get("ref", "main"))

    try:
        parsed = git_repository.parse_and_validate_url(url, platform_config.allowed_git_hosts)
        ref = git_repository.validate_ref(ref)
    except GitRepositoryError as exc:
        return jsonify({"error": str(exc)}), 400

    config = state.load()
    config.url = parsed.normalized
    config.ref = ref
    config.is_ssh = parsed.is_ssh
    if not parsed.is_ssh:
        config.known_host_pending = ""
        config.known_host_pending_fingerprints = []
        config.known_host_confirmed = False
    state.save(config)
    current_app.config["ACTIVITY"].record("repository_configured", {"host": parsed.host, "ref": ref})
    return jsonify(_redacted_config(config))


@bp.route("/deploy-key", methods=["POST"])
@login_required
def generate_deploy_key():
    state = current_app.config["REPOSITORY_STATE"]
    secrets_dir: Path = current_app.config["SECRETS_DIR"]
    instance_id = current_app.config["INSTANCE"].instance_id

    private_bytes, public_str = git_repository.generate_deploy_key(f"urrahosting-{instance_id}")
    secrets_dir.mkdir(parents=True, exist_ok=True)
    key_path = secrets_dir / "deploy_key"
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(private_bytes)

    config = state.load()
    config.deploy_public_key = public_str
    state.save(config)
    current_app.config["ACTIVITY"].record("deploy_key_generated", {})
    return jsonify({"ok": True, "public_key": public_str})


@bp.route("/host-key/scan", methods=["POST"])
@login_required
def scan_host_key():
    state = current_app.config["REPOSITORY_STATE"]
    config = state.load()
    if not config.url:
        return jsonify({"error": "Configura primero la URL del repositorio"}), 400

    platform_config = current_app.config["INSTANCE"]
    try:
        parsed = git_repository.parse_and_validate_url(config.url, platform_config.allowed_git_hosts)
        raw = git_repository.scan_host_key(parsed.host)
        fingerprints = git_repository.fingerprint_known_hosts(raw)
    except GitRepositoryError as exc:
        return jsonify({"error": str(exc)}), 400

    config.known_host_pending = raw
    config.known_host_pending_fingerprints = fingerprints
    config.known_host_confirmed = False
    state.save(config)
    return jsonify({"ok": True, "fingerprints": fingerprints})


@bp.route("/host-key/trust", methods=["POST"])
@login_required
def trust_host_key():
    state = current_app.config["REPOSITORY_STATE"]
    secrets_dir: Path = current_app.config["SECRETS_DIR"]
    config = state.load()
    if not config.known_host_pending:
        return jsonify({"error": "No hay una llave de host pendiente de confirmar"}), 400

    secrets_dir.mkdir(parents=True, exist_ok=True)
    known_hosts_path = secrets_dir / "known_hosts"
    known_hosts_path.write_text(config.known_host_pending, encoding="utf-8")
    known_hosts_path.chmod(0o600)

    config.known_host_confirmed = True
    state.save(config)
    current_app.config["ACTIVITY"].record("host_key_trusted", {"fingerprints": config.known_host_pending_fingerprints})
    return jsonify({"ok": True})


def _ssh_paths(app_config) -> tuple[Path | None, Path | None]:
    secrets_dir: Path = app_config["SECRETS_DIR"]
    key_path = secrets_dir / "deploy_key"
    known_hosts_path = secrets_dir / "known_hosts"
    if key_path.exists() and known_hosts_path.exists():
        return key_path, known_hosts_path
    return None, None


@bp.route("/refs", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def list_refs():
    state = current_app.config["REPOSITORY_STATE"]
    config = state.load()
    if not config.url:
        return jsonify({"error": "Configura primero la URL del repositorio"}), 400
    if config.is_ssh and not config.known_host_confirmed:
        return jsonify({"error": "Confirma la huella del host SSH antes de continuar"}), 400

    key_path, known_hosts_path = _ssh_paths(current_app.config)
    try:
        branches, tags = git_repository.list_remote_refs(
            config.url, private_key_path=key_path, known_hosts_path=known_hosts_path
        )
    except GitRepositoryError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"branches": branches, "tags": tags})


@bp.route("/check", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def check_updates():
    state = current_app.config["REPOSITORY_STATE"]
    config = state.load()
    if not config.url:
        return jsonify({"error": "Configura primero un repositorio"}), 400
    if config.is_ssh and not config.known_host_confirmed:
        return jsonify({"error": "Confirma la huella del host SSH antes de continuar"}), 400

    key_path, known_hosts_path = _ssh_paths(current_app.config)
    try:
        remote_sha = git_repository.ls_remote_sha(
            config.url, config.ref, private_key_path=key_path, known_hosts_path=known_hosts_path
        )
    except GitRepositoryError as exc:
        return jsonify({"error": str(exc)}), 400

    config.last_checked_sha = remote_sha
    config.last_checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state.save(config)

    manifest = current_app.config["DEPLOY_MANIFEST"]
    deploy_state = manifest.read()
    deployed_sha = deploy_state.current.sha if deploy_state.current else None
    return jsonify({"remote_sha": remote_sha, "deployed_sha": deployed_sha, "changes_available": remote_sha != deployed_sha})


@bp.route("/deploy", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def deploy_from_repository():
    state = current_app.config["REPOSITORY_STATE"]
    config = state.load()
    if not config.url:
        return jsonify({"error": "Configura primero un repositorio"}), 400
    if config.is_ssh and not config.known_host_confirmed:
        return jsonify({"error": "Confirma la huella del host SSH antes de continuar"}), 400

    if current_app.config["DEPLOY_PROGRESS"].is_active():
        return jsonify({"error": "Ya hay un despliegue en curso"}), 409

    payload = request.get_json(silent=True) or {}
    profile_id = str(payload.get("profile", "python"))

    key_path, known_hosts_path = _ssh_paths(current_app.config)
    state_dir: Path = current_app.config["STATE_DIR"]
    clone_dir = state_dir / "_git_clone"
    shutil.rmtree(clone_dir, ignore_errors=True)

    try:
        sha = git_repository.clone_at_ref(
            config.url, config.ref, clone_dir, private_key_path=key_path, known_hosts_path=known_hosts_path
        )
    except GitRepositoryError as exc:
        shutil.rmtree(clone_dir, ignore_errors=True)
        return jsonify({"error": str(exc)}), 400

    # The clone's own .git metadata never becomes part of the build
    # context - only the working tree does.
    shutil.rmtree(clone_dir / ".git", ignore_errors=True)

    deployment_service = current_app.config["DEPLOYMENT_SERVICE"]
    try:
        record = deployment_service.deploy_from_staging(
            clone_dir,
            sha=sha,
            source_kind="git",
            profile_id=profile_id,
            deployed_by=session.get("username", "unknown"),
        )
    except DeploymentError as exc:
        return jsonify({"ok": False, "error": str(exc), "log_tail": exc.log_tail}), 400

    return jsonify({"ok": True, "deployment": record.__dict__})
