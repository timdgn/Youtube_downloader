# Tasks: YouTube Video Download Service

**Input**: Design documents from `specs/001-youtube-video-download/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/api.md](./contracts/api.md),
[contracts/job-message.md](./contracts/job-message.md), [quickstart.md](./quickstart.md),
`.specify/memory/constitution.md` v1.0.0

**Tests**: Included, not optional here. Constitution principle IV (ratified in
`.specify/memory/constitution.md`) requires unit tests for pure logic and contract tests
for every handler, and research D-18 fixes the layering. Every test task precedes its
implementation task and MUST fail before that implementation exists.

**Organization**: Tasks are grouped by user story so each story is a testable increment.
Story priorities come from `spec.md`: US1 (P1), US2 (P2), US3 (P3), US4 (P4).

**Path conventions**: single project, no `backend/` or `frontend/` split. The service
lives in `app/`, Terraform in `infra/`, operational documentation in `docs/`, and the
deploy entry point is the root `Makefile`. Paths below are repo-relative.

**Scheduling note**: research D-02 and D-14 and the plan's "Next Step" put two items
before everything else, because they can invalidate later work: the worker image proven
to extract a real video (T009), and the budget alarms wired before any production use
(T037). Both sit in Phase 2.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Repository, Python project, container build, and test harness scaffolding.

- [X] T001 Initialize the git repository and write `.gitignore` covering `.terraform/`, `*.tfstate`, `*.tfstate.*`, `*.tfplan`, `.env`, `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.ruff_cache/`, `*.mp4`, `agent_backup/`, and `.DS_Store`; confirm `.terraform.lock.hcl` is NOT ignored, because constitution principle I.3 requires it committed
- [X] T002 [P] Write `app/pyproject.toml` with `requires-python = ">=3.12"`, the `ruff` configuration (line length 100, target py312), and `[tool.pytest.ini_options]` setting `testpaths = ["tests"]`, `pythonpath = ["src"]`, `addopts = "-q --strict-markers"`, and `markers = ["e2e: drives a deployed stack; skipped by default"]`
- [X] T003 [P] Write `app/requirements.txt` with fully pinned runtime dependencies (`boto3`, `botocore`) and `app/requirements-dev.txt` with fully pinned test tooling (`pytest`, `pytest-cov`, `ruff`); no floating or minimum-only version specifiers
- [X] T004 [P] Write `app/THIRD-PARTY-NOTICES.md` recording yt-dlp (Unlicense), the ffmpeg GPL build (installed at image build, not redistributed), Deno (MIT), and boto3 (Apache-2.0), plus the personal-use, no-redistribution note required by constitution constraint C.3
- [X] T005 [P] Write `app/.dockerignore` excluding `tests/`, `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `*.mp4`, and any `*.tfstate` file, so the build context stays small
- [X] T006 Write `app/Dockerfile` as one multi-stage build with two final targets, `api` and `worker`, both `FROM public.ecr.aws/lambda/python:3.12`, setting `PYTHONPATH=/var/task/src` and installing `requirements.txt` into `${LAMBDA_TASK_ROOT}`; the `worker` target adds a pinned static ffmpeg build and a pinned Deno release, each verified with `sha256sum -c` against a digest pinned in an `ARG`, plus `yt-dlp==2026.10.26`; the `api` target adds none of that media tooling
- [X] T007 [P] Create the package skeleton with empty `__init__.py` files at `app/src/ytdl/`, `app/src/ytdl/api/`, `app/src/ytdl/worker/`, and `app/src/ytdl/adapters/`, plus `app/tests/__init__.py`, `app/tests/unit/__init__.py`, `app/tests/contract/__init__.py`, `app/tests/fixtures/__init__.py`, and `app/tests/e2e/__init__.py`
- [X] T008 [P] Write the `Makefile` skeleton with targets `image-api`, `image-worker`, `image` (both, `docker buildx build --platform linux/arm64 --load`, tag `$(IMAGE_TAG)`), `test`, `test-unit`, `test-contract`, `test-e2e`, and `lint`; `IMAGE_TAG` defaults to `$(shell git rev-parse --short=12 HEAD)` so an inexact tag cannot be produced; `deploy`, `destroy`, `tf-check`, and `verify-image` are declared in later tasks

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Pure logic, adapters, and Terraform that every story depends on. Nothing in
Phase 3 or later can run until this phase is green.

**Critical**: the first full `terraform apply` does not happen here. Bootstrap is applied
and verified here (T040); the application stack is first applied at T056, once handlers
exist to serve the image.

### Risk-first checks

- [X] T009 Add a `verify-image` target to `Makefile` that runs the `worker` image with `--platform linux/arm64` and executes, inside it, `yt-dlp --version`, `ffmpeg -version`, `deno --version`, then one real download at 720p with `--merge-output-format mp4` and an `ffprobe` assertion that the result contains `h264` and `aac` streams; run it against a short public video. Pass condition: a playable MP4 with both streams. If YouTube refuses, capture the exact stderr in the task notes and apply the cookies or proxy hooks from research D-14 before continuing

### Core logic (tests first, then implementation)

