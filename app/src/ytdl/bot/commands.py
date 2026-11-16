"""Pure parsing of one bot message into a request intent (T033).

The grammar is documented in ``specs/002-telegram-bot-interface/contracts/bot-commands.md``:
a YouTube link, optionally followed by one resolution word. Links are validated by
:func:`ytdl.urls.parse_video_url`, the same function the terminal API uses. This module
adds no second URL parser and performs no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from ytdl.urls import InvalidVideoURL, VideoRef, parse_video_url

_TRAILING_PUNCTUATION = ".,!?"


@dataclass(frozen=True)
class Tier:
    """One resolution word and what it asks the pipeline to produce."""

    word: str
    ceiling: int | None
    media_kind: str
    extension: str


TIERS: dict[str, Tier] = {
    "low": Tier(word="low", ceiling=240, media_kind="video", extension="mp4"),
    "medium": Tier(word="medium", ceiling=480, media_kind="video", extension="mp4"),
    "high": Tier(word="high", ceiling=720, media_kind="video", extension="mp4"),
    "veryhigh": Tier(word="veryhigh", ceiling=1080, media_kind="video", extension="mp4"),
    "mp3": Tier(word="mp3", ceiling=None, media_kind="audio", extension="mp3"),
}

TIER_WORDS: tuple[str, ...] = ("low", "medium", "high", "veryhigh", "mp3")


@dataclass(frozen=True)
class Intent:
    """What one message asked for, with enough detail for the handler to reply."""

    kind: str
    reference: VideoRef | None = None
    tier: Tier | None = None
    word: str | None = None


def parse_request(text: str | None, default_tier: str) -> Intent:
    """Classify one message body without side effects.

    ``default_tier`` is applied when a link arrives with no resolution word. The
    returned intent carries the tier that was used, so the acknowledgement can name it.
    """
    if text is None:
        return Intent(kind="format_hint")

    stripped = text.strip()
    if not stripped or stripped.startswith("/"):
        return Intent(kind="format_hint")

    links: list[str] = []
    words: list[str] = []
    for token in stripped.split():
        candidate = token.rstrip(_TRAILING_PUNCTUATION)
        if "://" in candidate:
            links.append(candidate)
        elif candidate:
            words.append(candidate)

    if len(links) > 1:
        return Intent(kind="two_links")
    if not links:
        return Intent(kind="format_hint")

    try:
        reference = parse_video_url(links[0])
    except InvalidVideoURL:
        return Intent(kind="invalid_link")

    if not words:
        return Intent(kind="request", reference=reference, tier=TIERS[default_tier])

    word = words[0].lower()
    tier = TIERS.get(word)
    if tier is None:
        return Intent(kind="unknown_resolution", word=word)
    return Intent(kind="request", reference=reference, tier=tier)
