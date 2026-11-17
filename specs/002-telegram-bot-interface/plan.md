# Implementation Plan: Telegram Bot Interface

**Branch**: `002-telegram-bot-interface` | **Date**: 2026-11-16 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/002-telegram-bot-interface/spec.md`

**Status**: implemented, deployed and verified on 2026-11-16. Since 2026-11-17 the bot
runs on the host compute target (001 research D-20): the webhook is served by the host's
HTTP front end and terminal job events reach it over the container network instead of
SNS. The Lambda design below is the Lambda target and is kept as it was built.

> **Amendment 2026-11-17: host deployment.** The webhook URL is now the public origin
> (`https://ytdl.timcloud.fr/telegram/webhook`), registered by Terraform through the same
> `terraform_data` resource, driven by `bot_public_url`. The `bot` Lambda and its API
> Gateway stage remain deployed but receive no updates. The worker-to-bot event keeps its
> JSON body and moves from SNS to an authenticated `POST /internal/job-event` on the
> container network; the worker still never holds the bot token. The hourly health check
> still runs on the (otherwise idle) Lambda and now compares the host's URL, so
> `ytdl-prod-bot-webhook-health` remains meaningful. `ytdl-prod-bot-errors` was deleted
> with the other dormant-metric alarms. No command, sentence or ownership rule changed.

**Note**: Filled in by `/speckit.plan`. The design extends the deployed `001` stack
rather than replacing any part of it.

---

## Summary

A Telegram bot becomes the conversational front door to the existing downloader. An
approved operator sends one message containing a YouTube link and a resolution word, gets
an acknowledgement within seconds, and later receives a link in the same chat. The bot
also lists, deletes, and reports on what is stored, shows the running extraction version,
and can run one real pipeline test on request.

The technical approach: a third Lambda (`bot`) built from the existing `-api` image and
reached through its own API Gateway HTTP API; a webhook registered and removed by
Terraform through one reviewed script; an SSM allowlist read on every message; ownership
carried by a sparse GSI on the existing table; `update_id` idempotency inside the job
transaction; completion announced by the worker on a new SNS topic and delivered by the
bot; audio-only requests downloaded as a single audio stream and encoded to MP3 with the
already-pinned ffmpeg; and an hourly `getWebhookInfo` check that turns a silent bot into
an alarm instead of a surprise.

Three spec-level consequences are settled here and recorded in Complexity Tracking:

1. `mp3` reverses 001's "audio-only out of scope" assumption. It becomes the one path
   that re-encodes, bounded by the same 60-minute cap.
2. `low <= 240p` needs a `240p` value the job interface lacks. The value is added, and
   the selection rule gains a bounded fallback for sources that offer nothing at or below
   the ceiling.
3. `filename.zip` is read as a placeholder for the real file name, so `/list` prints
   `.mp4` and `.mp3` names and `/delete` accepts exactly those.

---

## Technical Context

**Language/Version**: Python 3.12, unchanged

**Primary Dependencies**: `boto3`/`botocore` (pinned) and the Python standard library.
Telegram calls use `urllib.request`; no new package is added. The worker keeps
`yt-dlp` and the static `ffmpeg` build, whose `libmp3lame` encoder is already compiled
in (research D-08).

**Storage**: the existing S3 bucket and DynamoDB table, plus one GSI and two new item
types; SSM Parameter Store for the bot token, webhook secret, and allowlist

**Testing**: `pytest`, offline. Unit tests over parsing, tier mapping, name derivation,
selection, splitting and rendering; contract tests over the three bot entry shapes
(webhook, SNS, health) with fake AWS and Telegram clients; opt-in `e2e` only against a
deployed dev stack

**Target Platform**: AWS Lambda container images on `arm64`; one new HTTP API; SNS;
EventBridge; CloudWatch alarms; all in `eu-west-3`, with the two budget alarms still in
`us-east-1`

**Project Type**: single-project serverless service with two front doors, a terminal API
and a chat bot

**Performance Goals**:
- Acknowledge a well-formed request in under 5 seconds (FR-007, SC-001)
- Return a fresh link for an already stored video in under 5 seconds with no
  reprocessing (FR-024, SC-008)
- Post the completion message within seconds of the worker recording it
- Keep `/list`, `/delete` and `/empty` inside the 30-second integration ceiling

