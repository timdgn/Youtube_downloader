# Phase 1 Data Model: Telegram Bot Interface

**Feature**: `002-telegram-bot-interface`
**Date**: 2026-11-16
**Inputs**: [spec.md](./spec.md), [research.md](./research.md),
[001 data model](../001-youtube-video-download/data-model.md)

The bot adds no store. It adds two item types and one index to the existing table, one
attribute to the job record, one optional attribute family to media objects, and
configuration that lives in SSM. The worker stays the only writer of media facts.

---

## Entity: Job (extended)

The existing Download Job, plus the fields ownership and delivery need.

**Identity and key** are unchanged: `job_id`, partition key `pk = "JOB#" + job_id`.

New or newly meaningful fields:

| Field | Type | Written by | Notes |
|-------|------|------------|-------|
| `owner_key` | string | bot | `tg:<telegram_user_id>#job`. GSI hash key. Absent on terminal requests, which keeps them out of the index. |
| `chat_id` | string | bot | Destination for the completion message. Equal to the sender id in a private chat, stored separately so the reply target is explicit. |
| `update_id` | number | bot | The Telegram update that created this job. Links the job to its interaction line. |
| `requested_tier` | string | bot | `low`, `medium`, `high`, `veryhigh`, `mp3`. The vocabulary word, not the resolved label. Absent on API requests. |
| `media_kind` | string | bot and API | `video` or `audio`. Defaults to `video` when absent, so records written before this feature still read correctly. |
| `is_test` | boolean | bot | True for a `/test` job. Changes only the completion wording and bypasses the reuse lookup. |
| `video_title` | string | worker | Source title, stripped of control characters and truncated to 80 characters. Display only, never in logs. |
| `deleted_at` | string | bot | ISO 8601 UTC. Present means the file was deleted through the bot. The item survives for audit; the object does not. |

Everything else is as documented in 001: state, timestamps, source host, video id,
requested and produced quality, media key and size, expiry, failure fields, attempt, TTL.

**Validation rules added**

| Rule | Requirement |
|------|-------------|
| `owner_key` matches `^tg:[0-9]{1,20}#job$` | FR-020 |
| `requested_tier` is one of the five words | FR-002 |
| `media_kind` is `video` or `audio`, never anything else | FR-005 |
| `video_title` has no control characters and is at most 80 characters | FR-012 (a title must not break a list line) |
| `deleted_at`, when present, is not earlier than `created_at` | FR-015 |

---

## Entity: Interaction

One item per update the bot handled, and the record FR-016 renders. It is not a job: a
refused or read-only message has no job, and history must still show it.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `pk` | string | yes | `UPD#<update_id>`. The same value implements FR-023 for job-creating requests. |
| `owner_key` | string | yes | `tg:<id>#interaction`. GSI hash key. |
| `created_at` | string | yes | ISO 8601 UTC, second precision. GSI range key. |
| `kind` | string | yes | `interaction`. Makes the item self-describing next to `JOB#` and `RATE#`. |
| `text` | string | yes | What was asked, sanitised: `<video_id> <tier>`, `/list`, `/delete <name>`, or `refused:<reason>`. Never a full URL with a query string, never a token, never a link. |
| `outcome` | string | yes | `accepted`, `reissued`, `in_progress`, `refused`, `rate_limited`, `command:<name>`. |
| `job_id` | string | no | Present when the interaction created a job or answered for one. Used to read the current state at render time. |
| `ttl` | number | yes | `created_at + 30 days`. History is not a permanent record. |

The interaction is written inside the job transaction when a job is created, so it
cannot exist without its job or vice versa. For commands and refusals it is a single
unconditional put after the reply is composed.

---

## Index: `owner-created-index`

| Setting | Value |
|---------|-------|
| Hash key | `owner_key` (string) |
| Range key | `created_at` (string) |
| Projection | `ALL` |
| Consistency | Eventually consistent, which is the only kind a GSI offers |
| Sparseness | Only items with `owner_key` are projected. Terminal jobs and rate windows are not. |

`ALL` is chosen over a hand-maintained include list because the items are under a
kilobyte and the fields a listing needs (state, produced quality, size, expiry, title,
tier) would otherwise need a second read of the base item.

**Access patterns**

| Operation | Query |
|-----------|-------|
| `/list` | GSI `owner_key = tg:<id>#job`, descending, stop at 200 live items |
| Reuse lookup (same video and tier) | GSI `owner_key = tg:<id>#job`, descending, filter on `video_id` and `requested_tier`, exclude `deleted_at`, take the first match |
| `/delete` target | GSI `owner_key = tg:<id>#job`, match the computed name in code |
| `/empty` | GSI `owner_key = tg:<id>#job`, stop at 200 live items per confirmation |
| `/history` | GSI `owner_key = tg:<id>#interaction`, descending, limit 10 |

Every query is scoped by the hash key, so one account's items are unreachable from
another's message. There is no scan anywhere in the bot.

---

## Entity: Media Object (extended)

