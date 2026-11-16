"""Contract tests for the US1 worker happy path (T047)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from typing import Any

import pytest

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.extractor_fake import FakeExtractor
from tests.fakes.s3_fake import FakeMediaStore
from ytdl import keys, states
from ytdl.config import Config, load_config
from ytdl.worker.handler import Worker
from ytdl.worker.pipeline import format_selector, quality_label

JOB_ID = "m3x9k2q1f7"
VIDEO_ID = "dQw4w9WgXcQ"
MESSAGE_ID = "7f1e6a3c-0000-4000-8000-000000000001"
ENQUEUED_AT = "2026-11-15T14:03:11Z"
NOW_EPOCH = 1_763_213_000
MEDIA_KEY = f"output/{JOB_ID}/{VIDEO_ID}.mp4"
CONTENT = b"fake-mp4-bytes"

CONFIG: Config = load_config(
    {
        "ENVIRONMENT": "dev",
        "JOBS_TABLE": "ytdl-dev-jobs",
        "MEDIA_BUCKET": "ytdl-dev-media",
        "JOBS_QUEUE_URL": "https://sqs.eu-west-1.amazonaws.com/000000000000/ytdl-dev-jobs",
        "TOKEN_PARAM_NAME": "/ytdl/dev/api-token",
    }
)

MESSAGE = {
    "version": 1,
    "job_id": JOB_ID,
    "video_id": VIDEO_ID,
    "source_host": "www.youtube.com",
    "requested_height": None,
    "enqueued_at": ENQUEUED_AT,
}

PROBE_RESULT = {"height": 1080, "vcodec": "avc1.640028", "acodec": "mp4a.40.2"}


class RecordingLogger:
    """Collects the structured log calls the worker emits."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str, dict[str, Any]]] = []

    def info(self, event: str, **fields: Any) -> None:
        self.lines.append(("info", event, fields))

    def warning(self, event: str, **fields: Any) -> None:
        self.lines.append(("warning", event, fields))

    def error(self, event: str, **fields: Any) -> None:
        self.lines.append(("error", event, fields))


def _copy_remux(source: str, dest: str) -> None:
    shutil.copyfile(source, dest)


def _fixed_probe(path: str) -> dict[str, Any]:
    return dict(PROBE_RESULT)


def _seed_job(dynamo: FakeDynamoRepository, state: str = states.ACCEPTED) -> None:
    dynamo.create_job_with_rate_increment(
        {
            "job_id": JOB_ID,
            "state": state,
            "source_host": "www.youtube.com",
            "video_id": VIDEO_ID,
            "created_at": "2026-11-15T14:03:11Z",
            "updated_at": "2026-11-15T14:03:11Z",
        },
        keys.rate_key(keys.rate_window(NOW_EPOCH), "test-token"),
        CONFIG.rate_limit_per_hour,
        NOW_EPOCH,
    )
    dynamo.calls.clear()
    if state != states.ACCEPTED:
        dynamo.items["JOB#" + JOB_ID]["state"] = state


def _record(body: str | dict[str, Any] = MESSAGE, message_id: str = MESSAGE_ID) -> dict[str, Any]:
    payload = body if isinstance(body, str) else json.dumps(body)
    return {
        "messageId": message_id,
        "receiptHandle": "receipt-1",
        "body": payload,
        "attributes": {"ApproximateReceiveCount": "1"},
        "messageAttributes": {},
    }


def _event(record: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"Records": [record if record is not None else _record()]}


def _build_worker(
    dynamo: FakeDynamoRepository,
    media: FakeMediaStore,
    extractor: FakeExtractor,
    *,
    logger: RecordingLogger | None = None,
    created: list[str] | None = None,
) -> Worker:
    workdirs = created if created is not None else []

    def factory() -> str:
        path = tempfile.mkdtemp(prefix="ytdl-worker-test-")
        workdirs.append(path)
        return path

    return Worker(
        dynamo,
        media,
        extractor,
        CONFIG,
        remux_fn=_copy_remux,
        probe_fn=_fixed_probe,
        logger=logger,
        workdir_factory=factory,
    )


def _call(dynamo: FakeDynamoRepository, name: str) -> list[dict[str, Any]]:
    return [payload for method, payload in dynamo.calls if method == name]


