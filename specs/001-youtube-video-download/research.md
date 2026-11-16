# Phase 0 Research: YouTube Video Download Service

**Feature**: `001-youtube-video-download`
**Date**: 2026-11-15
**Inputs**: [spec.md](./spec.md), `.specify/memory/constitution.md` v1.0.0

This document resolves every unknown in the plan's Technical Context, including the
two values the spec deliberately deferred to planning: the output container/codec
combination, and the monthly budget ceiling.

---

## Verified platform facts

Facts below were checked against primary sources on 2026-11-15 and are the numbers
the design is sized from.

| Fact | Value | Source |
|------|-------|--------|
| Lambda maximum invocation duration | 900 s (15 min), not increasable | AWS Lambda quotas |
| Lambda memory range | 128 MB to 10,240 MB; ~1 vCPU at 1,769 MB | AWS Lambda quotas |
| Lambda `/tmp` (ephemeral storage) | 512 MB to 10,240 MB, 1 MB increments | AWS Lambda quotas |
| Lambda synchronous payload | 6 MB request and response | AWS Lambda quotas |
| Lambda container image (uncompressed) | 10 GB maximum | AWS Lambda images |
| Lambda non-VPC bandwidth | 625 Mbps default | AWS Lambda quotas |
| Lambda arm64 duration price | $0.0000133334 per GB-second (first tier) | AWS Lambda pricing |
| Lambda request price | $0.20 per million, both architectures | AWS Lambda pricing |
| Lambda ephemeral storage price | $0.0000000309 per GB-second above 512 MB | AWS Lambda pricing |
| S3 presigned URL maximum lifetime (SigV4) | 7 days; well above the 1 hour needed | S3 documentation |
| S3 lifecycle expiration granularity | Whole days, applied at the next midnight UTC | S3 lifecycle documentation |
| Terraform S3 backend locking | `use_lockfile = true`, native (DynamoDB locking deprecated) | Terraform docs, v1.10+ |
| Latest Terraform AWS provider | 6.73.0 | Terraform Registry |
| yt-dlp latest stable | 2026.10.26 | yt-dlp GitHub releases |
| yt-dlp minimum Python | 3.11 (Python 3.10 support removed in 2026.10.26) | yt-dlp release notes |
| yt-dlp YouTube requirement | External JavaScript runtime plus `yt-dlp-ejs`; Deno ≥ 2.3.0 enabled by default, Node ≥ 22 needs `--js-runtimes node` | yt-dlp EJS wiki |

---

## D-01: Output container and codec (deferred value 1 of 2)

**Decision**: MP4 (ISO/IEC 14496-14) containing H.264 video (`avc1`) and AAC audio
(`mp4a`), produced by stream copy, never by re-encoding. When the source has no
H.264 rendition, fall back to the best available video stream and remux it into MP4
with its native codec, record the produced codecs in the job result and audit entry,
and mark the fallback in the structured log.

**Rationale**:
- MP4 with H.264/AAC is the only combination that plays in QuickTime, iOS, Android,
  every browser, and VLC without extra codecs. SC-004 requires a file that "plays
  start to finish in a standard media player", and this is the combination that makes
  that true by construction rather than by luck.
- Stream copy keeps the job inside the 15-minute Lambda window. Re-encoding 60
  minutes of 1080p on Lambda would take far longer than the timeout allows.
- YouTube serves H.264 renditions at every resolution up to 1080p for regular videos,
  so the ceiling defined by the spec (1080p) is always reachable in this combination.
- `Content-Type: video/mp4` gives correct browser and `curl -O` behaviour on the
  presigned link.

**Alternatives considered**:
- *WebM (VP9/Opus)*: smaller files at equal quality, but poor or absent support in
  QuickTime and iOS, which fails the "standard media player" criterion.
- *MKV*: handles any codec, but browsers and most mobile players refuse it, and the
  presigned link would download a file the operator cannot preview.
- *Re-encode to a fixed bitrate*: predictable size, but 10 to 40 times the CPU, which
  breaks the 15-minute budget and multiplies cost.
