"""Contract tests for both refusals (T045)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.s3_fake import FakeMediaStore
from tests.fakes.sqs_fake import FakeJobQueue
from tests.fakes.ssm_fake import FakeSsmClient
from tests.fakes.telegram_fake import FakeTelegramClient
from ytdl import keys
from ytdl.bot import messages
from ytdl.bot.access import AllowlistPolicy
from ytdl.bot.handler import Bot
from ytdl.config import load_config

TOKEN_PARAM = "/ytdl/test/telegram-bot-token"
SECRET_PARAM = "/ytdl/test/telegram-webhook-secret"
APPROVED_PARAM = "/ytdl/test/telegram-approved-user-ids"
TOKEN = "123456789:AA-token-value"
SECRET = "contract-test-webhook-secret"
APPROVED = "123456789"
STRANGER = 987654321
NOW = datetime(2026, 11, 16, 10, 30, 0, tzinfo=UTC)
VIDEO_ID = "dQw4w9WgXcQ"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
ENV = {
    "ENVIRONMENT": "dev",
    "JOBS_TABLE": "ytdl-test-jobs",
    "MEDIA_BUCKET": "ytdl-test-media",
    "JOBS_QUEUE_URL": "https://sqs.eu-west-3.amazonaws.com/000000000000/ytdl-test-jobs",
    "TOKEN_PARAM_NAME": "/ytdl/test/api-token",
    "TELEGRAM_TOKEN_PARAM": TOKEN_PARAM,
    "TELEGRAM_WEBHOOK_SECRET_PARAM": SECRET_PARAM,
    "TELEGRAM_APPROVED_PARAM": APPROVED_PARAM,
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


def _environment(
    approved: str = APPROVED,
) -> tuple[Bot, FakeSsmClient, FakeDynamoRepository, FakeJobQueue, FakeTelegramClient]:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()
    ssm = FakeSsmClient({TOKEN_PARAM: TOKEN, SECRET_PARAM: SECRET, APPROVED_PARAM: approved})
    config = load_config(dict(ENV))
    policy = AllowlistPolicy(ssm, TOKEN_PARAM, SECRET_PARAM, APPROVED_PARAM)
    bot = Bot(
        dynamo,
        queue,
        media,
        lambda token: telegram,
        policy,
        config,
        now=lambda: NOW,
        logger=_Logger(),
    )
    return bot, ssm, dynamo, queue, telegram


def _event(
    text: str,
    *,
    sender: int = STRANGER,
    chat_type: str = "private",
    update_id: int = 812345678,
) -> dict:
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
                    "date": int(NOW.timestamp()),
                    "chat": {"id": sender, "type": chat_type, "first_name": "Someone"},
                    "from": {"id": sender, "is_bot": False, "first_name": "Someone"},
                    "text": text,
                },
            }
        ),
        "isBase64Encoded": False,
    }


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(f"{VIDEO_URL} medium", id="request"),
        pytest.param("/start", id="start"),
        pytest.param("/help", id="help"),
        pytest.param("/list", id="list"),
        pytest.param("/test", id="test"),
    ],
)
def test_an_unapproved_account_learns_nothing(text: str) -> None:
    bot, _ssm, dynamo, queue, telegram = _environment()

    response = bot.handle(_event(text))

    assert response["statusCode"] == 200
    assert telegram.messages == [{"chat_id": STRANGER, "text": messages.NOT_APPROVED}]
    assert dynamo.items == {}
    assert dynamo.rate_counts == {}
    assert queue.messages == []
    assert dynamo.writes == []


def test_the_refusal_never_names_a_command_or_a_download() -> None:
    bot, _ssm, _dynamo, _queue, telegram = _environment()

    bot.handle(_event(f"{VIDEO_URL} medium"))

    text = telegram.messages[0]["text"]
    assert text == "This bot is private."
    for hint in ("/", "download", "video", "help", "link"):
        assert hint not in text


def test_one_get_parameters_call_per_message() -> None:
    bot, ssm, _dynamo, _queue, _telegram = _environment()

    bot.handle(_event(f"{VIDEO_URL} medium"))
    bot.handle(_event("/help", sender=int(APPROVED)))

    assert [call[0] for call in ssm.calls] == ["get_parameters", "get_parameters"]


def test_a_change_to_the_allowlist_changes_the_next_answer() -> None:
    bot, ssm, dynamo, queue, telegram = _environment(approved="")
    bot.handle(_event(f"{VIDEO_URL} medium", update_id=1))
    assert telegram.messages[-1]["text"] == messages.NOT_APPROVED

    ssm.parameters[APPROVED_PARAM] = str(STRANGER)
    bot.handle(_event(f"{VIDEO_URL} medium", update_id=2))

    assert telegram.messages[-1]["text"].startswith("Accepted:")
    assert len(queue.messages) == 1
    assert len([key for key in dynamo.items if key.startswith("JOB#")]) == 1


@pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
def test_an_approved_account_in_a_group_is_refused(chat_type: str) -> None:
    bot, _ssm, dynamo, queue, telegram = _environment()

    response = bot.handle(_event(f"{VIDEO_URL} medium", sender=int(APPROVED), chat_type=chat_type))

    assert response["statusCode"] == 200
    assert telegram.messages == [{"chat_id": int(APPROVED), "text": messages.GROUP_CHAT}]
    assert dynamo.items == {}
    assert dynamo.rate_counts == {}
    assert queue.messages == []
    assert dynamo.writes == []


def test_the_allowlist_is_checked_before_the_chat_type() -> None:
    bot, _ssm, _dynamo, _queue, telegram = _environment(approved="")

    bot.handle(_event(f"{VIDEO_URL} medium", chat_type="group"))

    assert telegram.messages == [{"chat_id": STRANGER, "text": messages.NOT_APPROVED}]


def test_an_approved_account_is_served() -> None:
    bot, _ssm, dynamo, queue, telegram = _environment()

    bot.handle(_event(f"{VIDEO_URL} medium", sender=int(APPROVED)))

    assert telegram.messages[0]["text"].startswith("Accepted:")
    assert len(queue.messages) == 1
    job = next(item for key, item in dynamo.items.items() if key.startswith("JOB#"))
    assert job["owner_key"] == keys.owner_job_key(APPROVED)


def test_a_removed_account_sees_none_of_its_earlier_items() -> None:
    bot, ssm, dynamo, _queue, telegram = _environment()
    dynamo.items["JOB#stored00000001"] = {
        "pk": "JOB#stored00000001",
        "job_id": "stored00000001",
        "state": "completed",
        "video_id": VIDEO_ID,
        "owner_key": keys.owner_job_key(STRANGER),
        "requested_tier": "high",
        "media_kind": "video",
        "produced_quality": "720p",
        "video_title": "A stored video",
        "media_created_at": int(NOW.timestamp()) - 60,
        "media_expires_at": int(NOW.timestamp()) + 86400,
    }
    ssm.parameters[APPROVED_PARAM] = ""

    bot.handle(_event("/list"))

    assert telegram.messages == [{"chat_id": STRANGER, "text": messages.NOT_APPROVED}]
    assert "A stored video" not in telegram.messages[0]["text"]
