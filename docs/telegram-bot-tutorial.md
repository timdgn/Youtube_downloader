# Using the Telegram bot

This is the operator's guide to the chat front door: creating the bot, letting
yourself in, asking for a video, and reading what comes back. It assumes the stack
is deployed (see `README.md`) and that you have this repository checked out.

Everything here happens either in Telegram or in a shell in the repository root.
No AWS console step is involved.

---

## 1. Create the bot, once

Open Telegram and talk to **@BotFather**:

1. Send `/newbot`.
2. Choose a display name (anything, for example `My downloads`).
3. Choose a username that ends in `bot` (for example `tim_downloads_bot`).
4. BotFather answers with a token shaped like `123456789:AA...`.

Copy the token. BotFather will show it again later with `/mybots`, but treat it as
a password: anyone holding it controls the bot.

This is the only step in the deployment that no tool can do for you. Telegram has
no API that creates a bot account, the same way AWS has no Terraform resource for
an AWS account.

---

## 2. Hand the token to Terraform

The token is a Terraform variable because the apply writes it to an SSM
`SecureString`. Export it in the shell that runs the deploy, and never commit it:

```bash
export TF_VAR_telegram_bot_token='123456789:AA...'
```

If you prefer to keep it out of your shell history, store it in a file outside the
repository and read it in:

```bash
mkdir -p ~/.config/ytdl
printf '%s' '123456789:AA...' > ~/.config/ytdl/telegram-bot-token
chmod 600 ~/.config/ytdl/telegram-bot-token
export TF_VAR_telegram_bot_token="$(cat ~/.config/ytdl/telegram-bot-token)"
```

The token is never written to a file in this repository, never becomes an
environment variable, and never appears in a log line. The bot reads it from SSM
on every message.

---

## 3. Find your numeric account id

The allowlist holds numbers, not usernames. `@userinfobot` reports yours: open it,
send any message, and it answers with your `Id`.

Alternatively, before the webhook exists, Telegram queues your messages and one
read-only call shows them:

```bash
curl -s "https://api.telegram.org/bot$TF_VAR_telegram_bot_token/getUpdates" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"][-1]["message"]["from"]["id"])'
```

That call stops working once the webhook is registered, which is why the first
route is the one to remember.

---

## 4. Approve yourself

The approved list is configuration, read from SSM on every message. Export it as
an HCL list and apply:

```bash
export TF_VAR_telegram_approved_user_ids='["<your numeric id>"]'
make deploy ENV=prod
```

An empty list approves nobody, which is the safe default. A change to the list
governs the next message: no image is rebuilt and no function is redeployed.

If the apply fails at the webhook step, the script printed Telegram's own reason.
A `401` means the token is wrong; a `400` usually means the URL is malformed.

---

## 5. Confirm the registration

```bash
make bot-health ENV=prod
```

Expected, and worth reading before you write anything in the chat:

```text
url                   https://ytdl.timcloud.fr/telegram/webhook
pending_update_count  0
max_connections       5
allowed_updates       ['message']
```

On a Lambda deployment the same line shows the API Gateway stage instead. Either way, what
matters is that the URL equals `terraform -chdir=infra output -raw bot_webhook_url`.

A non-empty `last_error_message` means Telegram cannot deliver to that address.

---

## 6. First contact

Open your bot in Telegram and press **Start**, or send `/start`.

Expected: the greeting, then the full command list, in one message. `/help` sends the
same list when you need it again.

Typing `/` in the chat also shows a menu of the commands, with a one-line description
each. That menu is registered on Telegram's side by `terraform_data.telegram_commands`
from `infra/locals.tf`; it is not the same thing as `/help`, and a command name there
cannot carry arguments.

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

`filename.zip` is a placeholder for the real file name that `/list` prints. Files
are playable `.mp4` and `.mp3`, not archives.

---

## 7. Ask for a video

One message: a YouTube link, optionally followed by one resolution word.

```text
https://www.youtube.com/watch?v=dQw4w9WgXcQ medium
```

| Word | Ceiling | You get |
|------|---------|---------|
| `low` | 240p | the best rendition at or below 240p |
| `medium` | 480p | the best rendition at or below 480p |
| `high` | 720p | the best rendition at or below 720p |
| `veryhigh` | 1080p | the best rendition at or below 1080p |
| `mp3` | none | the best audio stream, encoded to MP3 at 192 kbps |

