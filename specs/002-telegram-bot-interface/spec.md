# Feature Specification: Telegram Bot Interface

**Feature Branch**: `002-telegram-bot-interface`

**Created**: 2026-11-16

**Status**: Draft

**Input**: User description: "i want the user to use a telegram bot to talk, like give '[URL] [resolution]' ... and the telegram bot gives the url of download. And with the help message in the bot like ..." The resolution vocabulary, the tier ceilings, and the help text are reproduced verbatim in the Command Surface section below.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ask for a video in one chat message and receive a link (Priority: P1)

The operator opens a private chat with the bot and sends a single message containing a YouTube link followed by one of five resolution words. The bot confirms the request immediately, works in the background, and then posts a temporary download link into the same chat, saying when that link stops working. The operator taps the link on a phone or opens it on a laptop and gets the finished file. Asking for the same link again later, while the file is still stored, returns a fresh working link without downloading anything twice.

**Why this priority**: this is the whole feature. The existing service already downloads and stores videos; the bot is the conversational front door that makes it usable from a phone without a terminal.

**Independent Test**: from an approved account, send one message with a short public video and the word `medium`, wait for the bot's reply, follow the link, and confirm the downloaded file plays with picture and sound. This single path delivers the full value on its own.

**Acceptance Scenarios**:

1. **Given** an approved operator in a private chat, **When** they send a valid YouTube link and a resolution word, **Then** the bot acknowledges the request within seconds, without waiting for the download, and states which resolution tier was understood.
2. **Given** an accepted request, **When** processing finishes, **Then** the bot posts a download link in the same chat that works with no further credential, and states the link's expiry time.
3. **Given** a link the operator already requested at the same tier and whose file is still stored, **When** they send the same message again, **Then** the bot replies with a fresh link in seconds and does not download the video a second time.
4. **Given** a link with no resolution word, **When** the operator sends it, **Then** the bot uses the documented default tier and says so.
5. **Given** a link that is not a supported single video (wrong host, malformed identifier, playlist, channel, live broadcast), **When** the operator sends it, **Then** the bot refuses with a plain-language reason and starts no download.
6. **Given** a request for audio only, **When** it completes, **Then** the operator receives a playable audio file link rather than a video file.

---

### User Story 2 - Keep the bot private to approved people (Priority: P2)

Any Telegram account can find and message a bot. The operator therefore approves a small list of accounts, and everyone else is turned away without learning what the bot does, without triggering a download, and without consuming the operator's capacity or budget. Group chats are not served.

**Why this priority**: a download bot reachable by anyone is a public ripping service, which the project constitution forbids. It is also the only thing standing between an open bot handle and unbounded cost. It is graded second only because there is no bot to protect until Story 1 exists.

**Independent Test**: from an account not on the approved list, send a valid link and a valid resolution word. Confirm the bot refuses, and confirm from the operator's side that no job, file, or charge was created. Repeat from a group chat.

**Acceptance Scenarios**:

1. **Given** an account that is not approved, **When** it sends any message, **Then** it receives a short refusal that reveals neither the available commands nor that downloads are possible, and no work is started.
2. **Given** an approved account, **When** it sends a request from a group chat, **Then** the request is refused and the bot states that it only works in a private chat.
3. **Given** the approved list changes, **When** the change is applied, **Then** the new list governs the next message with no other change needed.
4. **Given** a removed account, **When** it sends a request, **Then** it is refused as in scenario 1 and sees none of its earlier items.

---

### User Story 3 - See, refresh, and delete what is stored (Priority: P3)

The operator asks the bot what is currently stored and gets one line per item: a name to use in later commands, the video it came from, the quality actually produced, its size, its age, and how long it has left before automatic deletion. They delete one item by name, or all of them at once when they want the storage back immediately. Everything they delete is reported honestly afterwards: the item disappears from the list, and a repeat request downloads it again.

**Why this priority**: files are deleted automatically after seven days, so a list is useful but not essential. Deleting early matters when a wrong file was fetched or storage should be freed now.

**Independent Test**: request two videos at two tiers, list them, delete one by name, list again, then empty the library and list a third time. Each listing must agree with what was actually deleted.

**Acceptance Scenarios**:

