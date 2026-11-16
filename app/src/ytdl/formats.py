"""Choose a video plus audio pair, or the best audio-only stream (T079).

The input is a ``yt-dlp --dump-single-json`` payload. Selection is pure and
deterministic: H.264 in MP4 with AAC audio when the source offers it, otherwise
the best rendition at or below the ceiling, with the codecs recorded so the job
can report what was actually produced (research D-01). Audio-only requests take
the best audio stream and never a muxed video stream (research D-08).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_MP4 = "mp4"
_AAC_EXTENSIONS = (_MP4, "m4a")
_AVC1 = "avc1"
_MP4A = "mp4a"
_NONE = "none"
_BEST_AUDIO = "ba"


@dataclass(frozen=True)
class Selection:
    """What to hand yt-dlp, and what the resulting file is expected to contain."""

    format_selector: str
    expected_height: int | None
    vcodec: str | None
    acodec: str | None


class NoUsableFormat(RuntimeError):
    """The metadata offers no usable video/audio pair, or no audio-only stream."""


def select(
    metadata: dict[str, Any],
    ceiling: int,
    service_ceiling: int | None = None,
) -> Selection:
    """Pick the best pair at or below ``ceiling`` from a probe payload.

    When nothing fits under the requested ceiling, the lowest rendition at or
    below ``service_ceiling`` is delivered instead, and its real height is
    reported (research D-07). ``service_ceiling`` is the cost guard, so the
    fallback can never exceed it; ``None`` disables the fallback entirely.
    """
    formats = _formats(metadata)
    videos = [fmt for fmt in formats if _video_height(fmt) is not None]
    audios = [fmt for fmt in formats if _codec(fmt.get("acodec")) is not None]

    below = [fmt for fmt in videos if _video_height(fmt) <= ceiling]
    video = _highest_video(below) if below else _fallback_video(videos, ceiling, service_ceiling)
    if not audios:
        raise NoUsableFormat("no audio stream in the metadata")

    audio = max(_aac_audio(audios), key=_audio_rank)
    selector_ceiling = ceiling if below else _video_height(video) or ceiling

    return Selection(
        format_selector=_format_selector(video, audio, selector_ceiling),
        expected_height=_video_height(video),
        vcodec=_codec(video.get("vcodec")),
        acodec=_codec(audio.get("acodec")),
    )


def select_audio(metadata: dict[str, Any]) -> Selection:
    """Pick the best audio-only stream, never a muxed video stream (D-08)."""
    audios = [
        fmt
        for fmt in _formats(metadata)
        if _codec(fmt.get("vcodec")) is None and _codec(fmt.get("acodec")) is not None
    ]
    if not audios:
        raise NoUsableFormat("no audio stream in the metadata")

    audio = max(audios, key=_audio_rank)
    return Selection(
        format_selector=_audio_selector(audio),
        expected_height=None,
        vcodec=None,
        acodec=_codec(audio.get("acodec")),
    )


def _formats(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    raw = metadata.get("formats")
    return [fmt for fmt in raw if isinstance(fmt, dict)] if isinstance(raw, list) else []


def _highest_video(below: list[dict[str, Any]]) -> dict[str, Any]:
    """H.264 MP4 when the candidates offer it, otherwise the highest rendition."""
    h264 = [fmt for fmt in below if _starts_with(fmt, "vcodec", _AVC1) and fmt.get("ext") == _MP4]
    return max(h264 or below, key=_video_rank)


def _fallback_video(
    videos: list[dict[str, Any]],
    ceiling: int,
    service_ceiling: int | None,
) -> dict[str, Any]:
    """The lowest rendition at or below the service ceiling, or a refusal."""
    if service_ceiling is None:
        raise NoUsableFormat(f"no video stream at or below {ceiling}p")
    allowed = [fmt for fmt in videos if _video_height(fmt) <= service_ceiling]
    if not allowed:
        raise NoUsableFormat(f"no video stream at or below {service_ceiling}p")
    lowest = min(_video_height(fmt) for fmt in allowed)
    at_lowest = [fmt for fmt in allowed if _video_height(fmt) == lowest]
    return _highest_video(at_lowest)


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


def _audio_selector(audio: dict[str, Any]) -> str:
    """The audio stream's id, or yt-dlp's best-audio expression when it has none."""
    audio_id = _format_id(audio)
    return audio_id if audio_id is not None else _BEST_AUDIO
