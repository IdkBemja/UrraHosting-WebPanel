from __future__ import annotations

from flask import Flask

from app.blueprints.dashboard import bp as dashboard_bp


def _client():
    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    return app.test_client()


def test_dashboard_healthz_ok_without_auth():
    response = _client().get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
