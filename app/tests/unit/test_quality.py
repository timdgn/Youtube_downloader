"""Unit tests for quality resolution and labelling (T074, T078)."""

from __future__ import annotations

import pytest

from ytdl.quality import (
    QUALITIES,
    InvalidQuality,
    cap_requested,
    height_for,
    label_for,
    resolve,
)


def test_qualities_are_the_accepted_labels() -> None:
    assert QUALITIES == ("360p", "480p", "720p", "1080p", "1440p", "2160p")


@pytest.mark.parametrize(
    ("label", "height"),
    [
        pytest.param("360p", 360, id="360p"),
        pytest.param("480p", 480, id="480p"),
        pytest.param("720p", 720, id="720p"),
        pytest.param("1080p", 1080, id="1080p"),
        pytest.param("1440p", 1440, id="1440p"),
        pytest.param("2160p", 2160, id="2160p"),
    ],
)
def test_height_for_standard_labels(label: str, height: int) -> None:
    assert height_for(label) == height


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("9000p", id="unknown-height"),
        pytest.param("best", id="keyword"),
        pytest.param("", id="empty"),
        pytest.param("720", id="missing-suffix"),
        pytest.param("720P", id="wrong-case"),
        pytest.param("p720", id="reversed"),
    ],
)
def test_height_for_rejects_unknown_labels(value: str) -> None:
    with pytest.raises(InvalidQuality) as caught:
        height_for(value)
    assert isinstance(caught.value, ValueError)
    assert caught.value.value == value


def test_resolve_absent_returns_the_ceiling() -> None:
    assert resolve(None, 1080) == 1080
    assert resolve(None, 720) == 720


@pytest.mark.parametrize(
    ("requested", "ceiling", "expected"),
    [
        pytest.param("360p", 1080, 360, id="360-under-1080"),
        pytest.param("480p", 1080, 480, id="480-under-1080"),
        pytest.param("720p", 1080, 720, id="720-under-1080"),
        pytest.param("1080p", 1080, 1080, id="1080-at-1080"),
        pytest.param("1080p", 720, 720, id="1080-capped-to-720"),
        pytest.param("720p", 480, 480, id="720-capped-to-480"),
    ],
)
def test_resolve_caps_at_the_ceiling(requested: str, ceiling: int, expected: int) -> None:
    assert resolve(requested, ceiling) == expected


def test_resolve_rejects_an_unknown_label() -> None:
    with pytest.raises(InvalidQuality):
        resolve("9000p", 1080)


def test_cap_requested_reports_the_capped_label() -> None:
    assert cap_requested("1080p", 720) == ("720p", 720)
    assert cap_requested("1440p", 1080) == ("1080p", 1080)
    assert cap_requested("2160p", 1080) == ("1080p", 1080)
    assert cap_requested(None, 1080) == (None, 1080)


@pytest.mark.parametrize(
    ("requested", "ceiling", "expected"),
    [
        pytest.param("720p", 1080, ("720p", 720), id="unchanged"),
        pytest.param("480p", 1080, ("480p", 480), id="unchanged-480"),
        pytest.param("360p", 720, ("360p", 360), id="unchanged-360"),
        pytest.param("1080p", 900, ("900p", 900), id="non-standard-ceiling"),
    ],
)
def test_cap_requested_keeps_or_reports_the_label(
    requested: str, ceiling: int, expected: tuple[str | None, int]
) -> None:
    assert cap_requested(requested, ceiling) == expected


def test_cap_requested_rejects_an_unknown_label() -> None:
    with pytest.raises(InvalidQuality):
        cap_requested("9000p", 1080)


@pytest.mark.parametrize(
    ("height", "label"),
    [
        pytest.param(None, None, id="none"),
        pytest.param(360, "360p", id="360"),
        pytest.param(480, "480p", id="480"),
        pytest.param(720, "720p", id="720"),
        pytest.param(1080, "1080p", id="1080"),
        pytest.param(1079, "1079p", id="just-below-standard"),
        pytest.param(240, "240p", id="below-the-lowest-standard"),
        pytest.param(1440, "1440p", id="above-the-highest-standard"),
    ],
)
def test_label_for_is_derived_from_the_height(height: int | None, label: str | None) -> None:
    assert label_for(height) == label


def test_label_for_never_echoes_a_request() -> None:
    assert label_for(1079) != "1080p"
    assert label_for(1080) == "1080p"
