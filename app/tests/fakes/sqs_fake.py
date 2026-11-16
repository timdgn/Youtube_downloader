"""In-memory job queue for contract tests."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any


class FakeJobQueue:
    """Same method set as ``JobQueue``, recording every enqueued body."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.messages: list[dict[str, Any]] = []

    @property
    def writes(self) -> list[tuple[str, dict[str, Any]]]:
        """Every enqueue is a write, exposed for read-only path assertions."""
        return list(self.calls)

    def enqueue(self, body: dict[str, Any]) -> None:
        payload = json.loads(json.dumps(body))
        self.calls.append(("enqueue", {"body": deepcopy(payload)}))
        self.messages.append(payload)
