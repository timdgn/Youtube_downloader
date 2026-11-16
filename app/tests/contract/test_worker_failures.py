"""Contract tests for the worker failure, redelivery, and idempotency rules (T064)."""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from dataclasses import replace
from typing import Any

import pytest

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.extractor_fake import FakeExtractor
from tests.fakes.s3_fake import FakeMediaStore
from ytdl import errors, keys, states
from ytdl.adapters.extractor import ExtractionError
from ytdl.config import Config, load_config
from ytdl.worker.handler import Worker
from ytdl.worker.pipeline import DeadlineExceeded, LimitExceeded, process_job

JOB_ID = "m3x9k2q1f7"
VIDEO_ID = "dQw4w9WgXcQ"
MESSAGE_ID = "7f1e6a3c-0000-4000-8000-000000000002"
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

TIMEOUT_STDERR = "ERROR: Read timed out. (read timeout=30)"
PRIVATE_STDERR = "ERROR: Private video. Sign in if you have been granted access."


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
    return {"height": 1080, "vcodec": "avc1.640028", "acodec": "mp4a.40.2"}


def _seed_job(
    dynamo: FakeDynamoRepository,
    *,
    state: str = states.ACCEPTED,
    attempt: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    job: dict[str, Any] = {
        "job_id": JOB_ID,
        "state": state,
        "source_host": "www.youtube.com",
        "video_id": VIDEO_ID,
        "created_at": ENQUEUED_AT,
        "updated_at": ENQUEUED_AT,
    }
    if attempt is not None:
        job["attempt"] = attempt
    if extra:
        job.update(extra)
    dynamo.create_job_with_rate_increment(
        job,
        keys.rate_key(keys.rate_window(NOW_EPOCH), "test-token"),
        CONFIG.rate_limit_per_hour,
        NOW_EPOCH,
    )
    dynamo.calls.clear()


def _record(body: str | dict[str, Any] = MESSAGE) -> dict[str, Any]:
    payload = body if isinstance(body, str) else json.dumps(body)
    return {
        "messageId": MESSAGE_ID,
        "receiptHandle": "receipt-1",
        "body": payload,
        "attributes": {"ApproximateReceiveCount": "1"},
        "messageAttributes": {},
    }


def _event(record: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"Records": [record if record is not None else _record()]}


def _worker(
    dynamo: FakeDynamoRepository,
    media: FakeMediaStore,
    extractor: FakeExtractor,
    logger: RecordingLogger | None = None,
) -> Worker:
    def factory() -> str:
        return tempfile.mkdtemp(prefix="ytdl-worker-failure-test-")

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


# --- classified failures -----------------------------------------------------


def test_a_retryable_extraction_error_at_attempt_one_is_redelivered() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(download_error=ExtractionError(stderr=TIMEOUT_STDERR, returncode=1))
    _seed_job(dynamo)
    logger = RecordingLogger()

    result = _worker(dynamo, media, extractor, logger).handle(_event())

    assert result == {"batchItemFailures": [{"itemIdentifier": MESSAGE_ID}]}
    failures = _call(dynamo, "fail_job")
    assert len(failures) == 1
    assert failures[0]["category"] == errors.UPSTREAM_PROBLEM
    assert failures[0]["retryable"] is True
    assert _call(dynamo, "complete_job") == []
    assert media.writes == []

    events = [event for _, event, _ in logger.lines]
    assert events == ["job_started", "job_failed"]
    audit = logger.lines[-1][2]
    assert audit["outcome"] == errors.UPSTREAM_PROBLEM
    assert audit["attempt"] == 1
    assert audit["job_id"] == JOB_ID
    assert set(audit) == {
        "job_id",
        "source_host",
        "video_id",
        "requested_quality",
        "produced_quality",
        "outcome",
        "processing_ms",
        "media_size_bytes",
        "attempt",
    }


def test_the_same_retryable_error_at_attempt_two_is_terminal() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(download_error=ExtractionError(stderr=TIMEOUT_STDERR, returncode=1))
    _seed_job(dynamo, attempt=1)

    result = _worker(dynamo, media, extractor).handle(_event())

    assert result == {"batchItemFailures": []}
    failures = _call(dynamo, "fail_job")
    assert len(failures) == 1
    assert failures[0]["category"] == errors.UPSTREAM_PROBLEM
    assert failures[0]["retryable"] is True
    assert dynamo.items["JOB#" + JOB_ID]["attempt"] == 2
    assert dynamo.items["JOB#" + JOB_ID]["state"] == states.FAILED


def test_a_non_retryable_failure_is_recorded_and_acknowledged() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(download_error=ExtractionError(stderr=PRIVATE_STDERR, returncode=1))
    _seed_job(dynamo)

    result = _worker(dynamo, media, extractor).handle(_event())

    assert result == {"batchItemFailures": []}
    failures = _call(dynamo, "fail_job")
    assert len(failures) == 1
    assert failures[0]["category"] == errors.UNSUPPORTED_MEDIA
    assert failures[0]["retryable"] is False


def test_a_two_hour_video_is_refused_before_any_download() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(metadata={"id": VIDEO_ID, "duration": 7200})
    _seed_job(dynamo)

    result = _worker(dynamo, media, extractor).handle(_event())

    assert result == {"batchItemFailures": []}
    assert [name for name, _ in extractor.calls] == ["probe"]
    failures = _call(dynamo, "fail_job")
    assert len(failures) == 1
    assert failures[0]["category"] == errors.LIMIT_EXCEEDED
    assert failures[0]["retryable"] is False
    assert media.writes == []


def test_missing_duration_metadata_is_unsupported_media() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(metadata={"id": VIDEO_ID, "title": "no duration"})
    _seed_job(dynamo)

    result = _worker(dynamo, media, extractor).handle(_event())

    assert result == {"batchItemFailures": []}
    assert [name for name, _ in extractor.calls] == ["probe"]
    failures = _call(dynamo, "fail_job")
    assert len(failures) == 1
    assert failures[0]["category"] == errors.UNSUPPORTED_MEDIA
    assert failures[0]["retryable"] is False


def test_no_usable_format_is_unsupported_media() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(metadata={"id": VIDEO_ID, "duration": 212, "formats": []})
    _seed_job(dynamo)

    result = _worker(dynamo, media, extractor).handle(_event())

    assert result == {"batchItemFailures": []}
    assert [name for name, _ in extractor.calls] == ["probe"]
    failures = _call(dynamo, "fail_job")
    assert len(failures) == 1
    assert failures[0]["category"] == errors.UNSUPPORTED_MEDIA
    assert failures[0]["retryable"] is False


def test_a_missed_deadline_is_an_internal_error() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    _seed_job(dynamo)

    result = _worker(dynamo, media, _DeadlineExtractor()).handle(_event())

    assert result == {"batchItemFailures": [{"itemIdentifier": MESSAGE_ID}]}
    failures = _call(dynamo, "fail_job")
    assert len(failures) == 1
    assert failures[0]["category"] == errors.INTERNAL_ERROR
    assert failures[0]["retryable"] is True


def test_a_failing_fail_job_redelivers_the_message() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(download_error=ExtractionError(stderr=PRIVATE_STDERR, returncode=1))
    _seed_job(dynamo)

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("table unavailable")

    dynamo.fail_job = refuse  # type: ignore[method-assign]

    result = _worker(dynamo, media, extractor).handle(_event())

    assert result == {"batchItemFailures": [{"itemIdentifier": MESSAGE_ID}]}


class _DeadlineExtractor(FakeExtractor):
    """Raises the pipeline's own deadline error instead of probing."""

    def probe(self, url: str, extra_args: Any = ()) -> dict[str, Any]:
        raise DeadlineExceeded("soft deadline passed")


# --- idempotency -------------------------------------------------------------


def test_an_existing_object_is_reused_without_downloading() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor()
    _seed_job(dynamo)
    media.seed(MEDIA_KEY, CONTENT, metadata={"produced-height": "720"})
    before = int(time.time())

    result = _worker(dynamo, media, extractor).handle(_event())

    assert result == {"batchItemFailures": []}
    assert extractor.calls == []
    completions = _call(dynamo, "complete_job")
    assert len(completions) == 1
    fields = completions[0]["fields"]
    assert fields["media_key"] == MEDIA_KEY
    assert fields["media_size_bytes"] == len(CONTENT)
    assert fields["media_created_at"] >= before
    assert fields["media_expires_at"] == fields["media_created_at"] + 604_800
    assert fields["produced_height"] == 720
    assert fields["produced_quality"] == "720p"
    assert fields["processing_ms"] >= 0
    assert media.writes == []


def test_a_second_delivery_with_an_existing_object_writes_nothing() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor()
    _seed_job(dynamo, attempt=1)
    media.seed(MEDIA_KEY, CONTENT)

    result = _worker(dynamo, media, extractor).handle(_event())

    assert result == {"batchItemFailures": []}
    assert extractor.calls == []
    assert media.writes == []
    assert len(_call(dynamo, "complete_job")) == 1
    assert dynamo.items["JOB#" + JOB_ID]["attempt"] == 2


def test_an_empty_object_does_not_skip_the_download() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(content=CONTENT)
    _seed_job(dynamo)
    media.seed(MEDIA_KEY, b"")

    assert _worker(dynamo, media, extractor).handle(_event()) == {"batchItemFailures": []}

    assert [name for name, _ in extractor.calls] == ["probe", "download"]


# --- acknowledged messages ---------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("{not json", id="malformed-json"),
        pytest.param("[]", id="not-an-object"),
        pytest.param(json.dumps({"version": 9, "job_id": JOB_ID}), id="unknown-version"),
        pytest.param(json.dumps({"version": 1}), id="no-job-id"),
        pytest.param(json.dumps({**MESSAGE, "version": 2}), id="version-2"),
    ],
)
def test_an_untrustworthy_message_is_acknowledged_without_a_write(body: str) -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor()
    _seed_job(dynamo)
    logger = RecordingLogger()

    result = _worker(dynamo, media, extractor, logger).handle(_event(_record(body)))

    assert result == {"batchItemFailures": []}
    assert _call(dynamo, "claim_job") == []
    assert dynamo.writes == []
    assert extractor.calls == []
    assert media.writes == []
    assert [event for _, event, _ in logger.lines] == ["malformed_message"]
    assert logger.lines[0][0] == "error"


