"""Unit tests for the Docker HTTP front end: translation, auth, sizes."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.client import HTTPConnection
from typing import Any

import pytest

from ytdl.server import web

TOKEN = "test-api-token"
JOB_PATH = "/v1/jobs/m3x9k2q1f7"


class FakeTokens:
    def __init__(self, value: str = TOKEN) -> None:
        self.value = value

    def get(self) -> str:
        return self.value


class RecordingHandler:
    """Stands in for the Api or the Bot and keeps what it was given."""

    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.events: list[dict[str, Any]] = []
        self.response = response or {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json; charset=utf-8"},
            "body": '{"ok":true}',
        }

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        self.events.append(event)
        return self.response


@pytest.fixture
def served() -> Iterator[tuple[int, RecordingHandler, RecordingHandler]]:
    api = RecordingHandler()
    bot = RecordingHandler({"statusCode": 200, "headers": {}, "body": ""})
    server = web._Server(("127.0.0.1", 0), web.Gateway(api, bot, FakeTokens()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], api, bot
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def call(
    port: int,
    method: str,
    path: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    sent = dict(headers or {})
    if body is not None:
        sent.setdefault("Content-Type", "application/json")
    connection.request(method, path, body=body, headers=sent)
    response = connection.getresponse()
    payload = response.read()
    received = {name.lower(): value for name, value in response.getheaders()}
    connection.close()
    return response.status, received, payload


def test_health_is_answered_without_calling_any_handler(
    served: tuple[int, RecordingHandler, RecordingHandler],
) -> None:
    port, api, bot = served

    status, _, body = call(port, "GET", "/healthz")

    assert status == 200
    assert json.loads(body) == {"status": "ok"}
    assert api.events == [] and bot.events == []


def test_a_job_route_reaches_the_api_as_an_api_gateway_event(
    served: tuple[int, RecordingHandler, RecordingHandler],
) -> None:
    port, api, _ = served

    status, headers, body = call(
        port,
        "GET",
        JOB_PATH,
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert status == 200
    assert body == b'{"ok":true}'
    assert headers["content-type"] == "application/json; charset=utf-8"
    event = api.events[0]
    assert event["rawPath"] == JOB_PATH
    assert event["requestContext"]["http"]["method"] == "GET"
    assert event["headers"]["authorization"] == f"Bearer {TOKEN}"
    assert event["body"] is None
    assert event["isBase64Encoded"] is False


def test_a_posted_body_arrives_as_text(
    served: tuple[int, RecordingHandler, RecordingHandler],
) -> None:
    port, api, _ = served
    body = b'{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'

    status, _, _ = call(port, "POST", "/v1/jobs", body=body)

    assert status == 200
    assert api.events[0]["body"] == body.decode()
    assert api.events[0]["requestContext"]["http"]["method"] == "POST"


def test_the_webhook_reaches_the_bot_with_its_secret_header(
    served: tuple[int, RecordingHandler, RecordingHandler],
) -> None:
    port, _, bot = served
    body = b'{"update_id":42}'

    call(
        port,
        "POST",
        "/telegram/webhook",
        body=body,
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cr3t"},
    )

    assert bot.events[0]["headers"]["x-telegram-bot-api-secret-token"] == "s3cr3t"
    assert bot.events[0]["body"] == body.decode()


def test_the_internal_event_refuses_a_missing_or_wrong_token(
    served: tuple[int, RecordingHandler, RecordingHandler],
) -> None:
    port, _, bot = served
    payload = b'{"event":"job_completed","job_id":"abc"}'

    missing, _, _ = call(port, "POST", "/internal/job-event", body=payload)
    wrong, _, _ = call(
        port,
        "POST",
        "/internal/job-event",
        body=payload,
        headers={"Authorization": "Bearer nope"},
    )

    assert missing == 401
    assert wrong == 401
    assert bot.events == []


def test_the_internal_event_is_delivered_as_an_sns_record(
    served: tuple[int, RecordingHandler, RecordingHandler],
) -> None:
    port, _, bot = served
    payload = b'{"version":1,"event":"job_failed","job_id":"abc"}'

    status, _, _ = call(
        port,
        "POST",
        "/internal/job-event",
        body=payload,
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert status == 202
    record = bot.events[0]["Records"][0]["Sns"]["Message"]
    assert json.loads(record) == {"version": 1, "event": "job_failed", "job_id": "abc"}


def test_an_unknown_route_is_a_json_404(
    served: tuple[int, RecordingHandler, RecordingHandler],
) -> None:
    port, api, bot = served

    status, headers, body = call(port, "GET", "/nope")

    assert status == 404
    assert headers["content-type"] == web._JSON
    assert json.loads(body)["error"]["code"] == "not_found"
    assert api.events == [] and bot.events == []


def test_a_body_over_the_limit_is_refused_before_any_handler(
    served: tuple[int, RecordingHandler, RecordingHandler],
) -> None:
    port, api, _ = served

    # The declared length is refused without reading a body, so the client must
    # not be sending one: a real 1 MiB write would race the connection close.
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    connection.putrequest("POST", "/v1/jobs")
    connection.putheader("Content-Length", str(web.MAX_BODY_BYTES + 1))
    connection.endheaders()
    response = connection.getresponse()
    status = response.status
    response.read()
    connection.close()

    assert status == 413
    assert api.events == []


def test_invalid_utf8_is_a_bad_request(
    served: tuple[int, RecordingHandler, RecordingHandler],
) -> None:
    port, api, _ = served

    status, _, body = call(port, "POST", "/v1/jobs", body=b"\xff\xfe")

    assert status == 400
    assert json.loads(body)["error"]["code"] == "invalid_input"
    assert api.events == []
