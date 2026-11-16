"""Unit tests for key derivation (T014)."""

from __future__ import annotations

import hashlib

from ytdl import keys

JOB_ID = "m3x9k2q1f7abc"
VIDEO_ID = "dQw4w9WgXcQ"
TOKEN = "s3cr3t-token-value"


def test_media_key_is_the_documented_path() -> None:
    assert keys.media_key(JOB_ID, VIDEO_ID) == f"output/{JOB_ID}/{VIDEO_ID}.mp4"


def test_media_key_is_deterministic() -> None:
    assert keys.media_key(JOB_ID, VIDEO_ID) == keys.media_key(JOB_ID, VIDEO_ID)
    assert keys.media_key(JOB_ID, VIDEO_ID) != keys.media_key("other", VIDEO_ID)


def test_config_key() -> None:
    assert keys.config_key("cookies.txt") == "config/cookies.txt"


def test_rate_window_is_hour_aligned_utc() -> None:
    window = keys.rate_window(1_700_000_000)

    assert window == 1_699_999_200
    assert window % 3600 == 0
    assert window <= 1_700_000_000 < window + 3600


def test_rate_window_is_stable_inside_one_hour() -> None:
    start = keys.rate_window(1_700_000_000)

    assert keys.rate_window(1_699_999_200) == start
    assert keys.rate_window(1_700_002_799) == start
    assert keys.rate_window(1_700_002_800) == start + 3600


def test_credential_fingerprint_is_the_first_sixteen_hex_characters() -> None:
    fingerprint = keys.credential_fingerprint(TOKEN)

    assert fingerprint == hashlib.sha256(TOKEN.encode("utf-8")).hexdigest()[:16]
    assert len(fingerprint) == 16
    assert fingerprint == keys.credential_fingerprint(TOKEN)
    assert fingerprint != keys.credential_fingerprint(TOKEN + "x")


def test_rate_key_shape() -> None:
    window = keys.rate_window(1_700_000_000)
    fingerprint = keys.credential_fingerprint(TOKEN)

    assert keys.rate_key(window, TOKEN) == f"RATE#{window}#{fingerprint}"


def test_no_key_contains_the_raw_token() -> None:
    window = keys.rate_window(1_700_000_000)

    for key in (
        keys.media_key(JOB_ID, VIDEO_ID),
        keys.config_key("cookies.txt"),
        keys.rate_key(window, TOKEN),
    ):
        assert TOKEN not in key
