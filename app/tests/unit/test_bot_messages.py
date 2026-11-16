"""Unit tests for the fixed bot replies and helpers (T024; contracts/bot-commands.md)."""

from __future__ import annotations

import pytest

from ytdl.bot.messages import (
    ACCEPTED_AUDIO,
    ACCEPTED_VIDEO,
    COMPLETED,
    EXPIRED,
    FAILED,
    FORMAT_HINT,
    GROUP_CHAT,
    IN_PROGRESS,
    INVALID_LINK,
    NEXT_STEP_RETRY,
    NOT_APPROVED,
    RATE_LIMITED,
    REUSED,
    TWO_LINKS,
    UNKNOWN_RESOLUTION,
    format_date,
    format_minute,
    format_size,
    paginate,
)

VIDEO_ID = "dQw4w9WgXcQ"
TITLE = "Some video title"
URL = "https://example.test/signed"
EXPIRES = "2026-11-16 19:00"
AVAILABLE_UNTIL = "2026-11-21"


def test_accepted_video_is_the_documented_sentence() -> None:
    assert ACCEPTED_VIDEO.format(video_id=VIDEO_ID, tier="medium", ceiling=480) == (
        "Accepted: dQw4w9WgXcQ at medium (480p). Working on it."
    )


def test_accepted_audio_is_the_documented_sentence() -> None:
    assert ACCEPTED_AUDIO.format(video_id=VIDEO_ID) == (
        "Accepted: dQw4w9WgXcQ as MP3 audio. Working on it."
    )


def test_completed_is_the_documented_template() -> None:
    rendered = COMPLETED.format(
        title=TITLE,
        quality="720p",
        size="182.4 MB",
        expires=EXPIRES,
        url=URL,
        available_until=AVAILABLE_UNTIL,
    )

    assert rendered == (
        "Ready: Some video title\n"
        "Quality: 720p\n"
        "Size: 182.4 MB\n"
        "\n"
        "Download (expires 2026-11-16 19:00 UTC):\n"
        "https://example.test/signed\n"
        "\n"
        "The file stays available until 2026-11-21. "
        "Send the same request again for a fresh link."
    )


def test_reused_is_the_documented_template() -> None:
    rendered = REUSED.format(
        title=TITLE,
        quality="720p",
        expires=EXPIRES,
        url=URL,
        available_until=AVAILABLE_UNTIL,
    )

    assert rendered == (
        "Already stored: Some video title\n"
        "Quality: 720p\n"
        "\n"
        "Download (expires 2026-11-16 19:00 UTC):\n"
        "https://example.test/signed\n"
        "\n"
        "The file stays available until 2026-11-21."
    )


def test_in_progress_is_the_documented_sentence() -> None:
    assert IN_PROGRESS.format(video_id=VIDEO_ID, tier="high") == (
        "Already working on dQw4w9WgXcQ at high. I will send the link when it is ready."
    )


def test_a_retryable_failure_appends_the_next_step() -> None:
    assert NEXT_STEP_RETRY == "Send the request again."
    assert FAILED.format(message="Upstream timed out.", next_step=NEXT_STEP_RETRY) == (
        "Upstream timed out. Send the request again."
    )


def test_a_non_retryable_failure_has_no_trailing_space() -> None:
    rendered = FAILED.format(message="This video is unsupported.", next_step="").rstrip()

    assert rendered == "This video is unsupported."


def test_rate_limited_is_the_documented_sentence() -> None:
    assert RATE_LIMITED.format(retry_at=EXPIRES) == (
        "Hourly limit reached. Capacity returns at 2026-11-16 19:00 UTC."
    )


def test_expired_is_the_service_sentence() -> None:
    assert EXPIRED == "The stored file has expired; submit the video link again."


def test_format_hint_is_the_documented_sentence() -> None:
    assert FORMAT_HINT == "Send a YouTube link followed by a resolution word, or /help."


def test_two_links_is_the_documented_sentence() -> None:
    assert TWO_LINKS == "Send one link at a time, followed by one resolution word."


def test_unknown_resolution_names_the_word_and_the_vocabulary() -> None:
    assert UNKNOWN_RESOLUTION.format(word="hq") == (
        'Unknown resolution "hq". Use low, medium, high, veryhigh or mp3.'
    )


def test_not_approved_reveals_nothing_else() -> None:
    assert NOT_APPROVED == "This bot is private."


def test_the_private_refusal_contains_no_command_or_hint() -> None:
    lowered = NOT_APPROVED.lower()
    for hint in ("/", "help", "command", "download", "video", "link", "approve", "request"):
        assert hint not in lowered


def test_group_chat_is_the_documented_sentence() -> None:
    assert GROUP_CHAT == "This bot only works in a private chat."


def test_invalid_link_is_the_service_sentence() -> None:
    assert INVALID_LINK == "The submitted link is not a supported single YouTube video."


def test_format_size_uses_one_decimal_and_a_mebibyte() -> None:
    assert format_size(191_260_000) == "182.4 MB"


@pytest.mark.parametrize("value", [0, None])
def test_format_size_of_nothing_is_zero(value: int | None) -> None:
    assert format_size(value) == "0.0 MB"


def test_format_minute_and_date_read_utc() -> None:
    epoch = 1_763_294_591

    assert format_minute(epoch) == "2025-11-16 12:03"
    assert format_date(epoch) == "2025-11-16"


def test_a_time_just_before_midnight_stays_on_its_utc_day() -> None:
    epoch = 1_763_337_599

    assert format_minute(epoch) == "2025-11-16 23:59"
    assert format_date(epoch) == "2025-11-16"


def test_a_short_text_is_one_page() -> None:
    assert paginate("one line\nand another") == ["one line\nand another"]


def test_pages_never_exceed_the_ceiling_and_split_on_line_boundaries() -> None:
    lines = [f"line {index:03d} " + "x" * 90 for index in range(80)]
    text = "\n".join(lines)

    pages = paginate(text)

    assert len(pages) > 1
    assert all(len(page) <= 3500 for page in pages)
    assert "\n".join(pages) == text
    for page in pages:
        for line in page.split("\n"):
            assert line in lines


def test_no_line_is_lost_or_duplicated() -> None:
    lines = [f"{index:04d} " + "y" * 100 for index in range(120)]
    text = "\n".join(lines)

    pages = paginate(text)

    assert [line for page in pages for line in page.split("\n")] == lines


def test_a_single_line_longer_than_the_limit_is_emitted_whole() -> None:
    line = "z" * 4000

    assert paginate(line) == [line]


def test_a_custom_limit_is_honoured_on_line_boundaries() -> None:
    text = "aaa\nbbb\nccc\nddd"

    assert paginate(text, limit=7) == ["aaa\nbbb", "ccc\nddd"]