- [ ] T010 [P] Write `app/tests/unit/test_config.py` asserting that missing required environment variables fail fast with a named error, that the defaults from the Environment table in `data-model.md` apply (`max_height` 1080, `max_duration_seconds` 3600, `max_output_bytes` 4294967296, `retention_days` 7, `link_ttl_seconds` 3600, `rate_limit_per_hour` 10, `worker_ephemeral_storage_mb` 10240), and that numeric values are parsed as integers, not strings
- [ ] T011 [P] Write `app/tests/unit/test_logging.py` asserting one JSON object per call on one line, that the field allowlist drops unknown keys, that a token-like or URL-with-query value passed under a non-allowlisted name never appears in output, and that the Embedded Metric Format block carries namespace `Ytdl/<env>` with dimensions `Environment` and `Function`
- [ ] T012 [P] Write `app/tests/unit/test_ids.py` asserting a 14-character output (base-36 millisecond timestamp plus 10 random characters), that two ids generated in the same millisecond differ, that output sorts lexicographically in generation order, and that the alphabet is `[a-z0-9]`
- [ ] T013 [P] Write `app/tests/unit/test_states.py` asserting every legal transition in the `data-model.md` table is accepted, every illegal one is refused (`completed` to anything, `failed` to `processing` without `failure_retryable` true, `failed` to `processing` at `attempt` 2), and that the stale-claim rule only fires when `updated_at` is older than 900 s
- [ ] T014 [P] Write `app/tests/unit/test_keys.py` asserting `output/<job_id>/<video_id>.mp4` is deterministic for the same inputs, that the rate key is `RATE#<window_start_epoch>#<fingerprint>`, that the fingerprint is the first 16 hex characters of the SHA-256 of the token and is stable, and that no key ever contains the raw token
- [ ] T015 [P] Write `app/tests/unit/test_auth.py` asserting a correct bearer token is accepted, and that missing, malformed (`Basic`, empty, multiple spaces), and wrong tokens are all rejected with one indistinguishable result; assert the comparison goes through `hmac.compare_digest` and that the SSM read happens once per process, not once per request
- [ ] T016 [P] Implement `app/src/ytdl/config.py` as a frozen dataclass built once from validated environment variables, with the defaults from `data-model.md` and no I/O
- [ ] T017 [P] Implement `app/src/ytdl/logging.py` with a JSON formatter, a fixed field allowlist (per `data-model.md` Audit Entry), an `emit_metric` helper producing Embedded Metric Format, and no `print` fallback
- [ ] T018 [P] Implement `app/src/ytdl/ids.py` with the time-ordered generator described in `data-model.md` (`job_id` is opaque, not enumerable, not sequential)
- [ ] T019 [P] Implement `app/src/ytdl/states.py` as a pure transition table returning allow or refuse plus the conditional-write expression the DynamoDB adapter needs
- [ ] T020 [P] Implement `app/src/ytdl/keys.py` with `media_key(job_id, video_id)`, `config_key(name)`, `rate_window(now)`, and `credential_fingerprint(token)`
- [ ] T021 [P] Implement `app/src/ytdl/auth.py` reading `/ytdl/<env>/api-token` from SSM once per cold start, caching it in a module-level variable, and comparing with `hmac.compare_digest`

### Adapters and fakes

- [ ] T022 [P] Write `app/tests/fakes/` with `dynamo_fake.py`, `s3_fake.py`, `sqs_fake.py`, `ssm_fake.py`, and `extractor_fake.py`, each implementing the same narrow method set as its real adapter and recording every call, so contract tests can assert both behaviour and call counts; add a `conftest.py` fixture per fake
- [ ] T023 Implement `app/src/ytdl/adapters/dynamo.py`: `put_job_with_rate_increment` using one `TransactWriteItems` with a `ConditionCheck` of `attribute_not_exists(count) OR count < :limit` on the rate item, `get_job`, `claim_job` (conditional on `state` and, for a stale claim, on `updated_at`), `complete_job`, and `fail_job`, with `ttl` written on every item (`window_start + 7200` for rate items, `media_expires_at + 30 days` for jobs)
- [ ] T024 Implement `app/src/ytdl/adapters/s3.py`: `put_file`, `head_object` returning `None` on 404, and `presign_get` producing `ExpiresIn = link_ttl_seconds` (3600), `ResponseContentDisposition = attachment; filename="<video_id>-<job_id>.mp4"`, and `ResponseContentType = video/mp4`
- [ ] T025 Implement `app/src/ytdl/adapters/sqs.py`: `enqueue` writing exactly the body in `contracts/job-message.md` (`version` 1, `job_id`, `video_id`, `source_host`, `requested_height`, `enqueued_at`) with no message attributes
- [ ] T026 Implement `app/src/ytdl/adapters/extractor.py`: run yt-dlp as a subprocess with `subprocess.run(..., timeout=...)`, never `shell=True`; capture stderr to a file in `/tmp`; expose `probe(url)` for metadata-only JSON and `download(url, format_selector, out_dir)`; pass `--no-playlist`, the cookies and proxy options when configured, and a hard kill on deadline

### Terraform (all resources declared, none created by hand)

