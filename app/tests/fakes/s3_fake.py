"""In-memory media store for contract tests."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import quote


class FakeMediaStore:
    """Same method set as ``MediaStore``, backed by a dict."""

    def __init__(self, bucket: str = "ytdl-test-media") -> None:
        self.bucket = bucket
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.objects: dict[str, dict[str, Any]] = {}

    @property
    def writes(self) -> list[tuple[str, dict[str, Any]]]:
        """Only the mutating calls, so read-only paths can assert emptiness."""
        return [call for call in self.calls if call[0] == "put_file"]

    def put_file(self, path: str, key: str, content_type: str, metadata: dict[str, str]) -> int:
        body = Path(path).read_bytes()
        self.calls.append(
            (
                "put_file",
                {
                    "path": path,
                    "key": key,
                    "content_type": content_type,
                    "metadata": deepcopy(metadata),
                    "size": len(body),
                },
            )
        )
        self.objects[key] = {
            "body": body,
            "content_type": content_type,
            "metadata": deepcopy(metadata),
        }
        return len(body)

    def head_object(self, key: str) -> dict[str, Any] | None:
        self.calls.append(("head_object", {"key": key}))
        stored = self.objects.get(key)
        if stored is None:
            return None
        body = stored["body"]
        return {
            "size": len(body),
            "etag": hashlib.md5(body).hexdigest(),
            "metadata": dict(stored.get("metadata") or {}),
        }

    def presign_get(
        self,
        key: str,
        filename: str,
        expires_in: int,
        content_type: str = "video/mp4",
    ) -> str:
        self.calls.append(
            (
                "presign_get",
                {
                    "key": key,
                    "filename": filename,
                    "expires_in": expires_in,
                    "content_type": content_type,
                },
            )
        )
        disposition = quote(f'attachment; filename="{filename}"')
        return (
            f"https://{self.bucket}.s3.eu-west-1.amazonaws.com/{key}"
            f"?X-Amz-Expires={expires_in}"
            f"&response-content-disposition={disposition}"
            f"&response-content-type={quote(content_type)}"
        )

    def delete_object(self, key: str) -> None:
        self.calls.append(("delete_object", {"key": key}))
        self.objects.pop(key, None)

    def get_text(self, key: str) -> str:
        self.calls.append(("get_text", {"key": key}))
        stored = self.objects.get(key)
        if stored is None:
            raise KeyError(key)
        return stored["body"].decode("utf-8")

    def seed(
        self,
        key: str,
        body: bytes,
        content_type: str = "video/mp4",
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Place an object without recording a call, for test setup only."""
        self.objects[key] = {
            "body": body,
            "content_type": content_type,
            "metadata": dict(metadata or {}),
        }
