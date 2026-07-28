"""Container lifecycle and log streaming for the app container.

No RCON, no shell, no `docker exec` (plan.md section 5.1): the dashboard
only ever starts/stops/restarts the container and reads its stdout/stderr
logs through the scoped docker-proxy.
"""

from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from ..extensions import limiter
from ..services.docker_client import DockerControlError
from .auth import is_authenticated, login_required

bp = Blueprint("console", __name__, url_prefix="/api")


def _uptime_seconds(started_at: str) -> int | None:
    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    seconds = int((datetime.now(timezone.utc) - started).total_seconds())
    return max(seconds, 0)


@bp.route("/status")
@login_required
def status():
    docker_client = current_app.config["DOCKER_CLIENT"]
    try:
        state = docker_client.status()
    except DockerControlError as exc:
        return jsonify({"error": str(exc)}), 502

    uptime_seconds = _uptime_seconds(state["started_at"]) if state["running"] else None

    return jsonify(
        {
            "running": state["running"],
            "container_status": state["status"],
            "health": state.get("health"),
            "image": state.get("image"),
            "uptime_seconds": uptime_seconds,
        }
    )


@bp.route("/container", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def container_action():
    payload = request.get_json(silent=True) or {}
    action = payload.get("action", "")
    docker_client = current_app.config["DOCKER_CLIENT"]

    try:
        if action == "start":
            docker_client.start()
        elif action == "stop":
            docker_client.stop(timeout=30)
        elif action == "restart":
            docker_client.restart(timeout=30)
        else:
            return jsonify({"error": "Accion no valida"}), 400
    except DockerControlError as exc:
        current_app.config["ACTIVITY"].record("container_action_failed", {"action": action, "error": str(exc)})
        return jsonify({"ok": False, "error": str(exc)}), 502

    current_app.config["ACTIVITY"].record("container_action", {"action": action})
    return jsonify({"ok": True})


@bp.route("/logs/stream")
def logs_stream():
    if not is_authenticated():
        return Response("event: error\ndata: No autorizado\n\n", status=401, mimetype="text/event-stream")

    docker_client = current_app.config["DOCKER_CLIENT"]

    def generate():
        try:
            for chunk in docker_client.stream_logs(tail=100):
                text = chunk.decode("utf-8", errors="replace")
                for line in text.splitlines():
                    safe_line = line.rstrip()
                    if safe_line:
                        yield f"data: {safe_line}\n\n"
        except DockerControlError as exc:
            yield f"event: error\ndata: {exc}\n\n"

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return Response(stream_with_context(generate()), mimetype="text/event-stream", headers=headers)
