# Contract: HTTP API

**Feature**: `001-youtube-video-download`
**Base URL**: `https://<api-id>.execute-api.<region>.amazonaws.com/v1`
**Payload format**: API Gateway HTTP API, version 2.0
**Content type**: `application/json; charset=utf-8` on every request with a body and
every response

Three routes. No other route exists, and any other path returns `404` in the standard
error envelope.

---

## Authentication

Every request must carry the shared access token:

```
Authorization: Bearer <token>
```

- The token comes from the environment's SSM `SecureString` parameter. The operator
  retrieves it with
  `aws ssm get-parameter --name /ytdl/<env>/api-token --with-decryption --query Parameter.Value --output text`.
- Missing, malformed, or wrong token: `401` with code `unauthorized`. No distinction is
  made between the three cases, and the comparison is constant-time.
- The token never appears in a URL, a query string, or a log line.

---

## POST /v1/jobs

Accept a video for download. Returns immediately; no media is transferred while the
request is open.

**Request**

```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "quality": "720p"
}
```

| Field | Type | Required | Rules |
|-------|------|----------|-------|
| `url` | string | yes | 8 to 2048 characters. Must resolve to a single YouTube video. Share links, `shorts`, `embed`, and links with extra parameters are normalised. Playlist, channel, and live URLs are rejected. |
| `quality` | string or null | no | One of `360p`, `480p`, `720p`, `1080p`. Absent or null means the configured ceiling. Values above the ceiling are capped, not rejected. Any other value is `400 invalid_input`. |

**Response 202 Accepted**

```json
{
  "job_id": "m3x9k2q1f7",
  "state": "accepted",
  "created_at": "2026-11-15T14:03:11Z",
  "status_url": "/v1/jobs/m3x9k2q1f7"
}
```

| Status | Code | When |
|--------|------|------|
| 202 | | Accepted and queued |
| 400 | `invalid_input` | Not a single-video YouTube URL, malformed identifier, unknown quality value, missing body |
| 401 | `unauthorized` | Missing or wrong token |
| 429 | `rate_limited` | More than 10 accepted submissions in the current hour. `Retry-After` is present and the body names the retry time. |
| 500 | `internal_error` | Queue or store failure. `retryable: true`. |

Guarantees:

- The response is produced within 3 seconds, independent of video length (FR-003,
  SC-001). The work is queued, never performed inline.
- A `400`, `401`, or `429` creates no job and consumes no rate capacity.
- Two identical submissions produce two distinct jobs (no deduplication).

---

## GET /v1/jobs/{job_id}

Report the job's state, and offer a download link when a file is available.

**Response 200, accepted or processing**

```json
{
  "job_id": "m3x9k2q1f7",
  "state": "processing",
  "created_at": "2026-11-15T14:03:11Z",
  "updated_at": "2026-11-15T14:03:19Z",
  "requested_quality": "720p",
  "produced_quality": null,
  "result": null,
  "failure": null
}
```

**Response 200, completed and available**

```json
{
  "job_id": "m3x9k2q1f7",
  "state": "completed",
  "created_at": "2026-11-15T14:03:11Z",
  "updated_at": "2026-11-15T14:04:52Z",
  "requested_quality": "720p",
  "produced_quality": "720p",
  "result": {
    "status": "available",
    "download_url": "https://<bucket>.s3.<region>.amazonaws.com/output/...&X-Amz-Expires=3600&...",
    "expires_at": "2026-11-15T15:04:52Z",
    "size_bytes": 412345678,
    "content_type": "video/mp4"
  },
  "failure": null
}
```

**Response 200, completed and expired**

```json
{
  "job_id": "m3x9k2q1f7",
  "state": "completed",
  "created_at": "2026-11-15T14:03:11Z",
  "updated_at": "2026-11-15T14:04:52Z",
  "requested_quality": "720p",
  "produced_quality": "720p",
  "result": {
    "status": "expired",
    "download_url": null,
    "expires_at": null,
    "size_bytes": 412345678,
    "content_type": "video/mp4"
  },
  "failure": null
}
```

**Response 200, failed**