**Constraints**:
- Telegram: 4,096 characters per message, one message per second per chat, HTTPS only,
  no redirects, `secret_token` header on every call
- API Gateway: 30-second integration ceiling; bot Lambda 15 seconds, 512 MB, reserved
  concurrency 5
- No media bytes through the chat (FR-009); presigned links 3,600 seconds or less
- The bot route is public by necessity: secret header, stage throttling, and the
  allowlist are the three bounds

**Scale/Scope**: one operator plus a small approved list, 2 environments, about 100
videos per month, 61 Terraform resources per environment (38 existing plus 23), 4 HTTP
routes across 2 APIs, 3 functions, 2 images, 1 table with 1 GSI, 3 SNS topics

---

## Constitution Check

*GATE: must pass before Phase 0 research. Re-checked after Phase 1 design.*

| # | Principle | Gate: does the plan... | Status | Evidence |
|---|-----------|------------------------|--------|----------|
| I.1 | IaC only | declare every AWS resource in Terraform, with no console or ad-hoc CLI step? | PASS | All 23 additions are declared in `infra/`. The one object outside AWS, the Telegram webhook, is managed by `terraform_data` plus one reviewed script, and recorded in Complexity Tracking (D-02) |
| I.2 | IaC only | keep state remote with locking, never committed? | PASS | Same S3 backend and lock file. The bot token is a variable, not a committed file; see the risk row below |
| I.3 | IaC only | pin provider, module and lock versions? | PASS | No new provider and no new module. The Telegram integration is shell plus the AWS CLI, both already required by the Makefile |
| I.4 | IaC only | parameterise `dev` and `prod` as one code path? | PASS | Three new variables (`telegram_bot_token`, `telegram_approved_user_ids`, `telegram_default_tier`) and one optional test video id; no environment branching |
| I.5 | IaC only | remove the whole environment on destroy? | PASS | The webhook removal runs in the destroy phase, before the SSM parameters vanish. Quickstart step 14 proves `url` is empty afterwards (FR-031, SC-012) |
| II.1 | Lambda limits | never return video bytes in a response? | PASS | The bot posts a presigned URL; no response body carries media (FR-009) |
| II.2 | Lambda limits | keep the pipeline validate, async job, S3, short-lived URL? | PASS | The bot calls the same submission path, and the same worker and presigner do the rest |
| II.3 | Lambda limits | split work that can exceed one invocation, and make stages idempotent? | PASS with justification | Video stays copy-only. The audio path re-encodes and is bounded by the 60-minute cap and the 780-second deadline. Idempotency is deterministic keys plus `update_id` in the job transaction (D-06, D-08) |
| II.4 | Lambda limits | vendor and pin the media toolchain? | PASS | No new binary. The MP3 encoder is inside the existing pinned `ffmpeg` (D-08), and the yt-dlp version gains a single source file (D-13) |
| II.5 | Lambda limits | declare timeout, memory and reserved concurrency per function? | PASS | `bot`: 512 MB, 15 s, reserved 5, Telegram `max_connections = 5`. Existing functions unchanged |
| III.1 | Least privilege | one role per function, scoped to exact actions and resources, no wildcards? | PASS | The bot role names the table, the one index ARN, the queue, `output/*`, and three SSM parameter ARNs. The worker gains `sns:Publish` on one topic only |
| III.2 | No public exposure | Block Public Access everywhere, links at most one hour? | PASS | The bucket is unchanged and still blocked; links keep `ExpiresIn <= 3600`. The public route is bounded by a secret header, throttling and the allowlist (D-01, D-03) |
| III.3 | No public exposure | no long-lived credentials in code, images, environment variables or logs? | PASS | Bot token and webhook secret are SecureString parameters read at run time. No secret is an environment variable. The interaction text and logs are sanitised (D-03, contract privacy rules) |
| III.4 | No public exposure | encryption at rest, lifecycle expiry, `Project`/`Environment` tags? | PASS | SSE-S3 and the 7-day lifecycle are unchanged. Every new resource carries `local.tags` |
| IV.1 | Test-first | unit tests precede pure logic? | PASS | New pure logic: message parsing, tier mapping, name derivation, pagination splitting, history rendering, audio selection (D-07 to D-09) |
| IV.2 | Test-first | every handler contract-tested with synthetic events and stubbed clients? | PASS | Three entry shapes for `bot` plus the worker's publish branch, against fakes; no network, no Telegram, no AWS |
| IV.3 | Test-first | default suite offline and deterministic; end-to-end separate? | PASS | `make test` stays offline; only `make test-e2e` touches dev |
| IV.4 | Test-first | input validation a tested boundary? | PASS | Foreign hosts, collections, live links, two links, unknown words and unapproved accounts are asserted at unit and contract level (FR-028) |
| V.1 | Operations | structured logs, one line per event, correlated, no account identifiers or URLs? | PASS | The bot logs `update_id` and outcomes, not account ids, tokens, or source URLs (contract privacy rules) |
| V.2 | Operations | alarms before launch, each with a named response? | PASS | Two new alarms: bot errors, and webhook health below 1 for two hours. Both have runbook rows (D-12) |
| V.3 | Operations | rate limited and concurrency capped? | PASS | The hourly per-account limit is reused for Telegram identities; bot reserved concurrency 5; stage throttling 10 rps (D-03) |
| V.4 | Operations | explicit cost ceilings: duration, resolution, lifecycle, monthly budget? | PASS | Caps unchanged; the added estimate is about $0.25/month against the $10 ceiling (D-14) |
| V.5 | Operations | failures classified and surfaced as distinct, actionable errors? | PASS | The five existing categories are reused; the bot adds a plain-language sentence and a retry hint (FR-027) |
| V.6 | Operations | treat cost ceilings as guardrails, never inducing spend to validate them? | PASS | No step in the quickstart sends traffic to reach a limit. The rate-limit behaviour is asserted offline, and `/test` is one operator-invoked job (FR-019, D-14) |
| C.1 | Constraints | container images, Python 3.12 baseline kept? | PASS | The bot reuses the `-api` container; no new runtime |
| C.2 | Constraints | acceptable use respected; no public ripping service; no DRM bypass? | PASS | Private chats only, allowlist mandatory, unapproved accounts learn nothing (FR-020, FR-021) |
| C.3 | Constraints | copyleft obligations recorded? | PASS | No new bundled tool. MP3 encoding uses the existing LGPL `libmp3lame`; `THIRD-PARTY-NOTICES.md` already records the ffmpeg build and is updated in words only |
| W.1 | Workflow | plan reviewed before implementation; changes attributable? | PASS | This document. Infrastructure and application changes ship as separate commits, as 001 did |
| W.2 | Workflow | deployment order dev then prod; immutable tags; rollback by re-applying a tag? | PASS | Unchanged. The bot deploys with the same `<sha>-api` tag as the API |
| W.3 | Workflow | documentation part of done? | PASS | README, runbook (bot sections), 001's superseded assumptions, and the quickstart ship in the same change |

