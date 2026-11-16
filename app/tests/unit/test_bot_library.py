"""Unit tests for list rendering and the live-item filter (T049)."""

from __future__ import annotations

from typing import Any

from ytdl.bot import messages
from ytdl.bot.library import live_items, render_list

NOW = 1_763_287_200
VIDEO_ID = "dQw4w9WgXcQ"
DAY = 86400


def _job(**overrides: Any) -> dict[str, Any]:
    job: dict[str, Any] = {
        "job_id": "m3x9k2q1f7k2q1",
        "state": "completed",
        "video_id": VIDEO_ID,
        "requested_tier": "high",
        "media_kind": "video",
        "produced_quality": "720p",
        "produced_height": 720,
        "video_title": "Some video title",
        "media_size_bytes": 191_260_000,
        "media_created_at": NOW - (2 * DAY + 3 * 3600),
        "media_expires_at": NOW + 3 * DAY,
    }
    job.update(overrides)
    return job


def test_one_line_carries_every_documented_field() -> None:
    line = render_list([_job()], NOW)

    assert line == (
        "dQw4w9WgXcQ-high-k2q1.mp4 | Some video title | 720p | 182.4 MB | 2d 3h old | 3 days left"
    )


def test_an_audio_item_prints_its_mp3_name_and_quality() -> None:
    job = _job(
        requested_tier="mp3",
        media_kind="audio",
        produced_quality="mp3",
        produced_height=None,
    )

    line = render_list([job], NOW)

    assert line.split(" | ")[0] == "dQw4w9WgXcQ-mp3-k2q1.mp3"
    assert line.split(" | ")[2] == "mp3"


def test_size_prints_one_decimal_place() -> None:
    assert "182.4 MB" in render_list([_job()], NOW)
    assert "0.0 MB" in render_list([_job(media_size_bytes=0)], NOW)


def test_age_prints_days_and_hours() -> None:
    assert "2d 3h old" in render_list([_job()], NOW)
    assert "0d 0h old" in render_list([_job(media_created_at=NOW)], NOW)
    assert "1d 0h old" in render_list([_job(media_created_at=NOW - DAY)], NOW)


def test_days_left_rounds_up() -> None:
    assert "3 days left" in render_list([_job(media_expires_at=NOW + 2 * DAY + 1)], NOW)
    assert "1 days left" in render_list([_job(media_expires_at=NOW + 1)], NOW)


def test_deleted_expired_and_unfinished_items_are_not_listed() -> None:
    jobs = [
        _job(job_id="aaaa0000000001"),
        _job(job_id="aaaa0000000002", deleted_at="2026-11-16T09:30:00Z"),
        _job(job_id="aaaa0000000003", media_expires_at=NOW - 1),
        _job(job_id="aaaa0000000004", state="processing"),
        _job(job_id="aaaa0000000005", state="accepted"),
        _job(job_id="aaaa0000000006", state="failed"),
    ]

    rendered = render_list(jobs, NOW)

    assert len(rendered.splitlines()) == 1
    assert "aaaa0000000001".endswith("0001")
    assert "-0001.mp4" in rendered


def test_the_query_order_is_preserved() -> None:
    jobs = [
        _job(job_id="newest00000001", video_id="aaaaaaaaaaa"),
        _job(job_id="older000000002", video_id="bbbbbbbbbbb"),
    ]

    lines = render_list(jobs, NOW).splitlines()

    assert lines[0].startswith("aaaaaaaaaaa-high-0001.mp4")
    assert lines[1].startswith("bbbbbbbbbbb-high-0002.mp4")


def test_an_empty_library_says_so() -> None:
    assert render_list([], NOW) == messages.NO_VIDEOS
    assert render_list([_job(deleted_at="2026-11-16T09:30:00Z")], NOW) == messages.NO_VIDEOS


def test_more_than_two_hundred_live_items_states_the_remainder() -> None:
    jobs = [_job(job_id=f"job{index:011d}") for index in range(250)]

    lines = render_list(jobs, NOW).splitlines()

    assert len(lines) == 201
    assert lines[-1] == "... and 50 more. Delete some items to see the rest."


def test_exactly_two_hundred_live_items_has_no_remainder_line() -> None:
    jobs = [_job(job_id=f"job{index:011d}") for index in range(200)]

    assert len(render_list(jobs, NOW).splitlines()) == 200


def test_live_items_keeps_only_what_can_be_downloaded() -> None:
    jobs = [
        _job(job_id="live0000000001"),
        _job(job_id="deleted0000002", deleted_at="2026-11-16T09:30:00Z"),
        _job(job_id="expired0000003", media_expires_at=NOW - 1),
        _job(job_id="pending0000004", state="processing"),
    ]

    assert [job["job_id"] for job in live_items(jobs, NOW)] == ["live0000000001"]
