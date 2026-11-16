"""The worker pipeline (T053, T067, T071, T081).

Probe, enforce the caps, select a format pair, download, remux, verify the size,
probe the output, upload, and return the completion fields. Every decision that
can refuse the job is taken before the media transfer, so a refused job costs no
bandwidth.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from typing import Any

from ytdl import errors, formats, keys, limits, quality
from ytdl.config import Config
from ytdl.worker.remux import probe_output, remux_to_mp4

MEDIA_RETENTION_SECONDS = 604_800
DEADLINE_SECONDS = 780

_SOURCE_URL = "https://www.youtube.com/watch?v={video_id}"
_OUTPUT_FILENAME = "output.mp4"
_CONTENT_TYPE_MEDIA = "video/mp4"


class LimitExceeded(Exception):
    """A configured cap refused the job, before or after the transfer."""

    def __init__(self, failure: errors.Failure) -> None:
        super().__init__(failure.category)
        self.failure = failure


class DeadlineExceeded(Exception):
    """The soft deadline passed before Lambda's own ceiling would be reached."""


def process_job(
    job: dict[str, Any],
    *,
    store: Any,
    extractor: Any,
    config: Config,
    workdir: str,
    remux_fn: Callable[[str, str], None] = remux_to_mp4,
    probe_fn: Callable[[str], dict[str, Any]] = probe_output,
    now: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Run the whole pipeline and return the completion fields.

    Raises rather than swallowing, so the caller decides whether the job is
    retryable. No environment reads and no boto3 clients: every boundary is
    injected.
    """
    clock = time.time if now is None else now
    started = clock()

    job_id = str(job["job_id"])
    video_id = str(job["video_id"])
    url = _SOURCE_URL.format(video_id=video_id)

    metadata = extractor.probe(url)
    duration = _duration_seconds(metadata.get("duration"))
    _enforce(limits.check_duration(duration, config.max_duration_seconds))

    ceiling = min(int(job.get("requested_height") or config.max_height), config.max_height)
    selection = formats.select(metadata, ceiling)

    estimated = _estimated_size(metadata, selection)
    if estimated is not None:
        _enforce(limits.check_size(estimated, config.max_output_bytes))

    _check_deadline(clock, started)

    source = extractor.download(url, selection.format_selector, workdir)
    _check_deadline(clock, started)

    output = os.path.join(workdir, _OUTPUT_FILENAME)
    remux_fn(source, output)

    if os.path.getsize(output) > config.max_output_bytes:
        raise LimitExceeded(_limit_failure())

    produced = probe_fn(output)
    height = _height(produced.get("height"))
    label = quality.label_for(height)

    media = keys.media_key(job_id, video_id)
    size = store.put_file(
        output,
        media,
        _CONTENT_TYPE_MEDIA,
        {
            "job-id": job_id,
            "produced-height": "" if height is None else str(height),
            "source-video-id": video_id,
        },
    )

    created_at = int(clock())
    fields: dict[str, Any] = {
        "media_key": media,
        "media_size_bytes": size,
        "media_created_at": created_at,
        "media_expires_at": created_at + MEDIA_RETENTION_SECONDS,
        "produced_vcodec": _text(produced.get("vcodec")) or selection.vcodec,
        "produced_acodec": _text(produced.get("acodec")) or selection.acodec,
        "processing_ms": max(0, int((clock() - started) * 1000)),
    }
    if height is not None:
        fields["produced_height"] = height
    if label is not None:
        fields["produced_quality"] = label
    if duration is not None:
        fields["duration_seconds"] = duration
    return fields


def _enforce(decision: limits.Decision) -> None:
    """Raise for a refused decision; an allowed one carries no failure."""
    if decision.allowed or decision.failure is None:
        return
    raise LimitExceeded(decision.failure)


def _check_deadline(clock: Callable[[], float], started: float) -> None:
    if clock() - started > DEADLINE_SECONDS:
        raise DeadlineExceeded("pipeline exceeded its soft deadline")


def _estimated_size(metadata: dict[str, Any], selection: formats.Selection) -> int | None:
    """Sum the advertised sizes of the selected ids, or None when one is unknown.

    The selector is ``<video id>+<audio id>`` when the metadata has ids; an
    expression selector matches no id, so the estimate is unknown and no guess
    is made.
    """
    wanted = selection.format_selector.split("+")
    if not wanted or not all(wanted):
        return None

    raw = metadata.get("formats")
    entries = raw if isinstance(raw, list) else []
    by_id = {fmt.get("format_id"): fmt for fmt in entries if isinstance(fmt, dict)}

    total = 0
    for format_id in wanted:
        fmt = by_id.get(format_id)
        if fmt is None:
            return None
        size = _format_size(fmt)
        if size is None:
            return None
        total += size
    return total


def _format_size(fmt: dict[str, Any]) -> int | None:
    for key in ("filesize", "filesize_approx"):
        value = fmt.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _limit_failure() -> errors.Failure:
    return errors.Failure(
        errors.LIMIT_EXCEEDED,
        errors.MESSAGES[errors.LIMIT_EXCEEDED],
        False,
    )


def _duration_seconds(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return int(number) if number.is_integer() else number


def _height(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value
