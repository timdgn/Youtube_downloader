"""Unit tests for key derivation (T014)."""

from __future__ import annotations

import hashlib
import re

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


# --- bot keys (data-model.md "Media Object", "Index") -----------------------


def test_media_key_takes_the_extension_and_defaults_to_mp4() -> None:
    assert keys.media_key(JOB_ID, VIDEO_ID) == f"output/{JOB_ID}/{VIDEO_ID}.mp4"
    assert keys.media_key(JOB_ID, VIDEO_ID, extension="mp4") == (f"output/{JOB_ID}/{VIDEO_ID}.mp4")
    assert keys.media_key(JOB_ID, VIDEO_ID, extension="mp3") == (f"output/{JOB_ID}/{VIDEO_ID}.mp3")


def test_owner_keys_carry_the_kind_suffix() -> None:
    assert keys.owner_job_key(123456789) == "tg:123456789#job"
    assert keys.owner_interaction_key(123456789) == "tg:123456789#interaction"

    pattern = re.compile(r"^tg:[0-9]{1,20}#(job|interaction)$")
    assert pattern.match(keys.owner_job_key(123456789))
    assert pattern.match(keys.owner_interaction_key(123456789))


def test_owner_keys_accept_a_string_form_of_the_same_id() -> None:
    assert keys.owner_job_key("123456789") == keys.owner_job_key(123456789)


def test_interaction_pk_is_the_update_id() -> None:
    assert keys.interaction_pk(812345678) == "UPD#812345678"


def test_display_name_is_video_tier_discriminator_and_extension() -> None:
    job = {
        "job_id": "m3x9k2q1f7k2q1",
        "video_id": VIDEO_ID,
        "requested_tier": "high",
        "media_kind": "video",
    }

    assert keys.display_name(job) == f"{VIDEO_ID}-high-k2q1.mp4"


def test_display_name_defaults_to_video_and_mp3_for_audio() -> None:
    without_kind = {
        "job_id": "m3x9k2q1f7k2q1",
        "video_id": VIDEO_ID,
        "requested_tier": "medium",
    }
    audio = {**without_kind, "media_kind": "audio"}
    video = {**without_kind, "media_kind": "video"}

    assert keys.display_name(without_kind).endswith(".mp4")
    assert keys.display_name(video).endswith(".mp4")
    assert keys.display_name(audio).endswith(".mp3")
    assert keys.display_name(audio) == f"{VIDEO_ID}-medium-k2q1.mp3"


def test_display_name_uses_only_the_last_four_characters_of_the_job_id() -> None:
    short = {"job_id": "abcd", "video_id": VIDEO_ID, "requested_tier": "low"}

    assert keys.display_name(short) == f"{VIDEO_ID}-low-abcd.mp4"


def test_owner_seq_key_is_the_sequence_counter() -> None:
    assert keys.owner_seq_key(123456789) == "SEQ#123456789"
    assert keys.owner_seq_key("123456789") == "SEQ#123456789"