**Gate result: PASS.** No principle is violated. Six design choices need to be recorded
as complexity rather than waived, and all six are in Complexity Tracking with the
simpler alternative that was rejected.

---

## Project Structure

### Documentation (this feature)

```text
specs/002-telegram-bot-interface/
├── plan.md                        # This file
├── research.md                    # Phase 0: 15 decisions, verified facts, sources
├── data-model.md                  # Phase 1: entities, index, access patterns, invariants
├── quickstart.md                  # Phase 1: runnable validation steps
├── contracts/
│   ├── telegram-webhook.md        # Inbound update, auth, status codes, outbound call
│   ├── bot-commands.md            # Verbatim help text, commands, templates, formats
│   └── job-event.md               # SNS body, publish conditions, consumer rules
├── checklists/
│   └── requirements.md            # Spec quality checklist (already complete)
├── spec.md                        # Behaviour specification
└── tasks.md                       # Phase 2 output (/speckit.tasks, not created here)
```

### Source Code (repository root)

Only the new or touched paths are listed. Everything else stays as documented in 001.

```text
app/
├── ytdlp-version.txt              # NEW: the one source of the extraction version
├── Dockerfile                     # touches: installs from the version file, copies it
├── src/ytdl/
│   ├── submissions.py             # NEW: accept a request (validate, rate, create, enqueue)
│   ├── quality.py                 # touches: adds 240p
│   ├── formats.py                 # touches: hard ceiling, bounded fallback, audio selection
│   ├── keys.py                    # touches: media extension, owner keys, interaction keys
│   ├── config.py                  # touches: bot settings, version, topic ARN
│   ├── api/
│   │   ├── handler.py             # touches: delegates acceptance to submissions.py
│   │   └── routes.py              # unchanged
│   ├── bot/
│   │   ├── handler.py             # NEW: webhook, SNS and health dispatch
│   │   ├── commands.py            # NEW: pure text to intent parsing
│   │   ├── messages.py            # NEW: fixed strings, including the help constant
│   │   ├── library.py             # NEW: list, delete, empty, history rendering
│   │   ├── access.py              # NEW: allowlist parsing and the private-chat rule
│   │   └── client.py              # NEW: Telegram HTTP client with a typed error policy
│   ├── worker/
│   │   ├── handler.py             # touches: publishes the job event, terminal only
│   │   ├── pipeline.py            # touches: audio branch
│   │   └── remux.py               # touches: convert_to_mp3
│   └── adapters/
│       ├── dynamo.py              # touches: extra transactional puts, queries, batch get
│       └── s3.py                  # touches: content type, delete object
└── tests/
    ├── unit/                      # parsing, tiers, names, splitting, rendering, selection
    ├── contract/                  # bot webhook, bot SNS, bot health, worker publish, API
    └── e2e/                       # opt-in, deployed dev stack

infra/
├── bot.tf                         # NEW: API, route, role, function, SNS, SSM, webhook
├── scripts/
│   └── telegram-webhook.sh        # NEW: setWebhook and deleteWebhook, used by Terraform
├── state.tf                       # touches: the owner-created GSI
├── functions.tf                   # touches: worker publish permission, worker environment
├── alarms.tf                      # touches: two new alarms
├── variables.tf                   # touches: four new variables
├── outputs.tf                     # touches: bot URL, function, token parameter, topic
└── env/
    ├── dev.tfvars                 # touches: default tier and test video, not secrets
    └── prod.tfvars                # touches: same

Makefile                           # touches: bot-help-check, bot-token, bot-health
README.md                          # touches: the chat front door
docs/runbook.md                    # touches: bot failures, alarm rows, token rotation, allowlist
```

