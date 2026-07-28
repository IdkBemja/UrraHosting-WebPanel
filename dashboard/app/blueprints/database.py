from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file

from ..extensions import limiter
from ..services.database import DatabaseError
from .auth import admin_required, login_required

bp = Blueprint("database", __name__, url_prefix="/api/database")


@bp.route("", methods=["GET"])
@login_required
def status():
    db_service = current_app.config["DATABASE_SERVICE"]
    return jsonify(db_service.status())


@bp.route("/provision", methods=["POST"])
@admin_required
@limiter.limit("5 per hour")
def provision():
    db_service = current_app.config["DATABASE_SERVICE"]
    instance_id = current_app.config["INSTANCE"].instance_id
    payload = request.get_json(silent=True) or {}
    engine = payload.get("engine", "")
    try:
        db_service.provision(engine, instance_id)
    except DatabaseError as exc:
        return jsonify({"error": str(exc)}), 400
    current_app.config["ACTIVITY"].record("database_provisioned", {"engine": engine})
    return jsonify(db_service.status())


@bp.route("/deprovision", methods=["POST"])
@admin_required
@limiter.limit("5 per hour")
def deprovision():
    db_service = current_app.config["DATABASE_SERVICE"]
    try:
        db_service.deprovision()
    except DatabaseError as exc:
        return jsonify({"error": str(exc)}), 400
    current_app.config["ACTIVITY"].record("database_deprovisioned", {})
    return jsonify({"ok": True})


@bp.route("/rotate", methods=["POST"])
@admin_required
@limiter.limit("10 per hour")
def rotate():
    db_service = current_app.config["DATABASE_SERVICE"]
    try:
        db_service.rotate_credentials()
    except DatabaseError as exc:
        return jsonify({"error": str(exc)}), 400
    current_app.config["ACTIVITY"].record("database_credentials_rotated", {})
    return jsonify({"ok": True})


@bp.route("/backup", methods=["POST"])
@admin_required
@limiter.limit("5 per hour")
def backup():
    db_service = current_app.config["DATABASE_SERVICE"]
    try:
        filename = db_service.backup()
    except DatabaseError as exc:
        return jsonify({"error": str(exc)}), 400
    current_app.config["ACTIVITY"].record("database_backup", {"filename": filename})
    return jsonify({"ok": True, "filename": filename})


@bp.route("/backups", methods=["GET"])
@login_required
def list_backups():
    backups_dir: Path = current_app.config["BACKUPS_DIR"]
    entries = []
    if backups_dir.is_dir():
        for entry in sorted(backups_dir.iterdir(), reverse=True):
            if entry.is_file() and not entry.is_symlink():
                stat = entry.stat()
                entries.append({"name": entry.name, "size": stat.st_size, "modified_at": stat.st_mtime})
    return jsonify({"backups": entries})


@bp.route("/backups/<name>/download", methods=["GET"])
@admin_required
def download_backup(name: str):
    backups_dir: Path = current_app.config["BACKUPS_DIR"]
    if "/" in name or "\x00" in name or name in (".", ".."):
        return jsonify({"error": "Nombre invalido"}), 400
    target = (backups_dir / name)
    if not target.is_file() or target.is_symlink() or target.parent != backups_dir:
        return jsonify({"error": "Archivo no encontrado"}), 404
    return send_file(target, as_attachment=True, download_name=name)


@bp.route("/restore", methods=["POST"])
@admin_required
@limiter.limit("5 per hour")
def restore():
    db_service = current_app.config["DATABASE_SERVICE"]
    payload = request.get_json(silent=True) or {}
    filename = payload.get("filename", "")
    try:
        db_service.restore(filename)
    except DatabaseError as exc:
        return jsonify({"error": str(exc)}), 400
    current_app.config["ACTIVITY"].record("database_restore", {"filename": filename})
    return jsonify({"ok": True})
