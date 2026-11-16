---

description: "Task list for feature implementation"
---

# Tasks: Telegram Bot Interface

**Input**: Design documents from `/Users/tim/TechProjects/yt_dl/specs/002-telegram-bot-interface/`

**Prerequisites**: plan.md, spec.md, research.md (D-01 to D-15), data-model.md, contracts/ (telegram-webhook.md, bot-commands.md, job-event.md), quickstart.md, `.specify/memory/constitution.md` v1.1.0

**Tests**: included. Constitution principle IV makes tests mandatory here: pure logic is
unit tested before implementation, and every handler is contract tested with synthetic
events and stubbed clients. The default suite stays offline and deterministic.

**Organization**: tasks are grouped by user story so each story is independently
implementable and testable, in the spec's priority order.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different file, no dependency on an incomplete task)
- **[Story]**: US1 to US4, mapped to spec.md
- Every task names the exact file it creates or edits

## Path Conventions

Single Python project with two image targets and one Terraform root:

- Application: `app/src/ytdl/`, tests in `app/tests/{unit,contract,e2e,fakes}/`
- Infrastructure: `infra/`, environment values in `infra/env/<env>.tfvars`
- Feature documents: `specs/002-telegram-bot-interface/`

## Non-negotiable constraints carried by every task

- No secret reaches a log line, an environment variable, an interaction item, or chat
  (FR-029, FR-030, constitution III.3 and V.1).
- Everything runs in `eu-west-3` and is declared in `infra/` (constitution I.1).
- Nothing in a test, a task, or the quickstart generates load to reach a cost ceiling
  (constitution V, FR-019).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: The one non-code dependency (the extraction version) and the test scaffolding
the rest of the feature reuses.

- [X] T001 Add `app/ytdlp-version.txt` containing the line `2026.10.26`, and change `app/Dockerfile` so the worker target installs `yt-dlp[default]==$(cat /tmp/ytdlp-version.txt)` from a `COPY ytdlp-version.txt` that precedes the `RUN`, and so both `api` and `worker` targets copy the file to `${LAMBDA_TASK_ROOT}/ytdlp-version.txt`; after this change the version string exists in exactly one file (research D-13)
- [X] T002 [P] Create `app/src/ytdl/bot/__init__.py` as an empty package marker so `ytdl.bot.handler.handler` is importable and matches the `image_config.command` of the bot function
- [X] T003 [P] Write `app/tests/fakes/telegram_fake.py` with a `FakeTelegramClient` that records every `send_message(chat_id, text)` call and every `get_webhook_info()` call, and whose response is programmable per call (2XX, 400, 401, 403, 429, 5XX, network error) so the client's error policy can be asserted; add a `telegram` fixture to `app/tests/conftest.py`
- [X] T004 [P] Extend `app/tests/fakes/ssm_fake.py` with `get_parameters(Names=[...], WithDecryption=True)` returning `Parameters` and `InvalidParameters`, matching what bot cold-path reads use, and add a `bot_env` fixture to `app/tests/conftest.py` holding the full bot environment mapping from data-model.md "Bot Configuration"

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: configuration, key derivation, the shared submission path, and the storage
primitives every user story needs. Nothing in Phase 3 or later is valid until this phase
is green.

**Critical**: no `terraform apply` happens in this phase. The stack is first applied in
US1, after the bot function and its webhook script exist.

### Tests first

- [X] T005 [P] Extend `app/tests/unit/test_config.py` to cover the new settings: `TELEGRAM_TOKEN_PARAM`, `TELEGRAM_WEBHOOK_SECRET_PARAM`, `TELEGRAM_APPROVED_PARAM`, `TELEGRAM_WEBHOOK_URL`, `TELEGRAM_TEST_VIDEO_ID`, `JOB_EVENTS_TOPIC_ARN` and `YTDLP_VERSION` are `None` when unset (so the api and worker functions still build), `TELEGRAM_DEFAULT_TIER` defaults to `high` and rejects any value outside `low, medium, high, veryhigh, mp3` with `ConfigError`, and a bot-mode assertion that a missing required bot setting raises `ConfigError` naming the variable
- [X] T006 [P] Extend `app/tests/unit/test_keys.py` to assert: `media_key(job_id, video_id, extension="mp4")` returns `output/<job_id>/<video_id>.mp4` and `extension="mp3"` returns `.mp3`; `owner_job_key(123456789)` is `tg:123456789#job` and `owner_interaction_key` is `tg:123456789#interaction`, both matching `^tg:[0-9]{1,20}#(job|interaction)$`; `interaction_pk(812345678)` is `UPD#812345678`; `display_name(job)` is `<video_id>-<requested_tier>-<last 4 chars of job_id>.<ext>` with `.mp4` for `media_kind` absent or `video` and `.mp3` for `audio`
- [X] T007 [P] Write `app/tests/unit/test_titles.py` asserting that `sanitise_title` removes control characters (`\x00`-`\x1f`, `\x7f`), collapses internal whitespace runs to one space, strips leading and trailing spaces, truncates to 80 characters, returns `untitled` for an empty or whitespace-only title, and never returns a value containing a newline (data-model.md validation rules)
- [X] T008 [P] Extend `app/tests/unit/test_adapters.py` for the DynamoDB adapter: `create_job_with_rate_increment` accepts an optional `interaction` item and then sends exactly three `TransactItems` in order (job `Put`, rate `Update`, `UPD#<update_id>` `Put` with `ConditionExpression = attribute_not_exists(pk)`); a `TransactionCanceledException` whose `CancellationReasons[2].Code` is `ConditionalCheckFailed` raises `DuplicateUpdate` and writes nothing else; `query_owner_jobs("tg:1#job", limit)` calls `query` on index `owner-created-index` with `ScanIndexForward=False` and no `FilterExpression` that excludes `deleted_at` in the query itself; `query_interactions` uses the same index with the `#interaction` suffix and limit 10; `get_jobs([...])` uses `batch_get_item` with `ConsistentRead=True`; `mark_deleted(job_id, iso)` sets `deleted_at` only when `state = completed` and `attribute_not_exists(deleted_at)`; `put_interaction` writes `kind = interaction` with a `ttl` of `created_at + 30 days`
- [X] T009 [P] Extend `app/tests/unit/test_adapters.py` for the S3 adapter: `presign_get` takes a `content_type` argument and puts it in `ResponseContentType` (`audio/mpeg` for MP3, `video/mp4` otherwise) while keeping `ResponseContentDisposition` as `attachment; filename="<name>"`; `delete_object(key)` calls `delete_object` and returns without raising when the key is already absent
- [X] T010 [P] Write `app/tests/unit/test_submissions.py` asserting `build_job` copies every request field and omits `owner_key`, `chat_id`, `update_id`, `requested_tier` and `is_test` for a terminal API submission, sets all of them for a bot submission, defaults `media_kind` to `video`, and that no field is ever written as `None`

