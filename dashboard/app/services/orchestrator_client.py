"""HTTP client for the deployment orchestrator (plan.md section 3, item 5).

The dashboard has no Docker access of its own for build/deploy/database
operations - it only ever asks the orchestrator, over the `control`
network, to carry out one of a small fixed set of actions. The client is
deliberately synchronous/blocking (the orchestrator itself owns any
polling loops, e.g. health-check retries) so callers in blueprints never
need their own retry logic.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class OrchestratorError(RuntimeError):
    pass


class OrchestratorClient:
    def __init__(self, base_url: str, token: str, timeout: float = 300.0):
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        body = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body if method != "GET" else None,
            method=method,
            headers={
                "Content-Type": "application/json",
                "X-Internal-Token": self._token,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(detail)
                message = parsed.get("error", detail)
            except ValueError:
                message = detail
            raise OrchestratorError(f"Orquestador respondio {exc.code}: {message[:500]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OrchestratorError(f"No se pudo contactar al orquestador: {exc}") from exc

        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise OrchestratorError("Respuesta invalida del orquestador") from exc

    def build_image(self, sha: str, dockerfile_path: str) -> dict[str, Any]:
        return self.call("POST", "/build", {"sha": sha, "dockerfile_path": dockerfile_path})

    def start_candidate(self, image_tag: str, env: dict[str, str], health_path: str) -> dict[str, Any]:
        return self.call(
            "POST",
            "/candidate/start",
            {"image_tag": image_tag, "env": env, "health_path": health_path},
        )

    def probe_candidate_health(self, candidate_name: str, health_path: str) -> dict[str, Any]:
        return self.call(
            "POST",
            "/candidate/probe-health",
            {"candidate_name": candidate_name, "health_path": health_path},
        )

    def discard_candidate(self, candidate_name: str) -> dict[str, Any]:
        return self.call("POST", "/candidate/discard", {"candidate_name": candidate_name})

    def activate(self, image_tag: str, env: dict[str, str]) -> dict[str, Any]:
        return self.call("POST", "/activate", {"image_tag": image_tag, "env": env})

    def db_provision(self, engine: str, db_name: str, db_user: str, db_password: str) -> dict[str, Any]:
        return self.call(
            "POST",
            "/db/provision",
            {"engine": engine, "db_name": db_name, "db_user": db_user, "db_password": db_password},
        )

    def db_deprovision(self, engine: str) -> dict[str, Any]:
        return self.call("POST", "/db/deprovision", {"engine": engine})

    def db_rotate(self, engine: str, db_name: str, db_user: str, current_password: str, new_password: str) -> dict[str, Any]:
        return self.call(
            "POST",
            "/db/rotate",
            {
                "engine": engine,
                "db_name": db_name,
                "db_user": db_user,
                "current_password": current_password,
                "new_password": new_password,
            },
        )

    def db_backup(self, engine: str, db_name: str, db_user: str, db_password: str, backup_filename: str) -> dict[str, Any]:
        return self.call(
            "POST",
            "/db/backup",
            {
                "engine": engine,
                "db_name": db_name,
                "db_user": db_user,
                "db_password": db_password,
                "backup_filename": backup_filename,
            },
        )

    def db_restore(self, engine: str, db_name: str, db_user: str, db_password: str, backup_filename: str) -> dict[str, Any]:
        return self.call(
            "POST",
            "/db/restore",
            {
                "engine": engine,
                "db_name": db_name,
                "db_user": db_user,
                "db_password": db_password,
                "backup_filename": backup_filename,
            },
        )

    def db_status(self, engine: str) -> dict[str, Any]:
        return self.call("POST", "/db/status", {"engine": engine})
