# Runbook

Operational guide for the YouTube download service. It covers what each failure means,
what to do for each alarm, how to rotate the access token, and how to read a log line.

Design references: `specs/001-youtube-video-download/contracts/api.md` (response shapes
and failure categories), `specs/001-youtube-video-download/research.md` (the decisions
behind the limits), `specs/001-youtube-video-download/quickstart.md` (the end-to-end
validation).

---

## 1. What the service does, in one paragraph

A request arrives at the HTTP API with a YouTube URL and a shared bearer token. The API
validates the URL, counts the request against the hourly window, writes a job record, and
puts a pointer on the queue. The worker claims the job, reads metadata, refuses anything
over the duration or size cap, downloads with a pinned yt-dlp, remuxes to MP4 with stream
copy (no re-encoding), uploads to private S3, and marks the job complete. The download
link is a presigned S3 URL valid for 60 minutes. Media expires after 7 days.

All resources live in **eu-west-3 (Paris)**: the bucket, the table, the queues, all three
functions, both HTTP APIs and the ECR repository. The only exception is the two budget
alarms, which read `AWS/Billing` in `us-east-1` because that metric is published nowhere
else. `AWS_REGION` defaults to `eu-west-3` in the `Makefile` so every CLI check targets
the same region as the stack.

---

## 2. Failure catalogue

Every failure carries one of five categories and a `retryable` flag. The message is
written once, in `app/src/ytdl/errors.py`, and never contains a URL, a token, or a stack
trace.

| Category | `retryable` | Typical cause | Operator action |
|----------|-------------|---------------|-----------------|
| `invalid_input` | false | Not a single-video YouTube URL: foreign host, malformed id, playlist, channel, live path | Fix the link. Rejection happens before any media transfer and costs nothing. |
| `unsupported_media` | false | Private, deleted, members-only, age-restricted, region-locked, DRM-protected, or a live broadcast | Nothing to fix in the service. The content is out of scope; do not resubmit. |
| `limit_exceeded` | false | Duration above 60 minutes, or estimated size above 4 GB | Resubmitting the same link gives the same answer. These caps are configuration values (`MAX_DURATION_SECONDS`, `MAX_OUTPUT_BYTES`). |
| `upstream_problem` | true or false | Throttling or a network reset (retryable); a "not a bot" challenge or a hard 403 (not retryable) | Retryable: wait a few minutes and submit again. Not retryable: the egress address is being refused. See section 6. |
| `internal_error` | true | Storage failure, deadline exceeded at 780 s, unexpected exception | Read the worker log for the `job_id`. Resubmit once. If it repeats, treat it as a defect. |

Two rules that follow from this table:

- Never resubmit an `unsupported_media` or `limit_exceeded` job. The answer cannot change.
- A `upstream_problem` marked not retryable is a configuration problem, not a bug. Sections
  6 and 7 are the remedy.

---

## 3. Alarm response

Two operational alarms publish to `ytdl-<env>-alarms` in Paris. The two budget alarms
publish to `ytdl-<env>-alarms-us-east-1`, because a CloudWatch alarm can only notify a
topic in its own region and `AWS/Billing` exists only in `us-east-1`. Both topics need a
confirmed email subscription; the same address is used for each when `alarm_email` is set.

Five Lambda and API Gateway alarms (`api-5xx`, `worker-errors`, `worker-throttles`,
`worker-duration-p95`, `bot-errors`) were deleted on 2026-11-17. The compute moved to the
Raspberry Pi, so those resources no longer receive traffic and the alarms could only stay
green; a green alarm that cannot fire is worse than no alarm. Process failures now surface
in the dead-letter queue, in the webhook health check, and in the container logs.

