"""Unit tests for the shared submission path (T016)."""

from __future__ import annotations

import pytest

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.sqs_fake import FakeJobQueue
from ytdl import states
from ytdl.config import load_config
from ytdl.submissions import build_job, submit
from ytdl.urls import VideoRef

CREATED_AT = "2026-11-16T10:00:00Z"
NOW_EPOCH = 1_763_287_200
REFERENCE = VideoRef(host="www.youtube.com", video_id="dQw4w9WgXcQ")
RATE_PK = "RATE#1763280000#0123456789abcdef"

ENV = {
    "ENVIRONMENT": "dev",
    "JOBS_TABLE": "ytdl-test-jobs",
    "MEDIA_BUCKET": "ytdl-test-media",
    "JOBS_QUEUE_URL": "https://sqs.eu-west-3.amazonaws.com/000000000000/ytdl-test-jobs",
    "TOKEN_PARAM_NAME": "/ytdl/test/api-token",
}

OWNER = {
    "owner_key": "tg:123456789#job",
    "chat_id": "123456789",
    "update_id": 812345678,
    "requested_tier": "high",
    "is_test": False,
}


def test_api_job_omits_every_bot_field() -> None:
    job = build_job(REFERENCE, CREATED_AT, "720p", 720)

    for name in ("owner_key", "chat_id", "update_id", "requested_tier", "is_test"):
        assert name not in job


def test_api_job_copies_the_request_fields() -> None:
    job = build_job(REFERENCE, CREATED_AT, "720p", 720)

    assert job["state"] == states.ACCEPTED
    assert job["created_at"] == CREATED_AT
    assert job["updated_at"] == CREATED_AT
    assert job["source_host"] == "www.youtube.com"
    assert job["video_id"] == "dQw4w9WgXcQ"
    assert job["requested_quality"] == "720p"
    assert job["requested_height"] == 720


def test_media_kind_defaults_to_video() -> None:
    assert build_job(REFERENCE, CREATED_AT, "720p", 720)["media_kind"] == "video"
    assert build_job(REFERENCE, CREATED_AT, None, None, media_kind="audio")["media_kind"] == (
        "audio"
    )


def test_bot_job_carries_the_owner_fields() -> None:
    job = build_job(REFERENCE, CREATED_AT, "720p", 720, owner=OWNER)

    assert job["owner_key"] == "tg:123456789#job"
    assert job["chat_id"] == "123456789"
    assert job["update_id"] == 812345678
    assert job["requested_tier"] == "high"
    assert job["is_test"] is False


@pytest.mark.parametrize(
    "owner",
    [
        pytest.param(None, id="terminal"),
        pytest.param(OWNER, id="bot-video"),
        pytest.param({**OWNER, "requested_tier": "mp3", "is_test": True}, id="bot-audio-test"),
    ],
)
def test_no_field_is_ever_written_as_none(owner: dict | None) -> None:
    job = build_job(REFERENCE, CREATED_AT, None, None, owner=owner)

    assert all(value is not None for value in job.values())


def test_an_absent_quality_keeps_no_quality_fields() -> None:
    job = build_job(REFERENCE, CREATED_AT, None, None)

    assert "requested_quality" not in job
    assert "requested_height" not in job


def test_build_job_returns_a_fresh_mapping() -> None:
    first = build_job(REFERENCE, CREATED_AT, "720p", 720, owner=OWNER)
    second = build_job(REFERENCE, CREATED_AT, "720p", 720, owner=OWNER)

    assert first["job_id"] != second["job_id"]


def test_submit_writes_the_job_then_enqueues_the_message() -> None:
    repo, queue = FakeDynamoRepository(), FakeJobQueue()
    job = build_job(REFERENCE, CREATED_AT, "720p", 720)
    message = {"version": 1, "job_id": job["job_id"]}

    submit(repo, queue, load_config(dict(ENV)), job, RATE_PK, 10, NOW_EPOCH, None, message)

    assert [call[0] for call in repo.calls] == ["create_job_with_rate_increment"]
    assert repo.items["JOB#" + job["job_id"]]["video_id"] == "dQw4w9WgXcQ"
    assert queue.messages == [message]


def test_submit_forwards_the_interaction_into_the_same_transaction() -> None:
    repo, queue = FakeDynamoRepository(), FakeJobQueue()
    job = build_job(REFERENCE, CREATED_AT, "720p", 720, owner=OWNER)
    interaction = {
        "pk": "UPD#812345678",
        "owner_key": "tg:123456789#interaction",
        "created_at": CREATED_AT,
        "text": "dQw4w9WgXcQ high",
        "outcome": "accepted",
        "job_id": job["job_id"],
    }

    submit(repo, queue, load_config(dict(ENV)), job, RATE_PK, 10, NOW_EPOCH, interaction, None)

    call = repo.calls[0][1]
    assert call["interaction"] == interaction


def test_submit_builds_the_api_message_when_none_is_given() -> None:
    repo, queue = FakeDynamoRepository(), FakeJobQueue()
    job = build_job(REFERENCE, CREATED_AT, "720p", 720)

    submit(repo, queue, load_config(dict(ENV)), job, RATE_PK, 10, NOW_EPOCH)

    assert queue.messages == [
        {
            "version": 1,
            "job_id": job["job_id"],
            "video_id": "dQw4w9WgXcQ",
            "source_host": "www.youtube.com",
            "requested_height": 720,
            "enqueued_at": CREATED_AT,
        }
    ]