- *MP4 with AV1*: better compression, but encoding AV1 on Lambda is far outside the
  time budget, and hardware decode is not universal.

---

## D-02: Monthly budget ceiling (deferred value 2 of 2)

**Decision**: **$10.00 USD per month** for the documented profile in SC-011, with a
warning alarm at $5.00 (50%) and a ceiling alarm at $10.00 (100%), both in
`us-east-1` because the AWS Billing metric is only published there.

Documented profile: 100 downloaded videos per month, average 15 minutes each, default
quality (1080p), 7-day retention, all prices from `us-east-1` list rates.

| Line item | Basis | Monthly cost |
|-----------|-------|--------------|
| Lambda worker, arm64 | 100 jobs × 180 s × 3.008 GB = 54,144 GB-s at $0.0000133334 | $0.72 |
| Lambda worker ephemeral storage | 9.5 GB × 180 s × 100 at $0.0000000309 | $0.01 |
| Lambda API, arm64 | ~300 invocations × 0.5 s × 0.5 GB | < $0.01 |
| S3 storage | 100 × 0.6 GB, average 7 of 30 days retained ≈ 14 GB at $0.023 | $0.33 |
| S3 requests | ~100 PUT, ~400 GET | < $0.01 |
| DynamoDB on-demand | a few thousand small reads and writes, under 1 GB stored | $0.10 |
| ECR image storage | 2 images, ~1.1 GB at $0.10 | $0.11 |
| CloudWatch Logs | ~200 MB at $0.50 per GB | $0.10 |
| API Gateway HTTP API, SQS, SNS | ~1,000 requests combined | < $0.01 |
| **Expected total** | | **≈ $1.40** |

Headroom: the ceiling is roughly 7 times the expected cost. The two variables that can
actually move the bill are average output size (a 4 GB cap means a single job can cost
10 times the 600 MB estimate) and retention overlap (all 100 videos inside one week is
60 GB, about $1.38 for storage alone). Even the pessimistic case of 100 maximum-size
files with full retention overlap stays near $3, so $10 is a real ceiling, not a
restatement of the estimate.

**Alternatives considered**:
- *$5 ceiling*: only 3.5 times the estimate; a single month of larger-than-average
  files or a few retried jobs would trip it and train the operator to ignore alarms.
- *No ceiling, alert on anomaly detection*: violates constitution principle V, which
  requires an explicit, documented monthly budget.
- *Free tier as the budget*: the free tier is time limited for new accounts and does
  not apply to provisioned capacity. It cannot be the guard.

---

## D-03: Runtime, packaging, and architecture

**Decision**: Python 3.12 on `public.ecr.aws/lambda/python:3.12`, `arm64`
(Graviton2), packaged as container images built from one multi-stage Dockerfile with
two final targets: a small image for the API functions (no media tooling) and a large
image for the worker (yt-dlp, ffmpeg, Deno). Both images are pushed to one ECR
repository with immutable tags `<git-sha>-api` and `<git-sha>-worker`.

**Rationale**:
- Python 3.12 keeps the constitution's platform baseline unchanged, and it is above
  yt-dlp's 3.11 floor with two years of support margin.
- arm64 is 20% cheaper per GB-second and matches the developer's machine, so the
  local build is native and needs no emulation.
- Container images sidestep the 250 MB zip-plus-layers limit that ffmpeg, Deno, and
  the Python dependency set would otherwise blow through.
- Two build targets keep the API cold start small. The API must acknowledge a
  submission in under 3 seconds (FR-003, SC-001), and a shared 1 GB image would put
  ffmpeg and Deno on the API's critical path for no benefit.

**Alternatives considered**:
- *Zip package for the API plus an image for the worker*: smaller API artifact, but
  two packaging systems, two deploy paths, and two sets of failure modes for no
  measurable gain over a 150 MB image.
- *x86_64*: no advantage here, 25% more expensive per GB-second, and it would force
  emulated local builds on the developer's arm64 laptop.
