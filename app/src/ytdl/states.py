"""The job state machine, as a pure transition table (T019)."""

from __future__ import annotations

ACCEPTED = "accepted"
PROCESSING = "processing"
COMPLETED = "completed"
FAILED = "failed"

STALE_CLAIM_SECONDS = 900
MAX_ATTEMPTS = 2

_CLAIM_CONDITION = (
    "#state = :accepted OR (#state = :processing AND #updated_at < :stale) OR "
    "(#state = :failed AND #failure_retryable = :true AND "
    "if_not_exists(#attempt, :zero) < :max)"
)
_WORKER_OUTCOME_CONDITION = "#state = :processing"

_CONDITIONS = {
    PROCESSING: _CLAIM_CONDITION,
    COMPLETED: _WORKER_OUTCOME_CONDITION,
    FAILED: _WORKER_OUTCOME_CONDITION,
}


def can_transition(
    current: str,
    target: str,
    *,
    retryable: bool = False,
    attempt: int = 1,
    updated_at_epoch: int | None = None,
    now_epoch: int | None = None,
) -> bool:
    """Apply the data-model transition table, guards included."""
    if target == PROCESSING:
        if current == ACCEPTED:
            return True
        if current == PROCESSING:
            return _is_stale(updated_at_epoch, now_epoch)
        if current == FAILED:
            return retryable and attempt < MAX_ATTEMPTS
        return False

    return current == PROCESSING and target in (COMPLETED, FAILED)


def transition_condition(target: str) -> str:
    """Return the DynamoDB condition expression guarding a write to ``target``."""
    try:
        return _CONDITIONS[target]
    except KeyError:
        raise ValueError(f"no transition condition for target {target!r}") from None


def _is_stale(updated_at_epoch: int | None, now_epoch: int | None) -> bool:
    if updated_at_epoch is None or now_epoch is None:
        return False
    return now_epoch - updated_at_epoch > STALE_CLAIM_SECONDS
