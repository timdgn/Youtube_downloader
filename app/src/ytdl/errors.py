"""Pure mapping from captured yt-dlp stderr to the fixed failure taxonomy (T065).

The five categories and their retryability come from ``contracts/api.md`` and
research D-13; the hard-block rule comes from research D-14. Every message is a
fixed sentence: no URL, no captured stderr, no stack trace.
"""

from __future__ import annotations

from dataclasses import dataclass

INVALID_INPUT = "invalid_input"
UNSUPPORTED_MEDIA = "unsupported_media"
LIMIT_EXCEEDED = "limit_exceeded"
UPSTREAM_PROBLEM = "upstream_problem"
INTERNAL_ERROR = "internal_error"

MESSAGES: dict[str, str] = {
    INVALID_INPUT: "The submitted link is not a supported single YouTube video.",
    UNSUPPORTED_MEDIA: (
        "The source refused this video. It may be private, removed, or unavailable in this region."
    ),
    LIMIT_EXCEEDED: "The video is longer or larger than this service allows.",
    UPSTREAM_PROBLEM: "The source was temporarily unreachable; the same request may succeed later.",
    INTERNAL_ERROR: "The job could not be completed because of an internal error; try again later.",
}

# The D-14 hard block: retrying costs a submission and cannot succeed, so the operator
# is sent to the configuration instead.
UPSTREAM_BLOCKED_MESSAGE = (
    "The source blocked this request; check the service configuration before submitting again."
)


@dataclass(frozen=True)
class Failure:
    """One classified failure: the stored category, its message, and retryability."""

    category: str
    message: str
    retryable: bool


# Signatures that mean the source has refused this video for good.
_UNSUPPORTED_SIGNATURES = (
    "private video",
    "video unavailable",
    "is unavailable",
    "has been removed",
    "removed by the uploader",
    "has been deleted",
    "members-only",
    "members only",
    "join this channel",
    "sign in to confirm your age",
    "age-restricted",
    "age restricted",
    "inappropriate for some users",
    "not available in your country",
    "not made this video available",
    "blocked it in your country",
    "geo-restricted",
    "drm protected",
    "digital rights management",
    "protected by drm",
    "live event",
    "premieres in",
    "will begin in",
    "this video is live",
    "playlist",
    "only available to",
)

# Signatures of a source-side block that a retry cannot clear (D-14).
_BLOCK_PHRASES = (
    "not a bot",
    "captcha",
    "unusual traffic",
    "access denied",
    "request was blocked",
    "request has been blocked",
    "request is blocked",
    "blocked by the remote server",
    "blocked from accessing",
    "ip address has been blocked",
)

_BLOCK_MARKERS = (
    "blocked",
    "access denied",
    "bot",
    "captcha",
    "unusual traffic",
    "sign in",
    "login required",
    "requires authentication",
)

# Signatures of a transient source or network problem: waiting can help.
_TRANSIENT_SIGNATURES = (
    "http error 429",
    "too many requests",
    "temporary failure",
    "temporarily unavailable",
    "service unavailable",
    "connection reset",
    "connection aborted",
    "connection refused",
    "remote end closed connection",
    "network is unreachable",
    "name resolution",
    "read timed out",
    "timed out",
    "timeout",
    "unable to download",
    "eof occurred",
    "http error 5",
)

_TRANSIENT_403 = ("http error 403", "403: forbidden", "403 forbidden")


def classify(
    stderr: str,
    returncode: int | None = None,
    exception: BaseException | None = None,
) -> Failure:
    """Map a captured stderr, exit code, or exception to one `Failure`.

    Ordered most-specific first: an exception overrides everything, then the D-14
    hard block, then a refusal of the media, then a transient source problem, then
    the catch-all internal error. An empty stderr with exit code 0 is never success.
    """
    if exception is not None:
        return _failure(INTERNAL_ERROR)

    text = _normalise(stderr)

    if _is_hard_block(text):
        return Failure(UPSTREAM_PROBLEM, UPSTREAM_BLOCKED_MESSAGE, False)
    if _contains(text, _UNSUPPORTED_SIGNATURES):
        return _failure(UNSUPPORTED_MEDIA)
    if _contains(text, _TRANSIENT_SIGNATURES) or _contains(text, _TRANSIENT_403):
        return _failure(UPSTREAM_PROBLEM)
    return _failure(INTERNAL_ERROR)


def _failure(category: str) -> Failure:
    retryable = category in (UPSTREAM_PROBLEM, INTERNAL_ERROR)
    return Failure(category, MESSAGES[category], retryable)


def _normalise(stderr: str | None) -> str:
    return (stderr or "").lower().replace("\u2019", "'")


def _contains(text: str, signatures: tuple[str, ...]) -> bool:
    return any(signature in text for signature in signatures)


def _is_hard_block(text: str) -> bool:
    if _contains(text, _BLOCK_PHRASES):
        return True
    return _contains(text, _TRANSIENT_403) and _contains(text, _BLOCK_MARKERS)
