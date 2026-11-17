# Quickstart: Telegram Bot Interface

**Feature**: `002-telegram-bot-interface`
**Prerequisite**: `001-youtube-video-download/quickstart.md` completed at least once, so
the stack deploys and the terminal path already works.

This guide proves the bot end to end: registration, every command, every refusal, and a
teardown that leaves nothing behind. Every step states what to observe. No step
generates traffic to approach a cost ceiling; the rate-limit behaviour is verified by
the offline suite instead (constitution principle V).

> **Amendment 2026-11-17.** Production runs the bot on the host compute target
> (`docs/runbook.md` section 12). Sections 1 to 5 are unchanged except for the address
> Telegram reports: it is the public origin
> `https://ytdl.timcloud.fr/telegram/webhook`, not an `execute-api` URL. The teardown in
> section 14 must also stop the two host services, or the front end keeps answering a
> webhook that no longer has a consumer.

---

## 0. What you need

- The dev stack from 001, or a fresh account where you will create it.
- A Telegram account.
- `TF_VAR_telegram_bot_token` exported in the shell, never written to a file.
- `TF_VAR_telegram_approved_user_ids` exported as an HCL list.

---

## 1. Create the bot

In Telegram, talk to **@BotFather**:

1. `/newbot`
2. Choose a display name.
3. Choose a username ending in `bot`.
4. Copy the token it returns.

This is the one human step in the whole feature. Telegram has no API for account
creation, the same way AWS has no Terraform resource for an AWS account.

```bash
export TF_VAR_telegram_bot_token='123456789:AA...'
unset HISTFILE   # optional: keep the token out of shell history
```

---

## 2. Find your numeric account id

Send `/start` to the new bot. Before a webhook exists, Telegram queues the update, and a
read-only call shows it:

```bash
curl -s "https://api.telegram.org/bot$TF_VAR_telegram_bot_token/getUpdates" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"][-1]["message"]["from"]["id"])'
```

```bash
export TF_VAR_telegram_approved_user_ids='["<that number>"]'
```

If you prefer not to call the API, a service such as @userinfobot reports the same
number. The bot cannot tell you: it refuses unapproved accounts without revealing
anything about itself.

---

## 3. Apply

```bash
make deploy ENV=dev
```

**Expected**: the apply succeeds, and it reports 23 new resources, including the bot
function, its API, three SSM parameters, the job-events topic, the hourly health rule
and two alarms.

If the webhook step fails, the script printed Telegram's own error. A `401` means the
token is wrong; a `400` usually means a malformed URL.

---

## 4. Prove the webhook is registered

```bash
make bot-health ENV=dev
```

**Expected**:

```text
url                   https://<api-id>.execute-api.eu-west-3.amazonaws.com/telegram/webhook
pending_update_count  0
max_connections       5
allowed_updates       ['message']
```

A non-empty `last_error_message` means Telegram is failing to deliver. Read it before
continuing.

---

## 5. First contact

Send `/start` in the private chat.

**Expected**: a greeting that points at `/help`. No delay beyond a second or two, even
on a cold start.

Then send `/help`.

**Expected**: the full block from `contracts/bot-commands.md`, word for word. Compare it:

```bash
make bot-help-check
```

**Expected**: one passing test. It extracts the block from
`contracts/bot-commands.md` and compares it, character for character, with the constant
the handler sends.

---

## 6. One download per tier

Send:

```text
https://www.youtube.com/watch?v=<a short public video> medium
```

**Expected**, in order:

1. An acknowledgement within five seconds naming `medium` and 480p, before any download.
2. A completion message with a title, `Quality: 480p` (or lower, if the source has no
   480p), the size, a link, and the link's expiry time.
3. The link downloads a file that plays with sound.

Repeat once each for `low`, `high`, `veryhigh` and `mp3`.

| Tier | What to check |
|------|---------------|
| `low` | A video file, 240p or lower, plays with sound |
| `medium` | 480p or lower |
| `high` | 720p or lower |
| `veryhigh` | 1080p or lower |
| `mp3` | An `.mp3` file that plays in a music player, not a video |

A tier that the source cannot meet must produce a lower quality and say so. It must not
claim the ceiling was met.

---

## 7. Reuse and refresh

Send the same `medium` request again.

**Expected**: `Already stored` with a fresh link, in seconds, and no acknowledgement
line. Confirm nothing was downloaded twice:

```bash
aws s3 ls "s3://$(terraform -chdir=infra output -raw media_bucket)/output/" \
  --recursive | grep <video_id>
```

**Expected**: one object per tier requested, and no second object for the repeated
request.

