"""Choose a video plus audio pair from probe metadata (T079).

The input is a ``yt-dlp --dump-single-json`` payload. Selection is pure and
deterministic: H.264 in MP4 with AAC audio when the source offers it, otherwise
the best rendition at or below the ceiling, with the codecs recorded so the job
can report what was actually produced (research D-01).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_MP4 = "mp4"
_AAC_EXTENSIONS = (_MP4, "m4a")
_AVC1 = "avc1"
_MP4A = "mp4a"
_NONE = "none"


@dataclass(frozen=True)
class Selection:
    """What to hand yt-dlp, and what the resulting file is expected to contain."""

    format_selector: str
    expected_height: int | None
    vcodec: str | None
    acodec: str | None


class NoUsableFormat(RuntimeError):
    """The metadata offers no video/audio pair at or below the ceiling."""


def select(metadata: dict[str, Any], max_height: int) -> Selection:
    """Pick the best pair at or below ``max_height`` from a probe payload."""
    raw = metadata.get("formats")
    formats = [fmt for fmt in raw if isinstance(fmt, dict)] if isinstance(raw, list) else []

    videos = [fmt for fmt in formats if _video_height(fmt) is not None]
    audios = [fmt for fmt in formats if _codec(fmt.get("acodec")) is not None]

    below = [fmt for fmt in videos if _video_height(fmt) <= max_height]
    if not below:
        raise NoUsableFormat(f"no video stream at or below {max_height}p")
    if not audios:
        raise NoUsableFormat("no audio stream in the metadata")

    h264 = [fmt for fmt in below if _starts_with(fmt, "vcodec", _AVC1) and fmt.get("ext") == _MP4]
    video = max(h264 or below, key=_video_rank)
    audio = max(_aac_audio(audios), key=_audio_rank)

    return Selection(
        format_selector=_format_selector(video, audio, max_height),
        expected_height=_video_height(video),
        vcodec=_codec(video.get("vcodec")),
        acodec=_codec(audio.get("acodec")),
    )


def _video_height(fmt: dict[str, Any]) -> int | None:
    """The height of a usable video stream, or ``None`` for anything else."""
    if _codec(fmt.get("vcodec")) is None:
        return None
    height = fmt.get("height")
    if isinstance(height, bool) or not isinstance(height, int):
        return None
    return height


def _codec(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    codec = value.strip()
    if not codec or codec == _NONE:
        return None
    return codec


def _starts_with(fmt: dict[str, Any], key: str, prefix: str) -> bool:
    codec = _codec(fmt.get(key))
    return codec is not None and codec.startswith(prefix)


def _aac_audio(audios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """AAC audio in an MP4-family container, or every audio stream as a fallback."""
    aac = [
        fmt
        for fmt in audios
        if _starts_with(fmt, "acodec", _MP4A) and fmt.get("ext") in _AAC_EXTENSIONS
    ]
    return aac or audios


def _video_rank(fmt: dict[str, Any]) -> tuple[int, float]:
    return (_video_height(fmt) or 0, _number(fmt.get("tbr")))


def _audio_rank(fmt: dict[str, Any]) -> tuple[float, float]:
    return (_number(fmt.get("abr")), _number(fmt.get("tbr")))


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


def _format_selector(video: dict[str, Any], audio: dict[str, Any], max_height: int) -> str:
    """Prefer explicit ids; fall back to an expression when either id is missing."""
    video_id = _format_id(video)
    audio_id = _format_id(audio)
    if video_id is not None and audio_id is not None:
        return f"{video_id}+{audio_id}"
    return f"bv*[height<={max_height}][vcodec^=avc1]+ba[acodec^=mp4a]/b[height<={max_height}]"


def _format_id(fmt: dict[str, Any]) -> str | None:
    value = fmt.get("format_id")
    if not isinstance(value, str) or not value:
        return None
    return value
