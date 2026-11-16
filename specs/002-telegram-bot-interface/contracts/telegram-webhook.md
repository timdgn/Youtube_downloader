# Contract: Telegram Webhook

**Feature**: `002-telegram-bot-interface`
**Producer**: Telegram Bot API
**Consumer**: `bot` Lambda, invoked synchronously by API Gateway HTTP API
**Route**: `POST /telegram/webhook` on the `ytdl-<env>-bot` API, stage `$default`
**Registration**: `setWebhook`, owned by `terraform_data.telegram_webhook`

The webhook is the only inbound path. Nothing in this service calls `getUpdates`, and a
webhook and a poll cannot coexist for one bot.

---

## Request

```http
POST /telegram/webhook HTTP/1.1
Content-Type: application/json
X-Telegram-Bot-Api-Secret-Token: <webhook secret>

{
  "update_id": 812345678,
  "message": {
    "message_id": 4412,
    "date": 1763294591,
    "chat": {"id": 123456789, "type": "private", "first_name": "..."},
    "from": {"id": 123456789, "is_bot": false, "first_name": "..."},
    "text": "https://www.youtube.com/watch?v=dQw4w9WgXcQ medium"
  }
}
```

| Field read | Type | Required | Use |
|------------|------|----------|-----|
| `update_id` | number | yes | Idempotency key and interaction identity |
| `message.from.id` | number | yes | Account identity for the allowlist and the rate window |
| `message.chat.id` | number | yes | Reply destination |
| `message.chat.type` | string | yes | Only `private` is served |
| `message.text` | string | no | The command or request. A message without text gets the format hint |
| Anything else | | | Ignored. The payload is never logged |

`allowed_updates` is `["message"]`. Edited messages, reactions, callback queries and
inline queries are not delivered and are not handled.

---

## Authentication

`setWebhook` supplies a `secret_token` of 1 to 256 characters from `A-Z a-z 0-9 _ -`.
Telegram sends it back in `X-Telegram-Bot-Api-Secret-Token` on every request. The
handler compares it with the value in SSM in constant time and answers `403` on any
mismatch, before parsing the body.

The route is public by necessity. Three things bound the exposure:

1. The secret header, which stops forged updates.
2. Stage throttling of 10 requests per second with a burst of 20.
3. Reserved concurrency of 5 on the function.

The secret is not in the URL, so the access log can keep the request path and still
contain nothing sensitive.

---

## Responses

| Situation | Status | Body | Effect on Telegram |
|-----------|--------|------|--------------------|
| Handled, including a refusal to the user | `200` | empty | Delivery confirmed, no retry |
| Duplicate `update_id` | `200` | empty | Delivery confirmed, no second side effect |
| Wrong or missing secret | `403` | empty | Delivery fails; the operator sees the alarm, not a retry storm |
| Malformed body, no `update_id` | `400` | empty | Delivery fails; a retry cannot fix it |
| Unexpected internal failure | `500` | empty | Telegram retries, which is what we want |

A refusal is a successful conversation turn, so it is a `200`. Only a fault that a retry
could fix is a `500`.

Nothing in the response body is ever read by Telegram for this design. The bot replies
with explicit `sendMessage` calls rather than the webhook-response trick, because an
explicit call can be tested, logged and retried by the platform.

---

## Outbound call

```http
POST https://api.telegram.org/bot<token>/sendMessage
Content-Type: application/json

{"chat_id": 123456789, "text": "<plain text, 1 to 4096 characters>"}
```

| Property | Value |
|----------|-------|
| Format | Plain text. No `parse_mode`, so a title can never inject markup |
| Length | At most 3,500 characters per page, split on line boundaries |
| Ordering | Pages sent in order, one request at a time |
| Timeout | 10 seconds per call |
| Retries in process | None |

Error policy:

| Telegram answer | Action | Reason |
|-----------------|--------|--------|
| 2XX | Log success | Delivered |
| 400 (bad request, chat not found) | Log, drop | Repeating it cannot help |
| 403 (bot blocked, kicked) | Log, drop | The operator removed the bot |
| 401 (unauthorized, revoked token) | Log, raise | A deployment fault; the health alarm is the response |
| 429 (too many requests) | Log, raise | The caller's retry policy applies |
| 5XX or network error | Log, raise | Transient |

On the webhook path a raise becomes a `500`, so Telegram retries. On the completion
path a raise reaches SNS, which retries the invocation.

---

## Idempotency

Telegram may deliver the same `update_id` more than once after a non-2XX response or a
timeout. For a request that creates a job, the `UPD#<update_id>` item is written in the
same DynamoDB transaction as the job, so a duplicate cancels everything. Read-only and
already idempotent commands are safe to repeat by nature.

The bot never relies on ordering: `update_id` is monotonic per bot but two updates may
be delivered concurrently, so each is handled independently.

---

## Privacy rules

| Rule | Requirement |
|------|-------------|
| No token, secret, or signed link in a log line | FR-030 |
| No full source URL with a query string in a log line or an interaction | FR-030 |
| No raw account identifier in a log line | Constitution principle V. The update id identifies the turn; the rate key uses a fingerprint |
| No media bytes through the chat | FR-009 |
| No stack trace or platform error text sent to a user | FR-027, SC-010 |
