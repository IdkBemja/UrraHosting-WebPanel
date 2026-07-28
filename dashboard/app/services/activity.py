"""Lightweight audit trail: every authenticated state-changing action is
appended as a JSON line to ${DATA_DIR}/state/activity.log (plan.md section
9.3). Callers must never pass secret values (passwords, deploy keys, app.env
values, database credentials) into `detail` - only identifiers like
usernames, SHAs, filenames and error messages that have already been
truncated/redacted upstream.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class ActivityLog:
    def __init__(self, path: Path, max_entries: int = 500):
        self._path = path
        self._max_entries = max_entries

    def record(self, action: str, detail: dict[str, Any] | None = None) -> None:
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "action": action,
            "detail": detail or {},
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

        entries = []
        for line in lines[-self._max_entries :]:
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
        entries.reverse()
        return entries[:limit]
