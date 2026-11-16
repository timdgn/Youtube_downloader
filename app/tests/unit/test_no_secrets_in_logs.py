"""SC-009: no credential, header or full URL can reach a log line (T095)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.s3_fake import FakeMediaStore
from tests.fakes.sqs_fake import FakeJobQueue
from tests.fakes.telegram_fake import FakeTelegramClient
from ytdl.api.routes import MESSAGES as API_MESSAGES
from ytdl.bot.handler import Bot
from ytdl.config import load_config
from ytdl.errors import MESSAGES as FAILURE_MESSAGES
from ytdl.logging import ALLOWED_FIELDS, get_logger

_SECRET = "s3cr3t-bearer-value"
_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLsecret&si=tracking"


def _emit(capsys: pytest.CaptureFixture[str], **fields: object) -> str:
    get_logger("api").info("request", **fields)
    return capsys.readouterr().out


def test_disallowed_field_names_are_dropped(capsys: pytest.CaptureFixture[str]) -> None:
    output = _emit(
        capsys,
        job_id="m3x9k2q1f7",
        authorization=f"Bearer {_SECRET}",
        token=_SECRET,
        url=_URL,
        headers={"authorization": f"Bearer {_SECRET}"},
    )

    assert _SECRET not in output
    assert "authorization" not in output
    assert "headers" not in output
    assert "youtube.com" not in output
    assert "m3x9k2q1f7" in output


def test_one_json_object_per_line(capsys: pytest.CaptureFixture[str]) -> None:
    output = _emit(capsys, job_id="m3x9k2q1f7")

    lines = [line for line in output.splitlines() if line.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["job_id"] == "m3x9k2q1f7"
    assert set(parsed) <= (
        set(ALLOWED_FIELDS) | {"level", "event", "function", "environment", "timestamp"}
    )


def test_allowlist_covers_the_audit_fields_required_by_fr_015() -> None:
    required = {
        "job_id",
        "source_host",
        "video_id",
        "requested_quality",
        "produced_quality",
        "outcome",
        "processing_ms",
        "media_size_bytes",
        "attempt",
    }
    assert required <= set(ALLOWED_FIELDS)


@pytest.mark.parametrize(
    "message",
    [*API_MESSAGES.values(), *FAILURE_MESSAGES.values()],
)
def test_fixed_messages_carry_no_url_or_line_break(message: str) -> None:
    assert message.strip() == message
    assert "\n" not in message
    assert "\r" not in message
    assert "http" not in message.lower()
    assert "www." not in message
    assert "\u2014" not in message


# --- the bot (T065) ----------------------------------------------------------

BOT_SECRET = "webhook-secret-value"
BOT_TOKEN = "123456789:AA-bot-token-value"
BOT_ACCOUNT = 424242424
BOT_NOW = datetime(2026, 11, 16, 10, 30, 0, tzinfo=UTC)
BOT_ENV = {
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
}
TRACKED_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=tracking-secret"


class _BotPolicy:
    token = BOT_TOKEN

    def read(self) -> None:
        return None

    def check_secret(self, presented: str | None) -> bool:
        return presented == BOT_SECRET

    def is_approved(self, user_id: int | str) -> bool:
        return str(user_id) == str(BOT_ACCOUNT)

    def is_private(self, chat_type: str | None) -> bool:
        return chat_type == "private"


def _bot(repo: object | None = None) -> Bot:
    return Bot(
        repo or FakeDynamoRepository(),
        FakeJobQueue(),
        FakeMediaStore(),
        lambda token: FakeTelegramClient(),
        _BotPolicy(),
        load_config(dict(BOT_ENV)),
        now=lambda: BOT_NOW,
        logger=get_logger("bot"),
    )


def _bot_event(text: str, update_id: int = 812345678) -> dict:
    return {
        "version": "2.0",
        "rawPath": "/telegram/webhook",
        "headers": {"x-telegram-bot-api-secret-token": BOT_SECRET},
        "requestContext": {"http": {"method": "POST", "path": "/telegram/webhook"}},
        "body": json.dumps(
            {
                "update_id": update_id,
                "message": {
                    "message_id": 4412,
                    "date": int(BOT_NOW.timestamp()),
                    "chat": {"id": BOT_ACCOUNT, "type": "private"},
                    "from": {"id": BOT_ACCOUNT, "is_bot": False},
                    "text": text,
                },
            }
        ),
        "isBase64Encoded": False,
    }


def _bot_lines(output: str) -> list[dict]:
    return [
        json.loads(line) for line in output.splitlines() if line.strip() and line.startswith("{")
    ]


def _assert_nothing_identifying(output: str) -> None:
    assert BOT_TOKEN not in output
    assert BOT_SECRET not in output
    assert str(BOT_ACCOUNT) not in output
    assert "si=tracking-secret" not in output
    assert "youtube.com" not in output
    assert "https://" not in output


def test_a_handled_request_logs_only_the_update_the_outcome_and_a_duration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _bot().handle(_bot_event(f"{TRACKED_URL} medium"))
    output = capsys.readouterr().out
    (line,) = _bot_lines(output)

    assert line["update_id"] == 812345678
    assert line["outcome"] == "accepted"
    assert line["duration_ms"] >= 0
    assert set(line) <= (
        set(ALLOWED_FIELDS) | {"level", "event", "function", "environment", "timestamp"}
    )
    _assert_nothing_identifying(output)


def test_a_refusal_logs_the_same_fields(capsys: pytest.CaptureFixture[str]) -> None:
    _bot().handle(_bot_event("just some words"))
    output = capsys.readouterr().out
    (line,) = _bot_lines(output)

    assert line["outcome"] == "format_hint"
    assert line["update_id"] == 812345678
    _assert_nothing_identifying(output)


def test_a_failed_update_logs_the_exception_class_name_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Broken(FakeDynamoRepository):
        def query_owner_jobs(self, owner_key: str, limit: int) -> list[dict]:
            raise RuntimeError(f"table unreachable for account {BOT_ACCOUNT}")

    _bot(_Broken()).handle(_bot_event(f"{TRACKED_URL} medium"))
    output = capsys.readouterr().out
    lines = _bot_lines(output)

    assert any(
        line.get("event") == "update_failed" and line.get("message") == "RuntimeError"
        for line in lines
    )
    assert "table unreachable" not in output
    _assert_nothing_identifying(output)
