"""Unit tests for the optional D-14 extractor hooks (T070)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ytdl.adapters.extractor import Extractor, hook_args

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
COOKIES = "/tmp/cookies.txt"
PROXY = "http://10.0.0.1:8080"
CLIENT = "web_safari"


def test_no_hooks_produce_no_flags() -> None:
    assert hook_args() == []
    assert hook_args(cookies_path=None, proxy=None, player_client=None) == []


def test_one_hook_produces_only_its_flag() -> None:
    assert hook_args(cookies_path=COOKIES) == ["--cookies", COOKIES]
    assert hook_args(proxy=PROXY) == ["--proxy", PROXY]
    assert hook_args(player_client=CLIENT) == [
        "--extractor-args",
        f"youtube:player_client={CLIENT}",
    ]


def test_empty_string_values_add_no_flags() -> None:
    assert hook_args(cookies_path="", proxy="", player_client="") == []


def test_all_three_hooks_produce_the_flags_in_order() -> None:
    assert hook_args(cookies_path=COOKIES, proxy=PROXY, player_client=CLIENT) == [
        "--cookies",
        COOKIES,
        "--proxy",
        PROXY,
        "--extractor-args",
        f"youtube:player_client={CLIENT}",
    ]


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def test_probe_passes_the_cookies_flag_before_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = MagicMock(return_value=_completed(json.dumps({"id": "dQw4w9WgXcQ"})))
    monkeypatch.setattr("ytdl.adapters.extractor.subprocess.run", runner)

    Extractor(cookies_path=COOKIES).probe(URL)

    command = runner.call_args.args[0]
    assert command[command.index("--cookies") + 1] == COOKIES
    assert command.index("--cookies") < command.index(URL)
    assert command[-1] == URL


def test_download_command_carries_the_hooks_before_the_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(list(command))
        output = command[command.index("-o") + 1].replace("%(ext)s", "mp4")
        Path(output).write_bytes(b"x")
        return _completed()

    monkeypatch.setattr("ytdl.adapters.extractor.subprocess.run", fake_run)

    Extractor(cookies_path=COOKIES, proxy=PROXY, player_client=CLIENT).download(
        URL, "bv*+ba", str(tmp_path)
    )

    (command,) = commands
    assert command[command.index("--cookies") + 1] == COOKIES
    assert command[command.index("--proxy") + 1] == PROXY
    assert command[command.index("--extractor-args") + 1] == f"youtube:player_client={CLIENT}"
    assert command.index("--cookies") < command.index(URL)
    assert command.index("--proxy") < command.index(URL)
    assert command.index("--extractor-args") < command.index(URL)
    assert command[-1] == URL


def test_the_default_extractor_adds_no_hook_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = MagicMock(return_value=_completed(json.dumps({"id": "dQw4w9WgXcQ"})))
    monkeypatch.setattr("ytdl.adapters.extractor.subprocess.run", runner)

    Extractor().probe(URL)

    command = runner.call_args.args[0]
    assert "--cookies" not in command
    assert "--proxy" not in command
    assert "--extractor-args" not in command
