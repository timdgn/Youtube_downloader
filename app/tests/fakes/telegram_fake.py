"""Programmable Telegram client for contract tests."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

DEFAULT_WEBHOOK_INFO: dict[str, Any] = {
    "url": "https://api.example.invalid/telegram/webhook",
    "has_custom_certificate": False,
    "pending_update_count": 0,
    "max_connections": 5,
    "allowed_updates": ["message"],
}

# Marks a call the real client drops quietly after a 400 or a 403.
DROPPED = object()

# The statuses the real client distinguishes, so a test can program one call.
_STATUS_ERRORS = {
    401: "unauthorized",
    429: "too many requests",
    500: "internal server error",
}


def status_error(status: int) -> BaseException:
    """Return the exception the real client raises for ``status``.

    Imported lazily so the fake does not require the client module to exist
    before the test suite is importable.
    """
    if status in (400, 403):
        raise ValueError("400 and 403 return quietly; there is no error to raise")
    from ytdl.bot.client import TelegramError

    return TelegramError(f"telegram answered {status}: {_STATUS_ERRORS.get(status, 'error')}")


class FakeTelegramClient:
    """Same method set as ``TelegramClient``, recording every call."""

    def __init__(
        self,
        webhook_info: dict[str, Any] | None = None,
        errors: list[Any] | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.messages: list[dict[str, Any]] = []
        self.webhook_info = deepcopy(DEFAULT_WEBHOOK_INFO if webhook_info is None else webhook_info)
        self.errors: list[Any] = list(errors or [])

    def send_message(self, chat_id: int | str, text: str) -> None:
        self.calls.append(("send_message", {"chat_id": chat_id, "text": text}))
        outcome = self.errors.pop(0) if self.errors else None
        if outcome is DROPPED:
            return
        if outcome is not None:
            raise outcome
        self.messages.append({"chat_id": chat_id, "text": text})

    def get_webhook_info(self) -> dict[str, Any]:
        self.calls.append(("get_webhook_info", {}))
        return deepcopy(self.webhook_info)

    def fail_next(self, *outcomes: Any) -> None:
        """Queue one outcome per upcoming ``send_message`` call."""
        self.errors.extend(outcomes)
