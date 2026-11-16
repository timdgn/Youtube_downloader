# Feature Specification: YouTube Video Download Service

**Feature Branch**: `001-youtube-video-download`

**Created**: 2026-11-15

**Status**: Draft

**Input**: User description: "aws lambda application with terraform to deploy, at the end i give an url of a youtube video here and it downloads the video through aws lambda"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Submit a video link and receive the finished file (Priority: P1)

The operator pastes a YouTube video URL into a documented terminal command. The service validates the link, accepts the request, and immediately hands back a job identifier. The operator then asks for the job's result and receives a temporary download link, which they open or fetch to obtain the finished video file with picture and sound already combined, at a sensible default quality.

**Why this priority**: this is the entire product. Without it there is nothing to observe, tune, or deploy.

**Independent Test**: submit one URL for a short public video from a fresh terminal, poll the job until it reports completion, follow the returned link, and confirm the downloaded file plays with audio and video in sync. This single path delivers full value on its own.

**Acceptance Scenarios**:

1. **Given** a supported single-video URL, **When** the operator submits it, **Then** the service acknowledges the request with a job identifier in under 3 seconds and starts working in the background.
2. **Given** an accepted job, **When** processing finishes, **Then** the job reports completion and provides a download link that works without any further credentials beyond the link itself.
3. **Given** a completed job whose link has not yet expired, **When** the operator follows the link, **Then** the downloaded file plays end to end with correctly combined audio and video.
4. **Given** a completed job whose earlier link has expired but whose file is still within its storage window, **When** the operator asks for the result again, **Then** a fresh working link is returned without reprocessing the video.
5. **Given** a URL that is not a supported single video (wrong host, malformed identifier, playlist, channel, live stream), **When** the operator submits it, **Then** the request is rejected immediately with a distinct, human-readable reason and no download work is started.

---

### User Story 2 - See what a job is doing, and why it failed (Priority: P2)

The operator submits a link and wants to know where it stands, especially when a video is long or unavailable. They query the job by identifier and get an honest state (accepted, processing, completed, failed), timestamps, and, on failure, a category and message that tells them whether to fix their input, wait and retry, or give up on that video. When the result has expired from storage, the job says so plainly instead of returning a broken link.

**Why this priority**: downloads fail routinely for reasons unrelated to our code (private video, region lock, upstream change). Without clear reporting the operator cannot tell a bad input from a broken system, and every failure becomes an investigation.

**Independent Test**: submit one URL that will fail (for example a private or unavailable video) and one that will succeed, then poll both. The failing job must surface a specific failure category and message; the succeeding job must show the expected state transitions in order.

**Acceptance Scenarios**:

1. **Given** a job that has just been accepted, **When** the operator queries it, **Then** the state is reported as accepted or processing with a creation timestamp, and no error is implied.
2. **Given** a job that failed because the video is unavailable, **When** the operator queries it, **Then** the response states that the source refused the video, marked as not retryable, and no download link is offered.
3. **Given** a job that failed for a transient reason (source throttling, network interruption), **When** the operator queries it, **Then** the response marks the failure as retryable and suggests submitting again.
4. **Given** a completed job whose stored file has passed its retention window, **When** the operator queries it, **Then** the response reports the result as expired and instructs the operator to submit the link again.
5. **Given** any job, **When** the operator queries it repeatedly, **Then** answers are consistent and no query itself changes the job's state or duplicates work.

---

### User Story 3 - Ask for a specific quality (Priority: P3)

The operator optionally states a preferred quality when submitting a link. The service honours it when the source offers it, silently falls back to the closest available quality when it does not, and says in the job result which quality was actually produced.

**Why this priority**: a default quality covers most cases, so this is a refinement. It matters for archiving and bandwidth, not for the feature to be useful.

**Independent Test**: submit the same short video twice, once with a low quality preference and once with a high one, and confirm each produced file matches the reported quality, then submit with a quality the source does not offer and confirm a documented fallback with the actual quality reported.

**Acceptance Scenarios**:

1. **Given** a submission with no quality preference, **When** it completes, **Then** the file is produced at the documented default ceiling and the result states the actual quality.
2. **Given** a submission with a quality the source offers, **When** it completes, **Then** the file matches that quality within one step and the result states it.
3. **Given** a submission with a quality the source does not offer, **When** it completes, **Then** the closest available quality is used, the fallback is recorded in the result, and the job does not fail for this reason.
4. **Given** a submission with a quality above the configured ceiling, **When** it is validated, **Then** it is capped to the ceiling rather than rejected, and the cap is reported.

---

### User Story 4 - Stand the service up, and tear it down, without touching a console (Priority: P4)

The operator, starting from an empty cloud account, provisions a complete working instance of the service from a single reviewable definition kept in version control, then exercises the end-to-end download. Later they remove that instance completely with one action, leaving no chargeable leftovers. They can keep a non-production and a production instance of the same definition side by side.

