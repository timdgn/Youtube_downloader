"""Contract tests for POST /v1/jobs/{job_id}/link (T046)."""

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

TOKEN = "reissue-token"
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


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _api(
    dynamo: FakeDynamoRepository,
    media: FakeMediaStore,
    queue: FakeJobQueue,
    *,
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
        "produced_height": 720,
        "produced_quality": "720p",
        "media_key": key,
        "media_size_bytes": len(MEDIA),
        "media_created_at": NOW_EPOCH,
        "media_expires_at": NOW_EPOCH + 604800,
        "attempt": 1,
        "processing_ms": 101000,
    }


def _link(api: Api, job_id: str = JOB_ID, headers: dict[str, str] | None = None) -> dict:
    event = {
        "version": "2.0",
        "rawPath": f"/v1/jobs/{job_id}/link",
        "headers": {"authorization": f"Bearer {TOKEN}"} if headers is None else headers,
        "requestContext": {"http": {"method": "POST", "path": f"/v1/jobs/{job_id}/link"}},
        "body": None,
        "isBase64Encoded": False,
    }
    return api.handle(event)


def _body(response: dict) -> dict:
    return json.loads(response["body"])


def test_completed_job_reissues_a_fresh_link() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))

    response = _link(_api(dynamo, media, queue))

    assert response["statusCode"] == 200
    body = _body(response)
    assert set(body) == {"job_id", "download_url", "expires_at", "size_bytes"}
    assert body["job_id"] == JOB_ID
    assert body["size_bytes"] == len(MEDIA)
    assert body["expires_at"] == _iso(NOW + timedelta(seconds=3600))
    assert keys.media_key(JOB_ID, VIDEO_ID) in body["download_url"]
    assert f"{VIDEO_ID}-{JOB_ID}.mp4" in body["download_url"]


def test_reissue_advances_the_expiry_with_the_clock() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))
    clock = _Clock(NOW)
    api = _api(dynamo, media, queue, clock=clock)

    first = _body(_link(api))
    clock.moment = NOW + timedelta(seconds=600)
    second = _body(_link(api))

    assert first["expires_at"] == _iso(NOW + timedelta(seconds=3600))
    assert second["expires_at"] == _iso(NOW + timedelta(seconds=4200))
    assert second["expires_at"] != first["expires_at"]
    assert len([call for call in media.calls if call[0] == "presign_get"]) == 2


def test_reissue_presigns_with_the_documented_filename_and_ttl() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))

    _link(_api(dynamo, media, queue))

    presign = next(call for call in media.calls if call[0] == "presign_get")
    assert presign[1] == {
        "key": keys.media_key(JOB_ID, VIDEO_ID),
        "filename": f"{VIDEO_ID}-{JOB_ID}.mp4",
        "expires_in": 3600,
    }


def test_unknown_identifier_returns_404() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()

    response = _link(_api(dynamo, media, queue), job_id="unknown")

    assert response["statusCode"] == 404
    assert _body(response)["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    "state",
    [
        pytest.param(states.ACCEPTED, id="accepted"),
        pytest.param(states.PROCESSING, id="processing"),
    ],
)
def test_unfinished_job_returns_409(state: str) -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, state=state, attempt=1)

    response = _link(_api(dynamo, media, queue))

    assert response["statusCode"] == 409
    body = _body(response)
    assert body["error"]["code"] == "not_completed"
    assert body["error"]["retryable"] is False
    assert not any(call[0] == "presign_get" for call in media.calls)


def test_failed_job_returns_409_not_completed() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(
        dynamo,
        state=states.FAILED,
        updated_at=UPDATED_AT,
        failure_category="unsupported_media",
        failure_retryable=False,
        failure_message="The source refused this video.",
    )

    response = _link(_api(dynamo, media, queue))

    assert response["statusCode"] == 409
    assert _body(response)["error"]["code"] == "not_completed"


def test_missing_object_returns_410() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    job = _completed(media)
    media.objects.clear()
    _seed_job(dynamo, **job)

    response = _link(_api(dynamo, media, queue))

    assert response["statusCode"] == 410
    body = _body(response)
    assert body["error"]["code"] == "result_expired"
    assert body["error"]["retryable"] is False
    assert "download_url" not in body


def test_expired_retention_returns_410() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))
    dynamo.items[f"JOB#{JOB_ID}"]["media_expires_at"] = NOW_EPOCH - 1

    response = _link(_api(dynamo, media, queue))

    assert response["statusCode"] == 410
    assert _body(response)["error"]["code"] == "result_expired"


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="missing"),
        pytest.param({"authorization": "Bearer wrong-token"}, id="wrong-token"),
    ],
)
def test_unauthorized_requests_are_refused(headers: dict[str, str]) -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))

    response = _link(_api(dynamo, media, queue), headers=headers)

    assert response["statusCode"] == 401
    assert _body(response)["error"]["code"] == "unauthorized"


def test_reissue_is_read_only() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_job(dynamo, **_completed(media))
    before = deepcopy(dynamo.items)

    _link(_api(dynamo, media, queue))

    assert dynamo.writes == []
    assert media.writes == []
    assert queue.writes == []
    assert dynamo.items == before
    assert dynamo.items[f"JOB#{JOB_ID}"]["attempt"] == 1
    assert dynamo.items[f"JOB#{JOB_ID}"]["updated_at"] == UPDATED_AT