### Implementation

- [X] T011 [P] Extend `app/src/ytdl/config.py` with the fields T005 describes, keeping every new bot field optional in `load_config` and validating `TELEGRAM_DEFAULT_TIER` against the five-word vocabulary; the api and worker configuration must stay loadable with today's environment
- [X] T012 [P] Extend `app/src/ytdl/keys.py` with `media_key(job_id, video_id, extension="mp4")`, `owner_job_key`, `owner_interaction_key`, `interaction_pk` and `display_name(job)`, all pure and deterministic; keep every existing call site working through the default extension
- [X] T013 [P] Implement `app/src/ytdl/titles.py` with `sanitise_title` as specified in T007, with no I/O and no truncation of a multi-byte character
- [X] T014 Extend `app/src/ytdl/adapters/dynamo.py` with the third transactional action, `DuplicateUpdate`, `query_owner_jobs`, `query_interactions`, `get_jobs`, `put_interaction` and `mark_deleted`; reuse the existing `_attribute_map`, `_without_none` and `_failed_at` helpers, add `deleted_at` filtering in Python rather than a query filter, and keep every existing method's signature valid; then bring `app/tests/fakes/dynamo_fake.py` to the same method set, including dedupe on `update_id` and the deleted overlay
- [X] T015 Extend `app/src/ytdl/adapters/s3.py` as T009 specifies, and mirror it in `app/tests/fakes/s3_fake.py` (record the content type and the deleted keys)
- [X] T016 Implement `app/src/ytdl/submissions.py`: a pure `build_job(reference, created_at, requested_quality, requested_height, owner, requested_tier, media_kind)` returning the job item, and `submit(repo, queue, config, job, rate_pk, limit, now_epoch, interaction, queue_message)` performing the one transaction then the one enqueue; then change `app/src/ytdl/api/handler.py::_create` to delegate to it with no behavioural change, so the existing `app/tests/contract/test_post_jobs.py`, `test_post_jobs_quality.py` and `app/tests/unit/test_rate_window.py` pass unmodified
- [X] T017 [P] Add the sparse index to `infra/state.tf`: `attribute { name = "owner_key" type = "S" }`, `attribute { name = "created_at" type = "S" }` and a `global_secondary_index` named `owner-created-index` with `hash_key = "owner_key"`, `range_key = "created_at"`, `projection_type = "ALL"`; this is an in-place table update, not a replacement, and `make tf-check` must stay clean
- [X] T018 [P] Extend `infra/locals.tf` with the bot names used by every later task: `bot_function_name = "${local.name_prefix}-bot"`, `job_events_topic_name = "${local.name_prefix}-job-events"` and the three parameter names `/ytdl/<env>/telegram-bot-token`, `/ytdl/<env>/telegram-webhook-secret`, `/ytdl/<env>/telegram-approved-user-ids`

**Checkpoint**: `make test` passes with the existing 551 tests plus the new unit tests, and
`make tf-check` is clean. The GSI exists in configuration but is not applied yet.

---

## Phase 3: User Story 1 - Ask for a video in one chat message and receive a link (Priority: P1) 🎯 MVP

**Goal**: an approved operator sends one message with a YouTube link and a resolution word,
is acknowledged in seconds, and later receives a working presigned link in the same chat.
Requesting the same video and tier again while it is stored returns a fresh link with no
second download. `mp3` produces a playable audio file.

**Independent Test**: from a private chat, send a short public video with `medium`; receive
the acknowledgement naming `medium` and 480p, then the completion message with a link that
expires in 60 minutes; follow the link and confirm the file plays with picture and sound.
Send the same message again and confirm `Already stored` with a fresh link and no second
S3 object. Repeat with `mp3` and confirm an `.mp3` file.

### Tests for User Story 1 ⚠️ Write these first and confirm they fail