| Alarm | Meaning | First action |
|-------|---------|--------------|
| DLQ depth above zero | A message was received twice and abandoned | Read the message, take its `job_id`, read that job's `failure_category`. Any dead letter is a defect. |
| Bot webhook health below 1 | The webhook is gone or the token was revoked | `make bot-health ENV=<env>`, then re-register, then rotate the token if Telegram answers 401. Section 11. |
| Estimated charges above $5 | Half the monthly ceiling | Check average output size and retention overlap. Storage is the dominant driver, not requests. |
| Estimated charges above $10 | Ceiling reached | Stop submitting. Inspect Cost Explorer, decide on a change, then resubmit. |

Budget context: the documented profile is 100 videos per month at 15 minutes each with
7-day retention, expected at roughly $1.40 (research D-02). The $10 ceiling is deliberate
headroom, not a restatement of the estimate.

This ceiling is a guardrail, not a target. No drill or test generates load to approach
it (constitution principle V): read actual spend, never induce it.

Observed at validation:

- Worker duration: on the Pi, a 19-second 240p video (7.8 MB) completed in **27 s** end to
  end, download included. The Lambda figures (14.9 s for a 10.6-minute 1080p video) are
  recorded in `specs/001-youtube-video-download/` and no longer describe production.
- Cost: Cost Explorer reported **$0.00** for the day of the Lambda validation, which is
  expected for a few minutes of use and a lagging daily metric. The $10 ceiling has not
  been exercised.
- Alarm delivery: the two topics have **no email subscriber** while `alarm_email` is empty,
  so delivery was not tested. Set the address and confirm the subscription email before
  relying on the alarms.

A brand-new account can show the two budget alarms in `INSUFFICIENT_DATA` for a day,
because `AWS/Billing EstimatedCharges` publishes only once billing data exists and only in
`us-east-1`. That is not a broken alarm.

---

## 4. Reading a log line

All three functions emit one JSON object per line on stdout. CloudWatch Logs Insights
query for one job:

```text
fields @timestamp, event, state, failure_category, retryable, processing_ms, media_size_bytes
| filter job_id = "<job_id>"
| sort @timestamp asc
```

The field set is a fixed allowlist. If a field is missing, it was not allowlisted, not
lost. Fields that must never appear, and are tested for: the bearer token, an
`Authorization` header, a cookie value, or a full URL with a query string.

The same lines carry Embedded Metric Format metrics in namespace `Ytdl/<env>` with
dimensions `Environment` and `Function`, so no extra `PutMetricData` call is made.

---

## 5. Rotate the access token

The token lives in SSM as a `SecureString` and is generated by Terraform. Rotation is a
Terraform change, not a console edit (constitution principle I).

```bash
cd infra
terraform taint random_password.api_token
terraform apply -var-file=env/dev.tfvars -var image_tag=<current-tag>
```

Then retrieve the new value and update the caller:

```bash
aws ssm get-parameter --name /ytdl/dev/api-token --with-decryption \
  --query Parameter.Value --output text
```

Cold-start functions read the parameter once and cache it, so a rotation takes effect as
instances recycle. In practice the next request after the old instances expire uses the
new token.

---

## 6. YouTube refuses the egress address

Symptom: `upstream_problem` with `retryable: false`, and a message naming a bot check or a
403 from the media host. This is the largest external risk in the design (research D-14).
A PO token does not fix IP reputation.

The address depends on the target. Production runs on the Raspberry Pi, so the egress is a
residential connection: better regarded than a datacenter range, but still shared with
every other service behind the same public address, and a CGNAT pool can be scored like
one. On the Lambda target the egress is a datacenter range, which YouTube scores worse.

Remedies, in order of effort:

1. **Cookies file.** Export cookies from a signed-in browser session in Netscape format,
   upload to `s3://<media-bucket>/config/cookies.txt`, and set `ytdlp_cookies_s3_uri` in the
   environment tfvars. The worker reads it and passes `--cookies`.
2. **Residential proxy.** Store the proxy URL in SSM and set `ytdlp_proxy_param` to that
   parameter name. The worker passes `--proxy`.
3. **Player client override.** Set `PLAYER_CLIENT` to a client that works from your region.