- [ ] T027 [P] Write `infra/bootstrap/versions.tf` and `infra/bootstrap/main.tf`: one S3 bucket for state with versioning enabled, SSE-S3, full Block Public Access, and `Project`/`Environment` tags, taking `state_bucket` as a variable; this root keeps local state because it cannot be managed by the state it holds
- [ ] T028 [P] Write `infra/versions.tf` (`required_version >= 1.10`, `hashicorp/aws ~> 6.0`, `hashicorp/random ~> 3.6`) and `infra/providers.tf` (default provider plus a `us_east_1` alias for billing alarms)
- [ ] T029 [P] Write `infra/backend.tf` with a partial S3 backend (`use_lockfile = true`, encrypt on) and `infra/backend/dev.backend` selecting key `env/dev/terraform.tfstate`
- [ ] T030 [P] Write `infra/variables.tf` declaring every row of the Environment table in `data-model.md`, typed, with `validation` blocks for ranges (`max_height` in 144..2160, `max_duration_seconds` > 0, `retention_days` >= 1, `link_ttl_seconds` in 60..3600, `rate_limit_per_hour` >= 1, `worker_memory_mb` in 128..10240, `worker_timeout_seconds` in 1..900, `worker_ephemeral_storage_mb` in 512..10240)
- [ ] T031 [P] Write `infra/locals.tf` with the naming convention `ytdl-<environment>-<component>` and the mandatory tag set (`Project = "ytdl"`, `Environment = var.environment`, `ManagedBy = "terraform"`)
- [ ] T032 Write `infra/storage.tf`: the media bucket `ytdl-<account_id>-<environment>-media` with `force_destroy = true` so destroy leaves nothing, SSE-S3 default encryption, Block Public Access on all four flags, `BucketOwnerEnforced` ownership, a TLS-only bucket policy, a lifecycle rule expiring `output/` after `var.retention_days` and aborting incomplete multipart uploads after 1 day, and no policy granting public or cross-account access
- [ ] T033 Write `infra/state.tf`: the DynamoDB table `ytdl-<environment>-jobs` (on-demand, partition key `pk` type S, TTL on `ttl`), the standard queue plus dead-letter queue (`maxReceiveCount = 2`, DLQ retention 14 days), and the SSM `SecureString` at `/ytdl/<environment>/api-token` produced by `random_password` with 48 characters
- [ ] T034 Write `infra/functions.tf`: the ECR repository `ytdl-<environment>-app` with `image_tag_mutability = "IMMUTABLE"` and `scan_on_push`, the `api` execution role (table read/write, queue send, `s3:GetObject` on `output/*`, `ssm:GetParameter` on the token parameter), the `worker` role (table read/write, `s3:GetObject`/`PutObject` on `output/*`, `s3:GetObject` on `config/*`, `ssm:GetParameter` on the token and proxy parameters, queue receive/delete/change-visibility), log groups with `retention_in_days = var.log_retention_days`, both `aws_lambda_function` resources (`arm64`, `image_uri = "<repo>:<image_tag>-api"` and `-worker`, `image_config.command` = `["ytdl.api.handler.handler"]` and `["ytdl.worker.handler.handler"]`, api 512 MB / 15 s / reserved 5, worker `var.worker_memory_mb` / `var.worker_timeout_seconds` / `ephemeral_storage.size = var.worker_ephemeral_storage_mb` / reserved 2), and the event source mapping with `batch_size = 1`, `maximum_batching_window_in_seconds = 0`, `function_response_types = ["ReportBatchItemFailures"]`, `scaling_config.maximum_concurrency = 2`, and `visibility_timeout = 960`
- [ ] T035 Write `infra/api.tf`: the HTTP API with the `$default` stage (`auto_deploy = true`), `default_route_settings` throttling as a backstop (`rate_limit = 5`, `burst_limit = 20`), the three routes `POST /v1/jobs`, `GET /v1/jobs/{job_id}`, `POST /v1/jobs/{job_id}/link`, their Lambda proxy integrations, and an access log format containing only `requestId`, `routeKey`, `status`, `responseLength`, `integrationStatus`, `sourceIp`, and `requestTime` (no query string, no headers, per FR-015)
- [ ] T036 Write `infra/outputs.tf` exposing `api_base_url`, `media_bucket`, `api_function_name`, `worker_function_name`, `token_parameter_name`, `queue_url`, and `alarm_topic_arn`
- [ ] T037 Write `infra/alarms.tf`: one SNS topic `ytdl-<environment>-alarms` with an optional email subscription when `var.alarm_email` is non-empty, then the seven alarm conditions enumerated in research D-17 (five operational: API `5xx` above 0, worker `Errors` above 0, worker `Throttles` above 0, worker `Duration` p95 above 600000 ms, DLQ `ApproximateNumberOfMessagesVisible` above 0; two budget: `AWS/Billing EstimatedCharges` above 5 and above 10 USD, both created with the `us_east_1` provider alias)
- [ ] T038 [P] Write `infra/env/dev.tfvars` with `environment = "dev"`, `aws_region = "eu-west-1"`, the documented defaults from `data-model.md`, and `alarm_email` left empty unless the operator supplies one
- [X] T039 Add `deploy`, `destroy`, and `tf-check` targets to `Makefile`: `tf-check` runs `terraform fmt -check -recursive infra/` and `terraform validate` in both roots; `deploy` runs `terraform init -backend-config=backend/dev.backend`, then `terraform apply -target=aws_ecr_repository.app` (the single documented circular-dependency break from research D-16), then builds and pushes both image tags, then `terraform apply -var image_tag=<tag>`; `destroy` runs `terraform destroy -var image_tag=<tag>` for the selected environment
- [ ] T040 Apply `infra/bootstrap` with `state_bucket = ytdl-tfstate-<account-id>` and verify `aws s3api get-bucket-versioning` reports `Enabled` and Block Public Access is fully on; then run `make tf-check` and confirm both Terraform roots are formatted and valid