- [X] T019 [P] [US1] Extend `app/tests/unit/test_formats.py`: when nothing exists at or below the requested ceiling but something exists at or below the service ceiling, `select` returns the lowest such rendition and reports its real height; when nothing exists at or below the service ceiling it still raises `NoUsableFormat`; `select_audio(metadata)` chooses the best audio-only stream by bitrate, never a muxed video stream, and raises `NoUsableFormat("no audio stream ...")` when the source has none; every existing selection test still passes
- [X] T020 [P] [US1] Extend `app/tests/unit/test_remux.py`: `build_mp3_args` contains `-vn`, `-c:a libmp3lame`, `-b:a 192k`, `-y` and no `-c copy`; `convert_to_mp3` raises `RemuxError` on a non-zero exit, on a missing output and on an empty output; `probe_output` on an audio-only stream list returns `height = None` and `acodec = "mp3"`
- [X] T021 [P] [US1] Write `app/tests/unit/test_worker_audio.py`: an audio job selects audio only, downloads once, converts to MP3, uploads to `output/<job_id>/<video_id>.mp3` with content type `audio/mpeg` and metadata `media-kind=audio`, and returns `produced_quality = "mp3"`, `media_kind = "audio"`, no `produced_height`, `media_key` ending in `.mp3`; a source with no audio track is classified `unsupported_media` before any download; a video job on a source with no rendition below its tier completes at the fallback height and reports that height
- [X] T022 [P] [US1] Write `app/tests/unit/test_worker_publish.py`: a completed job carrying `chat_id` publishes exactly `{"version":1,"event":"job_completed","job_id":"<id>"}`; a terminal failed job publishes `job_failed`; a failure the queue will retry publishes nothing; a job without `chat_id` publishes nothing; a publish exception is logged and does not change the job outcome or the batch response; publishing always happens after `complete_job` or `fail_job`
- [X] T023 [P] [US1] Write `app/tests/unit/test_bot_commands.py` for the request grammar in `contracts/bot-commands.md`: `URL medium` accepted, `URL MEDIUM` accepted, `URL` accepted at the configured default tier with the tier reported, a trailing full stop on the link or the word stripped, two links refused with `Send one link at a time, followed by one resolution word.`, `URL hq` refused with `Unknown resolution "hq". Use low, medium, high, veryhigh or mp3.`, a word with no link answered with the format hint, and the five tier words mapping to `(240, video, mp4)`, `(480, video, mp4)`, `(720, video, mp4)`, `(1080, video, mp4)`, `(none, audio, mp3)`
- [X] T024 [P] [US1] Write `app/tests/unit/test_bot_messages.py` asserting every request-phase template in `contracts/bot-commands.md` character for character (accepted video, accepted audio, completed, reused, in progress, failed with and without the retry sentence, rate limited, expiry, format hint), and that `paginate` splits only on line boundaries into pages of at most 3,500 characters, loses no line, duplicates no line, and leaves a short text in one page
- [X] T025 [P] [US1] Write `app/tests/unit/test_bot_client.py` asserting `send_message` posts JSON to `https://api.telegram.org/bot<token>/sendMessage` with `plain text` only (no `parse_mode`), a 10-second timeout and no in-process retry; that 400 and 403 return without raising; that 401, 429, 5XX and a network error raise; and that multiple pages are sent sequentially in order; `get_webhook_info` reads `url`, `pending_update_count`, `max_connection` fields from the documented response shape
- [X] T026 [P] [US1] Write `app/tests/contract/test_bot_webhook.py`: a valid update with the correct `X-Telegram-Bot-Api-Secret-Token` answers `200` with an empty body; a missing or wrong secret answers `403` before the body is parsed (assert the fake repository and the fake Telegram client saw no call); a malformed body or a body without `update_id` answers `400`; an unexpected exception answers `500`; a duplicate `update_id` answers `200` and produces no second side effect; and the health entry shape `{"action":"health"}` answers `200` and emits `WebhookHealthy = 1` when `getWebhookInfo` reports the configured URL with no recent error, `0` when the URL differs or `last_error_date` is inside the last hour
- [X] T027 [P] [US1] Write `app/tests/contract/test_bot_request.py` with fakes only, no network: an accepted request sends the accepted ack before any queue call and writes one job (`owner_key`, `chat_id`, `update_id`, `requested_tier`, `media_kind`, `requested_quality`, `requested_height`) plus one interaction in the one transaction, increments the rate once, and enqueues one message carrying `requested_height` and `media_kind`; a stored, non-deleted item for the same video and tier sends the reused template with a fresh link and enqueues nothing and does not increment the rate; an in-progress item sends the in-progress template and creates no second job; an exhausted rate limit sends the rate-limited template naming the return time and creates no job; an invalid link sends the service's invalid-link sentence and creates nothing; a duplicate `update_id` produces one job, one enqueue and one reply; `mp3` sends the audio acknowledgement and creates an audio job
- [X] T028 [P] [US1] Write `app/tests/contract/test_bot_completion.py`: `job_completed` presigns the stored object with `display_name(job)` and sends the completion template with title, `produced_quality`, size, expiry time and the reuse instruction, and `audio/mpeg` content type for an audio job; `job_failed` sends the failure sentence plus `Send the request again.` only when retryable; a missing job record, a job without `chat_id` and an unknown `version` are acknowledged with no send; a completed job whose object is gone or past retention sends the expiry sentence; Telegram 400 or 403 returns success; Telegram 401, 429, 5XX or a network error raises so SNS retries

