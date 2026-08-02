from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request, session, stream_with_context

from ..extensions import limiter
from ..services.archive_extract import ArchiveError, extract_archive
from ..services.deployment import DeploymentError
from .auth import is_authenticated, login_required

bp = Blueprint("deployment", __name__, url_prefix="/api/deployment")

# Safety net for the progress stream in case a deploy's terminal state is
# somehow never reached (e.g. worker killed mid-deploy) - covers the
# orchestrator's own 30-minute build timeout plus slack, so a stuck stream
# doesn't hold a thread forever (dashboard/Dockerfile: only 8 threads total).
_PROGRESS_STREAM_MAX_SECONDS = 40 * 60


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


@bp.route("/progress/stream")
def progress_stream():
    # Manual auth check (not @login_required) so an unauthenticated request
    # gets a well-formed SSE error frame instead of a redirect/JSON body the
    # EventSource client can't do anything useful with - same pattern as
    # console.py's /api/logs/stream.
    if not is_authenticated():
        return Response("event: error\ndata: No autorizado\n\n", status=401, mimetype="text/event-stream")

    tracker = current_app.config["DEPLOY_PROGRESS"]

    def generate():
        deadline = time.monotonic() + _PROGRESS_STREAM_MAX_SECONDS
        state, last_version = tracker.snapshot()
        yield f"data: {json.dumps(state.to_dict())}\n\n"
        seen_active = state.active
        while time.monotonic() < deadline:
            time.sleep(0.5)
            state, version = tracker.snapshot()
            if version == last_version:
                continue
            last_version = version
            yield f"data: {json.dumps(state.to_dict())}\n\n"
            if state.active:
                seen_active = True
            elif seen_active and state.status in ("success", "failed"):
                return
        yield 'event: error\ndata: {"message": "Tiempo de espera agotado"}\n\n'

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return Response(stream_with_context(generate()), mimetype="text/event-stream", headers=headers)


@bp.route("/upload", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def deploy_from_upload():
    if "file" not in request.files:
        return jsonify({"error": "Falta el archivo"}), 400
    upload = request.files["file"]
    if not upload.filename:
        return jsonify({"error": "Nombre de archivo vacio"}), 400

    if current_app.config["DEPLOY_PROGRESS"].is_active():
        return jsonify({"error": "Ya hay un despliegue en curso"}), 409

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
