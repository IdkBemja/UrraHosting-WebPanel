"""Path-safe file management scoped to `staging/` (user-writable, pending
deploy) and `source/` (read-only mirror of the active deployment) - plan.md
section 5.1. Secrets, manifests, DB data and platform config live under
other roots the file browser never mounts at all, so there is no path that
could accidentally expose them even with a validator bug.

Every operation walks the path one segment at a time with `os.open(...,
O_NOFOLLOW, dir_fd=parent_fd)` (openat-style), instead of resolving the
whole path once with `Path.resolve()` and checking the result. A resolve
based check is a classic TOCTOU: an attacker who can create a symlink
inside the tree (e.g. via a previous upload) can swap a real directory for
a symlink between the check and the actual open/write. Walking with
O_NOFOLLOW + dir_fd makes every single step in the path fail closed the
instant it hits a symlink, so there is no window to race.
"""

from __future__ import annotations

import os
import stat as stat_module
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Mapping

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class StorageError(ValueError):
    pass


class PathTraversalError(StorageError):
    pass


@dataclass
class FileEntry:
    name: str
    path: str
    is_dir: bool
    size: int
    modified_at: float


def sanitize_filename(filename: str) -> str:
    name = os.path.basename(filename.replace("\\", "/")).strip()
    if not name or name in (".", ".."):
        raise StorageError("Nombre de archivo invalido")
    if "\x00" in name:
        raise StorageError("Nombre de archivo invalido")
    return name


def _split_clean(relative_path: str) -> list[str]:
    cleaned = (relative_path or "").replace("\\", "/").strip("/")
    if not cleaned:
        return []
    parts = []
    for part in cleaned.split("/"):
        if part in ("", ".", ".."):
            if part == "..":
                raise PathTraversalError("Ruta invalida: no se permite '..'")
            continue
        parts.append(part)
    return parts


class _OpenDir:
    """Context manager wrapping a directory file descriptor opened with
    O_NOFOLLOW, closed automatically on exit."""

    def __init__(self, fd: int):
        self.fd = fd

    def __exit__(self, *exc):
        os.close(self.fd)
        return False

    def __enter__(self):
        return self


