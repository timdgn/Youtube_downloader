"""Unit tests for format selection from probe metadata (T076, T079)."""

from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from ytdl.formats import NoUsableFormat, Selection, select

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

FIXTURE_NAMES = (
    "formats_1080p.json",
    "formats_720p_max.json",
    "formats_no_h264.json",
    "formats_4k.json",
)


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_fixtures_are_the_four_documented_cases() -> None:
    for name in FIXTURE_NAMES:
        metadata = load_fixture(name)
        assert metadata["title"]
        assert isinstance(metadata["duration"], int)
        assert metadata["formats"]


@pytest.mark.parametrize(
    ("fixture", "ceiling", "video_id", "audio_id", "height", "vcodec", "acodec"),
    [
        # 1080p H.264 available.
        pytest.param(
            "formats_1080p.json", 360, "134", "140", 360, "avc1.4d401e", "mp4a.40.2", id="1080p-360"
        ),
        pytest.param(
            "formats_1080p.json", 480, "135", "140", 480, "avc1.4d401f", "mp4a.40.2", id="1080p-480"
        ),
        pytest.param(
            "formats_1080p.json", 720, "136", "140", 720, "avc1.64001F", "mp4a.40.2", id="1080p-720"
        ),
        pytest.param(
            "formats_1080p.json",
            1080,
            "137",
            "140",
            1080,
            "avc1.640028",
            "mp4a.40.2",
            id="1080p-1080",
        ),
        # 720p maximum: a 1080 request falls back to the highest rendition.
        pytest.param(
            "formats_720p_max.json",
            360,
            "134",
            "140",
            360,
            "avc1.4d401e",
            "mp4a.40.2",
            id="720max-360",
        ),
        pytest.param(
            "formats_720p_max.json",
            480,
            "135",
            "140",
            480,
            "avc1.4d401f",
            "mp4a.40.2",
            id="720max-480",
        ),
        pytest.param(
            "formats_720p_max.json",
            720,
            "136",
            "140",
            720,
            "avc1.64001F",
            "mp4a.40.2",
            id="720max-720",
        ),
        pytest.param(
            "formats_720p_max.json",
            1080,
            "136",
            "140",
            720,
            "avc1.64001F",
            "mp4a.40.2",
            id="720max-1080",
        ),
        # H.264 absent: best video by height, still AAC audio.
        pytest.param(
            "formats_no_h264.json", 360, "243", "140", 360, "vp9", "mp4a.40.2", id="noh264-360"
        ),
        pytest.param(
            "formats_no_h264.json", 480, "244", "140", 480, "vp9", "mp4a.40.2", id="noh264-480"
        ),
        pytest.param(
            "formats_no_h264.json",
            720,
            "247",
            "140",
            720,
            "vp9",
            "mp4a.40.2",
            id="noh264-720",
        ),
        pytest.param(
            "formats_no_h264.json",
            1080,
            "248",
            "140",
            1080,
            "vp9",
            "mp4a.40.2",
            id="noh264-1080",
        ),
        # 4K source: the ceiling picks H.264 at 1080, never 1440 or 2160.
        pytest.param(
            "formats_4k.json", 360, "134", "140", 360, "avc1.4d401e", "mp4a.40.2", id="4k-360"
        ),
        pytest.param(
            "formats_4k.json", 480, "135", "140", 480, "avc1.4d401f", "mp4a.40.2", id="4k-480"
        ),
        pytest.param(
            "formats_4k.json", 720, "136", "140", 720, "avc1.64001F", "mp4a.40.2", id="4k-720"
        ),
        pytest.param(
            "formats_4k.json", 1080, "137", "140", 1080, "avc1.640028", "mp4a.40.2", id="4k-1080"
        ),
    ],
)
def test_selection_pair_and_height(
    fixture: str,
    ceiling: int,
    video_id: str,
    audio_id: str,
    height: int,
    vcodec: str,
    acodec: str,
) -> None:
    selection = select(load_fixture(fixture), ceiling)

    assert selection == Selection(
        format_selector=f"{video_id}+{audio_id}",
        expected_height=height,
        vcodec=vcodec,
        acodec=acodec,
    )


