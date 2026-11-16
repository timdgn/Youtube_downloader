"""Contract tests for GET /v1/jobs/{job_id} failures and expiry (T063)."""

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

TOKEN = "get-job-failures-token"
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

FAILURES = [
    pytest.param(errors.INVALID_INPUT, False, id="invalid-input"),
    pytest.param(errors.UNSUPPORTED_MEDIA, False, id="unsupported-media"),
    pytest.param(errors.LIMIT_EXCEEDED, False, id="limit-exceeded"),
    pytest.param(errors.UPSTREAM_PROBLEM, True, id="upstream-problem"),
    pytest.param(errors.INTERNAL_ERROR, True, id="internal-error"),
]


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


def _event(method: str, path: str) -> dict:
    return {
        "version": "2.0",
        "rawPath": path,
        "headers": {"authorization": f"Bearer {TOKEN}"},
        "requestContext": {"http": {"method": method, "path": path}},
        "body": None,
        "isBase64Encoded": False,
    }


def _get(api: Api, job_id: str = JOB_ID) -> dict:
    return api.handle(_event("GET", f"/v1/jobs/{job_id}"))


def _link(api: Api, job_id: str = JOB_ID) -> dict:
    return api.handle(_event("POST", f"/v1/jobs/{job_id}/link"))


def _body(response: dict) -> dict:
    return json.loads(response["body"])


@pytest.mark.parametrize(("category", "retryable"), FAILURES)
def test_get_reports_each_failure_category(category: str, retryable: bool) -> None:
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

    response = _get(_api(dynamo, media, queue))

    assert response["statusCode"] == 200
    body = _body(response)
    assert body["state"] == "failed"
    assert body["result"] is None
    failure = body["failure"]
    assert failure["category"] == category
    assert failure["retryable"] is retryable
    assert failure["message"] == errors.MESSAGES[category]
    assert "http" not in failure["message"].lower()
    assert "\n" not in failure["message"]


def test_an_expired_completed_job_has_no_download_url() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))
    dynamo.items[f"JOB#{JOB_ID}"]["media_expires_at"] = NOW_EPOCH - 1

    body = _body(_get(_api(dynamo, media, queue)))

    assert body["state"] == "completed"
    assert body["result"]["status"] == "expired"
    assert body["result"]["download_url"] is None
    assert body["failure"] is None


def test_a_missing_object_has_no_download_url() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))
    media.objects.clear()

    body = _body(_get(_api(dynamo, media, queue)))

    assert body["result"]["status"] == "expired"
    assert body["result"]["download_url"] is None


def test_reissue_of_an_expired_completed_job_returns_410() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))
    dynamo.items[f"JOB#{JOB_ID}"]["media_expires_at"] = NOW_EPOCH - 1

    response = _link(_api(dynamo, media, queue))

    assert response["statusCode"] == 410
    error = _body(response)["error"]
    assert error["code"] == "result_expired"
    assert error["retryable"] is False
    assert "download_url" not in _body(response)


def test_reissue_with_a_missing_object_returns_410() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))
    media.objects.clear()

    response = _link(_api(dynamo, media, queue))

    assert response["statusCode"] == 410
    assert _body(response)["error"]["code"] == "result_expired"


def test_an_unknown_job_returns_404_not_410() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()

    get_response = _get(_api(dynamo, media, queue), job_id="unknown")
    link_response = _link(_api(dynamo, media, queue), job_id="unknown")

    assert get_response["statusCode"] == 404
    assert _body(get_response)["error"]["code"] == "not_found"
    assert link_response["statusCode"] == 404
    assert _body(link_response)["error"]["code"] == "not_found"


def test_failure_reads_are_repeatable_and_read_only() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(
        dynamo,
        state=states.FAILED,
        updated_at=UPDATED_AT,
        failure_category=errors.UPSTREAM_PROBLEM,
        failure_retryable=True,
        failure_message=errors.MESSAGES[errors.UPSTREAM_PROBLEM],
    )
    before = deepcopy(dynamo.items)
    api = _api(dynamo, media, queue)

    first = _get(api)
    second = _get(api)

    assert first == second
    assert dynamo.writes == []
    assert media.writes == []
    assert queue.writes == []
    assert dynamo.items == before


def test_expired_reads_are_read_only() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))
    dynamo.items[f"JOB#{JOB_ID}"]["media_expires_at"] = NOW_EPOCH - 1
    before = deepcopy(dynamo.items)
    clock = _Clock(NOW + timedelta(seconds=1))

    _get(_api(dynamo, media, queue, clock))

    assert dynamo.writes == []
    assert media.writes == []
    assert queue.writes == []
    assert dynamo.items == before
