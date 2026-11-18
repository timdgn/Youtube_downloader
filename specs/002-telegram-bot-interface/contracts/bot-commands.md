# Contract: Bot Commands

**Feature**: `002-telegram-bot-interface`
**Producer**: the bot handler, in reply to one webhook update (Lambda or host target)
**Consumer**: the approved operator, in a private chat

Every reply is plain text. Every reply is at most 3,500 characters per page, split on
line boundaries. Nothing below is a template the implementation may paraphrase: the
help text is fixed by the operator, and the other strings are fixed so a test can assert
them.

---

## The help text, verbatim

`/help` returns exactly this, with no prefix, no suffix, and no trailing blank line:

```text
📚 Available commands:

/start - Start the bot
/list - List all your videos on the server
/link number - Send a fresh download link
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

`filename.zip` is reproduced as written. It is a placeholder for the real file name
shown by `/list`, which ends in `.mp4` or `.mp3`. This is recorded as a spec assumption
and is not a silent reinterpretation: if archived bundles are ever wanted, the help text
and the storage model change together.

---

## Commands

| Command | Reply | Side effect |
|---------|-------|-------------|
| `/start` | `Hello. Send a YouTube link with a resolution word and I will fetch it.\n\nSend /help for the full command list.` followed by the fixed help text, in one message | None |
| `/list` | One numbered line per stored item, or `No videos are stored.` | None |
| `/link <n>` | `Link for <n>: <name>`, then `Quality: <quality>`, then `Download (expires <HH:MM> UTC):` and the URL, then `The file stays available until <date>.` | None; a fresh link, no new job and no object written |
| `/delete <n>` or `/delete <name>` | `Deleted <n>: <name>.` / `Deleted <name>.` / `No item is numbered <n>.` / `No item is named <name>.` | Object deleted, item marked deleted |
| `/empty` | `This will delete N items. Reply "/empty confirm" to proceed.` | None until confirmed |
| `/empty confirm` | `Deleted N items.` / `Deleted N items; M remain, reply "/empty confirm" again.` / `There was nothing to delete.` | Every live item up to the per-confirmation cap |
| `/history` | Up to ten lines, or `No history yet.` | None |
| `/info` | The version and limits block below | None |
| `/test` | `Pipeline test started.` then the normal completion message or a failure | One job for the configured test video at `low` |
| `/help` | The fixed text above | None |
| anything else | `Send a YouTube link followed by a resolution word, or /help.` | None |

Unknown commands beginning with `/` get the same last line, not the help text.

---

## Menu registration

The list a client shows when `/` is typed is Telegram-side state, registered with
`setMyCommands` from the list in `infra/locals.tf` by `terraform_data.telegram_commands`.
A command name carries no arguments, so the usage lives in its description.

| Command | Description |
|---------|-------------|
| `start` | Start the bot |
| `list` | List your stored videos |
| `link` | Fresh download link for a numbered item |
| `delete` | Delete a video by number or name |
| `empty` | Delete all your videos |
| `history` | Your last ten interactions |
| `info` | yt-dlp version and limits |
| `test` | Run one short test download |
| `help` | Show the command list |

Adding or renaming a command means editing this table, `infra/locals.tf`, `messages.HELP`
and the help contract test together. The menu and the help text are two surfaces of one
list, and nothing checks them against each other automatically.

---

## Request grammar

```text
request  := URL [resolution]
URL      := an http or https YouTube link on an accepted host
resolution := low | medium | high | veryhigh | mp3
```

Parsing rules:

| Input | Result |
|-------|--------|
| `https://youtu.be/abc123DEF45 medium` | Accepted, tier `medium` |
| `https://youtu.be/abc123DEF45 MEDIUM` | Accepted, same tier |
| `https://youtu.be/abc123DEF45` | Accepted at the configured default tier, and the acknowledgement says which |
| `https://youtu.be/abc123DEF45.` | Accepted, trailing sentence punctuation stripped from the link |
| `https://youtu.be/abc123DEF45 medium.` | Accepted, trailing punctuation stripped from the word |
| Two links in one message | `Send one link at a time, followed by one resolution word.` |
| One link and an unknown word (`hq`) | `Unknown resolution "hq". Use low, medium, high, veryhigh or mp3.` |
| A resolution word with no link | `Send a YouTube link followed by a resolution word, or /help.` |
| A non-YouTube URL | The service's invalid-link sentence, and no job |
| A playlist, channel, or live link | The service's invalid-link sentence, and no job |

The link is validated by the same `parse_video_url` the terminal API uses. The bot adds
no second URL parser.

---

## Resolution table

| Word | Ceiling | What is delivered |
|------|---------|-------------------|
| `low` | 240p | The highest rendition at or below 240p |
| `medium` | 480p | The highest rendition at or below 480p |
| `high` | 720p | The highest rendition at or below 720p |
| `veryhigh` | 1080p | The highest rendition at or below 1080p |
| `mp3` | none | The best audio stream, converted to MP3 at 192 kbps |

If no rendition exists at or below the ceiling, the lowest rendition at or below the
service maximum is delivered, and the reply states the quality actually produced. The
reply never claims a quality the file does not have.

---

## Reply templates

**Accepted** (sent before any download starts):

