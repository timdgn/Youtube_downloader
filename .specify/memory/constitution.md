<!--
Sync Impact Report
==================
Version change: (unratified template) -> 1.0.0
Rationale: initial ratification, first concrete constitution for this project.

Modified principles: none (all five applied from the template scaffold)
Added sections:
  - Technical & Legal Constraints
  - Development Workflow & Quality Gates
Removed sections: none

Deferred items: none

Notes for review:
  - RATIFICATION_DATE set to the first adoption date; correct it if the project
    was formally adopted earlier.
  - The Python 3.12 / container image / yt-dlp baseline in "Technical & Legal
    Constraints" is a recommended default, not a user-confirmed decision.
    Amend it before /speckit.plan if a different runtime is chosen.
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

**Rationale**: the infrastructure is the product's only durable asset. Anything
outside Terraform is invisible to review, unreproducible, and impossible to
restore after an incident.

### II. Serverless Within Lambda's Hard Limits

The service runs on AWS Lambda. AWS limits are design inputs, not problems to
discover in production.

- Lambda's 15-minute execution ceiling, its `/tmp` ceiling, and its 6 MB
  synchronous response limit are treated as fixed. Video bytes MUST NEVER be
  returned in an HTTP response body.
- The pipeline is: validated request -> asynchronous job -> media written to S3
  -> short-lived presigned URL returned to the caller. The download client is
  never proxied through the function.
- Work that can exceed a single invocation is split into explicit stages, and
  each stage is idempotent and keyed by a job identifier, so retries never
  duplicate output.
- Runtime dependencies (media extraction tool, media muxer/encoder) are
  vendored inside a container image with exact pinned versions. A silent
  dependency upgrade that breaks extraction is a production outage.
- Timeouts, memory, and reserved concurrency are declared per function, sized
  from observed data, and never left at defaults by accident.

**Rationale**: the Lambda execution model is the constraint that shapes the whole
architecture. Ignoring it produces a design that works only on small test clips.

### III. Least Privilege, No Public Exposure

- Each function gets its own IAM role, scoped to the exact actions and S3
  prefixes it needs. Wildcard actions on `*` resources are prohibited.
- S3 Block Public Access is enabled on every bucket. Objects are reachable only
  through presigned URLs with an expiry of one hour or less.
- Long-lived AWS credentials never appear in code, images, environment
  variables, or logs. Compute identity comes from the execution role; third-party
  secrets come from SSM Parameter Store or Secrets Manager.
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
- Every Lambda handler has a contract test driven by a synthetic event and a
  stubbed AWS client. No test reaches the real YouTube or real AWS.
- The default test suite runs offline and deterministically. Network-dependent
  verification lives in a separate, explicitly invoked end-to-end check against
  a deployed dev stack.
- Input validation is a tested boundary, not a convenience: non-YouTube hosts,
  malformed IDs, playlist URLs, live streams, and over-long videos are rejected
  with a defined error, before any download starts.

**Rationale**: the extraction layer is inherently unstable. Fast offline tests
are the only way to tell "our bug" from "upstream changed".

### V. Observable, Guarded, Cost-Aware Operations

- Structured JSON logs, one line per event, correlated by job identifier. No
  account identifiers, tokens, or full URLs-with-parameters in logs.
- Alarms exist before launch on: function errors, throttles, p95 duration
  approaching the timeout, DLQ depth, and estimated spend. Every alarm has a
  named response action.
- The API is rate limited and concurrency capped, so a single caller cannot
  exhaust the account's limits or budget.
- Cost ceilings are explicit: a maximum permitted video duration/resolution, an
  S3 lifecycle expiry, and a documented monthly budget. Exceeding them is a
  decision, never a surprise.
- Failures are classified (invalid input, unsupported media, transient upstream,
  infrastructure) and surfaced to the caller as distinct, actionable errors.

**Rationale**: a downloader can burn CPU hours and storage silently. Without
guards and alarms the first symptom is the bill.

## Technical & Legal Constraints

**Platform baseline**: AWS Lambda deployed exclusively through Terraform.
Functions are packaged as container images (media tooling and its native
dependencies exceed practical zip limits). Recommended: Python 3.12, `yt-dlp`
pinned to an exact version, and a static `ffmpeg` build, all versioned in the
repository and rebuilt only through a reviewed change.

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

## Development Workflow & Quality Gates

- Work proceeds spec -> plan -> tasks -> implementation. Implementation does not
  start before a reviewed plan passes the Constitution Check in
  `.specify/templates/plan-template.md`.
- Each change states which principles it touches and how it complies. A change
  that violates a principle is either rewritten or recorded as an explicit
  amendment with justification.
- Deployment order is always dev first, then prod. A prod apply requires a dev
  apply of the same commit to have succeeded.
- Rollback is re-applying the previously deployed image tag, not a hotfix
  applied by hand. Image tags are immutable; `latest` is never deployed.
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

**Version**: 1.0.0 | **Ratified**: 2026-11-15 | **Last Amended**: 2026-11-15
