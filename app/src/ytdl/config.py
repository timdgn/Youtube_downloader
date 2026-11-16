"""Validated, immutable runtime configuration (T016)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_MAX_HEIGHT = 1080
DEFAULT_MAX_DURATION_SECONDS = 3600
DEFAULT_MAX_OUTPUT_BYTES = 4294967296
DEFAULT_RETENTION_DAYS = 7
DEFAULT_LINK_TTL_SECONDS = 3600
DEFAULT_RATE_LIMIT_PER_HOUR = 10
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_WORKER_EPHEMERAL_STORAGE_MB = 10240

# The bot's fixed vocabulary (data-model.md "Tier vocabulary"). The ceilings live
# in bot/commands.py; this tuple is what the default-tier setting is validated on.
TELEGRAM_TIERS: tuple[str, ...] = ("low", "medium", "high", "veryhigh", "mp3")
DEFAULT_TELEGRAM_TIER = "high"

_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

# Only a bot invocation needs these; the api and worker functions build without them.
_BOT_REQUIRED = (
    ("TELEGRAM_TOKEN_PARAM", "telegram_token_param"),
    ("TELEGRAM_WEBHOOK_SECRET_PARAM", "telegram_webhook_secret_param"),
    ("TELEGRAM_APPROVED_PARAM", "telegram_approved_param"),
    ("TELEGRAM_WEBHOOK_URL", "telegram_webhook_url"),
)


class ConfigError(ValueError):
    """A required or malformed environment setting."""

    def __init__(self, variable: str, reason: str) -> None:
        super().__init__(f"{variable}: {reason}")
        self.variable = variable


@dataclass(frozen=True)
class Config:
    """Every setting the service reads, resolved once per cold start."""

    environment: str
    table_name: str
    media_bucket: str
    queue_url: str
    token_param_name: str
    max_height: int
    max_duration_seconds: int
    max_output_bytes: int
    retention_days: int
    link_ttl_seconds: int
    rate_limit_per_hour: int
    log_level: str
    cookies_s3_uri: str | None
    proxy_param_name: str | None
    player_client: str | None
    telegram_token_param: str | None = None
    telegram_webhook_secret_param: str | None = None
    telegram_approved_param: str | None = None
    telegram_webhook_url: str | None = None
    telegram_default_tier: str = DEFAULT_TELEGRAM_TIER
    telegram_test_video_id: str | None = None
    job_events_topic_arn: str | None = None
    ytdlp_version: str | None = None
    worker_ephemeral_storage_mb: int = DEFAULT_WORKER_EPHEMERAL_STORAGE_MB


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Build the configuration from ``env`` or the process environment.

    Callers resolve it once per cold start and keep the frozen result.
    """
    source: Mapping[str, str] = os.environ if env is None else env

    return Config(
        environment=_required(source, "ENVIRONMENT"),
        table_name=_required(source, "JOBS_TABLE"),
        media_bucket=_required(source, "MEDIA_BUCKET"),
        queue_url=_required(source, "JOBS_QUEUE_URL"),
        token_param_name=_required(source, "TOKEN_PARAM_NAME"),
        max_height=_integer(source, "MAX_HEIGHT", DEFAULT_MAX_HEIGHT),
        max_duration_seconds=_integer(source, "MAX_DURATION_SECONDS", DEFAULT_MAX_DURATION_SECONDS),
        max_output_bytes=_integer(source, "MAX_OUTPUT_BYTES", DEFAULT_MAX_OUTPUT_BYTES),
        retention_days=_integer(source, "RETENTION_DAYS", DEFAULT_RETENTION_DAYS),
        link_ttl_seconds=_integer(source, "LINK_TTL_SECONDS", DEFAULT_LINK_TTL_SECONDS),
        rate_limit_per_hour=_integer(source, "RATE_LIMIT_PER_HOUR", DEFAULT_RATE_LIMIT_PER_HOUR),
        log_level=_log_level(source),
        cookies_s3_uri=_optional(source, "COOKIES_S3_URI"),
        proxy_param_name=_optional(source, "PROXY_PARAM_NAME"),
        player_client=_optional(source, "PLAYER_CLIENT"),
        telegram_token_param=_optional(source, "TELEGRAM_TOKEN_PARAM"),
        telegram_webhook_secret_param=_optional(source, "TELEGRAM_WEBHOOK_SECRET_PARAM"),
        telegram_approved_param=_optional(source, "TELEGRAM_APPROVED_PARAM"),
        telegram_webhook_url=_optional(source, "TELEGRAM_WEBHOOK_URL"),
        telegram_default_tier=_tier(source),
        telegram_test_video_id=_optional(source, "TELEGRAM_TEST_VIDEO_ID"),
        job_events_topic_arn=_optional(source, "JOB_EVENTS_TOPIC_ARN"),
        ytdlp_version=_optional(source, "YTDLP_VERSION"),
        worker_ephemeral_storage_mb=_integer(
            source, "WORKER_EPHEMERAL_STORAGE_MB", DEFAULT_WORKER_EPHEMERAL_STORAGE_MB
        ),
    )


def require_bot_config(config: Config) -> Config:
    """Return ``config`` when every bot setting is present, or name the missing one.

    Called once per cold start by the bot function only, so the api and worker
    keep building from their own environments.
    """
    for variable, attribute in _BOT_REQUIRED:
        if getattr(config, attribute) is None:
            raise ConfigError(variable, "missing required bot setting")
    return config


def _tier(source: Mapping[str, str]) -> str:
    raw = source.get("TELEGRAM_DEFAULT_TIER")
    if raw is None:
        return DEFAULT_TELEGRAM_TIER
    value = raw.strip()
    if value not in TELEGRAM_TIERS:
        raise ConfigError(
            "TELEGRAM_DEFAULT_TIER",
            f"expected one of {', '.join(TELEGRAM_TIERS)}, got {raw!r}",
        )
    return value


def _required(source: Mapping[str, str], name: str) -> str:
    value = source.get(name, "")
    if not value.strip():
        raise ConfigError(name, "missing required environment variable")
    return value.strip()


def _optional(source: Mapping[str, str], name: str) -> str | None:
    value = source.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _integer(source: Mapping[str, str], name: str, default: int) -> int:
    value = source.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value.strip())
    except ValueError:
        raise ConfigError(name, f"expected an integer, got {value!r}") from None


def _log_level(source: Mapping[str, str]) -> str:
    value = _optional(source, "LOG_LEVEL")
    level = DEFAULT_LOG_LEVEL if value is None else value.upper()
    if level not in _LOG_LEVELS:
        raise ConfigError("LOG_LEVEL", f"expected one of {sorted(_LOG_LEVELS)}, got {value!r}")
    return level
