# Contract: Job Event (SNS)

**Feature**: `002-telegram-bot-interface`
**Producer**: `worker` Lambda, after a terminal outcome
**Consumer**: `bot` Lambda, subscribed to the topic
**Topic**: one standard SNS topic per environment, `ytdl-<env>-job-events`

SNS is a notification path, not a source of truth. The message carries a pointer; the
job record remains authoritative.

---

## Body

```json
{
  "version": 1,
  "event": "job_completed",
  "job_id": "m3x9k2q1f7"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `version` | number | yes | `1`. An unknown version is logged and dropped, never guessed at |
| `event` | string | yes | `job_completed` or `job_failed` |
| `job_id` | string | yes | The bot re-reads the job from DynamoDB |

No chat identifier, no title, no media key, and no link is carried. The bot reads those
from the job record, so a message that outlives its job is harmless: the record is gone
and nothing is sent.

Message attributes: none.

---

## When the worker publishes

| Condition | Publish |
|-----------|---------|
| Job completed and `chat_id` is present | `job_completed` |
| Job failed terminally and `chat_id` is present | `job_failed` |
| Job failed but will be retried by the queue | Nothing. The next attempt decides |
| Job came from the terminal API, so no `chat_id` | Nothing |
| Publishing itself fails | Log, do not fail the job. The job outcome is already recorded; a missing chat message is not a reason to redo a download |

Publishing happens after the job record is updated, so the consumer never reads a job
that has not reached its final state.

---

## Consumer rules

| Rule | Behaviour |
|------|-----------|
| Batch | SNS invokes with one record per invocation |
| Missing job record | Log and return success. Retrying cannot help |
| No `chat_id` | Log and return success |
| `job_completed` | Presign the stored object, send the completion message, return success |
| `job_failed` | Send the failure message, return success |
| Job completed but the object is missing or past retention | Send the expiry sentence, with the instruction to send the request again |
| Telegram returns 400 or 403 | Log and return success. The user blocked the bot, or the chat is gone |
| Telegram returns 401, 429, 5XX, or the network fails | Raise, so SNS retries |
| Same event delivered twice | The user may receive two identical messages. This is accepted; the alternative is a delivery ledger that can disagree with the chat |

---

## Delivery guarantees

| Property | Value |
|----------|-------|
| Delivery | At least once |
| Retries | SNS default policy, on a failed invocation |
| Ordering | Not guaranteed across jobs. Two jobs finishing together may post in either order, and each message names its own video |
| Durability | The topic is not used as a queue. Nothing depends on a message being delivered, because `/list` and a repeated request can always recover the link |

---

## Why this shape

The worker must not hold a chat token, and the bot must not poll. SNS is the smallest
component that turns "this job finished" into "this function ran", and the existing
stack already uses SNS for alarms, so the ownership and subscription model is familiar.

The cost is one extra resource and one small IAM statement (`sns:Publish` on this one
topic ARN). The benefit is that a chat outage cannot delay or fail a media job, and a
media failure cannot delay a chat message.
