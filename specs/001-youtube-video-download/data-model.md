# Phase 1 Data Model: YouTube Video Download Service

**Feature**: `001-youtube-video-download`
**Date**: 2026-11-15
**Inputs**: [spec.md](./spec.md), [research.md](./research.md)

Three stores hold state: DynamoDB for job records and rate windows, S3 for media
objects and optional operator configuration, and nothing else. The worker holds no
durable state between invocations.

---

## Entity: Download Job

The durable record of one request for one video. One item in DynamoDB.

**Identity**: `job_id`, an opaque string of 14 characters: 8 base-36 characters holding
the millisecond timestamp, then 6 random characters. Time-ordered and unique within a
millisecond. Not a sequential counter, and not enumerable.

**Key**: partition key `pk = "JOB#" + job_id`. No sort key.

| Field | Type | Required | Source | Notes |
|-------|------|----------|--------|-------|
| `pk` | string | yes | derived | `JOB#<job_id>` |
| `job_id` | string | yes | generated | Opaque, returned to the caller |
| `state` | string | yes | transitions | `accepted`, `processing`, `completed`, `failed` |
| `created_at` | string | yes | generated | ISO 8601 UTC, second precision |
| `updated_at` | string | yes | transitions | ISO 8601 UTC |
| `source_host` | string | yes | validation | Always a YouTube host in v1 |
| `video_id` | string | yes | validation | 11 characters from `[A-Za-z0-9_-]` |
| `requested_quality` | string or null | no | request | `360p`, `480p`, `720p`, `1080p`, or null for the default |
| `requested_height` | number or null | no | derived | Ceiling applied, so never above `max_height` |
| `produced_height` | number or null | no | extraction | Actual height of the stored file |
| `produced_quality` | string or null | no | derived | Label matching `produced_height` |
| `produced_vcodec` | string or null | no | extraction | e.g. `avc1.640028`, for the audit trail |
| `produced_acodec` | string or null | no | extraction | e.g. `mp4a.40.2` |
| `failure_category` | string or null | no | classification | One of the five categories; see FR-008 |
| `failure_message` | string or null | no | classification | Fixed, human-readable, no URLs |
| `failure_retryable` | boolean or null | no | classification | Whether resubmitting could succeed |
| `media_key` | string or null | no | derived | S3 object key |
| `media_size_bytes` | number or null | no | extraction | Verified against the stored object |
| `media_created_at` | number or null | no | stored | Epoch seconds |
| `media_expires_at` | number or null | no | derived | Epoch seconds, creation + 7 days |
| `attempt` | number | no | transitions | Absent at creation; set to 1 on the first claim, incremented on each further claim, never above 2 |
| `duration_seconds` | number or null | no | extraction | Source duration, for the audit entry |
| `processing_ms` | number or null | no | worker | Wall clock from claim to completion |
| `ttl` | number | yes | derived | Epoch seconds, `media_expires_at` + 30 days |

**Validation rules**

| Rule | Requirement |
|------|-------------|
| `video_id` matches `^[A-Za-z0-9_-]{11}$` | FR-002 |
| `source_host` is in the allowlist | FR-002 |
| `requested_height` ≤ configured ceiling; above the ceiling is capped, not rejected | FR-011 |
| `duration_seconds` ≤ 3,600 before any media is transferred | FR-009 |
| `media_size_bytes` ≤ 4,294,967,296 and equal to the uploaded object's size | FR-009 |
| `media_expires_at` = `media_created_at` + 604,800 | FR-012 |
| No field ever contains a token, cookie, header, or URL with a query string | FR-015 |

**State machine**

```text
                 +-------------+
   accepted ---->| processing  |----> completed
      |          +-------------+          |
      |                 |                 |
      |                 v                 v
      +------------> failed          (expired result, derived)
                        ^
                        |
              (retryable internal or upstream failure,
               maximum 2 attempts, then terminal)
```

| From | To | Guard | Effect |
|------|----|-------|--------|
| (none) | `accepted` | rate window has capacity | Item created, message sent |
| `accepted` | `processing` | none | `attempt` incremented, `updated_at` set |
| `processing` | `processing` | `updated_at` older than 900 s | Stale claim recovered after a killed invocation |
| `processing` | `completed` | media object exists and size is verified | Media fields and `ttl` written |
| `processing` | `failed` | none | Category, message, retryable flag written |
| `failed` | `processing` | `failure_retryable` is true and `attempt` < 2 | Only via SQS redelivery |
| `completed` | anything | never | Terminal for the record; the media may still expire |

Transitions are conditional writes on `state` (and, for the stale claim,
`updated_at`). A transition that does not match its guard is a no-op, never an
overwrite.

---

## Derived view: Result status

Not stored. Computed on every read, because it depends on the clock and on whether the
object still exists.

| Condition | Reported `result.status` | HTTP effect |
|-----------|--------------------------|-------------|
| `state != completed` | `null` | No link |
| `now` ≤ `media_expires_at` and object exists | `available` | Link included |
| `now` > `media_expires_at`, or object missing | `expired` | No link, instruction to resubmit |

"Object exists" is a `HeadObject` on `media_key`. The two conditions are checked
together so a lifecycle deletion is never masked by a stale timestamp.

---

## Entity: Media Object

The downloaded and remuxed file. One per job, at most, enforced by the deterministic
key.

