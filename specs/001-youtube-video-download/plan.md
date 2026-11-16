# Implementation Plan: YouTube Video Download Service

**Branch**: `001-youtube-video-download` | **Date**: 2026-11-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-youtube-video-download/spec.md`

**Status**: implemented and verified on 2026-11-16. Dev is live in `eu-west-3` (Paris);
see `tasks.md` for the final task state and `checklists/requirements.md` for the
verification record. The only open item is the cost-ceiling drill (T101).

**Note**: Filled in by `/speckit.plan`. Phase 0 and Phase 1 artifacts are generated
alongside this file.

---

## Summary

A personal, single-operator service that accepts a YouTube video URL from a terminal,
downloads the video in the background, stores one merged file in private S3 storage for
seven days, and hands back a presigned download link valid for 60 minutes, re-issuable
on demand while the file exists. Failures are classified so the operator can tell a bad
link from a broken system, and a single Terraform definition both provisions and
removes the whole environment.

The technical approach: API Gateway HTTP API fronting a small `api` Lambda that
validates, rate-limits, records, and enqueues; an SQS queue (with a dead-letter queue)
feeding a larger `worker` Lambda that probes metadata, enforces limits, downloads with
a pinned `yt-dlp`, remuxes to MP4 with `-c copy`, and uploads to S3; DynamoDB holds job
state and the rate window; S3 presigned URLs do the delivery. Both functions are
container images on arm64, built from one multi-stage Dockerfile with two targets.
Everything is Terraform, parameterised by environment.

Two values the spec deferred are settled here: **MP4 with H.264/AAC, produced by stream
copy** (research D-01) and a **$10 per month ceiling** for 100 videos per month at 7-day
retention (research D-02).

The single largest risk is external: YouTube scores datacenter IP ranges badly, so a
Lambda egress address may be refused outright. That risk is accepted, bounded by
configuration hooks (cookies file, proxy, player client override), and surfaced as a
distinct non-retryable failure instead of a generic error. See research D-14.

---

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: `yt-dlp==2026.10.26` (subprocess), pinned static `ffmpeg`
build, pinned `deno` runtime for yt-dlp's JavaScript challenges, `boto3`

**Storage**: S3 for media (`output/`) and optional configuration (`config/`); DynamoDB
on-demand single table for job records and rate windows

**Testing**: `pytest`; offline unit tests over pure logic, contract tests over handlers
with fake AWS clients; opt-in `e2e` suite against a deployed dev stack

**Target Platform**: AWS Lambda container images, `arm64`, provisioned by Terraform
(≥ 1.10) with the AWS provider 6.x; API Gateway HTTP API; SQS; DynamoDB; SSM; SNS

**Project Type**: Single-project serverless service with a terminal interface. No
library, no CLI to install, no web UI.

**Performance Goals**:
- Submission acknowledged in under 3 seconds, independent of video length (FR-003,
  SC-001)
- 95% of jobs for videos up to 30 minutes at default quality downloadable within
  10 minutes (SC-002)
- 2 concurrent jobs, 10 submissions per hour per credential (FR-014)
- Link re-issue in under 1 second, no reprocessing (FR-006, SC-012)

**Constraints**:
- Lambda 900-second ceiling, 10,240 MB `/tmp`, 6 MB synchronous payload; no media byte
  ever in a response body
- Maximum output 1080p, 60 minutes, 4 GB; rejected or capped before media transfer
  (FR-009)
- Presigned links 3,600 seconds or less; 7-day retention
- `$10` per month ceiling for the documented profile, with alarms at $5 and $10
- Copy-only processing (no re-encoding), which is what keeps one invocation inside the
  900-second window

**Scale/Scope**: One operator, one AWS account, 100 videos per month, 2 environments
(`dev`, `prod`), 38 Terraform resources per environment, 3 HTTP routes, 1 queue, 1 table, 1
bucket, 2 functions.

---

## Constitution Check

*GATE: must pass before Phase 0 research. Re-checked after Phase 1 design.*

| # | Principle | Gate: does the plan... | Status | Evidence |
|---|-----------|------------------------|--------|----------|
| I.1 | IaC only | declare every AWS resource in Terraform, with no console or ad-hoc CLI step? | PASS | All resources in `infra/`; the only human AWS actions are read-only (`aws ssm get-parameter`, `aws s3 ls`) |
| I.2 | IaC only | keep state remote with locking, never committed? | PASS | S3 backend with `use_lockfile = true`; `.gitignore` excludes state and `.terraform` |
| I.3 | IaC only | pin provider, module, and lock versions? | PASS | `required_version`, pinned provider constraint, committed `.terraform.lock.hcl`; yt-dlp, ffmpeg, and Deno pinned by version and checksum |
| I.4 | IaC only | parameterise `dev` and `prod` as one code path? | PASS | Same root, two tfvars; no environment branching in code (data-model, Environment) |
| I.5 | IaC only | remove the whole environment on destroy? | PASS | Quickstart step 13 verifies zero survivors across seven services (FR-017, SC-007) |
| II.1 | Lambda limits | never return video bytes in a response? | PASS | Presigned S3 GET only; the API returns kilobytes (contracts/api.md) |
| II.2 | Lambda limits | keep the pipeline validate → async job → S3 → short-lived URL? | PASS | Exactly that shape (D-05, D-07) |
| II.3 | Lambda limits | split work that can exceed one invocation; make each stage idempotent? | PASS with justification | Copy-only remux plus a 60-minute cap keeps a job inside 900 s; a 780 s deadline guard aborts cleanly. Idempotency by deterministic key and conditional transitions (D-08, D-09). See Complexity Tracking. |
| II.4 | Lambda limits | vendor and pin the media toolchain? | PASS | Pinned image with exact yt-dlp, ffmpeg, Deno versions and verified checksums (D-03, D-04) |
| II.5 | Lambda limits | declare timeout, memory, and reserved concurrency per function, sized from data? | PASS | Table in D-11: `api` 512 MB / 15 s / 5; `worker` 3,008 MB / 900 s / 2 / 10,240 MB `/tmp` |
| III.1 | Least privilege | one role per function, scoped to exact actions and resources, no wildcards? | PASS | `api` role: table, queue, one object prefix, one SSM parameter. `worker` role: table, object prefix, `config/` read, queue consume. No `*` action, no `*` resource |
| III.2 | No public exposure | Block Public Access everywhere, links ≤ 1 hour? | PASS | Bucket-level block, ownership enforced, TLS-only policy, `ExpiresIn = 3600` |
| III.3 | No public exposure | no long-lived credentials in code, images, env vars, or logs? | PASS | Token in SSM `SecureString`; execution roles only; logger uses an allowlist |
| III.4 | No public exposure | encryption at rest, lifecycle expiry, `Project`/`Environment` tags? | PASS | SSE-S3, 7-day lifecycle plus multipart abort, tags on every resource (D-12) |
| IV.1 | Test-first | unit tests precede pure logic? | PASS | Tested first: URL normalisation, quality capping, format selection, limit evaluation, stderr classification, key derivation, state transitions (D-18) |
| IV.2 | Test-first | every handler contract-tested with synthetic events and stubbed clients? | PASS | Contract tests for both handlers against fakes; no network, no AWS |
| IV.3 | Test-first | default suite offline and deterministic; end-to-end separate? | PASS | `make test` offline; `make test-e2e` opt-in against dev |
| IV.4 | Test-first | input validation a tested boundary? | PASS | Rejection cases enumerated in FR-002 and asserted both at unit and contract level |
| V.1 | Operations | structured JSON logs, one line per event, correlated by job id, no secrets or full URLs? | PASS | Logger with a fixed field allowlist; API access log configured to omit query strings and headers (FR-015, SC-009) |
| V.2 | Operations | alarms before launch for errors, throttles, p95 duration, DLQ depth, spend, each with a named response? | PASS | Seven alarms to two topics: five operational in the stack region, two budget in `us-east-1` because a CloudWatch alarm can only act on a topic in its own region; runbook table in `docs/runbook.md` (D-17) |
| V.3 | Operations | rate limited and concurrency capped? | PASS | Hourly conditional counter plus worker reserved concurrency of 2; stage throttling as a backstop (D-10) |
| V.4 | Operations | explicit cost ceilings: duration, resolution, lifecycle, documented monthly budget? | PASS | 60 min, 1080p, 4 GB, 7-day lifecycle, $10 ceiling with alarms at $5 and $10 (D-02) |
| V.5 | Operations | failures classified and surfaced as distinct actionable errors? | PASS | Five fixed categories plus `retryable` (D-13, contracts/api.md) |
| C.1 | Constraints | container images because media tooling exceeds zip limits, Python 3.12 baseline kept? | PASS | No amendment needed; the constitution's recommended baseline is used as written |
| C.2 | Constraints | acceptable-use constraint respected; no public ripping service; no DRM or paid-access bypass? | PASS | Token-gated single operator, no anonymous path, DRM and paid content rejected as `unsupported_media`, README states the constraint |
| C.3 | Constraints | copyleft obligations of bundled tooling recorded? | PASS | `THIRD-PARTY-NOTICES.md` records yt-dlp (Unlicense), ffmpeg (GPL build, not redistributed), Deno (MIT), and the no-redistribution note |
| W.1 | Workflow | plan reviewed before implementation; changes attributable? | PASS | This document; infrastructure and application changes ship as separate commits |
| W.2 | Workflow | deployment order dev then prod; immutable image tags; rollback by re-applying a tag? | PASS | `make deploy ENV=dev` precedes prod; tags are `<git-sha>-api` / `<git-sha>-worker`; `latest` never used (D-16) |
| W.3 | Workflow | documentation is part of done? | PASS | README, runbook, failure catalogue, and quickstart ship in the same change (FR-018) |

**Gate result: PASS.** No principle is violated. Three implementation choices need to be
recorded as complexity, not waived: the bootstrap Terraform root, the repository-only
apply on first deploy, and the two unused S3 prefixes. All three are in Complexity
Tracking with the simpler alternative that was rejected.

---

## Project Structure

### Documentation (this feature)

```text
specs/001-youtube-video-download/
├── plan.md              # This file (/speckit.plan)
├── research.md          # Phase 0: decisions, alternatives, verified facts
├── data-model.md        # Phase 1: entities, state machine, invariants
├── quickstart.md        # Phase 1: runnable validation guide
├── contracts/
│   ├── api.md           # HTTP contract
│   └── job-message.md   # SQS message contract
├── checklists/
│   └── requirements.md  # Spec quality checklist (already complete)
├── spec.md              # Behaviour specification
└── tasks.md             # Phase 2 output (/speckit.tasks, not created here)
```

### Source Code (repository root)

```text
app/
├── Dockerfile                 # Multi-stage: `api` and `worker` final targets
├── requirements.txt           # Fully pinned
├── pyproject.toml             # pytest config, lint config, e2e marker
├── THIRD-PARTY-NOTICES.md     # Licence record for bundled tooling
├── src/ytdl/
│   ├── config.py              # Environment variables, typed and validated once
│   ├── logging.py             # JSON logger with a fixed field allowlist
│   ├── auth.py                # Bearer token check, constant time, cached
│   ├── ids.py                 # Time-ordered job identifiers
│   ├── urls.py                # URL normalisation and single-video validation
│   ├── quality.py             # Quality labels, ceiling, capping, fallback
│   ├── formats.py             # Format selection from extracted metadata
│   ├── limits.py              # Duration and size evaluation, accept or reject
│   ├── errors.py              # stderr to failure category, plus retryable flag
│   ├── keys.py                # S3 key derivation
│   ├── states.py              # Allowed transitions
│   ├── api/
│   │   ├── handler.py         # Lambda entry point, API Gateway v2 events
│   │   └── routes.py          # The three routes and their response shaping
│   ├── worker/
│   │   ├── handler.py         # Lambda entry point, SQS batch of 1
│   │   └── pipeline.py        # claim, probe, enforce, download, remux, upload, record
│   └── adapters/
│       ├── dynamo.py          # Job repository and rate window, conditional writes
│       ├── s3.py              # Media object, presign, existence check
│       ├── sqs.py             # Enqueue
│       └── extractor.py       # yt-dlp subprocess, deadline, stderr capture
└── tests/
    ├── unit/                  # Pure logic, fixtures, no I/O
    ├── contract/              # Handlers with synthetic events and fakes
    ├── fixtures/              # Captured yt-dlp metadata and stderr samples
    └── e2e/                   # Opt-in, deployed dev stack

