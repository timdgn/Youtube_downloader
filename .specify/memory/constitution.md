<!--
Sync Impact Report
==================
Version change: 1.1.0 -> 2.0.0
Rationale: production compute moved to a single Linux host running Docker (a
Raspberry Pi) while AWS keeps media, job records, the queue and the secrets.
Two principles assumed Lambda-only compute and an execution-role credential:
II (serverless within Lambda's limits) and III (no long-lived credential outside
an execution role). Both are redefined rather than silently contradicted. The
dev-before-prod gate is given a definition for a target that has no dev twin.

Modified principles: II (compute is a stateless container with declared limits;
Lambda is one supported target), III (a scoped host user is permitted under
stated controls)
Added sections: Deployment Targets
Removed sections: none
Deferred items: deleting the dormant Lambda functions and API Gateway once the
host target has completed one full media-retention cycle in production.

History: 1.0.0 initial ratification on 2026-11-15; 1.1.0 made cost ceilings
guardrails rather than test targets; 2.0.0 added the host deployment target.
-->

# YouTube Download Service Constitution

## Core Principles

### I. Infrastructure as Code Only (Terraform)

Every AWS resource in this project MUST be declared in Terraform. No resource
created, modified, or deleted by hand in the AWS console, CLI, or SDK. Human
actions in the console are limited to read-only inspection.

- State lives in a remote backend with state locking enabled; local state MUST
  NOT be committed.
- `terraform plan` is reviewed before any `terraform apply`. Apply is never run
  blind, and never run from CI on an unreviewed change.
- Provider, module, and lock-file versions are pinned; upgrades are separate,
  reviewable changes.
- The stack is parameterised so `dev` and `prod` are the same code path with
  different variables. Environment-specific logic MUST NOT be branched in code.
- `terraform destroy` on an environment MUST remove the whole environment. A
  resource that survives destroy is a defect.
- IAM access keys are the one exception, and only because Terraform would store
  the secret in state: the user and its policy are declared in Terraform, and
  the key itself is created by the documented CLI procedure in the runbook, held
  only on the host, and rotated there.

**Rationale**: the infrastructure is the product's only durable asset. Anything
outside Terraform is invisible to review, unreproducible, and impossible to
restore after an incident.

### II. Stateless Compute Within Declared Limits

The service's compute is a container image built from this repository. Two
targets are supported and both keep the same contract: AWS Lambda, the default
for an AWS-only account, and a single Linux host running Docker. Production runs
on the host.

- Video bytes MUST NEVER be returned in an HTTP response body.
- The pipeline is: validated request -> asynchronous job -> media written to S3
  -> short-lived presigned URL returned to the caller. The download client is
  never proxied through the process that accepted the request.
- Work that can exceed a single invocation is split into explicit stages, and
  each stage is idempotent and keyed by a job identifier, so retries never
  duplicate output.
- Runtime dependencies (media extraction tool, media muxer/encoder) are
  vendored inside a container image with exact pinned versions. A silent
  dependency upgrade that breaks extraction is a production outage.
- Every execution limit is declared and sized from observed data: Lambda's
  15-minute, `/tmp` and 6 MB response ceilings where Lambda runs the code, and
  the pipeline deadline plus the queue's visibility timeout where a host runs it.
- A host target MUST expose the same routes and the same queue contract as the
  Lambda target, so switching targets changes where the bytes are processed and
  nothing about behaviour.

**Rationale**: the execution model is the constraint that shapes the whole
architecture. It used to be Lambda's alone; it is now a property of the target,
and every target states its ceilings instead of discovering them in production.

### III. Least Privilege, No Public Exposure

- Each function gets its own IAM role, scoped to the exact actions and S3
  prefixes it needs. Wildcard actions on `*` resources are prohibited.
- S3 Block Public Access is enabled on every bucket. Objects are reachable only
  through presigned URLs with an expiry of one hour or less.
- Long-lived AWS credentials never appear in code, images, environment
  variables, or logs. On Lambda, compute identity comes from the execution role.
  On a host without instance metadata, a dedicated IAM user whose policy is no
  broader than the roles it replaces is permitted; its key lives only in a
  mode-0600 file owned by the service account, is never committed, logged or
  baked into an image, and is rotated by the procedure in the runbook.
  Third-party secrets come from SSM Parameter Store or Secrets Manager.
- All data at rest is encrypted with customer-managed or AWS-managed keys, and
  buckets have a lifecycle rule that expires artefacts automatically.
- Every resource carries `Project` and `Environment` tags, so cost and ownership
  are attributable.

**Rationale**: an open bucket and an over-broad role are the two mistakes that
turn a utility project into a data-loss and cost incident.

### IV. Test-First for Core Logic, Contract-Tested Handlers

- Pure logic (URL validation and normalisation, format/quality selection, S3 key
  derivation, job state transitions) is unit tested before implementation. A
  failing test precedes each behaviour.
- Every entry point has a contract test driven by a synthetic event and a
  stubbed AWS client. No test reaches the real YouTube or real AWS.
- The default test suite runs offline and deterministically. Network-dependent
  verification lives in a separate, explicitly invoked end-to-end check against
  a deployed stack.
- Input validation is a tested boundary, not a convenience: non-YouTube hosts,
  malformed IDs, playlist URLs, live streams, and over-long videos are rejected
  with a defined error, before any download starts.

**Rationale**: the extraction layer is inherently unstable. Fast offline tests
are the only way to tell "our bug" from "upstream changed".

### V. Observable, Guarded, Cost-Aware Operations

