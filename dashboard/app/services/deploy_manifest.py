"""Deployment state manifest (plan.md sections 5.2 and 6).

A single JSON document at `state/deploy.json` is the source of truth for
"what is currently running" - image tag, commit SHA, profile, timestamps
and status. `deployment.py` is the only writer; every read here is a fresh
disk read (no in-memory cache) so a dashboard restart never shows stale
state, and writes go through a temp-file + os.replace so a crash mid-write
never leaves a half-written manifest.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

_MANIFEST_FILENAME = "deploy.json"


@dataclass
class DeploymentRecord:
    sha: str
    image_tag: str
    profile: str
    source_kind: str  # "git" | "upload"
    status: str  # "active" | "failed" | "rolled_back"
    deployed_at: str
    deployed_by: str
    error: str | None = None


@dataclass
class DeployState:
    current: DeploymentRecord | None = None
    previous: DeploymentRecord | None = None
    history: list[DeploymentRecord] = field(default_factory=list)


class DeployManifest:
    def __init__(self, state_dir: Path):
        self._path = Path(state_dir) / _MANIFEST_FILENAME
        self._lock = threading.Lock()

    def read(self) -> DeployState:
        if not self._path.exists():
            return DeployState()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return DeployState()
        current = DeploymentRecord(**raw["current"]) if raw.get("current") else None
        previous = DeploymentRecord(**raw["previous"]) if raw.get("previous") else None
        history = [DeploymentRecord(**item) for item in raw.get("history", [])]
        return DeployState(current=current, previous=previous, history=history)

    def record_success(self, record: DeploymentRecord) -> DeployState:
        with self._lock:
            state = self.read()
            state.previous = state.current
            state.current = record
            state.history.append(record)
            state.history = state.history[-50:]
            self._write(state)
            return state

    def record_failure(self, record: DeploymentRecord) -> DeployState:
        with self._lock:
            state = self.read()
            state.history.append(record)
            state.history = state.history[-50:]
            self._write(state)
            return state

    def rollback_to_previous(self, by: str) -> DeployState | None:
        with self._lock:
            state = self.read()
            if state.previous is None:
                return None
            rolled_back_record = DeploymentRecord(
                sha=state.previous.sha,
                image_tag=state.previous.image_tag,
                profile=state.previous.profile,
                source_kind=state.previous.source_kind,
                status="active",
                deployed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                deployed_by=by,
            )
            state.current = rolled_back_record
            state.previous = None
            state.history.append(rolled_back_record)
            state.history = state.history[-50:]
            self._write(state)
            return state

    def _write(self, state: DeployState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "current": asdict(state.current) if state.current else None,
            "previous": asdict(state.previous) if state.previous else None,
            "history": [asdict(item) for item in state.history],
        }
        fd, tmp_name = tempfile.mkstemp(dir=self._path.parent, prefix=".deploy-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            os.replace(tmp_name, self._path)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise
