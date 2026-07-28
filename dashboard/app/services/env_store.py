"""Encrypted storage, validation and redaction for `app.env` (plan.md
sections 4 and 5.2). Application environment variables are user-controlled
and may contain third-party API keys/tokens, so the file is encrypted at
rest with a key derived from APP_SECRET, validated against the reserved
name list on every write, and never returned in full through the API - only
redacted values are, so secrets never appear in the dashboard's own logs or
network responses (section 9.3).
"""

from __future__ import annotations

import base64
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from config.reserved_env import is_reserved, reserved_reason

_VAR_NAME_RE_ALLOWED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"
)
_MAX_VARS = 200
_MAX_VALUE_LENGTH = 8192


class EnvStoreError(ValueError):
    pass


@dataclass
class EnvValidationResult:
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def derive_key(app_secret: str, instance_id: str) -> bytes:
    """Deterministic Fernet key derived from the platform secret. Using
    PBKDF2 (rather than the raw secret) means the on-disk key length/format
    doesn't leak anything about APP_SECRET's own entropy, and salting with
    instance_id keeps two instances that (mis)share an APP_SECRET from
    ending up with identical encryption keys.
    """
    digest = hashlib.pbkdf2_hmac(
        "sha256", app_secret.encode("utf-8"), instance_id.encode("utf-8"), 390_000, dklen=32
    )
    return base64.urlsafe_b64encode(digest)


def validate_name(name: str) -> str | None:
    if not name:
        return "El nombre de la variable no puede estar vacio"
    if not (1 <= len(name) <= 128):
        return f"'{name}': el nombre debe tener entre 1 y 128 caracteres"
    if name[0].isdigit():
        return f"'{name}': el nombre no puede comenzar con un digito"
    if not set(name) <= _VAR_NAME_RE_ALLOWED:
        return f"'{name}': solo se permiten letras, numeros y guion bajo"
    if is_reserved(name):
        return reserved_reason(name)
    return None


def validate_vars(values: dict[str, str]) -> EnvValidationResult:
    errors: list[str] = []
    if len(values) > _MAX_VARS:
        errors.append(f"Maximo {_MAX_VARS} variables permitidas")
    for name, value in values.items():
        name_error = validate_name(name)
        if name_error:
            errors.append(name_error)
        if len(value) > _MAX_VALUE_LENGTH:
            errors.append(f"'{name}': el valor supera el maximo de {_MAX_VALUE_LENGTH} caracteres")
        if "\x00" in value or "\n" in value:
            errors.append(f"'{name}': el valor no puede contener saltos de linea ni caracteres nulos")
    return EnvValidationResult(errors=errors)


def redact(values: dict[str, str]) -> dict[str, str]:
    redacted = {}
    for name, value in values.items():
        if len(value) <= 4:
            redacted[name] = "*" * len(value)
        else:
            redacted[name] = value[:2] + "*" * (len(value) - 4) + value[-2:]
    return redacted


class EnvStore:
    def __init__(self, path: Path, fernet_key: bytes):
        self._path = Path(path)
        self._fernet = Fernet(fernet_key)

    def load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            token = self._path.read_bytes()
            plaintext = self._fernet.decrypt(token)
        except (OSError, InvalidToken) as exc:
            raise EnvStoreError(f"No se pudo leer/desencriptar app.env: {exc}") from exc

        values: dict[str, str] = {}
        for line in plaintext.decode("utf-8").splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            values[name] = value
        return values

    def save(self, values: dict[str, str]) -> None:
        result = validate_vars(values)
        if not result.ok:
            raise EnvStoreError("; ".join(result.errors))

        lines = [f"{name}={value}" for name, value in sorted(values.items())]
        plaintext = "\n".join(lines).encode("utf-8")
        token = self._fernet.encrypt(plaintext)

        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self._path.parent, prefix=".app-env-", suffix=".part")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(token)
            os.replace(tmp_name, self._path)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def load_redacted(self) -> dict[str, str]:
        return redact(self.load())