**Structure Decision**: the bot is a sibling of `api` and `worker`, not a layer inside
either. The pure logic (parsing, tier mapping, name derivation, rendering) sits beside
`formats.py` and `quality.py` with no I/O, so the constitution's test-first rule stays
mechanically checkable. The only shared mutable path is `submissions.py`, extracted from
the API handler so both front doors validate, rate-limit, record and enqueue identically;
duplicating that block would be the one place where the two front doors could drift.

`infra/bot.tf` holds everything the bot adds in one reviewable file, because the pieces
are meaningless apart: an API route without the function, or a function without its
allowlist parameter, is not a partial deployment. The GSI stays in `state.tf` because it
is the table's shape, and the alarms stay in `alarms.tf` because that file is the one
place to count them.

---

## Phase 0 and Phase 1 outputs

| Artifact | Status | Content |
|----------|--------|---------|
| `research.md` | complete | 15 decisions with rationale and rejected alternatives; 12 verified platform facts with sources; the cost model; the deliberate exclusions |
| `data-model.md` | complete | Extended Job, new Interaction entity, `owner-created-index`, five access patterns, media keys and display names, tier table, deleted overlay, eight new invariants |
| `contracts/telegram-webhook.md` | complete | Request shape, header auth, five response classes, error policy, idempotency, privacy rules |
| `contracts/bot-commands.md` | complete | The verbatim help text, command table, grammar, tier table, every reply template, list and history formats, `/empty` confirmation |
| `contracts/job-event.md` | complete | SNS body, publish conditions, consumer rules, at-least-once semantics |
| `quickstart.md` | complete | 14 validation steps from BotFather to a teardown that proves the webhook is gone, plus the list of what was deliberately not exercised |

No `NEEDS CLARIFICATION` markers remain anywhere in the plan or the artifacts.

---

## Implementation Sequence

Each phase ends in a command whose result is checkable. The order puts the riskiest
external assumption (a Lambda that answers Telegram in under five seconds) before the
volume of command work.