1. **Given** at least one stored item, **When** the operator lists them, **Then** each item appears once, with a unique name, its source video, the quality produced, size, age, and days remaining before automatic deletion.
2. **Given** a library larger than one chat message can hold, **When** the operator lists it, **Then** the list is delivered across consecutive messages without losing or duplicating an item.
3. **Given** a named stored item, **When** the operator deletes it, **Then** the file is removed, the confirmation names it, and a subsequent list no longer shows it.
4. **Given** a name that matches no stored item, **When** the operator deletes it, **Then** nothing is deleted and the reply says the name is unknown.
5. **Given** a library with items, **When** the operator empties it and confirms, **Then** every one of their items is removed, the reply states how many were removed, and the list is empty.
6. **Given** an item already deleted, **When** the operator sends its original link and tier again, **Then** the video is downloaded again instead of a dead link being offered.

---

### User Story 4 - Understand the bot and check that it works (Priority: P4)

The operator asks for help and receives the full command list and the resolution table. They ask for the bot's information and learn which extraction version is running and the limits that shape a request (quality ceiling, retention, link lifetime). They ask for their own recent interactions and see what they asked for and what happened, without credentials or expiring links. They trigger a single self-test and get a pass or a specific failure reason.

**Why this priority**: the bot is usable without any of this, but these commands are what stop every question from becoming a manual log reading.

**Independent Test**: send each documented command in turn from a fresh chat and confirm the reply matches the documented behaviour, including a self-test that passes on a healthy deployment.

**Acceptance Scenarios**:

1. **Given** a new chat, **When** the operator sends the start command, **Then** the bot greets them and points to the help command.
2. **Given** any chat, **When** the operator asks for help, **Then** the reply contains the command list and resolution table exactly as fixed below, complete and unaltered.
3. **Given** a healthy deployment, **When** the operator asks for information, **Then** the reply states the running extraction tool version, the quality ceiling, the retention window, and the link lifetime.
4. **Given** a healthy deployment, **When** the operator runs the self-test, **Then** the bot runs exactly one short test download and reports success, or reports the specific failure reason.
5. **Given** a request that ran while the submission rate was exhausted, **When** the operator runs the self-test, **Then** the bot reports the rate limit and the time when capacity returns, and does not bypass the limit.
6. **Given** recent activity, **When** the operator asks for history, **Then** the reply lists the most recent interactions with timestamps, what was asked, and the outcome, and contains no access tokens and no download links.

---

### Edge Cases

- A message containing two links, or a link and an unrecognised resolution word: refused as ambiguous, with the expected format restated and no download started.
- A resolution word sent alone, with no link: the bot restates the expected format and starts nothing.
- Resolution words in any capitalisation, with surrounding whitespace, or with a trailing full stop attached to the link: understood as the same request.
- An unrecognised word in the place where a resolution belongs (for example `hq`): refused with the five accepted words listed.
- A source that offers no rendition at or below the requested tier (only higher qualities exist): the lowest available quality is delivered, and the reply states the quality actually produced rather than implying the ceiling was met.
- An audio-only request against a source with no audio track: refused with a specific reason, not a silent empty file.
- A request for the same link and tier while that request is still processing: answered as already in progress, with no second job created.
- A message delivered twice by the chat platform, or the operator double-sending: exactly one item, one file, one link.
- A link followed after its 60-minute lifetime: the bot explains that links expire and returns a fresh one when the file is still stored.
- An item older than the retention window: it is absent from the list, and asking for the same link downloads it again.
- A library at the storage or per-item limit: refused with a clear reason that names the limit that was reached.
- The source refuses the download environment (the known upstream block): a distinct message says the source is refusing the service and that the operator must refresh its access material, which is different from a bad link.
- The submission rate is exhausted: a distinct message states when capacity returns, and no job is created.
- The self-test being repeated in quick succession: each run consumes the normal rate budget, and the bot never loops or schedules further runs on its own.
- A message from an approved operator naming another operator's item: treated as an unknown name, so one person's library is never disclosed to another.
- The chat platform can no longer reach the bot (revoked token or a removed subscription): this is a deployment fault that the operator sees when messages stop being answered, and it must not be silent in the service's own monitoring.

## Requirements *(mandatory)*

### Command Surface

The bot accepts exactly these commands. Each is documented in the help text and behaves as described in the user stories.