**Checkpoint**: Pure logic and adapters are unit tested, Terraform is valid and the state bucket exists. Application deploy is blocked until T056.

---

## Phase 3: User Story 1 - Submit a video link and receive the finished file (Priority: P1) MVP

**Goal**: A URL goes in, a job id comes back in under 3 seconds, the worker fetches and
remuxes the video in the background, and the job returns a 60-minute presigned link whose
file plays with audio and video in sync.

**Independent Test**: from a fresh terminal, submit one short public video, poll until
`completed`, follow the link, and confirm the file plays with both streams. Additionally
submit a foreign host, a playlist, and a request with no token, and confirm immediate
rejection with no worker invocation.

**Why the rate limit is here**: FR-014 and SC-010 have no other owning story. The
transactional counter is part of accepting a submission, so it ships with the acceptance
endpoint rather than in a later phase.

### Tests for User Story 1

> Write these first. They must fail before T049 onwards.

- [X] T041 [P] [US1] Write `app/tests/unit/test_urls.py` covering the accepted forms (`watch?v=<11 chars>`, `youtu.be/<id>`, `/shorts/<id>`, `/embed/<id>`, tracking parameters, `t=`/`start=` markers, and a playlist URL that also carries a `v=` parameter, which is treated as that single video per the documented edge-case choice) and the rejected forms (foreign hosts, 10 and 12 character ids, illegal id characters, `/playlist?list=` without `v=`, `/channel/`, `/c/`, `/@handle`, `/live/`, empty, and 2049 characters)
- [X] T042 [P] [US1] Write `app/tests/unit/test_rate_window.py` asserting the window start is the current hour boundary in UTC, the key is stable inside one hour and changes at the next, the TTL is `window_start + 7200`, and the 11th request in a window is refused while the 10th is accepted
- [X] T043 [P] [US1] Write `app/tests/unit/test_link.py` asserting the issued expiry is `now + link_ttl_seconds` (3600), the download filename is `<video_id>-<job_id>.mp4` and never the video title, and that `result` is `null` for `accepted` and `processing` states
- [X] T044 [P] [US1] Write `app/tests/contract/test_post_jobs.py` driving the API handler with API Gateway v2 events and fakes: `202` body exactly as `contracts/api.md` (job id, `state: accepted`, `created_at`, `status_url`), `400 invalid_input` for the rejection set, `401 unauthorized` for missing and wrong tokens, `429 rate_limited` with a `Retry-After` header and `retry_after` in the envelope, `500 internal_error` on an SQS failure; assert no job item is written on `400`, `401`, or `429`, and that every response body is under 4 KB
- [X] T045 [P] [US1] Write `app/tests/contract/test_get_job.py` covering `accepted`, `processing`, and `completed` with `result.status: available` and a presigned URL, plus `401`, `404` for an unknown id, and the read-only guarantee (the fakes record zero writes)
- [X] T046 [P] [US1] Write `app/tests/contract/test_reissue.py` covering `200` with a fresh URL and expiry, `404` for an unknown id, and `409 not_completed` for `accepted` and `processing`; assert the job item is unchanged and `attempt` and `updated_at` do not move
- [X] T047 [P] [US1] Write `app/tests/contract/test_worker_happy.py` driving the worker handler with one SQS record and fakes: claim to `processing`, probe, download, remux, upload to `output/<job_id>/<video_id>.mp4`, complete, and return `{"batchItemFailures": []}`; assert the media key and that `media_expires_at` is `media_created_at + 604800`
- [X] T048 [P] [US1] Write `app/tests/e2e/test_download.py` marked `e2e`, reading `API_BASE_URL` and `API_TOKEN` from the environment, submitting a real short video, polling to `completed`, downloading, and asserting the file is a non-empty MP4 whose `ffprobe` output contains `h264` and `aac`

### Implementation for User Story 1

