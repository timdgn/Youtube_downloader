# Quickstart Validation Guide: YouTube Video Download Service

**Feature**: `001-youtube-video-download`
**Date**: 2026-11-15
**Contracts**: [contracts/api.md](./contracts/api.md), [contracts/job-message.md](./contracts/job-message.md)
**Data model**: [data-model.md](./data-model.md)

This is the run guide. It proves the feature works end to end from an empty AWS
account, and it proves the environment can be removed again. It contains commands and
expected outcomes, not implementation code.

---

## Prerequisites

| Need | Detail |
|------|--------|
| AWS account | Empty is fine. Permissions to create S3, DynamoDB, SQS, Lambda, ECR, IAM, API Gateway, SSM, CloudWatch, SNS. |
| Credentials | An AWS profile exported in the shell. No long-lived keys in any file in the repo. |
| Tooling | Terraform ≥ 1.10, AWS CLI v2, Docker with `buildx`, `make`, `curl`, `jq` |
| Region | One region for everything, `eu-west-3` in the examples |
| Content | A URL for a short public video you are entitled to download, and one you know will fail (private or deleted) |

Budget: the documented ceiling is $10 per month (research D-02). Nothing in this guide
approaches it; a full validation run costs cents.

---

## 1. Provision the state bucket, once

```bash
cd infra/bootstrap
terraform init
terraform apply -var 'state_bucket=ytdl-tfstate-<account-id>'
```

Expected: one S3 bucket, versioning on, public access blocked, `Project` and
`Environment` tags present.

**Verify**: `aws s3api get-bucket-versioning --bucket ytdl-tfstate-<account-id>` reports
`Enabled`.

---

## 2. Deploy the dev environment

```bash
export AWS_REGION=eu-west-3
export ENV=dev
make deploy ENV=dev
```

`make deploy` runs, in order: `terraform init` against the dev backend, a
repository-only apply (ECR must exist before an image can be pushed), a container image
build for both targets with an immutable tag derived from the git commit, a push, then
`terraform apply -var image_tag=<tag>`. See research D-16 for why the repository apply
comes first.

**Verify**

```bash
terraform -chdir=infra output api_base_url
terraform -chdir=infra output media_bucket
```

Expected: a base URL and a bucket name, both suffixed for `dev`.

**Constitution compliance check** (principle I): `terraform -chdir=infra plan
-var image_tag=<tag>` immediately afterwards reports no changes. If it proposes
changes, something was applied outside the definition.

**Cost guard check** (principle V): the SNS topic `ytdl-dev-alarms` exists in Paris and
`ytdl-dev-alarms-us-east-1` exists in N. Virginia, each with one confirmed subscription
when `alarm_email` is set in `env/dev.tfvars`. Confirm both addresses, not just one.

---

## 3. Get the access token

```bash
export TOKEN=$(aws ssm get-parameter \
  --name /ytdl/dev/api-token --with-decryption \
  --query Parameter.Value --output text)
```

**Verify**: the token is a long random string; it is not present in
`infra/env/*.tfvars` or in any file in the repository.

---

## 4. Submit a link

```bash
export API=$(terraform -chdir=infra output -raw api_base_url)

curl -sS -X POST "$API/v1/jobs" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=<SHORT_PUBLIC_VIDEO>","quality":"720p"}' | tee /tmp/job.json

export JOB=$(jq -r .job_id /tmp/job.json)
```

Expected: `202` with `state: "accepted"` in under 3 seconds. This is SC-001, measured
with `curl -w '%{time_total}'`. The command returns before any media moves.

---

## 5. Poll the job

```bash
watch -n 3 "curl -sS '$API/v1/jobs/$JOB' -H 'Authorization: Bearer $TOKEN' | jq '{state, produced_quality, result, failure}'"
```

Expected sequence: `accepted` then `processing` then `completed` with
`result.status: "available"` and a `download_url`. A 15-minute 1080p video should reach
`completed` well inside 10 minutes (SC-002).

**Verify the state machine** (US2 scenario 1): the first poll after submission never
implies an error. `created_at` is present and `updated_at` advances monotonically.

---

## 6. Download and play the file

```bash
export URL=$(curl -sS "$API/v1/jobs/$JOB" -H "Authorization: Bearer $TOKEN" | jq -r .result.download_url)

curl -sS -o video.mp4 "$URL"
ls -lh video.mp4
curl -sSI "$URL" | grep -i 'content-type\|content-length'
```

Expected: a file that plays start to finish with audio and video in sync, `video/mp4`,
size equal to `result.size_bytes` (SC-004). The command carries no credential beyond
the URL itself (US1 scenario 2).

**Verify codecs**: `ffprobe -v error -show_entries stream=codec_name,height -of csv video.mp4`
reports `h264` and `aac` on the normal path (research D-01).

---

## 7. Re-issue the link

```bash
sleep 1
curl -sS -X POST "$API/v1/jobs/$JOB/link" -H "Authorization: Bearer $TOKEN" | jq .
```

Expected: a `200` with a *different* `download_url` and a fresh `expires_at` 3,600
seconds out. The file is not reprocessed and the rate counter does not move (FR-006,
SC-012).

