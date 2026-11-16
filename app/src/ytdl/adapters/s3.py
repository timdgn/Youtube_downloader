"""S3 access for media objects and operator configuration (T024)."""

from __future__ import annotations

import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

_MISSING_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


def s3_client(region: str | None = None) -> Any:
    """Build an S3 client whose presigned URLs target the regional endpoint.

    botocore signs a presigned URL for the client's region but emits the global
    ``s3.amazonaws.com`` host, and S3 rejects that mismatch with
    ``AuthorizationQueryParametersError``. Pinning the endpoint keeps the host and
    the signature in the same region. Verified: global host answers 307 or 400,
    the regional host answers 200.
    """
    resolved = region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not resolved:
        return boto3.client("s3")
    return boto3.client(
        "s3",
        region_name=resolved,
        endpoint_url=f"https://s3.{resolved}.amazonaws.com",
    )


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
