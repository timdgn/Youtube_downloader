"""End-to-end download drill against a deployed stack (T048).

Run only with a deployed environment:

    API_BASE_URL=https://<api-id>.execute-api.<region>.amazonaws.com \
    API_TOKEN=<token> E2E_VIDEO_URL=https://youtu.be/<id> \
    python -m pytest tests/e2e/test_download.py -m e2e
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

import pytest

API_BASE_URL = os.environ.get("API_BASE_URL", "").rstrip("/")
API_TOKEN = os.environ.get("API_TOKEN", "")
E2E_VIDEO_URL = os.environ.get("E2E_VIDEO_URL", "")

POLL_DEADLINE_SECONDS = 600.0
POLL_INTERVAL_SECONDS = 3.0
REQUEST_TIMEOUT_SECONDS = 60.0
MP4_SIGNATURE = b"ftyp"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not (API_BASE_URL and API_TOKEN),
        reason="API_BASE_URL and API_TOKEN must both be set",
    ),
    pytest.mark.skipif(not E2E_VIDEO_URL, reason="E2E_VIDEO_URL must be set"),
]


def _request(method: str, path: str, payload: dict | None = None) -> tuple[int, bytes]:
    """Call the API with the shared token and return the status and raw body."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE_URL}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {API_TOKEN}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def _download(url: str) -> bytes:
    """Follow a presigned URL with no credentials of any kind."""
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return response.read()


def test_submit_poll_and_download() -> None:
    status, raw = _request("POST", "/v1/jobs", {"url": E2E_VIDEO_URL})
    assert status == 202, raw.decode("utf-8", "replace")

    accepted = json.loads(raw)
    assert accepted["state"] == "accepted"
    job_id = accepted["job_id"]
    assert accepted["status_url"] == f"/v1/jobs/{job_id}"

    deadline = time.monotonic() + POLL_DEADLINE_SECONDS
    job: dict = {}
    while True:
        status, raw = _request("GET", f"/v1/jobs/{job_id}")
        assert status == 200, raw.decode("utf-8", "replace")
        job = json.loads(raw)

        if job["state"] == "completed":
            break
        if job["state"] == "failed":
            pytest.fail(f"job {job_id} failed: {job.get('failure')}")

        assert time.monotonic() < deadline, f"job {job_id} did not complete within 10 minutes"
        time.sleep(POLL_INTERVAL_SECONDS)

    result = job["result"]
    assert result["status"] == "available"
    assert result["content_type"] == "video/mp4"
    assert result["download_url"]

    media = _download(result["download_url"])

    assert media
    assert media[4:8] == MP4_SIGNATURE
    assert len(media) == result["size_bytes"]
