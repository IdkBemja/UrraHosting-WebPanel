"""Source-tree policy enforcement, run against staging before it is ever
copied into `source/` or handed to the orchestrator for a build (plan.md
section 6.3). Everything here fails closed: any file type or name the
policy doesn't explicitly recognize as safe is rejected.
"""

from __future__ import annotations

import os
import re
import stat as stat_module
from pathlib import Path

REQUIRED_COMPOSE_MESSAGE = (
    "La aplicacion contiene Docker Compose. Elimine Compose y configure la "
    "aplicacion para desplegarse unicamente con Dockerfile."
)

_COMPOSE_NAME_RE = re.compile(r"^(docker-)?compose(\.[a-z0-9_-]+)?\.ya?ml$", re.IGNORECASE)
_DEFAULT_DOCKERFILE_PATH = "Dockerfile"
_GIT_HOOK_SAMPLE_SUFFIX = ".sample"


class SourceValidationError(ValueError):
    pass


class ComposeDetectedError(SourceValidationError):
    def __init__(self):
        super().__init__(REQUIRED_COMPOSE_MESSAGE)


def validate_tree(root: Path, dockerfile_relpath: str = _DEFAULT_DOCKERFILE_PATH) -> Path:
    """Walks the whole tree and raises on the first policy violation.
    Returns the validated Dockerfile path on success.
    """
    root = Path(root)
    if not root.is_dir():
        raise SourceValidationError("El directorio de origen no existe")

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        rel_dir = current.relative_to(root)

        # .git/hooks: git itself would execute any non-.sample hook script
        # during clone/checkout/merge - reject outright rather than trying
        # to tell a benign hook from a malicious one.
        if current.name == "hooks" and rel_dir.parts and ".git" in rel_dir.parts:
            for name in filenames:
                if not name.endswith(_GIT_HOOK_SAMPLE_SUFFIX):
                    raise SourceValidationError(f"Git hook no permitido: {(rel_dir / name).as_posix()}")

        for name in list(dirnames):
            entry = current / name
            _check_entry(entry, root)

        for name in filenames:
            entry = current / name
            if name == ".gitmodules":
                raise SourceValidationError("No se permiten submodulos Git (.gitmodules)")
            if _COMPOSE_NAME_RE.match(name):
                raise ComposeDetectedError()
            _check_entry(entry, root)

    dockerfile_path = root / dockerfile_relpath
    try:
        st = dockerfile_path.lstat()
    except FileNotFoundError as exc:
        raise SourceValidationError(f"No se encontro un Dockerfile en '{dockerfile_relpath}'") from exc
    if stat_module.S_ISLNK(st.st_mode):
        raise SourceValidationError("El Dockerfile no puede ser un symlink")
    if not stat_module.S_ISREG(st.st_mode):
        raise SourceValidationError("El Dockerfile debe ser un archivo regular")

    return dockerfile_path


def _check_entry(entry: Path, root: Path) -> None:
    try:
        st = entry.lstat()
    except OSError as exc:
        raise SourceValidationError(f"No se pudo inspeccionar '{entry.relative_to(root)}'") from exc

    mode = st.st_mode
    rel = entry.relative_to(root).as_posix()

    if stat_module.S_ISLNK(mode):
        raise SourceValidationError(f"No se permiten symlinks: {rel}")
    if stat_module.S_ISSOCK(mode):
        raise SourceValidationError(f"No se permiten sockets: {rel}")
    if stat_module.S_ISFIFO(mode):
        raise SourceValidationError(f"No se permiten FIFOs: {rel}")
    if stat_module.S_ISCHR(mode) or stat_module.S_ISBLK(mode):
        raise SourceValidationError(f"No se permiten dispositivos: {rel}")
    if stat_module.S_ISREG(mode) and st.st_nlink > 1:
        raise SourceValidationError(f"No se permiten hard links: {rel}")
