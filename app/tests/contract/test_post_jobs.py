"""Contract tests for POST /v1/jobs (T044)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import pytest

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.s3_fake import FakeMediaStore
from tests.fakes.sqs_fake import FakeJobQueue
from tests.fakes.ssm_fake import FakeSsmClient
from ytdl import keys
from ytdl.api.handler import Api
from ytdl.auth import TokenProvider
from ytdl.config import load_config
from ytdl.logging import ALLOWED_FIELDS

TOKEN = "contract-test-token"
NOW = datetime(2026, 11, 15, 14, 3, 11, tzinfo=UTC)
NOW_EPOCH = int(NOW.timestamp())
CREATED_AT = "2026-11-15T14:03:11Z"
VIDEO_ID = "dQw4w9WgXcQ"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"

CONFIG_ENV = {
    "ENVIRONMENT": "dev",
    "JOBS_TABLE": "ytdl-test-jobs",
    "MEDIA_BUCKET": "ytdl-test-media",
    "JOBS_QUEUE_URL": "https://sqs.eu-west-1.amazonaws.com/000000000000/ytdl-test-jobs",
    "TOKEN_PARAM_NAME": "/ytdl/test/api-token",
    "RATE_LIMIT_PER_HOUR": "10",
}

BAD_URLS = [
    pytest.param("https://vimeo.com/123456789", id="foreign-host"),
    pytest.param("https://www.youtube.com/playlist?list=PL1234567890", id="playlist"),
    pytest.param("https://www.youtube.com/live/dQw4w9WgXcQ", id="live"),
    pytest.param("https://www.youtube.com/watch?v=tooshort", id="bad-id"),
    pytest.param("not a url", id="garbage"),
    pytest.param("", id="empty"),
]

BAD_BODIES = [
    pytest.param(None, id="missing-body"),
    pytest.param("", id="blank-body"),
    pytest.param("{", id="not-json"),
    pytest.param("[]", id="not-an-object"),
    pytest.param('{"quality": "720p"}', id="missing-url"),
    pytest.param('{"url": 42}', id="url-not-a-string"),
    pytest.param('{"url": null}', id="url-null"),
    pytest.param(json.dumps({"url": VIDEO_URL, "quality": "9000p"}), id="unknown-quality"),
    pytest.param(json.dumps({"url": VIDEO_URL, "quality": "best"}), id="best"),
    pytest.param(json.dumps({"url": VIDEO_URL, "quality": 720}), id="quality-not-a-string"),
]


class _Logger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **fields: object) -> None:
        self.calls.append((event, fields))

    def error(self, event: str, **fields: object) -> None:
        self.calls.append((event, fields))


class _FailingQueue:
    """A queue whose only call raises, to exercise the 500 path."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def writes(self) -> list[dict]:
        return list(self.calls)

    def enqueue(self, body: dict) -> None:
        self.calls.append(body)
        raise RuntimeError("queue unavailable")


def _api(
    dynamo: FakeDynamoRepository,
    media: FakeMediaStore,
    queue: object,
    *,
    now: datetime = NOW,
    logger: _Logger | None = None,
    env: dict[str, str] | None = None,
) -> Api:
    values = dict(CONFIG_ENV)
    values.update(env or {})
    config = load_config(values)
    tokens = TokenProvider(
        config.token_param_name,
        FakeSsmClient({config.token_param_name: TOKEN}),
    )
    return Api(
        dynamo,
        queue,  # type: ignore[arg-type]
        media,
        tokens,
        config,
        now=lambda: now,
        logger=logger or _Logger(),
    )


def _post(
    api: Api,
    *,
    body: str | None = None,
    headers: dict[str, str] | None = None,
    base64_encoded: bool = False,
) -> dict:
    event = {
        "version": "2.0",
        "rawPath": "/v1/jobs",
        "rawQueryString": "",
        "headers": ({"authorization": f"Bearer {TOKEN}"} if headers is None else headers),
        "requestContext": {"http": {"method": "POST", "path": "/v1/jobs"}},
        "body": body,
        "isBase64Encoded": base64_encoded,
    }
    return api.handle(event)