### Implementation for User Story 1

- [X] T029 [P] [US1] Add `240p` to `QUALITIES` in `app/src/ytdl/quality.py` so `resolve("240p", 1080)` is 240 and `label_for(240)` is `"240p"`, keeping every existing label, capping and `label_for` behaviour unchanged
- [X] T030 [US1] Change `app/src/ytdl/formats.py`: `select(metadata, ceiling, service_ceiling=None)` applies the hard ceiling first, then falls back to the lowest rendition at or below `service_ceiling` and reports its real height, and raises `NoUsableFormat` only when nothing exists at or below `service_ceiling`; add `select_audio(metadata)` returning a `Selection` for the best audio-only stream with `expected_height = None`. The fallback can never exceed `MAX_HEIGHT` (research D-07)
- [X] T031 [US1] Add `build_mp3_args` and `convert_to_mp3` to `app/src/ytdl/worker/remux.py`: one ffmpeg invocation with `-vn -c:a libmp3lame -b:a 192k -movflags +faststart`, a 600-second timeout, and the same failure and empty-output checks as `remux_to_mp4` (research D-08)
- [X] T032 [US1] Change `app/src/ytdl/worker/pipeline.py`: read `media_kind` from the job (default `video`), branch to `select_audio` + `convert_to_mp3` for audio, pass `service_ceiling=config.max_height` to `formats.select`, choose the extension and content type from `media_kind`, write `media-kind` object metadata, and return `produced_quality = "mp3"` with no `produced_height` for audio; the duration cap, size cap and deadline guard apply unchanged to both branches
- [X] T033 [US1] Add the five-word tier table and message parsing to `app/src/ytdl/bot/commands.py`: a frozen `Tier` record (word, ceiling, media kind, extension) for `low`, `medium`, `high`, `veryhigh`, `mp3`, and `parse_request(text, default_tier)` returning the intent (`request`, `ambiguous_two_links`, `unknown_resolution`, `format_hint`) with the validated `VideoRef` from `ytdl.urls.parse_video_url`. Do not add a second URL parser
- [X] T034 [US1] Write `app/src/ytdl/bot/messages.py` with the request-phase constants and `paginate(text, limit=3500)`, in plain text only, no `parse_mode`, no markdown, no emoji outside the verbatim help block that arrives in US4
- [X] T035 [US1] Write `app/src/ytdl/bot/client.py`: `TelegramClient(token, timeout=10)` with `send_message(chat_id, text)` that paginates and sends each page in order, and `get_webhook_info()`; use `urllib.request` with no new dependency, and raise `TelegramError` for 401, 429, 5XX and network failures while returning quietly for 400 and 403 (research D-10)
- [X] T036 [US1] Write `app/src/ytdl/bot/handler.py` with the three entry shapes and injected collaborators (`repo`, `queue`, `store`, `telegram`, `policy`, `config`, `now`, `logger`): a webhook branch that checks the secret header in constant time before parsing, then routes a request through `submissions.submit`, writes the refusal interaction for an approved-but-refused message, reuses a stored item, answers an in-progress item, answers an exhausted rate limit with the capacity time, and sends every reply; an SNS branch implementing `contracts/job-event.md`; a health branch calling `get_webhook_info` and emitting the embedded metric. Wiring in `_build()` uses a placeholder deny-all policy that US2 replaces; **do not deploy in this state**
- [X] T037 [US1] Change `app/src/ytdl/worker/handler.py` to publish the terminal job event: an optional injected `publish` callable, called only after `complete_job` or `fail_job` succeeded, only when `chat_id` is present, with the body from `contracts/job-event.md`; a publish exception is logged and never changes the job outcome or the batch failure list; `_build_worker` publishes to `config.job_events_topic_arn`
- [X] T038 [US1] Write `infra/bot.tf`: the HTTP API `ytdl-<env>-bot` with a `$default` stage (`auto_deploy`, throttling 10 rps and burst 20, the same seven-field access log format as `infra/api.tf`), one `AWS_PROXY` integration, the route `POST /telegram/webhook`, the Lambda permission, the bot log group with `var.log_retention_days`, the bot execution role and policy (table ARN and the `owner-created-index` ARN, `sqs:SendMessage` on the queue, `s3:GetObject` on `output/*` plus `s3:ListBucket` on the bucket, `ssm:GetParameter` on exactly the three bot parameter ARNs, `logs:CreateLogStream`/`PutLogEvents` on its own group), the `aws_lambda_function.bot` (image `<repo>:<tag>-api`, `command = ["ytdl.bot.handler.handler"]`, `arm64`, 512 MB, 15 s, reserved 5), the SNS topic `ytdl-<env>-job-events` with its Lambda subscription and the SNS invoke permission, and the three SSM parameters built from `random_password.telegram_webhook_secret` (`special = false`, length 48) and `var.telegram_bot_token`. The bot environment carries `TELEGRAM_TOKEN_PARAM`, `TELEGRAM_WEBHOOK_SECRET_PARAM`, `TELEGRAM_APPROVED_PARAM`, `TELEGRAM_WEBHOOK_URL`, `TELEGRAM_DEFAULT_TIER`, `TELEGRAM_TEST_VIDEO_ID`; no secret is an environment variable
- [X] T039 [US1] Change `infra/functions.tf` so the worker role gains `sns:Publish` on `aws_sns_topic.job_events.arn` only, and `local.function_environment` gains `JOB_EVENTS_TOPIC_ARN` and `YTDLP_VERSION = file("${path.module}/../app/ytdlp-version.txt")` (trimmed); nothing else in the worker changes
- [X] T040 [US1] Change `infra/variables.tf` with `telegram_bot_token` (`sensitive = true`, no default), `telegram_approved_user_ids` (`list(string)`, default `[]`, each element validated by `^[0-9]{1,20}$`), `telegram_default_tier` (default `high`, validated against the five words) and `telegram_test_video_id` (default a short public video id); then change `infra/env/dev.tfvars` and `infra/env/prod.tfvars` to set only `telegram_default_tier` and `telegram_test_video_id`, never a token (research D-13, data-model.md "Bot Configuration")
- [X] T041 [US1] Write `infra/scripts/telegram-webhook.sh` (executable) plus `terraform_data.telegram_webhook` in `infra/bot.tf`: on create, `setWebhook` with the URL from `self.input`, `secret_token` read from SSM, `allowed_updates=["message"]`, `max_connections=5`, `drop_pending_updates=true`; on destroy, `deleteWebhook`; both read the token and secret with `aws ssm get-parameter --with-decryption`, print Telegram's own description on a non-OK answer and exit non-zero, use only `self` in the destroy provisioner, and never echo the token (research D-02)
- [X] T042 [US1] Add the hourly health check and the two alarms: an `aws_cloudwatch_event_rule` with `schedule_expression = "rate(1 hour)"` and `input = jsonencode({action = "health"})`, its Lambda permission and target; in `infra/alarms.tf` an `aws_cloudwatch_metric_alarm.bot_errors` on `AWS/Lambda Errors` for the bot function and an `aws_cloudwatch_metric_alarm.bot_webhook_health` on `YTDL/Bot WebhookHealthy` with `LessThanThreshold 1` over two 3600-second periods, `treat_missing_data = "breaching"`, each with `alarm_actions` and `ok_actions` on the alarms topic (research D-12)
- [X] T043 [P] [US1] Extend `infra/outputs.tf` with `bot_api_base_url`, `bot_webhook_url`, `bot_function_name`, `telegram_token_parameter_name` and `job_events_topic_arn`, so the Makefile and the quickstart never hardcode a name