def test_happy_path_downloads_uploads_and_completes() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(content=CONTENT)
    _seed_job(dynamo)
    created: list[str] = []
    logger = RecordingLogger()
    worker = _build_worker(dynamo, media, extractor, logger=logger, created=created)

    result = worker.handle(_event())

    assert result == {"batchItemFailures": []}

    claims = _call(dynamo, "claim_job")
    assert len(claims) == 1
    assert claims[0]["job_id"] == JOB_ID
    assert dynamo.items["JOB#" + JOB_ID]["state"] == states.COMPLETED
    assert dynamo.items["JOB#" + JOB_ID]["attempt"] == 1

    assert [name for name, _ in extractor.calls] == ["probe", "download"]
    source_url = f"https://www.youtube.com/watch?v={VIDEO_ID}"
    assert extractor.calls[0][1]["url"] == source_url
    assert extractor.calls[1][1]["url"] == source_url
    assert extractor.calls[1][1]["out_dir"] == created[0]

    assert len(media.writes) == 1
    upload = media.writes[0][1]
    assert upload["key"] == MEDIA_KEY
    assert upload["content_type"] == "video/mp4"
    assert set(upload["metadata"]) == {"job-id", "produced-height", "source-video-id"}
    assert upload["metadata"]["job-id"] == JOB_ID
    assert upload["metadata"]["produced-height"] == "1080"
    assert upload["metadata"]["source-video-id"] == VIDEO_ID
    assert upload["size"] == len(CONTENT)

    completions = _call(dynamo, "complete_job")
    assert len(completions) == 1
    assert completions[0]["job_id"] == JOB_ID
    fields = completions[0]["fields"]
    assert fields["media_key"] == MEDIA_KEY
    assert fields["media_size_bytes"] == len(CONTENT)
    assert fields["media_expires_at"] == fields["media_created_at"] + 604_800
    assert fields["produced_height"] == 1080
    assert fields["produced_quality"] == "1080p"
    assert fields["produced_vcodec"] == "avc1.640028"
    assert fields["produced_acodec"] == "mp4a.40.2"
    assert fields["duration_seconds"] == 212
    assert fields["processing_ms"] >= 0

    assert [event for _, event, _ in logger.lines] == ["job_started", "job_completed"]
    assert not os.path.exists(created[0])


def test_completion_without_duration_metadata_leaves_the_field_unset() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(metadata={"id": VIDEO_ID, "title": "no duration"}, content=CONTENT)
    _seed_job(dynamo)
    worker = _build_worker(dynamo, media, extractor)

    assert worker.handle(_event()) == {"batchItemFailures": []}

    fields = _call(dynamo, "complete_job")[0]["fields"]
    assert "duration_seconds" not in fields


def test_missing_job_is_acknowledged_without_claiming() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor()
    worker = _build_worker(dynamo, media, extractor)

    assert worker.handle(_event()) == {"batchItemFailures": []}

    assert _call(dynamo, "claim_job") == []
    assert extractor.calls == []
    assert media.writes == []


def test_already_completed_job_is_acknowledged_without_downloading() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor()
    _seed_job(dynamo, state=states.COMPLETED)
    worker = _build_worker(dynamo, media, extractor)

    assert worker.handle(_event()) == {"batchItemFailures": []}

    assert _call(dynamo, "claim_job") == []
    assert extractor.calls == []
    assert media.writes == []


def test_unclaimable_job_is_acknowledged(monkeypatch: pytest.MonkeyPatch) -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor()
    _seed_job(dynamo)
    worker = _build_worker(dynamo, media, extractor)
    monkeypatch.setattr(dynamo, "claim_job", lambda *args, **kwargs: None)

    assert worker.handle(_event()) == {"batchItemFailures": []}

    assert extractor.calls == []
    assert media.writes == []


def test_pipeline_failure_returns_the_message_id_and_does_not_complete() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(download_error=RuntimeError("download exploded"))
    _seed_job(dynamo)
    created: list[str] = []
    worker = _build_worker(dynamo, media, extractor, created=created)

    result = worker.handle(_event())

    assert result == {"batchItemFailures": [{"itemIdentifier": MESSAGE_ID}]}
    assert _call(dynamo, "complete_job") == []
    assert media.writes == []
    assert not os.path.exists(created[0])


def test_invalid_json_is_a_batch_item_failure() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor()
    worker = _build_worker(dynamo, media, extractor)

    result = worker.handle(_event(_record(body="{not json")))

    assert result == {"batchItemFailures": [{"itemIdentifier": MESSAGE_ID}]}
    assert extractor.calls == []


def test_unknown_version_is_a_batch_item_failure() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor()
    worker = _build_worker(dynamo, media, extractor)
    message = {**MESSAGE, "version": 2}

    result = worker.handle(_event(_record(body=message)))

    assert result == {"batchItemFailures": [{"itemIdentifier": MESSAGE_ID}]}
    assert extractor.calls == []


@pytest.mark.parametrize(
    ("height", "expected"),
    [
        (360, "360p"),
        (480, "480p"),
        (720, "720p"),
        (1080, "1080p"),
        (481, "480p"),
        (1079, "720p"),
        (2160, "1080p"),
        (240, None),
        (None, None),
    ],
)
def test_quality_label_never_rounds_up(height: int | None, expected: str | None) -> None:
    assert quality_label(height) == expected


def test_format_selector_is_the_fixed_us1_selector() -> None:
    assert format_selector(1080) == (
        "bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/b[height<=1080]/b"
    )