def _submit(
    api: Api,
    url: str = VIDEO_URL,
    *,
    quality: object = ...,
    headers: dict[str, str] | None = None,
) -> dict:
    payload: dict = {"url": url}
    if quality is not ...:
        payload["quality"] = quality
    return _post(api, body=json.dumps(payload), headers=headers)


def _isolation() -> tuple[FakeDynamoRepository, FakeMediaStore, FakeJobQueue]:
    return FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- 202 acceptance ----------------------------------------------------------


def test_accept_returns_202_with_the_contract_body() -> None:
    dynamo, media, queue = _isolation()
    response = _submit(_api(dynamo, media, queue))

    assert response["statusCode"] == 202
    assert response["headers"]["Content-Type"] == "application/json; charset=utf-8"

    body = json.loads(response["body"])
    assert set(body) == {"job_id", "state", "created_at", "status_url"}
    assert body["state"] == "accepted"
    assert body["created_at"] == CREATED_AT
    assert len(body["job_id"]) == 14
    assert body["status_url"] == f"/v1/jobs/{body['job_id']}"
    assert "download_url" not in body
    assert "result" not in body
    assert "media" not in response["body"].lower()


def test_accept_writes_the_documented_job_item() -> None:
    dynamo, media, queue = _isolation()
    response = _submit(_api(dynamo, media, queue))
    job_id = json.loads(response["body"])["job_id"]

    item = dynamo.items[f"JOB#{job_id}"]

    assert item["job_id"] == job_id
    assert item["state"] == "accepted"
    assert item["created_at"] == CREATED_AT
    assert item["updated_at"] == CREATED_AT
    assert item["source_host"] == "www.youtube.com"
    assert item["video_id"] == VIDEO_ID
    assert "requested_quality" not in item
    assert "requested_height" not in item
    assert "attempt" not in item
    assert "ttl" in item
    assert all(value is not None for value in item.values())


def test_accept_stores_the_requested_quality_with_its_capped_height() -> None:
    dynamo, media, queue = _isolation()
    response = _submit(_api(dynamo, media, queue), quality="720p")
    job_id = json.loads(response["body"])["job_id"]

    item = dynamo.items[f"JOB#{job_id}"]

    assert item["requested_quality"] == "720p"
    assert item["requested_height"] == 720
    assert queue.messages[0]["requested_height"] == 720


def test_accept_treats_a_null_quality_as_absent() -> None:
    dynamo, media, queue = _isolation()
    response = _submit(_api(dynamo, media, queue), quality=None)
    job_id = json.loads(response["body"])["job_id"]

    assert "requested_quality" not in dynamo.items[f"JOB#{job_id}"]


def test_accept_enqueues_the_exact_pointer_message() -> None:
    dynamo, media, queue = _isolation()
    response = _submit(_api(dynamo, media, queue))
    job_id = json.loads(response["body"])["job_id"]

    assert queue.messages == [
        {
            "version": 1,
            "job_id": job_id,
            "video_id": VIDEO_ID,
            "source_host": "www.youtube.com",
            "requested_height": None,
            "enqueued_at": CREATED_AT,
        }
    ]


def test_accept_consumes_one_unit_of_rate_capacity() -> None:
    dynamo, media, queue = _isolation()
    _submit(_api(dynamo, media, queue))

    call = next(call for call in dynamo.calls if call[0] == "create_job_with_rate_increment")
    assert call[1]["rate_pk"] == keys.rate_key(keys.rate_window(NOW_EPOCH), TOKEN)
    assert call[1]["limit"] == 10
    assert call[1]["now_epoch"] == NOW_EPOCH


def test_accepts_a_base64_encoded_body() -> None:
    dynamo, media, queue = _isolation()
    encoded = base64.b64encode(json.dumps({"url": VIDEO_URL}).encode("utf-8")).decode("utf-8")

    response = _post(_api(dynamo, media, queue), body=encoded, base64_encoded=True)

    assert response["statusCode"] == 202


