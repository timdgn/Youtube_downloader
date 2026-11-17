# Contract: Job Message (SQS)

**Feature**: `001-youtube-video-download`
**Producer**: `api` Lambda, on a `202` acceptance
**Consumer**: `worker` Lambda, batch size 1, `ReportBatchItemFailures`
**Queue**: one standard queue per environment, with a dead-letter queue
(`maxReceiveCount = 2`, 14-day retention on the DLQ)

The message is a pointer, not a payload. It is never larger than a few hundred bytes,
well inside the 1 MB asynchronous invocation limit.

> **Deployment note (2026-11-17).** On the host target the consumer is the host's worker
> loop, not Lambda. It receives one message at a time, deletes on success and leaves a
> reported batch-item failure for redelivery after the queue's visibility timeout, which
> is the same guarantee `ReportBatchItemFailures` provides.

---

## Body

```json
{
  "version": 1,
  "job_id": "m3x9k2q1f7",
  "video_id": "dQw4w9WgXcQ",
  "source_host": "www.youtube.com",
  "requested_height": 720,
  "enqueued_at": "2026-11-15T14:03:11Z"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `version` | number | yes | `1`. A message with an unknown version is failed, not guessed at. |
| `job_id` | string | yes | Authoritative key. The worker re-reads the job from DynamoDB rather than trusting the rest of the message. |
| `video_id` | string | yes | 11 characters, already validated |
| `source_host` | string | yes | Normalised host |
| `requested_height` | number or null | yes | Already capped to the ceiling; null means the default |
| `enqueued_at` | string | yes | ISO 8601 UTC |

Message attributes: none. The `job_id` is carried in the body only, so no duplicate
source of truth exists.

---

## Consumption rules

| Rule | Behaviour |
|------|-----------|
| Batch size | 1. One message is one job, so a failure has one owner. |
| Partial batch failure | The handler returns `{"batchItemFailures": [{"itemIdentifier": "<messageId>"}]}` on a retryable failure, and an empty list on success or a terminal failure. |
| Redelivery | A retried job sees `attempt > 1` and either resumes from an existing S3 object or re-runs. It never creates a second file (FR-013). |
| Poison message | After 2 receives, the message moves to the DLQ and the job is already `failed` with a terminal category. A DLQ depth above zero is an alarm. |
| Unknown `version` | Terminal failure with `internal_error`, no retry. |
| Missing job record | Acknowledge and drop. The record's TTL may have expired, and retrying cannot help. |
| Job already `completed` | Acknowledge and drop without touching media. |
| Job already `failed` and not retryable | Acknowledge and drop. |

---

## Visibility and retry timing

| Setting | Value | Reason |
|---------|-------|--------|
| Visibility timeout | 960 s | Must exceed the 900 s function timeout, or a second worker takes a job that is still running |
| `maxReceiveCount` | 2 | One retry only: a second identical attempt rarely changes an outcome that depends on input or upstream |
| DLQ message retention | 14 days | Long enough to investigate a week later |
| Event source batch window | 0 s | No reason to delay a single-message batch |
| Maximum concurrency | Inherited from the worker's reserved concurrency of 2 | This is the simultaneous-job cap (FR-014) |