**Why this priority**: the environment is a prerequisite for every other story, but it is only built once and its value is in repeatability rather than feature behaviour. It is graded last only because the download flow defines what has to exist.

**Independent Test**: from an empty account, follow only the repository documentation, provision an environment, run one successful download, then remove the environment and confirm no chargeable resources remain.

**Acceptance Scenarios**:

1. **Given** an empty cloud account and the repository, **When** the operator follows the documented provisioning steps, **Then** a working environment exists with no manual console or ad-hoc command steps.
2. **Given** a provisioned non-production environment, **When** the operator applies the same definition with production settings, **Then** a second, independent environment is created without editing the definition's logic.
3. **Given** a provisioned environment, **When** the operator removes it, **Then** every resource created by the definition is gone and no further charges accrue.
4. **Given** the definition is applied twice with no changes in between, **When** the second run is reviewed, **Then** it reports no changes to make.

---

### Edge Cases

- A share link in short form, a link carrying extra tracking parameters, or a link with a start-time marker: handled by normalising to the underlying video, or rejected with the same clear reason as any malformed input.
- A playlist link that points at one specific video: treated as that single video, or rejected as a collection; the choice must be documented and consistent.
- A video whose duration or size exceeds the configured limit: rejected after the source's metadata is read but before any media is transferred, so no bandwidth is spent.
- A video that is private, age-restricted, region-locked, members-only, DRM-protected, or a live broadcast: rejected with a specific failure category and no retry suggestion where retrying cannot help.
- A job that runs longer than a single processing window: continued in a follow-up stage without re-downloading finished work and without producing a second file.
- The same job executed twice because of a retry or a re-delivery: exactly one stored file, with the second attempt recognising the first's output.
- Storage exhausted or the process killed mid-write: the job is reported as failed with an internal classification, never as completed with a truncated file.
- Two submissions of the same link in quick succession: each gets its own job and file, and the rate cap, not deduplication, protects capacity.
- A download link followed after its lifetime: re-issuing is allowed while the file is still stored; once the file is gone the job reports expiry and asks for a resubmission.
- Upstream changes break extraction without warning: the job fails with a specific upstream-related category, distinguishable from invalid input, so the operator knows to update the tool rather than their link.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The service MUST accept a video page URL plus an optional quality preference, and acknowledge the request with a job identifier without waiting for the download to finish.
- **FR-002**: The service MUST reject any input that is not a single video from the supported source, including foreign hosts, malformed identifiers, collections, channels, and live broadcasts, before any media is transferred, returning a distinct reason.
- **FR-003**: The service MUST complete acceptance of a valid request in under 3 seconds, independently of the video's duration or size.
- **FR-004**: The service MUST process accepted jobs asynchronously and MUST NOT hold a request open while media is transferred.
- **FR-005**: The service MUST deliver the finished media only as a temporary download link with a lifetime of 60 minutes or less; it MUST NOT return media bytes inside a submission or status response.
- **FR-006**: The service MUST re-issue a fresh link on demand for any completed job whose file is still stored, without reprocessing the video and without counting against the submission rate limit.
- **FR-007**: The service MUST report, for a given job identifier, the job's state (accepted, processing, completed, failed), its creation and last-update times, and the actual quality produced when completed.
- **FR-008**: The service MUST report every failure with one of a fixed set of categories (invalid input, unsupported or unavailable video, limit exceeded, transient source problem, internal error) and MUST mark whether resubmitting the same link could succeed.
- **FR-009**: The service MUST enforce a maximum video duration, a maximum output quality, and a maximum output size; it MUST reject or cap a request that would exceed them before transferring media.
- **FR-010**: The service MUST produce a file whose audio and video are present and synchronised for every completed job, at the requested quality when available, otherwise at the closest available quality, and it MUST state which quality was produced.
- **FR-011**: The service MUST default to a documented quality ceiling when no preference is given, and MUST cap requests above the ceiling rather than fail them.
- **FR-012**: The service MUST expire stored media automatically after a configured retention window and MUST report an expired result as such rather than offering a dead link.
- **FR-013**: The service MUST be idempotent per job: repeating or retrying the processing of one job MUST NOT create a second stored file or duplicate side effects.
- **FR-014**: The service MUST limit submission rate and the number of jobs processed simultaneously, and MUST answer requests above those limits with a distinct throttled response that tells the operator to retry later, within one second.
- **FR-015**: The service MUST record one audit entry per job (identifier, source host and video identifier, requested and produced quality, outcome, processing duration, output size), and MUST NOT record credentials, tokens, or full URLs including query parameters.
- **FR-016**: The service MUST be provisionable from a single version-controlled definition with no manual console steps, parameterised so that a non-production and a production environment are created from the same logic.
- **FR-017**: The service MUST be fully removable: one documented action deletes every resource the definition created, leaving no chargeable remnants.
- **FR-018**: The service MUST expose documentation covering how to submit a link, how to check a job, how to fetch the file, how to provision and remove an environment, and what each failure category means.
- **FR-019**: The service MUST be callable from a terminal using only documented commands, with no project-specific client to install.

