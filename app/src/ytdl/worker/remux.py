"""ffmpeg stream-copy remux and ffprobe output inspection (T052)."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

_FFMPEG = "ffmpeg"
_FFPROBE = "ffprobe"


class RemuxError(RuntimeError):
    """ffmpeg or ffprobe failed, timed out, or produced nothing usable."""

    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


def build_ffmpeg_args(source: str, dest: str) -> list[str]:
    """Build the one-invocation, stream-copy-only remux command (D-01)."""
    return [
        _FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        source,
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        dest,
    ]


def remux_to_mp4(source: str, dest: str, timeout_seconds: int = 300) -> None:
    """Copy the streams of ``source`` into a faststart MP4 at ``dest``.

    Never re-encodes. Raises :class:`RemuxError` with ffmpeg's stderr on failure.
    """
    command = build_ffmpeg_args(source, dest)
    completed = _run(command, timeout_seconds, "ffmpeg")

    if completed.returncode != 0:
        raise RemuxError(
            f"ffmpeg exited with code {completed.returncode}", stderr=_stderr(completed)
        )
    if not os.path.isfile(dest):
        raise RemuxError("ffmpeg produced no output file", stderr=_stderr(completed))
    if os.path.getsize(dest) == 0:
        raise RemuxError("ffmpeg produced an empty file", stderr=_stderr(completed))


def build_mp3_args(source: str, dest: str) -> list[str]:
    """Build the one-invocation MP3 encode command (research D-08)."""
    return [
        _FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        source,
        "-vn",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        dest,
    ]


def convert_to_mp3(source: str, dest: str, timeout_seconds: int = 600) -> None:
    """Encode the audio stream of ``source`` as a 192 kbps MP3 at ``dest``.

    Re-encodes with the pinned ``libmp3lame``; the video stream is dropped.
    Raises :class:`RemuxError` with ffmpeg's stderr on failure.
    """
    command = build_mp3_args(source, dest)
    completed = _run(command, timeout_seconds, "ffmpeg")

    if completed.returncode != 0:
        raise RemuxError(
            f"ffmpeg exited with code {completed.returncode}", stderr=_stderr(completed)
        )
    if not os.path.isfile(dest):
        raise RemuxError("ffmpeg produced no output file", stderr=_stderr(completed))
    if os.path.getsize(dest) == 0:
        raise RemuxError("ffmpeg produced an empty file", stderr=_stderr(completed))


def probe_output(path: str, timeout_seconds: int = 60) -> dict[str, Any]:
    """Return the produced height and codecs, parsed from ffprobe's JSON only."""
    command = [_FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", path]
    completed = _run(command, timeout_seconds, "ffprobe")

    if completed.returncode != 0:
        raise RemuxError(
            f"ffprobe exited with code {completed.returncode}", stderr=_stderr(completed)
        )

    try:
        payload = json.loads(completed.stdout or "")
    except json.JSONDecodeError:
        raise RemuxError("ffprobe returned malformed JSON", stderr=_stderr(completed)) from None

    streams = payload.get("streams") if isinstance(payload, dict) else None
    if not isinstance(streams, list):
        raise RemuxError("ffprobe returned no stream list", stderr=_stderr(completed))

    video = _first_stream(streams, "video")
    audio = _first_stream(streams, "audio")
    return {
        "height": _height(video),
        "vcodec": _codec(video),
        "acodec": _codec(audio),
    }


def _run(command: list[str], timeout_seconds: int, binary: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RemuxError(f"{binary} timed out", stderr="timeout") from None
    except OSError as error:
        raise RemuxError(f"{binary} could not start: {error}", stderr=str(error)) from None


def _stderr(completed: subprocess.CompletedProcess[str]) -> str:
    return completed.stderr or ""


def _first_stream(streams: list[Any], codec_type: str) -> dict[str, Any] | None:
    for stream in streams:
        if isinstance(stream, dict) and stream.get("codec_type") == codec_type:
            return stream
    return None


def _height(stream: dict[str, Any] | None) -> int | None:
    if stream is None:
        return None
    height = stream.get("height")
    if isinstance(height, bool) or not isinstance(height, int):
        return None
    return height


def _codec(stream: dict[str, Any] | None) -> str | None:
    if stream is None:
        return None
    codec = stream.get("codec_name")
    return codec if isinstance(codec, str) else None