- *Node.js or Go runtime*: the extraction ecosystem, yt-dlp, is Python-native; using
  another runtime means shelling out to a bundled Python anyway.

---

## D-04: Extraction toolchain

**Decision**: `yt-dlp==2026.10.26` invoked as a subprocess (never imported in
process), a pinned static ffmpeg build from `yt-dlp/FFmpeg-Builds` with a verified
SHA-256, and a pinned Deno binary for YouTube's JavaScript challenges, which yt-dlp
enables by default.

**Rationale**:
- A subprocess is killable. An in-process extraction that wedges (yt-dlp can hang in
  challenge solving) would consume the whole invocation with no way to recover; a
  subprocess has a hard deadline and a clean signal.
- The CLI's error text is the stable interface for classification. Parsing stderr into
  the five spec failure categories is pure, testable logic; importing internal yt-dlp
  modules is not a stable contract.
- Deno solves two problems at once: it satisfies yt-dlp's EJS requirement and its
  binary is a single static executable, which is the cheapest possible install inside
  a Lambda image. Leaving it out silently drops formats and prints deprecation
  warnings.

  **Corrected during implementation (T009, 2026-11-16)**: the Deno runtime alone is not
  enough. yt-dlp also needs the EJS *solver script distribution*, and without it the
  `n` challenge fails with "Some formats may be missing". Verified on the built image:
  `yt-dlp==2026.10.26` plus Deno produced the warning and fell back to a lower format;
  installing `yt-dlp[default]==2026.10.26`, whose `default` extra pins
  `yt-dlp-ejs==0.8.0`, removed the warning and solved the challenge locally. The image
  therefore installs the `default` extra and asserts `yt-dlp-ejs` is present at build
  time. The alternative, `--remote-components ejs:github`, downloads solver code at job
  time and would violate the pinned-toolchain rule in principle II.
- Pinning all three (yt-dlp, ffmpeg, Deno) satisfies constitution principle II: a
  silent dependency upgrade that breaks extraction is an outage.

**Alternatives considered**:
- *`yt_dlp` Python API*: better structured errors, but no hard kill, shared-process
  state, and a private API surface that changes between releases.
- *Nightly yt-dlp*: tracks YouTube faster, but "latest at build time" is an
  unreviewable dependency and contradicts the pinned-version principle.
- *Node as the JavaScript runtime*: works, but yt-dlp only enables Deno by default, so
  Node needs an extra flag, and a Node runtime in the image is heavier than the Deno
  binary.
- *PO token provider sidecar (bgutil)*: not possible in Lambda (no sidecar), and its
  own maintainers state that a token does not fix datacenter IP reputation. See D-14.

---

## D-05: Asynchronous pipeline shape

**Decision**: API Gateway HTTP API → `api` Lambda → SQS standard queue (+ dead-letter
queue) → `worker` Lambda, with job state in DynamoDB. The API returns `202` with a job
identifier; the worker is the only component that touches media.

**Rationale**:
- FR-004 forbids holding a request open during transfer. SQS is the smallest durable
  buffer that decouples acceptance from execution and still gives at-least-once
  delivery, a dead-letter queue, and redrive.
- SQS also gives the concurrency cap almost for free: reserved concurrency on the
  worker (2) limits how many messages are in flight, so the cap in FR-014 is enforced
  by the platform, not by application bookkeeping.
- HTTP API is the cheapest front door ($1 per million requests), supports stage-level
  throttling as a backstop, and needs no REST API feature the design uses.

**Alternatives considered**:
- *Lambda Function URL with direct asynchronous invocation*: no queue means no
  dead-letter queue, no redrive, and retries are capped at two attempts with no
  visibility into what was dropped.
- *Step Functions*: gives explicit stages and retry semantics, but a state machine per
  download is more moving parts, more cost, and more IAM than a single-stage copy-only
  pipeline needs. See D-08.