@pytest.mark.parametrize("fixture", FIXTURE_NAMES)
@pytest.mark.parametrize("ceiling", [144, 240, 360, 480, 720, 1080])
def test_never_selects_above_the_ceiling(fixture: str, ceiling: int) -> None:
    metadata = load_fixture(fixture)
    try:
        selection = select(metadata, ceiling)
    except NoUsableFormat:
        return
    assert selection.expected_height is not None
    assert selection.expected_height <= ceiling


def test_4k_source_is_capped_to_1080_not_2160() -> None:
    selection = select(load_fixture("formats_4k.json"), 1080)
    assert selection.expected_height == 1080
    assert selection.format_selector == "137+140"


def test_no_audio_raises() -> None:
    metadata = load_fixture("formats_1080p.json")
    metadata["formats"] = [
        fmt for fmt in metadata["formats"] if str(fmt.get("acodec", "none")) == "none"
    ]
    with pytest.raises(NoUsableFormat):
        select(metadata, 1080)


def test_ceiling_below_every_rendition_raises() -> None:
    with pytest.raises(NoUsableFormat):
        select(load_fixture("formats_1080p.json"), 144)


def test_empty_format_list_raises() -> None:
    with pytest.raises(NoUsableFormat):
        select({"title": "empty", "duration": 10, "formats": []}, 1080)


def test_falls_back_to_best_audio_when_no_aac_exists() -> None:
    metadata = {
        "title": "opus only",
        "duration": 10,
        "formats": [
            {
                "format_id": "136",
                "ext": "mp4",
                "height": 720,
                "width": 1280,
                "vcodec": "avc1.64001F",
                "acodec": "none",
                "tbr": 2200.0,
            },
            {
                "format_id": "251",
                "ext": "webm",
                "height": None,
                "width": None,
                "vcodec": "none",
                "acodec": "opus",
                "tbr": 161.0,
                "abr": 160.0,
            },
        ],
    }
    selection = select(metadata, 720)
    assert selection.format_selector == "136+251"
    assert selection.acodec == "opus"


def test_expression_is_used_when_ids_are_missing() -> None:
    metadata = {
        "title": "no ids",
        "duration": 10,
        "formats": [
            {
                "ext": "mp4",
                "height": 720,
                "width": 1280,
                "vcodec": "avc1.64001F",
                "acodec": "none",
                "tbr": 2200.0,
            },
            {
                "ext": "m4a",
                "height": None,
                "width": None,
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "tbr": 129.0,
                "abr": 128.0,
            },
        ],
    }
    selection = select(metadata, 720)
    assert selection.format_selector == (
        "bv*[height<=720][vcodec^=avc1]+ba[acodec^=mp4a]/b[height<=720]"
    )
    assert selection.expected_height == 720
    assert selection.vcodec == "avc1.64001F"
    assert selection.acodec == "mp4a.40.2"


def test_expression_is_used_when_only_the_video_has_an_id() -> None:
    metadata = load_fixture("formats_1080p.json")
    for fmt in metadata["formats"]:
        if str(fmt.get("acodec", "none")) != "none":
            fmt.pop("format_id", None)
    selection = select(metadata, 1080)
    assert selection.format_selector == (
        "bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/b[height<=1080]"
    )


def test_selection_is_frozen() -> None:
    selection = select(load_fixture("formats_1080p.json"), 1080)
    with pytest.raises(FrozenInstanceError):
        selection.expected_height = 2160  # type: ignore[misc]


def test_select_does_not_mutate_metadata() -> None:
    metadata = load_fixture("formats_4k.json")
    before = copy.deepcopy(metadata)
    select(metadata, 1080)
    assert metadata == before
