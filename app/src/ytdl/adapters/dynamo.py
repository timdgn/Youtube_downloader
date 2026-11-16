"""DynamoDB access for job records and rate windows (T023)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from ytdl import keys, states

TTL_AFTER_EXPIRY_SECONDS = 2_592_000
TTL_UNCOMPLETED_SECONDS = TTL_AFTER_EXPIRY_SECONDS + 604_800
RETRY_AFTER_SECONDS = 3600
INTERACTION_TTL_SECONDS = 2_592_000  # 30 days: history is not a permanent record.

_JOB_PREFIX = "JOB#"
_OWNER_INDEX = "owner-created-index"
_RATE_CONDITION = "attribute_not_exists(#count) OR #count < :limit"

_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()


class RateLimited(Exception):
    """The hourly submission budget for this credential is exhausted."""

    def __init__(self, retry_at_epoch: int) -> None:
        super().__init__(f"rate limit reached, retry after epoch {retry_at_epoch}")
        self.retry_at_epoch = retry_at_epoch


class DuplicateUpdate(Exception):
    """This Telegram update was already handled, so nothing was written."""


class DynamoRepository:
    """Reads and conditionally writes the one table."""

    def __init__(self, client: Any, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def create_job_with_rate_increment(
        self,
        job: dict[str, Any],
        rate_pk: str,
        limit: int,
        now_epoch: int,
        interaction: dict[str, Any] | None = None,
    ) -> None:
        """Create the job and consume one unit of rate capacity atomically.

        When ``interaction`` is given, the ``UPD#<update_id>`` item is written in
        the same transaction with ``attribute_not_exists(pk)``, so a redelivered
        update cancels all three actions and nothing is accepted (research D-06).
        """
        window_start = keys.rate_window(now_epoch)
        item = _without_none(job)
        item["pk"] = _job_pk(job["job_id"])
        item["ttl"] = now_epoch + TTL_UNCOMPLETED_SECONDS

        actions: list[dict[str, Any]] = [
            {"Put": {"TableName": self._table_name, "Item": _attribute_map(item)}},
            {
                "Update": {
                    "TableName": self._table_name,
                    "Key": {"pk": _attribute(rate_pk)},
                    "UpdateExpression": "SET #ttl = :ttl ADD #count :one",
                    "ConditionExpression": _RATE_CONDITION,
                    "ExpressionAttributeNames": {"#count": "count", "#ttl": "ttl"},
                    "ExpressionAttributeValues": _attribute_map(
                        {
                            ":one": 1,
                            ":limit": limit,
                            ":ttl": window_start + keys.RATE_TTL_SECONDS,
                        }
                    ),
                }
            },
        ]
        if interaction is not None:
            actions.append(
                {
                    "Put": {
                        "TableName": self._table_name,
                        "Item": _attribute_map(_interaction_item(interaction)),
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }
                }
            )

        try:
            self._client.transact_write_items(TransactItems=actions)
        except ClientError as error:
            if _failed_at(error, index=1):
                raise RateLimited(retry_at_epoch=window_start + RETRY_AFTER_SECONDS) from None
            if interaction is not None and _failed_at(error, index=2):
                raise DuplicateUpdate from None
            raise

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key={"pk": _attribute(_job_pk(job_id))},
            ConsistentRead=True,
        )
        item = response.get("Item")
        return _without_partition_key(_plain_item(item)) if item else None

    def get_jobs(self, job_ids: list[str]) -> list[dict[str, Any]]:
        """Batch-read job records consistently; unprocessed keys are not retried.

        The caller only uses this to decorate history lines, so returning what the
        one call produced is preferable to a retry loop inside a chat request.
        """
        if not job_ids:
            return []

        response = self._client.batch_get_item(
            RequestItems={
                self._table_name: {
                    "Keys": [{"pk": _attribute(_job_pk(job_id))} for job_id in job_ids],
                    "ConsistentRead": True,
                }
            }
        )
        items = response.get("Responses", {}).get(self._table_name, [])
        return [_without_partition_key(_plain_item(item)) for item in items]

    def query_owner_jobs(self, owner_key: str, limit: int) -> list[dict[str, Any]]:
        """One account's jobs, newest first, capped by ``limit``.

        The deleted overlay is filtered in Python by the caller: a query filter
        would consume read capacity for rows it then discards.
        """
        items = self._query_owner(owner_key, limit)
        return [_without_partition_key(_plain_item(item)) for item in items]

    def query_interactions(self, owner_key: str, limit: int = 10) -> list[dict[str, Any]]:
        """One account's interaction history, newest first."""
        return [_plain_item(item) for item in self._query_owner(owner_key, limit)]

    def _query_owner(self, owner_key: str, limit: int) -> list[dict[str, Any]]:
        response = self._client.query(
            TableName=self._table_name,
            IndexName=_OWNER_INDEX,
            KeyConditionExpression="owner_key = :owner",
            ExpressionAttributeValues=_attribute_map({":owner": owner_key}),
            ScanIndexForward=False,
            Limit=limit,
        )
        items = response.get("Items")
        return list(items) if isinstance(items, list) else []

    def put_interaction(self, item: dict[str, Any]) -> None:
        """Write one handled-update record; repeated updates overwrite harmlessly."""
        self._client.put_item(
            TableName=self._table_name,
            Item=_attribute_map(_interaction_item(item)),
        )

    def mark_deleted(self, job_id: str, now_iso: str) -> None:
        """Set the deleted overlay on a completed, not-yet-deleted job."""
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={"pk": _attribute(_job_pk(job_id))},
                UpdateExpression="SET #deleted_at = :now",
                ConditionExpression="#state = :completed AND attribute_not_exists(#deleted_at)",
                ExpressionAttributeNames={"#deleted_at": "deleted_at", "#state": "state"},
                ExpressionAttributeValues=_attribute_map(
                    {":now": now_iso, ":completed": states.COMPLETED}
                ),
            )
        except ClientError as error:
            if _is_conditional_failure(error):
                return
            raise

    def claim_job(self, job_id: str, now_epoch: int, now_iso: str) -> dict[str, Any] | None:
        """Move accepted, stale processing, or a retryable failure to processing.

        Every claim increments ``attempt``, so the retry branch can stop after
        ``states.MAX_ATTEMPTS`` claims.
        """
        values = {
            ":accepted": states.ACCEPTED,
            ":processing": states.PROCESSING,
            ":failed": states.FAILED,
            ":true": True,
            ":max": states.MAX_ATTEMPTS,
            ":now": now_iso,
            ":stale": _iso(now_epoch - states.STALE_CLAIM_SECONDS),
            ":zero": 0,
            ":one": 1,
        }
        try:
            response = self._client.update_item(
                TableName=self._table_name,
                Key={"pk": _attribute(_job_pk(job_id))},
                UpdateExpression=(
                    "SET #state = :processing, #updated_at = :now, "
                    "#attempt = if_not_exists(#attempt, :zero) + :one"
                ),
                ConditionExpression=states.transition_condition(states.PROCESSING),
                ExpressionAttributeNames={
                    "#state": "state",
                    "#failure_retryable": "failure_retryable",
                    "#updated_at": "updated_at",
                    "#attempt": "attempt",
                },
                ExpressionAttributeValues=_attribute_map(values),
                ReturnValues="ALL_NEW",
            )
        except ClientError as error:
            if _is_conditional_failure(error):
                return None
            raise

        return _without_partition_key(_plain_item(response["Attributes"]))

    def complete_job(self, job_id: str, fields: dict[str, Any], now_iso: str) -> None:
        media_expires_at = fields["media_expires_at"]
        update = {
            **fields,
            "state": states.COMPLETED,
            "updated_at": now_iso,
            "ttl": media_expires_at + TTL_AFTER_EXPIRY_SECONDS,
        }
        self._conditional_update(job_id, update, states.COMPLETED)

    def fail_job(
        self,
        job_id: str,
        category: str,
        message: str,
        retryable: bool,
        now_iso: str,
    ) -> None:
        update = {
            "state": states.FAILED,
            "updated_at": now_iso,
            "failure_category": category,
            "failure_message": message,
            "failure_retryable": retryable,
        }
        self._conditional_update(job_id, update, states.FAILED)

    def _conditional_update(self, job_id: str, fields: dict[str, Any], target: str) -> None:
        names = {"#state": "state"}
        values: dict[str, Any] = {":processing": states.PROCESSING}
        assignments: list[str] = []

        for index, (name, value) in enumerate(_without_none(fields).items()):
            names[f"#f{index}"] = name
            values[f":f{index}"] = value
            assignments.append(f"#f{index} = :f{index}")

        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={"pk": _attribute(_job_pk(job_id))},
                UpdateExpression="SET " + ", ".join(assignments),
                ConditionExpression=states.transition_condition(target),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=_attribute_map(values),
            )
        except ClientError as error:
            if _is_conditional_failure(error):
                return
            raise