def test_a_missing_job_record_is_acknowledged_without_claiming() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor()

    result = _worker(dynamo, media, extractor).handle(_event())

    assert result == {"batchItemFailures": []}
    assert _call(dynamo, "claim_job") == []
    assert extractor.calls == []


def test_an_already_completed_job_is_acknowledged_without_claiming() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor()
    _seed_job(dynamo, state=states.COMPLETED)

    result = _worker(dynamo, media, extractor).handle(_event())

    assert result == {"batchItemFailures": []}
    assert _call(dynamo, "claim_job") == []
    assert extractor.calls == []
    assert media.writes == []


def test_an_already_failed_non_retryable_job_is_acknowledged_without_claiming() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor()
    _seed_job(dynamo, state=states.FAILED)
    dynamo.items["JOB#" + JOB_ID]["failure_retryable"] = False

    result = _worker(dynamo, media, extractor).handle(_event())

    assert result == {"batchItemFailures": []}
    assert _call(dynamo, "claim_job") == []
    assert extractor.calls == []


def test_an_unclaimable_job_is_acknowledged_without_downloading() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor()
    _seed_job(dynamo)
    dynamo.claim_job = lambda *args, **kwargs: None  # type: ignore[method-assign]

    result = _worker(dynamo, media, extractor).handle(_event())

    assert result == {"batchItemFailures": []}
    assert extractor.calls == []
    assert media.writes == []


