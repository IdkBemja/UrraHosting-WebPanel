"""Deployment orchestrator (plan.md section 3, item 5 and section 9.1).

This is the only component in the platform that ever creates, builds or
recreates containers. It receives a small, fixed set of high-level actions
from the dashboard over the `control` network - build an image, start a
throwaway health-check candidate, activate a known-good image, provision/
rotate/backup a managed database - and is the one that fills in every
security-relevant container option itself (non-root, cap_drop, no bind
mounts to the host beyond its own instance directories, no docker socket
inside app/db containers, resource limits). The caller never gets to pass
those options through - it only ever names an image tag or a database
engine.
"""

from __future__ import annotations

import hmac
import os
import secrets
import socket
import urllib.error
import urllib.request
from pathlib import Path

import docker
import docker.errors
import docker.types
from flask import Flask, jsonify, request

from config.platform_config import load_from_environ

app = Flask(__name__)

CONFIG, _RESULT = load_from_environ(os.environ)
if CONFIG is None:
    raise RuntimeError("Configuracion de orquestador invalida: " + "; ".join(_RESULT.errors))

INTERNAL_TOKEN = os.environ["ORCHESTRATOR_TOKEN"]
DOCKER_HOST = os.environ["DOCKER_HOST"]
SOURCE_DIR = Path(os.environ.get("SOURCE_DIR", "/source"))
DATA_DIR_HOST = os.environ["DATA_DIR_HOST_ABS"]
RUNTIME_NETWORK = os.environ["RUNTIME_NETWORK_NAME"]
TRAEFIK_NETWORK = os.environ.get("TRAEFIK_PUBLIC_NETWORK", "traefik-public")
IMAGE_NAMESPACE = f"urrahosting-app-{CONFIG.instance_id}"
APP_CONTAINER_NAME = f"{CONFIG.instance_id}_app"
CANDIDATE_CONTAINER_NAME = f"{CONFIG.instance_id}_app_candidate"
DB_CONTAINER_NAME = f"{CONFIG.instance_id}_db"
DB_VOLUME_NAME = f"{CONFIG.instance_id}_db_data"

_DB_ENGINE_DEFAULTS = {
    "mysql": {
        "image": CONFIG.mysql_image,
        "port": 3306,
        "data_path": "/var/lib/mysql",
        "env": lambda db_name, db_user, db_password: {
            "MYSQL_DATABASE": db_name,
            "MYSQL_USER": db_user,
            "MYSQL_PASSWORD": db_password,
            "MYSQL_RANDOM_ROOT_PASSWORD": "yes",
        },
    },
    "postgres": {
        "image": CONFIG.postgres_image,
        "port": 5432,
        "data_path": "/var/lib/postgresql/data",
        "env": lambda db_name, db_user, db_password: {
            "POSTGRES_DB": db_name,
            "POSTGRES_USER": db_user,
            "POSTGRES_PASSWORD": db_password,
        },
    },
}


def _client() -> docker.DockerClient:
    return docker.DockerClient(base_url=DOCKER_HOST, timeout=180)


def _port_env() -> dict:
    # The platform's listen/port contract (config/reserved_env.py) reserves
    # both APP_PORT and PORT so the user can never set either from app.env -
    # inject both here, pointing at the same value, so apps written against
    # either convention (APP_PORT is this platform's own docs/examples;
    # PORT is the far more common one from Heroku-style buildpacks/
    # frameworks) bind to the port the health check and Traefik's
    # loadbalancer.server.port actually probe/route to.
    port = str(CONFIG.app_port)
    return {"APP_PORT": port, "PORT": port}


@app.before_request
def _check_auth():
    # /healthz is exempt: it's polled by Docker's own healthcheck (compose.yml,
    # no way to attach a header to a `CMD` probe) and only ever returns a
    # static {"ok": true} with no instance state, so there's nothing to protect.
    if request.path == "/healthz":
        return None
    provided = request.headers.get("X-Internal-Token", "")
    if not hmac.compare_digest(provided, INTERNAL_TOKEN):
        return jsonify({"error": "No autorizado"}), 401


