"""Unit tests for the stdlib Telegram client (T025; contracts/telegram-webhook.md)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from ytdl.bot.client import TelegramClient, TelegramError
from ytdl.bot.messages import paginate

TOKEN = "123456:test-token-value"
CHAT_ID = 42

WEBHOOK_INFO = {
    "url": "https://example.execute-api.eu-west-3.amazonaws.com/telegram/webhook",
    "pending_update_count": 3,
    "max_connections": 5,
    "allowed_updates": ["message"],
    "last_error_date": 1763294591,
    "last_error_message": "Wrong response from the webhook: 500 Internal Server Error",
}


class FakeResponse:
    """The response surface the client is allowed to read."""

    def __init__(self, status: int = 200, body: dict | None = None) -> None:
        self.status = status
        default = {"ok": True, "result": {"message_id": 1}}
        self._body = json.dumps(default if body is None else body).encode("utf-8")

    def read(self) -> bytes:
        return self._body


class RecordingOpener:
    """A transport double that records each request and replays one outcome per call."""

    def __init__(self, *outcomes: FakeResponse | Exception) -> None:
        self.requests: list[urllib.request.Request] = []
        self.timeouts: list[float | None] = []
        self._outcomes = list(outcomes)

    def __call__(self, request: urllib.request.Request, timeout: float | None = None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def body_of(request: urllib.request.Request) -> dict:
    return json.loads(request.data.decode("utf-8"))


def test_send_message_posts_plain_json_to_the_documented_url() -> None:
    opener = RecordingOpener(FakeResponse())
    client = TelegramClient(TOKEN, opener=opener)

    client.send_message(CHAT_ID, "hello")

    assert len(opener.requests) == 1
    request = opener.requests[0]
    assert request.full_url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert body_of(request) == {"chat_id": CHAT_ID, "text": "hello"}
    assert opener.timeouts == [10]


@pytest.mark.parametrize("status", [400, 403])
def test_a_bad_request_or_a_blocked_bot_returns_without_raising(status: int) -> None:
    opener = RecordingOpener(FakeResponse(status=status, body={"ok": False}))
    client = TelegramClient(TOKEN, opener=opener)

    assert client.send_message(CHAT_ID, "hello") is None
    assert len(opener.requests) == 1


@pytest.mark.parametrize("status", [401, 429, 500, 502, 503])
def test_unauthorized_throttled_and_server_errors_raise(status: int) -> None:
    opener = RecordingOpener(FakeResponse(status=status))
    client = TelegramClient(TOKEN, opener=opener)

    with pytest.raises(TelegramError):
        client.send_message(CHAT_ID, "hello")

    assert len(opener.requests) == 1


@pytest.mark.parametrize(
    "outcome",
    [
        urllib.error.URLError(OSError("connection refused")),
        TimeoutError("timed out"),
        OSError("network down"),
    ],
)
def test_a_network_failure_raises(outcome: Exception) -> None:
    opener = RecordingOpener(outcome)
    client = TelegramClient(TOKEN, opener=opener)

    with pytest.raises(TelegramError):
        client.send_message(CHAT_ID, "hello")

    assert len(opener.requests) == 1


@pytest.mark.parametrize(
    "outcome",
    [
        FakeResponse(status=401),
        urllib.error.URLError(OSError("connection refused")),
    ],
)
def test_no_exception_message_contains_the_token(outcome: FakeResponse | Exception) -> None:
    opener = RecordingOpener(outcome)
    client = TelegramClient(TOKEN, opener=opener)

    with pytest.raises(TelegramError) as raised:
        client.send_message(CHAT_ID, "hello")

    assert TOKEN not in str(raised.value)


def test_a_long_text_is_sent_as_several_pages_in_order() -> None:
    text = "\n".join(["a" * 2000, "b" * 2000, "c" * 2000])
    pages = paginate(text)
    assert len(pages) == 3

    opener = RecordingOpener(*(FakeResponse() for _ in pages))
    client = TelegramClient(TOKEN, opener=opener)

    client.send_message(CHAT_ID, text)

    assert len(opener.requests) == len(pages)
    assert [body_of(request)["text"] for request in opener.requests] == pages
    assert "\n".join(body_of(request)["text"] for request in opener.requests) == text


def test_get_webhook_info_reads_the_documented_result() -> None:
    opener = RecordingOpener(FakeResponse(body={"ok": True, "result": WEBHOOK_INFO}))
    client = TelegramClient(TOKEN, opener=opener)

    info = client.get_webhook_info()

    assert info == WEBHOOK_INFO
    assert info["url"] == WEBHOOK_INFO["url"]
    assert info["pending_update_count"] == 3
    assert info["max_connections"] == 5
    assert info["allowed_updates"] == ["message"]
    assert info["last_error_date"] == 1763294591
    assert info["last_error_message"] == WEBHOOK_INFO["last_error_message"]
    assert opener.requests[0].full_url == f"https://api.telegram.org/bot{TOKEN}/getWebhookInfo"
    assert body_of(opener.requests[0]) == {}
    assert opener.timeouts == [10]


def test_get_webhook_info_raises_on_an_unauthorized_answer() -> None:
    opener = RecordingOpener(FakeResponse(status=401))
    client = TelegramClient(TOKEN, opener=opener)

    with pytest.raises(TelegramError):
        client.get_webhook_info()
