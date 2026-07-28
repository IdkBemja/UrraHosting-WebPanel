from config.runtime_profiles import get as get_profile

from app.services.dockerfile_validator import validate

PYTHON_PROFILE = get_profile("python")

VALID_DOCKERFILE = """
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
USER 10001:10001
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:$APP_PORT app:app"]
"""


def test_valid_dockerfile_passes():
    result = validate(VALID_DOCKERFILE, PYTHON_PROFILE)
    assert result.ok, result.errors


def test_missing_user_is_rejected():
    text = VALID_DOCKERFILE.replace("USER 10001:10001\n", "")
    result = validate(text, PYTHON_PROFILE)
    assert not result.ok
    assert any("usuario no-root" in e for e in result.errors)


def test_explicit_root_user_is_rejected():
    text = VALID_DOCKERFILE.replace("USER 10001:10001", "USER root")
    result = validate(text, PYTHON_PROFILE)
    assert not result.ok


def test_disallowed_base_image_is_rejected():
    text = VALID_DOCKERFILE.replace("FROM python:3.12-slim", "FROM node:20-slim")
    result = validate(text, PYTHON_PROFILE)
    assert not result.ok
    assert "no esta permitida" in result.errors[0]


def test_remote_add_is_rejected():
    text = VALID_DOCKERFILE + "\nADD https://example.com/payload.sh /payload.sh\n"
    result = validate(text, PYTHON_PROFILE)
    assert not result.ok
    assert any("ADD con URL remota" in e for e in result.errors)


def test_buildkit_secret_mount_is_rejected():
    text = VALID_DOCKERFILE + "\nRUN --mount=type=secret,id=foo cat /run/secrets/foo\n"
    result = validate(text, PYTHON_PROFILE)
    assert not result.ok


def test_buildkit_ssh_mount_is_rejected():
    text = VALID_DOCKERFILE + "\nRUN --mount=type=ssh git clone git@example.com:x.git\n"
    result = validate(text, PYTHON_PROFILE)
    assert not result.ok


def test_missing_from_is_rejected():
    result = validate("RUN echo hi\n", PYTHON_PROFILE)
    assert not result.ok


def test_oversized_dockerfile_is_rejected():
    huge = "FROM python:3.12-slim\n" + ("# padding\n" * 20000)
    result = validate(huge, PYTHON_PROFILE)
    assert not result.ok
