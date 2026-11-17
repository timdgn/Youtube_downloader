# YouTube Download Service

A personal, single-operator service that takes a YouTube URL from your terminal or a
Telegram chat, downloads the video in the background, stores one finished
file privately for seven days, and returns a download link valid for 60 minutes.
The compute runs either on AWS Lambda or, in the production deployment, in Docker on a
Raspberry Pi; media, job records, the queue and the secrets stay in AWS either way.

Provisioning and removal are both Terraform. There is no console step, no bucket to empty
by hand.

**Acceptable use.** This is a personal tool. Use it only for content you are entitled to
download. It must not be used as a public service, to bypass DRM or paid access, or to
redistribute copyrighted material. See `app/THIRD-PARTY-NOTICES.md`.

---

## What you need

| Need | Detail |
|------|--------|
| AWS account | Permissions to create S3, DynamoDB, SQS, Lambda, ECR, IAM, API Gateway, SSM, CloudWatch and SNS |
| Credentials | An AWS profile exported in your shell. No keys in any file in this repo |
| Tooling | Terraform >= 1.10, AWS CLI v2, Docker with `buildx`, `make`, `python3.12`, `curl`, `jq` |
| Region | One region for everything: `eu-west-3`, Paris |

The account is expected to be empty, or at least free of resources named `ytdl-*`.

---

## First run, from an empty account

```bash
# 1. One-time: the bucket that holds Terraform state.
make bootstrap

# 2. Build, push and apply the dev environment.
make deploy ENV=dev

# 3. Read the API address and the access token.
export API=$(terraform -chdir=infra output -raw api_base_url)
export TOKEN=$(make -s token ENV=dev)
```

`make deploy` runs, in order: `terraform init` against `infra/backend/dev.backend`, a
repository-only apply (an image cannot be pushed to a repository that does not exist yet),
the two arm64 image builds with the tag from your current commit, and the full apply.

---

## Use it

**Submit a link.** Returns a job id immediately; nothing is downloaded while the request
is open.

```bash
curl -sS -X POST "$API/v1/jobs" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID","quality":"720p"}' | jq .
```

`quality` is optional: one of `360p`, `480p`, `720p`, `1080p`. Anything above 1080p is
capped, not rejected. Leave it out for the default ceiling.

**Check the job.** Poll until `completed`, then take `result.download_url`.

```bash
curl -sS "$API/v1/jobs/JOB_ID" -H "Authorization: Bearer $TOKEN" | jq .
```

**Fetch the file.** The link needs no credential beyond the URL.

```bash
curl -sS -o video.mp4 "$(curl -sS "$API/v1/jobs/JOB_ID" \
  -H "Authorization: Bearer $TOKEN" | jq -r .result.download_url)"
```

**Ask for a fresh link** when the 60-minute one has expired and the file is still stored
(within 7 days). This does not reprocess the video and does not count against the hourly
limit.

```bash
curl -sS -X POST "$API/v1/jobs/JOB_ID/link" -H "Authorization: Bearer $TOKEN" | jq .
```

---

## The chat front door

The bot answers in Telegram and calls the same submission path, worker and presigner as
the terminal, so both entry points produce the same job records and the same files.

**Create the bot.** In Telegram, open `@BotFather`, send `/newbot`, choose a display name
and a username ending in `bot`, then copy the token it returns. This is the one human step
in the whole deployment: Telegram has no API that creates a bot account, just as AWS has
no Terraform resource for an AWS account.

```bash
export TF_VAR_telegram_bot_token='123456789:AA...'
export TF_VAR_telegram_approved_user_ids='["<your numeric account id>"]'
```

The token is a Terraform variable because the apply writes it to an SSM `SecureString`.
It is never written to a file in this repo, never an environment variable on a function,
and never a log line.

**Approve the accounts.** An account id is a number, not a username; `@userinfobot`
reports yours, and the bot cannot tell you because it refuses unapproved accounts without
revealing anything. The list lives in SSM and is read on every message in a single
parameter call, so a change governs the next message: edit the variable and apply, which
rewrites one parameter, rebuilds no image and redeploys no code. An empty list approves
nobody, which is the safe default.

**Ask for a file.** One message: a YouTube link, optionally followed by one resolution
word. A word is a ceiling, not an exact quality, and the reply names the quality actually
produced.

| Word | Ceiling |
|------|---------|
| `low` | 240p |
| `medium` | 480p |
| `high` | 720p |
| `veryhigh` | 1080p |
| `mp3` | audio only, no ceiling |

A link with no word uses the configured default, `high` (720p), and the acknowledgement
says which tier was used.

**Follow the link before it expires.** A download link lives 60 minutes; the file lives
`RETENTION_DAYS`, 7 by default. When a link has expired, do not keep it: send the same
request again, and while the file is still stored the bot answers with a fresh link in
seconds and downloads nothing a second time. `/help` in the chat lists every command.

Two read-only checks, neither of which sends chat traffic:

```bash
make bot-health ENV=dev   # the registration as Telegram reports it
make bot-token ENV=dev    # the token value held in SSM
```

---

## What a failure tells you

Every failure carries one of five categories and a `retryable` flag.

| Category | Retry? | Meaning |
|----------|--------|---------|
| `invalid_input` | no | Not a single-video YouTube URL |
| `unsupported_media` | no | Private, deleted, members-only, age-restricted, region-locked, DRM, live |
| `limit_exceeded` | no | Over 60 minutes or over 4 GB |
| `upstream_problem` | sometimes | Source throttling (retry), or a hard block of the AWS egress address (do not retry, configure instead) |
| `internal_error` | yes | Storage, deadline, unexpected exception |

