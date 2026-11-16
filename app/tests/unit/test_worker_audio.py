"""Unit tests for the worker's audio branch and the bounded video fallback (T021)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.extractor_fake import FakeExtractor
from tests.fakes.s3_fake import FakeMediaStore
from ytdl import errors, keys, states
from ytdl.config import Config, load_config
from ytdl.formats import NoUsableFormat
from ytdl.worker.handler import Worker
from ytdl.worker.pipeline import process_job

JOB_ID = "m3x9k2q1f7"
VIDEO_ID = "dQw4w9WgXcQ"
MESSAGE_ID = "7f1e6a3c-0000-4000-8000-000000000003"
CREATED_AT = "2026-11-15T14:03:11Z"
NOW_EPOCH = 1_763_213_000
MP3_BYTES = b"fake-mp3-bytes"

CONFIG: Config = load_config(
    {
        "ENVIRONMENT": "dev",
        "JOBS_TABLE": "ytdl-dev-jobs",
        "MEDIA_BUCKET": "ytdl-dev-media",
        "JOBS_QUEUE_URL": "https://sqs.eu-west-1.amazonaws.com/000000000000/ytdl-dev-jobs",
        "TOKEN_PARAM_NAME": "/ytdl/dev/api-token",
    }
)

AUDIO_METADATA: dict[str, Any] = {
    "id": VIDEO_ID,
    "title": "audio source",
    "duration": 212,
    "formats": [
        {
            "format_id": "137",
            "height": 1080,
            "vcodec": "avc1.640028",
            "acodec": "none",
            "ext": "mp4",
            "tbr": 4418.0,
        },
        {
            "format_id": "18",
            "height": 360,
            "vcodec": "avc1.42001E",
            "acodec": "mp4a.40.2",
            "ext": "mp4",
            "tbr": 400.0,
            "abr": 300.0,
        },
        {
            "format_id": "140",
            "height": None,
            "vcodec": "none",
            "acodec": "mp4a.40.2",
            "ext": "m4a",
            "tbr": 129.0,
            "abr": 128.0,
        },
        {
            "format_id": "251",
            "height": None,
            "vcodec": "none",
            "acodec": "opus",
            "ext": "webm",
            "tbr": 161.0,
            "abr": 160.0,
        },
    ],
}

MUXED_ONLY_METADATA: dict[str, Any] = {
    "id": VIDEO_ID,
    "title": "muxed only",
    "duration": 212,
    "formats": [
        {
            "format_id": "18",
            "height": 360,
            "vcodec": "avc1.42001E",
            "acodec": "mp4a.40.2",
            "ext": "mp4",
            "tbr": 400.0,
            "abr": 300.0,
        },
    ],
}

FALLBACK_METADATA: dict[str, Any] = {
    "id": VIDEO_ID,
    "title": "no low rendition",
    "duration": 212,
    "formats": [
        {
            "format_id": "136",
            "height": 720,
            "vcodec": "avc1.4d401f",
            "acodec": "none",
            "ext": "mp4",
            "tbr": 2200.0,
        },
        {
            "format_id": "135",
            "height": 480,
            "vcodec": "avc1.4d401f",
            "acodec": "none",
            "ext": "mp4",
            "tbr": 1101.0,
        },
        {
            "format_id": "140",
            "height": None,
            "vcodec": "none",
            "acodec": "mp4a.40.2",
            "ext": "m4a",
            "tbr": 129.0,
            "abr": 128.0,
        },
    ],
}


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


def _forbidden_remux(source: str, dest: str) -> None:
    raise AssertionError("an audio job must not run the video remux")


def _copy_source(source: str, dest: str) -> None:
    Path(dest).write_bytes(Path(source).read_bytes())


def _write_mp3(source: str, dest: str) -> None:
    Path(dest).write_bytes(MP3_BYTES)


def _workdir() -> str:
    return tempfile.mkdtemp(prefix="ytdl-audio-test-")


def _audio_probe(path: str) -> dict[str, Any]:
    return {"height": None, "vcodec": None, "acodec": "mp3"}


def _fallback_probe(path: str) -> dict[str, Any]:
    return {"height": 480, "vcodec": "avc1.4d401f", "acodec": "mp4a.40.2"}


def _audio_job(**extra: Any) -> dict[str, Any]:
    job: dict[str, Any] = {
        "job_id": JOB_ID,
        "video_id": VIDEO_ID,
        "media_kind": "audio",
        "requested_height": None,
    }
    job.update(extra)
    return job


def _record(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "messageId": MESSAGE_ID,
        "receiptHandle": "receipt-1",
        "body": json.dumps(body),
        "attributes": {"ApproximateReceiveCount": "1"},
        "messageAttributes": {},
    }


def _event() -> dict[str, Any]:
    message = {"version": 1, "job_id": JOB_ID, "video_id": VIDEO_ID}
    return {"Records": [_record(message)]}


def _seed_job(dynamo: FakeDynamoRepository, **extra: Any) -> None:
    job: dict[str, Any] = {
        "job_id": JOB_ID,
        "state": states.ACCEPTED,
        "source_host": "www.youtube.com",
        "video_id": VIDEO_ID,
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
    }
    job.update(extra)
    dynamo.create_job_with_rate_increment(
        job,
        keys.rate_key(keys.rate_window(NOW_EPOCH), "test-token"),
        CONFIG.rate_limit_per_hour,
        NOW_EPOCH,
    )
    dynamo.calls.clear()


def _call(dynamo: FakeDynamoRepository, name: str) -> list[dict[str, Any]]:
    return [payload for method, payload in dynamo.calls if method == name]


def test_an_audio_job_downloads_one_audio_stream_and_uploads_an_mp3(tmp_path: Any) -> None:
    store = FakeMediaStore()
    extractor = FakeExtractor(metadata=AUDIO_METADATA)
    converted: list[tuple[str, str]] = []

    def mp3_fn(source: str, dest: str) -> None:
        converted.append((source, dest))
        _write_mp3(source, dest)

    fields = process_job(
        _audio_job(),
        store=store,
        extractor=extractor,
        config=CONFIG,
        workdir=str(tmp_path),
        remux_fn=_forbidden_remux,
        mp3_fn=mp3_fn,
        probe_fn=_audio_probe,
        now=lambda: NOW_EPOCH,
    )

    assert [name for name, _ in extractor.calls] == ["probe", "download"]
    assert extractor.calls[1][1]["format_selector"] == "251"

    assert len(converted) == 1
    assert converted[0][1] == str(tmp_path / "output.mp3")

    media_key = f"output/{JOB_ID}/{VIDEO_ID}.mp3"
    assert fields["media_key"] == media_key
    assert fields["media_key"].endswith(".mp3")
    assert fields["produced_quality"] == "mp3"
    assert fields["media_kind"] == "audio"
    assert "produced_height" not in fields
    assert "produced_vcodec" not in fields
    assert fields["produced_acodec"] == "mp3"
    assert fields["media_size_bytes"] == len(MP3_BYTES)
    assert fields["media_created_at"] == NOW_EPOCH
    assert fields["media_expires_at"] == NOW_EPOCH + 604_800
    assert fields["duration_seconds"] == 212

    assert len(store.writes) == 1
    upload = store.writes[0][1]
    assert upload["key"] == media_key
    assert upload["content_type"] == "audio/mpeg"
    assert upload["metadata"] == {
        "job-id": JOB_ID,
        "source-video-id": VIDEO_ID,
        "media-kind": "audio",
    }
    assert upload["size"] == len(MP3_BYTES)


def test_audio_without_an_audio_only_stream_fails_before_the_download(tmp_path: Any) -> None:
    store = FakeMediaStore()
    extractor = FakeExtractor(metadata=MUXED_ONLY_METADATA)

    with pytest.raises(NoUsableFormat):
        process_job(
            _audio_job(),
            store=store,
            extractor=extractor,
            config=CONFIG,
            workdir=str(tmp_path),
            remux_fn=_forbidden_remux,
            mp3_fn=_write_mp3,
            probe_fn=_audio_probe,
        )

    assert [name for name, _ in extractor.calls] == ["probe"]
    assert store.writes == []


def test_audio_without_an_audio_only_stream_is_unsupported_media() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(metadata=MUXED_ONLY_METADATA)
    _seed_job(dynamo, media_kind="audio")
    worker = Worker(
        dynamo,
        media,
        extractor,
        CONFIG,
        remux_fn=_forbidden_remux,
        probe_fn=_audio_probe,
        mp3_fn=_write_mp3,
        workdir_factory=_workdir,
    )

    result = worker.handle(_event())

    assert result == {"batchItemFailures": []}
    assert [name for name, _ in extractor.calls] == ["probe"]
    failures = _call(dynamo, "fail_job")
    assert len(failures) == 1
    assert failures[0]["category"] == errors.UNSUPPORTED_MEDIA
    assert failures[0]["retryable"] is False
    assert media.writes == []


def test_an_audio_retry_reuses_the_stored_mp3_and_reports_mp3() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(metadata=AUDIO_METADATA)
    _seed_job(dynamo, media_kind="audio")
    media_key = f"output/{JOB_ID}/{VIDEO_ID}.mp3"
    media.seed(
        media_key,
        MP3_BYTES,
        content_type="audio/mpeg",
        metadata={"job-id": JOB_ID, "source-video-id": VIDEO_ID, "media-kind": "audio"},
    )
    worker = Worker(
        dynamo,
        media,
        extractor,
        CONFIG,
        remux_fn=_forbidden_remux,
        probe_fn=_audio_probe,
        mp3_fn=_write_mp3,
        workdir_factory=_workdir,
    )

    result = worker.handle(_event())

    assert result == {"batchItemFailures": []}
    assert extractor.calls == []
    assert ("head_object", {"key": media_key}) in media.calls
    completions = _call(dynamo, "complete_job")
    assert len(completions) == 1
    fields = completions[0]["fields"]
    assert fields["media_key"] == media_key
    assert fields["produced_quality"] == "mp3"
    assert "produced_height" not in fields
    assert media.writes == []


def test_a_video_job_on_a_missing_tier_completes_at_the_fallback_height(
    tmp_path: Any,
) -> None:
    store = FakeMediaStore()
    extractor = FakeExtractor(metadata=FALLBACK_METADATA)

    fields = process_job(
        {"job_id": JOB_ID, "video_id": VIDEO_ID, "requested_height": 240},
        store=store,
        extractor=extractor,
        config=CONFIG,
        workdir=str(tmp_path),
        remux_fn=_copy_source,
        probe_fn=_fallback_probe,
        now=lambda: NOW_EPOCH,
    )

    assert [name for name, _ in extractor.calls] == ["probe", "download"]
    assert extractor.calls[1][1]["format_selector"] == "135+140"

    media_key = f"output/{JOB_ID}/{VIDEO_ID}.mp4"
    assert fields["media_key"] == media_key
    assert fields["produced_height"] == 480
    assert fields["produced_quality"] == "480p"

    upload = store.writes[0][1]
    assert upload["key"] == media_key
    assert upload["content_type"] == "video/mp4"
    assert upload["metadata"] == {
        "job-id": JOB_ID,
        "produced-height": "480",
        "source-video-id": VIDEO_ID,
        "media-kind": "video",
    }
