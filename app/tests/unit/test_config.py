"""Unit tests for configuration loading (T010)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ytdl.config import Config, ConfigError, load_config

BASE_ENV = {
    "ENVIRONMENT": "dev",
    "JOBS_TABLE": "ytdl-dev-jobs",
    "MEDIA_BUCKET": "ytdl-dev-media",
    "JOBS_QUEUE_URL": "https://sqs.eu-west-1.amazonaws.com/000000000000/ytdl-dev-jobs",
    "TOKEN_PARAM_NAME": "/ytdl/dev/api-token",
}

REQUIRED = tuple(BASE_ENV)

NUMERIC_DEFAULTS = (
    ("MAX_HEIGHT", "max_height", 1080),
    ("MAX_DURATION_SECONDS", "max_duration_seconds", 3600),
    ("MAX_OUTPUT_BYTES", "max_output_bytes", 4294967296),
    ("RETENTION_DAYS", "retention_days", 7),
    ("LINK_TTL_SECONDS", "link_ttl_seconds", 3600),
    ("RATE_LIMIT_PER_HOUR", "rate_limit_per_hour", 10),
    ("WORKER_EPHEMERAL_STORAGE_MB", "worker_ephemeral_storage_mb", 10240),
)


@pytest.mark.parametrize("name", REQUIRED)
def test_missing_required_variable_fails_fast(name: str) -> None:
    env = dict(BASE_ENV)
    del env[name]

    with pytest.raises(ConfigError) as excinfo:
        load_config(env)

    assert excinfo.value.variable == name
    assert name in str(excinfo.value)


@pytest.mark.parametrize("name", REQUIRED)
def test_blank_required_variable_fails_fast(name: str) -> None:
    env = dict(BASE_ENV)
    env[name] = "   "

    with pytest.raises(ConfigError) as excinfo:
        load_config(env)

    assert excinfo.value.variable == name


def test_defaults_from_environment_table() -> None:
    config = load_config(dict(BASE_ENV))

    assert isinstance(config, Config)
    for _variable, attribute, expected in NUMERIC_DEFAULTS:
        value = getattr(config, attribute)
        assert value == expected
        assert isinstance(value, int)
    assert config.log_level == "INFO"


def test_required_values_are_passed_through() -> None:
    config = load_config(dict(BASE_ENV))

    assert config.environment == "dev"
    assert config.table_name == "ytdl-dev-jobs"
    assert config.media_bucket == "ytdl-dev-media"
    assert config.queue_url == BASE_ENV["JOBS_QUEUE_URL"]
    assert config.token_param_name == "/ytdl/dev/api-token"


@pytest.mark.parametrize(
    ("variable", "attribute", "raw", "expected"),
    [
        ("MAX_HEIGHT", "max_height", "720", 720),
        ("MAX_DURATION_SECONDS", "max_duration_seconds", "1800", 1800),
        ("MAX_OUTPUT_BYTES", "max_output_bytes", "1024", 1024),
        ("RETENTION_DAYS", "retention_days", "1", 1),
        ("LINK_TTL_SECONDS", "link_ttl_seconds", "60", 60),
        ("RATE_LIMIT_PER_HOUR", "rate_limit_per_hour", "1", 1),
        ("WORKER_EPHEMERAL_STORAGE_MB", "worker_ephemeral_storage_mb", "3072", 3072),
    ],
)
def test_numeric_values_parse_as_integers(
    variable: str, attribute: str, raw: str, expected: int
) -> None:
    env = dict(BASE_ENV)
    env[variable] = raw

    value = getattr(load_config(env), attribute)

    assert value == expected
    assert isinstance(value, int)
    assert not isinstance(value, str)


@pytest.mark.parametrize("variable", [name for name, _attribute, _default in NUMERIC_DEFAULTS])
def test_non_numeric_value_names_the_variable(variable: str) -> None:
    env = dict(BASE_ENV)
    env[variable] = "many"

    with pytest.raises(ConfigError) as excinfo:
        load_config(env)

    assert excinfo.value.variable == variable


@pytest.mark.parametrize(
    ("variable", "attribute"),
    [
        ("COOKIES_S3_URI", "cookies_s3_uri"),
        ("PROXY_PARAM_NAME", "proxy_param_name"),
        ("PLAYER_CLIENT", "player_client"),
    ],
)
def test_optional_hooks_are_none_when_unset_or_empty(variable: str, attribute: str) -> None:
    assert getattr(load_config(dict(BASE_ENV)), attribute) is None

    blank = dict(BASE_ENV)
    blank[variable] = ""
    assert getattr(load_config(blank), attribute) is None

    configured = dict(BASE_ENV)
    configured[variable] = "s3://bucket/config/cookies.txt"
    assert getattr(load_config(configured), attribute) == "s3://bucket/config/cookies.txt"


def test_log_level_defaults_and_is_normalised() -> None:
    assert load_config(dict(BASE_ENV)).log_level == "INFO"

    env = dict(BASE_ENV)
    env["LOG_LEVEL"] = "debug"
    assert load_config(env).log_level == "DEBUG"


def test_unknown_log_level_names_the_variable() -> None:
    env = dict(BASE_ENV)
    env["LOG_LEVEL"] = "loud"

    with pytest.raises(ConfigError) as excinfo:
        load_config(env)

    assert excinfo.value.variable == "LOG_LEVEL"


def test_config_is_frozen() -> None:
    config = load_config(dict(BASE_ENV))

    with pytest.raises(FrozenInstanceError):
        config.max_height = 720  # type: ignore[misc]


def test_load_config_reads_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable, _attribute, _default in NUMERIC_DEFAULTS:
        monkeypatch.delenv(variable, raising=False)
    for variable in ("COOKIES_S3_URI", "PROXY_PARAM_NAME", "PLAYER_CLIENT", "LOG_LEVEL"):
        monkeypatch.delenv(variable, raising=False)
    for variable, value in BASE_ENV.items():
        monkeypatch.setenv(variable, value)

    config = load_config()

    assert config.environment == "dev"
    assert config.max_height == 1080
