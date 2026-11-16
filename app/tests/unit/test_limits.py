"""Unit tests for the pure duration and size evaluators (T061)."""

from __future__ import annotations

import dataclasses

import pytest

from ytdl import errors, limits

MAX_DURATION = 3600
MAX_BYTES = 4294967296


# --- duration -----------------------------------------------------------------


@pytest.mark.parametrize("seconds", [0, 1, 212, 3599, 3599.9, MAX_DURATION])
def test_duration_at_or_below_the_cap_is_allowed(seconds: float | int) -> None:
    decision = limits.check_duration(seconds, MAX_DURATION)

    assert decision.allowed is True
    assert decision.failure is None


@pytest.mark.parametrize("seconds", [3601, 3601.5, 4000])
def test_duration_above_the_cap_is_a_limit_exceeded(seconds: float | int) -> None:
    decision = limits.check_duration(seconds, MAX_DURATION)

    assert decision.allowed is False
    assert decision.failure is not None
    assert decision.failure.category == errors.LIMIT_EXCEEDED
    assert decision.failure.retryable is False
    assert decision.failure.message == errors.MESSAGES[errors.LIMIT_EXCEEDED]


@pytest.mark.parametrize("metadata", [None, "212"])
def test_missing_or_non_numeric_duration_is_unsupported(metadata: object) -> None:
    decision = limits.check_duration(metadata, MAX_DURATION)  # type: ignore[arg-type]

    assert decision.allowed is False
    assert decision.failure is not None
    assert decision.failure.category == errors.UNSUPPORTED_MEDIA
    assert decision.failure.retryable is False


def test_a_whole_float_duration_is_numeric_metadata() -> None:
    decision = limits.check_duration(212.0, MAX_DURATION)

    assert decision.allowed is True
    assert decision.failure is None


@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="infinity"),
        pytest.param(-1, id="negative"),
        pytest.param(-0.5, id="negative-float"),
        pytest.param(True, id="boolean"),
        pytest.param({"value": 212}, id="mapping"),
    ],
)
def test_non_finite_negative_or_non_numeric_duration_is_unsupported(metadata: object) -> None:
    decision = limits.check_duration(metadata, MAX_DURATION)  # type: ignore[arg-type]

    assert decision.allowed is False
    assert decision.failure is not None
    assert decision.failure.category == errors.UNSUPPORTED_MEDIA
    assert decision.failure.retryable is False


def test_duration_message_does_not_format_numbers() -> None:
    decision = limits.check_duration(7201, MAX_DURATION)

    assert decision.failure is not None
    assert "7201" not in decision.failure.message
    assert "3600" not in decision.failure.message


# --- size ---------------------------------------------------------------------


@pytest.mark.parametrize("size", [0, 1, MAX_BYTES - 1, MAX_BYTES])
def test_size_at_or_below_the_cap_is_allowed(size: int) -> None:
    decision = limits.check_size(size, MAX_BYTES)

    assert decision.allowed is True
    assert decision.failure is None


def test_one_byte_over_the_cap_is_a_limit_exceeded() -> None:
    decision = limits.check_size(MAX_BYTES + 1, MAX_BYTES)

    assert decision.allowed is False
    assert decision.failure is not None
    assert decision.failure.category == errors.LIMIT_EXCEEDED
    assert decision.failure.retryable is False
    assert decision.failure.message == errors.MESSAGES[errors.LIMIT_EXCEEDED]


def test_a_missing_size_is_a_retryable_internal_error() -> None:
    decision = limits.check_size(None, MAX_BYTES)

    assert decision.allowed is False
    assert decision.failure is not None
    assert decision.failure.category == errors.INTERNAL_ERROR
    assert decision.failure.retryable is True
    assert decision.failure.message == errors.MESSAGES[errors.INTERNAL_ERROR]


@pytest.mark.parametrize("size", ["42", 4.2, True, -1])
def test_a_malformed_size_is_a_retryable_internal_error(size: object) -> None:
    decision = limits.check_size(size, MAX_BYTES)  # type: ignore[arg-type]

    assert decision.allowed is False
    assert decision.failure is not None
    assert decision.failure.category == errors.INTERNAL_ERROR
    assert decision.failure.retryable is True


def test_size_message_does_not_format_numbers() -> None:
    decision = limits.check_size(MAX_BYTES + 1, MAX_BYTES)

    assert decision.failure is not None
    assert "4294967297" not in decision.failure.message
    assert "4294967296" not in decision.failure.message


# --- shape --------------------------------------------------------------------


def test_decisions_are_immutable_and_repeatable() -> None:
    first = limits.check_duration(3601, MAX_DURATION)
    second = limits.check_duration(3601, MAX_DURATION)

    assert first == second
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.allowed = True  # type: ignore[misc]
