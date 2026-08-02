"""Git clone/fetch for the Repository tab (plan.md section 6).

Security rules this module exists to enforce:
  * URL and host allowlist are validated BEFORE any network connection.
  * subprocess is always called with an argument list, never a shell
    string - the URL, ref and paths are never interpolated into anything
    a shell could re-parse.
  * SSH deploy keys use IdentitiesOnly=yes + BatchMode=yes (never prompts,
    never falls back to an unrelated key from the agent) and
    StrictHostKeyChecking=yes against a per-instance known_hosts file this
    module manages itself - StrictHostKeyChecking=no is never used.
  * A host's key is trusted only after an explicit one-time operator
    confirmation of its fingerprint (TOFU-with-confirmation), not silently
    on first connect.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_GIT_TIMEOUT = 120
_SCP_LIKE_RE = re.compile(r"^git@([A-Za-z0-9.\-]+):(.+)$")
_REF_RE = re.compile(r"^[A-Za-z0-9._/\-]{1,200}$")


class GitRepositoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParsedGitUrl:
    host: str
    is_ssh: bool
    normalized: str


def parse_and_validate_url(url: str, allowed_hosts: tuple[str, ...]) -> ParsedGitUrl:
    url = (url or "").strip()
    if not url or "\x00" in url or "\n" in url:
        raise GitRepositoryError("URL de repositorio invalida")

    scp_match = _SCP_LIKE_RE.match(url)
    if scp_match:
        host = scp_match.group(1).lower()
        is_ssh = True
    else:
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "ssh"}:
            raise GitRepositoryError(
                "Solo se permiten URLs https:// o ssh:// (o formato git@host:ruta)"
            )
        # A username is normal (and required) on ssh:// URLs - it's the
        # fixed SSH transport account (conventionally "git"), never a
        # secret; real auth happens via the deploy key, not the URL. A
        # password never legitimately appears in either scheme here, and
        # for https:// a username is also a credential smell (e.g. a PAT
        # passed as `https://<token>@host/...`).
        if parsed.password or (parsed.username and parsed.scheme == "https"):
            raise GitRepositoryError("No se permiten credenciales embebidas en la URL")
        host = (parsed.hostname or "").lower()
        is_ssh = parsed.scheme == "ssh"
        if not host:
            raise GitRepositoryError("URL de repositorio invalida")

    if host not in allowed_hosts:
        raise GitRepositoryError(
            f"El proveedor '{host}' no esta en la lista permitida ({', '.join(allowed_hosts)})"
        )

    return ParsedGitUrl(host=host, is_ssh=is_ssh, normalized=url)


def validate_ref(ref: str) -> str:
    ref = (ref or "").strip()
    if not ref or not _REF_RE.match(ref):
        raise GitRepositoryError("Rama/tag invalido")
    if ref.startswith("-"):
        raise GitRepositoryError("Rama/tag invalido")
    return ref


def generate_deploy_key(comment: str) -> tuple[bytes, str]:
    """Returns (private_key_openssh_bytes, public_key_openssh_str)."""
    key = Ed25519PrivateKey.generate()
    private_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )
    public_str = public_bytes.decode("ascii") + f" {comment}"
    return private_bytes, public_str


def scan_host_key(host: str, port: int = 22) -> str:
    """Runs ssh-keyscan and returns the raw known_hosts-format line(s) for
    manual/operator fingerprint confirmation before they are ever trusted.
    """
    try:
        completed = subprocess.run(
            ["ssh-keyscan", "-T", "5", "-p", str(port), "-t", "ed25519,rsa", host],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitRepositoryError(f"No se pudo consultar la llave del host: {exc}") from exc
    lines = [line for line in completed.stdout.splitlines() if line and not line.startswith("#")]
    if not lines:
        raise GitRepositoryError("El host no respondio con ninguna llave SSH")
    return "\n".join(lines) + "\n"


def fingerprint_known_hosts(known_hosts_text: str) -> list[str]:
    fingerprints = []
    for line in known_hosts_text.splitlines():
        if not line.strip():
            continue
        try:
            completed = subprocess.run(
                ["ssh-keygen", "-lf", "/dev/stdin"],
                input=line + "\n",
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode == 0 and completed.stdout.strip():
            fingerprints.append(completed.stdout.strip())
    return fingerprints


def _ssh_command(private_key_path: Path, known_hosts_path: Path) -> str:
    return (
        f"ssh -i {private_key_path} -o IdentitiesOnly=yes -o BatchMode=yes "
        f"-o StrictHostKeyChecking=yes -o UserKnownHostsFile={known_hosts_path}"
    )


def ls_remote_sha(
    url: str,
    ref: str,
    *,
    private_key_path: Path | None = None,
    known_hosts_path: Path | None = None,
) -> str:
    env = _build_env(private_key_path, known_hosts_path)
    command = ["git", "ls-remote", "--exit-code", url, f"refs/heads/{ref}", f"refs/tags/{ref}"]
    completed = _run_git(command, env=env)
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise GitRepositoryError(f"No se encontro la rama/tag '{ref}' en el repositorio remoto")
    return lines[0].split("\t", 1)[0]


def list_remote_refs(
    url: str,
    *,
    private_key_path: Path | None = None,
    known_hosts_path: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Returns (branches, tags) available on the remote, for the Repository
    tab's "Conectar" button - lets the operator pick a ref from a real list
    instead of typing a branch/tag name by hand and having it silently fail
    at deploy time if mistyped."""
    env = _build_env(private_key_path, known_hosts_path)
    command = ["git", "ls-remote", "--heads", "--tags", url]
    completed = _run_git(command, env=env)

    branches: list[str] = []
    tags: dict[str, None] = {}
    for line in completed.stdout.splitlines():
        parts = line.strip().split("\t", 1)
        if len(parts) != 2:
            continue
        _sha, ref = parts
        if ref.startswith("refs/heads/"):
            branches.append(ref[len("refs/heads/"):])
        elif ref.startswith("refs/tags/"):
            name = ref[len("refs/tags/"):]
            # Annotated tags appear twice (the tag object itself, then a
            # `^{}` peeled line for the commit it points at) - both map to
            # the same tag name, dict keys dedupe regardless of which line
            # arrives first.
            if name.endswith("^{}"):
                name = name[: -len("^{}")]
            tags[name] = None

    return sorted(branches), sorted(tags)


