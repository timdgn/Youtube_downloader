"""Contract tests for the Telegram webhook and the health entry shape (T026)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.s3_fake import FakeMediaStore
from tests.fakes.sqs_fake import FakeJobQueue
from tests.fakes.telegram_fake import FakeTelegramClient
from ytdl.bot.handler import Bot
from ytdl.config import load_config

SECRET = "contract-test-webhook-secret"
APPROVED = "123456789"
NOW = datetime(2026, 11, 16, 10, 30, 0, tzinfo=UTC)
VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
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
    "TELEGRAM_TEST_VIDEO_ID": "dQw4w9WgXcQ",
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
    """The access boundary, stubbed: approved, private, one secret."""

    token = "test-token"

    def __init__(self) -> None:
        self.reads = 0

    def read(self) -> None:
        self.reads += 1

    def check_secret(self, presented: str | None) -> bool:
        return presented == SECRET

    def is_approved(self, user_id: int | str) -> bool:
        return str(user_id) == APPROVED

    def is_private(self, chat_type: str | None) -> bool:
        return chat_type == "private"


def _bot(
    dynamo: FakeDynamoRepository | None = None,
    media: FakeMediaStore | None = None,
    queue: FakeJobQueue | None = None,
    telegram: FakeTelegramClient | None = None,
    *,
    policy: _Policy | None = None,
    logger: _Logger | None = None,
) -> Bot:
    return Bot(
        dynamo or FakeDynamoRepository(),
        queue or FakeJobQueue(),
        media or FakeMediaStore(),
        lambda token: telegram or FakeTelegramClient(),
        policy or _Policy(),
        load_config(dict(ENV)),
        now=lambda: NOW,
        logger=logger or _Logger(),
    )


def _event(
    body: Any = None,
    *,
    secret: str | None = SECRET,
    base64_encoded: bool = False,
) -> dict[str, Any]:
    headers = {} if secret is None else {"X-Telegram-Bot-Api-Secret-Token": secret}
    raw = json.dumps(body) if isinstance(body, dict) else body
    return {
        "version": "2.0",
        "rawPath": "/telegram/webhook",
        "headers": headers,
        "requestContext": {"http": {"method": "POST", "path": "/telegram/webhook"}},
        "body": raw,
        "isBase64Encoded": base64_encoded,
    }


def _update(
    text: str = f"{VIDEO_URL} medium",
    *,
    update_id: int = 812345678,
    chat_type: str = "private",
    sender: int = int(APPROVED),
    chat_id: int = int(APPROVED),
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 4412,
            "date": int(NOW.timestamp()),
            "chat": {"id": chat_id, "type": chat_type, "first_name": "Operator"},
            "from": {"id": sender, "is_bot": False, "first_name": "Operator"},
            "text": text,
        },
    }


def _health_lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    payloads = []
    for line in capsys.readouterr().out.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict) and "_aws" in payload:
            payloads.append(payload)
    return payloads


# --- authentication ----------------------------------------------------------


def test_a_valid_update_is_acknowledged_with_an_empty_body() -> None:
    response = _bot().handle(_event(_update()))

    assert response["statusCode"] == 200
    assert response["body"] == ""


@pytest.mark.parametrize("secret", [None, "wrong-secret", ""])
def test_a_missing_or_wrong_secret_is_refused_before_the_body_is_read(secret: str | None) -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()

    response = _bot(dynamo, media, queue, telegram).handle(_event(_update(), secret=secret))

    assert response["statusCode"] == 403
    assert dynamo.calls == []
    assert queue.messages == []
    assert telegram.calls == []


def test_the_secret_check_happens_before_a_malformed_body_is_rejected() -> None:
    response = _bot().handle(_event("{not json", secret="wrong-secret"))

    assert response["statusCode"] == 403


def test_a_base64_encoded_body_is_accepted() -> None:
    encoded = base64.b64encode(json.dumps(_update()).encode("utf-8")).decode("utf-8")

    response = _bot().handle(_event(encoded, base64_encoded=True))

    assert response["statusCode"] == 200


# --- 400 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(None, id="missing-body"),
        pytest.param("", id="blank-body"),
        pytest.param("{", id="not-json"),
        pytest.param("[]", id="not-an-object"),
        pytest.param('{"message": {}}', id="no-update-id"),
        pytest.param('{"update_id": "812"}', id="update-id-not-a-number"),
    ],
)
def test_a_body_without_a_usable_update_is_a_400(body: Any) -> None:
    dynamo, queue = FakeDynamoRepository(), FakeJobQueue()

    response = _bot(dynamo, queue=queue).handle(_event(body))

    assert response["statusCode"] == 400
    assert dynamo.calls == []
    assert queue.messages == []


# --- 500 ---------------------------------------------------------------------


def test_an_unexpected_failure_is_a_500_and_names_no_detail() -> None:
    class _Broken(FakeDynamoRepository):
        def query_owner_jobs(self, owner_key: str, limit: int) -> list[dict[str, Any]]:
            raise RuntimeError("table unavailable for 123456789")

    logger = _Logger()

    response = _bot(_Broken(), logger=logger).handle(_event(_update()))

    assert response["statusCode"] == 500
    assert response["body"] == ""
    assert any(
        event == "update_failed" and fields.get("message") == "RuntimeError"
        for event, fields in logger.calls
    )
    assert all("table unavailable" not in json.dumps(fields) for _event, fields in logger.calls)


# --- duplicate delivery ------------------------------------------------------


def test_a_duplicate_update_is_a_200_with_one_job_and_one_reply() -> None:
    dynamo, queue = FakeDynamoRepository(), FakeJobQueue()
    telegram = FakeTelegramClient()
    bot = _bot(dynamo, queue=queue, telegram=telegram)

    first = bot.handle(_event(_update()))
    second = bot.handle(_event(_update()))

    assert [first["statusCode"], second["statusCode"]] == [200, 200]
    jobs = [item for key, item in dynamo.items.items() if key.startswith("JOB#")]
    assert len(jobs) == 1
    assert len(queue.messages) == 1
    assert len(telegram.messages) == 1


# --- health ------------------------------------------------------------------


def test_health_reports_one_when_the_webhook_is_registered(
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = _bot().handle({"action": "health"})

    assert response["statusCode"] == 200
    (payload,) = _health_lines(capsys)
    assert payload["WebhookHealthy"] == 1
    assert payload["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "YTDL/Bot"
    assert payload["_aws"]["CloudWatchMetrics"][0]["Metrics"][0]["Name"] == "WebhookHealthy"
    assert payload["Environment"] == "dev"


def test_health_reports_zero_when_the_url_differs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    telegram = FakeTelegramClient(webhook_info={"url": "https://elsewhere.invalid/hook"})

    _bot(telegram=telegram).handle({"action": "health"})

    (payload,) = _health_lines(capsys)
    assert payload["WebhookHealthy"] == 0


def test_health_reports_zero_after_a_recent_delivery_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    telegram = FakeTelegramClient(
        webhook_info={
            "url": ENV["TELEGRAM_WEBHOOK_URL"],
            "pending_update_count": 3,
            "last_error_date": int(NOW.timestamp()) - 60,
            "max_connections": 5,
        }
    )

    _bot(telegram=telegram).handle({"action": "health"})

    (payload,) = _health_lines(capsys)
    assert payload["WebhookHealthy"] == 0


def test_health_ignores_an_old_error() -> None:
    telegram = FakeTelegramClient(
        webhook_info={
            "url": ENV["TELEGRAM_WEBHOOK_URL"],
            "last_error_date": int(NOW.timestamp()) - 7200,
        }
    )

    response = _bot(telegram=telegram).handle({"action": "health"})

    assert response["statusCode"] == 200
    assert telegram.calls == [("get_webhook_info", {})]


def test_health_sends_no_chat_traffic() -> None:
    telegram = FakeTelegramClient()

    _bot(telegram=telegram).handle({"action": "health"})

    assert [call[0] for call in telegram.calls] == ["get_webhook_info"]


def test_the_health_probe_also_works_when_posted_at_the_route(
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = _bot().handle(_event({"action": "health"}))

    assert response["statusCode"] == 200
    (payload,) = _health_lines(capsys)
    assert payload["WebhookHealthy"] == 1


def test_the_route_health_probe_still_needs_the_secret() -> None:
    response = _bot().handle(_event({"action": "health"}, secret="wrong"))

    assert response["statusCode"] == 403