# --- 400 invalid_input -------------------------------------------------------


@pytest.mark.parametrize("url", BAD_URLS)
def test_rejects_an_unsupported_url(url: str) -> None:
    dynamo, media, queue = _isolation()
    response = _submit(_api(dynamo, media, queue), url)

    assert response["statusCode"] == 400
    body = json.loads(response["body"])
    assert body["error"]["code"] == "invalid_input"
    assert body["error"]["retryable"] is False
    assert dynamo.writes == []
    assert dynamo.items == {}
    assert dynamo.rate_counts == {}
    assert queue.messages == []


@pytest.mark.parametrize("raw", BAD_BODIES)
def test_rejects_a_malformed_body(raw: str | None) -> None:
    dynamo, media, queue = _isolation()
    response = _post(_api(dynamo, media, queue), body=raw)

    assert response["statusCode"] == 400
    assert json.loads(response["body"])["error"]["code"] == "invalid_input"
    assert dynamo.writes == []
    assert dynamo.rate_counts == {}
    assert queue.messages == []


def test_rejects_a_malformed_base64_body() -> None:
    dynamo, media, queue = _isolation()
    response = _post(_api(dynamo, media, queue), body="not-base64!", base64_encoded=True)

    assert response["statusCode"] == 400
    assert json.loads(response["body"])["error"]["code"] == "invalid_input"
    assert dynamo.writes == []


# --- 401 unauthorized --------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="missing"),
        pytest.param({"authorization": "Basic abc"}, id="wrong-scheme"),
        pytest.param({"authorization": ""}, id="blank"),
        pytest.param({"authorization": "Bearer "}, id="empty-token"),
        pytest.param({"authorization": "Bearer wrong-token"}, id="wrong-token"),
        pytest.param({"authorization": "Bearer  two  spaces"}, id="multiple-spaces"),
        pytest.param({"authorization": f"bearer {TOKEN}"}, id="lowercase-prefix"),
    ],
)
def test_rejects_a_missing_or_wrong_token(headers: dict[str, str]) -> None:
    dynamo, media, queue = _isolation()
    response = _submit(_api(dynamo, media, queue), headers=headers)

    assert response["statusCode"] == 401
    body = json.loads(response["body"])
    assert body["error"]["code"] == "unauthorized"
    assert body["error"]["retryable"] is False
    assert "retry_after" not in body["error"]
    assert dynamo.writes == []
    assert dynamo.items == {}
    assert dynamo.rate_counts == {}
    assert queue.messages == []
    assert TOKEN not in response["body"]


def test_header_lookup_is_case_insensitive() -> None:
    dynamo, media, queue = _isolation()
    response = _submit(
        _api(dynamo, media, queue),
        headers={"AuThOrIzAtIoN": f"Bearer {TOKEN}"},
    )

    assert response["statusCode"] == 202


# --- 429 rate_limited -------------------------------------------------------


def _fill_rate_window(dynamo: FakeDynamoRepository, count: int = 10) -> str:
    rate_pk = keys.rate_key(keys.rate_window(NOW_EPOCH), TOKEN)
    dynamo.rate_counts[rate_pk] = count
    return rate_pk


def test_rate_limit_returns_429_with_retry_metadata() -> None:
    dynamo, media, queue = _isolation()
    rate_pk = _fill_rate_window(dynamo)

    response = _submit(_api(dynamo, media, queue))

    assert response["statusCode"] == 429
    window_end = keys.rate_window(NOW_EPOCH) + 3600
    assert response["headers"]["Retry-After"] == str(window_end - NOW_EPOCH)

    body = json.loads(response["body"])
    assert body["error"]["code"] == "rate_limited"
    assert body["error"]["retryable"] is True
    assert body["error"]["retry_after"] == _iso(window_end)
    assert _iso(window_end) in body["error"]["message"]
    assert dynamo.items == {}
    assert dynamo.rate_counts[rate_pk] == 10
    assert queue.messages == []


def test_retry_after_header_never_drops_below_one_second() -> None:
    dynamo, media, queue = _isolation()
    _fill_rate_window(dynamo)
    last_second = NOW.replace(minute=59, second=59)

    response = _submit(_api(dynamo, media, queue, now=last_second))

    assert response["statusCode"] == 429
    assert response["headers"]["Retry-After"] == "1"


# --- 500 internal_error -----------------------------------------------------


def test_queue_failure_returns_500_internal_error() -> None:
    dynamo, media, _queue = _isolation()
    logger = _Logger()
    api = _api(dynamo, media, _FailingQueue(), logger=logger)

    response = _submit(api)

    assert response["statusCode"] == 500
    body = json.loads(response["body"])
    assert body["error"]["code"] == "internal_error"
    assert body["error"]["retryable"] is True
    assert "Traceback" not in response["body"]
    assert "queue unavailable" not in response["body"]
    assert any(
        event == "request_failed" and fields.get("message") == "RuntimeError"
        for event, fields in logger.calls
    ), "a swallowed exception must leave a trace in the log"


# --- routing and envelopes --------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        pytest.param("GET", "/v1/jobs", id="jobs-collection"),
        pytest.param("GET", "/health", id="unknown-path"),
        pytest.param("POST", "/v1/jobs/abc/link/extra", id="unknown-sub-path"),
        pytest.param("DELETE", "/v1/jobs/abc", id="unknown-method"),
        pytest.param("GET", "/v1/jobs/", id="empty-job-id"),
    ],
)
def test_unknown_paths_return_a_json_404(method: str, path: str) -> None:
    dynamo, media, queue = _isolation()
    event = {
        "version": "2.0",
        "rawPath": path,
        "headers": {"authorization": f"Bearer {TOKEN}"},
        "requestContext": {"http": {"method": method, "path": path}},
        "body": None,
        "isBase64Encoded": False,
    }

    response = _api(dynamo, media, queue).handle(event)

    assert response["statusCode"] == 404
    assert response["headers"]["Content-Type"] == "application/json; charset=utf-8"
    body = json.loads(response["body"])
    assert body["error"]["code"] == "not_found"
    assert body["error"]["retryable"] is False
    assert dynamo.calls == []
    assert queue.messages == []


def test_every_response_body_stays_under_four_kilobytes() -> None:
    dynamo, media, queue = _isolation()
    api = _api(dynamo, media, queue)

    responses = [
        _submit(api),
        _submit(api, "not a url"),
        _submit(api, headers={}),
    ]
    _fill_rate_window(dynamo)
    responses.append(_submit(api))

    assert [response["statusCode"] for response in responses] == [202, 400, 401, 429]
    assert all(len(response["body"].encode("utf-8")) < 4096 for response in responses)


# --- structured logging -----------------------------------------------------


def test_logs_one_structured_line_with_allowlisted_fields_only() -> None:
    dynamo, media, queue = _isolation()
    logger = _Logger()
    api = _api(dynamo, media, queue, logger=logger)

    _submit(api)

    assert len(logger.calls) == 1
    _event, fields = logger.calls[0]
    assert set(fields) <= ALLOWED_FIELDS
    assert fields["job_id"]
    assert fields["state"] == "accepted"
    assert fields["duration_ms"] >= 0
    serialised = json.dumps(fields)
    assert TOKEN not in serialised
    assert VIDEO_URL not in serialised
    assert "?" not in serialised


def test_rejected_requests_never_log_the_url_or_the_token() -> None:
    dynamo, media, queue = _isolation()
    logger = _Logger()
    api = _api(dynamo, media, queue, logger=logger)

    _submit(api, "https://vimeo.com/123456789?token=secret")

    assert len(logger.calls) == 1
    _event, fields = logger.calls[0]
    assert set(fields) <= ALLOWED_FIELDS
    serialised = json.dumps(fields)
    assert "vimeo" not in serialised
    assert "token=secret" not in serialised
    assert TOKEN not in serialised