def _fixed_security_kwargs(memory_limit: str, cpu_limit: str) -> dict:
    return {
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "read_only": True,
        "tmpfs": {"/tmp": "size=64m,mode=1777,nosuid"},
        "mem_limit": memory_limit,
        "memswap_limit": memory_limit,
        "nano_cpus": int(float(cpu_limit) * 1_000_000_000),
        "pids_limit": 256,
        "ulimits": [docker.types.Ulimit(name="nofile", soft=1024, hard=2048)],
        "log_config": docker.types.LogConfig(type="json-file", config={"max-size": "10m", "max-file": "3"}),
        "network_disabled": False,
        "privileged": False,
        "cap_add": [],
        "devices": [],
        "extra_hosts": {},
        "sysctls": {},
        "ipc_mode": None,
        "pid_mode": None,
    }


def _db_security_kwargs(memory_limit: str, cpu_limit: str) -> dict:
    # The official mysql/postgres images start their entrypoint as root and
    # self-drop privileges (gosu) to fix ownership on the data volume on
    # first init - cap_drop: ALL (like the app container gets) removes
    # CAP_SETUID/CAP_SETGID/CAP_CHOWN/CAP_DAC_OVERRIDE and makes that
    # entrypoint fail outright ("setgid: Operation not permitted"). Add
    # back only that minimal set; everything else (no ports, no host
    # mounts, resource limits, no-new-privileges, network isolation) stays
    # identical to the app container's policy.
    kwargs = _fixed_security_kwargs(memory_limit, cpu_limit)
    kwargs["cap_add"] = ["SETUID", "SETGID", "CHOWN", "DAC_OVERRIDE", "FOWNER"]
    kwargs["read_only"] = False
    return kwargs


def _ensure_image(client: docker.DockerClient, image: str) -> None:
    # containers.create() (unlike the higher-level containers.run()) never
    # auto-pulls a missing image - needed for the platform-pinned
    # mysql/postgres images, which (unlike app images) are never built
    # locally first.
    try:
        client.images.get(image)
    except docker.errors.ImageNotFound:
        client.images.pull(image)


def _container_logs(client: docker.DockerClient, name: str, tail: int = 200) -> list[str]:
    # A candidate that fails health checks is discarded (removed) right
    # after - without capturing its logs first, the ONLY evidence of why it
    # never became healthy is gone forever, and the operator is left
    # guessing from indirect symptoms (e.g. DNS resolution failing once the
    # dead container's network endpoint is torn down) instead of seeing the
    # actual crash (e.g. "gunicorn: not found").
    try:
        container = client.containers.get(name)
    except docker.errors.NotFound:
        return []
    try:
        raw = container.logs(tail=tail, stdout=True, stderr=True)
    except docker.errors.DockerException:
        return []
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    return [line for line in text.splitlines() if line.strip()]


def _remove_container_if_exists(client: docker.DockerClient, name: str) -> None:
    try:
        container = client.containers.get(name)
    except docker.errors.NotFound:
        return
    try:
        container.remove(force=True)
    except docker.errors.NotFound:
        pass


def _connect_networks(client: docker.DockerClient, container, network_names: list[str]) -> None:
    for name in network_names:
        try:
            network = client.networks.get(name)
        except docker.errors.NotFound as exc:
            raise RuntimeError(f"Red '{name}' no existe") from exc
        network.connect(container)


