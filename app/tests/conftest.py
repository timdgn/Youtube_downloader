"""Shared fixtures: one fake per adapter boundary."""

from __future__ import annotations

import pytest

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.extractor_fake import FakeExtractor
from tests.fakes.s3_fake import FakeMediaStore
from tests.fakes.sqs_fake import FakeJobQueue
from tests.fakes.ssm_fake import FakeSsmClient
from tests.fakes.telegram_fake import FakeTelegramClient

BOT_TOKEN = "123456789:AA-contrat-test-token"
WEBHOOK_SECRET = "contract-test-webhook-secret"
APPROVED_IDS = "123456789, 987654321"


@pytest.fixture
def dynamo() -> FakeDynamoRepository:
    return FakeDynamoRepository()


@pytest.fixture
def media() -> FakeMediaStore:
    return FakeMediaStore()


@pytest.fixture
def queue() -> FakeJobQueue:
    return FakeJobQueue()


@pytest.fixture
def ssm() -> FakeSsmClient:
    return FakeSsmClient()


@pytest.fixture
def extractor() -> FakeExtractor:
    return FakeExtractor()


@pytest.fixture
def telegram() -> FakeTelegramClient:
    return FakeTelegramClient()


@pytest.fixture
def bot_env() -> dict[str, str]:
    """The bot environment of data-model.md "Bot Configuration", with no secrets."""
    return {
        "ENVIRONMENT": "dev",
        "JOBS_TABLE": "ytdl-test-jobs",
        "MEDIA_BUCKET": "ytdl-test-media",
        "JOBS_QUEUE_URL": "https://sqs.eu-west-3.amazonaws.com/000000000000/ytdl-test-jobs",
        "TOKEN_PARAM_NAME": "/ytdl/test/api-token",
        "TELEGRAM_TOKEN_PARAM": "/ytdl/test/telegram-bot-token",
        "TELEGRAM_WEBHOOK_SECRET_PARAM": "/ytdl/test/telegram-webhook-secret",
        "TELEGRAM_APPROVED_PARAM": "/ytdl/test/telegram-approved-user-ids",
        "TELEGRAM_WEBHOOK_URL": "https://api.example.invalid/telegram/webhook",
        "TELEGRAM_DEFAULT_TIER": "high",
        "TELEGRAM_TEST_VIDEO_ID": "dQw4w9WgXcQ",
        "JOB_EVENTS_TOPIC_ARN": "arn:aws:sns:eu-west-3:000000000000:ytdl-test-job-events",
        "YTDLP_VERSION": "2026.10.26",
        "MAX_HEIGHT": "1080",
        "MAX_DURATION_SECONDS": "3600",
        "RETENTION_DAYS": "7",
        "LINK_TTL_SECONDS": "3600",
        "RATE_LIMIT_PER_HOUR": "10",
        "LOG_LEVEL": "INFO",
    }
