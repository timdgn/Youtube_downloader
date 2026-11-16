"""Unit tests for download-link issuance (T043)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.s3_fake import FakeMediaStore
from tests.fakes.sqs_fake import FakeJobQueue
from tests.fakes.ssm_fake import FakeSsmClient
from ytdl import keys, states
from ytdl.api import routes
from ytdl.api.handler import Api
from ytdl.auth import TokenProvider
from ytdl.config import load_config

TOKEN = "link-token-value"
JOB_ID = "m3x9k2q1f7abc"
VIDEO_ID = "dQw4w9WgXcQ"
TITLE = "A Video Title That Must Never Reach The Filename"
NOW = datetime(2026, 11, 15, 14, 3, 11, tzinfo=UTC)
NOW_EPOCH = int(NOW.timestamp())
CREATED_AT = "2026-11-15T14:03:11Z"
MEDIA = b"fake-mp4-bytes"

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


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _api(
    dynamo: FakeDynamoRepository,
    media: FakeMediaStore,
    queue: FakeJobQueue,
    *,
    env: dict[str, str] | None = None,
) -> Api:
    values = dict(CONFIG_ENV)
    values.update(env or {})
    config = load_config(values)
    tokens = TokenProvider(
        config.token_param_name,
        FakeSsmClient({config.token_param_name: TOKEN}),
    )
    return Api(dynamo, queue, media, tokens, config, now=lambda: NOW, logger=_Logger())


def _seed(dynamo: FakeDynamoRepository, **overrides: object) -> dict:
    item: dict = {
        "pk": f"JOB#{JOB_ID}",
        "job_id": JOB_ID,
        "state": states.ACCEPTED,
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
        "source_host": "www.youtube.com",
        "video_id": VIDEO_ID,
        "title": TITLE,
    }
    item.update(overrides)
    dynamo.items[item["pk"]] = item
    return item


def _completed(media: FakeMediaStore) -> dict:
    key = keys.media_key(JOB_ID, VIDEO_ID)
    media.seed(key, MEDIA)
    return {
        "state": states.COMPLETED,
        "media_key": key,
        "media_size_bytes": len(MEDIA),
        "media_created_at": NOW_EPOCH,
        "media_expires_at": NOW_EPOCH + 604800,
        "produced_height": 720,
        "produced_quality": "720p",
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


def _link(api: Api) -> dict:
    event = {
        "version": "2.0",
        "rawPath": f"/v1/jobs/{JOB_ID}/link",
        "headers": {"authorization": f"Bearer {TOKEN}"},
        "requestContext": {"http": {"method": "POST", "path": f"/v1/jobs/{JOB_ID}/link"}},
        "body": None,
        "isBase64Encoded": False,
    }
    return api.handle(event)


def test_result_is_null_for_accepted_and_processing() -> None:
    for state in (states.ACCEPTED, states.PROCESSING):
        job = {"job_id": JOB_ID, "state": state, "created_at": CREATED_AT}

        assert routes.job_body(job, None, None)["result"] is None

    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed(dynamo, state=states.ACCEPTED)
    accepted = json.loads(_get(_api(dynamo, media, queue))["body"])
    assert accepted["result"] is None

    dynamo.items[f"JOB#{JOB_ID}"]["state"] = states.PROCESSING
    processing = json.loads(_get(_api(dynamo, media, queue))["body"])
    assert processing["result"] is None


def test_available_expiry_is_now_plus_the_configured_ttl() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed(dynamo, **_completed(media))

    body = json.loads(_get(_api(dynamo, media, queue))["body"])

    assert body["result"]["status"] == "available"
    assert body["result"]["expires_at"] == _iso(NOW + timedelta(seconds=3600))


def test_link_ttl_is_read_from_configuration() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed(dynamo, **_completed(media))

    body = json.loads(_get(_api(dynamo, media, queue, env={"LINK_TTL_SECONDS": "60"}))["body"])

    assert body["result"]["expires_at"] == _iso(NOW + timedelta(seconds=60))
    presign = next(call for call in media.calls if call[0] == "presign_get")
    assert presign[1]["expires_in"] == 60


def test_download_filename_is_the_video_id_and_job_id() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed(dynamo, **_completed(media))

    body = json.loads(_get(_api(dynamo, media, queue))["body"])

    presign = next(call for call in media.calls if call[0] == "presign_get")
    assert presign[1]["filename"] == f"{VIDEO_ID}-{JOB_ID}.mp4"
    assert presign[1]["key"] == keys.media_key(JOB_ID, VIDEO_ID)
    assert presign[1]["expires_in"] == 3600
    assert TITLE not in presign[1]["filename"]
    assert TITLE not in body["result"]["download_url"]


def test_reissue_uses_the_same_signing_path() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed(dynamo, **_completed(media))

    response = _link(_api(dynamo, media, queue))

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert set(body) == {"job_id", "download_url", "expires_at", "size_bytes"}
    assert body["expires_at"] == _iso(NOW + timedelta(seconds=3600))
    assert body["size_bytes"] == len(MEDIA)
    assert f"{VIDEO_ID}-{JOB_ID}.mp4" in body["download_url"]


def test_link_body_shape() -> None:
    job = {"job_id": JOB_ID}

    body = routes.link_body(job, "https://example.invalid/signed", CREATED_AT, 42)

    assert body == {
        "job_id": JOB_ID,
        "download_url": "https://example.invalid/signed",
        "expires_at": CREATED_AT,
        "size_bytes": 42,
    }
