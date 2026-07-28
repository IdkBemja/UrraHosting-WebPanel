"""Managed database credentials and lifecycle (plan.md section 8).

The dashboard never talks to MySQL/PostgreSQL directly and never receives
Docker access to run them - it generates random credentials, asks the
orchestrator to provision/rotate/backup/restore the container, and keeps
only an encrypted record of the current db_name/db_user/password so it can
hand the connection URL to deployment.py to inject into the app's env.
"""

from __future__ import annotations

import json
import os
import secrets
import string
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .orchestrator_client import OrchestratorClient, OrchestratorError

_ALPHABET = string.ascii_letters + string.digits


class DatabaseError(RuntimeError):
    pass


@dataclass
class DatabaseRecord:
    engine: str
    db_name: str
    db_user: str
    host: str
    port: int
    provisioned_at: str


def _random_token(length: int) -> str:
    # Restricted to letters+digits on purpose: these values are later
    # embedded into SQL statements and shell commands (orchestrator's
    # mysqldump/psql helpers) - excluding quotes/backslashes/shell
    # metacharacters removes an entire class of escaping bugs rather than
    # relying solely on downstream escaping to catch them.
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


class DatabaseService:
    def __init__(self, state_path: Path, fernet_key: bytes, orchestrator: OrchestratorClient, allowed_engines: tuple[str, ...]):
        self._path = Path(state_path)
        self._fernet = Fernet(fernet_key)
        self._orchestrator = orchestrator
        self._allowed_engines = allowed_engines

    def _load(self) -> tuple[DatabaseRecord, str] | None:
        if not self._path.exists():
            return None
        try:
            token = self._path.read_bytes()
            plaintext = self._fernet.decrypt(token)
        except (OSError, InvalidToken) as exc:
            raise DatabaseError(f"No se pudo leer el estado de la base de datos: {exc}") from exc
        data = json.loads(plaintext.decode("utf-8"))
        password = data.pop("db_password")
        return DatabaseRecord(**data), password

    def _save(self, record: DatabaseRecord, password: str) -> None:
        payload = {**asdict(record), "db_password": password}
        token = self._fernet.encrypt(json.dumps(payload).encode("utf-8"))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self._path.parent, prefix=".db-", suffix=".part")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(token)
            os.replace(tmp_name, self._path)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def status(self) -> dict:
        loaded = self._load()
        if loaded is None:
            return {"provisioned": False}
        record, _password = loaded
        try:
            remote = self._orchestrator.db_status(record.engine)
        except OrchestratorError as exc:
            remote = {"exists": False, "running": False, "status": f"error: {exc}"}
        return {
            "provisioned": True,
            "engine": record.engine,
            "db_name": record.db_name,
            "db_user": record.db_user,
            "host": record.host,
            "port": record.port,
            "provisioned_at": record.provisioned_at,
            "running": remote.get("running", False),
            "container_status": remote.get("status", "unknown"),
        }

    def provision(self, engine: str, instance_id: str) -> DatabaseRecord:
        if engine not in self._allowed_engines:
            raise DatabaseError(f"Motor de base de datos no permitido: {engine}")
        if self._load() is not None:
            raise DatabaseError("Ya existe una base de datos para esta instancia; elimina la actual primero")

        db_name = f"app_{instance_id.replace('-', '')[:16]}"
        db_user = f"user_{_random_token(12).lower()}"
        db_password = _random_token(32)

        try:
            result = self._orchestrator.db_provision(engine, db_name, db_user, db_password)
        except OrchestratorError as exc:
            raise DatabaseError(f"No se pudo aprovisionar la base de datos: {exc}") from exc
        if not result.get("ok"):
            raise DatabaseError(result.get("error", "fallo de aprovisionamiento"))

        record = DatabaseRecord(
            engine=engine,
            db_name=db_name,
            db_user=db_user,
            host=result["host"],
            port=result["port"],
            provisioned_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self._save(record, db_password)
        return record

    def deprovision(self) -> None:
        loaded = self._load()
        if loaded is None:
            return
        record, _password = loaded
        try:
            self._orchestrator.db_deprovision(record.engine)
        except OrchestratorError as exc:
            raise DatabaseError(f"No se pudo eliminar la base de datos: {exc}") from exc
        self._path.unlink(missing_ok=True)

    def rotate_credentials(self) -> None:
        loaded = self._load()
        if loaded is None:
            raise DatabaseError("No hay base de datos aprovisionada")
        record, current_password = loaded
        new_password = _random_token(32)
        try:
            result = self._orchestrator.db_rotate(record.engine, record.db_name, record.db_user, current_password, new_password)
        except OrchestratorError as exc:
            raise DatabaseError(f"No se pudo rotar la credencial: {exc}") from exc
        if not result.get("ok"):
            raise DatabaseError(result.get("error", "fallo de rotacion"))
        self._save(record, new_password)

    def backup(self) -> str:
        loaded = self._load()
        if loaded is None:
            raise DatabaseError("No hay base de datos aprovisionada")
        record, password = loaded
        filename = f"{record.engine}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.sql"
        try:
            result = self._orchestrator.db_backup(record.engine, record.db_name, record.db_user, password, filename)
        except OrchestratorError as exc:
            raise DatabaseError(f"No se pudo respaldar la base de datos: {exc}") from exc
        if not result.get("ok"):
            raise DatabaseError(result.get("error", "fallo de respaldo"))
        return filename

    def restore(self, filename: str) -> None:
        if "/" in filename or "\x00" in filename or filename in (".", ".."):
            raise DatabaseError("Nombre de archivo de respaldo invalido")
        loaded = self._load()
        if loaded is None:
            raise DatabaseError("No hay base de datos aprovisionada")
        record, password = loaded
        try:
            result = self._orchestrator.db_restore(record.engine, record.db_name, record.db_user, password, filename)
        except OrchestratorError as exc:
            raise DatabaseError(f"No se pudo restaurar la base de datos: {exc}") from exc
        if not result.get("ok"):
            raise DatabaseError(result.get("error", "fallo de restauracion"))

    def connection_env(self) -> dict[str, str]:
        loaded = self._load()
        if loaded is None:
            return {}
        record, password = loaded
        if record.engine == "mysql":
            url = f"mysql://{record.db_user}:{password}@{record.host}:{record.port}/{record.db_name}"
        else:
            url = f"postgresql://{record.db_user}:{password}@{record.host}:{record.port}/{record.db_name}"
        return {
            "DATABASE_URL": url,
            "DB_ENGINE": record.engine,
            "DB_HOST": record.host,
            "DB_PORT": str(record.port),
            "DB_NAME": record.db_name,
            "DB_USER": record.db_user,
            "DB_PASSWORD": password,
        }