**Checkpoint**: US1 works on its own. `make test` is green, `make tf-check` is green, and
the deployed dev stack answers a real message with an acknowledgement and then a link.
The bot is still reachable by any account, so deployment to prod waits for US2.

---

## Phase 4: User Story 2 - Keep the bot private to approved people (Priority: P2)

**Goal**: only numeric accounts on the SSM allowlist are served, only in private chats, and
the list governs the next message with no deployment. Everyone else learns nothing.

**Independent Test**: from an account not on the allowlist, send a valid link and a valid
resolution word, and confirm `This bot is private.` with no job, no rate increment, no
interaction item and no queue message. Add the account to the allowlist, apply only that
change, and confirm the next message works with no image rebuild. From an approved account,
send a request in a group and confirm the private-chat refusal.

### Tests for User Story 2 ⚠️

- [X] T044 [P] [US2] Write `app/tests/unit/test_bot_access.py`: the allowlist parser turns `"111, 222"` into `{"111","222"}`, turns `""` into an empty set, ignores surrounding whitespace, and raises `ConfigError` for a non-numeric entry; `is_approved` compares string forms of the numeric id so `123456789` and `"123456789"` agree; the secret comparison is constant time and a wrong-length secret is refused; the chat-type rule accepts only `private`
- [X] T045 [P] [US2] Write `app/tests/contract/test_bot_refusals.py`: an unapproved account sending a link with a tier, `/start`, `/help`, `/list` or `/test` receives exactly `This bot is private.` and produces no job, no rate increment, no interaction item, no queue message and no second reply; the token, the secret and the allowlist are read in one `GetParameters` call per message; a change to the allowlist value between two deliveries changes the second answer; a removed account is refused and sees none of its earlier items; an approved account in a `group` or `supergroup` chat receives exactly `This bot only works in a private chat.` and creates nothing; an approved account naming another account's item by name is treated as an unknown name
- [X] T046 [P] [US2] Extend `app/tests/unit/test_bot_messages.py` with the two refusal strings, byte for byte, including the absence of any command list or hint that downloads are possible in `This bot is private.`

### Implementation for User Story 2

- [X] T047 [US2] Write `app/src/ytdl/bot/access.py`: `AllowlistPolicy(ssm_client, token_param, secret_param, approved_param)` with one `GetParameters(Names=[...], WithDecryption=True)` per call, `check_secret(presented) -> bool` using `hmac.compare_digest`, `is_approved(user_id)`, and `is_private(chat_type)`; add the two refusal strings to `app/src/ytdl/bot/messages.py`
- [X] T048 [US2] Wire the policy into `app/src/ytdl/bot/handler.py`: replace the deny-all placeholder in `_build()` with `AllowlistPolicy`, verify the secret before parsing and the allowlist before interpreting any text, answer an unapproved account with the refusal and no side effect, answer a non-private chat with its refusal after the allowlist check, and confirm the `TELEGRAM_APPROVED_PARAM` environment variable is present in `infra/bot.tf` and that the role names exactly three parameter ARNs (`make tf-check`)

