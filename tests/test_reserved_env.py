from config.reserved_env import is_reserved


def test_exact_reserved_names_blocked():
    for name in ["PORT", "APP_PORT", "HOST", "APP_SECRET", "DATABASE_URL", "DB_PASSWORD"]:
        assert is_reserved(name), name


def test_reserved_is_case_insensitive():
    assert is_reserved("app_port")
    assert is_reserved("Database_Url")


def test_reserved_prefixes_blocked():
    for name in ["DOCKER_HOST", "COMPOSE_PROJECT_NAME", "TRAEFIK_WEB_ENTRYPOINT", "MYSQL_ROOT_PASSWORD", "FLASK_ENV"]:
        assert is_reserved(name), name


def test_ordinary_names_allowed():
    for name in ["STRIPE_API_KEY", "LOG_LEVEL", "FEATURE_FLAG_X", "MY_APP_TOKEN"]:
        assert not is_reserved(name), name


def test_empty_name_is_reserved():
    assert is_reserved("")
    assert is_reserved("   ")