@app.post("/build")
def build_image():
    payload = request.get_json(force=True, silent=True) or {}
    sha = str(payload.get("sha", ""))[:64]
    dockerfile_path = str(payload.get("dockerfile_path", "Dockerfile"))
    if not sha or any(ch not in "0123456789abcdefABCDEF-upload_" for ch in sha):
        return jsonify({"error": "sha invalido"}), 400
    if not SOURCE_DIR.is_dir():
        return jsonify({"error": "No hay codigo fuente para construir"}), 400

    image_tag = f"{IMAGE_NAMESPACE}:{sha}"
    client = _client()
    try:
        _image, build_logs = client.images.build(
            path=str(SOURCE_DIR),
            dockerfile=dockerfile_path,
            tag=image_tag,
            rm=True,
            forcerm=True,
            pull=True,
            nocache=False,
            network_mode="bridge",
            timeout=1800,
        )
        log_lines = [entry.get("stream", "").rstrip() for entry in build_logs if entry.get("stream")]
        return jsonify({"ok": True, "image_tag": image_tag, "log_tail": log_lines[-100:]})
    except docker.errors.BuildError as exc:
        log_lines = [entry.get("stream", "").rstrip() for entry in exc.build_log if isinstance(entry, dict) and entry.get("stream")]
        return jsonify({"ok": False, "error": str(exc), "log_tail": log_lines[-100:]}), 400
    except (docker.errors.DockerException, OSError) as exc:
        return jsonify({"ok": False, "error": f"Fallo de build: {exc}"}), 502
    finally:
        client.close()


@app.post("/candidate/start")
def start_candidate():
    payload = request.get_json(force=True, silent=True) or {}
    image_tag = str(payload.get("image_tag", ""))
    env = payload.get("env") or {}
    if not image_tag.startswith(f"{IMAGE_NAMESPACE}:"):
        return jsonify({"error": "image_tag invalido"}), 400
    if not isinstance(env, dict):
        return jsonify({"error": "env invalido"}), 400

    client = _client()
    try:
        _remove_container_if_exists(client, CANDIDATE_CONTAINER_NAME)
        container = client.containers.create(
            image=image_tag,
            name=CANDIDATE_CONTAINER_NAME,
            environment={**env, **_port_env()},
            network=RUNTIME_NETWORK,
            labels={"com.urrahosting.instance": CONFIG.instance_id, "com.urrahosting.role": "app-candidate"},
            **_fixed_security_kwargs(CONFIG.app_memory_limit, CONFIG.app_cpu_limit),
        )
        container.start()
        return jsonify({"ok": True, "candidate_name": CANDIDATE_CONTAINER_NAME})
    except (docker.errors.DockerException, OSError) as exc:
        return jsonify({"ok": False, "error": f"No se pudo iniciar el candidato: {exc}"}), 502
    finally:
        client.close()


def _probe_health(container_name: str, path: str, timeout: float = 3.0) -> tuple[bool, str]:
    """Returns (healthy, detail) - detail is a short, human-readable reason
    (HTTP status or connection failure) surfaced to the dashboard so the
    health-check-wait step can log something more useful than pass/fail
    per attempt (e.g. "HTTP 500" vs "conexion rechazada" point at very
    different problems: the app is up but erroring, vs not listening yet)."""
    url = f"http://{container_name}:{CONFIG.app_port}{path}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            healthy = 200 <= response.status < 400
            return healthy, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except TimeoutError:
        return False, "tiempo de espera agotado"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return False, f"sin conexion ({reason})"


# The dashboard owns the retry loop (dashboard/app/services/deployment.py)
# instead of this endpoint blocking on it - a single probe per call lets
# the dashboard log/report each attempt (progress SSE stream) instead of
# only learning the final pass/fail after the whole wait.
@app.post("/candidate/probe-health")
def probe_candidate_health():
    payload = request.get_json(force=True, silent=True) or {}
    candidate_name = str(payload.get("candidate_name", ""))
    health_path = str(payload.get("health_path", "/")) or "/"
    if candidate_name != CANDIDATE_CONTAINER_NAME:
        return jsonify({"error": "candidate_name invalido"}), 400

    healthy, detail = _probe_health(candidate_name, health_path)
    return jsonify({"ok": True, "healthy": healthy, "detail": detail})


