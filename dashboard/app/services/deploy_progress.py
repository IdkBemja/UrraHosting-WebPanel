"""In-process progress tracker for the currently running deploy (git-update
or upload), consumed by the SSE stream at /api/deployment/progress/stream
so the "Actualizar y desplegar" button can show a real stage + percent
instead of a static "esto puede tardar varios minutos" string.

Deliberately in-memory, not persisted: the dashboard runs a single gunicorn
worker (dashboard/Dockerfile: --workers 1, multiple threads), so a
module-level object guarded by a lock is enough to hand state from the
thread running the blocking deploy request to the thread serving the
concurrently-open EventSource - no cross-process sync needed, and nothing
here needs to survive a restart.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace

# (stage_id, label, weight) - weight is the % of the bar that stage
# contributes once it *starts* (i.e. the bar shows the cumulative weight of
# every stage completed so far). Not measured, just a reasonable split:
# the image build is by far the longest step in a real deploy.
STAGES: list[tuple[str, str, int]] = [
    ("validate", "Validando codigo y Dockerfile", 5),
    ("build", "Construyendo la imagen", 55),
    ("candidate_start", "Iniciando contenedor candidato", 10),
    ("health_check", "Esperando el chequeo de salud", 20),
    ("activate", "Activando la nueva version", 10),
]


def _cumulative_percent_before(stage_id: str) -> int:
    total = 0
    for sid, _label, weight in STAGES:
        if sid == stage_id:
            return total
        total += weight
    return total


def _label_for(stage_id: str) -> str:
    for sid, label, _weight in STAGES:
        if sid == stage_id:
            return label
    return stage_id


_MAX_LOG_LINES = 200


@dataclass
class DeployProgress:
    active: bool = False
    stage: str = ""
    label: str = ""
    percent: int = 0
    status: str = "idle"  # idle | running | success | failed
    message: str = ""
    # Detail lines for the current stage only (e.g. one per health-check
    # attempt) - reset to [] whenever the stage changes, since it's meant to
    # explain what a single stage is doing right now, not be a full deploy
    # transcript.
    logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "active": self.active,
            "stage": self.stage,
            "label": self.label,
            "percent": self.percent,
            "status": self.status,
            "message": self.message,
            "logs": self.logs,
        }


class DeployProgressTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = DeployProgress()
        self._version = 0

    def is_active(self) -> bool:
        with self._lock:
            return self._state.active

    def start(self) -> None:
        with self._lock:
            self._state = DeployProgress(active=True, stage="", label="Preparando...", percent=0, status="running")
            self._version += 1

    def set_stage(self, stage_id: str) -> None:
        with self._lock:
            self._state = DeployProgress(
                active=True,
                stage=stage_id,
                label=_label_for(stage_id),
                percent=_cumulative_percent_before(stage_id),
                status="running",
            )
            self._version += 1

    def append_log(self, line: str) -> None:
        with self._lock:
            logs = (self._state.logs + [line])[-_MAX_LOG_LINES:]
            self._state = replace(self._state, logs=logs)
            self._version += 1

    def finish_success(self) -> None:
        with self._lock:
            self._state = DeployProgress(active=False, stage="done", label="Completado", percent=100, status="success")
            self._version += 1

    def finish_failure(self, message: str = "") -> None:
        with self._lock:
            self._state = DeployProgress(
                active=False, stage="failed", label="Fallido", percent=self._state.percent,
                status="failed", message=message[:500],
            )
            self._version += 1

    def snapshot(self) -> tuple[DeployProgress, int]:
        with self._lock:
            return self._state, self._version
