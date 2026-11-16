"""Unit tests for the five-category failure taxonomy (T060)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from ytdl import errors

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "stderr"

# One captured yt-dlp stderr sample per signature group in contracts/api.md and D-13/D-14.
CAPTURED = [
    pytest.param("private_video.txt", errors.UNSUPPORTED_MEDIA, False, id="private"),
    pytest.param("video_removed.txt", errors.UNSUPPORTED_MEDIA, False, id="removed"),
    pytest.param("members_only.txt", errors.UNSUPPORTED_MEDIA, False, id="members-only"),
    pytest.param("age_restricted.txt", errors.UNSUPPORTED_MEDIA, False, id="age-restricted"),
    pytest.param("region_locked.txt", errors.UNSUPPORTED_MEDIA, False, id="region-locked"),
    pytest.param("drm_protected.txt", errors.UNSUPPORTED_MEDIA, False, id="drm"),
    pytest.param("live_upcoming.txt", errors.UNSUPPORTED_MEDIA, False, id="live-upcoming"),
    pytest.param("playlist_unavailable.txt", errors.UNSUPPORTED_MEDIA, False, id="playlist"),
    pytest.param("bot_check_cookies.txt", errors.UPSTREAM_PROBLEM, False, id="bot-cookies"),
    pytest.param("bot_check_plain.txt", errors.UPSTREAM_PROBLEM, False, id="bot-plain"),
    pytest.param("http_error_403_blocked.txt", errors.UPSTREAM_PROBLEM, False, id="403-blocked"),
    pytest.param("http_error_403_fragment.txt", errors.UPSTREAM_PROBLEM, True, id="403-fragment"),
    pytest.param("too_many_requests.txt", errors.UPSTREAM_PROBLEM, True, id="429"),
    pytest.param("connection_reset.txt", errors.UPSTREAM_PROBLEM, True, id="connection-reset"),
    pytest.param("read_timeout.txt", errors.UPSTREAM_PROBLEM, True, id="read-timed-out"),
    pytest.param(
        "temporary_failure_dns.txt", errors.UPSTREAM_PROBLEM, True, id="temporary-failure"
    ),
    pytest.param("eof_occurred.txt", errors.UPSTREAM_PROBLEM, True, id="eof"),
    pytest.param("unknown_exit.txt", errors.INTERNAL_ERROR, True, id="unknown"),
]

CATEGORIES = frozenset(
    {
        errors.INVALID_INPUT,
        errors.UNSUPPORTED_MEDIA,
        errors.LIMIT_EXCEEDED,
        errors.UPSTREAM_PROBLEM,
        errors.INTERNAL_ERROR,
    }
)

TERMINAL_CATEGORIES = (errors.INVALID_INPUT, errors.UNSUPPORTED_MEDIA, errors.LIMIT_EXCEEDED)


def _stderr(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize(("fixture", "category", "retryable"), CAPTURED)
def test_captured_stderr_maps_to_the_expected_category(
    fixture: str, category: str, retryable: bool
) -> None:
    failure = errors.classify(_stderr(fixture), returncode=1)

    assert isinstance(failure, errors.Failure)
    assert failure.category == category
    assert failure.retryable is retryable


@pytest.mark.parametrize(("fixture", "category", "retryable"), CAPTURED)
def test_every_message_is_one_sentence_with_no_url_and_no_newline(
    fixture: str, category: str, retryable: bool
) -> None:
    message = errors.classify(_stderr(fixture), returncode=1).message

    assert message
    assert "\n" not in message
    assert "\r" not in message
    assert "http" not in message.lower()
    assert "www." not in message.lower()
    assert "\u2014" not in message
    assert message.endswith(".")
    assert "  " not in message


@pytest.mark.parametrize(("fixture", "category", "retryable"), CAPTURED)
def test_no_message_quotes_the_captured_stderr_verbatim(
    fixture: str, category: str, retryable: bool
) -> None:
    stderr = _stderr(fixture)
    message = errors.classify(stderr, returncode=1).message

    for line in stderr.splitlines():
        if line.strip():
            assert line.strip() not in message


def test_the_five_categories_and_their_messages_are_fixed() -> None:
    assert set(errors.MESSAGES) == CATEGORIES
    assert all(isinstance(value, str) and value for value in errors.MESSAGES.values())
    assert len(set(errors.MESSAGES.values())) == len(CATEGORIES)


@pytest.mark.parametrize("category", TERMINAL_CATEGORIES)
def test_invalid_unsupported_and_limit_are_never_retryable(category: str) -> None:
    message = errors.MESSAGES[category]

    assert isinstance(message, str)
    assert errors.Failure(category=category, message=message, retryable=False).retryable is False


def test_the_bot_check_message_points_at_the_configuration() -> None:
    failure = errors.classify(_stderr("bot_check_cookies.txt"), returncode=1)

    assert failure.category == errors.UPSTREAM_PROBLEM
    assert failure.retryable is False
    assert "configuration" in failure.message.lower()
    assert "resubmit" not in failure.message.lower()
    assert failure.message == errors.UPSTREAM_BLOCKED_MESSAGE


def test_a_hard_403_message_points_at_the_configuration() -> None:
    failure = errors.classify(_stderr("http_error_403_blocked.txt"), returncode=1)

    assert failure.category == errors.UPSTREAM_PROBLEM
    assert failure.retryable is False
    assert failure.message == errors.UPSTREAM_BLOCKED_MESSAGE


def test_matching_is_case_insensitive() -> None:
    failure = errors.classify("ERROR: [YOUTUBE] ABC: PRIVATE VIDEO", returncode=1)

    assert failure.category == errors.UNSUPPORTED_MEDIA
    assert failure.retryable is False


def test_an_empty_stderr_with_a_zero_exit_is_an_internal_error() -> None:
    failure = errors.classify("", returncode=0)

    assert failure.category == errors.INTERNAL_ERROR
    assert failure.retryable is True


def test_empty_stderr_without_a_returncode_is_an_internal_error() -> None:
    failure = errors.classify("")

    assert failure.category == errors.INTERNAL_ERROR
    assert failure.retryable is True


def test_an_unrecognised_non_zero_exit_is_an_internal_error() -> None:
    failure = errors.classify("ERROR: something we have never seen before", returncode=2)

    assert failure.category == errors.INTERNAL_ERROR
    assert failure.retryable is True


def test_any_exception_is_an_internal_error_even_with_a_media_signature() -> None:
    failure = errors.classify(
        _stderr("private_video.txt"),
        returncode=1,
        exception=RuntimeError("boom"),
    )

    assert failure.category == errors.INTERNAL_ERROR
    assert failure.retryable is True


def test_a_throttle_signature_without_a_returncode_is_retryable() -> None:
    failure = errors.classify("HTTP Error 429: Too Many Requests")

    assert failure.category == errors.UPSTREAM_PROBLEM
    assert failure.retryable is True


def test_every_captured_failure_is_a_frozen_value() -> None:
    failure = errors.classify(_stderr("private_video.txt"), returncode=1)

    with pytest.raises(FrozenInstanceError):
        failure.category = errors.INTERNAL_ERROR  # type: ignore[misc]