| Command | Behaviour |
|---------|-----------|
| `/start` | Greets the operator and points to the help command. |
| `/list` | Lists the operator's stored items, one line each, with name, source video, produced quality, size, age, and days before automatic deletion. |
| `/delete <name>` | Deletes one stored item named as shown by the list. |
| `/empty` | Deletes every stored item belonging to the operator, after an explicit confirmation. |
| `/history` | Lists the operator's most recent interactions with time, request, and outcome. |
| `/info` | Reports the running extraction tool version, the quality ceiling, the retention window, and the link lifetime. |
| `/test` | Runs exactly one short test download through the real pipeline and reports pass or a specific failure reason. |
| `/help` | Returns the help text fixed in FR-010. |

**The help text, reproduced verbatim and not to be paraphrased:**

```text
📚 Available commands:

/start - Start the bot
/list - List all your videos on the server
/delete filename.zip - Delete a specific video
/empty - Delete all videos
/history - Show your message history
/info - Display yt-dlp version
/test - Test the download pipeline
/help - Display this help

To download a YouTube video:
"[URL] [resolution]"

Available resolutions:
• low - low quality (240p)
• medium - medium quality (480p)
• high - high quality (720p)
• veryhigh - very high quality (1080p)
• mp3 - audio only (MP3 format)

Example: "https://www.youtube.com/watch?v=example medium"
```

**Resolution vocabulary and ceilings.** Each tier is a ceiling, not an exact quality: the service delivers the best quality at or below the ceiling that the source actually offers.

| Word | Ceiling | Output |
|------|---------|--------|
| `low` | 240p | Video with sound, at or below 240p |
| `medium` | 480p | Video with sound, at or below 480p |
| `high` | 720p | Video with sound, at or below 720p |
| `veryhigh` | 1080p | Video with sound, at or below 1080p |
| `mp3` | not applicable | Audio only, in MP3 format |

### Functional Requirements

