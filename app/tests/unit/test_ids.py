"""Unit tests for the job identifier generator (T012)."""

from __future__ import annotations

import re
import string

import pytest

from ytdl import ids

ALPHABET = set(string.ascii_lowercase + string.digits)
JOB_ID = re.compile(r"^[a-z0-9]{14}$")


def test_job_id_shape() -> None:
    job_id = ids.new_job_id()

    assert JOB_ID.match(job_id)
    assert len(job_id) == 14
    assert set(job_id) <= ALPHABET


def test_two_ids_in_the_same_millisecond_differ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ids, "_now_ms", lambda: 1_700_000_000_000)
    monkeypatch.setattr(ids, "_last_seq", 0)

    assert ids.new_job_id() != ids.new_job_id()


def test_ids_sort_in_generation_order_with_a_frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ids, "_now_ms", lambda: 1_700_000_000_000)
    monkeypatch.setattr(ids, "_last_seq", 0)

    generated = [ids.new_job_id() for _ in range(500)]

    assert len(set(generated)) == 500
    assert generated == sorted(generated)
    assert all(JOB_ID.match(job_id) for job_id in generated)


def test_ids_sort_in_generation_order_with_the_real_clock() -> None:
    generated = [ids.new_job_id() for _ in range(2000)]

    assert len(set(generated)) == 2000
    assert generated == sorted(generated)


def test_ids_generated_in_later_milliseconds_sort_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ids, "_last_seq", 0)
    monkeypatch.setattr(ids, "_now_ms", lambda: 1_700_000_000_000)
    first = ids.new_job_id()

    monkeypatch.setattr(ids, "_now_ms", lambda: 1_700_000_000_001)
    second = ids.new_job_id()

    assert first < second
