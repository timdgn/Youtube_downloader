# Phase 0 Research: Telegram Bot Interface

**Feature**: `002-telegram-bot-interface`
**Date**: 2026-11-16
**Inputs**: [spec.md](./spec.md), [constitution](../../.specify/memory/constitution.md),
[001 research](../001-youtube-video-download/research.md)

This file records the decisions this feature adds, and only those. Everything the
foundational plan settled (runtime, container image, queue shape, storage split,
presigning, failure taxonomy) stands unchanged unless a decision below says otherwise.

> **Amendment 2026-11-17.** The decisions below were taken against the Lambda target and
> remain valid for it. Production now runs the bot in the host image (001 research D-20):
> D-01's "dedicated HTTP API" is the host's public origin, D-04's SNS transport is an
> authenticated loopback `POST` carrying the same body, and D-11's "one function" is one
> container service. Nothing about the command grammar, the replies, the allowlist, the
> ownership model or the idempotency rule changed.

---

## Verified platform facts

Facts checked against the current official documentation on 2026-11-16, not against
memory. Each one changes a design decision, so each one is cited.

| Fact | Consequence | Source |
|------|-------------|--------|
| Updates arrive either by webhook or by `getUpdates`, never both, and Telegram keeps them at most 24 hours | A webhook is the only push option; polling would need a scheduler and durable offset | [Bot API, Getting updates](https://core.telegram.org/bots/api#getting-updates) |
| `setWebhook` takes an HTTPS URL, and Telegram repeats a delivery whose response is not 2XY, then gives up after a reasonable number of attempts | The handler must answer 2xx for anything it has fully handled, and non-2xx only when a retry can help | [setWebhook](https://core.telegram.org/bots/api/methods/setwebhook) |
| `secret_token` is 1 to 256 characters of `A-Z a-z 0-9 _ -`, and is sent back in the `X-Telegram-Bot-Api-Secret-Token` header on every request | The bot can reject forged calls without a path secret, and the check is a constant-time string compare | [setWebhook](https://core.telegram.org/bots/api/methods/setwebhook) |
| `max_connections` is 1 to 100 and defaults to 40 | Concurrency is bounded at Telegram's side, matching the function's reserved concurrency | [setWebhook](https://core.telegram.org/bots/api/methods/setwebhook) |
| `allowed_updates` filters update types | Subscribe to `message` only, so edits, reactions and callback noise never invoke the function | [setWebhook](https://core.telegram.org/bots/api/methods/setwebhook) |
| Webhooks need TLS 1.2 or later, ports 443, 80, 88 or 8443, no redirects, IPv4 | The API Gateway default regional endpoint satisfies all of it with no certificate work | [Webhook guide](https://core.telegram.org/bots/webhooks) |
| `sendMessage` accepts 1 to 4096 characters after entities parsing | Every reply must be split, and plain text makes the count exact | [sendMessage](https://core.telegram.org/bots/api/methods/sendmessage) |
| A bot should send at most one message per second in one chat | Splitting a long list must not fire all pages at once | [Bot FAQ](https://core.telegram.org/bots/faq) |
| `getWebhookInfo` returns `url`, `pending_update_count`, `last_error_date`, `last_error_message`, `max_connections`, `allowed_updates` | Webhook health is observable without sending traffic | [WebhookInfo](https://core.telegram.org/bots/api/types/webhookinfo) |
| The yt-dlp FFmpeg builds compile `libmp3lame` into every variant, including `linuxarm64` | The existing pinned `ffmpeg` can produce MP3 with no image change beyond the code path | [FFmpeg-Builds audio codecs](https://github.com/yt-dlp/FFmpeg-Builds), [Dockerfile](../../app/Dockerfile) |
| An SNS to Lambda subscription retries a failed asynchronous invocation, and delivers at least once | The completion message can duplicate; the handler must be harmless when it does | [AWS SNS Lambda subscriptions](https://docs.aws.amazon.com/sns/latest/dg/sns-lambda-as-subscriber.html) |
| An HTTP API integration has a 30-second ceiling | `/empty` and `/list` must be bounded, and the bot must never wait for a download inside a request | [API Gateway quotas](https://docs.aws.amazon.com/apigateway/latest/developerguide/limits.html) |

---

## D-01: Transport is a webhook on its own HTTP API

**Decision**: a dedicated API Gateway HTTP API (`ytdl-<env>-bot`), one route,
`POST /telegram/webhook`, integrated with a `bot` Lambda. Its own stage, its own access
log group, its own throttling. The terminal API keeps exactly its three routes.

**Rationale**: Lambda has no process to hold a long poll. The webhook is push, fits the
free tier, and needs no scheduler or offset state. A separate API rather than a fourth
route on the existing one, because the two front doors have opposite trust models: the
terminal API is bearer-token gated and throttled at 5 requests per second, while the bot
route is public by necessity and must tolerate Telegram bursts. Sharing a stage would
couple those two numbers and would blur a contract that is already documented.

**Alternatives considered**:

- *Long polling on an EventBridge schedule*: one minute of latency at best, a durable
  offset to maintain, and constant invocations while idle. Rejected.
- *A fourth route on the existing API*: simplest to write, but it changes the
  documented three-route contract and forces one throttle setting on both callers.
  Rejected.
- *A Lambda function URL instead of API Gateway*: fewer resources, but no stage-level
  throttling, no access log format, and a second public-invoke style to reason about.
  Rejected.

---

## D-02: Webhook registration and removal belong to Terraform, through one script

**Decision**: a `terraform_data` resource with a creation-time `local-exec` that calls
`setWebhook`, and a destroy-time `local-exec` that calls `deleteWebhook`. Both run
`infra/scripts/telegram-webhook.sh`, which reads the token and the webhook secret from
SSM at run time. The token never appears in Terraform state or in the plan.

**Rationale**: FR-031 and SC-012 require that removing an environment leaves no bot
registration behind. A webhook registered by hand, or by a one-shot Lambda invocation
during apply, survives `terraform destroy` and then points at a dead URL. Terraform's
own documentation calls provisioners a last resort and names exactly this case, a
deregistration from a system with no provider, as a legitimate use.

Two limits of the mechanism are accepted and documented in the runbook:

1. A destroy-time provisioner only runs while its resource block is still in the
   configuration. Removing the block entirely skips it. The documented removal is
   `count = 0`, apply, then delete the block.
2. A destroy-time provisioner may only reference `self`, so the URL and parameter names
   are copied into `input` and read back through `self.input`.

Two details follow from the Telegram side, not the Terraform side:

- The generated secret uses `special = false` and length 48, because `secret_token`
  accepts only `A-Z a-z 0-9 _ -`. A punctuation character would make `setWebhook` fail.
- The script needs `curl` and the AWS CLI on the machine that applies. Both are already
  prerequisites of the Makefile, so this adds no new tool.

**Alternatives considered**:

- *`yi-jiayu/telegram` provider*: a real resource with create, update and delete, but
  last released in 2020, single-maintainer, and nothing in this repository pins or
  reviews it. Rejected.
- *`Mastercard/restapi` `restapi_object`*: Telegram's `setWebhook` returns
  `{"ok":true,"result":true}`, not an object with an identifier, and removal is a second
  method rather than `DELETE /resource/{id}`. It can be bent into shape with
  `destroy_method` and `destroy_data`, but the bending is less reviewable than twenty
  lines of shell. Rejected.
- *`data "aws_lambda_invocation"` during apply*: the pattern several AWS blog posts use,
  and it works for creation. A data source has no destroy phase, so the webhook would
  outlive the stack. Rejected.
- *Register once by hand*: violates FR-031 and the constitution's IaC principle.
  Rejected.

---

## D-03: Access control is an SSM allowlist, read per message

**Decision**: one SSM parameter, `/ytdl/<env>/telegram-approved-user-ids`, holding a
comma-separated list of numeric Telegram account identifiers, written by Terraform from
a variable. The bot reads it, together with the token and the webhook secret, in a
single `GetParameters` call on every message. Private chats only.

**Rationale**: FR-020 requires an allowlist that is configuration, not code, and the
spec's acceptance scenario requires a change to govern the next message. Reading per
invocation costs one call and about ten milliseconds, and removes every staleness
question a cold-start cache would raise. The same call already has to fetch the token,
so the allowlist is free in latency terms.

**Alternatives considered**:

- *Environment variable*: one fewer service, but a change requires a function
  configuration update and every warm instance keeps the old list until it recycles.
  Rejected for the staleness, not the cost.
- *A DynamoDB table of approved accounts*: more moving parts for a list that changes
  twice a year. Rejected.
- *Public access with no allowlist*: forbidden by the constitution's acceptable-use
  constraint. Rejected.

---

## D-04: The worker announces completion on SNS, the bot delivers it

**Decision**: one SNS topic, `ytdl-<env>-job-events`. The worker publishes
`{"version":1,"event":"job_completed"|"job_failed","job_id":"..."}` only when the job
carries a Telegram `chat_id` and its outcome is terminal. The bot Lambda subscribes and
posts the message.

**Rationale**: the worker is the only component that knows a job has finished, and it
must not hold the bot token or speak to a chat API. SNS decouples the two, is already a
familiar shape in this stack, and its at-least-once delivery is acceptable for a
message that contains a link. Terminal-only publishing avoids telling the user "failed"
for a job that the queue is about to retry.

**Alternatives considered**:

- *Worker calls Telegram directly*: puts a third-party secret inside the 3 GB media
  function and makes a media job depend on a chat API. Rejected.
- *Bot polls the job on a schedule*: generates invocations per in-flight job and adds
  latency. Rejected.
- *Delayed SQS message per job*: still polling, with extra queue management. Rejected.
- *EventBridge instead of SNS*: equivalent technically; SNS is already in the stack and
  the subscription model is the exact fit. Rejected for symmetry.

---

## D-05: Ownership uses a sparse GSI, not a second table

**Decision**: add `owner_key` and `created_at` to the existing job items for bot jobs,
and one global secondary index `owner-created-index` with hash `owner_key` and range
`created_at`, projection `ALL`. Interactions (one item per handled update) use the same
index with a different owner prefix: `tg:<id>#job` and `tg:<id>#interaction`.

**Rationale**: the base table was created with `pk` only. Adding a sort key means
replacing the table, which is destructive, and a second table would duplicate every
fact about media that already lives on the job record. A GSI can be added in place and
is sparse: only bot items carry `owner_key`, so terminal jobs are never projected and
cost nothing. One index serves both ordered listing and ordered history because the
kind is part of the hash key.

**Cost of the choice**: GSI reads are eventually consistent. A listing issued within
about a second of a delete can still show the deleted row. The bot never derives a
deletion from the index: `/delete` and `/empty` write the marker and report from the
write's result, and the next listing catches up. Recorded in the post-design re-check.

**Alternatives considered**:

- *A second table keyed by owner and time*: strongly consistent, but two sources of
  truth for `media_expires_at`, `media_key` and state, and a delete path that can
  diverge from the job record. Rejected.
- *Recreating the table with a sort key*: a destructive migration for a personal tool,
  and `terraform destroy` on a live stack is not a migration. Rejected.
- *Scanning the table and filtering by owner*: reads every job in the account and gets
  worse with every terminal request. Rejected.

---

## D-06: Idempotency is the Telegram `update_id`, written inside the job transaction

**Decision**: for a request that creates a job, the same `TransactWriteItems` writes the
job, increments the rate window, and puts an `UPD#<update_id>` item with
`attribute_not_exists(pk)`. A redelivery cancels the transaction, and nothing is
accepted, charged, or downloaded. Read-only and already-idempotent commands
(`/list`, `/delete`, `/empty`, `/history`, `/info`, `/help`) need no dedupe record.

**Rationale**: FR-023 fails exactly when the duplicate is detected too late. Putting the
marker in the same atomic write is the only version with no window between "seen" and
"done". A failure before the transaction leaves no marker, so Telegram's retry is
useful; a failure after it (for example, the acknowledgement send) is swallowed on
retry, which is the lesser evil: the completion message still arrives.

**Alternatives considered**:

- *A dedupe write before the work*: a transient failure after the write makes the retry
  a no-op, and the user loses the request. Rejected.
- *`message_id` as the key*: only unique within a chat, and not stable across
  redeliveries of the same message. Rejected.
- *Deduplicating every command*: `/delete` and `/empty` are naturally idempotent, and a
  marker adds a write per command for no behavioural gain. Rejected.

---

## D-07: Tiers map to ceilings, `240p` is added, and a missing tier falls back

**Decision**: `low` to 240, `medium` to 480, `high` to 720, `veryhigh` to 1080, `mp3`
to audio only. Add `240p` to the accepted quality labels. `formats.select` gains a hard
ceiling: if nothing exists at or below the requested ceiling, it takes the lowest
rendition at or below the service maximum and reports what was produced.

**Rationale**: the help text fixes the ceilings. A 240p value does not exist today, and
`formats.select` currently refuses when nothing fits, which contradicts FR-004. The hard
ceiling keeps the cost guard intact: the fallback can never exceed `max_height`.

**Effect on the terminal path**: an API request for `480p` against a source that only
offers 720p now returns a 720p file, where it used to fail with `unsupported_media`.
The configured ceiling remains absolute. `001/contracts/api.md` is updated to say so.

**Alternatives considered**:

- *A bot-only fallback flag*: two selection behaviours to test and explain. Rejected.
- *Refusing when the tier is absent*: directly contradicts FR-004. Rejected.
- *Adding `144p` too*: nothing asks for it, and `240p` resolves to 144 where that is all
  the source has. Rejected as speculative.

---

## D-08: Audio is the best audio stream, converted to MP3 at 192 kbps

**Decision**: select the best audio-only stream, download it alone, and convert with
the pinned `ffmpeg` using `libmp3lame` at 192 kbps CBR. Probe the result with ffprobe
and record `produced_quality = "mp3"`, `media_kind = "audio"`.

**Rationale**: converting with ffmpeg directly keeps the existing pipeline shape
(download, verify, upload) and keeps the bitrate deterministic. The pinned static build
compiles `libmp3lame` in, so no image change is needed. A 60-minute audio track encodes
in a couple of minutes, far inside the 900-second ceiling, and audio downloads are much
smaller than video ones, so `/tmp` is comfortable.

**The one assumption this breaks**: 001's D-08 justified a single invocation with
copy-only processing. MP3 is a re-encode. The same duration cap bounds it, and the
deadline guard still aborts cleanly, but it is recorded in Complexity Tracking rather
than left implicit.

**Alternatives considered**:

- *`yt-dlp -x --audio-format mp3`*: moves the encoder choice and quality into a
  postprocessor, which is harder to bound and to test offline. Rejected.
- *Copying the source audio into an `.m4a`*: not MP3, and the help text promises MP3.
  Rejected.
- *VBR `-q:a 0`*: better quality per byte, but less predictable file size for a service
  that enforces a byte cap. Rejected.

---

## D-09: Replies are plain text, split on line boundaries

**Decision**: no `parse_mode`. Every reply is plain text. Long replies are split into
pages of at most 3,500 characters on line boundaries and sent in order. `/list` stops at
200 items and states how many were not shown.

**Rationale**: video titles are attacker-controlled from the bot's point of view. Plain
text means no escaping, no markup injection, and an exact character count against
Telegram's 4,096 limit. 3,500 leaves room for the auto-link entity Telegram adds around
the download URL.

**Alternatives considered**:

- *Markdown or HTML*: one unescaped title breaks the message or formats it as a link.
  Rejected.
- *One message per item*: hits the one-message-per-second guidance and floods the chat.
  Rejected.

---

## D-10: The Telegram client is stdlib, with an explicit retry policy

**Decision**: `urllib.request` with a 10-second timeout, JSON bodies, no in-process
retry loop. A 400 or 403 is logged and dropped; 429, 5xx and network failures raise. On
the completion path a raise lets SNS retry; on the webhook path it produces a 500, which
makes Telegram retry the update.

**Rationale**: `boto3` is the only current dependency, and one endpoint family does not
justify another. The policy distinguishes "will never work" from "try again", which is
the same distinction the worker already makes for upstream failures.

**Alternatives considered**:

- *`requests` or `httpx`*: nicer API, new pinned dependency, new supply-chain surface.
  Rejected.
- *Retrying inside the invocation*: burns the 15-second timeout and delays the failure
  the platform is better placed to retry. Rejected.

---

## D-11: One new function, reusing the `-api` image

**Decision**: `ytdl-<env>-bot`, 512 MB, 15-second timeout, reserved concurrency 5, image
`<repo>:<tag>-api`, command `ytdl.bot.handler.handler`. Two images, three functions.

**Rationale**: the bot needs neither ffmpeg nor yt-dlp, and the api image already
contains every module in `src/`. Both functions are built from one commit, so they move
together anyway. A third image would add a build and a push for no size benefit.

**Alternatives considered**:

- *A third Docker target*: a smaller image, but the api image is already the lean one.
  Rejected.
- *Folding the bot into the api function*: one reserved-concurrency pool shared by a
  token-gated API and a public webhook, and one log group with two trust levels.
  Rejected.

---

## D-12: Webhook health is a scheduled `getWebhookInfo`, not an assumption

**Decision**: an EventBridge rule invokes the bot function hourly with
`{"action":"health"}`. The handler calls `getWebhookInfo` and emits an Embedded Metric
Format line: `WebhookHealthy = 1` when the URL matches the configured webhook and no
delivery error was recorded in the last hour. A CloudWatch alarm fires when the metric
is below 1 for two consecutive hours.

**Rationale**: the spec requires a revoked webhook or token to be visible in the
service's own monitoring, and a silent bot is otherwise indistinguishable from an idle
one. EMF needs no `cloudwatch:PutMetricData` permission, and the check sends no chat
traffic, so it cannot be mistaken for load generation.

**Alternatives considered**:

- *No check*: the first symptom is "the bot stopped answering", discovered by the
  operator, not by monitoring. Rejected.
- *CloudWatch Synthetics canary*: more expensive than the whole bot. Rejected.
- *A separate health function*: more resources for one API call. Rejected.

---

## D-13: The yt-dlp version has one source

**Decision**: `app/ytdlp-version.txt`. The Dockerfile installs
`yt-dlp[default]==$(cat ...)` and copies the file into both image targets. Terraform
reads the same file into the `YTDLP_VERSION` environment variable. `/info` reports it.

**Rationale**: `/info` must report the version actually running. A separate Terraform
variable would be a second source that can drift; `pip show` is unavailable in the api
image, which has no yt-dlp. One file read by both the build and the plan keeps them
identical by construction.

**Alternatives considered**:

- *A Terraform variable*: drifts the moment someone bumps the Dockerfile. Rejected.
- *Reading the installed distribution at runtime*: absent from the api image. Rejected.

---

## D-14: Cost

Added resources, priced from the AWS price list at plan time. The estimate is a pricing
model, not a load test: nothing in this feature generates traffic to reach a ceiling
(constitution principle V).

| Item | Monthly assumption | Price | Monthly |
|------|--------------------|-------|---------|
| HTTP API requests | 1,000 | $1.00 per million | $0.001 |
| Bot Lambda | 1,700 × 0.5 s × 512 MB | inside the free tier | $0.00 |
| SNS publishes | 500 | $0.50 per million | $0.0003 |
| SSM `GetParameters` | 1,730 | $0.05 per 10,000 | $0.01 |
| DynamoDB reads and writes | a few thousand | on-demand, cents | $0.01 |
| EventBridge scheduled rule | 730 invocations | free tier | $0.00 |
| Two CloudWatch alarms | 2 | $0.10 per alarm | $0.20 |
| CloudWatch Logs | 50 MB | $0.50 per GB | $0.03 |
| **Added total** | | | **about $0.25** |

The two alarms dominate. Expected stack total moves from about $1.40 to about $1.65 per
month against the documented $10 ceiling. The ceiling stays a guardrail: it is read from
billing data, never approached on purpose.

---

## D-15: Deliberately not built

| Not built | Why |
|-----------|-----|
| Inline mode, callback buttons, message editing | Nothing in the spec asks for them, and each adds an update type and a handler path. |
| Group or channel use | The constitution and the spec both scope this to a private, personal tool. |
| Sign-up, invitations, self-service approval | The allowlist is operator configuration; an invitation flow is a public-service feature. |
| An admin command to edit the allowlist | The list is Terraform and SSM configuration, which keeps every access change reviewable and reversible. |
| Sending media through the chat | FR-009 forbids it, and Telegram's 50 MB ceiling would truncate real videos. |
| A storage quota per account | The hourly rate limit already bounds creation; a quota adds a counter with no requirement behind it. |
| A runtime command to change the quality ceiling | It is a cost guard; moving it belongs in a reviewed apply, not a chat message. |
| A webhook path secret | The `secret_token` header is stronger than an unguessable path, and the path stays readable in logs. |
| Localization | The fixed help text is English, and the spec says English only. |

---

## Resolved: everything the Technical Context asked for

| Question | Answer |
|----------|--------|
| Transport | Webhook on a dedicated HTTP API (D-01) |
| Registration and teardown | `terraform_data` plus one reviewed script (D-02) |
| Who may use it | SSM allowlist, private chats only (D-03) |
| How the result reaches the chat | SNS job events, bot consumer (D-04) |
| Where ownership lives | Sparse GSI on the existing table (D-05) |
| Duplicate deliveries | `update_id` inside the job transaction (D-06) |
| Resolution vocabulary | Five tiers, `240p` added, bounded fallback (D-07) |
| MP3 | Best audio stream, ffmpeg `libmp3lame` 192 kbps (D-08) |
| Message limits | Plain text, 3,500-character pages (D-09) |
| HTTP client | stdlib, typed error policy (D-10) |
| Function and image layout | One function, `-api` image (D-11) |
| Silent failures | Hourly `getWebhookInfo`, EMF metric, alarm (D-12) |
| Version reporting | One file read by build and plan (D-13) |
| Cost | About $0.25 per month added (D-14) |
| Out of scope | Table in D-15 |
