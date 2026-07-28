"""Thin wrapper around the Docker SDK, talking to the instance's scoped
docker-socket-proxy instead of /var/run/docker.sock directly (plan.md
section 9.1: the dashboard never receives direct Docker access). Every
operation re-fetches the container and checks its
`com.urrahosting.instance` label matches our own INSTANCE_ID before acting,
so a misconfigured DOCKER_HOST can never make this dashboard control a
container belonging to a different instance.

Scope is intentionally read/lifecycle-only for the app container: status,
start, stop, restart, logs. There is no exec, no build, no image
management here - those are the orchestrator's job (services/deployment.py),
which talks to a separate, more privileged docker-proxy.
"""

from __future__ import annotations

from typing import Iterator

import docker
import docker.errors


class DockerControlError(RuntimeError):
    pass


class InstanceDockerClient:
    def __init__(self, docker_host: str, container_name: str, instance_id: str):
        # docker.DockerClient(...) is NOT lazy: it connects immediately to
        # negotiate the API version. Building it eagerly here would crash
        # the whole app factory (and the dashboard container) if the
        # docker-proxy service isn't reachable yet on a cold start, which
        # `depends_on` alone doesn't guarantee. So the real client is built
        # lazily on first use instead, and connection failures surface as a
        # DockerControlError scoped to that one request.
        self._docker_host = docker_host
        self._client: docker.DockerClient | None = None
        self._container_name = container_name
        self._instance_id = instance_id

    def _get_client(self) -> docker.DockerClient:
        if self._client is None:
            try:
                self._client = docker.DockerClient(base_url=self._docker_host)
            except docker.errors.DockerException as exc:
                raise DockerControlError(f"No se pudo conectar al docker-proxy: {exc}") from exc
        return self._client

    def _get_container(self):
        try:
            container = self._get_client().containers.get(self._container_name)
        except docker.errors.NotFound as exc:
            raise DockerControlError(f"Contenedor '{self._container_name}' no encontrado") from exc
        except (docker.errors.DockerException, OSError) as exc:
            raise DockerControlError(f"Error de Docker: {exc}") from exc

        label = (container.labels or {}).get("com.urrahosting.instance")
        if label != self._instance_id:
            raise DockerControlError("El contenedor encontrado no pertenece a esta instancia")
        return container

    def status(self) -> dict:
        container = self._get_container()
        container.reload()
        state = container.attrs.get("State", {})
        health = (state.get("Health") or {}).get("Status")
        # Read the image reference straight off the container's own inspect
        # data (Config.Image) rather than the docker-py `container.image`
        # convenience property, which issues a separate `images.get()` API
        # call - this proxy intentionally has IMAGES=0 (dashboard has no
        # image-management access at all), so that call would 403.
        image_ref = container.attrs.get("Config", {}).get("Image")
        return {
            "status": state.get("Status", "unknown"),
            "running": bool(state.get("Running")),
            "started_at": state.get("StartedAt", ""),
            "health": health,
            "image": image_ref,
        }

    def start(self) -> None:
        try:
            self._get_container().start()
        except (docker.errors.DockerException, OSError) as exc:
            raise DockerControlError(f"No se pudo iniciar: {exc}") from exc

    def stop(self, timeout: int = 30) -> None:
        try:
            self._get_container().stop(timeout=timeout)
        except (docker.errors.DockerException, OSError) as exc:
            raise DockerControlError(f"No se pudo detener: {exc}") from exc

    def restart(self, timeout: int = 30) -> None:
        try:
            self._get_container().restart(timeout=timeout)
        except (docker.errors.DockerException, OSError) as exc:
            raise DockerControlError(f"No se pudo reiniciar: {exc}") from exc

    def stream_logs(self, tail: int = 50) -> Iterator[bytes]:
        container = self._get_container()
        try:
            yield from container.logs(stream=True, follow=True, tail=tail, timestamps=False)
        except (docker.errors.DockerException, OSError) as exc:
            raise DockerControlError(f"No se pudo leer logs: {exc}") from exc
