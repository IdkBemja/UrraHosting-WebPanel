"""Puts the repo layout on sys.path the same way the dashboard/orchestrator
containers see it at runtime: repo root (for the top-level `config`
package) and `dashboard/` (so `app.*` resolves exactly like it does from
`/app` inside the dashboard container - see dashboard/Dockerfile).
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "dashboard"

for path in (_REPO_ROOT, _DASHBOARD_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
