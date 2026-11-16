"""Contract tests for GET /v1/jobs/{job_id} (T045)."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.s3_fake import FakeMediaStore
from tests.fakes.sqs_fake import FakeJobQueue
from tests.fakes.ssm_fake import FakeSsmClient
from ytdl import keys, states
from ytdl.api.handler import Api
from ytdl.auth import TokenProvider
from ytdl.config import load_config

TOKEN = "get-job-token"
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


class _Logger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **fields: object) -> None:
        self.calls.append((event, fields))

    def error(self, event: str, **fields: object) -> None:
        self.calls.append((event, fields))


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _api(
    dynamo: FakeDynamoRepository,
    media: FakeMediaStore,
    queue: FakeJobQueue,
) -> Api:
    config = load_config(dict(CONFIG_ENV))
    tokens = TokenProvider(
        config.token_param_name,
        FakeSsmClient({config.token_param_name: TOKEN}),
    )
    return Api(dynamo, queue, media, tokens, config, now=lambda: NOW, logger=_Logger())


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
        "produced_height": 720,
        "produced_quality": "720p",
        "media_key": key,
        "media_size_bytes": len(MEDIA),
        "media_created_at": NOW_EPOCH,
        "media_expires_at": NOW_EPOCH + 604800,
        "attempt": 1,
    }


def _get(api: Api, job_id: str = JOB_ID, headers: dict[str, str] | None = None) -> dict:
    event = {
        "version": "2.0",
        "rawPath": f"/v1/jobs/{job_id}",
        "headers": {"authorization": f"Bearer {TOKEN}"} if headers is None else headers,
        "requestContext": {"http": {"method": "GET", "path": f"/v1/jobs/{job_id}"}},
        "body": None,
        "isBase64Encoded": False,
    }
    return api.handle(event)


def _body(response: dict) -> dict:
    return json.loads(response["body"])


def test_accepted_job_reports_accepted_with_a_null_result() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, requested_quality="720p")

    response = _get(_api(dynamo, media, queue))

    assert response["statusCode"] == 200
    body = _body(response)
    assert set(body) == JOB_KEYS
    assert body["job_id"] == JOB_ID
    assert body["state"] == "accepted"
    assert body["created_at"] == CREATED_AT
    assert body["updated_at"] == CREATED_AT
    assert body["requested_quality"] == "720p"
    assert body["produced_quality"] is None
    assert body["result"] is None
    assert body["failure"] is None


def test_processing_job_reports_processing() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, state=states.PROCESSING, updated_at=UPDATED_AT, attempt=1)

    body = _body(_get(_api(dynamo, media, queue)))

    assert body["state"] == "processing"
    assert body["updated_at"] == UPDATED_AT
    assert body["result"] is None


def test_completed_job_with_the_object_reports_an_available_result() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))

    body = _body(_get(_api(dynamo, media, queue)))

    assert body["state"] == "completed"
    assert body["produced_quality"] == "720p"
    result = body["result"]
    assert result["status"] == "available"
    assert result["content_type"] == "video/mp4"
    assert result["size_bytes"] == len(MEDIA)
    assert result["expires_at"] == _iso(NOW + timedelta(seconds=3600))
    assert keys.media_key(JOB_ID, VIDEO_ID) in result["download_url"]
    assert body["failure"] is None


def test_completed_job_presigns_with_the_documented_filename_and_ttl() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))

    _get(_api(dynamo, media, queue))

    presign = next(call for call in media.calls if call[0] == "presign_get")
    assert presign[1] == {
        "key": keys.media_key(JOB_ID, VIDEO_ID),
        "filename": f"{VIDEO_ID}-{JOB_ID}.mp4",
        "expires_in": 3600,
        "content_type": "video/mp4",
    }


def test_completed_job_past_retention_reports_expired() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))
    dynamo.items[f"JOB#{JOB_ID}"]["media_expires_at"] = NOW_EPOCH - 1

    body = _body(_get(_api(dynamo, media, queue)))

    result = body["result"]
    assert result["status"] == "expired"
    assert result["download_url"] is None
    assert result["expires_at"] is None
    assert result["size_bytes"] == len(MEDIA)
    assert result["content_type"] == "video/mp4"
    assert not any(call[0] == "presign_get" for call in media.calls)


def test_completed_job_with_a_missing_object_reports_expired() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    job = _completed(media)
    media.objects.clear()
    _seed_job(dynamo, **job)

    body = _body(_get(_api(dynamo, media, queue)))

    assert body["result"]["status"] == "expired"
    assert body["result"]["download_url"] is None
    assert body["result"]["expires_at"] is None
    assert not any(call[0] == "presign_get" for call in media.calls)


def test_completed_job_without_a_stored_media_key_derives_it() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    job = _completed(media)
    del job["media_key"]
    _seed_job(dynamo, **job)

    body = _body(_get(_api(dynamo, media, queue)))

    assert body["result"]["status"] == "available"
    head = next(call for call in media.calls if call[0] == "head_object")
    assert head[1]["key"] == keys.media_key(JOB_ID, VIDEO_ID)


def test_failed_job_reports_the_failure_block() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(
        dynamo,
        state=states.FAILED,
        updated_at=UPDATED_AT,
        attempt=2,
        failure_category="unsupported_media",
        failure_retryable=False,
        failure_message="The source refused this video.",
    )

    body = _body(_get(_api(dynamo, media, queue)))

    assert body["state"] == "failed"
    assert body["result"] is None
    assert body["failure"] == {
        "category": "unsupported_media",
        "retryable": False,
        "message": "The source refused this video.",
    }


@pytest.mark.parametrize("job_id", ["unknown", "a" * 14, "'; DROP TABLE jobs"])
def test_unknown_identifier_returns_404(job_id: str) -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()

    response = _get(_api(dynamo, media, queue), job_id=job_id)

    assert response["statusCode"] == 404
    assert _body(response)["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="missing"),
        pytest.param({"authorization": "Basic abc"}, id="wrong-scheme"),
        pytest.param({"authorization": "Bearer wrong-token"}, id="wrong-token"),
    ],
)
def test_unauthorized_requests_are_refused(headers: dict[str, str]) -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))

    response = _get(_api(dynamo, media, queue), headers=headers)

    assert response["statusCode"] == 401
    assert _body(response)["error"]["code"] == "unauthorized"
    assert dynamo.calls == []


def test_get_is_read_only() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))
    before = deepcopy(dynamo.items)

    _get(_api(dynamo, media, queue))

    assert dynamo.writes == []
    assert media.writes == []
    assert queue.writes == []
    assert dynamo.items == before
    assert dynamo.items[f"JOB#{JOB_ID}"]["attempt"] == 1
    assert dynamo.items[f"JOB#{JOB_ID}"]["updated_at"] == UPDATED_AT


def test_repeated_reads_return_identical_output() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))
    api = _api(dynamo, media, queue)

    first = _get(api)
    second = _get(api)

    assert first == second


# --- the deleted overlay -----------------------------------------------------


def test_a_deleted_job_is_reported_as_expired_even_when_the_object_exists() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(
        dynamo,
        **_completed(media),
        deleted_at="2026-11-15T14:05:00Z",
    )

    response = _get(_api(dynamo, media, queue))

    assert response["statusCode"] == 200
    body = _body(response)
    assert body["state"] == states.COMPLETED
    assert body["result"]["status"] == "expired"
    assert body["result"]["download_url"] is None
    assert body["result"]["expires_at"] is None
    assert body["result"]["size_bytes"] == len(MEDIA)


def test_a_deleted_job_does_not_consult_s3() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media), deleted_at="2026-11-15T14:05:00Z")
    media.objects.clear()

    response = _get(_api(dynamo, media, queue))

    assert _body(response)["result"]["status"] == "expired"
    assert [call[0] for call in media.calls] == []
