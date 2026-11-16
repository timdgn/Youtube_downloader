# YouTube Download Service

A personal, single-operator service that takes a YouTube URL from your terminal, downloads
the video in the background on AWS Lambda, stores one finished file privately for seven
days, and returns a download link valid for 60 minutes.

Provisioning and removal are both Terraform. There is no console step, no server to
patch, and no bucket to empty by hand.

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
workaround for YouTube refusing Lambda's egress address.

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

Design authority lives in `specs/001-youtube-video-download/`: `spec.md` for behaviour,
`plan.md` and `research.md` for the decisions, `contracts/` for the exact API and queue
shapes, `.specify/memory/constitution.md` for the rules the project must follow.

---

## Cost

The documented profile is 100 videos per month at 15 minutes each with 7-day retention,
expected at roughly $1.40 per month, against a $10 ceiling with alarms at $5 and $10.
There is no idle cost: `terraform destroy` removes every chargeable resource.

## Known limitation

YouTube scores datacenter IP ranges badly, and Lambda egress addresses are datacenter
ranges. A video may be refused with a bot check even though it plays fine in your browser.
The service reports this honestly as a non-retryable `upstream_problem`; the remedy is
configuration, not code. `docs/runbook.md` section 6 covers the three options: a cookies
file, a residential proxy, or a player-client override.
