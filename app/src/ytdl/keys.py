"""Deterministic storage keys and rate-limit identities (T020)."""

from __future__ import annotations

import hashlib

RATE_WINDOW_SECONDS = 3600
RATE_TTL_SECONDS = 7200
_FINGERPRINT_LENGTH = 16


def media_key(job_id: str, video_id: str) -> str:
    return f"output/{job_id}/{video_id}.mp4"


def config_key(name: str) -> str:
    return f"config/{name}"


def rate_window(now_epoch: int) -> int:
    """Return the start of the UTC hour containing ``now_epoch``."""
    return now_epoch - now_epoch % RATE_WINDOW_SECONDS


def rate_key(window_start: int, token: str) -> str:
    return f"RATE#{window_start}#{credential_fingerprint(token)}"


def credential_fingerprint(token: str) -> str:
    """Return the first 16 hex characters of SHA-256(token), never the token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:_FINGERPRINT_LENGTH]