| Phase | Work | Verify |
|-------|------|--------|
| A | `submissions.py` extraction, `240p`, `formats` hard ceiling and audio selection, `keys` extensions. Tests first. | The existing 551 tests still pass, and new unit tests cover capping, fallback, audio selection and key derivation |
| B | GSI in `state.tf`, adapter methods (extra puts, owner query, batch get, soft delete), `update_id` rollback semantics | Contract tests against a fake DynamoDB assert one job, one rate increment and one interaction per update, and none on a duplicate |
| C | `bot/` modules: parsing, access, messages, library, client, handler. Tests first. | Offline contract tests cover all three entry shapes, every command, both refusals, and the 4,096-character split |
| D | Worker: audio branch, MP3 conversion, terminal-only SNS publish. The pre-download existence check and the completion-from-existing path both derive the extension and the quality label from `media_kind`, so an audio retry still reports `mp3` | Unit tests for the MP3 command and the publish conditions; the audio job completes in the pipeline test |
| E | Terraform: `bot.tf`, GSI, IAM, topic, alarms, health rule, webhook script, Makefile targets | `make tf-check` clean; a reviewed `terraform plan` shows 23 additions and one in-place table update |
| F | Documentation: README, runbook sections, 001's superseded audio assumption and 240p note, third-party notices wording | The docs describe what the deployed stack does, and the quickstart matches the commands |
| G | Deploy to dev and run the quickstart, one request per tier, one `/test` | Steps 4 to 14 of `quickstart.md`, stopping before anything that would generate load |

Phase G is deliberately the last. Deploying earlier would let a half-built bot register a
webhook and start answering a real operator with incomplete behaviour.

---

## Post-Design Constitution Re-Check

*Required after Phase 1. Design can introduce risks the pre-research check could not see.*

| Risk introduced by design | Assessment | Result |
|---------------------------|------------|--------|
| The bot route is publicly reachable | Three independent bounds: the `secret_token` header, stage throttling, and reserved concurrency. A forged call gets 403 and costs one invocation. The bot does not reveal whether it is a download service | PASS |
| The bot token passes through Terraform state | `sensitive = true`, stored in an SSM SecureString, state encrypted at rest and access-controlled. The API token already sets this precedent (`random_password`). The token is never in an environment variable, a file, or a log | PASS, recorded |
| GSI reads are eventually consistent | The bot never derives a deletion or a completion from the index. Deletes report from the write, and completion messages come from the job record. A list issued in the same second may lag, which is invisible at chat speed | PASS, recorded |
| Soft delete leaves a job record after the file is gone | Intentional: audit and history keep meaning, `/list` filters it out, reuse ignores it, and the terminal API reports `expired` because the object is gone | PASS, recorded |
| Audio re-encodes, breaking 001's copy-only assumption | Bounded by the same 60-minute cap and the 780-second deadline. A 60-minute MP3 encode is minutes, and audio downloads are far smaller than video. If re-encoding ever expands to video, this gate fails and the design must be split | PASS, conditional |
| A tier fallback can exceed the requested ceiling | It can never exceed `MAX_HEIGHT`, which is the cost guard. The reply states the produced quality, so nothing is implied | PASS |
| SNS delivers at least once | A duplicate completion message may appear. It contains a link the operator asked for, and the alternative is a delivery ledger that can disagree with the chat | PASS, accepted |
| The webhook provisioner cannot run if its block is deleted | Documented two-step removal in the runbook. A destroy contract that silently skips is worse than one with a written procedure | PASS, documented |
| Telegram becomes a third-party dependency in the request path | Already true of YouTube. The hourly webhook check makes a broken link to Telegram visible in our own alarms, and the terminal API keeps working when the bot does not | PASS, accepted |
| The 240p value changes the terminal API's accepted qualities | Additive. The only behavioral change is that a request with nothing at or below its tier now succeeds at a lower-than-maximum quality instead of failing | PASS, recorded |

**Post-design gate result: PASS.** No Complexity Tracking entry is required beyond the
six below.

---

## Complexity Tracking

> Filled because six choices need justification, not because a principle is violated.

