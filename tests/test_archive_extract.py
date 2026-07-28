import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from app.services.archive_extract import ArchiveError, extract_archive


def _zip_bytes(entries: dict[str, bytes]) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    buffer.seek(0)
    return buffer


def test_valid_zip_extracts(tmp_path: Path):
    stream = _zip_bytes({"Dockerfile": b"FROM python:3.12-slim\n", "app.py": b"print(1)\n"})
    dest = tmp_path / "out"
    extract_archive("app.zip", stream, dest, max_total_bytes=1024 * 1024)
    assert (dest / "Dockerfile").read_bytes() == b"FROM python:3.12-slim\n"
    assert (dest / "app.py").exists()


def test_zip_slip_absolute_path_rejected(tmp_path: Path):
    stream = _zip_bytes({"/etc/passwd": b"pwned"})
    dest = tmp_path / "out"
    with pytest.raises(ArchiveError):
        extract_archive("app.zip", stream, dest, max_total_bytes=1024 * 1024)


def test_zip_slip_dotdot_rejected(tmp_path: Path):
    stream = _zip_bytes({"../../outside.txt": b"pwned"})
    dest = tmp_path / "out"
    with pytest.raises(ArchiveError):
        extract_archive("app.zip", stream, dest, max_total_bytes=1024 * 1024)
    assert not (tmp_path / "outside.txt").exists()


def test_zip_oversized_rejected(tmp_path: Path):
    stream = _zip_bytes({"big.txt": b"x" * 1000})
    dest = tmp_path / "out"
    with pytest.raises(ArchiveError):
        extract_archive("app.zip", stream, dest, max_total_bytes=10)


def test_unsupported_extension_rejected(tmp_path: Path):
    stream = io.BytesIO(b"not an archive")
    with pytest.raises(ArchiveError):
        extract_archive("app.rar", stream, tmp_path / "out", max_total_bytes=1024)


def test_tar_slip_rejected(tmp_path: Path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        data = b"pwned"
        info = tarfile.TarInfo(name="../outside.txt")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    buffer.seek(0)
    with pytest.raises(ArchiveError):
        extract_archive("app.tar.gz", buffer, tmp_path / "out", max_total_bytes=1024 * 1024)
    assert not (tmp_path / "outside.txt").exists()


def test_tar_symlink_rejected(tmp_path: Path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo(name="link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        archive.addfile(info)
    buffer.seek(0)
    with pytest.raises(ArchiveError):
        extract_archive("app.tar.gz", buffer, tmp_path / "out", max_total_bytes=1024 * 1024)
