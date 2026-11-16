"""Time-ordered, opaque job identifiers (T018)."""

from __future__ import annotations

import secrets
import threading
import time

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
_TIMESTAMP_DIGITS = 8
_RANDOM_DIGITS = 6
_TOTAL_DIGITS = _TIMESTAMP_DIGITS + _RANDOM_DIGITS
_RANDOM_SPACE = 36**_RANDOM_DIGITS

_lock = threading.Lock()
_last_seq = 0


def new_job_id() -> str:
    """Return a 14-character identifier that sorts in creation order.

    The first eight base-36 characters are the millisecond epoch (good until
    2059), the last six are random. Within one millisecond the counter is bumped
    so identifiers still compare in creation order, and no identifier is
    enumerable from the clock alone.
    """
    global _last_seq

    with _lock:
        candidate = _now_ms() * _RANDOM_SPACE + secrets.randbelow(_RANDOM_SPACE)
        if candidate <= _last_seq:
            candidate = _last_seq + 1
        _last_seq = candidate

    return _encode(candidate)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _encode(value: int) -> str:
    digits: list[str] = []
    for _ in range(_TOTAL_DIGITS):
        value, remainder = divmod(value, 36)
        digits.append(_ALPHABET[remainder])
    return "".join(reversed(digits))
