"""The worker's US1 happy-path pipeline (T053)."""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from typing import Any

from ytdl import keys
from ytdl.config import Config
from ytdl.worker.remux import probe_output, remux_to_mp4

MEDIA_RETENTION_SECONDS = 604_800

_SOURCE_URL = "https://www.youtube.com/watch?v={video_id}"
_OUTPUT_FILENAME = "output.mp4"
_QUALITY_HEIGHTS = (360, 480, 720, 1080)


def quality_label(height: int | None) -> str | None:
    """Map a produced height onto the nearest label at or below it.

    Never rounds up: a height below 360 has no label, and a height between two
    labels reports the lower one. US3 moves this into ``ytdl.quality``.
    """
    if height is None:
        return None
    for label in reversed(_QUALITY_HEIGHTS):
        if height >= label:
            return f"{label}p"
    return None


def format_selector(max_height: int) -> str:
    """US1's fixed selector: H.264 video plus AAC audio, or the best fallback."""
    return f"bv*[height<={max_height}][vcodec^=avc1]+ba[acodec^=mp4a]/b[height<={max_height}]/b"


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
    """Probe, download, remux, probe the output, upload, and return the completion fields.

    Raises rather than swallowing, so the caller decides whether the job is retryable.
    No environment reads and no boto3 clients: every boundary is injected.
    """
    clock = time.time if now is None else now
    started = clock()

    job_id = str(job["job_id"])
    video_id = str(job["video_id"])
    url = _SOURCE_URL.format(video_id=video_id)

    metadata = extractor.probe(url)
    duration = _duration_seconds(metadata.get("duration"))

    source = extractor.download(url, format_selector(config.max_height), workdir)
    output = os.path.join(workdir, _OUTPUT_FILENAME)
    remux_fn(source, output)

    produced = probe_fn(output)
    height = _height(produced.get("height"))
    media = keys.media_key(job_id, video_id)
    size = store.put_file(
        output,
        media,
        "video/mp4",
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
        "produced_height": height,
        "produced_quality": quality_label(height),
        "produced_vcodec": produced.get("vcodec"),
        "produced_acodec": produced.get("acodec"),
        "processing_ms": max(0, int((clock() - started) * 1000)),
    }
    if duration is not None:
        fields["duration_seconds"] = duration
    return fields


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
