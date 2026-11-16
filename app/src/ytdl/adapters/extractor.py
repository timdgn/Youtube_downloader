"""yt-dlp subprocess adapter (T026)."""

from __future__ import annotations

import glob
import json
import os
import subprocess
from collections.abc import Sequence
from typing import Any

_OUTPUT_TEMPLATE = "source.%(ext)s"


class ExtractionError(Exception):
    """yt-dlp failed, timed out, or produced no file."""

    def __init__(
        self,
        stderr: str = "",
        returncode: int | None = None,
        message: str | None = None,
    ) -> None:
        if message is None:
            if returncode is None:
                message = "yt-dlp timed out"
            else:
                message = f"yt-dlp exited with code {returncode}"
        super().__init__(message)
        self.stderr = stderr
        self.returncode = returncode


def hook_args(
    cookies_path: str | None = None,
    proxy: str | None = None,
    player_client: str | None = None,
) -> list[str]:
    """The optional D-14 flags, in a stable order, for the values that are supplied."""
    args: list[str] = []
    if cookies_path:
        args += ["--cookies", cookies_path]
    if proxy:
        args += ["--proxy", proxy]
    if player_client:
        args += ["--extractor-args", f"youtube:player_client={player_client}"]
    return args


class Extractor:
    """Runs yt-dlp as a child process, never through a shell."""

    def __init__(
        self,
        timeout_seconds: int = 780,
        binary: str = "yt-dlp",
        cookies_path: str | None = None,
        proxy: str | None = None,
        player_client: str | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._binary = binary
        self._hooks = hook_args(
            cookies_path=cookies_path,
            proxy=proxy,
            player_client=player_client,
        )

    def probe(self, url: str, extra_args: Sequence[str] = ()) -> dict[str, Any]:
        """Return the metadata JSON for ``url`` without downloading media."""
        command = [
            self._binary,
            "--dump-single-json",
            "--no-playlist",
            "--no-warnings",
            *self._hooks,
            *extra_args,
            url,
        ]
        completed = self._run(command)
        return json.loads(completed.stdout)

    def download(
        self,
        url: str,
        format_selector: str,
        out_dir: str,
        extra_args: Sequence[str] = (),
    ) -> str:
        """Download and merge into ``out_dir``, returning the produced file path."""
        command = [
            self._binary,
            "--no-playlist",
            "-f",
            format_selector,
            "-o",
            os.path.join(out_dir, _OUTPUT_TEMPLATE),
            "--merge-output-format",
            "mp4",
            *self._hooks,
            *extra_args,
            url,
        ]
        self._run(command)
        return _produced_file(out_dir)

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                command,
                timeout=self._timeout_seconds,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
                env=_child_environment(),
            )
        except subprocess.TimeoutExpired:
            raise ExtractionError(returncode=None, stderr="timeout") from None

        if completed.returncode != 0:
            raise ExtractionError(returncode=completed.returncode, stderr=completed.stderr)
        return completed


def _child_environment() -> dict[str, str]:
    return {**os.environ, "HOME": "/tmp", "TMPDIR": "/tmp"}


def _produced_file(out_dir: str) -> str:
    expected = os.path.join(out_dir, "source.mp4")
    if os.path.exists(expected):
        return expected
    candidates = [
        path for path in glob.glob(os.path.join(out_dir, "source.*")) if not path.endswith(".part")
    ]
    if len(candidates) == 1:
        return candidates[0]
    raise ExtractionError(message="yt-dlp produced no output file")
