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

All alarms publish to one SNS topic, `ytdl-<env>-alarms`. Each row names the first action,
not the whole investigation.

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

Observed at validation: not yet recorded. The three figures in this paragraph (worker
p95 duration, actual monthly cost, and a delivered test alarm) require a deployed stack
with AWS credentials, and were not captured in the session that built this service. Run
`make test-e2e`, then read the CloudWatch metrics for the `worker` function and the
Billing console, and record the numbers here before relying on the alarms.

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