### Key Entities *(include if data involved)*

- **Download Job**: one request for one video. Holds a unique identifier, the source video reference, the requested quality, the current state, timestamps for creation and last update, the produced quality, the failure category and message when failed, and a reference to any produced file.
- **Media File**: the downloaded and combined result of a job. Holds its size, media quality, creation time, expiry time, and the job it belongs to. Never more than one per job.
- **Quality Preference**: the operator's requested output quality, constrained by a configured ceiling; carries a documented default and a documented fallback rule when the requested quality is unavailable.
- **Environment**: one deployed instance of the service with its own storage, limits, retention window, rate and concurrency caps, and access credential. At least a non-production and a production environment exist.
- **Audit Entry**: the durable record of one job's outcome, used for cost attribution, troubleshooting, and support of the retention and access guarantees.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of valid submissions are acknowledged in under 3 seconds, for videos of any length within the configured limit.
- **SC-002**: At least 95% of jobs for videos up to 30 minutes at the default quality become downloadable within 10 minutes of submission.
- **SC-003**: 100% of rejected inputs are rejected before any media is transferred, verified by zero media bytes recorded for those jobs.
- **SC-004**: 100% of completed jobs produce a file that plays start to finish in a standard media player with audio and video in sync.
- **SC-005**: Zero stored files survive past the configured retention window, verified by a periodic inventory comparison.
- **SC-006**: A first-time operator goes from an empty cloud account to a successful downloaded file in under 30 minutes using only repository documentation, with zero manual console actions.
- **SC-007**: Removing an environment leaves zero chargeable resources attributable to it, verified by an inventory check after removal.
- **SC-008**: Zero duplicate media files across 100 deliberately repeated or retried job executions.
- **SC-009**: Zero credentials, tokens, or full URLs with query parameters appear in logs or audit records, verified by a scan of a representative retention period.
- **SC-010**: 100% of requests above the configured rate or concurrency limit receive the throttled response within one second, rather than a timeout or a generic error.
- **SC-011**: A documented usage profile of 100 downloaded videos per month at the default quality, kept for 7 days, stays within the documented monthly budget ceiling.
- **SC-012**: 100% of completed jobs still within their 7-day storage window return a fresh working link on demand, with zero reprocessing of the video.

## Assumptions

- **Storage is bucket-based and private**: one bucket per environment, as fixed by the constitution. Finished media is written there and nowhere else; the processing environment holds no lasting copy. Objects are unreachable except through the temporary links the service issues, and public access is disabled.
- **Delivery model is fixed by the project constitution**: serverless functions and infrastructure as code (provisioning and removal of every resource from one version-controlled definition) are mandated there and are therefore not restated here as requirements. This specification describes behaviour, not platform choices.
- **Single operator, personal use**: the service is not multi-tenant, has no public sign-up, and has no user interface beyond terminal commands. Use is limited to content the operator is entitled to download, as recorded in the constitution's acceptable-use constraint.
- **"Here" means the operator's terminal**: the primary interaction is a documented command that takes a URL, prints a job identifier, and a second command that prints the result link.
- **One supported source in v1**: YouTube only. Any other host is rejected, including hosts that are otherwise technically extractable.
- **Single videos only in v1**: playlists, channels, live broadcasts, premieres, members-only, age-restricted, region-locked, and DRM-protected content are out of scope and produce a defined rejection.
- **Video with audio only in v1**: audio-only extraction, subtitles, thumbnails, and metadata sidecars are out of scope for the terminal interface described here. The Telegram front door specified in `specs/002-telegram-bot-interface/spec.md` does deliver MP3 for its `mp3` tier, so the assumption is amended here rather than contradicted silently.
- **Default output**: best available video up to 1080p, with audio combined into a single playable file. The container/codec combination is chosen at planning time and documented.
- **Default limits**: maximum 60 minutes duration, maximum 1080p, maximum 4 GB output; these are configuration values, changeable per environment without code changes.
- **Default access control**: every request must present a shared access credential; the service is not open to anonymous callers. Credential rotation is a documented operator task.
- **Default rate and concurrency caps**: 10 submissions per hour per credential and 2 jobs processed at once, configurable per environment.
- **Default retention**: media files are kept for 7 days after creation, then deleted automatically; download links live 60 minutes and may be re-issued on request at any time while the file is stored. Storage lifetime and link lifetime are two different things: the file survives the link, not the other way round.
- **Default budget**: the monthly ceiling for the usage profile in SC-011 is set at planning time and documented, with an alert configured before first production use.
- **No deduplication in v1**: identical links submitted twice produce two independent jobs; the rate cap is the protection against accidental load.
- **Documentation is part of done**: invocation examples, failure categories, environment variables, and a short operational runbook (how to read logs, what to do for each failure category) ship with the feature.
