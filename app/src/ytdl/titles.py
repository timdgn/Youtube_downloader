"""Display titles, sanitised so a source title cannot break a list line (FR-012).

Pure: no I/O, no clock. The worker writes the result onto the job record; the bot
only reads it.
"""

from __future__ import annotations

MAX_TITLE_LENGTH = 80
_FALLBACK_TITLE = "untitled"


def sanitise_title(title: str | None) -> str:
    """Strip control characters, collapse whitespace, and truncate to 80 characters."""
    if not isinstance(title, str):
        return _FALLBACK_TITLE

    cleaned = "".join(_replacement(character) for character in title)
    collapsed = " ".join(cleaned.split())
    stripped = collapsed.strip()

    if not stripped:
        return _FALLBACK_TITLE
    return stripped[:MAX_TITLE_LENGTH].strip() or _FALLBACK_TITLE


def _replacement(character: str) -> str:
    """Whitespace controls become a space; other controls are removed."""
    if character in "\t\n\r\v\f":
        return " "
    return "" if _is_control(character) else character


def _is_control(character: str) -> bool:
    """C0 control characters and DEL."""
    return "\x00" <= character <= "\x1f" or character == "\x7f"
