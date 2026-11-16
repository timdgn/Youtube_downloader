"""Quality labels, ceiling capping, and label derivation (T078).

Pure functions only: the API resolves the request, the worker derives what was
actually produced, and neither needs a network or a clock.
"""

from __future__ import annotations

# 240p is the `low` tier's ceiling (research D-07). 1440p and 2160p are accepted so
# that FR-011 can cap them to the ceiling instead of failing them (US3 acceptance
# scenario 4). They are never produced.
QUALITIES: tuple[str, ...] = ("240p", "360p", "480p", "720p", "1080p", "1440p", "2160p")

_HEIGHTS: dict[str, int] = {label: int(label.removesuffix("p")) for label in QUALITIES}
_LABELS: dict[int, str] = {height: label for label, height in _HEIGHTS.items()}


class InvalidQuality(ValueError):
    """A quality label outside :data:`QUALITIES`."""

    def __init__(self, value: str) -> None:
        super().__init__(f"invalid quality {value!r}; expected one of {', '.join(QUALITIES)}")
        self.value = value


def height_for(label: str) -> int:
    """Return the standard height for an accepted label, or raise ``InvalidQuality``."""
    try:
        return _HEIGHTS[label]
    except (KeyError, TypeError):
        raise InvalidQuality(label) from None


def resolve(requested: str | None, ceiling: int) -> int:
    """Resolve a request into the height to look for, never above ``ceiling``.

    An absent request means the ceiling itself; a valid label is capped by it;
    an unknown label is refused rather than guessed at.
    """
    if requested is None:
        return ceiling
    return min(height_for(requested), ceiling)


def cap_requested(requested: str | None, ceiling: int) -> tuple[str | None, int]:
    """Return the label after capping (``None`` when absent) and the resolved height."""
    height = resolve(requested, ceiling)
    if requested is None:
        return None, height
    return label_for(height), height


def label_for(height: int | None) -> str | None:
    """Derive the reported quality from a produced height.

    Standard heights map to their label; any other positive height keeps its own
    value (1079 stays ``"1079p"``) so the report is never rounded up to something
    the file is not.
    """
    if height is None or height <= 0:
        return None
    return _LABELS.get(height, f"{height}p")