infra/
├── bootstrap/                 # State bucket, applied once, local state
│   ├── versions.tf
│   └── main.tf
├── versions.tf                # Terraform and provider constraints
├── providers.tf               # Default provider, plus us-east-1 alias for billing alarms
├── backend.tf                 # Partial S3 backend config
├── backend/
│   ├── dev.backend            # Key: env/dev/terraform.tfstate
│   └── prod.backend           # Key: env/prod/terraform.tfstate
├── variables.tf
├── locals.tf                  # Naming and the mandatory tag set
├── storage.tf                 # Bucket, block public access, policy, lifecycle
├── state.tf                   # DynamoDB table, queue, dead-letter queue
├── functions.tf               # ECR, roles, log groups, both functions, event source mapping
├── api.tf                     # HTTP API, stage, routes, access logging
├── alarms.tf                  # Two SNS topics (one per alarm region), seven alarms
├── outputs.tf                 # Base URL, bucket, function names
└── env/
    ├── dev.tfvars
    └── prod.tfvars

docs/
└── runbook.md                 # Failure catalogue and alarm responses

Makefile                       # deploy, destroy, test, test-e2e, image
README.md                      # Entry point: what it does, how to use it, constraints
```

**Structure Decision**: single project, no nested Terraform modules. There is one
application and one deployment target per environment, so module indirection would add
variable plumbing without reuse. The Terraform root is split by concern rather than by
depth, which keeps each file reviewable on its own. The application is split by
responsibility with a hard line between pure logic (`src/ytdl/*.py`), adapters (all I/O
behind four narrow interfaces), and entry points, so the constitution's test-first rule
is mechanically enforceable: anything in `src/ytdl/` that is not an adapter has no I/O
and needs no stub.

The `api` and `worker` functions share one image repository and one Dockerfile but build
two images, because putting ffmpeg and Deno in front of the API's 3-second budget would
be a self-inflicted latency cost.

---

## Phase 0 and Phase 1 outputs

| Artifact | Status | Content |
|----------|--------|---------|
| `research.md` | complete | 19 decisions with rationale and rejected alternatives; platform facts with sources; the two deferred spec values resolved; the YouTube IP-reputation risk recorded |
| `data-model.md` | complete | Download Job, Media Object, Quality Preference, Rate Window, Audit Entry, Environment; state machine with guards; 10 invariants |
| `contracts/api.md` | complete | 3 routes, request and response schemas, 5 failure categories, error envelope, cross-cutting guarantees |
| `contracts/job-message.md` | complete | SQS body, consumption rules, visibility and retry timing |
| `quickstart.md` | complete | 14 steps from an empty account to verified teardown, including rejection, failure, expiry, rate-limit, and second-environment drills |

No `NEEDS CLARIFICATION` markers remain anywhere in the plan or the artifacts.

---

## Post-Design Constitution Re-Check

*Required after Phase 1. Design can introduce violations that the pre-research check
could not see.*

| Risk introduced by design | Assessment | Result |
|---------------------------|------------|--------|
| One table serving two entity types (jobs and rate windows) | IAM is scoped to the table, not per item, so this does not widen permissions. Both item types are written by the same function. | PASS |
| Presigned URL exactly 3,600 seconds | Principle III says "one hour or less"; 3,600 is exactly the boundary, and the TTL is a variable so it can only be lowered per environment. | PASS |
| The job record keeps a 30-day TTL after media expiry | It contains no media and no secrets. Audit history outliving the file is intentional, and FR-015 is satisfied by the logger's field allowlist. | PASS |
| Worker `HEAD` before download to prove idempotency | Adds an S3 read on the object prefix the role already reads. No new permission. | PASS |
| Cookies file under `config/` when configured | The worker role gains `s3:GetObject` on `config/*` only, and the object is never logged. It is empty by default, so the default deployment grants no read on a non-existent prefix. | PASS |
| Lifecycle expiry lands between day 7 and day 8 | The service never offers a file past its 7-day timestamp, and `HeadObject` covers the gap. Recorded in research D-12 with its verification method instead of being hidden. | PASS, recorded |
| Single-invocation pipeline (no Step Functions) | Valid only because processing is a stream copy and the duration cap is enforced before transfer. The deadline guard makes the failure mode explicit. If re-encoding ever enters scope, this gate fails and the design must be split. | PASS, conditional |
| YouTube may refuse all traffic from Lambda | External, not a design violation. Mitigations, classification, and an early end-to-end check are in the plan (D-14, quickstart step 4 onward). | PASS, accepted risk |

**Post-design gate result: PASS.** No Complexity Tracking entry is required beyond the
three recorded below.

---

## Complexity Tracking

> Filled because three design choices need justification, not because a principle is
> violated.

| Choice | Why needed | Simpler alternative rejected because |
|--------|------------|--------------------------------------|
| A second Terraform root, `infra/bootstrap/`, for the state bucket | The bucket that holds state cannot be managed by the state it holds. | Putting it in the main root creates a circular dependency; creating it by hand in the console violates principle I. Fifteen lines is the smallest honest answer. |
| `terraform apply -target=aws_ecr_repository.app` on first deploy | A lambda function cannot reference an image in a repository that does not exist yet. | A third root for one resource, or a `null_resource` running Docker inside `terraform apply`, both spread the problem further and make plans non-reproducible. Confined to one documented Makefile line. |
| Only `output/` and `config/` prefixes exist, not `incoming/` and `tmp/` | Nothing is ever staged in the bucket: media is written once, after it is finished, and scratch lives in Lambda `/tmp`. | Creating empty prefixes and lifecycle rules for them would be resources that exist to satisfy a diagram. Documented here so the difference from the constitution's example layout is visible. |
| One image repository, two image tags, two build targets | The API must stay small to protect the 3-second acknowledgement; the worker needs ffmpeg and Deno. | A single shared image puts ~800 MB of media tooling on the API's cold start path; two repositories duplicate the build and push machinery. |
| No PO token provider bundled | It cannot run as a sidecar in Lambda, and tokens do not fix datacenter IP reputation. | Shipping it adds a second runtime and ~150 MB to the worker image for a failure mode it does not address. Configuration hooks (cookies, proxy) are the remedies that work (research D-14). |

---

## Next Step

`/speckit.tasks` breaks this plan into ordered, independently testable tasks. Two items
from the plan should be scheduled early, because they can change later decisions:

1. The container image with pinned yt-dlp, ffmpeg, and Deno, proven to extract one real
   video from a Lambda-like environment (riskiest assumption first).
2. The `$10` budget guard: the billing alarms and the SNS subscription wired before any
   production use, as principle V requires.
