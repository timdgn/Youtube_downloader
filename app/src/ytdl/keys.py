"""Deterministic storage keys and rate-limit identities (T020)."""

from __future__ import annotations

import hashlib
from typing import Any

RATE_WINDOW_SECONDS = 3600
RATE_TTL_SECONDS = 7200
_FINGERPRINT_LENGTH = 16


def media_key(job_id: str, video_id: str, extension: str = "mp4") -> str:
    return f"output/{job_id}/{video_id}.{extension}"


def owner_job_key(user_id: int | str) -> str:
    """The GSI hash key of one account's jobs (data-model.md "Index")."""
    return f"tg:{user_id}#job"


def owner_interaction_key(user_id: int | str) -> str:
    """The GSI hash key of one account's interaction history."""
    return f"tg:{user_id}#interaction"


def interaction_pk(update_id: int | str) -> str:
    """The identity of one handled update; also the idempotency key (FR-023)."""
    return f"UPD#{update_id}"


def owner_seq_key(user_id: int | str) -> str:
    """The per-account counter that numbers stored items (FR-013)."""
    return f"SEQ#{user_id}"


def display_name(job: dict[str, Any]) -> str:
    """The one name ``/list`` prints and ``/delete`` accepts (invariant 18).

    Derived, never stored: ``<video_id>-<tier>-<last 4 of job_id>.<ext>``.
    """
    extension = "mp3" if job.get("media_kind") == "audio" else "mp4"
    tier = job.get("requested_tier") or "high"
    return f"{job['video_id']}-{tier}-{str(job['job_id'])[-4:]}.{extension}"


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