All three are empty by default and add no flags when unset. Treat the cookies file as a
credential: it is never logged, and it lives under a prefix only the compute may read.

**Cookies must be fresh.** An export whose session cookies have expired looks present but
authenticates nothing, and the hard block remains. Check the file date before blaming the
network: in the first full deployment (2026-11-16) an export with 136 of 160 cookies past
their expiry still produced a non-retryable block, and forcing the `mweb` or `ios` player
client did not change it. Re-export from a signed-in browser session, upload over
`config/cookies.txt`, then submit again. The worker reads the file once when its process
starts, so restarting the worker container is what picks up a new copy on the host.

---

## 7. Retention and expiry

- S3 lifecycle expires `output/` after 7 days, counting whole days and applying at the next
  midnight UTC. An object therefore disappears between day 7 and day 8.
- The service never *offers* a file past `media_expires_at` (creation + 7 days). The
  `HeadObject` check covers the gap, so a late lifecycle run produces `expired`, not a dead
  link.
- Job records carry a DynamoDB TTL 30 days after media expiry, so the audit trail outlives
  the file without accumulating forever.
- Verification: `make verify-retention` fails if any object under `output/` is older than
  8 days.

---

## 8. What is deliberately not built

Recorded so nobody rediscovers it as a bug: no deduplication (two submissions are two
jobs), no audio-only or subtitle variants, no playlists or live streams, no re-encoding, no
web UI, no multi-tenant identity, no PO token provider service. See research D-19.

---

## 9. Switching between dev and prod

The root module uses one working directory and two backends. `terraform init` remembers the
last backend it saw, so **every** manual Terraform command must be preceded by an init for
the environment you mean:

```bash
terraform -chdir=infra init -reconfigure -backend-config=backend/dev.backend \
  -backend-config=bucket=ytdl-tfstate-<account-id>
```

`make deploy` and `make destroy` do this for you and refuse to run without `ENV`.

**The two environments are not symmetric any more.** `dev` deploys the Lambda target;
`prod` keeps the same Lambda resources but serves traffic from the Raspberry Pi
(section 12). `make deploy ENV=prod` applies Terraform and nothing else: it neither builds
nor reloads the host image, which travels separately with `make pi-ship`. A change that
must reach production therefore takes an apply and a host reload, and a teardown takes the
reverse.

This is not a formality. An apply with the wrong backend does not fail: it destroys the
other environment's resources and adopts the new names into the wrong state file. That
happened once during the first bring-up on 2026-11-16, before `-reconfigure` was made
unconditional, and it destroyed the dev stack. If it happens again, the recovery is to
destroy the resources now recorded in the wrong state, then re-initialise each environment
with `-reconfigure` and apply it fresh.

`make outputs ENV=<env>` prints an environment's outputs after re-initialising, so a stale
backend pointer cannot silently answer for the wrong environment.

**Operator-only values live in `.env`.** The alarm address is not in the repository, and
neither is any cookies location. Copy `.env.example` to `.env`, fill it in, and `make`
exports it: Terraform reads the `TF_VAR_*` names from there, and `make deploy ENV=prod`
refuses to run without `TF_VAR_alarm_email`, so an apply cannot silently delete the
subscriptions. `-var-file` outranks the environment, so a value also written in a
`.tfvars` would win; keep these two out of the `.tfvars` files.

---

## 10. Monthly cost review

Once a month, read the bill; never induce it.

1. Read the month to date:
   `aws ce get-cost-and-usage --region us-east-1 --time-period Start=<first>,End=<today> --granularity MONTHLY --metrics UnblendedCost`.
2. Compare with the research D-02 table, which expects roughly $1.40 for the documented
   profile (100 videos, 15 minutes each, 7-day retention). Since 2026-11-17 the worker
   compute runs on the host, so the Lambda duration and request lines in that table no
   longer apply to production: what remains is S3 storage and requests, the DynamoDB
   table, the queue, and CloudWatch. Expect less than the model, not more.