```text
Accepted: <video_id> at <tier> (<ceiling>p). Working on it.
```

For audio:

```text
Accepted: <video_id> as MP3 audio. Working on it.
```

**Completed** (sent when the job event arrives):

```text
Ready: <title>
Quality: <produced_quality>
Size: <size>

Download (expires <YYYY-MM-DD HH:MM> UTC):
<url>

The file stays available until <YYYY-MM-DD>. Send the same request again for a fresh link.
```

**Reused** (the same video and tier is already stored, no new download):

```text
Already stored: <title>
Quality: <produced_quality>

Download (expires <YYYY-MM-DD HH:MM> UTC):
<url>

The file stays available until <YYYY-MM-DD>.
```

**Deleted** (by number, where the name is repeated so the chat stays unambiguous):

```text
Deleted <n>: <name>.
```

**Unknown number**:

```text
No item is numbered <n>.
```

**In progress**:

```text
Already working on <video_id> at <tier>. I will send the link when it is ready.
```

**Failed**:

```text
<failure sentence> <next step if the failure is retryable>
```

The failure sentence is the service's existing category message, unchanged. The next
step is `Send the request again.` when the service marked the failure retryable, and
absent otherwise.

**Rate limited**:

```text
Hourly limit reached. Capacity returns at <YYYY-MM-DD HH:MM> UTC.
```

**Not approved**:

```text
This bot is private.
```

No command list, no hint that downloads are possible, no explanation of how to be
approved.

**Group chat**:

```text
This bot only works in a private chat.
```

---

## `/list` format

One line per item, in descending creation order. Each line starts with the item's number:

```text
<n>. <title> | <produced_quality> | <size>
```

Example:

```text
3. Some video title | 720p | 182.4 MB
```

Items are separated by one blank line, so a long list does not read as a wall of text.

Numbers descend because the list is newest first. They are allocated once per account and
never reused, so a number read here still names the same file after another download or
delete. An item written before numbers existed prints without one and is deleted by name.

The stored name (`<video_id>-<tier>-<job_id last 4>.<ext>`, the label `/link` prints and
`/delete` accepts) is deliberately absent: the number identifies the item, and the name is
a download filename rather than an identifier worth reading in a listing.

Fields are separated by a pipe between spaces, the same as the rest of the bot's text.

| Field | Derivation |
|-------|------------|
| Number | The job's stored `seq`, allocated per account before the job is written |
| Title | Stored on the job by the worker, control characters stripped, 80 characters at most |
| Quality | `produced_quality`, the label recorded for the stored file |
| Size | `media_size_bytes`, one decimal place |

Age and days remaining are deliberately not shown. The item number, the title, the quality
and the size are what an operator reads before choosing; retention is bounded and reported
by `/link`, which states the last day the file is available.

Items past their retention time, and items deleted through the bot, are not listed. The
object's existence is verified when a link is issued, not during a listing, so `/list`
costs one index query rather than one S3 call per item.

If more than 200 items are live, the reply ends with:

```text
... and N more. Delete some items to see the rest.
```

---

## `/link` format

`/link <n>` signs a fresh URL for the live item numbered `<n>`, without creating a job,
writing an object or consuming the hourly request allowance:

```text
Link for <n>: <name>
Quality: <quality>

Download (expires <HH:MM> UTC):
<url>

The file stays available until <YYYY-MM-DD>.
```

`<name>`, `<quality>` and `<YYYY-MM-DD>` are the same values `/list` shows for that item.
The URL is a new signature with the normal link lifetime; it does not extend retention.

| Input | Reply |
|-------|-------|
| `/link 3`, live item numbered 3 | The block above |
| `/link` | `Use /link <number>, the number /list shows.` |
| `/link abc` | Same usage line |
| `/link 99`, no live item numbered 99 | `No item is numbered 99.` |
| `/link 3`, item live but object already gone | `The stored file has expired; submit the video link again.` |

The interaction is recorded as `command:link`, with the number as its text.

---

## `/history` format

Ten lines at most, newest first:

```text
<YYYY-MM-DD HH:MM> UTC | <text> | <outcome>
```

`<text>` is the sanitised request: `<video_id> <tier>`, the command name and its
argument, or `refused:<reason>`. `<outcome>` is `accepted`, `reissued`, `in_progress`,
`refused`, `rate_limited`, `command`, or the job's current state when the interaction
has a job that has since completed or failed.

History contains no tokens, no download links, and no URL with a query string.

---

## `/info` block

```text
yt-dlp <version>
Quality ceiling: <max_height>p
Retention: <retention_days> days
Link lifetime: <link_ttl_seconds / 60> minutes
Rate limit: <rate_limit_per_hour> requests per hour
```

The version is the one baked into the image, not a value typed into a document.

---

## `/empty` confirmation

`/empty` never deletes on its own. The confirmation is the literal argument `confirm`:

| Input | Behaviour |
|-------|-----------|
| `/empty` | Reports the live count and asks for `/empty confirm` |
| `/empty confirm` | Deletes up to 200 items, reports exactly what happened |
| `/empty yes`, `/empty` with any other argument | Treated as `/empty`, so a typo cannot delete anything |

The cap exists because an HTTP API integration has a 30-second ceiling. The reply states
the remainder rather than claiming a completeness it did not achieve.
