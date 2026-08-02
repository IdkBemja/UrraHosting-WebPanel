"""Deploy orchestration on the dashboard side (plan.md section 6.4).

Ties together source validation, Dockerfile policy, the encrypted app.env
store and the orchestrator client into the single atomic-deploy flow:
validate -> build (tagged by SHA) -> start an unrouted health-check
candidate -> activate only if healthy, otherwise leave the previously
active container untouched and report the failure.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from config.runtime_profiles import RuntimeProfile, get as get_profile

from . import source_validator
from .deploy_manifest import DeployManifest, DeploymentRecord
from .deploy_progress import DeployProgressTracker
from .dockerfile_validator import validate as validate_dockerfile
from .env_store import EnvStore
from .orchestrator_client import OrchestratorClient, OrchestratorError
from .source_validator import SourceValidationError

_DEFAULT_HEALTH_TIMEOUT_SECONDS = 90


class DeploymentError(RuntimeError):
    def __init__(self, message: str, *, log_tail: list[str] | None = None):
        super().__init__(message)
        self.log_tail = log_tail or []


class DeploymentService:
    def __init__(
        self,
        source_dir: Path,
        state_dir: Path,
        manifest: DeployManifest,
        env_store: EnvStore,
        orchestrator: OrchestratorClient,
        activity,
        db_env_provider=lambda: {},
        progress: DeployProgressTracker | None = None,
    ):
        self._source_dir = Path(source_dir)
        self._state_dir = Path(state_dir)
        self._manifest = manifest
        self._env_store = env_store
        self._orchestrator = orchestrator
        self._activity = activity
        # Supplies DATABASE_URL/DB_* (plan.md section 8, item 4) to merge
        # into the app's env at deploy/rollback time - the user's own
        # app.env can never contain these keys (config/reserved_env.py), so
        # there is no collision to resolve here, just a union.
        self._db_env_provider = db_env_provider
        self._progress = progress or DeployProgressTracker()

    def deploy_from_staging(
        self,
        staging_dir: Path,
        *,
        sha: str,
        source_kind: str,
        profile_id: str,
        deployed_by: str,
        dockerfile_relpath: str = "Dockerfile",
    ) -> DeploymentRecord:
        self._progress.start()
        try:
            profile = get_profile(profile_id)
            if profile is None:
                raise DeploymentError(f"Perfil desconocido: {profile_id}")

            self._progress.set_stage("validate")
            try:
                dockerfile_path = source_validator.validate_tree(staging_dir, dockerfile_relpath)
            except SourceValidationError as exc:
                shutil.rmtree(staging_dir, ignore_errors=True)
                raise DeploymentError(str(exc)) from exc

            dockerfile_text = dockerfile_path.read_text(encoding="utf-8", errors="replace")
            check = validate_dockerfile(dockerfile_text, profile)
            if not check.ok:
                shutil.rmtree(staging_dir, ignore_errors=True)
                raise DeploymentError("Dockerfile invalido: " + "; ".join(check.errors))

            self._promote_staging_to_source(staging_dir)

            try:
                app_env = {**self._env_store.load(), **self._db_env_provider()}
            except Exception as exc:  # noqa: BLE001 - surfaced to the caller, not logged with secret content
                raise DeploymentError(f"No se pudo leer app.env: {exc}") from exc

            record = self._run_build_and_activate(
                sha=sha,
                dockerfile_relpath=dockerfile_relpath,
                profile=profile,
                app_env=app_env,
                source_kind=source_kind,
                deployed_by=deployed_by,
            )
            self._progress.finish_success()
            return record
        except DeploymentError as exc:
            self._progress.finish_failure(str(exc))
            raise
        except Exception as exc:  # noqa: BLE001 - unexpected bug: still unstick the progress tracker
            self._progress.finish_failure(f"Error inesperado: {exc}")
            raise

    def _promote_staging_to_source(self, staging_dir: Path) -> None:
        # source_dir is a bind-mount root (compose.yml: ${DATA_DIR}/source),
        # so it can never be rmtree'd/replaced as a whole - only its
        # contents can. Clear those, then move each staged entry in.
        self._source_dir.mkdir(parents=True, exist_ok=True)
        for child in self._source_dir.iterdir():
            if child.is_symlink() or child.is_file():
                child.unlink()
            else:
                shutil.rmtree(child)
        for child in staging_dir.iterdir():
            shutil.move(str(child), str(self._source_dir / child.name))
        shutil.rmtree(staging_dir, ignore_errors=True)

    def _run_build_and_activate(
        self,
        *,
        sha: str,
        dockerfile_relpath: str,
        profile: RuntimeProfile,
        app_env: dict[str, str],
        source_kind: str,
        deployed_by: str,
    ) -> DeploymentRecord:
        now = lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        self._progress.set_stage("build")
        try:
            build_result = self._orchestrator.build_image(sha, dockerfile_relpath)
        except OrchestratorError as exc:
            self._record_failure(sha, profile.id, source_kind, deployed_by, str(exc))
            raise DeploymentError(f"El build fallo: {exc}") from exc

        if not build_result.get("ok"):
            error = build_result.get("error", "build fallo")
            self._record_failure(sha, profile.id, source_kind, deployed_by, error)
            raise DeploymentError(f"El build fallo: {error}", log_tail=build_result.get("log_tail", []))

        image_tag = build_result["image_tag"]

        self._progress.set_stage("candidate_start")
        try:
            candidate = self._orchestrator.start_candidate(image_tag, app_env, profile.default_health_check_path)
        except OrchestratorError as exc:
            self._record_failure(sha, profile.id, source_kind, deployed_by, str(exc))
            raise DeploymentError(f"No se pudo iniciar el candidato: {exc}") from exc

        if not candidate.get("ok"):
            error = candidate.get("error", "no se pudo iniciar el candidato")
            self._record_failure(sha, profile.id, source_kind, deployed_by, error)
            raise DeploymentError(error)

        candidate_name = candidate["candidate_name"]
        self._progress.set_stage("health_check")
        try:
            healthy = self._await_candidate_health(candidate_name, profile.default_health_check_path)
        except OrchestratorError as exc:
            log_tail = self._discard_candidate_with_logs(candidate_name)
            self._record_failure(sha, profile.id, source_kind, deployed_by, str(exc))
            raise DeploymentError(f"Fallo el chequeo de salud: {exc}", log_tail=log_tail) from exc

        if not healthy:
            log_tail = self._discard_candidate_with_logs(candidate_name)
            error = "El candidato no supero el chequeo de salud a tiempo; se mantiene la version anterior activa"
            self._record_failure(sha, profile.id, source_kind, deployed_by, error)
            raise DeploymentError(error, log_tail=log_tail)

        self._progress.set_stage("activate")
        try:
            activation = self._orchestrator.activate(image_tag, app_env)
        except OrchestratorError as exc:
            self._record_failure(sha, profile.id, source_kind, deployed_by, str(exc))
            raise DeploymentError(f"No se pudo activar el despliegue: {exc}") from exc

        if not activation.get("ok"):
            error = activation.get("error", "no se pudo activar")
            self._record_failure(sha, profile.id, source_kind, deployed_by, error)
            raise DeploymentError(error)

        record = DeploymentRecord(
            sha=sha,
            image_tag=image_tag,
            profile=profile.id,
            source_kind=source_kind,
            status="active",
            deployed_at=now(),
            deployed_by=deployed_by,
        )
        self._manifest.record_success(record)
        self._activity.record("deploy_success", {"sha": sha, "profile": profile.id})
        return record

    def _await_candidate_health(self, candidate_name: str, health_path: str) -> bool:
        # Polls a single-probe orchestrator endpoint instead of one blocking
        # call so each attempt can be logged to the progress tracker as it
        # happens (visible live in the deploy log) instead of the operator
        # only learning the final pass/fail after the whole wait.
        start = time.monotonic()
        deadline = start + _DEFAULT_HEALTH_TIMEOUT_SECONDS
        attempt = 0
        while True:
            attempt += 1
            probe = self._orchestrator.probe_candidate_health(candidate_name, health_path)
            healthy = bool(probe.get("healthy"))
            detail = probe.get("detail", "")
            elapsed = int(time.monotonic() - start)
            self._progress.append_log(
                f"Intento {attempt} ({elapsed}s): {health_path} -> "
                f"{'saludable' if healthy else 'no saludable'} ({detail})"
            )
            if healthy:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(2)

    def _discard_candidate_with_logs(self, candidate_name: str) -> list[str]:
        # Captures the candidate's own stdout/stderr BEFORE it's removed -
        # otherwise a crash (e.g. the app's entrypoint failing outright) is
        # only visible indirectly, as DNS resolution failures once Docker
        # tears down the dead container's network endpoint, which is
        # useless for actually diagnosing the crash.
        try:
            result = self._orchestrator.discard_candidate(candidate_name)
        except OrchestratorError:
            return []
        log_tail = result.get("log_tail") or []
        if log_tail:
            self._progress.append_log("--- logs del contenedor candidato ---")
            for line in log_tail:
                self._progress.append_log(line)
        return log_tail

    def _record_failure(self, sha: str, profile_id: str, source_kind: str, by: str, error: str) -> None:
        record = DeploymentRecord(
            sha=sha,
            image_tag="",
            profile=profile_id,
            source_kind=source_kind,
            status="failed",
            deployed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            deployed_by=by,
            error=error[:1000],
        )
        self._manifest.record_failure(record)
        self._activity.record("deploy_failed", {"sha": sha, "error": error[:300]})

    def rollback(self, by: str):
        state = self._manifest.read()
        if state.previous is None:
            raise DeploymentError("No hay una version anterior a la cual volver")

        try:
            app_env = {**self._env_store.load(), **self._db_env_provider()}
        except Exception as exc:  # noqa: BLE001
            raise DeploymentError(f"No se pudo leer app.env: {exc}") from exc

        try:
            activation = self._orchestrator.activate(state.previous.image_tag, app_env)
        except OrchestratorError as exc:
            raise DeploymentError(f"No se pudo revertir: {exc}") from exc
        if not activation.get("ok"):
            raise DeploymentError(activation.get("error", "no se pudo revertir"))

        new_state = self._manifest.rollback_to_previous(by)
        self._activity.record("deploy_rollback", {"sha": new_state.current.sha if new_state else ""})
        return new_state.current if new_state else None