A word is a ceiling, not a demand. A link with no word uses the configured default
tier (`high` unless changed) and the acknowledgement says which tier it used.

What happens next, in order:

1. **Accepted** within seconds, before any download starts:
   ```text
   Accepted: dQw4w9WgXcQ at medium (480p). Working on it.
   ```
   For audio:
   ```text
   Accepted: dQw4w9WgXcQ as MP3 audio. Working on it.
   ```
2. **Ready** when the worker finishes, usually within a minute or two:
   ```text
   Ready: Some video title
   Quality: 480p
   Size: 182.4 MB

   Download (expires 2026-11-16 11:30 UTC):
   https://...

   The file stays available until 2026-11-23. Send the same request again for a fresh link.
   ```
3. **The link works with no credential.** It is a presigned S3 URL and lives 60
   minutes. The file itself lives `RETENTION_DAYS`, 7 by default.

If the source has nothing at or below your ceiling, you get the lowest rendition at
or below the service maximum, and `Quality:` states what was actually produced. The
reply never claims a quality the file does not have.

**Sending the same request again is cheap.** While the file is still stored, the
bot answers `Already stored:` with a fresh link in seconds and downloads nothing a
second time. Use that whenever a link expired.

**While a job is still running**, another request for the same video and tier
answers `Already working on ...` and creates nothing new.

---

## 8. Everyday commands

| Command | What it does |
|---------|--------------|
| `/list` | One numbered line per stored item, newest first |
| `/link <n>` | Sends a fresh download link for the item carrying that number |
| `/delete <n>` | Removes the item carrying that number |
| `/delete <name>` | Removes the item with that exact name |
| `/empty` | Reports how many items it would delete and asks you to confirm |
| `/empty confirm` | Deletes them and states the count |
| `/history` | Your last ten interactions with their outcome |
| `/info` | The yt-dlp version actually running, plus the four limits |
| `/test` | One real download at `low` against the configured test video |

A `/list` line looks like this:

```text
3. Some video title 720p 182.4 MB
```

The leading number is allocated once, when the item is created, and never reused. It is
stable: after another download or another delete, `3` still means the same file. Numbers
descend because the list is newest first. The list does not repeat the stored file name,
which is a download label rather than something to read: the number is the handle.

```text
/delete 3
```

You receive `Deleted 3: dQw4w9WgXcQ-high-k2q1.mp4.` and the object is gone from S3. The
job record survives with a `deleted_at` timestamp, so history keeps meaning. Send the
original request again and the video is downloaded afresh rather than a dead link being
offered. `/delete` still accepts the stored name shown by `/link` if you prefer it, but
`/list` no longer prints it.

A finished link expires after 60 minutes; the file itself stays for 7 days. `/link 3`
signs a new URL for the same stored object, so nothing is downloaded again. It answers
`No item is numbered 3.` when nothing live carries that number, and says the file has
expired when the record is still listed but the object is gone.

A number that matches nothing answers `No item is numbered 9.` and deletes nothing. A
number can be skipped: a request refused by the hourly limit burns its number without
creating an item, which is why the list can read `5, 4, 2, 1`.

`/empty` never deletes on its own. Only the literal word `confirm` does. A typo
such as `/empty yes` is treated as a bare `/empty`, so it cannot delete anything.
Each confirmation removes up to 200 items and says how many remain, because an
HTTP API integration has a 30-second ceiling.

`/test` runs exactly one job through the real pipeline, at `low`, against the
video id in `telegram_test_video_id`. It consumes one unit of the hourly budget,
like any other request. Nothing loops or schedules it again.

---

## 9. Reading a refusal

