"""Contract tests for /start, /help, /info, /history and /test (T056)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.s3_fake import FakeMediaStore
from tests.fakes.sqs_fake import FakeJobQueue
from tests.fakes.telegram_fake import FakeTelegramClient
from ytdl import keys
from ytdl.bot import messages
from ytdl.bot.handler import Bot
from ytdl.config import load_config

SECRET = "contract-test-webhook-secret"
USER_ID = 123456789
CHAT_ID = 123456789
NOW = datetime(2026, 11, 16, 10, 30, 0, tzinfo=UTC)
NOW_EPOCH = int(NOW.timestamp())
VIDEO_ID = "dQw4w9WgXcQ"
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
        return str(user_id) == str(USER_ID)

    def is_private(self, chat_type: str | None) -> bool:
        return chat_type == "private"


def _bot(
    dynamo: FakeDynamoRepository,
    media: FakeMediaStore,
    queue: FakeJobQueue,
    telegram: FakeTelegramClient,
) -> Bot:
    return Bot(
        dynamo,
        queue,
        media,
        lambda token: telegram,
        _Policy(),
        load_config(dict(ENV)),
        now=lambda: NOW,
        logger=_Logger(),
    )


def _event(text: str, update_id: int = 812345678) -> dict:
    return {
        "version": "2.0",
        "rawPath": "/telegram/webhook",
        "headers": {"x-telegram-bot-api-secret-token": SECRET},
        "requestContext": {"http": {"method": "POST", "path": "/telegram/webhook"}},
        "body": json.dumps(
            {
                "update_id": update_id,
                "message": {
                    "message_id": 4412,
                    "date": NOW_EPOCH,
                    "chat": {"id": CHAT_ID, "type": "private", "first_name": "Operator"},
                    "from": {"id": USER_ID, "is_bot": False, "first_name": "Operator"},
                    "text": text,
                },
            }
        ),
        "isBase64Encoded": False,
    }


def _interactions(dynamo: FakeDynamoRepository) -> list[dict]:
    return [item for key, item in dynamo.items.items() if key.startswith("UPD#")]


def _jobs(dynamo: FakeDynamoRepository) -> list[dict]:
    return [item for key, item in dynamo.items.items() if key.startswith("JOB#")]


# --- /start, /help, /info ----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected", "outcome"),
    [
        pytest.param("/start", messages.START, "command:start", id="start"),
        pytest.param("/help", messages.HELP, "command:help", id="help"),
    ],
)
def test_a_simple_command_sends_one_reply_and_one_interaction(
    text: str, expected: str, outcome: str
) -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()

    _bot(dynamo, media, queue, telegram).handle(_event(text))

    assert telegram.messages == [{"chat_id": CHAT_ID, "text": expected}]
    (interaction,) = _interactions(dynamo)
    assert interaction["outcome"] == outcome


def test_info_reports_the_running_version_and_the_four_limits() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()

    _bot(dynamo, media, queue, telegram).handle(_event("/info"))

    text = telegram.messages[0]["text"]
    assert text == (
        "yt-dlp 2026.10.26\n"
        "Quality ceiling: 1080p\n"
        "Retention: 7 days\n"
        "Link lifetime: 60 minutes\n"
        "Rate limit: 10 requests per hour"
    )
    (interaction,) = _interactions(dynamo)
    assert interaction["outcome"] == "command:info"


def test_an_unknown_command_gets_the_format_hint_not_the_help_text() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()

    _bot(dynamo, media, queue, telegram).handle(_event("/foo"))

    assert telegram.messages == [{"chat_id": CHAT_ID, "text": messages.FORMAT_HINT}]
    assert telegram.messages[0]["text"] != messages.HELP


# --- /history ----------------------------------------------------------------


def _seed_interaction(
    dynamo: FakeDynamoRepository,
    update_id: int,
    created_at: str,
    text: str,
    outcome: str,
    job_id: str | None = None,
) -> None:
    item = {
        "pk": keys.interaction_pk(update_id),
        "owner_key": keys.owner_interaction_key(USER_ID),
        "created_at": created_at,
        "kind": "interaction",
        "text": text,
        "outcome": outcome,
        "ttl": NOW_EPOCH + 86400,
    }
    if job_id is not None:
        item["job_id"] = job_id
    dynamo.items[item["pk"]] = item


def test_history_with_no_items_says_so() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()

    _bot(dynamo, media, queue, telegram).handle(_event("/history"))

    assert telegram.messages == [{"chat_id": CHAT_ID, "text": messages.NO_HISTORY}]


def test_history_renders_ten_lines_newest_first_without_a_token_or_a_link() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()
    for index in range(12):
        _seed_interaction(
            dynamo,
            update_id=index,
            created_at=f"2026-11-16T09:{index:02d}:00Z",
            text=f"{VIDEO_ID} medium",
            outcome="accepted",
        )

    _bot(dynamo, media, queue, telegram).handle(_event("/history"))

    lines = telegram.messages[0]["text"].splitlines()
    assert len(lines) == 10
    assert lines[0] == f"2026-11-16 09:11 UTC | {VIDEO_ID} medium | accepted"
    assert lines[-1] == f"2026-11-16 09:02 UTC | {VIDEO_ID} medium | accepted"
    for line in lines:
        assert "@" not in line
        assert "http" not in line
        assert "?" not in line


def test_history_shows_the_jobs_current_state_when_it_has_moved_on() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()
    _seed_interaction(
        dynamo,
        update_id=1,
        created_at="2026-11-16T09:00:00Z",
        text=f"{VIDEO_ID} medium",
        outcome="accepted",
        job_id="m3x9k2q1f7abc",
    )
    dynamo.items["JOB#m3x9k2q1f7abc"] = {
        "pk": "JOB#m3x9k2q1f7abc",
        "job_id": "m3x9k2q1f7abc",
        "state": "completed",
    }

    _bot(dynamo, media, queue, telegram).handle(_event("/history"))

    assert telegram.messages[0]["text"].endswith("| completed")


# --- /test -------------------------------------------------------------------


def test_test_starts_one_low_job_and_names_it() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()

    response = _bot(dynamo, media, queue, telegram).handle(_event("/test"))

    assert response["statusCode"] == 200
    assert telegram.messages == [{"chat_id": CHAT_ID, "text": messages.TEST_STARTED}]
    (job,) = _jobs(dynamo)
    assert job["video_id"] == VIDEO_ID
    assert job["requested_tier"] == "low"
    assert job["requested_height"] == 240
    assert job["is_test"] is True
    assert job["media_kind"] == "video"
    assert len(queue.messages) == 1

    rate_pk = keys.rate_key(keys.rate_window(NOW_EPOCH), keys.owner_job_key(USER_ID))
    assert dynamo.rate_counts == {rate_pk: 1}


def test_test_bypasses_the_reuse_lookup() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()
    dynamo.items["JOB#stored00000001"] = {
        "pk": "JOB#stored00000001",
        "job_id": "stored00000001",
        "state": "completed",
        "video_id": VIDEO_ID,
        "owner_key": keys.owner_job_key(USER_ID),
        "requested_tier": "low",
        "media_kind": "video",
        "produced_quality": "240p",
        "media_key": keys.media_key("stored00000001", VIDEO_ID),
        "media_created_at": NOW_EPOCH - 100,
        "media_expires_at": NOW_EPOCH + 86400,
    }
    media.seed(keys.media_key("stored00000001", VIDEO_ID), b"x")

    _bot(dynamo, media, queue, telegram).handle(_event("/test"))

    assert telegram.messages[0]["text"] == messages.TEST_STARTED
    assert len(_jobs(dynamo)) == 2
    assert len(queue.messages) == 1


def test_test_at_the_rate_limit_creates_nothing() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()
    rate_pk = keys.rate_key(keys.rate_window(NOW_EPOCH), keys.owner_job_key(USER_ID))
    dynamo.rate_counts[rate_pk] = 10

    _bot(dynamo, media, queue, telegram).handle(_event("/test"))

    assert _jobs(dynamo) == []
    assert queue.messages == []
    assert telegram.messages[0]["text"] == messages.RATE_LIMITED.format(
        retry_at=messages.format_minute(keys.rate_window(NOW_EPOCH) + 3600)
    )


def test_empty_confirm_is_not_shadowed_by_command_dispatch() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()

    _bot(dynamo, media, queue, telegram).handle(_event("/empty confirm"))

    assert telegram.messages[0]["text"] == messages.EMPTY_NOTHING
