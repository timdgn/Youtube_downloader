"""Telegram front door: webhook, job events, and the hourly health check (T036).

Three entry shapes share one object:

- a webhook delivery from API Gateway, authenticated by the secret header;
- an SNS notification that a job reached a terminal state;
- the hourly ``{"action": "health"}`` invocation from EventBridge.

Every collaborator is injected, so the contract tests drive all three without a
network, a clock or an AWS client.
"""

from __future__ import annotations

import base64
import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

import boto3

from ytdl import errors, keys, quality, states, submissions
from ytdl.adapters.dynamo import DuplicateUpdate, DynamoRepository, RateLimited
from ytdl.adapters.s3 import MediaStore, s3_client
from ytdl.adapters.sqs import JobQueue
from ytdl.bot import library, messages
from ytdl.bot.access import AllowlistPolicy
from ytdl.bot.client import TelegramClient
from ytdl.bot.commands import TIERS, Intent, Tier, parse_request
from ytdl.config import Config, ConfigError, load_config, require_bot_config
from ytdl.logging import StructuredLogger, get_logger
from ytdl.urls import VideoRef

SECRET_HEADER = "x-telegram-bot-api-secret-token"
EVENT_VERSION = 1
HEALTH_NAMESPACE = "YTDL/Bot"
HEALTH_METRIC = "WebhookHealthy"
HEALTH_WINDOW_SECONDS = 3600
REUSE_LIMIT = 200

_CONTENT_TYPES = {"audio": "audio/mpeg", "video": "video/mp4"}


class Policy(Protocol):
    """The access boundary: one credential read, then three pure questions."""

    token: str

    def read(self) -> None: ...

    def check_secret(self, presented: str | None) -> bool: ...

    def is_approved(self, user_id: int | str) -> bool: ...

    def is_private(self, chat_type: str | None) -> bool: ...


class _BadRequest(Exception):
    """A body that cannot be read as one Telegram update."""