@app.post("/candidate/discard")
def discard_candidate():
    payload = request.get_json(force=True, silent=True) or {}
    candidate_name = str(payload.get("candidate_name", ""))
    if candidate_name != CANDIDATE_CONTAINER_NAME:
        return jsonify({"error": "candidate_name invalido"}), 400
    client = _client()
    try:
        log_tail = _container_logs(client, candidate_name)
        _remove_container_if_exists(client, CANDIDATE_CONTAINER_NAME)
        return jsonify({"ok": True, "log_tail": log_tail})
    finally:
        client.close()


@app.post("/activate")
def activate():
    payload = request.get_json(force=True, silent=True) or {}
    image_tag = str(payload.get("image_tag", ""))
    env = payload.get("env") or {}
    if not image_tag.startswith(f"{IMAGE_NAMESPACE}:"):
        return jsonify({"error": "image_tag invalido"}), 400

    labels = {
        "com.urrahosting.instance": CONFIG.instance_id,
        "com.urrahosting.role": "app",
        "traefik.enable": "true",
        "traefik.docker.network": TRAEFIK_NETWORK,
        f"traefik.http.routers.app-{CONFIG.instance_id}.rule": f"Host(`{CONFIG.instance_id}.{CONFIG.public_base_domain}`)",
        f"traefik.http.routers.app-{CONFIG.instance_id}.entrypoints": CONFIG.traefik_web_entrypoint,
        f"traefik.http.routers.app-{CONFIG.instance_id}.tls": "true",
        # Without an explicit certresolver, tls=true only makes Traefik
        # terminate TLS with its own default (self-signed) certificate - it
        # never requests a real one from Let's Encrypt on its own.
        f"traefik.http.routers.app-{CONFIG.instance_id}.tls.certresolver": CONFIG.traefik_cert_resolver,
        f"traefik.http.routers.app-{CONFIG.instance_id}.service": f"app-{CONFIG.instance_id}",
        f"traefik.http.services.app-{CONFIG.instance_id}.loadbalancer.server.port": str(CONFIG.app_port),
    }

    client = _client()
    try:
        _remove_container_if_exists(client, APP_CONTAINER_NAME)
        container = client.containers.create(
            image=image_tag,
            name=APP_CONTAINER_NAME,
            environment={**env, **_port_env()},
            network=RUNTIME_NETWORK,
            labels=labels,
            restart_policy={"Name": "unless-stopped"},
            **_fixed_security_kwargs(CONFIG.app_memory_limit, CONFIG.app_cpu_limit),
        )
        _connect_networks(client, container, [TRAEFIK_NETWORK])
        container.start()
        _remove_container_if_exists(client, CANDIDATE_CONTAINER_NAME)
        return jsonify({"ok": True, "container_name": APP_CONTAINER_NAME})
    except (docker.errors.DockerException, OSError, RuntimeError) as exc:
        return jsonify({"ok": False, "error": f"No se pudo activar el despliegue: {exc}"}), 502
    finally:
        client.close()


# --- Managed database -------------------------------------------------


def _engine_or_400(engine: str):
    spec = _DB_ENGINE_DEFAULTS.get(engine)
    if spec is None or engine not in CONFIG.allowed_db_engines:
        return None
    return spec


