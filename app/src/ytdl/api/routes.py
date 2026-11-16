"""Pure request and response shapes for the HTTP API (T050).

No I/O happens here: the handler supplies the data and these functions only
assemble the JSON bodies documented in ``contracts/api.md``.
"""

from __future__ import annotations

import json
from typing import Any

CONTENT_TYPE = "application/json; charset=utf-8"
QUALITIES = ("360p", "480p", "720p", "1080p")

MESSAGES = {
    "invalid_input": "The submitted link is not a supported single YouTube video.",
    "unauthorized": "The request is missing a valid access token.",
    "rate_limited": "Submission limit reached for this hour; retry after {retry_after}.",
    "not_found": "No job exists with that identifier.",
    "not_completed": "The job has not finished, so no download link is available yet.",
    "result_expired": "The stored file has expired; submit the video link again.",
    "internal_error": "The request could not be completed; try again later.",
}


def accepted_body(job: dict[str, Any]) -> dict[str, Any]:
    """Body of the ``202`` acceptance response."""
    return {
        "job_id": job["job_id"],
        "state": job["state"],
        "created_at": job["created_at"],
        "status_url": f"/v1/jobs/{job['job_id']}",
    }


def job_body(
    job: dict[str, Any],
    result: dict[str, Any] | None,
    failure: dict[str, Any] | None,
) -> dict[str, Any]:
    """Body of ``GET /v1/jobs/{job_id}`` in any state."""
    return {
        "job_id": job["job_id"],
        "state": job["state"],
        "created_at": job["created_at"],
        "updated_at": job.get("updated_at"),
        "requested_quality": job.get("requested_quality"),
        "produced_quality": job.get("produced_quality"),
        "result": result,
        "failure": failure,
    }


def link_body(job: dict[str, Any], url: str, expires_at: str, size: int) -> dict[str, Any]:
    """Body of a freshly issued download link."""
    return {
        "job_id": job["job_id"],
        "download_url": url,
        "expires_at": expires_at,
        "size_bytes": size,
    }


def error_body(
    code: str,
    message: str,
    retryable: bool,
    retry_after: str | None = None,
) -> dict[str, Any]:
    """The single error envelope; ``retry_after`` is present on ``429`` only."""
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if retry_after is not None:
        error["retry_after"] = retry_after
    return {"error": error}


def response(
    status: int,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """An API Gateway v2 proxy response carrying deterministic JSON."""
    merged = {"Content-Type": CONTENT_TYPE}
    merged.update(headers or {})
    return {
        "statusCode": status,
        "headers": merged,
        "body": json.dumps(body, separators=(",", ":"), sort_keys=True),
    }