3. Above $5, check average `media_size_bytes` and retention overlap before anything else.
   Above $10 the ceiling alarm fires: stop submissions until the cause is understood.
4. Record the figure and the date at the bottom of this section.

| Date | Spend | Note |
|------|-------|------|
| 2026-11-16 | $0.00 | validation day, partial day, Cost Explorer still settling |

Do not generate load, extra submissions, or a synthetic month to "test" the ceiling.
Constitution principle V makes the ceiling a guardrail, not a test target: it is
justified by arithmetic and monitored from billing data.

---

## 11. The Telegram bot

The bot is a second front door, not a second service. In production it runs on the
Raspberry Pi (section 12), not on Lambda: the HTTP front end answers the webhook, and the
same image's worker service turns a terminal job into a chat message over the loopback.
Reading section 12 first is the fast path when the bot is the thing that looks broken.

The bot calls the same submission path, worker and presigner as the terminal API. It reads
the token, the webhook secret and the allowlist from SSM in one call per webhook delivery,
so no secret is an environment variable.

The command menu a client shows when `/` is typed is registered with `setMyCommands` by
`terraform_data.telegram_commands`, from the list in `infra/locals.tf`. It is Telegram-side
state, so an apply refreshes it and a destroy deliberately leaves it behind. Editing a
description re-registers it on the next apply; the menu and the `/help` text are changed
together, since nothing compares them automatically.

Its logs are in `/aws/lambda/ytdl-<env>-bot` on a Lambda deployment, and in
`sudo docker logs ytdl` on the Raspberry Pi. Every webhook delivery writes one line with
`event = "update"` and an `outcome`: `accepted`, `reissued`, `in_progress`,
`rate_limited`, `refused`, `command:<name>`, `format_hint`, `duplicate`, `malformed`,
`unauthenticated`, `ignored`, or `failed`. A delivery that failed internally also writes
`event = "update_failed"` with the exception's class name, and the hourly check writes one
`webhook_health` line carrying the embedded metric.

**The sentences a user can receive.** A failure arrives as the service's own fixed
sentence, followed by `Send the request again.` only when the job was marked retryable.
The strings live in `app/src/ytdl/errors.py` and `app/src/ytdl/bot/messages.py`.

| Sentence | Cause | Operator action |
|----------|-------|-----------------|
| The submitted link is not a supported single YouTube video. | Wrong host, malformed id, playlist, channel, or live link. Refused before any job, so nothing was stored or charged. | Fix the link. Nothing to repair in the service. |
| The source refused this video. It may be private, removed, or unavailable in this region. | `unsupported_media`, not retryable. | Do not send it again. The content is out of scope, as in section 2. |
| The video is longer or larger than this service allows. | `limit_exceeded`: over `MAX_DURATION_SECONDS` or `MAX_OUTPUT_BYTES`. | Resending gives the same answer. These are configuration values. |
| The source was temporarily unreachable; the same request may succeed later. Send the request again. | `upstream_problem`, retryable: throttling, a reset, a timeout. | Wait a few minutes, then resend. A retry costs one submission. |
| The source blocked this request; check the service configuration before submitting again. | `upstream_problem`, not retryable: the known egress block. | Section 6 is the remedy. Do not resend before changing the configuration. |
| The job could not be completed because of an internal error; try again later. Send the request again. | `internal_error`: storage, the 780 s deadline, an unexpected exception. | Read the worker log for the `job_id`. One resend is reasonable; a repeat is a defect. |
| The stored file has expired; submit the video link again. | The job finished, but its file was already gone when the completion message was built. | Nothing to repair. The same request starts a new download. |

Three more sentences are not failures and need no repair: `This bot is private.` for an
account outside the allowlist (no job, no charge, and no hint of what the bot does),
`This bot only works in a private chat.` for a group, and `Hourly limit reached. Capacity
returns at <time> UTC.` for the shared rate window, which creates no job.