- *REST API with usage plans for rate limiting*: per-key throttling, but the spec's cap
  is per credential and per hour with a specific `429` body, which needs an
  application-level counter anyway.

---

## D-06: Job state store

**Decision**: One DynamoDB table, on-demand capacity, single string partition key
`pk`. Job records use `pk = JOB#<job_id>`; rate-limit windows use
`pk = RATE#<window>#<credential-fingerprint>`. DynamoDB TTL is enabled on `ttl`.

**Rationale**:
- On-demand capacity means no provisioned throughput to size or pay for at 100 jobs a
  month.
- Conditional writes give the state machine its atomicity: claiming a job, incrementing
  a counter, and rejecting a duplicate transition are all single-item operations that
  cannot interleave.
- TTL keeps job records from accumulating forever without a cleanup job, and one table
  keeps the IAM policy short (no table ARNs to enumerate, no cross-table grants).

**Alternatives considered**:
- *Two tables (jobs, rate limits)*: cleaner entity separation, but two resources, two
  ARNs, and two sets of lifecycle rules for a few hundred items.
- *SQLite or a JSON file in S3*: no conditional writes, so two concurrent submissions
  could lose an update, and the rate counter would race.
- *Aurora Serverless*: dozens of times the cost for a workload of a few hundred writes
  a month.

---

## D-07: Delivery of the finished file

**Decision**: A presigned S3 `GetObject` URL with `ExpiresIn = 3600`,
`ResponseContentDisposition = attachment; filename="<video_id>-<job_id>.mp4"`, and
`ResponseContentType = video/mp4`. The same signing function serves both the status
response and the explicit re-issue endpoint. Before signing, the service checks the
object's existence with `HeadObject`.

**Rationale**:
- Satisfies constitution principle III (one hour or less) and FR-005 exactly at the
  boundary, so there is no ambiguity about the limit.
- Presigning is a local signing operation: no bytes pass through Lambda, so the 6 MB
  payload limit is irrelevant and the download never consumes Lambda time.
- The filename is built from the video identifier and job identifier, never from the
  video title, because a title is attacker-controlled text going into a response
  header.
- The `HeadObject` check turns a lifecycle-deleted object into an honest "expired"
  answer instead of a link that 404s, which is what FR-012 requires.

**Alternatives considered**:
- *CloudFront signed URLs*: adds a distribution, a key pair, and a cache to invalidate,
  for a personal tool with a handful of downloads a day.
- *A redirect endpoint in Lambda*: reintroduces the proxy the constitution forbids and
  burns Lambda time per download byte.
- *Public objects with unguessable keys*: violates principle III, and access would
  survive the intended lifetime.

---

## D-08: One invocation per job, with a deadline guard

**Decision**: A single worker invocation performs probe, download, remux, and upload.
No Step Functions, no segmenting. A soft deadline of 780 seconds aborts the job with a
classified failure before Lambda's 900-second ceiling; the job remains retryable.

**Rationale**:
- Because D-01 copies streams instead of re-encoding, the expensive phase is bounded
  by network and disk, not by codec work. At 625 Mbps, a 4 GB download is about 55
  seconds at line rate; remuxing 4 GB with `-c copy` is tens of seconds. The 60-minute
  duration cap exists partly to keep this true.
- A deadline below the Lambda timeout means the function always gets to record a
  failure and clean up, instead of being killed mid-write with the job stuck in
  `processing`.
- Constitution principle II requires splitting work that *can* exceed one invocation.
  With copy-only processing and a hard duration cap, a normal job cannot; the deadline
  guard plus the retry path is the honest answer, documented rather than assumed.

**Alternatives considered**:
- *Step Functions with probe, download, and upload stages*: the robust answer if
  re-encoding ever enters scope, but it triples the infrastructure for a path that is
  now a copy operation.
- *Segment-and-concatenate*: only worthwhile for re-encoding, and it introduces
  keyframe alignment and audio-sync bugs.
- *ECS Fargate task per download*: no timeout ceiling at all, but it is a container
  service to operate, with 30 to 60 second cold starts, against a constitution that
  mandates Lambda.

