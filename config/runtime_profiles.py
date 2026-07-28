"""Managed runtime profiles (plan.md section 7).

A profile is NOT a build system the platform runs on the user's behalf -
the application still supplies its own `Dockerfile` (plan.md section 3.2)
and `dockerfile_validator.py` is what actually enforces the policy below at
deploy time. This module is the single declarative source for that policy
(allowed base images, fixed listen port contract, health check default) so
the validator, the UI ("Software" tab) and the `runtime/<profile>/` example
templates all agree on the same rules instead of drifting apart.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeProfile:
    id: str
    label: str
    description: str
    # Dockerfile FROM lines must start with one of these (case-insensitive,
    # matched against the image name before any tag/digest). Multi-stage
    # builds may use unlisted images for earlier stages, but the FINAL
    # stage's base image must match one of these prefixes.
    allowed_base_images: tuple[str, ...]
    # Default HTTP path used for the platform health check unless the
    # profile documents another one; the app must answer 2xx/3xx on
    # APP_PORT at this path within the deploy health-check window.
    default_health_check_path: str
    example_dockerfile: str
    example_entrypoint: str | None = None


_PYTHON_EXAMPLE_DOCKERFILE = """FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# APP_PORT is injected by the platform at container start and is the ONLY
# port that will ever receive traffic; it changes per instance, so the app
# must bind to it at runtime (shell form CMD expands $APP_PORT). Do not
# hardcode a port.
USER 10001:10001
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:$APP_PORT app:app"]
"""

_NODE_EXAMPLE_DOCKERFILE = """FROM node:20-slim

WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY . .

# Read process.env.APP_PORT in server.js and listen on it - the platform
# assigns and injects this port; it must not be hardcoded.
USER 10001:10001
CMD ["node", "server.js"]
"""

_JAVA_EXAMPLE_DOCKERFILE = """FROM eclipse-temurin:21-jre-jammy

WORKDIR /app
COPY target/app.jar app.jar

# The platform injects APP_PORT; shell form lets $APP_PORT expand at
# container start instead of being baked in at build time.
USER 10001:10001
CMD ["sh", "-c", "java -jar app.jar --server.port=$APP_PORT"]
"""

_DOTNET_EXAMPLE_DOCKERFILE = """FROM mcr.microsoft.com/dotnet/aspnet:8.0

WORKDIR /app
COPY publish/ .

USER 10001:10001
CMD ["sh", "-c", "ASPNETCORE_URLS=http://+:$APP_PORT dotnet app.dll"]
"""

PROFILES: dict[str, RuntimeProfile] = {
    "python": RuntimeProfile(
        id="python",
        label="Python (WSGI/ASGI)",
        description="Aplicaciones Flask, Django, FastAPI u otras WSGI/ASGI servidas con gunicorn/uvicorn.",
        allowed_base_images=("python:",),
        default_health_check_path="/",
        example_dockerfile=_PYTHON_EXAMPLE_DOCKERFILE,
    ),
    "node": RuntimeProfile(
        id="node",
        label="Node.js",
        description="Aplicaciones Node.js (Express, Next.js en modo standalone, etc.).",
        allowed_base_images=("node:",),
        default_health_check_path="/",
        example_dockerfile=_NODE_EXAMPLE_DOCKERFILE,
    ),
    "java": RuntimeProfile(
        id="java",
        label="Java (JRE)",
        description="Artefactos o servidores web empaquetados en JAR, ejecutados sobre un JRE.",
        allowed_base_images=("eclipse-temurin:", "amazoncorretto:", "openjdk:"),
        default_health_check_path="/",
        example_dockerfile=_JAVA_EXAMPLE_DOCKERFILE,
    ),
    "dotnet": RuntimeProfile(
        id="dotnet",
        label="C#/.NET (ASP.NET Core)",
        description="Aplicaciones ASP.NET Core publicadas y ejecutadas sobre el runtime oficial de .NET.",
        allowed_base_images=("mcr.microsoft.com/dotnet/aspnet:", "mcr.microsoft.com/dotnet/runtime:"),
        default_health_check_path="/",
        example_dockerfile=_DOTNET_EXAMPLE_DOCKERFILE,
    ),
}


def get(profile_id: str) -> RuntimeProfile | None:
    return PROFILES.get(profile_id)


def list_profiles() -> list[RuntimeProfile]:
    return list(PROFILES.values())