**Checkpoint**: US1 and US2 together are the first deployable state. No account outside the
allowlist can create a job, and no group chat is served.

---

## Phase 5: User Story 3 - See, refresh, and delete what is stored (Priority: P3)

**Goal**: `/list` shows one honest line per stored item, `/delete <name>` removes exactly the
named item, and `/empty` (after an explicit confirmation) removes everything and reports
exactly what happened. A deleted item is downloadable again on request.

**Independent Test**: request two videos at two tiers, `/list` them, delete one by name and
list again, then `/empty`, `/empty confirm`, and list a third time. Every listing must
agree with what was deleted, and the object must be gone from S3 while the job record
survives with `deleted_at`.

### Tests for User Story 3 ⚠️

- [X] T049 [P] [US3] Write `app/tests/unit/test_bot_library.py`: a list line is exactly `<video_id>-<tier>-<job_id last 4>.<ext> | <title> | <produced_quality> | <size> | <age> old | <days> days left`; size prints one decimal place (`182.4 MB`); age prints days and hours; days left is the ceiling of the remaining seconds over 86400; items with `deleted_at`, items past `media_expires_at` and items in a non-completed state are excluded; lines are newest first; more than 200 live items appends `... and N more. Delete some items to see the rest.`; `No videos are stored.` is returned for an empty library; `/empty` reports the live count and asks for `/empty confirm`; `/empty confirm` reports `Deleted N items.`, the remainder form when more than 200 remain, and `There was nothing to delete.` when nothing is live; `/empty yes` and `/empty <anything>` are treated as `/empty`
- [X] T050 [P] [US3] Write `app/tests/contract/test_bot_library.py` with fakes: `/list` reads only the caller's `owner_key` prefix and returns one line per live item; `/delete <name>` deletes one S3 object, writes `deleted_at` and answers `Deleted <name>.`; `/delete` with an unknown or another account's name writes nothing and answers `No item is named <name>.`; a request repeating the deleted item's video and tier enqueues a new job instead of offering the dead item; `/empty` writes nothing; `/empty confirm` deletes every live item up to 200 and reports the count; `/list` after a delete no longer shows the item
- [X] T051 [P] [US3] Extend `app/tests/contract/test_get_job.py` and `app/tests/contract/test_reissue.py`: a job carrying `deleted_at` is reported by the terminal API as `expired` with `download_url = null`, and `POST /v1/jobs/{id}/link` answers `410 result_expired`, even when the S3 object happens to still exist (data-model.md state machine)

### Implementation for User Story 3

- [X] T052 [US3] Write `app/src/ytdl/bot/library.py`: `render_list(jobs, now)` and `live_items(jobs, now)` as pure functions over the records the repository returns, plus `delete_item(repo, store, owner_key, name, now)` and `empty_library(repo, store, owner_key, now, cap=200)` which delete the object first, then write `deleted_at` only for completed, non-deleted items, and return the counts the reply needs
- [X] T053 [US3] Add `/list`, `/delete <name>` and `/empty [confirm]` dispatch to `app/src/ytdl/bot/handler.py`: the owner prefix is built from the sender id, the per-confirmation cap is 200, an unknown sub-argument to `/empty` can never delete, and each command writes one interaction item with `outcome = command:<name>` after its reply is composed
- [X] T054 [US3] Change `app/src/ytdl/api/handler.py` so `_result` and `_reissue` treat a job with `deleted_at` as expired before they consult S3, keeping the terminal API consistent with the bot (FR-015, data-model.md "deleted overlay")

**Checkpoint**: US1, US2 and US3 work independently. A listing issued in the same second as
a delete may lag, which is accepted and recorded in the plan's post-design re-check.

---

## Phase 6: User Story 4 - Understand the bot and check that it works (Priority: P4)

**Goal**: `/help` returns the operator's fixed text verbatim, `/start` greets and points at
it, `/info` reports the running extraction version and the four limits, `/history` shows the
last ten interactions with no token and no link, and `/test` runs exactly one real
pipeline job and reports pass or a specific failure.

**Independent Test**: from a clean chat, send `/start`, `/help`, `/info`, `/history`,
`/test` and an unknown `/foo`, and confirm each reply matches `contracts/bot-commands.md`,
including a `/test` that runs exactly one job at `low` against the configured video id and
consumes one unit of the hourly budget.

### Tests for User Story 4 ⚠️

- [X] T055 [P] [US4] Write `app/tests/unit/test_bot_help.py` that reads the fenced help block out of `specs/002-telegram-bot-interface/contracts/bot-commands.md` and asserts `messages.HELP` equals it character for character, including the emoji, the bullet character, the `filename.zip` line and the example; also assert `/start` returns the greeting that names `/help` and that no string in the module is built by concatenation that could drift
- [X] T056 [P] [US4] Write `app/tests/contract/test_bot_commands.py`: `/start`, `/help` and `/info` send their single reply and write one interaction item each; `/info` prints `yt-dlp <version>` from `config.ytdlp_version`, the ceiling, the retention days, the link lifetime in minutes and the rate limit; `/history` renders at most ten lines newest first as `<YYYY-MM-DD HH:MM> UTC | <text> | <outcome>` and contains no token, no signed link and no URL with a query string; `/foo` receives the format hint, not the help text; `/history` with no items answers `No history yet.`; `/test` sends `Pipeline test started.` and creates exactly one job with `requested_tier = "low"`, `is_test = true`, the configured test video and one rate increment, bypassing the reuse lookup; `/test` while the limit is exhausted sends the rate-limited sentence and creates nothing; `/empty confirm` (from US3) is not shadowed by command dispatch

