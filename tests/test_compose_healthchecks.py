from __future__ import annotations

from pathlib import Path

import yaml

_COMPOSE_PATH = Path(__file__).resolve().parent.parent / "compose.yml"
_SERVICES_REQUIRING_HEALTHCHECK = ("dashboard", "orchestrator")


def _load_compose() -> dict:
    with _COMPOSE_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_dashboard_and_orchestrator_declare_a_healthcheck():
    compose = _load_compose()
    services = compose["services"]
    for name in _SERVICES_REQUIRING_HEALTHCHECK:
        healthcheck = services[name].get("healthcheck")
        assert healthcheck, f"service '{name}' has no healthcheck: block"
        assert healthcheck["test"][0] == "CMD", (
            f"service '{name}' healthcheck should use CMD (not CMD-SHELL/NONE) so it "
            "runs the probe as a direct process, not a shell"
        )
