"""Unit tests for URL normalisation (T041)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ytdl.urls import InvalidVideoURL, VideoRef, parse_video_url

VIDEO_ID = "dQw4w9WgXcQ"
MAX_LENGTH = 2048

ACCEPTED = [
    pytest.param("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "www.youtube.com", id="watch-www"),
    pytest.param("https://youtube.com/watch?v=dQw4w9WgXcQ", "www.youtube.com", id="watch-bare"),
    pytest.param("http://www.youtube.com/watch?v=dQw4w9WgXcQ", "www.youtube.com", id="watch-http"),
    pytest.param("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "www.youtube.com", id="watch-mobile"),
    pytest.param("https://youtu.be/dQw4w9WgXcQ", "youtu.be", id="short"),
    pytest.param("http://youtu.be/dQw4w9WgXcQ", "youtu.be", id="short-http"),
    pytest.param("https://youtu.be/dQw4w9WgXcQ/", "youtu.be", id="short-trailing-slash"),
    pytest.param("https://www.youtube.com/shorts/dQw4w9WgXcQ", "www.youtube.com", id="shorts"),
    pytest.param("https://www.youtube.com/embed/dQw4w9WgXcQ", "www.youtube.com", id="embed"),
    pytest.param(
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
        "www.youtube.com",
        id="embed-nocookie",
    ),
    pytest.param("https://www.youtube.com/v/dQw4w9WgXcQ", "www.youtube.com", id="v-path"),
    pytest.param(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=tracking&feature=share&t=42&start=42&pp=x",
        "www.youtube.com",
        id="watch-tracking",
    ),
    pytest.param(
        "https://youtu.be/dQw4w9WgXcQ?si=tracking&t=42",
        "youtu.be",
        id="short-tracking",
    ),
    pytest.param(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&utm_source=x&utm_medium=y&utm_campaign=z",
        "www.youtube.com",
        id="watch-utm",
    ),
    pytest.param(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1234567890",
        "www.youtube.com",
        id="watch-with-playlist",
    ),
    pytest.param(
        "https://www.youtube.com/playlist?list=PL1234567890&v=dQw4w9WgXcQ",
        "www.youtube.com",
        id="playlist-with-video",
    ),
    pytest.param(
        "  https://youtu.be/dQw4w9WgXcQ  ",
        "youtu.be",
        id="surrounding-whitespace",
    ),
]

_PADDING = MAX_LENGTH - len(f"https://www.youtube.com/watch?v={VIDEO_ID}&x=")
BOUNDARY_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}&x=" + "a" * _PADDING
LONG_URL = BOUNDARY_URL + "a"

REJECTED = [
    pytest.param("https://vimeo.com/123456789", "foreign_host", id="foreign-host"),
    pytest.param(
        "https://evil.example/watch?v=dQw4w9WgXcQ", "foreign_host", id="foreign-host-watch"
    ),
    pytest.param(
        "https://www.youtube.com.evil.example/watch?v=dQw4w9WgXcQ",
        "foreign_host",
        id="suffix-trick",
    ),
    pytest.param(
        "https://www.youtube.com@evil.example/watch?v=dQw4w9WgXcQ",
        "foreign_host",
        id="userinfo-trick",
    ),
    pytest.param("https://www.youtube.com/watch?v=dQw4w9WgXc", "bad_video_id", id="id-too-short"),
    pytest.param("https://www.youtube.com/watch?v=dQw4w9WgXcQQ", "bad_video_id", id="id-too-long"),
    pytest.param(
        "https://www.youtube.com/watch?v=dQw4w9WgXc!", "bad_video_id", id="id-illegal-character"
    ),
    pytest.param("https://www.youtube.com/watch?v=dQw4w9.WgXc", "bad_video_id", id="id-dot"),
    pytest.param("https://www.youtube.com/watch?v=", "bad_video_id", id="id-empty"),
    pytest.param("https://www.youtube.com/watch", "bad_video_id", id="watch-without-v"),
    pytest.param("https://www.youtube.com/playlist?list=PL1234567890", "collection", id="playlist"),
    pytest.param(
        "https://www.youtube.com/channel/UC123456?v=dQw4w9WgXcQ",
        "collection",
        id="channel-with-v",
    ),
    pytest.param("https://www.youtube.com/channel/UC123456", "collection", id="channel"),
    pytest.param("https://www.youtube.com/c/someone", "collection", id="custom"),
    pytest.param("https://www.youtube.com/user/someone", "collection", id="user"),
    pytest.param("https://www.youtube.com/@someone", "collection", id="handle"),
    pytest.param("https://www.youtube.com/@someone/videos", "collection", id="handle-sub-path"),
    pytest.param("https://www.youtube.com/live/dQw4w9WgXcQ", "live", id="live"),
    pytest.param("https://www.youtube.com/live", "live", id="live-without-id"),
    pytest.param("https://youtu.be", "bad_video_id", id="short-without-id"),
    pytest.param("https://youtu.be/dQw4w9WgXcQ/extra", "unsupported_path", id="short-sub-path"),
    pytest.param("", "empty", id="empty"),
    pytest.param("   ", "empty", id="whitespace"),
    pytest.param(LONG_URL, "too_long", id="too-long"),
    pytest.param("ftp://www.youtube.com/watch?v=dQw4w9WgXcQ", "bad_scheme", id="ftp-scheme"),
    pytest.param("//www.youtube.com/watch?v=dQw4w9WgXcQ", "bad_scheme", id="scheme-relative"),
    pytest.param("www.youtube.com/watch?v=dQw4w9WgXcQ", "bad_scheme", id="no-scheme"),
    pytest.param("https://www.youtube.com/feed/trending", "unsupported_path", id="feed"),
]


@pytest.mark.parametrize(("url", "host"), ACCEPTED)
def test_accepted_forms_normalise_to_host_and_video_id(url: str, host: str) -> None:
    reference = parse_video_url(url)

    assert reference == VideoRef(host=host, video_id=VIDEO_ID)


@pytest.mark.parametrize(("url", "reason"), REJECTED)
def test_rejected_forms_raise_with_a_machine_usable_reason(url: str, reason: str) -> None:
    with pytest.raises(InvalidVideoURL) as excinfo:
        parse_video_url(url)

    assert excinfo.value.reason == reason


def test_video_reference_is_frozen() -> None:
    reference = parse_video_url(f"https://youtu.be/{VIDEO_ID}")

    with pytest.raises(FrozenInstanceError):
        reference.host = "vimeo.com"  # type: ignore[misc]


def test_two_thousand_and_forty_eight_characters_are_accepted() -> None:
    assert len(BOUNDARY_URL) == MAX_LENGTH

    assert parse_video_url(BOUNDARY_URL).video_id == VIDEO_ID


def test_video_id_character_set_is_exact() -> None:
    for candidate in ("dQw4w9WgXc-", "dQw4w9WgXc_", "___________"):
        assert parse_video_url(f"https://youtu.be/{candidate}").video_id == candidate