---

## D-09: Idempotency model

**Decision**: Three independent guards. The S3 key is derived deterministically from
the job identifier (`output/<job_id>/<video_id>.mp4`). State transitions are
conditional writes (`accepted → processing` only from `accepted`, or from a stale
`processing` older than the invocation timeout). Before downloading, the worker issues
a `HeadObject`; if the object exists with a size above zero, it skips extraction
entirely and finishes the job.

**Rationale**: FR-013 and SC-008 demand exactly one stored file per job across
retries and redeliveries. A deterministic key makes a duplicate write overwrite rather
than duplicate, conditional transitions prevent two workers processing the same job,
and the pre-check avoids re-downloading work already finished (an explicit edge case in
the spec).

**Alternatives considered**:
- *Random keys plus a uniqueness check*: requires a second index or a scan to detect
  duplicates, and loses the natural overwrite behaviour.
- *SQS FIFO with a message group per job*: ordered exactly-once delivery, but 300
  messages per second of throughput and a deduplication window measured in minutes,
  neither of which this workload needs.

---

## D-10: Authentication and rate limiting

**Decision**: A single shared bearer token, generated by Terraform with
`random_password`, stored as an SSM `SecureString` parameter, read once per cold start
and cached, compared with `hmac.compare_digest`. Rate limiting is a DynamoDB
conditional counter over a fixed one-hour window aligned to the hour, written in the
same `TransactWriteItems` as the job record.

**Rationale**:
- The spec's access control is one shared credential, not identity. A token in SSM
  satisfies "no long-lived credentials in environment variables" (principle III), and
  constant-time comparison removes the timing oracle.
- Making the rate check and the job write one transaction means a throttled request
  never leaves an orphan job, and an accepted request is always counted. The spec's
  FR-014 wants a fast, distinct answer, not eventually consistent bookkeeping.
- A fixed window is simple to reason about and to test, and 10 per hour against a
  personal workload has no burst problem that a sliding window would fix.

**Alternatives considered**:
- *API Gateway usage plans and API keys*: only available on REST APIs, costs more per
  request, and the key would still need to be created and rotated somewhere.
- *A Lambda authorizer function*: correct separation, but a third function and a second
  cold start on every request, for one comparison.
- *AWS WAF rate-based rule*: per IP, per 5 minutes, so it cannot express 10 per hour per
  credential, and it costs more than the function it protects.
- *Sliding window in DynamoDB*: more accurate, twice the writes, no benefit at this
  volume.

---

## D-11: Sizing, in numbers

| Function | Memory | Timeout | `/tmp` | Reserved concurrency | Why |
|----------|--------|---------|--------|----------------------|-----|
| `api` | 512 MB | 15 s | default (512 MB) | 5 | Only JSON shaping, DynamoDB, SQS, and one SSM read; 512 MB keeps the 3-second acknowledgement honest. |
| `worker` | 3,008 MB | 900 s | 10,240 MB | 2 | ~1.7 vCPU for remuxing; `/tmp` must hold inputs plus output (2 × 4 GB cap plus headroom); concurrency 2 is the spec's cap. |

Disk arithmetic for the worst allowed job: 4 GB video stream + ~100 MB audio + 4 GB
remuxed output ≈ 8.1 GB, inside the 10,240 MB allocation but not inside 8 GB, which is
why the allocation is the maximum rather than a round number. If re-encoding is ever
added, this is the first number that must change.

---

## D-12: Retention mechanics

**Decision**: S3 lifecycle rule expires `output/` after 7 days and aborts incomplete
multipart uploads after 1 day. The job record stores `media_expires_at =`
creation + 7 days and is used as the authority for "available" versus "expired". The
job record itself carries a DynamoDB TTL 30 days after expiry, so audit history
outlives the media without accumulating forever.

**Rationale**: FR-012 and SC-005 require automatic expiry and honest reporting. S3
lifecycle is the mechanism that cannot fail silently, and the stored timestamp is what
lets the API answer "expired" without a failed request.