An expired link is not the expired-result row. A link that lived its 60 minutes is
replaced from the stored file when the operator sends the same request again, with no
second download; the expired-result sentence means the file itself was gone.

**Bot failures without an alarm.** The `bot-errors` alarm was deleted with the other
Lambda and API Gateway alarms (section 3). A delivery that fails internally is still
caught and answered `500`, logged as `update_failed` with the exception's class name, and
Telegram retries it. Start from `sudo docker logs ytdl` and group by `event` and `outcome`;
`update_failed` points at the webhook path, `health_failed` at the hourly check. Then run
`make bot-health ENV=<env>`, which calls Telegram once and prints the registration,
`pending_update_count`, `max_connections` and `allowed_updates`. If that shows no URL at
all, the registration or the token is at fault: follow the webhook-health response below.
If the job record carries a `failure_category`, section 2 applies instead; the bot only
relays that sentence.

**Alarm `ytdl-<env>-bot-webhook-health`.** Namespace `YTDL/Bot`, metric
`WebhookHealthy`, dimension `Environment`, statistic `Minimum`, one-hour periods, alarm
when the value is below 1 for two consecutive periods. The hourly `{"action": "health"}`
invocation emits `1` when `getWebhookInfo` reports exactly the environment's webhook URL
and no delivery error in the previous hour, and `0` otherwise. Missing data is treated as
breaching on purpose: a check that stopped running is itself the failure, so the alarm can
fire without Telegram being at fault.

Response, in order:

1. `make bot-health ENV=<env>`. On a healthy bot the `url` line equals
   `terraform -chdir=infra output -raw bot_webhook_url` and `allowed_updates` is
   `['message']`.

A freshly applied stack can sit in `ALARM` for up to an hour, because the alarm
treats missing data as breaching and the first datapoint only arrives with the
first hourly check. Run the check by hand to clear it immediately, and read
`StateReason`: if it names missing datapoints rather than a `0`, the check simply
had not run yet. An `OK` state after that is the proof, not a formality.
2. When `url` is empty or `None`, read Telegram's full answer, which carries the reason:

   ```bash
   TG_TOKEN=$(make -s bot-token ENV=<env>)
   curl -s "https://api.telegram.org/bot$TG_TOKEN/getWebhookInfo" | python3 -m json.tool
   ```

   An empty `url` means the registration is gone: re-register (below). An `error_code`
   of `401` means the token was revoked: rotate it, then re-register. A non-empty `url`
   with a recent `last_error_message` means Telegram cannot deliver to that address.
3. No datapoint at all, rather than a `0`, points at the hourly rule
   `ytdl-<env>-bot-health`: check that it is enabled and that its Lambda permission
   survives.

**Re-register the webhook.** The registration is owned by
`terraform_data.telegram_webhook`, so the reviewed way to recreate it is to let Terraform
replace that resource. The destroy phase removes the old registration and the create phase
sets the new one, both through `infra/scripts/telegram-webhook.sh`, which reads the token
from SSM at run time.

```bash
make outputs ENV=<env> >/dev/null   # re-initialise that backend first
terraform -chdir=infra apply -replace=terraform_data.telegram_webhook \
  -var-file=env/<env>.tfvars -var image_tag=<current-tag>
```

`TF_VAR_telegram_bot_token` must be exported for that apply, as for any other. Confirm
with `make bot-health ENV=<env>`: the `url` line is back.

**Rotate the bot token.** Rotate when the token was revoked in BotFather, or on suspicion
that it leaked. A revoked token makes every call answer `401`, so the bot cannot reply and
the hourly check reports `0`. The parameter name is the `telegram_token_parameter_name`
output of `infra/outputs.tf`.

```bash
make outputs ENV=<env> >/dev/null
NAME=$(terraform -chdir=infra output -raw telegram_token_parameter_name)
aws ssm put-parameter --name "$NAME" --type SecureString --overwrite --value '<new token>'
```

