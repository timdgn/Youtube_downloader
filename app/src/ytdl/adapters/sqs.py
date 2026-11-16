"""SQS access for job messages (T025)."""

from __future__ import annotations

import json
from typing import Any


class JobQueue:
    """Sends the pointer message the worker consumes."""

    def __init__(self, client: Any, url: str) -> None:
        self._client = client
        self._url = url

    def enqueue(self, body: dict[str, Any]) -> None:
        """Send ``body`` as the JSON message body, with no attributes and no delay."""
        self._client.send_message(
            QueueUrl=self._url,
            MessageBody=json.dumps(body, separators=(",", ":")),
        )
