from __future__ import annotations

from flask import Blueprint, jsonify, redirect, render_template, url_for

from .auth import is_authenticated

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def root():
    if is_authenticated():
        return redirect(url_for("dashboard.index"))
    return redirect(url_for("auth.login"))


@bp.route("/healthz")
def healthz():
    # Deliberately unauthenticated and independent of the orchestrator: this
    # only confirms the dashboard's own gunicorn worker is up and serving
    # requests, so docker-host's health wait (compose.yml healthcheck:) can
    # detect a dashboard that started but never bound its port.
    return jsonify({"ok": True})


@bp.route("/dashboard")
def index():
    if not is_authenticated():
        return redirect(url_for("auth.login"))
    return render_template("dashboard.html")
