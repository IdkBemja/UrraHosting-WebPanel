"""Small JSON-backed state for the Repository tab: configured URL/ref, deploy
key public part, and the SSH host-key trust workflow (plan.md section
6.2 - TOFU with an explicit one-time operator confirmation, never
StrictHostKeyChecking=no).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class RepositoryConfig:
    url: str = ""
    ref: str = "main"
    is_ssh: bool = False
    deploy_public_key: str = ""
    known_host_pending: str = ""
    known_host_pending_fingerprints: list[str] | None = None
    known_host_confirmed: bool = False
    last_checked_sha: str = ""
    last_checked_at: str = ""


class RepositoryState:
    def __init__(self, path: Path):
        self._path = Path(path)

    def load(self) -> RepositoryConfig:
        if not self._path.exists():
            return RepositoryConfig()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return RepositoryConfig()
        return RepositoryConfig(**{**asdict(RepositoryConfig()), **data})

    def save(self, config: RepositoryConfig) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self._path.parent, prefix=".repo-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(asdict(config), handle, indent=2)
            os.replace(tmp_name, self._path)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise
