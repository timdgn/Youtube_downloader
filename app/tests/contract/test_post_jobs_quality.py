"""Contract tests for the optional quality preference on POST /v1/jobs (T077, T080)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.s3_fake import FakeMediaStore
from tests.fakes.sqs_fake import FakeJobQueue
from tests.fakes.ssm_fake import FakeSsmClient
from ytdl.api.handler import Api
from ytdl.auth import TokenProvider
from ytdl.config import load_config

TOKEN = "quality-contract-token"
NOW = datetime(2026, 11, 15, 14, 3, 11, tzinfo=UTC)
CREATED_AT = "2026-11-15T14:03:11Z"
VIDEO_ID = "dQw4w9WgXcQ"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"

CONFIG_ENV = {
    "ENVIRONMENT": "dev",
    "JOBS_TABLE": "ytdl-test-jobs",
    "MEDIA_BUCKET": "ytdl-test-media",
    "JOBS_QUEUE_URL": "https://sqs.eu-west-1.amazonaws.com/000000000000/ytdl-test-jobs",
    "TOKEN_PARAM_NAME": "/ytdl/test/api-token",
}


class _Logger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **fields: object) -> None:
        self.calls.append((event, fields))

    def error(self, event: str, **fields: object) -> None:
        self.calls.append((event, fields))


def _api(**env: str) -> tuple[Api, FakeDynamoRepository, FakeJobQueue]:
    values = dict(CONFIG_ENV)
    values.update(env)
    config = load_config(values)
    dynamo = FakeDynamoRepository()
    queue = FakeJobQueue()
    tokens = TokenProvider(
        config.token_param_name,
        FakeSsmClient({config.token_param_name: TOKEN}),
    )
    api = Api(
        dynamo,
        queue,
        FakeMediaStore(),
        tokens,
        config,
        now=lambda: NOW,
        logger=_Logger(),
    )
    return api, dynamo, queue


def _submit(api: Api, quality: object = ...) -> dict:
    payload: dict = {"url": VIDEO_URL}
    if quality is not ...:
        payload["quality"] = quality
    event = {
        "version": "2.0",
        "rawPath": "/v1/jobs",
        "headers": {"authorization": f"Bearer {TOKEN}"},
        "requestContext": {"http": {"method": "POST", "path": "/v1/jobs"}},
        "body": json.dumps(payload),
        "isBase64Encoded": False,
    }
    return api.handle(event)


def _job(dynamo: FakeDynamoRepository, response: dict) -> dict:
    job_id = json.loads(response["body"])["job_id"]
    return dynamo.items[f"JOB#{job_id}"]


def test_no_quality_stores_no_quality_and_enqueues_a_null_height() -> None:
    api, dynamo, queue = _api()

    response = _submit(api)

    assert response["statusCode"] == 202
    item = _job(dynamo, response)
    assert "requested_quality" not in item
    assert "requested_height" not in item
    assert queue.messages[0]["requested_height"] is None
    assert queue.messages[0]["enqueued_at"] == CREATED_AT


def test_an_accepted_quality_is_stored_with_its_resolved_height() -> None:
    api, dynamo, queue = _api()

    response = _submit(api, quality="720p")

    assert response["statusCode"] == 202
    item = _job(dynamo, response)
    assert item["requested_quality"] == "720p"
    assert item["requested_height"] == 720
    assert queue.messages[0]["requested_height"] == 720


@pytest.mark.parametrize(
    ("requested", "label", "height"),
    [
        pytest.param("2160p", "1080p", 1080, id="2160-capped"),
        pytest.param("1440p", "1080p", 1080, id="1440-capped"),
        pytest.param("1080p", "1080p", 1080, id="1080-at-ceiling"),
    ],
)
def test_a_quality_above_the_ceiling_is_accepted_and_capped(
    requested: str, label: str, height: int
) -> None:
    api, dynamo, queue = _api()

    response = _submit(api, quality=requested)

    assert response["statusCode"] == 202
    item = _job(dynamo, response)
    assert item["requested_quality"] == label
    assert item["requested_height"] == height
    assert queue.messages[0]["requested_height"] == height


def test_the_ceiling_comes_from_the_configuration() -> None:
    api, dynamo, queue = _api(MAX_HEIGHT="720")

    response = _submit(api, quality="2160p")

    assert response["statusCode"] == 202
    item = _job(dynamo, response)
    assert item["requested_quality"] == "720p"
    assert item["requested_height"] == 720
    assert queue.messages[0]["requested_height"] == 720


@pytest.mark.parametrize(
    "quality",
    [
        pytest.param("9000p", id="unknown-height"),
        pytest.param("best", id="keyword"),
        pytest.param("", id="empty"),
        pytest.param("720", id="missing-suffix"),
        pytest.param(720, id="not-a-string"),
    ],
)
def test_an_unknown_quality_is_rejected_without_writing_a_job(quality: object) -> None:
    api, dynamo, queue = _api()

    response = _submit(api, quality=quality)

    assert response["statusCode"] == 400
    body = json.loads(response["body"])
    assert body["error"]["code"] == "invalid_input"
    assert body["error"]["retryable"] is False
    assert dynamo.items == {}
    assert dynamo.writes == []
    assert dynamo.rate_counts == {}
    assert queue.messages == []
