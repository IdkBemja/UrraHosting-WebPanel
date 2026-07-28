from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, session

from ..extensions import limiter
from ..services.archive_extract import ArchiveError, extract_archive
from ..services.deployment import DeploymentError
from .auth import login_required

bp = Blueprint("deployment", __name__, url_prefix="/api/deployment")


@bp.route("/status", methods=["GET"])
@login_required
def status():
    manifest = current_app.config["DEPLOY_MANIFEST"]
    state = manifest.read()
    return jsonify(
        {
            "current": state.current.__dict__ if state.current else None,
            "previous": state.previous.__dict__ if state.previous else None,
            "history": [item.__dict__ for item in state.history[-20:]],
        }
    )


@bp.route("/upload", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def deploy_from_upload():
    if "file" not in request.files:
        return jsonify({"error": "Falta el archivo"}), 400
    upload = request.files["file"]
    if not upload.filename:
        return jsonify({"error": "Nombre de archivo vacio"}), 400

    profile_id = request.form.get("profile", "python")
    platform_config = current_app.config["INSTANCE"]
    max_bytes = platform_config.max_upload_mb * 1024 * 1024

    state_dir: Path = current_app.config["STATE_DIR"]
    stage_dir = state_dir / "_upload_stage"
    shutil.rmtree(stage_dir, ignore_errors=True)

    try:
        extract_archive(upload.filename, upload.stream, stage_dir, max_bytes)
    except ArchiveError as exc:
        shutil.rmtree(stage_dir, ignore_errors=True)
        return jsonify({"error": str(exc)}), 400

    sha = f"upload-{uuid.uuid4().hex[:12]}"
    deployment_service = current_app.config["DEPLOYMENT_SERVICE"]
    try:
        record = deployment_service.deploy_from_staging(
            stage_dir,
            sha=sha,
            source_kind="upload",
            profile_id=profile_id,
            deployed_by=session.get("username", "unknown"),
        )
    except DeploymentError as exc:
        return jsonify({"ok": False, "error": str(exc), "log_tail": exc.log_tail}), 400

    return jsonify({"ok": True, "deployment": record.__dict__})


@bp.route("/rollback", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def rollback():
    deployment_service = current_app.config["DEPLOYMENT_SERVICE"]
    try:
        record = deployment_service.rollback(session.get("username", "unknown"))
    except DeploymentError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "deployment": record.__dict__ if record else None})
