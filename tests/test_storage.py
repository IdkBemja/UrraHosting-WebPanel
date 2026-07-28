import io
import os
from pathlib import Path

import pytest

from app.services.storage import InstanceStorage, PathTraversalError, StorageError


def _make_storage(tmp_path: Path) -> InstanceStorage:
    return InstanceStorage(
        roots={"staging": tmp_path / "staging", "source": tmp_path / "source"},
        max_upload_bytes=1024 * 1024,
        quota_bytes=None,
        writable_categories=frozenset({"staging"}),
    )


def test_path_traversal_rejected(tmp_path: Path):
    storage = _make_storage(tmp_path)
    with pytest.raises(PathTraversalError):
        storage.list_dir("staging", "../../etc")


def test_upload_and_read_round_trip(tmp_path: Path):
    storage = _make_storage(tmp_path)
    saved_path = storage.save_upload("staging", "", "hello.txt", io.BytesIO(b"hello world"), 11)
    assert saved_path == "hello.txt"
    assert storage.read_file("staging", "hello.txt") == b"hello world"


def test_upload_rejects_readonly_category(tmp_path: Path):
    storage = _make_storage(tmp_path)
    with pytest.raises(StorageError):
        storage.save_upload("source", "", "hello.txt", io.BytesIO(b"x"), 1)


def test_upload_without_overwrite_conflict(tmp_path: Path):
    storage = _make_storage(tmp_path)
    storage.save_upload("staging", "", "a.txt", io.BytesIO(b"one"), 3)
    with pytest.raises(StorageError):
        storage.save_upload("staging", "", "a.txt", io.BytesIO(b"two"), 3)
    storage.save_upload("staging", "", "a.txt", io.BytesIO(b"two"), 3, overwrite=True)
    assert storage.read_file("staging", "a.txt") == b"two"


def test_upload_over_max_bytes_rejected(tmp_path: Path):
    storage = InstanceStorage(
        roots={"staging": tmp_path / "staging"},
        max_upload_bytes=4,
        writable_categories=frozenset({"staging"}),
    )
    with pytest.raises(StorageError):
        storage.save_upload("staging", "", "big.txt", io.BytesIO(b"way too big"), 11)


def test_delete_rejects_symlink(tmp_path: Path):
    storage = _make_storage(tmp_path)
    storage.save_upload("staging", "", "real.txt", io.BytesIO(b"data"), 4)
    link = (tmp_path / "staging" / "link.txt")
    try:
        os.symlink(tmp_path / "staging" / "real.txt", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform/permissions")
    with pytest.raises(StorageError):
        storage.delete("staging", "link.txt")


def test_list_dir_hides_symlinks(tmp_path: Path):
    storage = _make_storage(tmp_path)
    storage.save_upload("staging", "", "real.txt", io.BytesIO(b"data"), 4)
    try:
        os.symlink(tmp_path / "staging" / "real.txt", tmp_path / "staging" / "link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform/permissions")
    entries = storage.list_dir("staging", "")
    names = [e.name for e in entries]
    assert "real.txt" in names
    assert "link.txt" not in names


def test_unknown_category_rejected(tmp_path: Path):
    storage = _make_storage(tmp_path)
    with pytest.raises(StorageError):
        storage.list_dir("secrets", "")
