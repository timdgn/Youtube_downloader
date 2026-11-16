"""Unit tests for the bot request grammar (T023; contracts/bot-commands.md)."""

from __future__ import annotations

import pytest

from ytdl.bot.commands import TIER_WORDS, TIERS, parse_request

VIDEO = "https://youtu.be/dQw4w9WgXcQ"
OTHER_VIDEO = "https://www.youtube.com/watch?v=abcdefghijk"
VIDEO_ID = "dQw4w9WgXcQ"
DEFAULT_TIER = "high"


def test_the_tier_words_are_the_five_documented_words() -> None:
    assert TIER_WORDS == ("low", "medium", "high", "veryhigh", "mp3")
    assert tuple(TIERS) == TIER_WORDS


@pytest.mark.parametrize(
    ("word", "ceiling", "media_kind", "extension"),
    [
        ("low", 240, "video", "mp4"),
        ("medium", 480, "video", "mp4"),
        ("high", 720, "video", "mp4"),
        ("veryhigh", 1080, "video", "mp4"),
        ("mp3", None, "audio", "mp3"),
    ],
)
def test_every_tier_word_maps_to_its_ceiling_kind_and_extension(
    word: str, ceiling: int | None, media_kind: str, extension: str
) -> None:
    tier = TIERS[word]

    assert tier.word == word
    assert tier.ceiling == ceiling
    assert tier.media_kind == media_kind
    assert tier.extension == extension


@pytest.mark.parametrize("word", ["medium", "MEDIUM", "Medium"])
def test_a_link_and_a_resolution_word_are_a_request(word: str) -> None:
    intent = parse_request(f"{VIDEO} {word}", DEFAULT_TIER)

    assert intent.kind == "request"
    assert intent.reference is not None
    assert intent.reference.video_id == VIDEO_ID
    assert intent.tier == TIERS["medium"]


@pytest.mark.parametrize("default", ["low", "medium", "high", "veryhigh", "mp3"])
def test_a_link_alone_uses_the_configured_default_and_reports_which_tier(default: str) -> None:
    intent = parse_request(VIDEO, default)

    assert intent.kind == "request"
    assert intent.tier is not None
    assert intent.tier.word == default


@pytest.mark.parametrize("punctuation", [".", ",", "!", "?"])
def test_trailing_punctuation_is_stripped_from_the_link(punctuation: str) -> None:
    intent = parse_request(f"{VIDEO}{punctuation}", DEFAULT_TIER)

    assert intent.kind == "request"
    assert intent.reference is not None
    assert intent.reference.video_id == VIDEO_ID
    assert intent.tier == TIERS[DEFAULT_TIER]


@pytest.mark.parametrize(
    ("text", "word"),
    [
        (f"{VIDEO} medium.", "medium"),
        (f"{VIDEO} medium,", "medium"),
        (f"{VIDEO} medium!", "medium"),
        (f"{VIDEO} medium?", "medium"),
        (f"{VIDEO} MEDIUM.", "medium"),
    ],
)
def test_trailing_punctuation_is_stripped_from_the_word(text: str, word: str) -> None:
    intent = parse_request(text, DEFAULT_TIER)

    assert intent.kind == "request"
    assert intent.tier is not None
    assert intent.tier.word == word


def test_two_links_in_one_message_are_two_links() -> None:
    intent = parse_request(f"{VIDEO} {OTHER_VIDEO} medium", DEFAULT_TIER)

    assert intent.kind == "two_links"
    assert intent.tier is None


@pytest.mark.parametrize(
    ("text", "word"),
    [
        (f"{VIDEO} hq", "hq"),
        (f"{VIDEO} HQ", "hq"),
        (f"{VIDEO} hq.", "hq"),
    ],
)
def test_an_unknown_resolution_word_is_reported(text: str, word: str) -> None:
    intent = parse_request(text, DEFAULT_TIER)

    assert intent.kind == "unknown_resolution"
    assert intent.word == word
    assert intent.tier is None


@pytest.mark.parametrize(
    "text",
    [
        "medium",
        "MEDIUM",
        "mp3",
        "",
        "   ",
        None,
        "\t\n",
        "/help",
        "/start",
        "/foo bar",
        "hello there",
        "youtu.be",
    ],
)
def test_text_without_a_link_is_a_format_hint(text: str | None) -> None:
    intent = parse_request(text, DEFAULT_TIER)

    assert intent.kind == "format_hint"
    assert intent.reference is None
    assert intent.tier is None


@pytest.mark.parametrize(
    "url",
    [
        "https://vimeo.com/123456789",
        "https://www.youtube.com/playlist?list=PL1234567890",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "https://www.youtube.com/@somechannel",
        "ftp://youtu.be/dQw4w9WgXcQ",
    ],
)
def test_a_link_that_is_not_one_supported_video_is_invalid(url: str) -> None:
    intent = parse_request(url, DEFAULT_TIER)

    assert intent.kind == "invalid_link"
    assert intent.reference is None