class Bot:
    """One delivery in, one HTTP-ish response out."""

    def __init__(
        self,
        repo: Any,
        queue: Any,
        store: Any,
        telegram: Callable[[str], Any],
        policy: Policy,
        config: Config,
        now: Callable[[], datetime] | None = None,
        logger: StructuredLogger | None = None,
    ) -> None:
        self._repo = repo
        self._queue = queue
        self._store = store
        # A factory, because the token is read per message rather than cached.
        self._telegram = telegram
        self._policy = policy
        self._config = config
        self._now = now if now is not None else _utc_now
        self._logger = logger if logger is not None else get_logger("bot")

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        """Dispatch one of the three entry shapes and never raise for a 4xx."""
        if not isinstance(event, dict):
            return _empty(400)
        if event.get("action") == "health":
            return self._health()
        records = event.get("Records")
        if isinstance(records, list) and records:
            return self._events(records)
        return self._webhook(event)

    # --- webhook ------------------------------------------------------------

    def _webhook(self, event: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        outcome = "unauthenticated"
        update_id: Any = None
        try:
            self._policy.read()
            if not self._policy.check_secret(_header(event, SECRET_HEADER)):
                return _empty(403)
            if _action(event) == "health":
                # The same probe works when posted at the route and when the
                # hourly rule invokes the function directly.
                self._health()
                outcome = "health"
                return _empty(200)
            update = _update(event)
            update_id = update["update_id"]
            outcome = self._handle_update(update)
        except _BadRequest:
            outcome = "malformed"
            return _empty(400)
        except DuplicateUpdate:
            outcome = "duplicate"
        except Exception as error:
            # Only the class name: a message could carry a token or a URL.
            self._logger.error("update_failed", message=type(error).__name__)
            outcome = "failed"
            return _empty(500)
        finally:
            self._log(started, outcome, update_id)
        return _empty(200)

    def _handle_update(self, update: dict[str, Any]) -> str:
        update_id = update["update_id"]
        message = update.get("message")
        if not isinstance(message, dict):
            return "ignored"

        sender = (message.get("from") or {}).get("id")
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if sender is None or chat_id is None:
            return "ignored"

        if not self._policy.is_approved(sender):
            # No command list, no hint that downloads exist, no side effect.
            self._reply(chat_id, messages.NOT_APPROVED)
            return "refused"

        if not self._policy.is_private(chat.get("type")):
            self._reply(chat_id, messages.GROUP_CHAT)
            return "refused"

        return self._dispatch(update_id, sender, chat_id, message.get("text"))

    def _dispatch(self, update_id: Any, sender: Any, chat_id: Any, text: Any) -> str:
        if isinstance(text, str) and text.startswith("/"):
            if self._command(update_id, sender, chat_id, text):
                return "command"
            self._reply(chat_id, messages.FORMAT_HINT)
            return "format_hint"

        intent = parse_request(text, self._config.telegram_default_tier)
        if intent.kind == "two_links":
            return self._refuse(update_id, sender, chat_id, messages.TWO_LINKS, "two_links")
        if intent.kind == "unknown_resolution":
            return self._refuse(
                update_id,
                sender,
                chat_id,
                messages.UNKNOWN_RESOLUTION.format(word=intent.word),
                "unknown_resolution",
            )
        if intent.kind == "invalid_link":
            return self._refuse(update_id, sender, chat_id, messages.INVALID_LINK, "invalid_link")
        if intent.kind != "request" or intent.reference is None or intent.tier is None:
            self._reply(chat_id, messages.FORMAT_HINT)
            return "format_hint"

        return self._request(update_id, sender, chat_id, intent)

    def _refuse(self, update_id: Any, sender: Any, chat_id: Any, text: str, reason: str) -> str:
        self._reply(chat_id, text)
        self._interaction(update_id, sender, f"refused:{reason}", "refused")
        return "refused"

    # --- one request ---------------------------------------------------------

    def _request(self, update_id: Any, sender: Any, chat_id: Any, intent: Intent) -> str:
        reference = intent.reference
        tier = intent.tier
        if reference is None or tier is None:
            return "format_hint"

        now = self._now()
        owner_key = keys.owner_job_key(sender)
        owned = self._repo.query_owner_jobs(owner_key, REUSE_LIMIT)
        matching = [job for job in owned if _matches(job, reference.video_id, tier)]

        if any(str(job.get("update_id")) == str(update_id) for job in matching):
            # The index already shows this update's job, so this is a redelivery.
            # When it does not (eventual consistency), the transaction rejects it.
            return "duplicate"

        for job in matching:
            if job.get("state") != states.COMPLETED:
                continue
            if self._reissue(update_id, sender, chat_id, job, now):
                return "reissued"

        for job in matching:
            if job.get("state") in (states.ACCEPTED, states.PROCESSING):
                self._reply(
                    chat_id,
                    messages.IN_PROGRESS.format(video_id=reference.video_id, tier=tier.word),
                )
                self._interaction(
                    update_id,
                    sender,
                    _request_text(reference.video_id, tier),
                    "in_progress",
                    job.get("job_id"),
                )
                return "in_progress"

        return self._create(update_id, sender, chat_id, intent, now)

    def _reissue(
        self,
        update_id: Any,
        sender: Any,
        chat_id: Any,
        job: dict[str, Any],
        now: datetime,
    ) -> bool:
        """Send a fresh link for a stored item, or report that it was not sent."""
        if job.get("deleted_at") is not None or not _inside_retention(job, now):
            return False
        head = self._store.head_object(_media(job))
        if head is None:
            return False

        moment = int(now.timestamp())
        url = self._presign(job, moment)
        self._reply(
            chat_id,
            messages.REUSED.format(
                title=_title(job),
                quality=_produced(job),
                expires=messages.format_minute(moment + self._config.link_ttl_seconds),
                url=url,
                available_until=messages.format_date(int(job["media_expires_at"])),
            ),
        )
        self._interaction(
            update_id,
            sender,
            _request_text(job.get("video_id"), _tier_for(job)),
            "reissued",
            job.get("job_id"),
        )
        return True

    def _create(
        self,
        update_id: Any,
        sender: Any,
        chat_id: Any,
        intent: Intent,
        now: datetime,
        is_test: bool = False,
    ) -> str:
        reference = intent.reference
        tier = intent.tier
        if reference is None or tier is None:
            return "format_hint"

        now_epoch = int(now.timestamp())
        created_at = _iso(now)
        requested_quality, requested_height = _requested(tier, self._config.max_height)
        owner = {
            "owner_key": keys.owner_job_key(sender),
            "chat_id": str(chat_id),
            "update_id": update_id,
            "requested_tier": tier.word,
            "is_test": True if is_test else None,
            # Reserved before the transaction, so it is stable for the item's life.
            "seq": self._repo.allocate_seq(keys.owner_seq_key(sender)),
        }
        job = submissions.build_job(
            reference,
            created_at,
            requested_quality,
            requested_height,
            owner=owner,
            media_kind=tier.media_kind,
        )
        text = _request_text(reference.video_id, tier)
        interaction = {
            "pk": keys.interaction_pk(update_id),
            "owner_key": keys.owner_interaction_key(sender),
            "created_at": created_at,
            "text": text,
            "outcome": "accepted",
            "job_id": job["job_id"],
        }
        message = {
            "version": 1,
            "job_id": job["job_id"],
            "video_id": reference.video_id,
            "source_host": reference.host,
            "requested_height": job.get("requested_height"),
            "enqueued_at": created_at,
            "media_kind": tier.media_kind,
        }
        acknowledgement = (
            messages.TEST_STARTED
            if is_test
            else _acknowledgement(reference.video_id, tier, requested_height)
        )

        try:
            submissions.submit(
                self._repo,
                self._queue,
                self._config,
                job,
                keys.rate_key(keys.rate_window(now_epoch), keys.owner_job_key(sender)),
                self._config.rate_limit_per_hour,
                now_epoch,
                interaction,
                message,
                before_enqueue=lambda: self._reply(chat_id, acknowledgement),
            )
        except RateLimited as limited:
            self._reply(
                chat_id,
                messages.RATE_LIMITED.format(
                    retry_at=messages.format_minute(limited.retry_at_epoch)
                ),
            )
            self._interaction(update_id, sender, text, "rate_limited")
            return "rate_limited"

        return "accepted"

    # --- job events ----------------------------------------------------------

    def _events(self, records: list[Any]) -> dict[str, Any]:
        for record in records:
            sns = record.get("Sns") if isinstance(record, dict) else None
            if isinstance(sns, dict):
                self._event(sns.get("Message"))
        return _empty(200)

    def _event(self, body: Any) -> None:
        try:
            payload = json.loads(body) if isinstance(body, str) else None
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            self._logger.warning("job_event_malformed")
            return
        if payload.get("version") != EVENT_VERSION:
            self._logger.warning("job_event_version", message="unsupported")
            return

        job_id = payload.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            self._logger.warning("job_event_malformed")
            return

        job = self._repo.get_job(job_id)
        if job is None:
            self._logger.warning("job_event_missing", job_id=job_id)
            return
        if not job.get("chat_id"):
            self._logger.info("job_event_unaddressed", job_id=job_id)
            return

        event = payload.get("event")
        if event == "job_completed":
            self._announce_completed(job)
        elif event == "job_failed":
            self._announce_failed(job)
        else:
            self._logger.warning("job_event_unknown", job_id=job_id)

    def _announce_completed(self, job: dict[str, Any]) -> None:
        moment = self._now()
        chat_id = job["chat_id"]
        if job.get("deleted_at") is not None or not _inside_retention(job, moment):
            self._reply(chat_id, messages.EXPIRED)
            return

        key = _media(job)
        head = self._store.head_object(key)
        if head is None:
            self._reply(chat_id, messages.EXPIRED)
            return

        now_epoch = int(moment.timestamp())
        self._reply(
            chat_id,
            messages.COMPLETED.format(
                title=_title(job),
                quality=_produced(job),
                size=messages.format_size(head["size"]),
                expires=messages.format_minute(now_epoch + self._config.link_ttl_seconds),
                url=self._presign(job, now_epoch),
                available_until=messages.format_date(int(job["media_expires_at"])),
            ),
        )

    def _announce_failed(self, job: dict[str, Any]) -> None:
        sentence = job.get("failure_message") or errors.MESSAGES[errors.INTERNAL_ERROR]
        next_step = messages.NEXT_STEP_RETRY if job.get("failure_retryable") is True else ""
        self._reply(
            job["chat_id"],
            messages.FAILED.format(message=sentence, next_step=next_step).strip(),
        )

    # --- health --------------------------------------------------------------

    def _health(self) -> dict[str, Any]:
        try:
            self._policy.read()
            info = self._telegram(self._policy.token).get_webhook_info()
        except Exception as error:
            self._logger.error("health_failed", message=type(error).__name__)
            info = {}

        healthy = _is_healthy(
            info,
            self._config.telegram_webhook_url,
            int(self._now().timestamp()),
        )
        _emit_health(healthy, self._config.environment)
        return _empty(200)

    # --- commands (US3 and US4 add the rest) ---------------------------------

    def _command(self, update_id: Any, sender: Any, chat_id: Any, text: str) -> bool:
        name, _, argument = text.partition(" ")
        command = name.strip().lower()
        rest = argument.strip()

        if command == "/list":
            self._list(update_id, sender, chat_id)
        elif command == "/link":
            self._link(update_id, sender, chat_id, rest)
        elif command == "/delete":
            self._delete(update_id, sender, chat_id, rest)
        elif command == "/empty":
            self._empty(update_id, sender, chat_id, rest)
        elif command == "/start":
            self._simple(update_id, sender, chat_id, "/start", messages.START_REPLY)
        elif command == "/help":
            self._simple(update_id, sender, chat_id, "/help", messages.HELP)
        elif command == "/info":
            self._simple(
                update_id,
                sender,
                chat_id,
                "/info",
                messages.info_text(self._config),
            )
        elif command == "/history":
            self._history(update_id, sender, chat_id)
        elif command == "/test":
            self._test(update_id, sender, chat_id)
        else:
            return False
        return True

    def _simple(self, update_id: Any, sender: Any, chat_id: Any, name: str, text: str) -> None:
        self._reply(chat_id, text)
        self._interaction(update_id, sender, name, f"command:{name.lstrip('/')}")

    def _history(self, update_id: Any, sender: Any, chat_id: Any) -> None:
        items = self._repo.query_interactions(keys.owner_interaction_key(sender), 10)
        job_ids = [str(item["job_id"]) for item in items if isinstance(item.get("job_id"), str)]
        states_by_job_id: dict[str, str] = {}
        if job_ids:
            for job in self._repo.get_jobs(job_ids):
                job_id = job.get("job_id")
                state = job.get("state")
                if isinstance(job_id, str) and isinstance(state, str):
                    states_by_job_id[job_id] = state
        self._reply(
            chat_id,
            library.render_history(items, states_by_job_id, self._epoch()),
        )
        self._interaction(update_id, sender, "/history", "command:history")

    def _test(self, update_id: Any, sender: Any, chat_id: Any) -> None:
        video_id = self._config.telegram_test_video_id
        if not video_id:
            # A missing target is a deployment fault, not something to guess at.
            raise ConfigError("TELEGRAM_TEST_VIDEO_ID", "missing test video id")

        intent = Intent(
            kind="request",
            reference=VideoRef(host="www.youtube.com", video_id=str(video_id)),
            tier=TIERS["low"],
        )
        self._create(update_id, sender, chat_id, intent, self._now(), is_test=True)

    def _list(self, update_id: Any, sender: Any, chat_id: Any) -> None:
        jobs = self._repo.query_owner_jobs(keys.owner_job_key(sender), library.OWNER_LIMIT)
        self._reply(chat_id, library.render_list(jobs, self._epoch()))
        self._interaction(update_id, sender, "/list", "command:list")

    def _link(self, update_id: Any, sender: Any, chat_id: Any, argument: str) -> None:
        if not argument.isdigit():
            self._reply(chat_id, messages.LINK_USAGE)
            self._interaction(update_id, sender, "/link", "command:link")
            return

        number = int(argument)
        now_epoch = self._epoch()
        job = library.link_numbered(
            self._repo,
            keys.owner_job_key(sender),
            number,
            now_epoch,
        )
        if job is None:
            text = messages.NO_SUCH_NUMBER.format(number=number)
        elif self._store.head_object(_media(job)) is None:
            text = messages.EXPIRED
        else:
            text = messages.FRESH_LINK.format(
                number=number,
                name=keys.display_name(job),
                quality=_produced(job),
                expires=messages.format_minute(now_epoch + self._config.link_ttl_seconds),
                url=self._presign(job, now_epoch),
                available_until=messages.format_date(int(job["media_expires_at"])),
            )

        self._reply(chat_id, text)
        self._interaction(update_id, sender, f"/link {number}", "command:link")

    def _delete(self, update_id: Any, sender: Any, chat_id: Any, name: str) -> None:
        if not name:
            # Nothing was named, so nothing can be deleted.
            self._reply(chat_id, messages.FORMAT_HINT)
            self._interaction(update_id, sender, "/delete", "command:delete")
            return

        owner_key = keys.owner_job_key(sender)
        now_epoch = self._epoch()
        if name.isdigit():
            job = library.delete_numbered(self._repo, self._store, owner_key, int(name), now_epoch)
            text = (
                messages.DELETED_NUMBER.format(number=int(name), name=keys.display_name(job))
                if job is not None
                else messages.NO_SUCH_NUMBER.format(number=int(name))
            )
        else:
            job = library.delete_item(self._repo, self._store, owner_key, name, now_epoch)
            text = (
                messages.DELETED.format(name=name)
                if job is not None
                else messages.NO_SUCH_ITEM.format(name=name)
            )

        self._reply(chat_id, text)
        self._interaction(update_id, sender, f"/delete {name}", "command:delete")

    def _empty(self, update_id: Any, sender: Any, chat_id: Any, argument: str) -> None:
        owner_key = keys.owner_job_key(sender)
        now_epoch = self._epoch()

        if argument.lower() != "confirm":
            # Any other argument is treated as a bare /empty, so a typo is safe.
            live = library.live_items(
                self._repo.query_owner_jobs(owner_key, library.OWNER_LIMIT),
                now_epoch,
            )
            self._reply(chat_id, messages.EMPTY_CONFIRM.format(count=len(live)))
            self._interaction(update_id, sender, "/empty", "command:empty")
            return

        deleted, remaining = library.empty_library(
            self._repo,
            self._store,
            owner_key,
            now_epoch,
        )
        if deleted == 0:
            text = messages.EMPTY_NOTHING
        elif remaining > 0:
            text = messages.EMPTY_REMAIN.format(count=deleted, remaining=remaining)
        else:
            text = messages.EMPTY_DONE.format(count=deleted)
        self._reply(chat_id, text)
        self._interaction(update_id, sender, "/empty confirm", "command:empty")

    def _epoch(self) -> int:
        return int(self._now().timestamp())

    # --- shared helpers ------------------------------------------------------

    def _reply(self, chat_id: Any, text: str) -> None:
        self._telegram(self._policy.token).send_message(chat_id, text)

    def _interaction(
        self,
        update_id: Any,
        sender: Any,
        text: str,
        outcome: str,
        job_id: Any = None,
    ) -> None:
        item: dict[str, Any] = {
            "pk": keys.interaction_pk(update_id),
            "owner_key": keys.owner_interaction_key(sender),
            "created_at": _iso(self._now()),
            "text": text,
            "outcome": outcome,
        }
        if job_id:
            item["job_id"] = job_id
        self._repo.put_interaction(item)

    def _presign(self, job: dict[str, Any], now_epoch: int) -> str:
        return self._store.presign_get(
            _media(job),
            keys.display_name(job),
            self._config.link_ttl_seconds,
            content_type=_content_type(job),
        )

    def _log(self, started: float, outcome: str, update_id: Any = None) -> None:
        fields: dict[str, Any] = {"outcome": outcome}
        fields["duration_ms"] = int((time.monotonic() - started) * 1000)
        if isinstance(update_id, int) and not isinstance(update_id, bool):
            fields["update_id"] = update_id
        if outcome == "failed":
            self._logger.error("update", **fields)
        else:
            self._logger.info("update", **fields)


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    """Lambda entry point; the dependencies are built once per cold start."""
    global _BOT
    if _BOT is None:
        _BOT = build_bot()
    return _BOT.handle(event)


_BOT: Bot | None = None


def build_bot() -> Bot:
    config = require_bot_config(load_config())
    return Bot(
        repo=DynamoRepository(boto3.client("dynamodb"), config.table_name),
        queue=JobQueue(boto3.client("sqs"), config.queue_url),
        store=MediaStore(s3_client(), config.media_bucket),
        telegram=TelegramClient,
        policy=AllowlistPolicy(
            boto3.client("ssm"),
            config.telegram_token_param,
            config.telegram_webhook_secret_param,
            config.telegram_approved_param,
        ),
        config=config,
    )


def _action(event: dict[str, Any]) -> Any:
    """The health probe carried in the request body, or None for anything else."""
    raw = event.get("body")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload.get("action") if isinstance(payload, dict) else None


def _update(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body")
    if not isinstance(raw, str) or not raw.strip():
        raise _BadRequest
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise _BadRequest from None
    try:
        payload = json.loads(raw)
    except ValueError:
        raise _BadRequest from None
    if not isinstance(payload, dict):
        raise _BadRequest

    update_id = payload.get("update_id")
    if isinstance(update_id, bool) or not isinstance(update_id, int):
        raise _BadRequest
    return payload


def _header(event: dict[str, Any], name: str) -> str | None:
    headers = event.get("headers") or {}
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return value if isinstance(value, str) else None
    return None


def _empty(status: int) -> dict[str, Any]:
    return {"statusCode": status, "headers": {}, "body": ""}


def _request_text(video_id: Any, tier: Tier) -> str:
    return f"{video_id} {tier.word}"


def _matches(job: dict[str, Any], video_id: str, tier: Tier) -> bool:
    return (
        job.get("video_id") == video_id
        and job.get("requested_tier") == tier.word
        and job.get("deleted_at") is None
    )


def _tier_for(job: dict[str, Any]) -> Tier:
    return TIERS.get(str(job.get("requested_tier")), TIERS["high"])


def _requested(tier: Tier, max_height: int) -> tuple[str | None, int | None]:
    if tier.ceiling is None:
        return None, None
    return quality.cap_requested(quality.label_for(tier.ceiling), max_height)


def _acknowledgement(video_id: str, tier: Tier, height: int | None) -> str:
    if tier.media_kind == "audio":
        return messages.ACCEPTED_AUDIO.format(video_id=video_id)
    return messages.ACCEPTED_VIDEO.format(
        video_id=video_id,
        tier=tier.word,
        ceiling=height if height is not None else tier.ceiling,
    )


def _media(job: dict[str, Any]) -> str:
    if job.get("media_key"):
        return str(job["media_key"])
    extension = "mp3" if job.get("media_kind") == "audio" else "mp4"
    return keys.media_key(str(job["job_id"]), str(job["video_id"]), extension)


def _content_type(job: dict[str, Any]) -> str:
    return _CONTENT_TYPES.get(str(job.get("media_kind")), "video/mp4")


def _title(job: dict[str, Any]) -> str:
    return str(job.get("video_title") or job.get("video_id") or "untitled")


def _produced(job: dict[str, Any]) -> str:
    label = job.get("produced_quality")
    if isinstance(label, str) and label:
        return label
    derived = quality.label_for(job.get("produced_height"))
    if derived is not None:
        return derived
    if job.get("media_kind") == "audio":
        return "mp3"
    return str(job.get("requested_quality") or "unknown")


def _inside_retention(job: dict[str, Any], moment: datetime) -> bool:
    expires_at = job.get("media_expires_at")
    if isinstance(expires_at, bool) or not isinstance(expires_at, int):
        return False
    return int(moment.timestamp()) <= expires_at


def _is_healthy(info: dict[str, Any], expected_url: str | None, now_epoch: int) -> bool:
    if not expected_url or info.get("url") != expected_url:
        return False
    error_at = info.get("last_error_date")
    if isinstance(error_at, bool) or not isinstance(error_at, int):
        return True
    return error_at < now_epoch - HEALTH_WINDOW_SECONDS


def _emit_health(healthy: bool, environment: str) -> None:
    """One Embedded Metric Format line; EMF needs no PutMetricData permission."""
    payload = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": HEALTH_NAMESPACE,
                    "Dimensions": [["Environment"]],
                    "Metrics": [{"Name": HEALTH_METRIC, "Unit": "Count"}],
                }
            ],
        },
        "level": "INFO",
        "event": "webhook_health",
        "Environment": environment,
        HEALTH_METRIC: 1 if healthy else 0,
    }
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