### Implementation for User Story 4

- [X] T057 [US4] Add `HELP` (the exact block), `START`, and the `/info` block builder to `app/src/ytdl/bot/messages.py`, reading the version, ceiling, retention, link lifetime and rate limit from `Config`; the version is never typed into a Python literal (research D-13)
- [X] T058 [US4] Add `render_history(interactions, states_by_job_id, now)` to `app/src/ytdl/bot/library.py`, resolving an interaction's outcome to the job's current state when it carries a `job_id`, and never emitting a token, a link or a query string
- [X] T059 [US4] Add `/start`, `/help`, `/info`, `/history` and `/test` dispatch to `app/src/ytdl/bot/handler.py`; `/test` submits through `submissions.submit` with `is_test = True`, `requested_tier = "low"`, `media_kind = "video"`, `config.telegram_test_video_id` and the normal rate window, and writes its interaction; `/info` and `/history` add the `command:<name>` interaction line
- [X] T060 [US4] Add the Makefile targets the plan names: `bot-help-check` running only the T055 test, `bot-health` reading the token from SSM and printing `url`, `pending_update_count`, `max_connections` and `allowed_updates` from `getWebhookInfo`, and `bot-token` printing the parameter value from `infra/outputs.tf`; `bot-health` and `bot-token` resolve `ENV` and never write the token to a file or a log

**Checkpoint**: every command in the fixed help text behaves as the help text says, and the
help text is asserted against the contract file rather than trusted.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: documentation, the cross-story privacy assertion, and the one opt-in end-to-end
check. Documentation is part of done (constitution W.3).

- [X] T061 [P] Add the chat front door section to `README.md`: creating the bot with BotFather, exporting the token, the approved-list procedure, the five tiers, the default tier, and the fact that links expire before files do
- [X] T062 [P] Extend `docs/runbook.md`: bot failure sentences and what each means, the `bot-errors` and `bot-webhook-health` alarm rows with their named responses, token rotation, the allowlist change, and the two-step webhook removal (`count = 0`, apply, then delete the block), including that a removed block skips its destroy provisioner
- [X] T063 [P] Amend the earlier feature so the two documents describe one rule: `specs/001-youtube-video-download/spec.md` points its "audio-only out of scope" assumption at 002, `specs/001-youtube-video-download/contracts/api.md` accepts `240p` and states the bounded fallback, and `specs/001-youtube-video-download/data-model.md` gains the fallback row in its Quality Preference table
- [X] T064 [P] Add one sentence to `app/THIRD-PARTY-NOTICES.md` recording that MP3 encoding uses the LGPL `libmp3lame` already inside the pinned ffmpeg build, with no new licence obligation
- [X] T065 [P] Extend `app/tests/unit/test_no_secrets_in_logs.py` to the bot: a handled request logs `update_id`, the outcome and a duration, and never the chat id, the account id, the token, the webhook secret, the source URL with its query string or a signed link; a refusal logs the same fields; an error logs the exception class name only
- [X] T066 [P] Write `app/tests/e2e/test_bot.py` marked `e2e`: POST a synthetic API Gateway event to the deployed `bot_webhook_url` from `infra/outputs.tf`, assert `403` for a wrong secret and `200` for `{"action":"health"}`; send no chat traffic unless `YTDL_E2E_SEND=1`, in which case send exactly one operator request and assert one acknowledgement, then stop. This is the only test that reaches the deployed stack and it never repeats a run (constitution V)
- [X] T067 Run `make test`, `make lint` and `make tf-check` across `app/` and `infra/` and fix anything they report; confirm the suite in `app/tests/` is still offline (no AWS, no Telegram, no network) and that the previously passing 551 tests still pass
- [ ] T068 Deploy `ENV=dev` once and execute `quickstart.md` steps 4 to 14 in order, one request per tier, one `/test`, then `make destroy` and the `getWebhookInfo` check that proves `url` is empty; record the observed results next to the steps. Stop at any step that would generate load to approach a cost ceiling

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies, starts immediately
- **Foundational (Phase 2)**: depends on Setup; blocks every user story
- **US1 (Phase 3)**: depends on Foundational. Deployable alone, but see the US2 gate
- **US2 (Phase 4)**: depends on Foundational; integrates with US1's handler and refusals
- **US3 (Phase 5)**: depends on Foundational; uses US1's handler dispatch and US2's owner identity
- **US4 (Phase 6)**: depends on Foundational; uses US1's request path for `/test` and US3's rendering helpers
- **Polish (Phase 7)**: depends on every story it documents

### Story dependencies in practice

- **US1 (P1)**: no dependency on another story
- **US2 (P2)**: no dependency on another story's behaviour, but the wiring task T048 edits the US1 handler
- **US3 (P3)**: no dependency on another story's behaviour; needs the GSI and adapter methods from Phase 2
- **US4 (P4)**: `/test` reuses US1's submission path; `/history` reads items US1, US3 and US4 write