`docs/runbook.md` has the cause and next action for each, the alarm responses, and the
workaround for YouTube refusing the egress address.

---

## A second environment

```bash
make deploy ENV=prod
```

Same definition, different variable file and a different state key. The two stacks share
nothing, so destroying one cannot touch the other.

---

## Remove everything

```bash
make destroy ENV=prod
make destroy ENV=dev
make verify-destroy ENV=dev
```

`verify-destroy` checks seven services and fails if anything named `ytdl-` survives. The
state bucket is the single deliberate exception: it outlives the application so the next
deploy has somewhere to write. Remove it by hand only if you are done with the project:

```bash
terraform -chdir=infra/bootstrap destroy -var state_bucket=ytdl-tfstate-<account-id>
```

---

## Development

```bash
make install        # app/.venv with pinned dependencies
make test           # unit and contract suites, offline and deterministic
make lint           # ruff check plus format check
make tf-check       # terraform fmt -check and validate, both roots
make image          # both arm64 images, tagged with the current commit
make test-e2e       # opt-in, drives a deployed stack with real URLs
make outputs ENV=dev  # re-init that backend, then print its outputs
```

```bash
make verify-image VIDEO_URL='https://www.youtube.com/watch?v=<id>'
```

runs yt-dlp, ffmpeg and Deno inside the worker container against a real video and prints
the resulting codecs. This is the fastest way to prove the media toolchain still works
after a bump.

Other verification drills, each of which needs a deployed stack:

```bash
make verify-retention
make verify-idempotency JOB_ID=<id> VIDEO_ID=<id>
```

---

## How it is built

```text
terminal -> HTTP API -> api Lambda (validate, rate limit, enqueue)
                            |
                          SQS (+ DLQ)
                            |
                     worker Lambda (probe, download, remux, upload)
                            |
                     private S3 (7-day lifecycle)
                            |
              presigned GET URL, 60 minutes
```

- Python 3.12 on arm64 Lambda container images, built from one multi-stage Dockerfile with
  two targets: a small `api` image and a `worker` image carrying pinned yt-dlp, ffmpeg and
  Deno.
- Stream copy, never re-encode: that is what keeps a 60-minute video inside the 15-minute
  Lambda ceiling.
- Job state and the hourly rate window share one DynamoDB table; the rate check and the
  job write are one transaction.
- Media is private, encrypted with SSE-S3, and expires after 7 days. Only presigned URLs
  reach it.

Production substitutes the host's two Docker services (see above) for the `api`, `worker`
and `bot` functions; the queue, the table, the bucket, the presigner and both entry points
are identical. The diagram is the Lambda target, and it is what `dev` deploys.

Design authority lives in `specs/001-youtube-video-download/`: `spec.md` for behaviour,
`plan.md` and `research.md` for the decisions, `contracts/` for the exact API and queue
shapes, `.specify/memory/constitution.md` for the rules the project must follow.

---

## Running on a Raspberry Pi

The production stack runs its compute on one arm64 host in Docker while AWS keeps S3,
DynamoDB, SQS and SSM. One image, two services: an HTTP front end (the terminal API, the
Telegram webhook and the worker's terminal-event hook) and a queue consumer that
downloads, remuxes and uploads.

```bash
make pi-ship IMAGE_TAG=<sha> PI_HOST=rasp   # build arm64, then docker load over SSH
ssh rasp 'cd /opt/serveur && sudo docker compose up -d ytdl ytdl-worker'
```

The containers sit behind the host's Caddy on the loopback, and the existing Cloudflare
tunnel publishes them as `https://ytdl.timcloud.fr`; no inbound port is opened. Terraform
owns the AWS side: `worker_lambda_enabled = false` stops the Lambda mapping from competing
for the same queue, `bot_public_url` points the webhook at the host, and the
`ytdl-<env>-pi` IAM user holds the host's least privilege. The image is built on an arm64
machine because the Pi runs it natively. Paths, update procedure, credential rotation,
disk floor and failure modes are in `docs/runbook.md` section 12.

## Cost

The documented profile is 100 videos per month at 15 minutes each with 7-day retention,
expected at roughly $1.40 per month, against a $10 ceiling with alarms at $5 and $10.
There is no idle cost: `terraform destroy` removes every chargeable resource.

Everything runs in `eu-west-3` (Paris), with one deliberate exception: the two budget
alarms live in `us-east-1`, because the `AWS/Billing` metric is published only there.
They notify a second SNS topic, `ytdl-<env>-alarms-us-east-1`. Set `alarm_email` and
confirm both subscription emails, or the alarms fire into a topic nobody reads.

The ceiling is a guardrail, not a target: nothing in this project generates load to
approach it. It is justified by a pricing model and monitored from billing data, never
reached on purpose (constitution principle V).

## Known limitation

YouTube scores datacenter IP ranges badly, and Lambda egress addresses are datacenter
ranges. The production host's residential address fares better but is shared with every
service behind the same connection, and a CGNAT pool can be scored like a datacenter. A
video may be refused with a bot check even though it plays fine in your browser. The
service reports this honestly as a non-retryable `upstream_problem`; the remedy is
configuration, not code. `docs/runbook.md` section 6 covers the three options: a cookies
file, a residential proxy, or a player-client override.