@app.post("/db/provision")
def db_provision():
    payload = request.get_json(force=True, silent=True) or {}
    engine = str(payload.get("engine", ""))
    db_name = str(payload.get("db_name", ""))
    db_user = str(payload.get("db_user", ""))
    db_password = str(payload.get("db_password", ""))
    spec = _engine_or_400(engine)
    if spec is None or not db_name or not db_user or not db_password:
        return jsonify({"error": "Parametros de base de datos invalidos"}), 400

    client = _client()
    try:
        _remove_container_if_exists(client, DB_CONTAINER_NAME)
        _ensure_image(client, spec["image"])
        container = client.containers.create(
            image=spec["image"],
            name=DB_CONTAINER_NAME,
            environment=spec["env"](db_name, db_user, db_password),
            network=RUNTIME_NETWORK,
            volumes={DB_VOLUME_NAME: {"bind": spec["data_path"], "mode": "rw"}},
            labels={"com.urrahosting.instance": CONFIG.instance_id, "com.urrahosting.role": "database"},
            restart_policy={"Name": "unless-stopped"},
            **_db_security_kwargs("1G", "1.0"),
        )
        container.start()
        return jsonify({"ok": True, "container_name": DB_CONTAINER_NAME, "host": DB_CONTAINER_NAME, "port": spec["port"]})
    except (docker.errors.DockerException, OSError) as exc:
        return jsonify({"ok": False, "error": f"No se pudo aprovisionar la base de datos: {exc}"}), 502
    finally:
        client.close()


@app.post("/db/deprovision")
def db_deprovision():
    payload = request.get_json(force=True, silent=True) or {}
    engine = str(payload.get("engine", ""))
    if _engine_or_400(engine) is None:
        return jsonify({"error": "engine invalido"}), 400
    client = _client()
    try:
        _remove_container_if_exists(client, DB_CONTAINER_NAME)
        try:
            client.volumes.get(DB_VOLUME_NAME).remove(force=True)
        except docker.errors.NotFound:
            pass
        return jsonify({"ok": True})
    finally:
        client.close()


@app.post("/db/status")
def db_status():
    payload = request.get_json(force=True, silent=True) or {}
    engine = str(payload.get("engine", ""))
    if _engine_or_400(engine) is None:
        return jsonify({"error": "engine invalido"}), 400
    client = _client()
    try:
        try:
            container = client.containers.get(DB_CONTAINER_NAME)
            container.reload()
            state = container.attrs.get("State", {})
            return jsonify({"exists": True, "running": bool(state.get("Running")), "status": state.get("Status")})
        except docker.errors.NotFound:
            return jsonify({"exists": False, "running": False, "status": "absent"})
    finally:
        client.close()


def _run_db_client(client: docker.DockerClient, engine: str, image: str, command: list[str], env: dict, extra_binds: dict) -> tuple[bool, str]:
    try:
        output = client.containers.run(
            image=image,
            command=command,
            environment=env,
            network=RUNTIME_NETWORK,
            volumes=extra_binds,
            remove=True,
            stdout=True,
            stderr=True,
            labels={"com.urrahosting.instance": CONFIG.instance_id, "com.urrahosting.role": "db-helper"},
            **_fixed_security_kwargs("512M", "0.50"),
        )
        return True, output.decode("utf-8", errors="replace")
    except docker.errors.ContainerError as exc:
        return False, (exc.stderr or b"").decode("utf-8", errors="replace")[:2000]
    except (docker.errors.DockerException, OSError) as exc:
        return False, str(exc)


@app.post("/db/rotate")
def db_rotate():
    payload = request.get_json(force=True, silent=True) or {}
    engine = str(payload.get("engine", ""))
    db_name = str(payload.get("db_name", ""))
    db_user = str(payload.get("db_user", ""))
    new_password = str(payload.get("new_password", ""))
    spec = _engine_or_400(engine)
    if spec is None or not db_name or not db_user or not new_password:
        return jsonify({"error": "Parametros invalidos"}), 400

    current_password = str(payload.get("current_password", ""))
    client = _client()
    try:
        # Authenticates as the app's own database user with its current
        # password (rather than an admin/root credential the platform
        # doesn't retain - MYSQL_RANDOM_ROOT_PASSWORD=yes at provision
        # time) and changes only that user's own password.
        # Passwords are always platform-generated from a restricted
        # alphanumeric charset (see services/database.py), so this escaping
        # is defense in depth, not the only thing standing between user
        # input and these statements.
        escaped = new_password.replace("\\", "\\\\").replace("'", "\\'")
        if engine == "mysql":
            ok, output = _run_db_client(
                client, engine, spec["image"],
                # MySQL 8 removed PASSWORD(); SET PASSWORD = '<plain>' hashes
                # it server-side using the account's own auth plugin.
                ["mysql", "-h", DB_CONTAINER_NAME, "-u", db_user, "-e", f"SET PASSWORD = '{escaped}';"],
                {"MYSQL_PWD": current_password}, {},
            )
        else:
            escaped_pg = new_password.replace("'", "''")
            ok, output = _run_db_client(
                client, engine, spec["image"],
                ["psql", "-h", DB_CONTAINER_NAME, "-U", db_user, "-c", f"ALTER USER CURRENT_USER WITH PASSWORD '{escaped_pg}';"],
                {"PGPASSWORD": current_password}, {},
            )
        if not ok:
            return jsonify({"ok": False, "error": "No se pudo rotar la credencial"}), 502
        return jsonify({"ok": True})
    finally:
        client.close()


