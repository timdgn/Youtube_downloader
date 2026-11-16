"""Unit tests for the derived result view of a job (T062)."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.s3_fake import FakeMediaStore
from tests.fakes.sqs_fake import FakeJobQueue
from tests.fakes.ssm_fake import FakeSsmClient
from ytdl import errors, keys, states
from ytdl.api.handler import Api
from ytdl.auth import TokenProvider
from ytdl.config import load_config

TOKEN = "status-view-token"
JOB_ID = "m3x9k2q1f7abc"
VIDEO_ID = "dQw4w9WgXcQ"
NOW = datetime(2026, 11, 15, 14, 3, 11, tzinfo=UTC)
NOW_EPOCH = int(NOW.timestamp())
CREATED_AT = "2026-11-15T14:03:11Z"
UPDATED_AT = "2026-11-15T14:04:52Z"
MEDIA = b"fake-mp4-bytes"

CONFIG_ENV = {
    "ENVIRONMENT": "dev",
    "JOBS_TABLE": "ytdl-test-jobs",
    "MEDIA_BUCKET": "ytdl-test-media",
    "JOBS_QUEUE_URL": "https://sqs.eu-west-1.amazonaws.com/000000000000/ytdl-test-jobs",
    "TOKEN_PARAM_NAME": "/ytdl/test/api-token",
}

JOB_KEYS = {
    "job_id",
    "state",
    "created_at",
    "updated_at",
    "requested_quality",
    "produced_quality",
    "result",
    "failure",
}


class _Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


class _Logger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **fields: object) -> None:
        self.calls.append((event, fields))

    def error(self, event: str, **fields: object) -> None:
        self.calls.append((event, fields))


def _api(
    dynamo: FakeDynamoRepository,
    media: FakeMediaStore,
    queue: FakeJobQueue,
    clock: _Clock | None = None,
) -> Api:
    config = load_config(dict(CONFIG_ENV))
    tokens = TokenProvider(
        config.token_param_name,
        FakeSsmClient({config.token_param_name: TOKEN}),
    )
    return Api(
        dynamo,
        queue,
        media,
        tokens,
        config,
        now=clock or (lambda: NOW),
        logger=_Logger(),
    )


def _seed_job(dynamo: FakeDynamoRepository, **overrides: object) -> dict:
    item: dict = {
        "pk": f"JOB#{JOB_ID}",
        "job_id": JOB_ID,
        "state": states.ACCEPTED,
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
        "source_host": "www.youtube.com",
        "video_id": VIDEO_ID,
    }
    item.update(overrides)
    dynamo.items[item["pk"]] = item
    return item


def _completed(media: FakeMediaStore) -> dict:
    key = keys.media_key(JOB_ID, VIDEO_ID)
    media.seed(key, MEDIA)
    return {
        "state": states.COMPLETED,
        "updated_at": UPDATED_AT,
        "requested_quality": "720p",
        "produced_quality": "720p",
        "media_key": key,
        "media_size_bytes": len(MEDIA),
        "media_created_at": NOW_EPOCH,
        "media_expires_at": NOW_EPOCH + 604800,
        "attempt": 1,
    }


def _get(api: Api) -> dict:
    event = {
        "version": "2.0",
        "rawPath": f"/v1/jobs/{JOB_ID}",
        "headers": {"authorization": f"Bearer {TOKEN}"},
        "requestContext": {"http": {"method": "GET", "path": f"/v1/jobs/{JOB_ID}"}},
        "body": None,
        "isBase64Encoded": False,
    }
    return api.handle(event)


def _body(response: dict) -> dict:
    assert response["statusCode"] == 200
    return json.loads(response["body"])


def test_a_completed_job_inside_retention_with_the_object_is_available() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))

    result = _body(_get(_api(dynamo, media, queue)))["result"]

    assert result["status"] == "available"
    assert result["download_url"]
    assert result["expires_at"] is not None
    assert result["size_bytes"] == len(MEDIA)


def test_now_past_media_expires_at_reports_expired() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))
    clock = _Clock(NOW + timedelta(seconds=604801))

    result = _body(_get(_api(dynamo, media, queue, clock)))["result"]

    assert result["status"] == "expired"
    assert result["download_url"] is None
    assert result["expires_at"] is None


def test_a_missing_object_reports_expired() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))
    media.objects.clear()

    result = _body(_get(_api(dynamo, media, queue)))["result"]

    assert result["status"] == "expired"
    assert result["download_url"] is None
    assert result["expires_at"] is None


def test_both_conditions_are_required_for_available() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))
    media.objects.clear()
    clock = _Clock(NOW + timedelta(seconds=604801))

    result = _body(_get(_api(dynamo, media, queue, clock)))["result"]

    assert result["status"] == "expired"


def test_a_completed_job_without_media_expires_at_reports_expired() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    job = _completed(media)
    del job["media_expires_at"]
    _seed_job(dynamo, **job)

    result = _body(_get(_api(dynamo, media, queue)))["result"]

    assert result["status"] == "expired"


def test_an_expired_result_keeps_the_stored_size_and_content_type() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))
    media.objects.clear()

    result = _body(_get(_api(dynamo, media, queue)))["result"]

    assert result["size_bytes"] == len(MEDIA)
    assert result["content_type"] == "video/mp4"


@pytest.mark.parametrize(
    ("category", "retryable"),
    [
        pytest.param(errors.INVALID_INPUT, False, id="invalid-input"),
        pytest.param(errors.UNSUPPORTED_MEDIA, False, id="unsupported-media"),
        pytest.param(errors.LIMIT_EXCEEDED, False, id="limit-exceeded"),
        pytest.param(errors.UPSTREAM_PROBLEM, True, id="upstream-problem"),
        pytest.param(errors.INTERNAL_ERROR, True, id="internal-error"),
    ],
)
def test_a_failed_job_exposes_category_retryable_and_message(
    category: str, retryable: bool
) -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(
        dynamo,
        state=states.FAILED,
        updated_at=UPDATED_AT,
        attempt=1,
        failure_category=category,
        failure_retryable=retryable,
        failure_message=errors.MESSAGES[category],
    )

    body = _body(_get(_api(dynamo, media, queue)))

    assert body["state"] == "failed"
    assert body["result"] is None
    assert body["failure"]["category"] == category
    assert body["failure"]["retryable"] is retryable
    assert body["failure"]["message"]
    assert body["failure"]["message"] == errors.MESSAGES[category]


def test_repeated_reads_of_the_same_record_are_identical() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))
    api = _api(dynamo, media, queue)

    assert _get(api) == _get(api)


def test_repeated_reads_of_a_failed_record_are_identical() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(
        dynamo,
        state=states.FAILED,
        updated_at=UPDATED_AT,
        failure_category=errors.UNSUPPORTED_MEDIA,
        failure_retryable=False,
        failure_message=errors.MESSAGES[errors.UNSUPPORTED_MEDIA],
    )
    api = _api(dynamo, media, queue)

    assert _get(api) == _get(api)


def test_the_read_path_performs_zero_writes() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))
    before = deepcopy(dynamo.items)

    _get(_api(dynamo, media, queue))

    assert dynamo.writes == []
    assert media.writes == []
    assert queue.writes == []
    assert dynamo.items == before
