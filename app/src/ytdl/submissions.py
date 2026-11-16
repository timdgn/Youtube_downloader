"""The one submission path both front doors use (T016).

The terminal API and the Telegram bot validate, rate-limit, record and enqueue
identically. Duplicating that block is the one place where the two front doors
could drift, so it lives here and both callers delegate to it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ytdl import states
from ytdl.config import Config
from ytdl.ids import new_job_id
from ytdl.urls import VideoRef


def build_job(
    reference: VideoRef,
    created_at: str,
    requested_quality: str | None,
    requested_height: int | None,
    owner: dict[str, Any] | None = None,
    media_kind: str = "video",
) -> dict[str, Any]:
    """Return the job item for one validated request.

    ``owner`` carries the bot-only fields (``owner_key``, ``chat_id``,
    ``update_id``, ``requested_tier``, ``is_test``); a terminal submission passes
    ``None`` and the record stays exactly as 001 defined it.
    """
    job: dict[str, Any] = {
        "job_id": new_job_id(),
        "state": states.ACCEPTED,
        "created_at": created_at,
        "updated_at": created_at,
        "source_host": reference.host,
        "video_id": reference.video_id,
        "media_kind": media_kind or "video",
    }
    if requested_quality is not None:
        job["requested_quality"] = requested_quality
        job["requested_height"] = requested_height

    if owner:
        job.update({name: value for name, value in owner.items() if value is not None})
    return job


def submit(
    repo: Any,
    queue: Any,
    config: Config,
    job: dict[str, Any],
    rate_pk: str,
    limit: int,
    now_epoch: int,
    interaction: dict[str, Any] | None = None,
    queue_message: dict[str, Any] | None = None,
    before_enqueue: Callable[[], None] | None = None,
) -> None:
    """Write the job and its rate increment atomically, then enqueue one message.

    ``config`` is accepted so both callers pass the object they already hold; the
    limits themselves come from it. ``before_enqueue`` runs after the transaction
    and before the queue call, which is where the bot acknowledges a job that now
    exists. A rate refusal or a duplicate update raises from the repository, and a
    queue failure propagates: the caller decides.
    """
    repo.create_job_with_rate_increment(job, rate_pk, limit, now_epoch, interaction)
    if before_enqueue is not None:
        before_enqueue()
    queue.enqueue(queue_message if queue_message is not None else _message(job))


def _message(job: dict[str, Any]) -> dict[str, Any]:
    """The terminal API's pointer message, unchanged from 001."""
    return {
        "version": 1,
        "job_id": job["job_id"],
        "video_id": job["video_id"],
        "source_host": job["source_host"],
        "requested_height": job.get("requested_height"),
        "enqueued_at": job["created_at"],
    }