---

## 8. List, delete, empty

`/list`

**Expected**: one line per stored item, each with a name like
`dQw4w9WgXcQ-high-k2q1.mp4`, a title, the produced quality, the size, the age, and the
days left.

`/delete <that name>`

**Expected**: `Deleted <name>.` Then `/list` no longer shows it, and the object is gone
from S3.

Send the original request again.

**Expected**: the video downloads again, rather than a dead link being offered.

`/empty`

**Expected**: a count and a request to confirm. Nothing is deleted yet: `/list` is
unchanged.

`/empty confirm`

**Expected**: `Deleted N items.` and `/list` answers `No videos are stored.`

---

## 9. History, info, self-test

`/history`

**Expected**: the last ten interactions, newest first, with time, request and outcome.
No access token and no download link appears in any line.

`/info`

**Expected**: the yt-dlp version actually in the image, the quality ceiling, the
retention window, the link lifetime, and the rate limit. Compare the version:

```bash
cat app/ytdlp-version.txt
```

**Expected**: the same string.

`/test`

**Expected**: `Pipeline test started.`, then a completion message for the configured
short video. Run it once. It consumes one unit of the hourly budget like any other
request, and nothing loops or schedules it again.

---

## 10. Refusals

From an account that is **not** on the allowlist, send a valid link and tier.

**Expected**: `This bot is private.` No download starts. Confirm nothing was created:

```bash
aws dynamodb scan --table-name ytdl-dev-jobs \
  --filter-expression 'attribute_exists(owner_key)' --select COUNT
```

**Expected**: the count is unchanged from before the attempt.

From an approved account, add the bot to a group and send a request there.

**Expected**: `This bot only works in a private chat.` No job is created.

Send two links in one message, then a link with `hq`.

**Expected**: a refusal that restates the expected format, or lists the five accepted
words. Neither starts a job.

---

## 11. Change the allowlist without redeploying

Add or remove an account:

```bash
export TF_VAR_telegram_approved_user_ids='["<id one>"]'
make deploy ENV=dev
```

**Expected**: the next message from the removed account is refused, and the next message
from a remaining account still works. No image was rebuilt for this step; the list lives
in SSM and is read per message.

---

## 12. Rate limit, verified offline

Do **not** send eleven real requests to watch the limit trigger. That spends money to
prove arithmetic. The behaviour is proven by the contract suite with a fake clock:

```bash
make test
```

**Expected**: a test asserts that the eleventh request in one hour is answered with the
capacity-return time and creates no job and no rate increment.

Live confirmation is available cheaply by reading the counter:

```bash
aws dynamodb query --table-name ytdl-dev-jobs \
  --key-condition-expression 'begins_with(pk, :p)' \
  --expression-attribute-values '{":p":{"S":"RATE#"}}'
```

---

## 13. Health signal

Read the metric the hourly check publishes, without waiting for an alarm:

```bash
aws cloudwatch get-metric-statistics \
  --namespace YTDL/Bot --metric-name WebhookHealthy \
  --start-time "$(date -u -v-6H +%Y-%m-%dT%H:%M:%SZ)" \
  --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --period 3600 --statistics Minimum
```

**Expected**: at least one datapoint with value `1`. A `0` is the alarm condition and
means the webhook is gone or the token was revoked.

---

## 14. Teardown, and proof that Telegram forgets

Save the token first, because the parameter it lives in is about to be deleted:

```bash
TG_TOKEN=$(aws ssm get-parameter --name /ytdl/dev/telegram-bot-token \
  --with-decryption --query Parameter.Value --output text)
```

```bash
make destroy ENV=dev
make verify-destroy ENV=dev
```

**Expected**: destroy succeeds, and the webhook removal step runs before the parameters
are deleted. `verify-destroy` reports no surviving AWS resources.

```bash
curl -s "https://api.telegram.org/bot$TG_TOKEN/getWebhookInfo"
```

**Expected**: `"url": ""`. The bot registration is gone, not pointing at a dead API.

If the removal step printed a warning instead of succeeding, the token had already been
invalidated. Registration is impossible then, but check the result above before calling
the teardown complete.

---

## 15. What this guide deliberately did not do

| Not done | Why |
|----------|-----|
| Eleven live submissions to watch the rate limit | Spends money to demonstrate a counter; covered offline |
| Hours of repeated `/test` runs | Principle V: a ceiling is a guardrail, never a target |
| A second real account for the allowlist step | Optional; removing yourself from the list proves the same path |
| A retained environment with no media | Not a validation step, and idle storage is still cost |