| Reply | Meaning | What to do |
|-------|---------|------------|
| `This bot is private.` | Your account is not on the allowlist | Add your numeric id to `TF_VAR_telegram_approved_user_ids` and apply |
| `This bot only works in a private chat.` | The bot was used in a group | Talk to it in a private chat |
| `Send one link at a time, followed by one resolution word.` | Two links in one message | One link per message |
| `Unknown resolution "hq". Use low, medium, high, veryhigh or mp3.` | A word outside the vocabulary | Use one of the five |
| `Send a YouTube link followed by a resolution word, or /help.` | No link in the message | Send a link |
| `No item is numbered 9.` | Nothing live carries that number | Run `/list` and use a number it prints |
| `The submitted link is not a supported single YouTube video.` | Foreign host, playlist, channel, or live link | Send a single video link |
| `Hourly limit reached. Capacity returns at 2026-11-16 11:00 UTC.` | Ten requests in this hour | Wait for the stated time |
| `The source refused this video. It may be private, removed, or unavailable in this region.` | `unsupported_media`, not retryable | Do not resend; the content is out of scope |
| `The video is longer or larger than this service allows.` | Over the duration or size cap | Resending gives the same answer |
| `The source was temporarily unreachable; the same request may succeed later. Send the request again.` | Transient upstream problem | Wait a few minutes, then resend |
| `The job could not be completed because of an internal error; try again later. Send the request again.` | Storage, the soft deadline, or an unexpected exception | One resend is reasonable; a repeat is a defect |
| `The stored file has expired; submit the video link again.` | The job finished but the file was already gone | Send the request again |

A failure always arrives as a plain sentence, followed by `Send the request again.`
only when a retry can help. No stack trace and no platform error text reaches the
chat.

---

## 10. Keeping it yours

**Change who is allowed.** Edit the list and apply; nothing is rebuilt:

```bash
export TF_VAR_telegram_approved_user_ids='["111111111","222222222"]'
make deploy ENV=prod
```

A removed account is refused on its next message and sees none of its earlier
items, because every listing is scoped to the sender.

**Rotate the token.** Do this if the token leaked, or if BotFather revoked it
(every call then answers `401`, so the bot goes silent and the hourly check reports
`0`). Write the new value to SSM first, because the registration script
authenticates with whatever SSM holds:

```bash
export TF_VAR_telegram_bot_token='<new token>'
make outputs ENV=prod >/dev/null
terraform -chdir=infra apply -var-file=env/prod.tfvars -var image_tag="$(git rev-parse --short=12 HEAD)"
```

Then confirm with `make bot-health ENV=prod`. Exporting the new value in the same
shell matters: Terraform still holds the previous one and would write it back.

**Re-register the webhook.** The registration is owned by Terraform, so let
Terraform replace it rather than calling Telegram by hand. Every apply and every
destroy needs `TF_VAR_telegram_bot_token` in the environment:

```bash
export TF_VAR_telegram_bot_token="$(cat ~/.config/ytdl/telegram-bot-token)"
make outputs ENV=prod >/dev/null
terraform -chdir=infra apply -replace=terraform_data.telegram_webhook \
  -var-file=env/prod.tfvars -var image_tag="$(git rev-parse --short=12 HEAD)"
```

**Remove the bot completely.** `make destroy ENV=prod` deregisters the webhook
before it deletes the SSM parameters, so Telegram is left posting to nothing.
Prove it, reading the token while the parameter still exists:

```bash
TG_TOKEN=$(make -s bot-token ENV=prod)
make destroy ENV=prod
curl -s "https://api.telegram.org/bot$TG_TOKEN/getWebhookInfo" | python3 -m json.tool
```

Expected: `"url": ""`.

Removing only the webhook, while keeping the stack, is the two-step procedure in
`docs/runbook.md` section 11: `count = 0`, apply, then delete the block.

---

## 11. What the bot will not do

- **No media through the chat.** It posts links only, so file size is limited by
  this service, not by Telegram's transfer limits.
- **No group chats, no channels.** Private chats only.
- **No sign-up or invitation.** The allowlist is operator configuration.
- **No admin commands.** Changing the ceiling, the retention window, the link
  lifetime or the rate limit is a reviewed apply, not a chat message.
- **No load generation.** `/test` is one job per invocation and nothing repeats it.

The bot also refuses to reveal anything about itself to an unapproved account: the
refusal contains no command list and no hint that downloads are possible.

---

## 12. When something looks broken

| Symptom | First check |
|---------|-------------|
| The bot never answers | `make bot-health ENV=prod`. An empty `url` means the registration is gone; a recent `last_error_message` means Telegram cannot deliver |
| Every message is refused | Your id is not on the allowlist, or the list was written for another environment |
| The bot answered before and stopped | The token may be revoked: `make bot-health` reports `401` from Telegram |
| A download never completes | `youtube` refused the host's address, or the job hit a cap. `/info` shows the limits; the runbook has the failure table |

The operational detail, including the two alarms and their named responses, is in
`docs/runbook.md` section 11.