```json
{
  "job_id": "m3x9k2q1f7",
  "state": "failed",
  "created_at": "2026-11-15T14:03:11Z",
  "updated_at": "2026-11-15T14:04:02Z",
  "requested_quality": "720p",
  "produced_quality": null,
  "result": null,
  "failure": {
    "category": "unsupported_media",
    "retryable": false,
    "message": "The source refused this video. It may be private, removed, or unavailable in this region."
  }
}
```

| Status | Code | When |
|--------|------|------|
| 200 | | Any known job, in any state |
| 401 | `unauthorized` | Missing or wrong token |
| 404 | `not_found` | Unknown identifier, or one belonging to another environment |
| 500 | `internal_error` | Store failure |

Guarantees:

- The call is read-only: it never changes `state`, `updated_at`, or `attempt`, and never
  starts or repeats work on the first `HEAD`/`PUT` path (FR-007, US2 scenario 5).
- Repeated calls return the same state and the same failure category.
- `produced_quality` is the actual quality of the stored file, never an echo of the
  request (FR-010).
- `result.status` is `available` only if the object exists and is inside the retention
  window. Otherwise `expired`, with `download_url: null` (FR-012).
- Every `download_url` is a presigned `GetObject` valid for 3,600 seconds and requires
  no further credential.

**Failure categories** (fixed set, FR-008)

| Category | `retryable` | Meaning |
|----------|-------------|---------|
| `invalid_input` | false | The link is not a supported single video |
| `unsupported_media` | false | Private, removed, members-only, age-restricted, region-locked, DRM, live |
| `limit_exceeded` | false | Duration or size above the configured caps |
| `upstream_problem` | true or false | Source throttling or interruption (retryable); a hard block or an extraction break (not retryable, message says to check configuration) |
| `internal_error` | true | Our failure: storage, deadline, unexpected exception |

---

## POST /v1/jobs/{job_id}/link

Issue a fresh download link for a completed job whose file is still stored. Does not
reprocess the video and does not consume rate capacity (FR-006, SC-012).

**Request body**: none.

**Response 200**

```json
{
  "job_id": "m3x9k2q1f7",
  "download_url": "https://<bucket>.s3.<region>.amazonaws.com/output/...&X-Amz-Expires=3600&...",
  "expires_at": "2026-11-15T15:41:07Z",
  "size_bytes": 412345678
}
```

| Status | Code | When |
|--------|------|------|
| 200 | | File exists and is inside the retention window |
| 401 | `unauthorized` | Missing or wrong token |
| 404 | `not_found` | Unknown identifier |
| 409 | `not_completed` | Job is `accepted`, `processing`, or `failed` |
| 410 | `result_expired` | Job is `completed` but the file is gone or past retention |

Guarantees:

- Identical semantics to the link in `GET /v1/jobs/{job_id}`, including the 3,600-second
  lifetime.
- Never mutates the job record.
- `410` instructs the operator to submit the link again; it never returns a dead URL.

---

## Error envelope

Every non-2xx response uses one shape.

```json
{
  "error": {
    "code": "rate_limited",
    "message": "Submission limit reached for this hour. Retry after 2026-11-15T15:00:00Z.",
    "retryable": true,
    "retry_after": "2026-11-15T15:00:00Z"
  }
}
```

| Field | Type | Notes |
|-------|------|-------|
| `code` | string | Machine-readable, from the fixed sets above |
| `message` | string | One sentence, English, no stack trace, no URL with a query string |
| `retryable` | boolean | Whether the same request could succeed later |
| `retry_after` | string or null | ISO 8601 UTC, present on `429` only |

---

## Cross-cutting guarantees

| Guarantee | Requirement |
|-----------|-------------|
| No response body ever contains media bytes | FR-005, constitution II |
| No response body exceeds a few kilobytes | constitution II (6 MB limit respected by design) |
| Every response is valid JSON, including `404` for an unknown route | contract tests |
| A `429` is returned within one second, never a timeout | FR-014, SC-010 |
| No query string, token, or header value is written to logs or the access log | FR-015, SC-009 |
| The API is reachable only with the shared token; there is no anonymous path | constitution III |
