"""S3 access for media objects and operator configuration (T024)."""

from __future__ import annotations

import os
from typing import Any

from botocore.exceptions import ClientError

_MISSING_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


class MediaStore:
    """Uploads media, verifies its presence, and signs download links."""

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def put_file(self, path: str, key: str, content_type: str, metadata: dict[str, str]) -> int:
        """Upload ``path`` to ``key`` and return the uploaded byte count."""
        self._client.upload_file(
            Filename=path,
            Bucket=self._bucket,
            Key=key,
            ExtraArgs={"ContentType": content_type, "Metadata": metadata},
        )
        return os.path.getsize(path)

    def head_object(self, key: str) -> dict[str, Any] | None:
        """Return the object size, ETag and user metadata, or None when it is gone."""
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in _MISSING_CODES:
                return None
            raise

        metadata = response.get("Metadata")
        return {
            "size": int(response["ContentLength"]),
            "etag": str(response.get("ETag", "")).strip('"'),
            "metadata": dict(metadata) if isinstance(metadata, dict) else {},
        }

    def presign_get(self, key: str, filename: str, expires_in: int) -> str:
        """Sign a GetObject URL that downloads as ``filename`` for ``expires_in``."""
        return self._client.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
                "ResponseContentType": "video/mp4",
            },
            ExpiresIn=expires_in,
        )

    def get_text(self, key: str) -> str:
        """Read a small UTF-8 object, used only for config/cookies.txt."""
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        return response["Body"].read().decode("utf-8")
