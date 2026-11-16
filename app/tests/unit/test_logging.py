"""Unit tests for the structured logger (T011)."""

from __future__ import annotations

import json

import pytest

from ytdl.logging import get_logger

TOKEN = "s3cr3t-token-value"
PRESIGNED = (
    "https://ytdl-dev-media.s3.eu-west-1.amazonaws.com/output/job/video.mp4"
    "?X-Amz-Signature=deadbeef&X-Amz-Credential=AKIAEXAMPLE"
)


def _records(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    captured = capsys.readouterr()
    return [json.loads(line) for line in captured.out.splitlines() if line.strip()]


@pytest.fixture
def logger(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "dev")
    return get_logger("api")


def test_one_json_object_per_call(logger, capsys: pytest.CaptureFixture[str]) -> None:
    logger.info("job.accepted", job_id="m3x9k2q1f7")
    logger.warning("job.slow", attempt=2)

    records = _records(capsys)

    assert len(records) == 2
    assert [record["level"] for record in records] == ["INFO", "WARNING"]
    assert [record["event"] for record in records] == ["job.accepted", "job.slow"]
    for record in records:
        assert record["function"] == "api"
        assert record["environment"] == "dev"
        assert record["timestamp"].endswith("Z")


def test_error_level(logger, capsys: pytest.CaptureFixture[str]) -> None:
    logger.error("job.failed", failure_category="internal_error", retryable=True)

    (record,) = _records(capsys)

    assert record["level"] == "ERROR"
    assert record["failure_category"] == "internal_error"
    assert record["retryable"] is True


def test_allowlist_drops_unknown_keys(logger, capsys: pytest.CaptureFixture[str]) -> None:
    logger.info(
        "job.accepted",
        job_id="m3x9k2q1f7",
        source_host="www.youtube.com",
        attempt=1,
        token=TOKEN,
        authorization=f"Bearer {TOKEN}",
        url=PRESIGNED,
        download_url=PRESIGNED,
    )

    captured = capsys.readouterr()
    (record,) = [json.loads(line) for line in captured.out.splitlines() if line.strip()]

    assert record["job_id"] == "m3x9k2q1f7"
    assert record["source_host"] == "www.youtube.com"
    assert record["attempt"] == 1
    for dropped in ("token", "authorization", "url", "download_url"):
        assert dropped not in record
    assert TOKEN not in captured.out
    assert "X-Amz-Signature" not in captured.out


def test_nullable_allowlisted_values_keep_their_types(
    logger, capsys: pytest.CaptureFixture[str]
) -> None:
    logger.info(
        "job.completed",
        requested_quality=None,
        produced_quality="720p",
        media_size_bytes=412345678,
        processing_ms=0,
        outcome="completed",
        duration_ms=12.5,
        queue="ytdl-dev-jobs",
        message="done",
        state="completed",
        video_id="dQw4w9WgXcQ",
    )

    (record,) = _records(capsys)

    assert record["requested_quality"] is None
    assert record["produced_quality"] == "720p"
    assert record["media_size_bytes"] == 412345678
    assert record["processing_ms"] == 0
    assert record["duration_ms"] == 12.5
    assert record["queue"] == "ytdl-dev-jobs"
    assert record["message"] == "done"


def test_message_with_newline_stays_on_one_line(logger, capsys: pytest.CaptureFixture[str]) -> None:
    logger.error("job.failed", message="first line\nsecond line")

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]

    assert len(lines) == 1
    assert json.loads(lines[0])["message"] == "first line\nsecond line"


def test_metric_emits_one_embedded_metric_format_object(
    logger, capsys: pytest.CaptureFixture[str]
) -> None:
    logger.metric(
        "JobProcessing",
        1234.5,
        "Milliseconds",
        job_id="m3x9k2q1f7",
        token=TOKEN,
    )

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) == 1
    (record,) = [json.loads(line) for line in lines]

    block = record["_aws"]["CloudWatchMetrics"][0]
    assert block["Namespace"] == "Ytdl/dev"
    assert block["Dimensions"] == [["Environment", "Function"]]
    assert block["Metrics"] == [{"Name": "JobProcessing", "Unit": "Milliseconds"}]
    assert isinstance(record["_aws"]["Timestamp"], int)
    assert record["Environment"] == "dev"
    assert record["Function"] == "api"
    assert record["JobProcessing"] == 1234.5
    assert record["level"] == "INFO"
    assert record["event"] == "JobProcessing"
    assert record["function"] == "api"
    assert record["environment"] == "dev"
    assert record["timestamp"].endswith("Z")
    assert record["job_id"] == "m3x9k2q1f7"
    assert "token" not in record
    assert TOKEN not in captured.out


def test_logger_without_environment_still_emits_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    get_logger("worker").info("job.claimed")

    (record,) = _records(capsys)
    assert record["function"] == "worker"
    assert "environment" in record
