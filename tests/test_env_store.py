from pathlib import Path

import pytest

from app.services.env_store import EnvStore, derive_key, redact, validate_vars


def test_validate_vars_rejects_reserved():
    result = validate_vars({"APP_PORT": "1234"})
    assert not result.ok
    assert "reservada" in result.errors[0]


def test_validate_vars_rejects_invalid_characters():
    result = validate_vars({"1BAD": "x"})
    assert not result.ok


def test_validate_vars_rejects_newlines_in_value():
    result = validate_vars({"OK_NAME": "line1\nline2"})
    assert not result.ok


def test_validate_vars_accepts_normal_vars():
    result = validate_vars({"STRIPE_KEY": "sk_live_abc123", "FEATURE_X": "true"})
    assert result.ok


def test_redact_masks_but_preserves_length_hint():
    redacted = redact({"SECRET": "abcdefgh", "SHORT": "ab"})
    assert redacted["SECRET"] == "ab****gh"
    assert redacted["SHORT"] == "**"
    assert "abcdefgh" not in redacted["SECRET"]


def test_env_store_round_trip_and_secrecy(tmp_path: Path):
    key = derive_key("a-fake-but-long-enough-app-secret-value-1234", "instance-1")
    path = tmp_path / "app.env.enc"
    store = EnvStore(path, key)

    store.save({"API_KEY": "supersecretvalue"})

    raw_on_disk = path.read_bytes()
    assert b"supersecretvalue" not in raw_on_disk

    loaded = store.load()
    assert loaded == {"API_KEY": "supersecretvalue"}


def test_env_store_save_rejects_reserved_vars(tmp_path: Path):
    key = derive_key("a-fake-but-long-enough-app-secret-value-1234", "instance-1")
    store = EnvStore(tmp_path / "app.env.enc", key)
    with pytest.raises(Exception):
        store.save({"DATABASE_URL": "postgres://x"})


def test_derive_key_differs_per_instance():
    key_a = derive_key("same-secret-value-thats-long-enough", "instance-a")
    key_b = derive_key("same-secret-value-thats-long-enough", "instance-b")
    assert key_a != key_b