- **FR-001**: The bot MUST accept a single message in the form "a YouTube link, optionally followed by one resolution word", and MUST treat the resolution word case-insensitively.
- **FR-002**: The bot MUST support exactly the five resolution words `low`, `medium`, `high`, `veryhigh`, and `mp3`, and MUST reject any other word offered in that position with a reply listing the five accepted words.
- **FR-003**: The bot MUST treat each of `low`, `medium`, `high`, and `veryhigh` as a quality ceiling of 240p, 480p, 720p, and 1080p respectively, delivering the highest available quality at or below that ceiling.
- **FR-004**: When a source offers no quality at or below the requested ceiling, the bot MUST deliver the lowest available quality and state the quality actually produced, rather than failing or implying the ceiling was met.
- **FR-005**: The bot MUST support an audio-only request that produces a playable MP3 file, and MUST refuse an audio-only request against a source with no audio track with a specific reason.
- **FR-006**: The bot MUST apply the documented default tier when a message contains a link and no resolution word, and MUST say which tier it used.
- **FR-007**: The bot MUST acknowledge every accepted request within 5 seconds with a reply that states the understood tier, without waiting for the download to finish.
- **FR-008**: When a request completes, the bot MUST post a download link in the originating chat, MUST state the link's expiry time, and MUST tell the operator how to obtain a fresh link. Download links MUST live 60 minutes or less.
- **FR-009**: The bot MUST never send media bytes through the chat. It delivers links only, so file size is limited by the service's own limits, not by the chat platform's file-transfer limits.
- **FR-010**: The `/help` reply MUST contain the help text fixed in the Command Surface section, complete and unaltered in wording, order, and example.
- **FR-011**: The bot MUST answer `/start` with a greeting that points to `/help`.
- **FR-012**: The `/list` reply MUST show, for each of the operator's stored items, a name unique within that operator's library, the source video, the quality actually produced, the size, the age, and the days remaining before automatic deletion, and MUST split across messages, without loss or duplication, when one message cannot hold the list.
- **FR-013**: `/delete` MUST delete only the item whose name is given, MUST confirm the deletion by name, and MUST delete nothing when the name is unknown or ambiguous, listing the candidates in the ambiguous case.
- **FR-014**: `/empty` MUST require an explicit confirmation before deleting, MUST then delete every item belonging to the operator, MUST report how many were removed, and MUST report plainly when there was nothing to remove.
- **FR-015**: Deleting an item MUST remove its stored file, and a later request for the same video and tier MUST download it again rather than offer a dead link.
- **FR-016**: `/history` MUST list the operator's most recent interactions with a timestamp, what was requested, and the outcome, and MUST contain no access tokens and no download links.
- **FR-017**: `/info` MUST report the extraction tool version actually running in the deployment, together with the quality ceiling, the retention window, and the link lifetime.
- **FR-018**: `/test` MUST run exactly one short test download through the real pipeline, MUST report pass, or a specific failure reason, and MUST consume the same rate budget as a normal request.
- **FR-019**: `/test` and every other command MUST NOT loop, schedule further runs, or generate load to approach a cost ceiling. A ceiling is a guardrail, never a test target (constitution principle V).
- **FR-020**: The bot MUST serve only accounts on an approved list, which is configuration rather than code, and MUST refuse every other account with a reply that reveals neither the command list nor the download capability.
- **FR-021**: The bot MUST refuse requests from group chats with a reply stating that it only works in a private chat.
- **FR-022**: The bot MUST scope every listing, deletion, and history reply to its own requesting account, and MUST treat another account's item name as unknown rather than disclose it.
- **FR-023**: The bot MUST be idempotent per incoming message: a message delivered more than once, or a double-send of the same request, MUST produce at most one item, one file, and one link.
- **FR-024**: A request for the same video and tier that already has a stored item MUST be answered with a fresh working link in seconds, without downloading the video again and without counting as a new submission.
- **FR-025**: A request for the same video and tier while an earlier one is still processing MUST be answered as already in progress, with no second job created.
- **FR-026**: The bot MUST carry over the service's existing submission rate limit per approved account, and MUST answer an exhausted limit with a distinct message naming when capacity returns, without creating a job.
- **FR-027**: The bot MUST translate every failure into plain language, distinguish a bad link from a source refusal, a missing audio track, a limit reached, an exhausted rate limit, and an internal fault, and MUST state whether sending the same message again could succeed.
- **FR-028**: The bot MUST reject any input that is not a supported single video before media is transferred, including foreign hosts, malformed identifiers, collections, channels, and live broadcasts, and MUST refuse an ambiguous message (two links, or an unrecognised word where a resolution belongs) without starting work.
- **FR-029**: The bot's access token MUST be supplied as a secret from managed configuration, never committed, never echoed in chat, and never written to logs.
- **FR-030**: No access tokens, no signed download links, and no full source links with query parameters MUST appear in stored history, in logs, or in the service's audit records.
- **FR-031**: The bot MUST be provisioned and removed by the same single version-controlled process as the rest of the service, and removing the environment MUST leave no bot registration, message subscription, or secret behind.
- **FR-032**: The approved-account list, the default tier, the quality ceiling, the retention window, the link lifetime, and the rate limit MUST be configuration values that can change per environment without code changes.
- **FR-033**: The bot MUST reject any message it does not understand with a short reply restating the expected request format, and MUST NOT stay silent.
- **FR-034**: The documentation MUST be updated with the bot's commands, the resolution table, the approved-list procedure, and the recovery step for a source refusal, so an operator can use the bot with no prior knowledge of the service.
- **FR-035**: The existing terminal and API path MUST keep working unchanged; the bot is an additional front door, not a replacement.

### Key Entities *(include if data involved)*