**Known granularity**: S3 lifecycle counts whole days and applies at the next midnight
UTC, so an object is deleted between day 7 and day 8 after creation, not at the exact
second. The service never *offers* a file past its 7-day timestamp, and the
`HeadObject` check covers the gap. Verified as: no object in the bucket has an age
above 8 days. This is the one place where "7 days" means "7 days, removed at the next
lifecycle run", and it is recorded rather than hidden.

**Alternatives considered**:
- *A daily sweeper Lambda that deletes at the exact timestamp*: exact to the hour, but
  a scheduled function, an extra role, and a paginated list-and-delete path to
  reproduce what lifecycle already does.
- *Lifecycle `days = 6`*: makes the maximum age exactly 7 days, at the price of a
  configuration value that contradicts the documented retention and will confuse the
  next reader.
- *No expiry, manual cleanup*: violates principle III outright.

---

## D-13: Failure taxonomy

**Decision**: Five stored categories, mapped one-to-one onto FR-008, plus a
`retryable` boolean and a fixed human message.

| Category | Typical cause | Retryable |
|----------|---------------|-----------|
| `invalid_input` | Not a single-video YouTube URL, malformed identifier, playlist or channel | no |
| `unsupported_media` | Private, deleted, members-only, age-restricted, region-locked, DRM, live | no |
| `limit_exceeded` | Duration above 60 min, estimated or actual size above 4 GB | no |
| `upstream_problem` | Throttling, 403 from the media host, network reset, "not a bot" challenge | yes, except when the signature is a hard block (see D-14) |
| `internal_error` | Storage failure, unexpected exception, deadline exceeded | yes |

**Rationale**: the operator's decision is always one of three: fix the input, wait and
retry, or stop trying. The category plus the boolean encodes exactly that, and both are
derived by pure functions from captured stderr, so the mapping is unit-testable without
a network.

**Alternatives considered**:
- *Pass yt-dlp's message through verbatim*: leaks internals and breaks the fixed set the
  spec requires.
- *Single `failed` state*: satisfies nothing in FR-008.

---

## D-14: Risk, YouTube blocks datacenter IPs

**Decision**: Ship the configuration hooks that address it, document the risk as
accepted, and classify the symptom honestly. Specifically: an optional cookies file
(`YTDLP_COOKIES_S3_URI`, read from `config/` in the media bucket), an optional proxy
(`YTDLP_PROXY_PARAM`, an SSM `SecureString`), and an optional `YTDLP_PLAYER_CLIENTS`
override. All three default to empty. A "Sign in to confirm you're not a bot" or a 403
from the media host is classified `upstream_problem` with `retryable = false` and a
message that names the likely cause, so the operator does not burn submissions on it.

**Rationale**: this is the largest technical risk in the feature, and it is not
something the code can eliminate. YouTube scores requests by IP reputation, and AWS
Lambda egress addresses are datacenter ranges that score badly. PO tokens prove the
client looks plausible but, per the token provider's own README, do not bypass
IP-based login restrictions; cookies from a signed-in session or a residential proxy
are the only reliable remedies. Because cookies are the operator's own credential, the
design must be able to use them without a rebuild.

**Consequences for the plan**: the first end-to-end validation of a deployed
environment must happen in the first provisioning session, not after the rest of the
build (see quickstart). If the operator's region is blocked outright, the remedy is
configuration (cookies or proxy), not code. No PO token provider service is bundled in
v1: it cannot run as a sidecar in Lambda, and it does not solve the dominant failure
mode.

**Confirmed in the first deployment (2026-11-16)**: the risk is not theoretical. From
`eu-west-3`, yt-dlp against a public 19-second video returned a hard block in 1 to 7
seconds, classified `upstream_problem` with `retryable = false`, with no media byte
transferred. A cookies file supplied by the operator loaded correctly (no hook warning,
160 cookies) but 136 of them were past their expiry, and the block persisted. Forcing the
`mweb` and `ios` player clients did not change the result either. The pipeline, the
classification, the rate limit, and the logging all behaved as designed; only the upstream
refused. The remaining remedy is a fresh export from a signed-in browser session, or a
residential proxy. This is recorded because "it failed in production" and "the design was
wrong" are different findings, and only the first is true here.

