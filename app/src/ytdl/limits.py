"""Pure duration and size limits, evaluated before any media transfer (T066)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ytdl import errors

_RETRYABLE = {
    errors.INVALID_INPUT: False,
    errors.UNSUPPORTED_MEDIA: False,
    errors.LIMIT_EXCEEDED: False,
    errors.UPSTREAM_PROBLEM: True,
    errors.INTERNAL_ERROR: True,
}


@dataclass(frozen=True)
class Decision:
    """The outcome of one limit check; ``failure`` is set only when refused."""

    allowed: bool
    failure: errors.Failure | None


_ALLOWED = Decision(allowed=True, failure=None)


def check_duration(seconds: float | int | None, max_seconds: int) -> Decision:
    """Accept a duration at or below the cap, never guess a missing one."""
    if not _is_number(seconds):
        return _refused(errors.UNSUPPORTED_MEDIA)

    value = float(seconds)
    if not math.isfinite(value) or value < 0:
        return _refused(errors.UNSUPPORTED_MEDIA)
    if value > max_seconds:
        return _refused(errors.LIMIT_EXCEEDED)
    return _ALLOWED


def check_size(size_bytes: int | None, max_bytes: int) -> Decision:
    """Accept a size at or below the cap; a missing size is our failure, not the source's."""
    if not _is_size(size_bytes):
        return _refused(errors.INTERNAL_ERROR)
    if size_bytes > max_bytes:
        return _refused(errors.LIMIT_EXCEEDED)
    return _ALLOWED


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_size(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _refused(category: str) -> Decision:
    return Decision(
        allowed=False,
        failure=errors.Failure(category, errors.MESSAGES[category], _RETRYABLE[category]),
    )