- **Approved Account**: one Telegram account permitted to use the bot. Holds the account identifier, a label, and whether it is currently approved. Managed as configuration.
- **Incoming Message**: one message the bot receives. Holds the platform's message identity, the sending account, the chat type (private or group), the text, and receipt time. The platform's message identity is the basis for treating redeliveries as the same message.
- **Download Request**: one interpreted request from a message. Holds the source video reference, the requested tier, the resolved quality ceiling or audio-only indicator, the state, and the originating chat so the result can be posted back.
- **Stored Item**: one available file owned by one approved account. Holds the name shown in the list, the source video, the tier and the quality actually produced, the size, the creation time, the automatic deletion time, and its owning account.
- **Interaction Record**: one line of the operator's history. Holds the time, the command or request, the outcome, and a reference to the request, with no tokens and no links.
- **Bot Configuration**: the values that shape behaviour per environment: approved list, default tier, tier ceilings, retention window, link lifetime, rate limit, and the access secret reference.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of well-formed requests are acknowledged in under 5 seconds, for videos of any length within the configured limit.
- **SC-002**: At least 95% of requests for videos up to 30 minutes become downloadable within 10 minutes of the message being sent.
- **SC-003**: 100% of messages from accounts that are not approved are refused, and 0 downloads, files, or charges result, verified from three unapproved accounts attempting a valid request.
- **SC-004**: A first-time operator with the bot secret and the approved list completes first use, from `/start` to a working download link, in under 2 minutes using only the chat.
- **SC-005**: 100% of the commands in the help text behave as the help text describes, so no undocumented command is needed to complete any flow.
- **SC-006**: 100% of requested tiers produce a file at or below the tier ceiling, or state the quality actually produced when the ceiling cannot be met; no reply ever claims a quality the file does not have.
- **SC-007**: 0 duplicate files and 0 duplicate items across 100 deliberately repeated or redelivered messages.
- **SC-008**: Resending a request for a stored item returns a working link in under 5 seconds, with 0 reprocessing of the video.
- **SC-009**: `/delete` and `/empty` leave the library exactly as reported, verified by comparing a listing before and after each deletion.
- **SC-010**: 100% of failures reach the operator as a plain-language reason and, where useful, a next action; none appears as silence, a stack trace, or a bare platform error.
- **SC-011**: 0 access tokens, 0 signed links, and 0 full source links with query parameters appear in chat replies, history, logs, or audit records, verified by a scan of a representative retention period.
- **SC-012**: Removing an environment leaves 0 bot registrations, message subscriptions, secrets, or chargeable resources attributable to it.
- **SC-013**: The documented monthly usage profile stays within the documented cost ceiling, and the ceiling is verified by reading billing data only, never by generating load to reach it.

## Assumptions

- **The bot is a new front door on the existing service, not a rewrite.** It reuses the existing validation, downloading, storage, retention, and link-issuing behaviour. Where this specification names a quality tier, the bot translates it into a request the existing pipeline accepts.
- **Audio-only output expands the earlier scope.** The foundational specification declared audio-only extraction out of scope. The `mp3` tier deliberately reverses that for this feature, and this is the one place where the new feature contradicts an earlier assumption. It is recorded here so the earlier document is amended rather than quietly contradicted.
- **The `low` tier needs a 240p request value that does not exist yet.** The current job interface accepts 360p as its lowest named quality and can produce 240p only as a fallback. Expressing "at or below 240p" therefore requires that interface to gain a 240p value or an equivalent ceiling. This is a planning-phase dependency, not a behavioural ambiguity: the bot's meaning is fixed by the table above.
- **`filename.zip` in the help text is a placeholder for a file's real name.** The service produces playable MP4 and MP3 files, not archives, because a direct file plays on a phone while an archive would have to be unpacked first. The token therefore refers to the name shown by `/list`, and `/delete` accepts that name. If archived bundles are actually wanted, that is a change to be raised explicitly, not assumed here.
- **Private chats only, one operator plus a small approved list.** The bot is a personal tool. Group chats are refused, and the approved list is the constitutional guard against becoming a public ripping service. There is no sign-up, no invitation flow, and no self-service approval.
- **Approval changes are an operator action through configuration.** Applying an updated list takes effect on the next message, with no code change and no redeploy of logic.
- **English only.** Every bot reply is in English, including the fixed help text.
- **The bot posts results into the chat that made the request.** For a private chat this is the same conversation. The bot does not maintain a second delivery channel such as email.
- **Chat messages are not a storage system.** A posted link is a convenience; the authoritative view of what is available is `/list`. Once a link expires, the file is still reachable by asking again.
- **Default values**: the tier ceilings are the five in the table above, the default tier for a bare link is `high` (at or below 720p), storage lasts 7 days, links last 60 minutes, and the submission rate limit is the service's existing per-account limit of 10 per hour. All are configuration values.
- **The help text is fixed by the operator.** Its wording, emoji, ordering, and example are reproduced exactly, and any behaviour change that would contradict it is either not made or the help text is amended with the operator's agreement.
- **Documentation is part of done.** The command reference, the resolution table, the approved-list procedure, and the source-refusal recovery step ship with the feature.
