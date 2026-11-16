"""Fake yt-dlp extractor for contract tests."""

from __future__ import annotations

import os
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

DEFAULT_METADATA: dict[str, Any] = {
    "id": "dQw4w9WgXcQ",
    "title": "fixture video",
    "duration": 212,
    "extractor": "youtube",
    "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "formats": [
        {
            "format_id": "137",
            "height": 1080,
            "vcodec": "avc1.640028",
            "acodec": "none",
            "ext": "mp4",
        },
        {
            "format_id": "136",
            "height": 720,
            "vcodec": "avc1.4d401f",
            "acodec": "none",
            "ext": "mp4",
        },
        {
            "format_id": "140",
            "height": None,
            "vcodec": "none",
            "acodec": "mp4a.40.2",
            "ext": "m4a",
        },
    ],
}

DEFAULT_CONTENT = b"fake-mp4-bytes"


class FakeExtractor:
    """Same method set as ``Extractor``, with no subprocess and no network."""

    def __init__(
        self,
        metadata: dict[str, Any] | None = None,
        content: bytes = DEFAULT_CONTENT,
        probe_error: Exception | None = None,
        download_error: Exception | None = None,
    ) -> None:
        self.metadata = deepcopy(DEFAULT_METADATA if metadata is None else metadata)
        self.content = content
        self.probe_error = probe_error
        self.download_error = download_error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def probe(self, url: str, extra_args: Sequence[str] = ()) -> dict[str, Any]:
        self.calls.append(("probe", {"url": url, "extra_args": list(extra_args)}))
        if self.probe_error is not None:
            raise self.probe_error
        return deepcopy(self.metadata)

    def download(
        self,
        url: str,
        format_selector: str,
        out_dir: str,
        extra_args: Sequence[str] = (),
    ) -> str:
        self.calls.append(
            (
                "download",
                {
                    "url": url,
                    "format_selector": format_selector,
                    "out_dir": out_dir,
                    "extra_args": list(extra_args),
                },
            )
        )
        if self.download_error is not None:
            raise self.download_error
        os.makedirs(out_dir, exist_ok=True)
        produced = Path(out_dir) / "source.mp4"
        produced.write_bytes(self.content)
        return str(produced)
