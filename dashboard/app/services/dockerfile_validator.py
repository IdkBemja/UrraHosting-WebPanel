"""Dockerfile policy enforcement (plan.md sections 6.3 and 7).

The application always supplies its own Dockerfile - the platform never
builds from a Compose file or a template on the user's behalf - but that
Dockerfile still has to respect the platform's runtime-profile contract
(approved base image, non-root user, fixed listen port) before it is ever
handed to the orchestrator to build. This is a deliberately conservative
line-based policy check, not a full BuildKit-grammar parser: anything it
can't confidently classify as safe is rejected rather than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from config.runtime_profiles import RuntimeProfile

_MAX_DOCKERFILE_BYTES = 64 * 1024
_FORBIDDEN_MOUNT_TYPES = ("type=ssh", "type=secret")
_ROOT_USERS = {"root", "0", "0:0", "root:root"}


class DockerfileValidationError(ValueError):
    pass


@dataclass
class DockerfileCheckResult:
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def _iter_instructions(text: str):
    """Yields (instruction, rest) pairs, joining line-continuations (\\)."""
    buffer = ""
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not buffer and (not line.strip() or line.strip().startswith("#")):
            continue
        buffer += line
        if buffer.endswith("\\"):
            buffer = buffer[:-1] + " "
            continue
        stripped = buffer.strip()
        buffer = ""
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        instruction = parts[0].upper()
        rest = parts[1] if len(parts) > 1 else ""
        yield instruction, rest


def validate(dockerfile_text: str, profile: RuntimeProfile) -> DockerfileCheckResult:
    errors: list[str] = []
    warnings: list[str] = []

    if len(dockerfile_text.encode("utf-8")) > _MAX_DOCKERFILE_BYTES:
        return DockerfileCheckResult(errors=["El Dockerfile supera el tamano maximo permitido (64 KB)"], warnings=[])

    stages: list[str] = []
    current_user = "root"  # Docker's own default when a stage has no USER.
    final_from: str | None = None
    saw_from = False

    for instruction, rest in _iter_instructions(dockerfile_text):
        if instruction == "FROM":
            saw_from = True
            image_ref = rest.split(None, 1)[0]
            final_from = image_ref
            stages.append(image_ref)
            current_user = "root"
        elif instruction == "USER":
            current_user = rest.strip().split(None, 1)[0] if rest.strip() else "root"
        elif instruction == "ADD":
            if re.search(r"https?://", rest, re.IGNORECASE):
                errors.append(
                    f"ADD con URL remota no esta permitido ('{rest.strip()[:80]}'); usa COPY con archivos del propio repositorio"
                )
        elif instruction == "RUN":
            for mount_type in _FORBIDDEN_MOUNT_TYPES:
                if mount_type in rest:
                    errors.append(f"RUN --mount={mount_type} no esta permitido")
        elif instruction in {"COPY"} and "--from=" not in rest and rest.strip().startswith("--"):
            # Any other unrecognized COPY flag (e.g. future BuildKit
            # features) is rejected rather than silently allowed through.
            if not re.match(r"^--from=", rest.strip()):
                warnings.append(f"COPY con opcion no reconocida: {rest.strip()[:80]}")

    if not saw_from:
        errors.append("El Dockerfile no contiene una instruccion FROM")
        return DockerfileCheckResult(errors=errors, warnings=warnings)

    if final_from is None or not any(
        final_from.lower().startswith(prefix.lower()) for prefix in profile.allowed_base_images
    ):
        allowed = ", ".join(profile.allowed_base_images)
        errors.append(
            f"La imagen base final '{final_from}' no esta permitida para el perfil '{profile.id}'. "
            f"Imagenes permitidas: {allowed}"
        )

    if current_user.lower() in _ROOT_USERS:
        errors.append(
            "El Dockerfile debe fijar un usuario no-root con USER antes del CMD/ENTRYPOINT final "
            "(por ejemplo: USER 10001:10001)"
        )

    return DockerfileCheckResult(errors=errors, warnings=warnings)
