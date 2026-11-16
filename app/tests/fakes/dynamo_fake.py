"""In-memory DynamoDB repository for contract tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from ytdl import keys, states
from ytdl.adapters.dynamo import (
    RETRY_AFTER_SECONDS,
    TTL_AFTER_EXPIRY_SECONDS,
    TTL_UNCOMPLETED_SECONDS,
    RateLimited,
)

_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_WRITE_METHODS = frozenset(
    {
        "create_job_with_rate_increment",
        "claim_job",
        "complete_job",
        "fail_job",
    }
)


class FakeDynamoRepository:
    """Same method set as ``DynamoRepository``, backed by a dict."""

    def __init__(self, table_name: str = "ytdl-test-jobs") -> None:
        self.table_name = table_name
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.items: dict[str, dict[str, Any]] = {}
        self.rate_counts: dict[str, int] = {}

    @property
    def writes(self) -> list[tuple[str, dict[str, Any]]]:
        """Only the mutating calls, so read-only paths can assert emptiness."""
        return [call for call in self.calls if call[0] in _WRITE_METHODS]

    def create_job_with_rate_increment(
        self, job: dict[str, Any], rate_pk: str, limit: int, now_epoch: int
    ) -> None:
        self.calls.append(
            (
                "create_job_with_rate_increment",
                {
                    "job": deepcopy(job),
                    "rate_pk": rate_pk,
                    "limit": limit,
                    "now_epoch": now_epoch,
                },
            )
        )
        count = self.rate_counts.get(rate_pk, 0)
        if count >= limit:
            window_start = keys.rate_window(now_epoch)
            raise RateLimited(retry_at_epoch=window_start + RETRY_AFTER_SECONDS)
        self.rate_counts[rate_pk] = count + 1
        item = {name: value for name, value in deepcopy(job).items() if value is not None}
        item["pk"] = "JOB#" + job["job_id"]
        item.setdefault("ttl", now_epoch + TTL_UNCOMPLETED_SECONDS)
        self.items[item["pk"]] = item

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        self.calls.append(("get_job", {"job_id": job_id}))
        item = self.items.get("JOB#" + job_id)
        return _public(item) if item else None

    def claim_job(self, job_id: str, now_epoch: int, now_iso: str) -> dict[str, Any] | None:
        self.calls.append(
            ("claim_job", {"job_id": job_id, "now_epoch": now_epoch, "now_iso": now_iso})
        )
        item = self.items.get("JOB#" + job_id)
        if item is None:
            return None
        if not _claimable(item, now_epoch):
            return None
        item["state"] = states.PROCESSING
        item["updated_at"] = now_iso
        item["attempt"] = item.get("attempt", 0) + 1
        return _public(item)

    def complete_job(self, job_id: str, fields: dict[str, Any], now_iso: str) -> None:
        self.calls.append(
            ("complete_job", {"job_id": job_id, "fields": deepcopy(fields), "now_iso": now_iso})
        )
        item = self.items.get("JOB#" + job_id)
        if item is None or item.get("state") != states.PROCESSING:
            return
        item.update({name: value for name, value in fields.items() if value is not None})
        item["state"] = states.COMPLETED
        item["updated_at"] = now_iso
        item["ttl"] = fields["media_expires_at"] + TTL_AFTER_EXPIRY_SECONDS

    def fail_job(
        self,
        job_id: str,
        category: str,
        message: str,
        retryable: bool,
        now_iso: str,
    ) -> None:
        self.calls.append(
            (
                "fail_job",
                {
                    "job_id": job_id,
                    "category": category,
                    "message": message,
                    "retryable": retryable,
                    "now_iso": now_iso,
                },
            )
        )
        item = self.items.get("JOB#" + job_id)
        if item is None or item.get("state") != states.PROCESSING:
            return
        item["state"] = states.FAILED
        item["updated_at"] = now_iso
        item["failure_category"] = category
        item["failure_message"] = message
        item["failure_retryable"] = retryable


def _claimable(item: dict[str, Any], now_epoch: int) -> bool:
    state = item.get("state")
    if state == states.ACCEPTED:
        return True
    if state == states.FAILED:
        # Mirrors the third branch of the real transition condition: a retryable
        # failure below the attempt cap may be claimed again.
        return states.can_transition(
            states.FAILED,
            states.PROCESSING,
            retryable=bool(item.get("failure_retryable")),
            attempt=int(item.get("attempt", 0)),
        )
    if state != states.PROCESSING:
        return False
    updated_at = item.get("updated_at")
    if not updated_at:
        return False
    return states.can_transition(
        states.PROCESSING,
        states.PROCESSING,
        updated_at_epoch=_epoch(updated_at),
        now_epoch=now_epoch,
    )


def _epoch(timestamp: str) -> int:
    return int(datetime.strptime(timestamp, _ISO_FORMAT).replace(tzinfo=UTC).timestamp())


def _public(item: dict[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in deepcopy(item).items() if name != "pk"}