def _job_pk(job_id: str) -> str:
    return f"{_JOB_PREFIX}{job_id}"


def _interaction_item(item: dict[str, Any]) -> dict[str, Any]:
    """Normalise an interaction: self-describing, with its own 30-day TTL."""
    prepared = _without_none(dict(item))
    prepared["kind"] = "interaction"
    if "ttl" not in prepared:
        prepared["ttl"] = _iso_epoch(str(prepared["created_at"])) + INTERACTION_TTL_SECONDS
    return prepared


def _iso_epoch(timestamp: str) -> int:
    return int(datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp())


def _attribute(value: Any) -> dict[str, Any]:
    """Encode one Python value as a DynamoDB AttributeValue.

    The low-level client accepts nothing else. Floats are not a DynamoDB type, so
    they are carried as an exact decimal instead of being silently truncated.
    """
    if isinstance(value, float):
        value = Decimal(str(value))
    return _SERIALIZER.serialize(value)


def _attribute_map(values: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {name: _attribute(value) for name, value in values.items()}


def _plain_attr(value: dict[str, Any]) -> Any:
    plain = _DESERIALIZER.deserialize(value)
    if isinstance(plain, Decimal):
        return int(plain) if plain == plain.to_integral_value() else float(plain)
    return plain


def _plain_item(item: dict[str, Any]) -> dict[str, Any]:
    """Decode an item and turn every Decimal back into a plain number.

    DynamoDB has no integer type; without this, ints would reach ``json.dumps`` as
    ``Decimal`` and every response body would fail to serialise.
    """
    return {name: _plain_attr(value) for name, value in item.items()}


def _without_none(item: dict[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in item.items() if value is not None}


def _without_partition_key(item: dict[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in item.items() if name != "pk"}


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_conditional_failure(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _failed_at(error: ClientError, index: int) -> bool:
    """True when the transaction was cancelled by the action at ``index``."""
    if error.response.get("Error", {}).get("Code") != "TransactionCanceledException":
        return False
    reasons = error.response.get("CancellationReasons") or []
    if index >= len(reasons):
        return False
    return reasons[index].get("Code") == "ConditionalCheckFailed"
