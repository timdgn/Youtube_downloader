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

All resources live in **eu-west-3 (Paris)**: the bucket, the table, the queues, both
functions, the HTTP API and the ECR repository. The only exception is the two budget
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
| `upstream_problem` | true or false | Throttling or a network reset (retryable); a "not a bot" challenge or a hard 403 (not retryable) | Retryable: wait a few minutes and submit again. Not retryable: the Lambda egress address is being refused. See section 6. |
| `internal_error` | true | Storage failure, deadline exceeded at 780 s, unexpected exception | Read the worker log for the `job_id`. Resubmit once. If it repeats, treat it as a defect. |

Two rules that follow from this table:

- Never resubmit an `unsupported_media` or `limit_exceeded` job. The answer cannot change.
- A `upstream_problem` marked not retryable is a configuration problem, not a bug. Sections
  6 and 7 are the remedy.

---

## 3. Alarm response

Five operational alarms publish to `ytdl-<env>-alarms` in Paris. The two budget alarms
publish to `ytdl-<env>-alarms-us-east-1`, because a CloudWatch alarm can only notify a
topic in its own region and `AWS/Billing` exists only in `us-east-1`. Both topics need a
confirmed email subscription; the same address is used for each when `alarm_email` is set.

Each row names the first action, not the whole investigation.

| Alarm | Meaning | First action |
|-------|---------|--------------|
| API 5xx above zero | The API is failing requests | Read the API log for the failing `job_id`. Check DynamoDB and SQS permissions before touching code. |
| Worker errors above zero | Jobs are failing | Group the log by `failure_category`. A `upstream_problem` spike is YouTube, not the service. |
| Worker throttles above zero | Reserved concurrency of 2 is saturated | Expected during bursts. Investigate only if it persists when no jobs are queued. |
| Worker p95 duration above 600 s | Approaching the 900 s Lambda ceiling | Check average `media_size_bytes` and download rate. Raising memory is a last resort; the deadline guard aborts at 780 s. |
| DLQ depth above zero | A message was received twice and abandoned | Read the message, take its `job_id`, read that job's `failure_category`. Any dead letter is a defect. |
| Estimated charges above $5 | Half the monthly ceiling | Check average output size and retention overlap. Storage is the dominant driver, not requests. |
| Estimated charges above $10 | Ceiling reached | Stop submitting. Inspect Cost Explorer, decide on a change, then resubmit. |

Budget context: the documented profile is 100 videos per month at 15 minutes each with
7-day retention, expected at roughly $1.40 (research D-02). The $10 ceiling is deliberate
headroom, not a restatement of the estimate.

Observed at validation (2026-11-16, `eu-west-3`):

- Worker duration: a 10.6-minute 1080p video (269 MB) completed in **14.9 s** of worker
  time; a 19-second 240p video took **6.2 s** warm and **27.7 s** cold. The 600 s alarm
  has a very large margin because processing is a stream copy, not a re-encode.
- Cost: Cost Explorer reported **$0.00** for the day of validation, which is expected for
  a few minutes of use and a lagging daily metric. The $10 ceiling has not been exercised.
- Alarm delivery: the two topics have **no email subscriber** while `alarm_email` is empty,
  so delivery was not tested. Set the address and confirm the subscription email before
  relying on the alarms.

Two prerequisites for these signals:

- **Request logs.** HTTP API access logging needs no account-level CloudWatch role (unlike
  REST APIs). If request log lines never appear in `/aws/apigateway/<prefix>`, check that
  per-account API Gateway setting first. It is deliberately not managed by Terraform,
  because it is an account-wide toggle.
- **Billing metric.** `AWS/Billing EstimatedCharges` only publishes in `us-east-1` and
  only once billing data exists for the account. A brand-new account can show the two
  budget alarms in `INSUFFICIENT_DATA` for a day. That is not a broken alarm.

---

## 4. Reading a log line

Both functions emit one JSON object per line on stdout. CloudWatch Logs Insights query for
one job:

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

## 6. YouTube refuses the Lambda egress address

Symptom: `upstream_problem` with `retryable: false`, and a message naming a bot check or a
403 from the media host. This is the largest external risk in the design (research D-14).
AWS Lambda egress addresses are datacenter ranges that YouTube scores badly. A PO token
does not fix IP reputation.

Remedies, in order of effort:

1. **Cookies file.** Export cookies from a signed-in browser session in Netscape format,
   upload to `s3://<media-bucket>/config/cookies.txt`, and set `ytdlp_cookies_s3_uri` in the
   environment tfvars. The worker reads it and passes `--cookies`.
2. **Residential proxy.** Store the proxy URL in SSM and set `ytdlp_proxy_param` to that
   parameter name. The worker passes `--proxy`.
3. **Player client override.** Set `PLAYER_CLIENT` to a client that works from your region.

All three are empty by default and add no flags when unset. Treat the cookies file as a
credential: it is never logged, and it lives under a prefix only the worker role can read.

**Cookies must be fresh.** An export whose session cookies have expired looks present but
authenticates nothing, and the hard block remains. Check the file date before blaming the
network: in the first full deployment (2026-11-16) an export with 136 of 160 cookies past
their expiry still produced a non-retryable block, and forcing the `mweb` or `ios` player
client did not change it. Re-export from a signed-in browser session, upload over
`config/cookies.txt`, then submit again. The worker reads the file at cold start, so a new
job always picks up the new copy.

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

This is not a formality. An apply with the wrong backend does not fail: it destroys the
other environment's resources and adopts the new names into the wrong state file. That
happened once during the first bring-up on 2026-11-16, before `-reconfigure` was made
unconditional, and it destroyed the dev stack. If it happens again, the recovery is to
destroy the resources now recorded in the wrong state, then re-initialise each environment
with `-reconfigure` and apply it fresh.

`make outputs ENV=<env>` prints an environment's outputs after re-initialising, so a stale
backend pointer cannot silently answer for the wrong environment.