### Deployment gate that must not be broken

US1 ships a handler whose wired access policy denies every account. The first deploy to any
environment happens after T048, so no window exists in which a real token reaches a public
bot. `make deploy ENV=prod` after US1 alone is forbidden by FR-020 and constitution C.2.

### Within each story

- A test task precedes the implementation it asserts, and must fail first
- Pure logic before the handler that calls it
- Providers and collaborators before the wiring
- Application changes before infrastructure that references them
- Each checkpoint is a stop-and-verify point, not a formality

---

## Parallel Execution Examples

### Phase 2, five test authors in parallel

```bash
Task: "Extend app/tests/unit/test_config.py with the bot settings"        # T005
Task: "Extend app/tests/unit/test_keys.py with owner and display keys"    # T006
Task: "Write app/tests/unit/test_titles.py"                               # T007
Task: "Extend app/tests/unit/test_adapters.py for DynamoDB"               # T008
Task: "Extend app/tests/unit/test_adapters.py for S3"                     # T009
```

### Phase 3, US1 test authoring in parallel

```bash
Task: "Extend app/tests/unit/test_formats.py with the fallback and audio"  # T019
Task: "Extend app/tests/unit/test_remux.py with MP3"                       # T020
Task: "Write app/tests/unit/test_worker_audio.py"                          # T021
Task: "Write app/tests/unit/test_worker_publish.py"                        # T022
Task: "Write app/tests/unit/test_bot_commands.py"                          # T023
Task: "Write app/tests/unit/test_bot_messages.py"                          # T024
Task: "Write app/tests/unit/test_bot_client.py"                            # T025
Task: "Write app/tests/contract/test_bot_webhook.py"                       # T026
Task: "Write app/tests/contract/test_bot_request.py"                       # T027
Task: "Write app/tests/contract/test_bot_completion.py"                    # T028
```

### Phase 3, independent implementation pairs

```bash
# Pure modules with no shared file:
Task: "Add 240p to app/src/ytdl/quality.py"                                # T029
Task: "Write app/src/ytdl/bot/messages.py"                                 # T034
Task: "Write app/src/ytdl/bot/client.py"                                   # T035

# Infrastructure files that do not overlap:
Task: "Write infra/bot.tf"                                                 # T038
Task: "Change infra/functions.tf for the worker publish permission"        # T039
Task: "Change infra/variables.tf and infra/env/*.tfvars"                   # T040
Task: "Write infra/scripts/telegram-webhook.sh"                            # T041
Task: "Add the health rule and the two alarms"                             # T042
```

### Phase 4 to 6, test authoring

```bash
Task: "Write app/tests/unit/test_bot_access.py"                            # T044
Task: "Write app/tests/contract/test_bot_refusals.py"                      # T045
Task: "Write app/tests/unit/test_bot_library.py"                           # T048
Task: "Write app/tests/contract/test_bot_library.py"                       # T049
Task: "Write app/tests/unit/test_bot_help.py"                              # T054
Task: "Write app/tests/contract/test_bot_commands.py"                      # T055
```

---

## Implementation Strategy

### MVP scope: US1 with US2, in that order, in one release

US1 alone is the whole value (a message in, a link out), but it is not deployable on its
own: the handler's wired policy denies everyone until T048 lands. The smallest safe release
is therefore Phase 1 + Phase 2 + US1 + US2, validated against `quickstart.md` steps 4 to 9.
Nothing is deployed to a real environment before that point.

1. Phase 1: version file and test scaffolding
2. Phase 2: configuration, keys, adapters, GSI, shared submission path (`make test` green)
3. US1: tiers, audio, bot modules, worker publish, `infra/bot.tf`, webhook, health, alarms
4. US2: allowlist, refusals, wiring
5. **STOP and VALIDATE** on dev: one `medium` request end to end, then one `mp3`
6. Deploy to prod only after that validation

### Incremental delivery

1. Setup + Foundational → foundation ready, no bot yet
2. US1 + US2 → private chat bot that downloads and links (MVP)
3. US3 → library listing, deletion and emptying
4. US4 → help, info, history and the self-test
5. Polish → documentation, privacy sweep, one opt-in end-to-end check

### Parallel team strategy

With more than one developer, the split is by file, not by layer:

- Developer A: bot pure modules (`commands.py`, `messages.py`, `access.py`, `library.py`)
- Developer B: adapters, `submissions.py` and the worker audio path
- Developer C: `infra/bot.tf`, the webhook script, alarms and outputs
- Documentation (T061 to T064) runs last and in parallel

### Two ordering rules that must not be broken

1. **Tests before implementation**, per constitution IV. Each `test_*.py` task must fail
   for the right reason before its implementation task starts.
2. **US2 before any deploy**, per FR-020 and constitution C.2. A bot without its allowlist
   is a public ripping service.

---

## Notes

- `[P]` marks a different file with no dependency on an incomplete task; two `[P]` tasks
  never touch the same file
- The Story label maps a task to exactly one user story, so a regression can be attributed
- T017 is an in-place DynamoDB update: a reviewed plan must show one in-place table change,
  never a replacement
- No task generates traffic to approach a cost ceiling (constitution V); rate-limit and
  cost behaviour are asserted offline, and `/test` is one operator-invoked job
- Commit infrastructure and application changes separately, as 001 did, and never apply a
  plan that was not reviewed
