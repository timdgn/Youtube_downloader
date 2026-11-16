# Specification Quality Checklist: YouTube Video Download Service

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-11-15
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- Items marked incomplete require spec updates before `/speckit.clarify` or `/speckit.plan`
- Every ambiguous point (default quality, limits, retention, access control, budget) was resolved with a documented default in Assumptions rather than a clarification marker, so no clarifications are outstanding.
- Operator decision, 2026-11-15: media retention is 7 days (was 24 hours in the first draft), stored in a private per-environment bucket, with the download link lifetime unchanged at 60 minutes and re-issuable while the file exists. This drove FR-006, SC-012, and the retention and storage assumptions.
- Platform choices (serverless functions, infrastructure as code) are mandated by the ratified constitution and intentionally left out of the specification body; see the Assumptions.
- Two values were deliberately deferred to planning and are now settled there (2026-11-15): the output container/codec combination is MP4 with H.264/AAC produced by stream copy, and the monthly budget ceiling is $10 for the SC-011 usage profile. See `../research.md` D-01 and D-02 for the pricing table and the rejected alternatives. No spec change was required; both values are recorded in the plan artifacts, and the spec's "chosen at planning time" and "set at planning time" wording is now satisfied.

## Implementation Verification (2026-11-16)

What was proven, and against what:

| Item | Status | Evidence |
|------|--------|----------|
| Unit and contract suites | PASS | 551 tests: 419 unit, 132 contract, all offline |
| Lint and formatting | PASS | `ruff check` and `ruff format --check` clean over `app/` |
| Terraform validity | PASS | `validate` clean in both roots; both lock files committed |
| Container packaging | PASS | both images built for arm64; handler modules import inside the images |
| Media toolchain with pinned versions | PASS | yt-dlp 2026.10.26, EJS 0.8.0, ffmpeg N-128946, Deno 2.9.8; SHA-256 verified at build |
| Real extraction, no AWS | PASS | the worker image ran `process_job` end to end: probe, select, download, `-c copy` remux, ffprobe, completion record with `h264`/`aac` |
| Provision in `eu-west-3` | PASS | `make bootstrap` then `make deploy ENV=dev`: 38 resources, API answered in 71 to 1753 ms |
| Rejection and auth | PASS | `401` without a token, `400 invalid_input` for a foreign host and a playlist |
| Rate limit | PASS | 10 accepted, 11th `429` in 65 ms with `Retry-After` |
| Failure classification | PASS | a real YouTube refusal surfaced as `upstream_problem`, `retryable: false`, with a complete audit log line |
| Alarms | PASS | five operational alarms in Paris, two budget alarms in `us-east-1`, each pointing at a topic in its own region |
| No drift | PASS | `terraform plan` reports no changes after apply |
| Two environments | PASS | dev and prod ran side by side with separate state keys and distinct API ids |
| Destroy dev, prod survives | PASS | dev API unreachable, dev bucket and cookies gone, prod still answered `401` |
| Destroy prod | PASS | `verify-destroy` reports no surviving resource; only the state bucket remains |
| Successful download (FR-005, SC-004) | PASS | a 19-second video completed in about 30 s; the downloaded file is 635,474 bytes of `h264`+`aac`, 19.06 s, with no credential beyond the URL |
| Quality selection and capping | PASS | one 10.6-minute source at 360p, 720p and a 2160p request: produced 360p (29.0 MB), 720p (161.4 MB), and 1080p (268.5 MB) after the cap; the downloaded 720p file is genuinely 1280x720 |
| Quality fallback | PASS | a 720p request against a 240p-max source completed as 240p, with the produced quality reported |
| Expiry reporting | PASS | deleting one object turned its job into `expired` with no URL, the re-issue route answered `410`, and another job stayed `available` |
| Idempotency (SC-008) | PASS | 100 replays of a completed job left exactly one object and an unchanged size |
| Retention (SC-005) | PASS | eight objects, oldest 0 days; `make verify-retention` passes |
| Concurrency cap (FR-014) | PASS | three simultaneous submissions: never more than two `processing`, the third waited, reserved concurrency 2 |
| Worker duration (SC-002) | PASS | a 10.6-minute 1080p video took 14.9 s of worker time, far below the 600 s alarm |
| Cost ceiling (SC-011) | OPEN | Cost Explorer reported $0.00 on the day of validation; the 100-video profile was not exercised, so $10 remains unvalidated |

**The block, and its resolution.** On the first deployment yt-dlp reached YouTube from
Lambda and was refused in 1 to 7 seconds, classified `upstream_problem` with
`retryable = false`: exactly research D-14's hard block. The first cookies file loaded but
136 of its 160 entries had expired, and `mweb` or `ios` changed nothing. With a fresh
export (23 cookies, none expired), the same job completed on the first attempt. The
remedy is configuration, not code; a stale export is indistinguishable from no export
until you count its expired entries.

**Defects found only by deploying, all fixed and covered by tests:**

- the DynamoDB adapter passed plain Python values to the low-level client, which requires
  AttributeValue maps, so every submission returned 500 while mocked tests stayed green;
- the retry branch used `if_not_exists` inside a `ConditionExpression`, which DynamoDB
  rejects;
- switching `ENV` reused the previous backend, so the prod apply destroyed the dev stack
  and adopted the prod names into dev's state file; `-reconfigure` is now unconditional;
- the budget alarms pointed at a Paris SNS topic, but a CloudWatch alarm can only act on a
  topic in its own region, so they now have a `us-east-1` topic;
- buildx pushed an image index with attestations, which Lambda rejects as an unsupported
  manifest; provenance and SBOM are now disabled for the Lambda images;
- the S3 client signed presigned URLs for the region but emitted the global
  `s3.amazonaws.com` host, so S3 rejected every download with
  `AuthorizationQueryParametersError`; both builders now pin the regional endpoint.