# --- the size guards ---------------------------------------------------------


def test_an_oversized_estimate_is_refused_before_the_download() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    metadata = {
        "id": VIDEO_ID,
        "duration": 212,
        "formats": [
            {
                "format_id": "137",
                "height": 1080,
                "vcodec": "avc1.640028",
                "acodec": "none",
                "ext": "mp4",
                "filesize_approx": 5_000_000_000,
            },
            {
                "format_id": "140",
                "height": None,
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "ext": "m4a",
                "filesize": 1_000_000,
            },
        ],
    }
    extractor = FakeExtractor(metadata=metadata)
    _seed_job(dynamo)

    result = _worker(dynamo, media, extractor).handle(_event())

    assert result == {"batchItemFailures": []}
    assert [name for name, _ in extractor.calls] == ["probe"]
    failures = _call(dynamo, "fail_job")
    assert failures[0]["category"] == errors.LIMIT_EXCEEDED
    assert failures[0]["retryable"] is False
    assert media.writes == []


def test_an_oversized_output_is_refused_before_the_upload(tmp_path: Any) -> None:
    class _RefusingStore:
        def put_file(self, *args: Any, **kwargs: Any) -> int:
            raise AssertionError("the upload must not happen")

    config = replace(CONFIG, max_output_bytes=1024)

    with pytest.raises(LimitExceeded) as caught:
        process_job(
            {"job_id": JOB_ID, "video_id": VIDEO_ID},
            store=_RefusingStore(),
            extractor=FakeExtractor(content=b"x" * 4096),
            config=config,
            workdir=str(tmp_path),
            remux_fn=_copy_remux,
            probe_fn=_fixed_probe,
        )

    assert caught.value.failure.category == errors.LIMIT_EXCEEDED
    assert caught.value.failure.retryable is False


# --- redelivery of a retryable failure ---------------------------------------


def test_retryable_failed_job_is_claimed_again_and_completed() -> None:
    """A failed job with failure_retryable true and attempt 1 may run once more."""
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor()
    _seed_job(
        dynamo,
        state=states.FAILED,
        attempt=1,
        extra={
            "failure_category": errors.UPSTREAM_PROBLEM,
            "failure_message": errors.MESSAGES[errors.UPSTREAM_PROBLEM],
            "failure_retryable": True,
        },
    )

    result = _worker(dynamo, media, extractor).handle(_event())

    assert result == {"batchItemFailures": []}
    assert len(_call(dynamo, "claim_job")) == 1
    assert extractor.calls, "the retried job must actually re-run extraction"
    assert len(_call(dynamo, "complete_job")) == 1
    assert dynamo.items["JOB#" + JOB_ID]["state"] == states.COMPLETED


def test_non_retryable_failed_job_is_acknowledged_without_reprocessing() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor()
    _seed_job(
        dynamo,
        state=states.FAILED,
        attempt=1,
        extra={
            "failure_category": errors.UNSUPPORTED_MEDIA,
            "failure_message": errors.MESSAGES[errors.UNSUPPORTED_MEDIA],
            "failure_retryable": False,
        },
    )

    result = _worker(dynamo, media, extractor).handle(_event())

    assert result == {"batchItemFailures": []}
    assert _call(dynamo, "claim_job") == []
    assert extractor.calls == []
