"""Minimal Telegram Bot API client built on the standard library (T035).

Only ``sendMessage`` and ``getWebhookInfo`` are needed. The error policy follows
``contracts/telegram-webhook.md``: 400 and 403 are dropped because repeating them cannot
help, while 401, 429, 5XX and network failures raise so the caller (Telegram's retry, or
SNS) can act. There is no in-process retry, and no exception message contains the token.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from ytdl.bot.messages import paginate

_API_BASE = "https://api.telegram.org/bot"
_DROPPED_STATUSES = frozenset({400, 403})

Opener = Callable[..., Any]


class TelegramError(Exception):
    """A Telegram call that the caller may usefully retry or alarm on."""


class TelegramClient:
    """Talk to one bot, one request at a time."""

    def __init__(self, token: str, timeout: float = 10, *, opener: Opener | None = None) -> None:
        self._token = token
        self._timeout = timeout
        self._opener: Opener = opener if opener is not None else urllib.request.urlopen

    def send_message(self, chat_id: int, text: str) -> None:
        """Send plain text, split on line boundaries, one page per request in order."""
        for page in paginate(text):
            self._post("sendMessage", {"chat_id": chat_id, "text": page})

    def get_webhook_info(self) -> dict[str, Any]:
        """Return the ``WebhookInfo`` object reported for this bot."""
        payload = self._post("getWebhookInfo", {})
        if not payload:
            return {}
        result = payload.get("result", {})
        return result if isinstance(result, dict) else {}

    def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        url = f"{_API_BASE}{self._token}/{method}"
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            response = self._opener(request, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            # The HTTPError holds the URL, and the URL holds the token: do not chain it.
            self._check_status(method, exc.code)
            return None
        except (urllib.error.URLError, OSError) as exc:
            raise TelegramError(f"telegram {method} failed: network error") from exc

        self._check_status(method, response.status)
        if response.status in _DROPPED_STATUSES:
            return None

        try:
            decoded = json.loads(response.read().decode("utf-8"))
        except ValueError:
            return None
        return decoded if isinstance(decoded, dict) else None

    @staticmethod
    def _check_status(method: str, status: int) -> None:
        if 200 <= status < 300 or status in _DROPPED_STATUSES:
            return
        raise TelegramError(f"telegram {method} failed with status {status}")