def _open_root_dir(root: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW
    return os.open(root, flags)


def _open_child_dir(parent_fd: int, name: str) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW
    return os.open(name, flags, dir_fd=parent_fd)


class InstanceStorage:
    """`roots` maps category name -> filesystem root. Only categories
    listed here are ever reachable; there is no generic "give me any path"
    escape hatch anywhere in this class.
    """

    def __init__(self, roots: Mapping[str, Path], max_upload_bytes: int, quota_bytes: int | None = None,
                 writable_categories: frozenset[str] = frozenset()):
        self._roots = {name: Path(root) for name, root in roots.items()}
        for root in self._roots.values():
            root.mkdir(parents=True, exist_ok=True)
        self.max_upload_bytes = max_upload_bytes
        self.quota_bytes = quota_bytes
        self._writable = writable_categories

    @property
    def categories(self) -> list[str]:
        return list(self._roots)

    def is_writable(self, category: str) -> bool:
        return category in self._writable

    def _root(self, category: str) -> Path:
        root = self._roots.get(category)
        if root is None:
            raise StorageError(f"Categoria de almacenamiento desconocida: {category}")
        return root

    def _require_writable(self, category: str) -> None:
        if category not in self._writable:
            raise StorageError(f"La categoria '{category}' es de solo lectura")

    def _walk_to_parent(self, category: str, relative_path: str) -> tuple[int, list[str]]:
        """Opens (openat-style, O_NOFOLLOW at every step) every directory
        component up to but not including the final segment. Returns the
        parent directory fd (caller must close it) and the remaining
        [leaf_name] (empty list if relative_path points at the root
        itself).
        """
        root = self._root(category)
        parts = _split_clean(relative_path)
        fd = _open_root_dir(root)
        try:
            for part in parts[:-1]:
                next_fd = _open_child_dir(fd, part)
                os.close(fd)
                fd = next_fd
        except (OSError, FileNotFoundError) as exc:
            os.close(fd)
            raise PathTraversalError(f"Ruta invalida en '{category}': {relative_path}") from exc
        return fd, parts[-1:]

    def resolve(self, category: str, relative_path: str) -> Path:
        """Best-effort human-readable Path for display/send_file purposes
        only. Any operation that reads/writes/deletes MUST use the dir_fd
        based helpers below instead of trusting this path in isolation.
        """
        root = self._root(category)
        parts = _split_clean(relative_path)
        return root.joinpath(*parts) if parts else root

    def list_dir(self, category: str, relative_path: str = "") -> list[FileEntry]:
        root = self._root(category)
        parts = _split_clean(relative_path)
        fd = _open_root_dir(root)
        try:
            for part in parts:
                next_fd = _open_child_dir(fd, part)
                os.close(fd)
                fd = next_fd

            entries: list[FileEntry] = []
            with os.scandir(fd) as scanner:
                for child in scanner:
                    if child.is_symlink():
                        continue
                    try:
                        st = child.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if not stat_module.S_ISREG(st.st_mode) and not stat_module.S_ISDIR(st.st_mode):
                        continue
                    entries.append(
                        FileEntry(
                            name=child.name,
                            path="/".join([*parts, child.name]),
                            is_dir=child.is_dir(follow_symlinks=False),
                            size=st.st_size if child.is_file(follow_symlinks=False) else 0,
                            modified_at=st.st_mtime,
                        )
                    )
            entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
            return entries
        except (FileNotFoundError,):
            return []
        except NotADirectoryError as exc:
            raise StorageError(f"'{relative_path}' no es un directorio") from exc
        except OSError as exc:
            raise PathTraversalError(f"Ruta invalida en '{category}': {relative_path}") from exc
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def usage_bytes(self, category: str | None = None) -> int:
        categories = [category] if category else self.categories
        total = 0
        for cat in categories:
            root = self._root(cat)
            for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
                for name in filenames:
                    file_path = Path(dirpath) / name
                    if file_path.is_symlink():
                        continue
                    try:
                        total += file_path.stat().st_size
                    except OSError:
                        continue
        return total

    def check_quota(self, additional_bytes: int) -> None:
        if self.quota_bytes is None:
            return
        if self.usage_bytes() + additional_bytes > self.quota_bytes:
            raise StorageError("Cuota de almacenamiento de la instancia excedida")

    def save_upload(
        self,
        category: str,
        relative_dir: str,
        filename: str,
        stream: BinaryIO,
        content_length: int | None,
        *,
        overwrite: bool = False,
    ) -> str:
        self._require_writable(category)
        if content_length is not None and content_length > self.max_upload_bytes:
            raise StorageError("El archivo excede el tamano maximo permitido (MAX_UPLOAD_MB)")

        safe_name = sanitize_filename(filename)
        if content_length is not None:
            self.check_quota(content_length)

        root = self._root(category)
        parts = _split_clean(relative_dir)
        fd = _open_root_dir(root)
        try:
            for part in parts:
                try:
                    next_fd = _open_child_dir(fd, part)
                except FileNotFoundError:
                    os.mkdir(part, dir_fd=fd)
                    next_fd = _open_child_dir(fd, part)
                os.close(fd)
                fd = next_fd

            tmp_name = f".{safe_name}.part"
            _unlink_if_exists(fd, tmp_name)
            open_flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _NOFOLLOW
            tmp_fd = os.open(tmp_name, open_flags, 0o600, dir_fd=fd)
            written = 0
            try:
                with os.fdopen(tmp_fd, "wb") as handle:
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > self.max_upload_bytes:
                            raise StorageError("El archivo excede el tamano maximo permitido durante la subida")
                        handle.write(chunk)
            except Exception:
                _unlink_if_exists(fd, tmp_name)
                raise

            if content_length is None:
                self.check_quota(written)

            target_exists = _exists_no_follow(fd, safe_name)
            if target_exists and not overwrite:
                _unlink_if_exists(fd, tmp_name)
                raise StorageError(f"'{safe_name}' ya existe (confirma sobrescritura)")
            if target_exists and _is_symlink(fd, safe_name):
                _unlink_if_exists(fd, tmp_name)
                raise StorageError("No se permite escribir sobre un symlink")

            os.rename(tmp_name, safe_name, src_dir_fd=fd, dst_dir_fd=fd)
            return "/".join([*parts, safe_name])
        finally:
            os.close(fd)

    def delete(self, category: str, relative_path: str) -> None:
        self._require_writable(category)
        fd, leaf = self._walk_to_parent(category, relative_path)
        try:
            if not leaf:
                raise StorageError("No se puede eliminar la raiz de la categoria")
            name = leaf[0]
            if not _exists_no_follow(fd, name):
                raise StorageError("El elemento no existe")
            if _is_symlink(fd, name):
                raise StorageError("No se permite operar sobre symlinks")
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat_module.S_ISDIR(st.st_mode):
                child_fd = _open_child_dir(fd, name)
                try:
                    _rmtree_fd(child_fd)
                finally:
                    os.close(child_fd)
                os.rmdir(name, dir_fd=fd)
            else:
                os.unlink(name, dir_fd=fd)
        finally:
            os.close(fd)

    def read_file(self, category: str, relative_path: str, max_bytes: int = 5 * 1024 * 1024) -> bytes:
        fd, leaf = self._walk_to_parent(category, relative_path)
        try:
            if not leaf:
                raise StorageError("Ruta invalida")
            name = leaf[0]
            flags = os.O_RDONLY | _NOFOLLOW
            try:
                file_fd = os.open(name, flags, dir_fd=fd)
            except (OSError,) as exc:
                raise StorageError("Archivo no encontrado") from exc
            try:
                st = os.fstat(file_fd)
                if not stat_module.S_ISREG(st.st_mode):
                    raise StorageError("No es un archivo regular")
                if st.st_size > max_bytes:
                    raise StorageError("El archivo es demasiado grande para leerlo en linea")
                return os.read(file_fd, st.st_size)
            finally:
                os.close(file_fd)
        finally:
            os.close(fd)


def _exists_no_follow(dir_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _is_symlink(dir_fd: int, name: str) -> bool:
    st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    return stat_module.S_ISLNK(st.st_mode)


def _unlink_if_exists(dir_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=dir_fd)
    except FileNotFoundError:
        pass


def _rmtree_fd(dir_fd: int) -> None:
    with os.scandir(dir_fd) as scanner:
        children = list(scanner)
    for child in children:
        st = child.stat(follow_symlinks=False)
        if stat_module.S_ISLNK(st.st_mode):
            os.unlink(child.name, dir_fd=dir_fd)
        elif stat_module.S_ISDIR(st.st_mode):
            child_fd = _open_child_dir(dir_fd, child.name)
            try:
                _rmtree_fd(child_fd)
            finally:
                os.close(child_fd)
            os.rmdir(child.name, dir_fd=dir_fd)
        else:
            os.unlink(child.name, dir_fd=dir_fd)
