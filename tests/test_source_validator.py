import os
from pathlib import Path

import pytest

from app.services.source_validator import (
    REQUIRED_COMPOSE_MESSAGE,
    ComposeDetectedError,
    SourceValidationError,
    validate_tree,
)


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_valid_tree_passes(tmp_path: Path):
    _write(tmp_path / "Dockerfile", "FROM python:3.12-slim\n")
    _write(tmp_path / "app.py", "print('hi')\n")
    result = validate_tree(tmp_path)
    assert result == tmp_path / "Dockerfile"


@pytest.mark.parametrize(
    "filename",
    [
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
        "docker-compose.override.yml",
        "COMPOSE.YML",
    ],
)
def test_compose_variants_rejected_with_required_message(tmp_path: Path, filename: str):
    _write(tmp_path / "Dockerfile", "FROM python:3.12-slim\n")
    _write(tmp_path / filename, "services: {}\n")
    with pytest.raises(ComposeDetectedError) as exc_info:
        validate_tree(tmp_path)
    assert str(exc_info.value) == REQUIRED_COMPOSE_MESSAGE


def test_compose_in_subdirectory_is_also_rejected(tmp_path: Path):
    _write(tmp_path / "Dockerfile", "FROM python:3.12-slim\n")
    _write(tmp_path / "nested" / "compose.yaml", "services: {}\n")
    with pytest.raises(ComposeDetectedError):
        validate_tree(tmp_path)


def test_gitmodules_rejected(tmp_path: Path):
    _write(tmp_path / "Dockerfile", "FROM python:3.12-slim\n")
    _write(tmp_path / ".gitmodules", "[submodule]\n")
    with pytest.raises(SourceValidationError):
        validate_tree(tmp_path)


def test_missing_dockerfile_rejected(tmp_path: Path):
    _write(tmp_path / "app.py", "print(1)\n")
    with pytest.raises(SourceValidationError):
        validate_tree(tmp_path)


def test_symlink_rejected(tmp_path: Path):
    _write(tmp_path / "Dockerfile", "FROM python:3.12-slim\n")
    target = tmp_path / "real.txt"
    _write(target, "hi")
    link = tmp_path / "link.txt"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform/permissions")
    with pytest.raises(SourceValidationError):
        validate_tree(tmp_path)


def test_dockerfile_symlink_rejected(tmp_path: Path):
    real = tmp_path / "real_dockerfile"
    _write(real, "FROM python:3.12-slim\n")
    link = tmp_path / "Dockerfile"
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform/permissions")
    with pytest.raises(SourceValidationError):
        validate_tree(tmp_path)


def test_git_hooks_rejected(tmp_path: Path):
    _write(tmp_path / "Dockerfile", "FROM python:3.12-slim\n")
    _write(tmp_path / ".git" / "hooks" / "post-checkout", "#!/bin/sh\nrm -rf /\n")
    with pytest.raises(SourceValidationError):
        validate_tree(tmp_path)


def test_git_hook_samples_are_allowed(tmp_path: Path):
    _write(tmp_path / "Dockerfile", "FROM python:3.12-slim\n")
    _write(tmp_path / ".git" / "hooks" / "post-checkout.sample", "#!/bin/sh\necho noop\n")
    result = validate_tree(tmp_path)
    assert result == tmp_path / "Dockerfile"
