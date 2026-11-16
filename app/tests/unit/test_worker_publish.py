"""Unit tests for the worker's terminal job-event publish (T022, T037)."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import replace
from typing import Any

import pytest

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.extractor_fake import FakeExtractor
from tests.fakes.s3_fake import FakeMediaStore
from ytdl import keys, states
from ytdl.adapters.extractor import ExtractionError
from ytdl.config import Config, load_config
from ytdl.worker import handler
from ytdl.worker.handler import Worker

JOB_ID = "m3x9k2q1f7"
VIDEO_ID = "dQw4w9WgXcQ"
MESSAGE_ID = "7f1e6a3c-0000-4000-8000-000000000004"
CHAT_ID = 812345678
CREATED_AT = "2026-11-15T14:03:11Z"
NOW_EPOCH = 1_763_213_000
CONTENT = b"fake-mp4-bytes"
TOPIC_ARN = "arn:aws:sns:eu-west-3:000000000000:ytdl-dev-job-events"

CONFIG: Config = load_config(
    {
        "ENVIRONMENT": "dev",
        "JOBS_TABLE": "ytdl-dev-jobs",
        "MEDIA_BUCKET": "ytdl-dev-media",
        "JOBS_QUEUE_URL": "https://sqs.eu-west-1.amazonaws.com/000000000000/ytdl-dev-jobs",
        "TOKEN_PARAM_NAME": "/ytdl/dev/api-token",
    }
)

MESSAGE = {"version": 1, "job_id": JOB_ID, "video_id": VIDEO_ID}

PRIVATE_STDERR = "ERROR: Private video. Sign in if you have been granted access."
TIMEOUT_STDERR = "ERROR: Read timed out. (read timeout=30)"


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


class RecordingPublisher:
    """Records every publish call and can refuse on demand."""

    def __init__(self, error: Exception | None = None, order: list[str] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.error = error
        self._order = order

    def __call__(self, event: str, job_id: str) -> None:
        self.calls.append((event, job_id))
        if self._order is not None:
            self._order.append(f"publish:{event}")
        if self.error is not None:
            raise self.error


def _copy_remux(source: str, dest: str) -> None:
    shutil.copyfile(source, dest)


def _fixed_probe(path: str) -> dict[str, Any]:
    return {"height": 1080, "vcodec": "avc1.640028", "acodec": "mp4a.40.2"}


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


def _record() -> dict[str, Any]:
    return {
        "messageId": MESSAGE_ID,
        "receiptHandle": "receipt-1",
        "body": json.dumps(MESSAGE),
        "attributes": {"ApproximateReceiveCount": "1"},
        "messageAttributes": {},
    }


def _event() -> dict[str, Any]:
    return {"Records": [_record()]}


def _worker(
    dynamo: FakeDynamoRepository,
    media: FakeMediaStore,
    extractor: FakeExtractor,
    publish: RecordingPublisher | None,
    logger: RecordingLogger | None = None,
) -> Worker:
    def factory() -> str:
        return tempfile.mkdtemp(prefix="ytdl-worker-publish-test-")

    return Worker(
        dynamo,
        media,
        extractor,
        CONFIG,
        remux_fn=_copy_remux,
        probe_fn=_fixed_probe,
        logger=logger,
        workdir_factory=factory,
        publish=publish,
    )


class _RecordingSns:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def publish(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _sns_client(name: str, sns: _RecordingSns) -> Any:
    return sns if name == "sns" else object()


def test_a_completed_chat_job_publishes_job_completed_after_complete_job() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(content=CONTENT)
    _seed_job(dynamo, chat_id=CHAT_ID)
    order: list[str] = []
    publisher = RecordingPublisher(order=order)
    complete = dynamo.complete_job

    def recorded_complete(*args: Any, **kwargs: Any) -> None:
        order.append("complete_job")
        complete(*args, **kwargs)

    dynamo.complete_job = recorded_complete  # type: ignore[method-assign]

    result = _worker(dynamo, media, extractor, publisher).handle(_event())

    assert result == {"batchItemFailures": []}
    assert publisher.calls == [("job_completed", JOB_ID)]
    assert order == ["complete_job", "publish:job_completed"]
    assert dynamo.items["JOB#" + JOB_ID]["state"] == states.COMPLETED


def test_a_terminal_failure_publishes_job_failed_after_fail_job() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(download_error=ExtractionError(stderr=PRIVATE_STDERR, returncode=1))
    _seed_job(dynamo, chat_id=CHAT_ID)
    order: list[str] = []
    publisher = RecordingPublisher(order=order)
    fail = dynamo.fail_job

    def recorded_fail(*args: Any, **kwargs: Any) -> None:
        order.append("fail_job")
        fail(*args, **kwargs)

    dynamo.fail_job = recorded_fail  # type: ignore[method-assign]

    result = _worker(dynamo, media, extractor, publisher).handle(_event())

    assert result == {"batchItemFailures": []}
    assert publisher.calls == [("job_failed", JOB_ID)]
    assert order == ["fail_job", "publish:job_failed"]
    assert dynamo.items["JOB#" + JOB_ID]["state"] == states.FAILED


def test_a_retryable_failure_publishes_nothing() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(download_error=ExtractionError(stderr=TIMEOUT_STDERR, returncode=1))
    _seed_job(dynamo, chat_id=CHAT_ID)
    publisher = RecordingPublisher()

    result = _worker(dynamo, media, extractor, publisher).handle(_event())

    assert result == {"batchItemFailures": [{"itemIdentifier": MESSAGE_ID}]}
    assert publisher.calls == []


def test_a_job_without_chat_id_publishes_nothing() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(content=CONTENT)
    _seed_job(dynamo)
    publisher = RecordingPublisher()

    result = _worker(dynamo, media, extractor, publisher).handle(_event())

    assert result == {"batchItemFailures": []}
    assert publisher.calls == []
    assert dynamo.items["JOB#" + JOB_ID]["state"] == states.COMPLETED


def test_an_empty_chat_id_publishes_nothing() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(content=CONTENT)
    _seed_job(dynamo, chat_id="")
    publisher = RecordingPublisher()

    assert _worker(dynamo, media, extractor, publisher).handle(_event()) == {
        "batchItemFailures": []
    }

    assert publisher.calls == []


def test_a_publish_error_is_logged_and_does_not_change_the_outcome() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(content=CONTENT)
    _seed_job(dynamo, chat_id=CHAT_ID)
    logger = RecordingLogger()
    publisher = RecordingPublisher(error=RuntimeError("sns unavailable"))

    result = _worker(dynamo, media, extractor, publisher, logger).handle(_event())

    assert result == {"batchItemFailures": []}
    assert publisher.calls == [("job_completed", JOB_ID)]
    assert dynamo.items["JOB#" + JOB_ID]["state"] == states.COMPLETED

    errors_logged = [(event, fields) for level, event, fields in logger.lines if level == "error"]
    assert [event for event, _ in errors_logged] == ["job_event_publish_failed"]
    assert errors_logged[0][1]["message"] == "RuntimeError"
    assert str(CHAT_ID) not in json.dumps([fields for _, fields in errors_logged])


def test_a_publish_error_during_a_terminal_failure_keeps_the_batch_response() -> None:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    extractor = FakeExtractor(download_error=ExtractionError(stderr=PRIVATE_STDERR, returncode=1))
    _seed_job(dynamo, chat_id=CHAT_ID)
    logger = RecordingLogger()
    publisher = RecordingPublisher(error=RuntimeError("sns unavailable"))

    result = _worker(dynamo, media, extractor, publisher, logger).handle(_event())

    assert result == {"batchItemFailures": []}
    assert dynamo.items["JOB#" + JOB_ID]["state"] == states.FAILED
    assert [event for level, event, _ in logger.lines if level == "error"] == [
        "job_event_publish_failed"
    ]


def test_the_publisher_sends_the_contract_body(monkeypatch: pytest.MonkeyPatch) -> None:
    sns = _RecordingSns()
    monkeypatch.setattr(handler.boto3, "client", lambda name: _sns_client(name, sns))
    config = replace(CONFIG, job_events_topic_arn=TOPIC_ARN)

    publisher = handler._job_publisher(config)
    assert publisher is not None
    publisher("job_completed", JOB_ID)

    assert sns.calls == [
        {
            "TopicArn": TOPIC_ARN,
            "Message": '{"version":1,"event":"job_completed","job_id":"m3x9k2q1f7"}',
        }
    ]


def test_the_publisher_is_absent_without_a_topic() -> None:
    assert handler._job_publisher(CONFIG) is None


def test_build_worker_wires_the_configured_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    sns = _RecordingSns()
    config = replace(CONFIG, job_events_topic_arn=TOPIC_ARN)
    monkeypatch.setattr(handler, "load_config", lambda: config)
    monkeypatch.setattr(handler, "MediaStore", lambda *args, **kwargs: object())
    monkeypatch.setattr(handler, "Extractor", lambda **kwargs: object())
    monkeypatch.setattr(handler, "DynamoRepository", lambda *args, **kwargs: object())
    monkeypatch.setattr(handler, "get_logger", lambda name: RecordingLogger())
    monkeypatch.setattr(handler, "s3_client", lambda: object())
    monkeypatch.setattr(handler.boto3, "client", lambda name: _sns_client(name, sns))

    worker = handler._build_worker()
    assert worker._publish is not None
    worker._publish("job_failed", JOB_ID)

    assert sns.calls == [
        {
            "TopicArn": TOPIC_ARN,
            "Message": '{"version":1,"event":"job_failed","job_id":"m3x9k2q1f7"}',
        }
    ]


def test_build_worker_passes_no_publisher_without_a_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(handler, "load_config", lambda: CONFIG)
    monkeypatch.setattr(handler, "MediaStore", lambda *args, **kwargs: object())
    monkeypatch.setattr(handler, "Extractor", lambda **kwargs: object())
    monkeypatch.setattr(handler, "DynamoRepository", lambda *args, **kwargs: object())
    monkeypatch.setattr(handler, "get_logger", lambda name: RecordingLogger())
    monkeypatch.setattr(handler, "s3_client", lambda: object())
    monkeypatch.setattr(handler.boto3, "client", lambda name: object())

    assert handler._build_worker()._publish is None
