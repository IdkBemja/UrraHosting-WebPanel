"""Safe extraction for the "upload code" deploy path (plan.md section 3.2).
Handles zip-slip (entries that escape the destination via `..` or an
absolute path), symlink entries, device/FIFO entries and a total
uncompressed-size cap - all of which `source_validator.validate_tree` would
also catch on the *extracted* tree, but rejecting them during extraction
means a malicious archive never gets to write outside the destination in
the first place.
"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path
from typing import BinaryIO

_MAX_MEMBERS = 20_000


class ArchiveError(ValueError):
    pass


def _safe_target(dest_root: Path, member_name: str) -> Path:
    cleaned = member_name.replace("\\", "/")
    if cleaned.startswith("/") or cleaned.startswith("../") or "/../" in cleaned or cleaned == ".." or cleaned.endswith("/.."):
        raise ArchiveError(f"Entrada de archivo invalida: {member_name}")
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ArchiveError(f"Entrada de archivo invalida: {member_name}")
    target = dest_root.joinpath(*parts) if parts else dest_root
    try:
        target.relative_to(dest_root)
    except ValueError as exc:
        raise ArchiveError(f"Entrada de archivo fuera del destino: {member_name}") from exc
    return target


def extract_zip(stream: BinaryIO, dest_root: Path, max_total_bytes: int) -> None:
    dest_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(stream) as archive:
        infos = archive.infolist()
        if len(infos) > _MAX_MEMBERS:
            raise ArchiveError("El archivo contiene demasiados elementos")
        total = 0
        for info in infos:
            total += info.file_size
            if total > max_total_bytes:
                raise ArchiveError("El archivo descomprimido excede el tamano maximo permitido")
            target = _safe_target(dest_root, info.filename)
            is_symlink = (info.external_attr >> 16) & 0o170000 == 0o120000
            if is_symlink:
                raise ArchiveError(f"No se permiten symlinks en el archivo: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(target, "wb") as handle:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)


def extract_tar(stream: BinaryIO, dest_root: Path, max_total_bytes: int) -> None:
    dest_root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=stream, mode="r:*") as archive:
        members = archive.getmembers()
        if len(members) > _MAX_MEMBERS:
            raise ArchiveError("El archivo contiene demasiados elementos")
        total = 0
        for member in members:
            if member.issym() or member.islnk():
                raise ArchiveError(f"No se permiten symlinks/hardlinks en el archivo: {member.name}")
            if member.isdev():
                raise ArchiveError(f"No se permiten dispositivos en el archivo: {member.name}")
            if member.isfile():
                total += member.size
                if total > max_total_bytes:
                    raise ArchiveError("El archivo descomprimido excede el tamano maximo permitido")
            target = _safe_target(dest_root, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    continue
                with open(target, "wb") as handle:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)


def extract_archive(filename: str, stream: BinaryIO, dest_root: Path, max_total_bytes: int) -> None:
    lower = filename.lower()
    if lower.endswith(".zip"):
        extract_zip(stream, dest_root, max_total_bytes)
    elif lower.endswith(".tar.gz") or lower.endswith(".tgz") or lower.endswith(".tar"):
        extract_tar(stream, dest_root, max_total_bytes)
    else:
        raise ArchiveError("Formato no soportado; usa .zip, .tar.gz o .tar")