**Verify no reprocessing**: `processing_ms` in `GET /v1/jobs/$JOB` is unchanged, and
the worker has no new invocation in CloudWatch for this job.

---

## 8. Prove the rejection paths, without spending bandwidth

```bash
# Foreign host: 400 invalid_input
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$API/v1/jobs" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"url":"https://vimeo.com/123456"}'

# Playlist: 400 invalid_input
curl -sS -X POST "$API/v1/jobs" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/playlist?list=PL123"}' | jq .error.code

# Missing token: 401 unauthorized
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$API/v1/jobs" \
  -H 'Content-Type: application/json' -d '{"url":"https://youtu.be/dQw4w9WgXcQ"}'
```

Expected: `400`, `invalid_input`, `401`. None of these creates a job, and none appears
in the worker's logs (SC-003: zero media bytes for rejected input).

---

## 9. Prove the failure reporting

```bash
curl -sS -X POST "$API/v1/jobs" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=<PRIVATE_OR_DELETED_VIDEO>"}' | jq -r .job_id
```

Poll it until it settles, then:

```bash
curl -sS "$API/v1/jobs/$JOB_FAILED" -H "Authorization: Bearer $TOKEN" | jq .failure
```

Expected: `state: "failed"`, `failure.category` one of the five, `failure.retryable`
matching the table in `contracts/api.md`, and a message that names the next action. No
`download_url` is offered (US2 scenarios 2 and 3).

**Rate limit check** (FR-014, SC-010): submit 11 times in one hour with a deliberately
invalid body so no downloads start. The 11th answer is `429` with `Retry-After`, in
under a second, and no job was created.

---

## 10. Prove retention and expiry reporting

Two options, in increasing cost:

- **Fast (recommended)**: in dev only, temporarily set `retention_days = 0` in a
  throwaway plan, or delete the object directly with
  `aws s3 rm s3://<media-bucket>/output/<job_id>/ --recursive`, then re-read the job.
  Expected: `result.status: "expired"`, `download_url: null`, and a `410 result_expired`
  from `POST /v1/jobs/<id>/link`. This proves the `HeadObject` guard (FR-012).
- **Slow**: leave a job for 7 days and run
  `aws s3 ls s3://<media-bucket>/output/ --recursive` to confirm the object is gone and
  no object is older than 8 days (SC-005, research D-12).

---

## 11. Add a second environment from the same definition

```bash
make deploy ENV=prod
```

Expected: a second, independent set of resources with `prod` in their names and the
`Environment=prod` tag, no change to any `.tf` file, and a separate state object
(US4 scenario 2). Destroying `dev` afterwards leaves `prod` untouched.

---

## 12. Run the automated suites

```bash
make test          # unit + contract, offline, deterministic
make test-e2e      # opt-in, drives the deployed dev stack, needs real URLs
```

Expected: unit tests cover URL normalisation, quality capping, format selection from
fixture metadata, limit evaluation, stderr classification, key derivation, and state
transitions. Contract tests drive both handlers with synthetic API Gateway and SQS
events against fakes. Neither layer reaches real AWS or real YouTube (constitution
principle IV).

---

## 13. Tear down

```bash
make destroy ENV=prod
make destroy ENV=dev
```

**Verify** (SC-007, US4 scenario 3):

```bash
aws s3 ls | grep ytdl
aws dynamodb list-tables | grep ytdl
aws sqs list-queues | grep ytdl
aws lambda list-functions | grep ytdl
aws ecr describe-repositories | grep ytdl
aws apigatewayv2 get-apis | grep ytdl
aws ssm describe-parameters --parameter-filters "Key=Name,Option=Contains,Values=/ytdl/"
```

Expected: no output from any of them, except the state bucket from step 1, which is
deliberately outside the application's lifecycle. An empty S3 bucket is still a
resource: the bucket itself must be gone, not just emptied. If anything survives
destroy, that is a defect under constitution principle I.

**Cost check**: after destroy, the Billing console shows no resources attributable to
the environment, and no further charges accrue.

---

## 14. Operational runbook (short form)

| Alarm | First action |
|-------|--------------|
| API 5xx above threshold | Read the API log for the failing `job_id`; check DynamoDB and SQS permissions before changing code |
| Worker errors above threshold | Group by `failure_category`; a spike in `upstream_problem` is YouTube, not us |
| Worker throttles | The reserved concurrency of 2 is being hit; expected at the cap, investigate only if it persists outside bursts |
| Worker p95 duration above 600 s | Approaching the 900 s ceiling; check average output size and download rate before raising memory |
| DLQ depth above zero | Read the message, find the `job_id`, read its failure; a dead letter is always a defect |
| Estimated charges above $5 | Check average `media_size_bytes`; the dominant driver is retention of large files |
| Estimated charges above $10 | Ceiling reached. Stop submissions, inspect the cost explorer, then decide on a change |

Full documentation required by FR-018 ships with the feature: how to submit, poll,
fetch, provision, destroy, and what each failure category means. Both commands and
their expected outputs are in this file.
