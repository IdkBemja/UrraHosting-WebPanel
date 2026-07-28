import pytest

from app.services.git_repository import GitRepositoryError, parse_and_validate_url, validate_ref

ALLOWED = ("github.com", "gitlab.com", "bitbucket.org")


def test_https_allowed_host_accepted():
    parsed = parse_and_validate_url("https://github.com/user/repo.git", ALLOWED)
    assert parsed.host == "github.com"
    assert not parsed.is_ssh


def test_scp_like_ssh_url_accepted():
    parsed = parse_and_validate_url("git@github.com:user/repo.git", ALLOWED)
    assert parsed.host == "github.com"
    assert parsed.is_ssh


def test_ssh_scheme_url_accepted():
    parsed = parse_and_validate_url("ssh://git@gitlab.com/user/repo.git", ALLOWED)
    assert parsed.host == "gitlab.com"
    assert parsed.is_ssh


def test_disallowed_host_rejected():
    with pytest.raises(GitRepositoryError):
        parse_and_validate_url("https://evil.example.com/x.git", ALLOWED)


def test_embedded_credentials_rejected():
    with pytest.raises(GitRepositoryError):
        parse_and_validate_url("https://user:token@github.com/user/repo.git", ALLOWED)


def test_disallowed_scheme_rejected():
    with pytest.raises(GitRepositoryError):
        parse_and_validate_url("file:///etc/passwd", ALLOWED)
    with pytest.raises(GitRepositoryError):
        parse_and_validate_url("git://github.com/user/repo.git", ALLOWED)


def test_empty_url_rejected():
    with pytest.raises(GitRepositoryError):
        parse_and_validate_url("", ALLOWED)


def test_valid_ref_accepted():
    assert validate_ref("main") == "main"
    assert validate_ref("release/1.2.3") == "release/1.2.3"


def test_ref_with_leading_dash_rejected():
    with pytest.raises(GitRepositoryError):
        validate_ref("--upload-pack=touch /tmp/pwned")


def test_ref_with_shell_metacharacters_rejected():
    with pytest.raises(GitRepositoryError):
        validate_ref("main; rm -rf /")


def test_empty_ref_rejected():
    with pytest.raises(GitRepositoryError):
        validate_ref("")