**Alternatives considered**:
- *Bundle the bgutil PO token provider and its Node runtime*: roughly +150 MB of image,
  a second runtime to pin, and no documented benefit on flagged IPs.
- *Run the worker outside Lambda on a home connection*: solves the IP problem outright
  but contradicts constitution principle II.
- *Ignore the risk*: the first symptom would be a 100% failure rate in production with a
  generic error, which is the exact outcome FR-008 and the spec's edge cases are written
  to prevent.

---

## D-15: Terraform layout and the environment parameterisation

**Decision**: One root module at `infra/`, split by concern (`storage.tf`,
`state.tf`, `functions.tf`, `api.tf`, `alarms.tf`), driven by
`infra/env/dev.tfvars` and `infra/env/prod.tfvars` with separate backend config files
(`infra/backend/dev.backend`, `prod.backend`) selecting distinct state keys in one
state bucket. The bucket itself comes from a small separate root, `infra/bootstrap/`,
applied once with local state.

**Rationale**:
- One code path, two variable sets is exactly what principle I demands, and it is
  visible in review as a diff of two small files.
- Separate state keys give dev and prod independent lifecycles: destroying dev cannot
  touch prod.
- The state bucket cannot be managed by the state it holds, so a bootstrap root is
  unavoidable. Keeping it to a bucket (with `use_lockfile = true` for locking, no
  DynamoDB table) keeps it to about fifteen lines.
- A flat root beats nested modules here: there is one application, and module
  indirection would add variable plumbing for no reuse.

**Alternatives considered**:
- *Terraform workspaces*: one state bucket with per-workspace keys, but workspaces hide
  which environment is selected, which is the classic way to apply prod by accident.
- *Terragrunt or separate directories per environment*: duplicates resources per
  environment and breaks the "same code path" rule.
- *Local state*: forbidden by principle I.

---

## D-16: Building and pushing the image inside a reviewable workflow

**Decision**: Terraform owns the ECR repository and the function definitions; a
`Makefile` target performs `docker buildx build --platform linux/arm64 --push` with an
immutable tag derived from the git commit, then applies Terraform with
`-var image_tag=<tag>`. The first apply on an empty account runs with
`-target=aws_ecr_repository.app` to create the repository, because the image cannot be
pushed into a repository that does not exist yet.

**Rationale**: principle I covers AWS resources, not build artifacts, so the image
build stays in a script where it is reviewable and reproducible; the function resource
still declares the exact immutable tag it runs. `latest` never appears.

**Known wart**: the `-target` call in the deploy target. It exists only to break the
repository/image circular dependency on first deploy and is confined to one line of the
Makefile, documented in the quickstart, instead of spreading into a third Terraform
root.

**Alternatives considered**:
- *A `null_resource` with `local-exec` that builds and pushes during apply*: puts a
  Docker daemon requirement inside `terraform apply`, makes plans non-deterministic, and
  hides build failures inside resource creation.
- *CI pipeline*: the right answer for a team, but there is no CI here and the
  constitution's deployment order (dev then prod) is a human action.
- *`:latest` tag*: forbidden by the constitution's rollback rule.

---

## D-17: Observability

**Decision**: One structured JSON log line per event, correlated by `job_id`, with a
fixed field set and no URLs, tokens, headers, or account identifiers. Custom metrics
are emitted with CloudWatch Embedded Metric Format from the same log lines (no extra
API calls). Seven alarms: five operational ones (API 5xx rate, worker errors, worker
throttles, worker p95 duration above 600 s, DLQ depth above zero) publish to the stack's
SNS topic, and the two budget alarms (estimated charges at $5 and $10) publish to a
second topic in `us-east-1`.