Then re-register the webhook, using the command above. The order matters: the script
authenticates with whatever value SSM holds, so the write comes first. The bot itself reads
the token per message, so no image is rebuilt and no deployment is needed for it to start
using the new value.

Terraform still holds the previous token from your last `TF_VAR_telegram_bot_token`, so
the next apply would write it back. Export the new value in the same shell before that
apply. Doing the whole rotation that way, export then apply, rewrites the same parameter.

**Change the allowlist.** The list is one SSM parameter, written from
`TF_VAR_telegram_approved_user_ids` and read on every message, so a change governs the
next message with no cache to wait out.

```bash
export TF_VAR_telegram_approved_user_ids='["111111111","222222222"]'
make outputs ENV=<env> >/dev/null
terraform -chdir=infra apply -var-file=env/<env>.tfvars -var image_tag=<current-tag>
```

That apply rewrites one parameter and nothing else: no image is rebuilt, no function is
redeployed. A removed account is refused on its next message and sees none of its earlier
items, because every listing is scoped to the sender. An empty list approves nobody, which
is the safe default.

**Remove the webhook: two steps.** A destroy-time provisioner only runs while its resource
block is still in the configuration, so deleting the block outright skips deregistration
and leaves Telegram posting to a dead URL. Add a count first, apply, then remove the block.

```hcl
resource "terraform_data" "telegram_webhook" {
  count = 0
  # ... unchanged
}
```

An `apply` with `count = 0` removes the resource and its destroy provisioner calls
`deleteWebhook`. Then delete the block and apply again; nothing is left in state, so the
second apply changes nothing. The same resource is not referenced elsewhere, so the
`count` needs no other edit.

A full `make destroy ENV=<env>` does not need those steps. The webhook resource depends on
the three SSM parameters, so its deregistration runs before they are deleted and the script
still has a token to authenticate with. The proof is Telegram's own answer, so read the
token before destroying, while the parameter still exists:

```bash
TG_TOKEN=$(make -s bot-token ENV=<env>)
make destroy ENV=<env>
curl -s "https://api.telegram.org/bot$TG_TOKEN/getWebhookInfo" | python3 -m json.tool
```

Expected: `"url": ""`. A non-empty URL means a registration survived, and the teardown is not
complete. If the deregistration step fails, the destroy stops on that provisioner rather
than finishing quietly.

## 12. Running on the Raspberry Pi

Production compute runs on `rasp`, a Raspberry Pi 4 (2 GB, Debian 13, arm64) behind the
existing Cloudflare tunnel. AWS keeps the state: S3 for media, DynamoDB for jobs, SQS for
the queue, SSM for every secret. Nothing is reimplemented, the same handlers run in one
Docker image with two roles.

| Piece | Where | Notes |
|---|---|---|
| HTTP front end (`/v1/*`, `/telegram/webhook`, `/internal/job-event`) | Pi, container `ytdl` | `127.0.0.1:8090`; Caddy block `http://ytdl.timcloud.fr`, TLS terminated at the Cloudflare edge |
| Queue consumer | Pi, container `ytdl-worker` | same image, polls the queue one message at a time |
| Media, jobs, queue, secrets | AWS | unchanged |
| API Gateway, bot Lambda, worker Lambda, SNS topic | deployed, dormant | `worker_lambda_enabled = false` removed the event source mapping; `bot_public_url` moved the webhook to the Pi |

The hourly health rule still invokes the (otherwise idle) bot Lambda. Its environment now
carries the Pi's webhook URL, so `ytdl-prod-bot-webhook-health` remains a real check on the
Pi's registration. The five alarms that watched the dormant API Gateway and Lambda
functions were deleted on 2026-11-17, so only `ytdl-prod-dlq-not-empty` and the webhook
health check remain in Paris, plus the two budget alarms in `us-east-1`. The DLQ alarm is
meaningful because the Pi worker uses the same queue.

**Paths on `rasp`.**