- [X] T049 [P] [US1] Implement `app/src/ytdl/urls.py`: normalise short, `shorts`, `embed`, and parameter-laden links to `(source_host, video_id)`; enforce the `^[A-Za-z0-9_-]{11}$` identifier rule from `data-model.md`; reject foreign hosts, collections, channels, and live paths with a distinct reason string
- [X] T050 [P] [US1] Implement `app/src/ytdl/api/routes.py` with the three route shapes from `contracts/api.md`, the single error envelope, deterministic JSON serialisation, and the `status_url` field; no I/O in this module
- [X] T051 [US1] Implement `app/src/ytdl/api/handler.py`: parse API Gateway v2 events, resolve the route, enforce the bearer token via `auth.py`, validate input, compute the rate window, call `put_job_with_rate_increment`, enqueue, and map every exception onto the fixed error envelope; no stack traces and no query strings in any response or log
- [X] T052 [US1] Implement `app/src/ytdl/worker/remux.py`: one ffmpeg invocation with `-c copy`, MP4 output, and `-movflags +faststart`; assert the output exists and is non-zero before returning; never re-encode (research D-01)
- [X] T053 [US1] Implement `app/src/ytdl/worker/pipeline.py` for the happy path: claim the job, probe metadata, download to `/tmp`, remux, upload with `Content-Type: video/mp4` and the `job-id`, `produced-height`, `source-video-id` metadata, then `complete_job` with `media_size_bytes`, `media_created_at`, `media_expires_at`, and `processing_ms`
- [X] T054 [US1] Implement `app/src/ytdl/worker/handler.py`: SQS batch of 1, re-read the job from DynamoDB by `job_id` rather than trusting the message body, call the pipeline, and return `batchItemFailures` only for a retryable failure
- [X] T055 [US1] Wire delivery in `app/src/ytdl/api/routes.py` and `app/src/ytdl/api/handler.py`: on `completed`, `head_object` the media key, and only then presign with `ExpiresIn = 3600`; return `result.status: available` with `download_url`, `expires_at`, `size_bytes`, and `content_type`; re-issue uses the same signing path
- [ ] T056 [US1] Run `make deploy ENV=dev`, confirm `terraform output api_base_url` and `media_bucket`, then run `terraform plan` again with the same `-var image_tag` and confirm it reports no changes (constitution principle I)
- [ ] T057 [US1] Run the deployed path by hand per `quickstart.md` steps 3 to 6: submit a short public video, poll, download, and confirm the file plays, is `video/mp4`, and its size equals `result.size_bytes`
- [ ] T058 [US1] Run the rejection drill from `quickstart.md` step 8 (foreign host, playlist, missing token) and confirm no job item was created and the worker logged no invocation for those attempts
- [ ] T059 [US1] Measure acceptance latency with `curl -w '%{time_total}'` over 10 submissions and confirm every sample is under 3 seconds (FR-003, SC-001); record the samples in the phase notes

**Checkpoint**: User Story 1 is deployable and independently demonstrable. This is the MVP.

---

## Phase 4: User Story 2 - See what a job is doing, and why it failed (Priority: P2)

**Goal**: Every job reports an honest state, and every failure carries one of the five
fixed categories plus a retryable flag, so the operator can tell a bad link from a broken
system. Expired media is reported as expired, never as a dead link.

**Independent Test**: submit one video that will fail and one that will succeed, poll
both, and confirm the failure carries a specific category and message with no link, while
the success shows the expected transitions. Delete a completed job's object and confirm
the job reports `expired` and the re-issue route answers `410`.

### Tests for User Story 2

- [X] T060 [P] [US2] Write `app/tests/unit/test_errors.py` asserting the mapping from captured stderr fixtures to the five categories in `contracts/api.md` and research D-13, that `invalid_input` and `unsupported_media` and `limit_exceeded` are never retryable, that throttling and network resets are retryable, and that a "Sign in to confirm you're not a bot" or hard 403 signature maps to `upstream_problem` with `retryable = false` per research D-14; unknown stderr maps to `internal_error`
- [X] T061 [P] [US2] Write `app/tests/unit/test_limits.py` asserting duration 3600 is accepted and 3601 is refused as `limit_exceeded`, size 4294967296 is accepted and one byte more is refused, that both decisions are made from metadata alone with no media transfer, and that missing duration metadata is refused as `unsupported_media` rather than guessed at
- [X] T062 [P] [US2] Write `app/tests/unit/test_status_view.py` asserting `result.status` is `expired` when `now` is past `media_expires_at` or the object is absent, `available` only when both conditions hold, that a `failed` job exposes `failure.category`, `failure.retryable`, and a message, and that repeated calls over the same record produce identical output
- [X] T063 [P] [US2] Write `app/tests/contract/test_get_job_failures.py` covering all five failure categories in `GET /v1/jobs/{job_id}`, the expired result with `download_url: null`, `410 result_expired` from the re-issue route, and `404` versus `410` disambiguation
- [X] T064 [P] [US2] Write `app/tests/contract/test_worker_failures.py` covering the consumption rules in `contracts/job-message.md`: retryable failure records the category and returns the message id in `batchItemFailures`; terminal failure records and returns an empty list; unknown `version` is terminal `internal_error`; a missing job record and an already `completed` job are acknowledged and dropped; an existing non-zero object causes the download to be skipped; and a second delivery at `attempt > 1` with an existing object produces no second write
- [X] T065 [US2] Implement `app/src/ytdl/errors.py` as pure functions from stderr and exception type to `(category, retryable, message)`, with the fixed message table and no URL or stack trace in any message
- [X] T066 [US2] Implement `app/src/ytdl/limits.py` as pure evaluators for duration and size, both returning accept or `limit_exceeded` before any transfer
- [X] T067 [US2] Add the failure path to `app/src/ytdl/worker/pipeline.py`: enforce `limits.py` right after the probe, apply a soft deadline of 780 seconds that aborts with a classified failure before Lambda's 900-second ceiling, catch every exception into `errors.py`, call `fail_job` with category, message, and retryable, and leave the stored object untouched on a retry
- [X] T068 [US2] Add the redelivery and idempotency rules from `contracts/job-message.md` to `app/src/ytdl/worker/handler.py`, including stale-claim recovery via the `states.py` guard and a `head_object` pre-check that skips extraction when the object already exists with non-zero size
- [X] T069 [US2] Add expired-result handling and the `410`/`409` distinction to `app/src/ytdl/api/routes.py` and `app/src/ytdl/api/handler.py`, using `media_expires_at` plus the `head_object` result, with an instruction to resubmit and no dead URL
- [X] T070 [US2] Extend `app/src/ytdl/adapters/extractor.py` to pass the three optional D-14 hooks: the cookies file fetched from `config/cookies.txt` when `ytdlp_cookies_s3_uri` is set, the proxy from SSM when `ytdlp_proxy_param` is set, and a `--extractor-args youtube:player_client=<value>` override when configured; all three default to empty and add no flags
- [X] T071 [US2] Emit the audit entry required by FR-015 from `app/src/ytdl/worker/pipeline.py` on both outcomes: `job_id`, `source_host`, `video_id`, `requested_quality`, `produced_quality`, `outcome`, `processing_ms`, `media_size_bytes` on success, `attempt`, and no token, cookie, header, or URL with a query string
- [ ] T072 [US2] Run the failure drill from `quickstart.md` step 9 against dev with a private or deleted video, confirm `state: failed`, the correct category and retryable flag, and no `download_url`; then run the 11-submission rate drill and confirm the 11th answers `429` in under a second with no job created
- [ ] T073 [US2] Run the expiry drill from `quickstart.md` step 10 (delete `output/<job_id>/` with `aws s3 rm`), confirm `result.status: expired` and `410 result_expired` from the re-issue route, then confirm a poison message reaches the DLQ and raises the DLQ alarm by inspecting the dead-letter queue

