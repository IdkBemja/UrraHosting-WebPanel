from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ..extensions import limiter
from ..services.env_store import EnvStoreError, validate_vars
from .auth import login_required

bp = Blueprint("environment", __name__, url_prefix="/api/environment")


@bp.route("", methods=["GET"])
@login_required
def get_env():
    env_store = current_app.config["ENV_STORE"]
    try:
        redacted = env_store.load_redacted()
    except EnvStoreError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"variables": redacted})


@bp.route("", methods=["PUT"])
@login_required
@limiter.limit("30 per hour")
def put_env():
    env_store = current_app.config["ENV_STORE"]
    payload = request.get_json(silent=True) or {}
    variables = payload.get("variables")
    if not isinstance(variables, dict) or not all(isinstance(v, str) for v in variables.values()):
        return jsonify({"error": "El cuerpo debe ser {\"variables\": {NOMBRE: valor}}"}), 400

    result = validate_vars(variables)
    if not result.ok:
        return jsonify({"error": "; ".join(result.errors)}), 400

    try:
        env_store.save(variables)
    except EnvStoreError as exc:
        return jsonify({"error": str(exc)}), 400

    current_app.config["ACTIVITY"].record("env_updated", {"count": len(variables)})
    return jsonify({"ok": True})
