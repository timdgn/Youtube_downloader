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

_JOB_PREFIX = "JOB#"
_RATE_CONDITION = "attribute_not_exists(#count) OR #count < :limit"

_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()


class RateLimited(Exception):
    """The hourly submission budget for this credential is exhausted."""

    def __init__(self, retry_at_epoch: int) -> None:
        super().__init__(f"rate limit reached, retry after epoch {retry_at_epoch}")
        self.retry_at_epoch = retry_at_epoch


class DynamoRepository:
    """Reads and conditionally writes the one table."""

    def __init__(self, client: Any, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def create_job_with_rate_increment(
        self, job: dict[str, Any], rate_pk: str, limit: int, now_epoch: int
    ) -> None:
        """Create the job and consume one unit of rate capacity atomically."""
        window_start = keys.rate_window(now_epoch)
        item = _without_none(job)
        item["pk"] = _job_pk(job["job_id"])
        item["ttl"] = now_epoch + TTL_UNCOMPLETED_SECONDS

        try:
            self._client.transact_write_items(
                TransactItems=[
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
            )
        except ClientError as error:
            if _failed_at(error, index=1):
                raise RateLimited(retry_at_epoch=window_start + RETRY_AFTER_SECONDS) from None
            raise

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key={"pk": _attribute(_job_pk(job_id))},
            ConsistentRead=True,
        )
        item = response.get("Item")
        return _without_partition_key(_plain_item(item)) if item else None

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
