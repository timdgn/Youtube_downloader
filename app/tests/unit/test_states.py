"""Unit tests for the job state machine (T013)."""

from __future__ import annotations

import pytest

from ytdl import states
from ytdl.states import ACCEPTED, COMPLETED, FAILED, PROCESSING

CLAIM_CONDITION = (
    "#state = :accepted OR (#state = :processing AND #updated_at < :stale) OR "
    "(#state = :failed AND #failure_retryable = :true AND "
    "if_not_exists(#attempt, :zero) < :max)"
)


def test_state_constants() -> None:
    assert (ACCEPTED, PROCESSING, COMPLETED, FAILED) == (
        "accepted",
        "processing",
        "completed",
        "failed",
    )


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ACCEPTED, PROCESSING),
        (PROCESSING, COMPLETED),
        (PROCESSING, FAILED),
    ],
)
def test_legal_transitions(current: str, target: str) -> None:
    assert states.can_transition(current, target) is True


def test_stale_claim_is_recovered() -> None:
    assert (
        states.can_transition(
            PROCESSING,
            PROCESSING,
            updated_at_epoch=1_000,
            now_epoch=1_901,
        )
        is True
    )


def test_stale_claim_first_fires_after_nine_hundred_seconds() -> None:
    exactly = states.can_transition(PROCESSING, PROCESSING, updated_at_epoch=1_000, now_epoch=1_900)
    just_over = states.can_transition(
        PROCESSING, PROCESSING, updated_at_epoch=1_000, now_epoch=1_901
    )

    assert exactly is False
    assert just_over is True


@pytest.mark.parametrize("now_epoch", [None, 5_000])
def test_processing_to_processing_needs_both_timestamps(now_epoch: int | None) -> None:
    assert (
        states.can_transition(PROCESSING, PROCESSING, updated_at_epoch=None, now_epoch=now_epoch)
        is False
    )


@pytest.mark.parametrize("target", [ACCEPTED, PROCESSING, COMPLETED, FAILED])
def test_completed_is_terminal(target: str) -> None:
    assert states.can_transition(COMPLETED, target) is False


@pytest.mark.parametrize("target", [ACCEPTED, FAILED, COMPLETED])
def test_accepted_only_moves_forward_to_processing(target: str) -> None:
    assert states.can_transition(ACCEPTED, target) is False


@pytest.mark.parametrize("target", [ACCEPTED, COMPLETED, FAILED])
def test_failed_is_terminal_except_for_a_retry_claim(target: str) -> None:
    assert states.can_transition(FAILED, target, retryable=True, attempt=1) is False


def test_failed_retries_only_when_retryable_and_below_two_attempts() -> None:
    assert states.can_transition(FAILED, PROCESSING, retryable=True, attempt=1) is True
    assert states.can_transition(FAILED, PROCESSING, retryable=True, attempt=2) is False
    assert states.can_transition(FAILED, PROCESSING, retryable=True, attempt=3) is False
    assert states.can_transition(FAILED, PROCESSING, retryable=False, attempt=1) is False
    assert states.can_transition(FAILED, PROCESSING) is False


@pytest.mark.parametrize(
    ("current", "target"),
    [("pending", PROCESSING), (ACCEPTED, "done"), ("", "")],
)
def test_unknown_states_are_refused(current: str, target: str) -> None:
    assert states.can_transition(current, target) is False


def test_transition_condition_for_the_claim() -> None:
    assert states.transition_condition(PROCESSING) == CLAIM_CONDITION


@pytest.mark.parametrize("target", [COMPLETED, FAILED])
def test_transition_condition_for_the_worker_outcome(target: str) -> None:
    assert states.transition_condition(target) == "#state = :processing"


def test_transition_condition_refuses_unknown_targets() -> None:
    with pytest.raises(ValueError):
        states.transition_condition("accepted")
