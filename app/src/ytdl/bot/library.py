"""The bot's library: listing, deletion, emptying and history rendering (T052).

``render_list`` and ``live_items`` are pure; ``delete_item`` and ``empty_library``
perform the two writes in the one order that cannot leave a listed item whose file
is already gone: remove the object first, then set the overlay.

The index is eventually consistent, so a delete reports from the write's own
result and never re-reads the index to describe it (research D-05).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ytdl import keys, states
from ytdl.bot import messages
from ytdl.bot.commands import TIERS

LIST_LIMIT = 200
OWNER_LIMIT = 1000


def live_items(jobs: list[dict[str, Any]], now_epoch: int) -> list[dict[str, Any]]:
    """Stored, not deleted, not past retention, newest first as the query returned."""
    return [job for job in jobs if _is_live(job, now_epoch)]


def render_list(
    jobs: list[dict[str, Any]],
    now_epoch: int,
    limit: int = LIST_LIMIT,
) -> str:
    """One line per live item, or the empty-library sentence."""
    live = live_items(jobs, now_epoch)
    if not live:
        return messages.NO_VIDEOS

    lines = [_line(job) for job in live[:limit]]
    if len(live) > limit:
        lines.append(messages.MORE_ITEMS.format(count=len(live) - limit))
    return "\n\n".join(lines)


def delete_item(
    repo: Any,
    store: Any,
    owner_key: str,
    name: str,
    now_epoch: int,
) -> dict[str, Any] | None:
    """Delete exactly the named item, or return None when no live item carries it."""
    live = live_items(repo.query_owner_jobs(owner_key, OWNER_LIMIT), now_epoch)
    target = next((job for job in live if keys.display_name(job) == name), None)
    return None if target is None else _remove(repo, store, target, now_epoch)


def delete_numbered(
    repo: Any,
    store: Any,
    owner_key: str,
    number: int,
    now_epoch: int,
) -> dict[str, Any] | None:
    """Delete the item carrying ``number``, or return None when no live item has it."""
    live = live_items(repo.query_owner_jobs(owner_key, OWNER_LIMIT), now_epoch)
    target = next((job for job in live if job.get("seq") == number), None)
    return None if target is None else _remove(repo, store, target, now_epoch)


def link_numbered(
    repo: Any,
    owner_key: str,
    number: int,
    now_epoch: int,
) -> dict[str, Any] | None:
    """Return the item carrying ``number``, or None when no live item has it."""
    live = live_items(repo.query_owner_jobs(owner_key, OWNER_LIMIT), now_epoch)
    return next((job for job in live if job.get("seq") == number), None)


def _remove(repo: Any, store: Any, job: dict[str, Any], now_epoch: int) -> dict[str, Any]:
    """Remove the object first, then set the overlay; the reverse can list a dead item."""
    store.delete_object(_media(job))
    repo.mark_deleted(str(job["job_id"]), _iso(now_epoch))
    return job


def empty_library(
    repo: Any,
    store: Any,
    owner_key: str,
    now_epoch: int,
    cap: int = LIST_LIMIT,
) -> tuple[int, int]:
    """Delete up to ``cap`` live items and report how many remain."""
    live = live_items(repo.query_owner_jobs(owner_key, OWNER_LIMIT), now_epoch)
    for job in live[:cap]:
        store.delete_object(_media(job))
        repo.mark_deleted(str(job["job_id"]), _iso(now_epoch))
    deleted = len(live[:cap])
    return deleted, len(live) - deleted


def render_history(
    interactions: list[dict[str, Any]],
    states_by_job_id: dict[str, str],
    now_epoch: int,
) -> str:
    """Ten lines at most, newest first, with no token, link or query string."""
    if not interactions:
        return messages.NO_HISTORY

    lines: list[str] = []
    for item in interactions[:10]:
        created_at = _epoch(item.get("created_at"))
        when = messages.format_minute(created_at if created_at is not None else now_epoch)
        text = _plain(str(item.get("text") or ""))
        outcome = _outcome(item, states_by_job_id)
        lines.append(f"{when} UTC | {text} | {outcome}")
    return "\n".join(lines)


def _line(job: dict[str, Any]) -> str:
    fields = " | ".join(
        (
            _title(job),
            _produced(job),
            messages.format_size(job.get("media_size_bytes")),
        )
    )
    return f"{job['seq']}. {fields}" if _number(job.get("seq")) is not None else fields


def _number(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _is_live(job: dict[str, Any], now_epoch: int) -> bool:
    if job.get("state") != states.COMPLETED or job.get("deleted_at") is not None:
        return False
    expires_at = job.get("media_expires_at")
    if isinstance(expires_at, bool) or not isinstance(expires_at, int):
        return False
    return expires_at >= now_epoch


def _title(job: dict[str, Any]) -> str:
    return str(job.get("video_title") or "untitled")


def _produced(job: dict[str, Any]) -> str:
    label = job.get("produced_quality")
    if isinstance(label, str) and label:
        return label
    if job.get("media_kind") == "audio":
        return "mp3"
    return "unknown"


def _media(job: dict[str, Any]) -> str:
    if job.get("media_key"):
        return str(job["media_key"])
    tier = TIERS.get(str(job.get("requested_tier")))
    extension = tier.extension if tier is not None else "mp4"
    return keys.media_key(str(job["job_id"]), str(job["video_id"]), extension)


def _outcome(item: dict[str, Any], states_by_job_id: dict[str, str]) -> str:
    job_id = item.get("job_id")
    if isinstance(job_id, str) and job_id in states_by_job_id:
        return states_by_job_id[job_id]
    outcome = item.get("outcome")
    if not isinstance(outcome, str) or not outcome:
        return "unknown"
    return _plain(outcome)


def _plain(value: str) -> str:
    """One line, so a stored title or outcome cannot break the history format."""
    return " ".join(value.replace("|", "/").split())


def _epoch(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    return int(parsed.timestamp())


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
