from config.platform_config import load_from_environ

_BASE_ENV = {
    "INSTANCE_ID": "test-instance",
    "DATA_DIR": "/data",
    "DASHBOARD_PORT": "5230",
    "APP_PORT": "5231",
    "APP_USER": "admin",
    "APP_PASSWORD": "a-strong-unique-password-123",
    "APP_SECRET": "a" * 40,
    "MAX_UPLOAD_MB": "1024",
    "APP_STORAGE_LIMIT_GB": "10",
}


def test_valid_config_loads():
    config, result = load_from_environ(dict(_BASE_ENV))
    assert config is not None, result.errors
    assert result.ok
    assert config.instance_id == "test-instance"
    assert config.app_port == 5231


def test_placeholder_app_secret_rejected():
    env = dict(_BASE_ENV, APP_SECRET="cambia-este-secreto-largo-y-aleatorio")
    config, result = load_from_environ(env)
    assert config is None
    assert not result.ok


def test_short_app_secret_rejected():
    env = dict(_BASE_ENV, APP_SECRET="short")
    config, result = load_from_environ(env)
    assert config is None


def test_placeholder_app_password_rejected():
    env = dict(_BASE_ENV, APP_PASSWORD="cambia-esta-contrasena-unica")
    config, result = load_from_environ(env)
    assert config is None


def test_same_dashboard_and_app_port_rejected():
    env = dict(_BASE_ENV, APP_PORT="5230")
    config, result = load_from_environ(env)
    assert config is None
    assert any("mismo puerto" in e for e in result.errors)


def test_missing_required_var_rejected():
    env = dict(_BASE_ENV)
    del env["INSTANCE_ID"]
    config, result = load_from_environ(env)
    assert config is None
    assert any("INSTANCE_ID" in e for e in result.errors)


def test_debug_true_produces_warning_not_error():
    env = dict(_BASE_ENV, DEBUG="true")
    config, result = load_from_environ(env)
    assert config is not None
    assert any("DEBUG" in w for w in result.warnings)


def test_unsupported_db_engine_rejected():
    env = dict(_BASE_ENV, ALLOWED_DB_ENGINES="mysql,mongodb")
    config, result = load_from_environ(env)
    assert config is None
