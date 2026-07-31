from __future__ import annotations

import importlib.util
from pathlib import Path

_ORCHESTRATOR_APP_PATH = Path(__file__).resolve().parent.parent / "orchestrator" / "app.py"

# Same required fields as tests/test_platform_config.py's _BASE_ENV, plus the
# orchestrator-only variables that orchestrator/app.py reads at import time.
_ENV = {
    "INSTANCE_ID": "test-instance",
    "DATA_DIR": "/data",
    "DASHBOARD_PORT": "5230",
    "APP_PORT": "5231",
    "APP_USER": "admin",
    "APP_PASSWORD": "a-strong-unique-password-123",
    "APP_SECRET": "a" * 40,
    "MAX_UPLOAD_MB": "1024",
    "APP_STORAGE_LIMIT_GB": "10",
    "ORCHESTRATOR_TOKEN": "orchestrator-test-token",
    "DOCKER_HOST": "tcp://docker-proxy-orchestrator:2375",
    "DATA_DIR_HOST_ABS": "/srv/urrahosting/instances/test-instance",
    "RUNTIME_NETWORK_NAME": "test-instance_runtime",
}


def _load_orchestrator_app(monkeypatch):
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)
    # Loaded via file path under a name distinct from the dashboard's `app`
    # package (already on sys.path via conftest.py) so the two never collide
    # in sys.modules despite both being called "app".
    spec = importlib.util.spec_from_file_location("orchestrator_app_under_test", _ORCHESTRATOR_APP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_healthz_is_exempt_from_the_internal_token_check(monkeypatch):
    module = _load_orchestrator_app(monkeypatch)
    response = module.app.test_client().get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_other_routes_still_require_the_internal_token(monkeypatch):
    module = _load_orchestrator_app(monkeypatch)
    response = module.app.test_client().post("/build", json={})
    assert response.status_code == 401
