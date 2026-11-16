"""Unit tests for title sanitisation (data-model.md validation rules)."""

from __future__ import annotations

import pytest

from ytdl.titles import sanitise_title

MAX_LENGTH = 80


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Some video title", "Some video title"),
        ("  padded  ", "padded"),
        ("tabs\tand\tspaces", "tabs and spaces"),
        ("many   internal    spaces", "many internal spaces"),
        ("line\nbreak", "line break"),
        ("carriage\rreturn", "carriage return"),
        ("null\x00byte", "nullbyte"),
        ("bell\x07", "bell"),
        ("delete\x7fcharacter", "deletecharacter"),
        ("unit\u2028separator", "unit separator"),
    ],
)
def test_sanitise_title_strips_control_characters_and_whitespace(raw: str, expected: str) -> None:
    assert sanitise_title(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "\t\n\r", "\x00\x1f"])
def test_empty_or_whitespace_only_titles_become_untitled(raw: str) -> None:
    assert sanitise_title(raw) == "untitled"


def test_none_becomes_untitled() -> None:
    assert sanitise_title(None) == "untitled"


def test_long_titles_are_truncated_to_eighty_characters() -> None:
    raw = "a" * 200

    result = sanitise_title(raw)

    assert len(result) == MAX_LENGTH
    assert result == "a" * MAX_LENGTH


def test_truncation_happens_after_whitespace_collapsing() -> None:
    raw = "a " * 100

    result = sanitise_title(raw)

    assert len(result) <= MAX_LENGTH
    assert result == result.strip()
    assert "  " not in result


def test_a_multi_byte_character_is_never_cut_in_half() -> None:
    raw = "é" * 200

    result = sanitise_title(raw)

    assert len(result) == MAX_LENGTH
    assert result == "é" * MAX_LENGTH


def test_the_result_never_contains_a_newline() -> None:
    for raw in ("a\nb", "a\r\nb", "a" * 79 + "\nb", "\n"):
        assert "\n" not in sanitise_title(raw)
        assert "\r" not in sanitise_title(raw)
