"""SC-009: no credential, header or full URL can reach a log line (T095)."""

from __future__ import annotations

import json

import pytest

from ytdl.api.routes import MESSAGES as API_MESSAGES
from ytdl.errors import MESSAGES as FAILURE_MESSAGES
from ytdl.logging import ALLOWED_FIELDS, get_logger

_SECRET = "s3cr3t-bearer-value"
_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLsecret&si=tracking"


def _emit(capsys: pytest.CaptureFixture[str], **fields: object) -> str:
    get_logger("api").info("request", **fields)
    return capsys.readouterr().out


def test_disallowed_field_names_are_dropped(capsys: pytest.CaptureFixture[str]) -> None:
    output = _emit(
        capsys,
        job_id="m3x9k2q1f7",
        authorization=f"Bearer {_SECRET}",
        token=_SECRET,
        url=_URL,
        headers={"authorization": f"Bearer {_SECRET}"},
    )

    assert _SECRET not in output
    assert "authorization" not in output
    assert "headers" not in output
    assert "youtube.com" not in output
    assert "m3x9k2q1f7" in output


def test_one_json_object_per_line(capsys: pytest.CaptureFixture[str]) -> None:
    output = _emit(capsys, job_id="m3x9k2q1f7")

    lines = [line for line in output.splitlines() if line.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["job_id"] == "m3x9k2q1f7"
    assert set(parsed) <= (
        set(ALLOWED_FIELDS) | {"level", "event", "function", "environment", "timestamp"}
    )


def test_allowlist_covers_the_audit_fields_required_by_fr_015() -> None:
    required = {
        "job_id",
        "source_host",
        "video_id",
        "requested_quality",
        "produced_quality",
        "outcome",
        "processing_ms",
        "media_size_bytes",
        "attempt",
    }
    assert required <= set(ALLOWED_FIELDS)


@pytest.mark.parametrize(
    "message",
    [*API_MESSAGES.values(), *FAILURE_MESSAGES.values()],
)
def test_fixed_messages_carry_no_url_or_line_break(message: str) -> None:
    assert message.strip() == message
    assert "\n" not in message
    assert "\r" not in message
    assert "http" not in message.lower()
    assert "www." not in message
    assert "\u2014" not in message