**Rationale**: principle V names exactly these alarms. EMF keeps metrics free of
`PutMetricData` calls and out of the request path. A DLQ alarm at "greater than zero"
is correct for a pipeline where any dead letter is a defect.

**Corrected during the first deployment (2026-11-16)**: the two budget alarms cannot
share the stack's topic. A CloudWatch alarm can only act on a topic in its own region,
and `AWS/Billing` exists only in `us-east-1`, so the pair needs its own topic there and
its own confirmed subscription. The first apply failed with `Invalid region eu-west-3
specified. Only us-east-1 is supported`, which is the topic region, not the alarm
region.

**Alternatives considered**:
- *X-Ray tracing*: useful for latency attribution, but the pipeline is three hops and
  the job identifier already correlates everything.
- *A dashboard*: nice, but the constitution asks for alarms with named responses; the
  runbook in `quickstart.md` is the response.

---

## D-18: Test strategy

**Decision**: Three layers, all offline by default.
1. Unit tests (`pytest`) over pure logic: URL normalisation, quality capping and
   fallback, format selection from fixture metadata, limit evaluation, stderr
   classification, key derivation, state transitions.
2. Contract tests over each handler with synthetic API Gateway v2 and SQS events, with
   DynamoDB, S3, SQS, and SSM replaced by fakes that record calls. No network, no AWS.
3. An opt-in `e2e` suite, marked and skipped by default, that drives a deployed dev
   stack with a real URL and the real token.

**Rationale**: principle IV requires unit-tested pure logic and contract-tested
handlers, and it explicitly forbids tests that reach the real YouTube or AWS. Captured
yt-dlp stderr and metadata fixtures make the unstable layer testable without being
online, which is the only way to distinguish "our bug" from "upstream changed".

**Alternatives considered**:
- *moto for AWS*: works offline, but it is a large dependency that reimplements
  DynamoDB semantics imperfectly; hand-written fakes over narrow interfaces are faster
  and assert more.
- *VCR-style HTTP recording*: useful for yt-dlp internals, but the design never makes
  HTTP calls itself; it spawns a process, and the process boundary is stubbed.

---

## D-19: Deliberately not built

Recorded so the boundaries are visible in review rather than rediscovered later.

| Not built | Why | Spec reference |
|-----------|-----|----------------|
| Deduplication of identical URLs | Each submission is its own job by design; the rate cap is the protection | Assumptions, edge cases |
| Audio-only, subtitles, thumbnails | Out of scope for v1 | Assumptions |
| Playlists, channels, live | Rejected at validation | FR-002 |
| Re-encoding or quality transcoding | Breaks the time budget and the cost ceiling | D-01, D-08 |
| Web UI or public endpoint | Personal tool, terminal only | Assumptions, FR-019 |
| PO token provider service | Cannot run in Lambda; does not fix IP reputation | D-14 |
| Multi-tenant identity | One shared credential | Assumptions |
| Cross-region replication | Retention is 7 days, not archival | FR-012 |

---

## Resolved: everything the Technical Context asked for

| Technical Context field | Resolution |
|-------------------------|------------|
| Language and version | Python 3.12 |
| Primary dependencies | `yt-dlp==2026.10.26`, pinned static ffmpeg, pinned Deno, `boto3` |
| Storage | S3 for media, DynamoDB for job state and rate windows |
| Testing | pytest, offline unit and contract suites, opt-in e2e |
| Target platform | AWS Lambda container images, arm64, provisioned by Terraform 1.17 and AWS provider 6.x |
| Project type | Single-project serverless service with a terminal interface |
| Performance goals | Acknowledge in under 3 s; 30-minute video downloadable within 10 min; 2 concurrent jobs |
| Constraints | 15-minute invocation, 10 GB `/tmp`, 6 MB payload, 60-minute and 4 GB caps, 60-minute links |
| Scale and scope | One operator, 100 videos per month, 10 submissions per hour, 7-day retention, $10/month ceiling |

No `NEEDS CLARIFICATION` markers remain.
