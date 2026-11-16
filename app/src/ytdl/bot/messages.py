"""Fixed bot replies, page splitting and small formatting helpers (T034).

Every string in this module is plain text: no markdown, no ``parse_mode``, no emoji.
The help block is the only exception and arrives with US4. Templates are ``str.format``
templates so a caller cannot silently reword them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ytdl.config import Config

ACCEPTED_VIDEO = "Accepted: {video_id} at {tier} ({ceiling}p). Working on it."
ACCEPTED_AUDIO = "Accepted: {video_id} as MP3 audio. Working on it."
IN_PROGRESS = "Already working on {video_id} at {tier}. I will send the link when it is ready."
RATE_LIMITED = "Hourly limit reached. Capacity returns at {retry_at} UTC."
TWO_LINKS = "Send one link at a time, followed by one resolution word."
UNKNOWN_RESOLUTION = 'Unknown resolution "{word}". Use low, medium, high, veryhigh or mp3.'
FORMAT_HINT = "Send a YouTube link followed by a resolution word, or /help."
NOT_APPROVED = "This bot is private."
GROUP_CHAT = "This bot only works in a private chat."

COMPLETED = (
    "Ready: {title}\n"
    "Quality: {quality}\n"
    "Size: {size}\n"
    "\n"
    "Download (expires {expires} UTC):\n"
    "{url}\n"
    "\n"
    "The file stays available until {available_until}. "
    "Send the same request again for a fresh link."
)
REUSED = (
    "Already stored: {title}\n"
    "Quality: {quality}\n"
    "\n"
    "Download (expires {expires} UTC):\n"
    "{url}\n"
    "\n"
    "The file stays available until {available_until}."
)
FAILED = "{message} {next_step}"
NEXT_STEP_RETRY = "Send the request again."
EXPIRED = "The stored file has expired; submit the video link again."
INVALID_LINK = "The submitted link is not a supported single YouTube video."

# Library commands (US3). The list line is assembled by bot/library.py.
NO_VIDEOS = "No videos are stored."
MORE_ITEMS = "... and {count} more. Delete some items to see the rest."
DELETED = "Deleted {name}."
DELETED_NUMBER = "Deleted {number}: {name}."
NO_SUCH_ITEM = "No item is named {name}."
NO_SUCH_NUMBER = "No item is numbered {number}."
EMPTY_CONFIRM = 'This will delete {count} items. Reply "/empty confirm" to proceed.'
EMPTY_DONE = "Deleted {count} items."
EMPTY_REMAIN = 'Deleted {count} items; {remaining} remain, reply "/empty confirm" again.'
EMPTY_NOTHING = "There was nothing to delete."
NO_HISTORY = "No history yet."

# The operator's fixed help text. Every line is a literal so the block in
# contracts/bot-commands.md can be compared character for character.
HELP = (
    "📚 Available commands:\n"
    "\n"
    "/start - Start the bot\n"
    "/list - List all your videos on the server\n"
    "/delete filename.zip - Delete a specific video\n"
    "/empty - Delete all videos\n"
    "/history - Show your message history\n"
    "/info - Display yt-dlp version\n"
    "/test - Test the download pipeline\n"
    "/help - Display this help\n"
    "\n"
    "To download a YouTube video:\n"
    '"[URL] [resolution]"\n'
    "\n"
    "Available resolutions:\n"
    "• low - low quality (240p)\n"
    "• medium - medium quality (480p)\n"
    "• high - high quality (720p)\n"
    "• veryhigh - very high quality (1080p)\n"
    "• mp3 - audio only (MP3 format)\n"
    "\n"
    'Example: "https://www.youtube.com/watch?v=example medium"'
)
START = (
    "Hello. Send a YouTube link with a resolution word and I will fetch it.\n"
    "\n"
    "Send /help for the full command list."
)
# /start answers with the greeting and the fixed block, so a first-time operator
# sees the commands without typing /help. One message, one notification.
START_REPLY = START + "\n\n" + HELP
TEST_STARTED = "Pipeline test started."
INFO = (
    "yt-dlp {version}\n"
    "Quality ceiling: {max_height}p\n"
    "Retention: {retention_days} days\n"
    "Link lifetime: {minutes} minutes\n"
    "Rate limit: {rate_limit} requests per hour"
)


def info_text(config: Config) -> str:
    """Render the /info block from the running configuration, never a typed version."""
    return INFO.format(
        version=config.ytdlp_version or "unknown",
        max_height=config.max_height,
        retention_days=config.retention_days,
        minutes=config.link_ttl_seconds // 60,
        rate_limit=config.rate_limit_per_hour,
    )


_BYTES_PER_MB = 1024 * 1024


def format_size(size_bytes: int | None) -> str:
    """Return a size in mebibytes with one decimal place, or ``0.0 MB`` when unknown."""
    if not size_bytes:
        return "0.0 MB"
    return f"{size_bytes / _BYTES_PER_MB:.1f} MB"


def format_minute(epoch: int) -> str:
    """Return the UTC minute of an epoch second as ``YYYY-MM-DD HH:MM``."""
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%d %H:%M")


def format_date(epoch: int) -> str:
    """Return the UTC day of an epoch second as ``YYYY-MM-DD``."""
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%d")


def paginate(text: str, limit: int = 3500) -> list[str]:
    """Split text on line boundaries into pages of at most ``limit`` characters.

    A single line longer than the limit is emitted whole, because splitting it would
    break the download link it probably contains. Rejoining the pages with a newline
    reproduces the input exactly.
    """
    if len(text) <= limit:
        return [text]

    pages: list[str] = []
    current: list[str] = []
    current_length = 0

    for line in text.split("\n"):
        extra = len(line) + (1 if current else 0)
        if current and current_length + extra > limit:
            pages.append("\n".join(current))
            current = [line]
            current_length = len(line)
        else:
            current.append(line)
            current_length += extra

    pages.append("\n".join(current))
    return pages