- Structured JSON logs, one line per event, correlated by job identifier. No
  account identifiers, tokens, or full URLs-with-parameters in logs.
- Alarms exist before launch on: errors, throttles, duration approaching the
  deadline, DLQ depth, and estimated spend. Every alarm has a named response
  action.
- The API is rate limited and concurrency capped, so a single caller cannot
  exhaust the account's limits or budget.
- Cost ceilings are explicit: a maximum permitted video duration/resolution, an
  S3 lifecycle expiry, and a documented monthly budget. Exceeding them is a
  decision, never a surprise.
- A cost ceiling is a guardrail, never a test target. Nothing in this project
  generates load, synthetic usage, or sustained activity to approach, exercise,
  or validate a ceiling. A ceiling is justified by a pricing model reviewed in
  the plan, and real spend is read from billing data, never induced to prove it
  works. A ceiling that has been reached on purpose has already failed at the
  one job it had.
- Failures are classified (invalid input, unsupported media, transient upstream,
  infrastructure) and surfaced to the caller as distinct, actionable errors.

**Rationale**: a downloader can burn CPU hours and storage silently. Without
guards and alarms the first symptom is the bill.

## Technical & Legal Constraints

**Platform baseline**: a container image built from one Dockerfile, deployed
either as AWS Lambda functions through Terraform or as Docker services on a
Linux host. Python 3.12 or newer, `yt-dlp` pinned to an exact version and a
static `ffmpeg` build, all versioned in the repository and rebuilt only through
a reviewed change.

**Interface**: a single entry point accepts a YouTube URL and optional quality
preference. It validates, enqueues, and returns a job handle immediately. A
status endpoint reports job state and, when complete, the presigned download
URL.

**Storage**: one bucket per environment, split by prefix (`incoming/`,
`output/`, `tmp/`), lifecycle-expired. No object is retained indefinitely.

**Acceptable use**: this is a personal tool for content the operator is entitled
to download. It MUST NOT be exposed as a public, general-purpose ripping
service, MUST NOT bypass DRM or paid access, and MUST NOT be used to
redistribute copyrighted material. Where required, the caller acknowledges this
before a job is accepted. Note that downloading YouTube content generally
conflicts with YouTube's Terms of Service regardless of copyright status; this
constraint is a risk to be accepted knowingly by the operator, not a risk
eliminated by code.

**Licensing**: the bundled media tooling carries copyleft obligations. Its
licence is recorded in the repository, and the container image is not
redistributed outside this project without review.

## Deployment Targets

**Lambda.** Terraform declares every function, role, queue, bucket and alarm.
Images are immutable and tagged with the commit that produced them.

**Host (Docker).** One image, two services: an HTTP front end serving the
terminal API, the Telegram webhook and the worker's terminal-event hook, and a
queue consumer running the same pipeline.

- AWS keeps media, job records, the queue and secrets. The host holds no durable
  state except its scratch directory, which lives on a disk that is not the boot
  device when one exists.
- The queue has exactly ONE consumer. A Lambda event source mapping and a host
  poller MUST NOT both be enabled; the Terraform switch that decides this is the
  only supported way to move it.
- The host is reachable only through the reverse proxy and tunnel already on the
  machine; no inbound port is opened for this service.
- The image tag names the commit it was built from. Rollback is reloading the
  previous tag and recreating the containers.
- The host's free disk space is an operational constraint with an alarm-free
  guard: the deployment records the floor below which an existing service on
  the machine refuses to start, and keeps headroom above it.

## Development Workflow & Quality Gates

- Work proceeds spec -> plan -> tasks -> implementation. Implementation does not
  start before a reviewed plan passes the Constitution Check in
  `.specify/templates/plan-template.md`.
- Each change states which principles it touches and how it complies. A change
  that violates a principle is either rewritten or recorded as an explicit
  amendment with justification.
- Deployment order is dev first, then prod, for every target that has both
  environments. A prod apply requires a dev apply of the same commit to have
  succeeded. A single-instance target with no dev twin (the host deployment)
  instead requires a live end-to-end verification of every route after
  deployment, plus a documented rollback to the previous image tag; the absence
  of a dev twin is stated in the change.
- Rollback is re-applying or reloading the previously deployed image tag, not a
  hotfix applied by hand. Image tags are immutable; `latest` is never deployed.
- Infrastructure changes and application changes ship in separate commits so a
  failure is attributable.
- Documentation is part of "done": the endpoint contract, the environment
  variables, and the operational runbook (how to read logs, how to respond to
  each alarm) are updated in the same change.

## Governance

This constitution supersedes ad-hoc practice. When it conflicts with a
convenience, the constitution wins or the constitution is amended; the two are
not silently reconciled in code.

**Amendments**: proposed as a change to this file with the reasoning, the
affected principles, and the version bump it implies. Amendments are reviewed
before merge. Versioning follows semantic versioning: MAJOR for removal or
redefinition of a principle, MINOR for a new principle or materially expanded
guidance, PATCH for wording and clarification.

**Compliance review**: every plan and every pull request verifies compliance
with the principles above. Non-compliance is recorded with a justification and a
migration path, or the change is rejected. Complexity that cannot be traced to a
principle or a stated requirement is removed rather than justified.

**Runtime guidance**: agent and developer instructions in `AGENTS.md` (when
present) must not contradict this document; conflicts are resolved in favour of
this constitution.

**Version**: 2.0.0 | **Ratified**: 2026-11-15 | **Last Amended**: 2026-11-17
