"""Normalise YouTube links to ``(host, video_id)`` (T049).

Accepted forms are ``watch?v=``, ``youtu.be``, ``/shorts/``, ``/embed/``, and
``/v/``, on ``youtube.com``, ``www.youtube.com``, ``m.youtube.com``, and
``youtu.be``, with or without tracking parameters. ``youtube-nocookie.com``
embed links are accepted and normalised to ``www.youtube.com``.

Documented edge case: a playlist URL that also carries a valid ``v=`` parameter
is treated as that single video. The submitted link names one video, so the
playlist context is ignored rather than rejected. A playlist without ``v=`` is
a collection and is refused.

Live links are out of scope in v1, so ``/live/<id>`` is refused even though it
carries a video identifier. Every refusal raises ``InvalidVideoURL`` with a
short machine-usable ``reason``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

MAX_URL_LENGTH = 2048

_WWW_HOST = "www.youtube.com"
_SHORT_HOST = "youtu.be"

_SCHEMES = frozenset({"http", "https"})
_YOUTUBE_HOSTS = {
    "youtube.com": _WWW_HOST,
    "www.youtube.com": _WWW_HOST,
    "m.youtube.com": _WWW_HOST,
    "www.youtube-nocookie.com": _WWW_HOST,
    "youtube-nocookie.com": _WWW_HOST,
}
_HOSTS = {**_YOUTUBE_HOSTS, _SHORT_HOST: _SHORT_HOST}

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_VIDEO_PATHS = frozenset({"shorts", "embed", "v"})
_COLLECTION_PATHS = frozenset({"channel", "c", "user", "results"})


@dataclass(frozen=True)
class VideoRef:
    """One video, identified by its normalised source host and identifier."""

    host: str
    video_id: str


class InvalidVideoURL(ValueError):
    """The input does not resolve to exactly one supported YouTube video."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"invalid video url: {reason}")
        self.reason = reason


def parse_video_url(raw: str) -> VideoRef:
    """Return the normalised reference, or raise ``InvalidVideoURL``."""
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidVideoURL("empty")

    value = raw.strip()
    if len(value) > MAX_URL_LENGTH:
        raise InvalidVideoURL("too_long")

    try:
        parts = urlsplit(value)
    except ValueError:
        raise InvalidVideoURL("bad_url") from None

    scheme = parts.scheme.lower()
    if scheme not in _SCHEMES:
        raise InvalidVideoURL("bad_scheme")

    host = (parts.hostname or "").lower()
    if host not in _HOSTS:
        raise InvalidVideoURL("foreign_host")

    segments = [segment for segment in parts.path.split("/") if segment]
    query = parse_qs(parts.query, keep_blank_values=True)

    if host == _SHORT_HOST:
        video_id = _short_id(segments)
        return VideoRef(host=_SHORT_HOST, video_id=_validate_id(video_id))

    video_id = _youtube_id(segments, query)
    return VideoRef(host=_WWW_HOST, video_id=_validate_id(video_id))


def _short_id(segments: list[str]) -> str:
    if not segments:
        raise InvalidVideoURL("bad_video_id")
    if len(segments) != 1:
        raise InvalidVideoURL("unsupported_path")
    return segments[0]


def _youtube_id(segments: list[str], query: dict[str, list[str]]) -> str:
    if not segments:
        raise InvalidVideoURL("unsupported_path")

    head = segments[0]
    if head == "live":
        raise InvalidVideoURL("live")

    if head == "watch":
        if len(segments) != 1:
            raise InvalidVideoURL("unsupported_path")
        values = query.get("v")
        if not values:
            raise InvalidVideoURL("bad_video_id")
        return values[0]

    if head == "playlist":
        values = query.get("v")
        if values:
            return values[0]
        raise InvalidVideoURL("collection")

    if head.startswith("@") or head in _COLLECTION_PATHS:
        raise InvalidVideoURL("collection")

    if head in _VIDEO_PATHS:
        if len(segments) != 2:
            raise InvalidVideoURL("bad_video_id")
        return segments[1]

    raise InvalidVideoURL("unsupported_path")


def _validate_id(candidate: str) -> str:
    if not _VIDEO_ID.match(candidate):
        raise InvalidVideoURL("bad_video_id")
    return candidate