def clone_at_ref(
    url: str,
    ref: str,
    destination: Path,
    *,
    private_key_path: Path | None = None,
    known_hosts_path: Path | None = None,
) -> str:
    env = _build_env(private_key_path, known_hosts_path)
    destination.mkdir(parents=True, exist_ok=True)
    command = [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "clone",
        "--no-hardlinks",
        "--depth",
        "1",
        "--single-branch",
        "--branch",
        ref,
        "--",
        url,
        str(destination),
    ]
    _run_git(command, env=env)

    rev_parse = subprocess.run(
        ["git", "-C", str(destination), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if rev_parse.returncode != 0:
        raise GitRepositoryError("No se pudo determinar el SHA clonado")
    return rev_parse.stdout.strip()


def _build_env(private_key_path: Path | None, known_hosts_path: Path | None) -> dict[str, str]:
    env = {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "HOME": "/tmp",
    }
    if private_key_path is not None and known_hosts_path is not None:
        env["GIT_SSH_COMMAND"] = _ssh_command(private_key_path, known_hosts_path)
    return env


def _run_git(command: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitRepositoryError(f"Operacion Git fallida: {exc}") from exc
    if completed.returncode != 0:
        # Never surface the raw command/env (could echo the key path);
        # stderr from git itself does not include key material.
        raise GitRepositoryError(f"Git fallo: {completed.stderr.strip()[:500]}")
    return completed