**Checkpoint**: User Stories 1 and 2 both work independently; every failure mode is classified and observable.

---

## Phase 5: User Story 3 - Ask for a specific quality (Priority: P3)

**Goal**: The optional `quality` preference is honoured when available, capped above the
ceiling, silently falls back to the closest available rendition, and the result always
reports the quality actually produced.

**Independent Test**: submit the same short video at `360p` and at `1080p` and confirm each
file matches its reported quality; submit a preference the source lacks and confirm a
documented fallback with the produced quality reported and no failure; submit `2160p` and
confirm a cap to 1080 rather than a rejection.

### Tests for User Story 3

- [X] T074 [P] [US3] Write `app/tests/unit/test_quality.py` asserting the accepted labels `360p`/`480p`/`720p`/`1080p`, that absent or null resolves to the configured ceiling, that `1440p` and `2160p` cap to 1080 without error, that an unknown label such as `9000p` or `best` is rejected as `invalid_input`, and that `produced_quality` is derived from the produced height, never echoed from the request
- [X] T075 [P] [US3] Capture metadata fixtures in `app/tests/fixtures/` for four real cases: 1080p H.264 available, 720p maximum, H.264 absent at the requested height, and a 4K source where the ceiling applies; store the `yt-dlp --dump-single-json` format list, not a whole page
- [X] T076 [P] [US3] Write `app/tests/unit/test_formats.py` asserting selection prefers `avc1` video plus `mp4a` audio at or below the resolved height, closes the gap to the next lower rendition when the exact height is absent, never selects above the requested height, and returns the produced height, video codec, and audio codec as a tuple
- [X] T077 [P] [US3] Write `app/tests/contract/test_post_jobs_quality.py` asserting `quality` is accepted in `POST /v1/jobs`, that a value above the ceiling is accepted with the cap recorded in the job item, that an unknown value returns `400 invalid_input`, and that omitting it stores `requested_quality: null`
- [X] T078 [US3] Implement `app/src/ytdl/quality.py` with the label-to-height map, the ceiling cap, and the fallback rule as pure functions
- [X] T079 [US3] Implement `app/src/ytdl/formats.py` selecting `(format_selector, expected_height)` from the metadata fixtures, in the order: exact height with `avc1`+`mp4a`, next lower with `avc1`+`mp4a`, best available at or below the cap, recording the actual codecs
- [X] T080 [US3] Add `quality` parsing and capping to `app/src/ytdl/api/routes.py` and `app/src/ytdl/api/handler.py`, persisting `requested_quality` and the capped `requested_height` onto the job item
- [X] T081 [US3] Use `requested_height` and `formats.py` in `app/src/ytdl/worker/pipeline.py`, and persist `produced_height`, `produced_quality`, `produced_vcodec`, and `produced_acodec` on completion so `GET` reports the produced value
- [ ] T082 [US3] Run the quality drill from `quickstart.md`: submit one video at `360p` and at `1080p`, confirm each file's `ffprobe` height matches the reported `produced_quality`, then submit `2160p` and confirm the cap is reported without a failure
- [ ] T083 [US3] Submit a preference the source does not offer (for example `1080p` against the 720p fixture equivalent), confirm the job completes with the closest lower quality, and confirm the fallback is visible in `produced_quality` and the structured log line

**Checkpoint**: All four stories except provisioning are independently functional.

---

## Phase 6: User Story 4 - Stand the service up, and tear it down (Priority: P4)

**Goal**: From an empty account, one documented action provisions a working environment;
the same definition produces an independent second environment; one action removes
everything with no chargeable remnants.

