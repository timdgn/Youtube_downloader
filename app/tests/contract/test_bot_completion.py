"""Contract tests for the job-event consumer (T028)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.s3_fake import FakeMediaStore
from tests.fakes.sqs_fake import FakeJobQueue
from tests.fakes.telegram_fake import DROPPED, FakeTelegramClient
from ytdl import errors, keys
from ytdl.bot import messages
from ytdl.bot.client import TelegramError
from ytdl.bot.handler import Bot
from ytdl.config import load_config

SECRET = "contract-test-webhook-secret"
USER_ID = 123456789
CHAT_ID = "123456789"
NOW = datetime(2026, 11, 16, 10, 30, 0, tzinfo=UTC)
NOW_EPOCH = int(NOW.timestamp())
VIDEO_ID = "dQw4w9WgXcQ"
JOB_ID = "m3x9k2q1f7k2q1"
ENV = {
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
    "TELEGRAM_TEST_VIDEO_ID": VIDEO_ID,
    "JOB_EVENTS_TOPIC_ARN": "arn:aws:sns:eu-west-3:000000000000:ytdl-test-job-events",
    "YTDLP_VERSION": "2026.10.26",
}


class _Logger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **fields: object) -> None:
        self.calls.append((event, fields))

    def warning(self, event: str, **fields: object) -> None:
        self.calls.append((event, fields))

    def error(self, event: str, **fields: object) -> None:
        self.calls.append((event, fields))


class _Policy:
    token = "test-token"

    def read(self) -> None:
        return None

    def check_secret(self, presented: str | None) -> bool:
        return presented == SECRET

    def is_approved(self, user_id: int | str) -> bool:
        return True

    def is_private(self, chat_type: str | None) -> bool:
        return True


def _job(media: FakeMediaStore, **overrides: object) -> dict:
    job = {
        "job_id": JOB_ID,
        "state": "completed",
        "created_at": "2026-11-16T09:00:00Z",
        "updated_at": "2026-11-16T09:05:00Z",
        "source_host": "www.youtube.com",
        "video_id": VIDEO_ID,
        "owner_key": keys.owner_job_key(USER_ID),
        "chat_id": CHAT_ID,
        "update_id": 812345678,
        "requested_tier": "high",
        "requested_quality": "720p",
        "requested_height": 720,
        "media_kind": "video",
        "produced_quality": "720p",
        "produced_height": 720,
        "video_title": "Some video title",
        "media_key": keys.media_key(JOB_ID, VIDEO_ID),
        "media_size_bytes": 1_572_864,
        "media_created_at": NOW_EPOCH - 7200,
        "media_expires_at": NOW_EPOCH + 3 * 86400,
    }
    job.update(overrides)
    media.seed(job["media_key"], b"x" * 1_572_864, content_type="video/mp4")
    return job


def _bot(
    dynamo: FakeDynamoRepository,
    media: FakeMediaStore,
    telegram: FakeTelegramClient,
) -> Bot:
    return Bot(
        dynamo,
        FakeJobQueue(),
        media,
        lambda token: telegram,
        _Policy(),
        load_config(dict(ENV)),
        now=lambda: NOW,
        logger=_Logger(),
    )


def _event(payload: dict | str | None = None, **overrides: object) -> dict:
    body = payload if isinstance(payload, str) else payload
    if isinstance(payload, dict):
        body = {**payload, **overrides}
    return {"Records": [{"EventSource": "aws:sns", "Sns": {"Message": json.dumps(body)}}]}


def _completed(**overrides: object) -> dict:
    return _event({"version": 1, "event": "job_completed", "job_id": JOB_ID}, **overrides)


# --- completion --------------------------------------------------------------


def test_a_completed_job_posts_the_documented_message() -> None:
    dynamo, media, telegram = FakeDynamoRepository(), FakeMediaStore(), FakeTelegramClient()
    job = _job(media)
    dynamo.items["JOB#" + JOB_ID] = dict(job)

    response = _bot(dynamo, media, telegram).handle(_completed())

    assert response["statusCode"] == 200
    (message,) = telegram.messages
    assert message["chat_id"] == CHAT_ID
    text = message["text"]
    assert text.startswith("Ready: Some video title")
    assert "Quality: 720p" in text
    assert "Size: 1.5 MB" in text
    assert "Download (expires 2026-11-16 11:30 UTC):" in text
    assert "https://" in text
    assert "The file stays available until 2026-11-19." in text
    assert "Send the same request again for a fresh link." in text


def test_a_completed_job_presigns_with_its_display_name() -> None:
    dynamo, media, telegram = FakeDynamoRepository(), FakeMediaStore(), FakeTelegramClient()
    job = _job(media)
    dynamo.items["JOB#" + JOB_ID] = dict(job)

    _bot(dynamo, media, telegram).handle(_completed())

    (presign,) = [call for call in media.calls if call[0] == "presign_get"]
    assert presign[1]["key"] == job["media_key"]
    assert presign[1]["filename"] == keys.display_name(job)
    assert presign[1]["content_type"] == "video/mp4"
    assert presign[1]["expires_in"] == 3600


def test_an_audio_completion_uses_the_audio_content_type_and_quality() -> None:
    dynamo, media, telegram = FakeDynamoRepository(), FakeMediaStore(), FakeTelegramClient()
    job = _job(
        media,
        media_kind="audio",
        requested_tier="mp3",
        requested_quality=None,
        requested_height=None,
        produced_quality="mp3",
        produced_height=None,
        media_key=keys.media_key(JOB_ID, VIDEO_ID, extension="mp3"),
    )
    dynamo.items["JOB#" + JOB_ID] = dict(job)

    _bot(dynamo, media, telegram).handle(_completed())

    (presign,) = [call for call in media.calls if call[0] == "presign_get"]
    assert presign[1]["content_type"] == "audio/mpeg"
    assert presign[1]["filename"].endswith(".mp3")
    assert "Quality: mp3" in telegram.messages[0]["text"]


def test_a_completed_job_whose_object_is_gone_sends_the_expiry_sentence() -> None:
    dynamo, media, telegram = FakeDynamoRepository(), FakeMediaStore(), FakeTelegramClient()
    job = _job(media)
    dynamo.items["JOB#" + JOB_ID] = dict(job)
    media.objects.clear()

    _bot(dynamo, media, telegram).handle(_completed())

    assert telegram.messages == [{"chat_id": CHAT_ID, "text": messages.EXPIRED}]


def test_a_completed_job_past_retention_sends_the_expiry_sentence() -> None:
    dynamo, media, telegram = FakeDynamoRepository(), FakeMediaStore(), FakeTelegramClient()
    job = _job(media, media_expires_at=NOW_EPOCH - 1)
    dynamo.items["JOB#" + JOB_ID] = dict(job)

    _bot(dynamo, media, telegram).handle(_completed())

    assert telegram.messages == [{"chat_id": CHAT_ID, "text": messages.EXPIRED}]


def test_a_deleted_job_sends_the_expiry_sentence() -> None:
    dynamo, media, telegram = FakeDynamoRepository(), FakeMediaStore(), FakeTelegramClient()
    job = _job(media, deleted_at="2026-11-16T09:30:00Z")
    dynamo.items["JOB#" + JOB_ID] = dict(job)

    _bot(dynamo, media, telegram).handle(_completed())

    assert telegram.messages == [{"chat_id": CHAT_ID, "text": messages.EXPIRED}]


# --- failure -----------------------------------------------------------------


def test_a_retryable_failure_adds_the_next_step() -> None:
    dynamo, media, telegram = FakeDynamoRepository(), FakeMediaStore(), FakeTelegramClient()
    job = _job(
        media,
        state="failed",
        failure_category=errors.UPSTREAM_PROBLEM,
        failure_message=errors.MESSAGES[errors.UPSTREAM_PROBLEM],
        failure_retryable=True,
    )
    dynamo.items["JOB#" + JOB_ID] = dict(job)

    _bot(dynamo, media, telegram).handle(
        _event({"version": 1, "event": "job_failed", "job_id": JOB_ID})
    )

    assert telegram.messages == [
        {
            "chat_id": CHAT_ID,
            "text": f"{errors.MESSAGES[errors.UPSTREAM_PROBLEM]} Send the request again.",
        }
    ]


def test_a_terminal_failure_omits_the_next_step() -> None:
    dynamo, media, telegram = FakeDynamoRepository(), FakeMediaStore(), FakeTelegramClient()
    job = _job(
        media,
        state="failed",
        failure_category=errors.UNSUPPORTED_MEDIA,
        failure_message=errors.MESSAGES[errors.UNSUPPORTED_MEDIA],
        failure_retryable=False,
    )
    dynamo.items["JOB#" + JOB_ID] = dict(job)

    _bot(dynamo, media, telegram).handle(
        _event({"version": 1, "event": "job_failed", "job_id": JOB_ID})
    )

    assert telegram.messages == [
        {"chat_id": CHAT_ID, "text": errors.MESSAGES[errors.UNSUPPORTED_MEDIA]}
    ]


# --- acknowledged but not delivered -----------------------------------------


@pytest.mark.parametrize(
    ("payload", "job_overrides"),
    [
        pytest.param(
            {"version": 1, "event": "job_completed", "job_id": "unknown"}, {}, id="missing"
        ),
        pytest.param(
            {"version": 1, "event": "job_completed", "job_id": JOB_ID},
            {"chat_id": None},
            id="no-chat",
        ),
        pytest.param(
            {"version": 2, "event": "job_completed", "job_id": JOB_ID}, {}, id="unknown-version"
        ),
        pytest.param({"job_id": JOB_ID}, {}, id="no-version"),
        pytest.param({"version": 1, "event": "job_started", "job_id": JOB_ID}, {}, id="unknown"),
    ],
)
def test_an_event_that_cannot_be_delivered_is_acknowledged_silently(
    payload: dict, job_overrides: dict
) -> None:
    dynamo, media, telegram = FakeDynamoRepository(), FakeMediaStore(), FakeTelegramClient()
    job = _job(media, **job_overrides)
    dynamo.items["JOB#" + JOB_ID] = dict(job)

    response = _bot(dynamo, media, telegram).handle(_event(payload))

    assert response["statusCode"] == 200
    assert telegram.messages == []


def test_a_malformed_event_body_is_acknowledged() -> None:
    dynamo, media, telegram = FakeDynamoRepository(), FakeMediaStore(), FakeTelegramClient()
    event = {"Records": [{"Sns": {"Message": "{not json"}}]}

    response = _bot(dynamo, media, telegram).handle(event)

    assert response["statusCode"] == 200
    assert telegram.messages == []


# --- Telegram error policy ---------------------------------------------------


def test_a_dropped_delivery_still_returns_success() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    job = _job(media)
    dynamo.items["JOB#" + JOB_ID] = dict(job)
    telegram = FakeTelegramClient()
    telegram.fail_next(DROPPED)

    response = _bot(dynamo, media, telegram).handle(_completed())

    assert response["statusCode"] == 200
    assert telegram.messages == []


@pytest.mark.parametrize("status", [401, 429, 500])
def test_a_retryable_telegram_failure_raises_so_sns_retries(status: int) -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    job = _job(media)
    dynamo.items["JOB#" + JOB_ID] = dict(job)
    telegram = FakeTelegramClient()
    telegram.fail_next(TelegramError(f"telegram answered {status}"))

    with pytest.raises(TelegramError):
        _bot(dynamo, media, telegram).handle(_completed())


def test_a_network_failure_raises() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    job = _job(media)
    dynamo.items["JOB#" + JOB_ID] = dict(job)
    telegram = FakeTelegramClient()
    telegram.fail_next(TelegramError("telegram sendMessage failed: network error"))

    with pytest.raises(TelegramError):
        _bot(dynamo, media, telegram).handle(_completed())
