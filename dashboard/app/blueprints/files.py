from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, Response, current_app, jsonify, request

from ..extensions import limiter
from ..services.storage import PathTraversalError, StorageError
from .auth import login_required

bp = Blueprint("files", __name__, url_prefix="/api/files")


@bp.route("/<category>")
@login_required
def list_files(category: str):
    storage = current_app.config["STORAGE"]
    relative_path = request.args.get("path", "")
    try:
        entries = storage.list_dir(category, relative_path)
    except (PathTraversalError, StorageError) as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(
        {
            "category": category,
            "path": relative_path,
            "writable": storage.is_writable(category),
            "entries": [asdict(entry) for entry in entries],
            "usage_bytes": storage.usage_bytes(),
            "quota_bytes": storage.quota_bytes,
        }
    )


@bp.route("/<category>/upload", methods=["POST"])
@login_required
@limiter.limit("30 per hour")
def upload_file(category: str):
    storage = current_app.config["STORAGE"]
    if "file" not in request.files:
        return jsonify({"error": "Falta el archivo"}), 400

    upload = request.files["file"]
    if not upload.filename:
        return jsonify({"error": "Nombre de archivo vacio"}), 400

    relative_dir = request.form.get("path", "")
    overwrite = request.form.get("overwrite", "false").lower() == "true"

    try:
        saved_path = storage.save_upload(
            category,
            relative_dir,
            upload.filename,
            upload.stream,
            request.content_length,
            overwrite=overwrite,
        )
    except (PathTraversalError, StorageError) as exc:
        return jsonify({"error": str(exc)}), 400

    current_app.config["ACTIVITY"].record("file_upload", {"category": category, "path": saved_path})
    return jsonify({"ok": True, "path": saved_path})


@bp.route("/<category>/download")
@login_required
def download_file(category: str):
    storage = current_app.config["STORAGE"]
    relative_path = request.args.get("path", "")
    try:
        data = storage.read_file(category, relative_path)
    except (PathTraversalError, StorageError) as exc:
        return jsonify({"error": str(exc)}), 404

    filename = relative_path.rsplit("/", 1)[-1] or "download"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(data, mimetype="application/octet-stream", headers=headers)


@bp.route("/<category>/delete", methods=["POST"])
@login_required
def delete_file(category: str):
    storage = current_app.config["STORAGE"]
    payload = request.get_json(silent=True) or {}
    relative_path = payload.get("path", "")

    try:
        storage.delete(category, relative_path)
    except (PathTraversalError, StorageError) as exc:
        return jsonify({"error": str(exc)}), 400

    current_app.config["ACTIVITY"].record("file_delete", {"category": category, "path": relative_path})
    return jsonify({"ok": True})