| Path | Contents |
|---|---|
| `/opt/serveur/compose.yaml` | services `ytdl` and `ytdl-worker`, in the `Serveur` stack |
| `/opt/serveur/ytdl/env` | every setting plus the IAM key, mode `600`, owner `tim` |
| `/mnt/jellyfin_medias/ytdl-scratch` | the worker's scratch space, on the USB disk, never the SD card |
| `/var/log/caddy/ytdl.log` | one JSON line per request, rotated at 10 MiB, five kept |
| `sudo docker logs ytdl` / `ytdl-worker` | the application's own structured lines |

The worker refuses to start unless `YTDL_WORK_DIR` is set and resolves to a filesystem other
than `/`. A missing variable, a missing directory or the same device writes one
`workdir_invalid` line with the reason and exits 1, so the container restarts instead of
quietly downloading onto the SD card. Since the check runs inside the container, "different
device" means different from the container's `/`, which is the overlay on the card.

**Deploy an update.** The image is built on an arm64 machine and travels as a stream: there
is no registry in the path.

```bash
make pi-ship IMAGE_TAG=<sha>            # build, then docker load on rasp
# set `image: ytdl-pi:<sha>` on both services of /opt/serveur/compose.yaml
ssh rasp 'cd /opt/serveur && sudo docker compose up -d ytdl ytdl-worker'
```

`docker compose up -d` replaces only a container whose image tag changed, so a tag that does
not change deploys nothing. The `ytdl` healthcheck calls `/healthz` every 30 s; `ytdl-worker`
has a 17-minute `stop_grace_period`, which lets a running job finish on `docker stop` before
SIGKILL. A job killed anyway is redelivered after the queue's 960 s visibility timeout, and
`claim_job` plus the completion pre-check make the retry safe.

**Credentials.** The Pi authenticates as IAM user `ytdl-prod-pi`, whose policy is the union
of what the three Lambda roles could do. The access key is created out of band on purpose:
a Terraform-managed key would sit in the state file. To rotate:

```bash
aws iam create-access-key --user-name ytdl-prod-pi      # write the pair straight to the host
ssh rasp 'sudo -e /opt/serveur/ytdl/env'                # replace the two AWS_* lines
ssh rasp 'cd /opt/serveur && sudo docker compose up -d --force-recreate ytdl ytdl-worker'
aws iam delete-access-key --user-name ytdl-prod-pi --access-key-id <old id>
```

**Disk is the binding constraint.** The SD card is 15 GB and the image is 874 MB. Jellyfin
refuses to start below 2 GiB free on `/`, and `serveur-update.sh` refuses to run below
2.25 GiB. After the deployment the host had 2.5 GiB free, and that headroom was bought by
moving a stale 1 GB `/var/swap` (a sparse leftover; zram is the active swap) and 600 MB of
old Warp remote-server binaries to `/mnt/jellyfin_medias/agent_backup/`. Before adding
another image, run `df -h /` and `sudo docker system df`; never `docker system prune -a`
blindly, it removes the rollback images the update script relies on.

**Failure modes.**

| Symptom | Check |
|---|---|
| Telegram gets no answer | `make bot-health ENV=prod`; `sudo docker compose ps ytdl`; `sudo docker logs ytdl --tail 50` |
| Webhook answers 500 | The log line carries `event = "request_failed"` and a class name; the body and headers are never logged. The alarm does not see these (the handler answers instead of raising) |
| A submission is accepted but never completes | `sudo docker logs ytdl-worker`, then the queue's visible count and the DLQ; a job whose `state` stays `processing` past 15 minutes is the sign of a dead worker |
| `NoSuchEntity`/`SignatureDoesNotMatch` on every request | The IAM key in the env file was rotated or deleted |
| Every chat message is refused | The allowlist parameter changed, or the message came from an account not on it |

**Do not** point the webhook at API Gateway again while `worker_lambda_enabled = false`;
that combination accepts jobs nobody consumes. The supported switch back is: set
`bot_public_url = ""`, set `worker_lambda_enabled = true`, apply, and stop the Pi services.