@app.post("/db/backup")
def db_backup():
    payload = request.get_json(force=True, silent=True) or {}
    engine = str(payload.get("engine", ""))
    db_name = str(payload.get("db_name", ""))
    db_user = str(payload.get("db_user", ""))
    db_password = str(payload.get("db_password", ""))
    backup_filename = str(payload.get("backup_filename", ""))
    spec = _engine_or_400(engine)
    if spec is None or not db_name or not backup_filename or "/" in backup_filename or "\x00" in backup_filename:
        return jsonify({"error": "Parametros invalidos"}), 400

    host_backup_dir = f"{DATA_DIR_HOST}/backups"
    client = _client()
    try:
        if engine == "mysql":
            command = ["sh", "-c", f"mysqldump -h {DB_CONTAINER_NAME} -u {db_user} {db_name} > /backups/{backup_filename}"]
            env = {"MYSQL_PWD": db_password}
        else:
            command = ["sh", "-c", f"pg_dump -h {DB_CONTAINER_NAME} -U {db_user} {db_name} > /backups/{backup_filename}"]
            env = {"PGPASSWORD": db_password}
        ok, output = _run_db_client(
            client, engine, spec["image"], command, env, {host_backup_dir: {"bind": "/backups", "mode": "rw"}}
        )
        if not ok:
            return jsonify({"ok": False, "error": "El respaldo fallo"}), 502
        return jsonify({"ok": True, "filename": backup_filename})
    finally:
        client.close()


@app.post("/db/restore")
def db_restore():
    payload = request.get_json(force=True, silent=True) or {}
    engine = str(payload.get("engine", ""))
    db_name = str(payload.get("db_name", ""))
    db_user = str(payload.get("db_user", ""))
    db_password = str(payload.get("db_password", ""))
    backup_filename = str(payload.get("backup_filename", ""))
    spec = _engine_or_400(engine)
    if spec is None or not db_name or not backup_filename or "/" in backup_filename or "\x00" in backup_filename:
        return jsonify({"error": "Parametros invalidos"}), 400

    host_backup_dir = f"{DATA_DIR_HOST}/backups"
    client = _client()
    try:
        if engine == "mysql":
            command = ["sh", "-c", f"mysql -h {DB_CONTAINER_NAME} -u {db_user} {db_name} < /backups/{backup_filename}"]
            env = {"MYSQL_PWD": db_password}
        else:
            command = ["sh", "-c", f"psql -h {DB_CONTAINER_NAME} -U {db_user} {db_name} < /backups/{backup_filename}"]
            env = {"PGPASSWORD": db_password}
        ok, output = _run_db_client(
            client, engine, spec["image"], command, env, {host_backup_dir: {"bind": "/backups", "mode": "ro"}}
        )
        if not ok:
            return jsonify({"ok": False, "error": "La restauracion fallo"}), 502
        return jsonify({"ok": True})
    finally:
        client.close()


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})