| Property | Video | Audio |
|----------|-------|-------|
| Key | `output/<job_id>/<video_id>.mp4` | `output/<job_id>/<video_id>.mp3` |
| Content type | `video/mp4` | `audio/mpeg` |
| Object metadata | `job-id`, `produced-height`, `source-video-id`, `media-kind` | `job-id`, `source-video-id`, `media-kind=audio` |
| Presigned disposition | `attachment; filename="<name>"` | `attachment; filename="<name>"` |

The key derivation stays a pure function of `(job_id, video_id, extension)`. Extension
defaults to `mp4` so every existing caller and test is unchanged.

**Display name** is derived, never stored:

```text
<video_id>-<requested_tier>-<last 4 characters of job_id>.<ext>
example: dQw4w9WgXcQ-high-k2q1.mp4
```

The discriminator makes a name unique even after a delete and a fresh download, and
makes `/test` reproducible without colliding with an earlier test item. `/list` prints
exactly this string, and `/delete` accepts exactly this string.

---

## Entity: Approved Account (configuration)

Not stored in DynamoDB. One SSM parameter holds the list; Terraform owns it.

| Field | Storage | Notes |
|-------|---------|-------|
| Numeric account identifiers | `/ytdl/<env>/telegram-approved-user-ids`, comma separated | Empty means nobody, which is the safe default |
| Approval state | Presence in the list | Removal is deletion from the list; no separate flag |

The bot reads this list on every message, so a change governs the next message with no
deployment (FR-020, SC-003).

---

## Entity: Bot Configuration

All of it is Terraform variables materialised as environment variables or SSM
parameters. None of it is code branching.

| Setting | Source | dev default | Notes |
|---------|--------|-------------|-------|
| `telegram_bot_token` | Terraform variable, sensitive, stored in SSM | none, required | Never in a file, never in an environment variable, never logged |
| `TELEGRAM_TOKEN_PARAM` | environment | `/ytdl/dev/telegram-bot-token` | SecureString |
| `TELEGRAM_WEBHOOK_SECRET_PARAM` | environment | `/ytdl/dev/telegram-webhook-secret` | SecureString, generated by `random_password` with `special = false` and length 48, because Telegram accepts only `A-Z a-z 0-9 _ -` in `secret_token` |
| `TELEGRAM_APPROVED_PARAM` | environment | `/ytdl/dev/telegram-approved-user-ids` | String |
| `TELEGRAM_WEBHOOK_URL` | environment | stage URL plus `/telegram/webhook` | Compared by the health check |
| `TELEGRAM_DEFAULT_TIER` | environment | `high` | Used when a message carries a link only |
| `TELEGRAM_TEST_VIDEO_ID` | environment | a short public video | `/test` target, replaceable without code |
| `JOB_EVENTS_TOPIC_ARN` | environment | topic ARN | Published by the worker, read by nobody else |
| `YTDLP_VERSION` | file `app/ytdlp-version.txt` | pinned version | Reported by `/info` |
| `LINK_TTL_SECONDS`, `RETENTION_DAYS`, `MAX_HEIGHT`, `RATE_LIMIT_PER_HOUR` | existing environment | unchanged | Already validated in `config.py` |

---

## Tier vocabulary

| Word | Requested label | Height ceiling | Media kind | File extension |
|------|-----------------|----------------|------------|----------------|
| `low` | `240p` | 240 | video | `.mp4` |
| `medium` | `480p` | 480 | video | `.mp4` |
| `high` | `720p` | 720 | video | `.mp4` |
| `veryhigh` | `1080p` | 1080 | video | `.mp4` |
| `mp3` | none | none | audio | `.mp3` |

The requested label is capped by `MAX_HEIGHT` before it is stored, exactly as an API
request is capped today. `requested_tier` keeps the original word for display, reuse,
and the file name.

---

## State machine

No new job states. Two overlays on the existing machine:

```text
completed --(bot /delete, /empty)--> completed with deleted_at
                                          |
                                          +--> media object removed
                                          +--> excluded from /list, reuse, /history links
                                          +--> state stays completed for the audit trail
```

| From | To | Guard | Effect |
|------|----|-------|--------|
| `completed` | `completed` (deleted) | object deleted, or already absent | `deleted_at` set, idempotent |
| any non-completed | deleted | never through the bot | `/delete` only accepts stored, completed items |

A deleted job is reported as `expired` by the terminal API, because the object is gone.
The API's retention check also reads `deleted_at`, so the answer does not depend on the
timing of the S3 delete.

---

## Invariants

Statements the test suite asserts, in addition to the ten in 001.

11. Every item with `owner_key` also has `chat_id`; the reverse is not required.
12. `update_id` appears at most once in the table, for the lifetime of its TTL.
13. A job with `media_kind = "audio"` has `produced_quality = "mp3"` and no
    `produced_height`.
14. A listed item's object extension matches its `media_kind`.
15. A job with `deleted_at` is never returned by `/list`, never matched by reuse, and
    never yields a link.
16. No interaction item contains an access token, a download link, or a URL with a
    query string.
17. A request from an account that is not on the allowlist produces no job, no rate
    increment, and no interaction item.
18. The name printed by `/list` is the only name `/delete` accepts.
