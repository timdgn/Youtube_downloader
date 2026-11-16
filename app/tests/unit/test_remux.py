"""Unit tests for the ffmpeg remux and ffprobe helpers (T052)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ytdl.worker import remux

SOURCE = "/tmp/source.mp4"
DEST = "/tmp/output.mp4"

STREAMS_JSON = json.dumps(
    {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "height": 1080, "width": 1920},
            {"codec_type": "audio", "codec_name": "aac", "channels": 2},
        ]
    }
)


def _completed(
    command: list[str], *, returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=command, returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_build_ffmpeg_args_copies_streams_without_encoding() -> None:
    args = remux.build_ffmpeg_args(SOURCE, DEST)

    assert args[0] == "ffmpeg"
    assert args[-1] == DEST
    assert args[args.index("-i") + 1] == SOURCE
    assert args[args.index("-c") + 1] == "copy"
    assert args.count("-c") == 1
    for encoder_flag in ("-c:v", "-c:a", "-c:v:0", "-c:a:0", "-vn", "-an"):
        assert encoder_flag not in args
    assert "-hide_banner" in args
    assert "-nostdin" in args
    assert "-y" in args


def test_build_ffmpeg_args_puts_faststart_before_the_destination() -> None:
    args = remux.build_ffmpeg_args(SOURCE, DEST)

    assert args[args.index("-movflags") + 1] == "+faststart"
    assert args.index("-movflags") < args.index(DEST)
    assert args.index("-i") < args.index("-c") < args.index("-movflags")


def test_remux_to_mp4_runs_ffmpeg_without_a_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = str(tmp_path / "source.mp4")
    dest = tmp_path / "output.mp4"
    seen: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["command"] = command
        seen.update(kwargs)
        dest.write_bytes(b"mp4-bytes")
        return _completed(command)

    monkeypatch.setattr(remux.subprocess, "run", fake_run)

    remux.remux_to_mp4(source, str(dest), timeout_seconds=42)

    assert seen["command"] == remux.build_ffmpeg_args(source, str(dest))
    assert seen["shell"] is False
    assert seen["capture_output"] is True
    assert seen["text"] is True
    assert seen["timeout"] == 42


def test_remux_to_mp4_raises_with_stderr_on_non_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return _completed(command, returncode=1, stderr="Invalid data found when processing input")

    monkeypatch.setattr(remux.subprocess, "run", fake_run)

    with pytest.raises(remux.RemuxError) as excinfo:
        remux.remux_to_mp4(SOURCE, DEST)

    assert excinfo.value.stderr == "Invalid data found when processing input"
    assert "1" in str(excinfo.value)


def test_remux_to_mp4_raises_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(remux.subprocess, "run", fake_run)

    with pytest.raises(remux.RemuxError):
        remux.remux_to_mp4(SOURCE, DEST)


def test_remux_to_mp4_raises_when_the_output_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return _completed(command)

    monkeypatch.setattr(remux.subprocess, "run", fake_run)

    with pytest.raises(remux.RemuxError):
        remux.remux_to_mp4(SOURCE, str(tmp_path / "output.mp4"))


def test_remux_to_mp4_raises_when_the_output_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "output.mp4"

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        dest.write_bytes(b"")
        return _completed(command)

    monkeypatch.setattr(remux.subprocess, "run", fake_run)

    with pytest.raises(remux.RemuxError):
        remux.remux_to_mp4(SOURCE, str(dest))


def test_probe_output_parses_the_streams_json(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["command"] = command
        seen.update(kwargs)
        return _completed(command, stdout=STREAMS_JSON)

    monkeypatch.setattr(remux.subprocess, "run", fake_run)

    result = remux.probe_output(DEST)

    assert result == {"height": 1080, "vcodec": "h264", "acodec": "aac"}
    assert seen["command"] == [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        DEST,
    ]
    assert seen["shell"] is False
    assert seen["timeout"] == 60


def test_probe_output_reports_missing_streams_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"streams": [{"codec_type": "audio", "codec_name": "aac"}]})

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return _completed(command, stdout=payload)

    monkeypatch.setattr(remux.subprocess, "run", fake_run)

    assert remux.probe_output(DEST) == {"height": None, "vcodec": None, "acodec": "aac"}


def test_probe_output_raises_with_stderr_on_non_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return _completed(command, returncode=2, stderr="No such file or directory")

    monkeypatch.setattr(remux.subprocess, "run", fake_run)

    with pytest.raises(remux.RemuxError) as excinfo:
        remux.probe_output(DEST)

    assert excinfo.value.stderr == "No such file or directory"


def test_probe_output_raises_on_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return _completed(command, stdout="height: 1080")

    monkeypatch.setattr(remux.subprocess, "run", fake_run)

    with pytest.raises(remux.RemuxError):
        remux.probe_output(DEST)