| Choice | Why needed | Simpler alternative rejected because |
|--------|------------|--------------------------------------|
| `terraform_data` plus a shell script to own the webhook | FR-031 and SC-012 require that destroy leave no bot registration. No maintained Terraform provider exposes a webhook resource, and a Lambda invoked during apply has no destroy phase | A manual `setWebhook` is invisible to review and survives destroy; `restapi_object` does not match Telegram's id-less response and second-method removal; the 2020 community provider is unmaintained |
| The bot reuses the `-api` image | The bot needs no media tooling, every module is already in `src/`, and both functions deploy from one commit | A third image adds a build and a push for a few hundred kilobytes; folding the bot into the API function merges two trust models and one concurrency pool |
| A sparse GSI instead of a second table | The table has no sort key, and ownership must be queried in order. The index is added in place, only bot items are projected, and no media fact is duplicated | A second table is strongly consistent but duplicates `media_key`, expiry and state, and its delete path can diverge; recreating the table is a destructive migration |
| MP3 re-encoding | The help text promises MP3, and the existing pinned ffmpeg can produce it | Copying the source audio yields `.m4a`, not MP3; `yt-dlp -x` moves the decision into a postprocessor that is harder to bound and test |
| The bot token is a sensitive Terraform variable | Terraform must write it to SSM on a fresh environment, with no manual console step | An out-of-band `aws ssm put-parameter` is a manual step that `terraform destroy` and a fresh account cannot reproduce; the API token already follows the variable-to-SSM pattern |
| The hourly webhook health check | The spec requires a revoked webhook not to be silent, and an idle bot is otherwise indistinguishable from a broken one | Without it, the first symptom is a human noticing. A Synthetics canary costs more than the bot itself |

---

## Risks and Responses

| Risk | Likelihood | Response |
|------|------------|----------|
| Telegram retries a webhook after a timeout, producing a second acknowledgement | Medium | `update_id` in the job transaction; a duplicate is a no-op with a 200 |
| A completion message is lost because SNS gave up | Low | `/list` and a repeated request both recover the link; the job record is the truth |
| The webhook is removed or the token revoked | Low | Hourly `getWebhookInfo` metric and alarm; runbook row names the re-registration step |
| `/empty` over a large library exceeds the 30-second ceiling | Low | Bounded at 200 items per confirmation, with the remainder stated, never claimed as complete |
| A source has no rendition at or below a tier | Medium | Lowest rendition at or below `MAX_HEIGHT`, and the reply names the produced quality |
| Audio encoding approaches the timeout on a 60-minute track | Low | Same duration cap and deadline guard; if observed, the audio path is the first candidate to move into its own stage |
| YouTube refuses the Lambda egress address | Unchanged from 001 | The existing cookies and proxy hooks and failure category still apply; the bot relays the same sentence |

---

## Documentation Changes That Are Part of Done

| File | Change |
|------|--------|
| `README.md` | A section on the chat front door: creating the bot, the allowlist, the five tiers, and the fact that links expire before files do |
| `docs/runbook.md` | Bot failure sentences, the two new alarm rows, token rotation, allowlist changes, and the two-step webhook removal |
| `specs/001-youtube-video-download/spec.md` | The "audio-only out of scope" assumption is amended to point at 002, rather than contradicted silently |
| `specs/001-youtube-video-download/contracts/api.md` | `240p` joins the accepted qualities, and the bounded fallback is stated |
| `specs/001-youtube-video-download/data-model.md` | The Quality Preference table gains the fallback row, so the two features describe one rule |
| `app/THIRD-PARTY-NOTICES.md` | A sentence recording that MP3 encoding uses the LGPL `libmp3lame` already inside the ffmpeg build. No new licence obligation |

---

## Out of Scope for This Plan

Anything listed in research D-15, and two things the spec left open on purpose:

- **Archived bundles.** The help text's `filename.zip` stays as written, but the files
  are playable `.mp4` and `.mp3`. If archives are wanted, that changes storage, naming
  and the help text together, and it is a separate specification.
- **A self-service approval flow.** The allowlist is configuration. There is no
  invitation, no request-to-join, and no admin command.

---

## Next Step

`/speckit.tasks` breaks this plan into ordered, independently testable tasks. Three
should be scheduled early because they can change later decisions:

1. The bot function, deployed, answering `/start` through a real webhook. It proves the
   five-second budget and the registration script before any command work.
2. The transactional `update_id` guard, because every later command assumes it.
3. The audio path end to end on one real track, because it is the only place where the
   single-invocation assumption is stretched.