**Independent Test**: from an empty account, follow only the repository documentation,
provision `dev`, run one successful download, apply `prod` from the same logic, confirm
independence, destroy both, and confirm the seven-service inventory check returns nothing.

**Note**: the Terraform resource definitions themselves live in Phase 2, because every
other story needs them. This phase delivers the environment parameterisation, the
teardown guarantee, and the documentation that FR-016, FR-017, FR-018, and FR-019 require.

- [X] T084 [P] [US4] Write `infra/env/prod.tfvars` with `environment = "prod"`, the same region and documented defaults, and `alarm_email` supplied by the operator; confirm the only differences from `dev.tfvars` are environment-scoped values and the alarm address
- [X] T085 [US4] Make the `deploy` target in `Makefile` environment-aware: `ENV` selects `infra/backend/$(ENV).backend` and `infra/env/$(ENV).tfvars`, the image tag is the git sha with the `-api` and `-worker` suffixes, and a missing `ENV` or an unknown `ENV` fails loudly instead of defaulting
- [X] T086 [US4] Make the `destroy` target in `Makefile` environment-aware with the same `ENV` guard, passing `-var-file` and `-var image_tag`, and refusing to run when `ENV` is unset
- [X] T087 [US4] Add a `verify-destroy` target to `Makefile` that runs the seven inventory checks from `quickstart.md` step 13 against `ytdl` and exits non-zero if any resource survives; the state bucket is explicitly out of scope and must be named in the check so its absence or presence is a deliberate decision, not an accident
- [ ] T088 [US4] Run `make deploy ENV=prod` alongside the running `dev` stack and confirm a second, independent set of resources: distinct names, `Environment=prod` tags, and a distinct state object under `env/prod/`, with no change to any `.tf` file
- [ ] T089 [US4] Run `make destroy ENV=dev` and confirm the `prod` stack still answers a `GET /v1/jobs/<id>` with `404` rather than a connection error, proving the two environments share no resources
- [ ] T090 [US4] Run `make destroy ENV=prod` then `make verify-destroy ENV=prod` and confirm all seven services report nothing, the media bucket itself is gone rather than merely empty, and no further charges accrue
- [ ] T091 [US4] Run `terraform plan` twice in a row with the same `-var-file` and `-var image_tag` and confirm the second run reports no changes (US4 scenario 4, constitution principle I)
- [X] T092 [P] [US4] Write `README.md` with what the service does, the acceptable-use constraint, prerequisites, the four operator commands (submit, poll, fetch, re-issue), provisioning, second environment, teardown, and the location of the failure catalogue
- [ ] T093 [P] [US4] Write `docs/runbook.md` with the failure catalogue (five categories, cause, and the operator's next action), the credential rotation procedure, the alarm response table from `quickstart.md` step 14, and how to read a structured log line
- [ ] T094 [US4] Time a first-time operator walkthrough from an empty account to a downloaded file using only `README.md` and `docs/runbook.md`, and confirm it finishes under 30 minutes with zero console actions (SC-006); record the elapsed time in the phase notes

**Checkpoint**: The service is provisionable, reproducible, and fully removable.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Guarantees that span stories and cannot be proven inside a single one.

- [X] T095 [P] Write `app/tests/unit/test_no_secrets_in_logs.py` that runs the captured log lines from the contract fixtures through a scanner asserting no token value, no `Authorization` header, and no URL containing a query string longer than a video id (FR-015, SC-009)
- [X] T096 Add `lint` and complete `tf-check` in `Makefile`: `ruff check app/` and `ruff format --check app/`, `terraform fmt -check -recursive infra/`, and `terraform validate` in `infra/` and `infra/bootstrap/`; wire both into `make test`'s documented pre-step so they cannot be skipped silently
- [ ] T097 Add a `verify-idempotency` target to `Makefile` that re-processes one completed job 100 times through the worker path and asserts exactly one object exists under `output/<job_id>/` and that `media_size_bytes` never changes (SC-008, FR-013)
- [ ] T098 Run the rate and concurrency drills: 11 submissions in one hour and 3 simultaneous jobs against a reserved concurrency of 2; confirm the throttled response arrives within one second and that the third job waits rather than failing (FR-014, SC-010), then record the observed latencies in `docs/runbook.md` under the worker throttles alarm
- [ ] T099 Add a `verify-retention` target to `Makefile` that lists `output/` with `aws s3api list-objects-v2` and fails if any object is older than 8 days, then run it and record the oldest observed age (SC-005, research D-12)
- [ ] T100 Measure worker duration for a 30-minute video at default quality, confirm the p95 stays under 600 s and the job is downloadable within 10 minutes of submission, and record the numbers in `docs/runbook.md` under the worker duration alarm (SC-002)
- [ ] T101 Review actual cost for the documented 100-video profile against the $10 ceiling, compare with the research D-02 table, and record the observed figures and any deviation in `docs/runbook.md` (SC-011)
- [ ] T102 Confirm the SNS subscription is confirmed, publish one test notification, and verify both budget alarms exist in `us-east-1` and read `AWS/Billing EstimatedCharges` in USD; record the check in `docs/runbook.md`
- [ ] T103 Run `make test` (unit plus contract, offline), `make lint`, and `make tf-check`, confirm all green, then update `specs/001-youtube-video-download/checklists/requirements.md` with the verification status of the settled values and the final task state
- [ ] T104 Leave the account in a documented state: either destroy both environments and note it, or leave `dev` running with its monthly cost recorded in `docs/runbook.md`; neither option may leave an undocumented resource behind (FR-017)

---

## Dependencies & Execution Order

### Phase dependencies

| Phase | Depends on | Note |
|-------|------------|------|
| 1 Setup | none | Can start immediately |
| 2 Foundational | Phase 1 | Blocks every story |
| 3 US1 | Phase 2 (except T056, which needs T039 and T040) | MVP |
| 4 US2 | US1 (T051 to T055) | Shares `worker/pipeline.py`, `worker/handler.py`, `api/routes.py` with US1 |
| 5 US3 | US1 (T053, T055) | Shares `worker/pipeline.py` and `api/routes.py` with US2 |
| 6 US4 | Phase 2 and a working US1 deploy | Needs handlers for a real apply |
| 7 Polish | all stories | Cross-cutting proofs |

### Within Phase 2

- T009 needs T006 (the image must exist).
- T016 to T021 each need their own test (T010 to T015) written first.
- T023 to T026 need T022 (the fakes).
- T040 needs T027, T039.
- T034 needs T028 to T033 and T038.

### Within each story

- Tests before implementation; a test task is complete when it fails for the right reason.
- New pure modules before the handlers that call them.
- Handlers before deploy or drill tasks.

### Story independence

- US2 and US3 are behaviourally independent, but both edit `api/routes.py` and
  `worker/pipeline.py`. Run them sequentially, or coordinate so US3's edits land in
  separate functions from US2's failure path.
- US4's apply and destroy drills require a working US1, but no US2 or US3 behaviour.

---

## Parallel Execution Examples

### Phase 2, core logic (six test authors, then six implementers)

```text
T010 test_config.py        T013 test_states.py
T011 test_logging.py       T014 test_keys.py
T012 test_ids.py           T015 test_auth.py
```

Then `T016` to `T021` in parallel, one module each.

### Phase 2, Terraform files

```text
T028 versions.tf + providers.tf     T030 variables.tf     T031 locals.tf
T029 backend.tf + dev.backend       T038 env/dev.tfvars
```

`T032` to `T037` follow, since they read those variables and locals.

### Phase 3, US1 test authoring

```text
T041 test_urls.py        T044 test_post_jobs.py    T047 test_worker_happy.py
T042 test_rate_window.py T045 test_get_job.py      T048 e2e/test_download.py
T043 test_link.py        T046 test_reissue.py
```

### Phase 5, US3 test authoring

```text
T074 test_quality.py   T075 fixtures/   T076 test_formats.py   T077 test_post_jobs_quality.py
```

### Phase 6, documentation

```text
T092 README.md   T093 docs/runbook.md
```

---

## Implementation Strategy

### MVP first (Phase 1 + Phase 2 + Phase 3)

1. Phase 1: scaffolding and the two image targets (T001 to T008).
2. Phase 2: risk-first image proof (T009), then pure logic with tests first (T010 to
   T021), adapters (T022 to T026), Terraform (T027 to T037), and bootstrap (T038 to T040).
3. Phase 3: US1 tests, then implementation, then deploy (T041 to T059).
4. **Stop and validate**: the US1 independent test is the product. A working download,
   a playable file, and a 60-minute link.
5. Deploy and use it. Everything after this point is refinement.

### Incremental delivery

| Increment | Adds | Verifiable by |
|-----------|------|---------------|
| Phase 1 + 2 + US1 | A working download service | Submit, poll, fetch, play |
| US2 | Honest states and classified failures | A failing video reports a category; a deleted object reports expiry |
| US3 | Quality preference, cap, and fallback | Two qualities compared; a 2160p request capped |
| US4 | Repeatable second environment and clean teardown | `verify-destroy` returns nothing |
| Polish | Cost, retention, idempotency, and log-hygiene proofs | The four `verify-*` targets and the recorded numbers |

### Two ordering rules that must not be broken

1. **T009 before the rest of Phase 2's application work.** If the worker image cannot
   extract a video from this network, the design conversation changes (research D-14),
   and it is cheaper to learn that before writing the pipeline.
2. **T037 before the second submission to production.** Constitution principle V requires
   the budget alarm in place before production use, not after the first invoice.

---

## Notes

- `[P]` marks tasks that touch different files and depend on nothing incomplete at the
  moment they are scheduled. It does not mean "skip the test first" where a test task
  exists.
- `[USn]` labels appear only in the story phases, never in Setup, Foundational, or Polish.
- Every story ends with drills that use the deployed environment, because most of the
  success criteria (SC-001 to SC-007, SC-010, SC-012) cannot be measured offline.
- No task creates a resource outside Terraform. The only human AWS actions in this list
  are read-only (`aws s3api get-bucket-versioning`, `aws s3 ls`, `aws ssm get-parameter`),
  plus the documented bootstrap apply that owns the state bucket (constitution I).
- The link lifetime is 3600 seconds and the retention is 7 days in every task. Changing
  either is a constitution amendment, not an implementation decision.