| Property | Value |
|----------|-------|
| Bucket | One private bucket per environment |
| Key | `output/<job_id>/<video_id>.mp4` |
| Content type | `video/mp4` |
| Container / codecs | MP4 with H.264/AAC on the normal path (see research D-01) |
| Encryption | SSE-S3 (AES-256, AWS-managed key) |
| Object metadata | `job-id`, `produced-height`, `source-video-id` |
| Lifecycle | Expire after 7 days; abort incomplete multipart uploads after 1 day |
| Public access | Blocked at bucket level; reachable only through presigned URLs |
| Maximum size | 4,294,967,296 bytes |
| Access | Presigned `GetObject`, 3,600-second lifetime, attachment disposition |

**Key derivation is a pure function** of `(job_id, video_id)`, so a retried worker
writes the same object rather than a second one (FR-013).

**Second prefix**: `config/cookies.txt` holds an optional operator-supplied cookies
file for YouTube sessions. It is never written by the service, never logged, and is
read only by the worker role. This is the only other prefix in use.

---

## Entity: Quality Preference

A value object, resolved before any download starts.

| Requested | Resolved ceiling | Result label | Fallback rule |
|-----------|------------------|--------------|---------------|
| absent | 1080 (configured ceiling) | actual height | Best available at or below the ceiling |
| `1080p` | 1080 | actual height | Next lower rendition if 1080 absent |
| `720p` | 720 | actual height | Next lower rendition if 720 absent |
| `480p` | 480 | actual height | Next lower rendition if 480 absent |
| `360p` | 360 | actual height | Lowest available |
| `1440p`, `2160p`, anything above | capped to 1080 | actual height | Cap is reported, never an error |

Whatever is produced is written to `produced_quality`, and the job reports that value,
not the requested one (FR-010, FR-011).

---

## Entity: Rate Window

One item per credential per fixed one-hour window, aligned to the hour boundary.

| Field | Type | Notes |
|-------|------|-------|
| `pk` | string | `RATE#<window_start_epoch>#<credential_fingerprint>` |
| `count` | number | Incremented atomically |
| `ttl` | number | `window_start + 7200`, so the item self-cleans |

`credential_fingerprint` is the first 16 hex characters of the SHA-256 of the presented
token, so the item is attributable without storing the secret.

The counter is incremented in the same `TransactWriteItems` as the job creation, with
the condition `attribute_not_exists(count) OR count < :limit`. A failed condition means
the request is rejected with the throttled response and no job item is written. Link
re-issue and status reads never touch this item (FR-006, FR-014).

---

## Entity: Audit Entry

Written as the structured completion log line, and mirrored by the job record's own
fields. Not a separate table: at this volume, one record per job is the job record.

| Field | Present |
|-------|---------|
| `job_id` | yes |
| `source_host`, `video_id` | yes |
| `requested_quality`, `produced_quality` | yes |
| `outcome` (`completed` or a failure category) | yes |
| `processing_ms` | yes |
| `media_size_bytes` | completed jobs only |
| `attempt` | yes |
| Token, cookie, header, full URL with query string | never (FR-015, SC-009) |

`config/` objects, tokens, and presigned URLs are excluded by construction: the logger
accepts a fixed field allowlist and drops anything else.

---

## Entity: Environment

The deployment unit. All of it is Terraform variables; none of it is code branching.

| Setting | dev default | prod default | Notes |
|---------|-------------|--------------|-------|
| `environment` | `dev` | `prod` | Used in every resource name and tag |
| `aws_region` | `eu-west-3` | `eu-west-3` | |
| `max_height` | 1080 | 1080 | Requested values above it are capped |
| `max_duration_seconds` | 3600 | 3600 | Rejected before media transfer |
| `max_output_bytes` | 4294967296 | 4294967296 | |
| `retention_days` | 7 | 7 | Drives the S3 lifecycle rule |
| `link_ttl_seconds` | 3600 | 3600 | Constitution caps it at one hour |
| `rate_limit_per_hour` | 10 | 10 | |
| `worker_reserved_concurrency` | 2 | 2 | Also the simultaneous job cap |
| `worker_memory_mb` | 3008 | 3008 | |
| `worker_timeout_seconds` | 900 | 900 | Lambda maximum |
| `worker_ephemeral_storage_mb` | 10240 | 10240 | Must exceed 2 × `max_output_bytes` |
| `api_token` | generated | generated | `random_password`, stored in SSM |
| `alarm_email` | empty | operator-supplied | SNS subscription for alarms |
| `ytdlp_cookies_s3_uri` | set when the operator supplies cookies, e.g. `s3://<media-bucket>/config/cookies.txt` | empty | Optional; any non-empty value enables the hook, which reads `config/cookies.txt` from that environment's own bucket. See research D-14 |
| `ytdlp_proxy_param` | empty | empty | Optional, SSM parameter name |

Every resource carries `Project` and `Environment` tags (constitution principle III).

---

## Invariants

Statements that must hold for every reachable state, and that the test suite asserts.

1. At most one media object exists per `job_id`, at any time.
2. A job is never `completed` without a non-zero media object that exists in S3.
3. A job is never `completed` and `failed` at the same time.
4. `updated_at` never decreases.
5. `attempt` never exceeds 2 in a terminal state.
6. A rejected submission writes no job item and consumes no rate capacity.
7. A `429` response leaves no job item behind.
8. No item contains a presigned URL, a token, or a URL whose query string is longer
   than the video identifier.
9. Re-issuing a link never changes `updated_at` or `attempt`.
10. Deleting the media object externally turns every subsequent read into `expired`,
    never into a broken link.
