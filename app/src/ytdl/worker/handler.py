"""SQS consumer for the worker Lambda (T054, T068).

The message is a pointer. The job record is authoritative, every failure is
classified into the fixed taxonomy, and the batch item failure is returned only
when a retry can still change the outcome.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import boto3

from ytdl import errors, keys, quality, states
from ytdl.adapters.dynamo import DynamoRepository
from ytdl.adapters.extractor import ExtractionError, Extractor
from ytdl.adapters.s3 import MediaStore, s3_client
from ytdl.config import Config, load_config
from ytdl.formats import NoUsableFormat
from ytdl.logging import StructuredLogger, get_logger
from ytdl.worker.pipeline import (
    MEDIA_RETENTION_SECONDS,
    LimitExceeded,
    process_job,
)
from ytdl.worker.remux import convert_to_mp3, probe_output, remux_to_mp4

SUPPORTED_VERSION = 1
FUNCTION_NAME = "worker"
IDEMPOTENCY_PRECHECK = "idempotency_precheck"

_AUDIO = "audio"
_MP3 = "mp3"
_MP4 = "mp4"

_COOKIES_PATH = "/tmp/cookies.txt"
_COOKIES_KEY = "cookies.txt"
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class Logger(Protocol):
    """The subset of the structured logger the worker uses."""

    def info(self, event: str, **fields: object) -> None: ...

    def warning(self, event: str, **fields: object) -> None: ...

    def error(self, event: str, **fields: object) -> None: ...


class Worker:
    """One SQS batch in, one ``batchItemFailures`` payload out."""

    def __init__(
        self,
        repo: Any,
        store: Any,
        extractor: Any,
        config: Config,
        remux_fn: Callable[[str, str], None] = remux_to_mp4,
        mp3_fn: Callable[[str, str], None] = convert_to_mp3,
        probe_fn: Callable[[str], dict[str, Any]] = probe_output,
        logger: Logger | None = None,
        workdir_factory: Callable[[], str] | None = None,
        publish: Callable[[str, str], None] | None = None,
    ) -> None:
        self._repo = repo
        self._store = store
        self._extractor = extractor
        self._config = config
        self._remux_fn = remux_fn
        self._mp3_fn = mp3_fn
        self._probe_fn = probe_fn
        self._logger = logger
        self._workdir_factory = workdir_factory or _default_workdir
        self._publish = publish

    def handle(self, event: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
        failures: list[dict[str, str]] = []
        for record in event.get("Records") or []:
            message_id = str(record.get("messageId", ""))
            try:
                retry = self._process_record(record)
            except Exception:
                # Nothing was recorded, so this is not a job outcome: redeliver.
                retry = True
            if retry:
                failures.append({"itemIdentifier": message_id})
        return {"batchItemFailures": failures}

    def _process_record(self, record: dict[str, Any]) -> bool:
        """Handle one record; return True when the message should be redelivered."""
        message = self._message(record)
        if message is None:
            return False

        job_id = str(message["job_id"])
        job = self._repo.get_job(job_id)
        if job is None or not _needs_work(job):
            return False

        now_epoch = _now_epoch()
        claimed = self._repo.claim_job(job_id, now_epoch, _iso(now_epoch))
        if claimed is None:
            return False

        started = time.monotonic()
        attempt = _attempt(claimed)
        self._log(
            "job_started",
            job_id=job_id,
            state=claimed.get("state"),
            attempt=attempt,
        )

        media = keys.media_key(
            job_id,
            str(claimed.get("video_id") or message.get("video_id") or ""),
            extension=_extension(claimed),
        )
        existing = self._store.head_object(media)
        if existing is not None and _size(existing) > 0:
            return self._complete_from_existing(claimed, media, existing, started)

        workdir = self._workdir_factory()
        try:
            fields = process_job(
                claimed,
                store=self._store,
                extractor=self._extractor,
                config=self._config,
                workdir=workdir,
                remux_fn=self._remux_fn,
                mp3_fn=self._mp3_fn,
                probe_fn=self._probe_fn,
            )
            self._repo.complete_job(job_id, fields, _iso(_now_epoch()))
            self._notify(claimed, "job_completed")
            self._log("job_completed", **_audit(claimed, fields, states.COMPLETED))
            return False
        except Exception as error:
            return self._record_failure(claimed, error, started)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _complete_from_existing(
        self,
        job: dict[str, Any],
        media: str,
        head: dict[str, Any],
        started: float,
    ) -> bool:
        """Reuse a stored object instead of downloading the same job twice."""
        now_epoch = _now_epoch()
        fields = _existing_fields(media, head, now_epoch, started)
        self._log(
            IDEMPOTENCY_PRECHECK,
            job_id=job.get("job_id"),
            attempt=job.get("attempt"),
            media_size_bytes=fields["media_size_bytes"],
        )
        self._repo.complete_job(str(job["job_id"]), fields, _iso(now_epoch))
        self._notify(job, "job_completed")
        self._log("job_completed", **_audit(job, fields, states.COMPLETED))
        return False

    def _record_failure(
        self,
        job: dict[str, Any],
        error: BaseException,
        started: float,
    ) -> bool:
        """Classify, record, and decide whether the message is redelivered."""
        failure = _classify(error)
        processing_ms = max(0, int((time.monotonic() - started) * 1000))
        self._log(
            "job_failed",
            **_audit(job, {"processing_ms": processing_ms}, failure.category),
        )

        job_id = str(job["job_id"])
        try:
            self._repo.fail_job(
                job_id,
                failure.category,
                failure.message,
                failure.retryable,
                _iso(_now_epoch()),
            )
        except Exception:
            # The failure could not be recorded, so a retry still has work to do.
            return True

        retry = failure.retryable and _attempt(job) < states.MAX_ATTEMPTS
        if not retry:
            self._notify(job, "job_failed")
        return retry

    def _notify(self, job: dict[str, Any], event: str) -> None:
        """Publish a terminal event; a publish failure never changes the outcome."""
        if self._publish is None:
            return
        chat_id = job.get("chat_id")
        if chat_id is None or (isinstance(chat_id, str) and not chat_id.strip()):
            return
        try:
            self._publish(event, str(job["job_id"]))
        except Exception as error:
            self._log_error("job_event_publish_failed", type(error).__name__)

    def _message(self, record: dict[str, Any]) -> dict[str, Any] | None:
        """Return the message, or None for anything that cannot be trusted."""
        try:
            return _message_from(record)
        except ValueError as error:
            self._log_error("malformed_message", str(error))
            return None

    def _log(self, event: str, **fields: object) -> None:
        if self._logger is not None:
            self._logger.info(event, **fields)

    def _log_error(self, event: str, message: str) -> None:
        if self._logger is not None:
            self._logger.error(event, message=message)


_WORKER: Worker | None = None


def handler(event: dict[str, Any], context: object = None) -> dict[str, list[dict[str, str]]]:
    """Lambda entry point: build the dependencies once per cold start, then handle."""
    global _WORKER
    if _WORKER is None:
        _WORKER = _build_worker()
    return _WORKER.handle(event)


def _build_worker() -> Worker:
    config = load_config()
    logger = get_logger(FUNCTION_NAME)
    store = MediaStore(s3_client(), config.media_bucket)
    extractor = Extractor(
        cookies_path=_cookies_hook(store, config, logger),
        proxy=_proxy_hook(config, logger),
        player_client=config.player_client,
    )
    return Worker(
        DynamoRepository(boto3.client("dynamodb"), config.table_name),
        store,
        extractor,
        config,
        logger=logger,
        publish=_job_publisher(config),
    )


def _job_publisher(config: Config) -> Callable[[str, str], None] | None:
    """Build the SNS publisher once per cold start, or None when unconfigured."""
    topic_arn = config.job_events_topic_arn
    if not topic_arn:
        return None
    client = boto3.client("sns")

    def publish(event: str, job_id: str) -> None:
        body = {"version": SUPPORTED_VERSION, "event": event, "job_id": job_id}
        client.publish(
            TopicArn=topic_arn,
            Message=json.dumps(body, separators=(",", ":")),
        )

    return publish


def _cookies_hook(store: Any, config: Config, logger: StructuredLogger) -> str | None:
    """Fetch the optional cookies file; a failure leaves the hook unset."""
    if not config.cookies_s3_uri:
        return None
    try:
        text = store.get_text(keys.config_key(_COOKIES_KEY))
        Path(_COOKIES_PATH).write_text(text, encoding="utf-8")
        os.chmod(_COOKIES_PATH, 0o600)
        return _COOKIES_PATH
    except Exception as error:
        logger.warning("cookies_hook_failed", message=type(error).__name__)
        return None


def _proxy_hook(config: Config, logger: StructuredLogger) -> str | None:
    """Read the optional proxy URL from SSM; its value never reaches a log."""
    if not config.proxy_param_name:
        return None
    try:
        response = boto3.client("ssm").get_parameter(
            Name=config.proxy_param_name,
            WithDecryption=True,
        )
        value = response.get("Parameter", {}).get("Value")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("proxy parameter is empty")
        return value
    except Exception as error:
        logger.warning("proxy_hook_failed", message=type(error).__name__)
        return None


def _message_from(record: dict[str, Any]) -> dict[str, Any]:
    body = record.get("body")
    if not isinstance(body, str):
        raise ValueError("message body is not a string")
    try:
        message = json.loads(body)
    except json.JSONDecodeError:
        raise ValueError("message body is not valid JSON") from None
    if not isinstance(message, dict):
        raise ValueError("message body is not a JSON object")
    if message.get("version") != SUPPORTED_VERSION:
        raise ValueError("unsupported message version")
    job_id = message.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("message has no job_id")
    return message


def _classify(error: BaseException) -> errors.Failure:
    """Map one pipeline exception onto the fixed failure taxonomy."""
    if isinstance(error, LimitExceeded):
        return error.failure
    if isinstance(error, NoUsableFormat):
        return errors.Failure(
            errors.UNSUPPORTED_MEDIA,
            errors.MESSAGES[errors.UNSUPPORTED_MEDIA],
            False,
        )
    if isinstance(error, ExtractionError):
        return errors.classify(error.stderr, error.returncode)
    return errors.Failure(
        errors.INTERNAL_ERROR,
        errors.MESSAGES[errors.INTERNAL_ERROR],
        True,
    )


def _needs_work(job: dict[str, Any]) -> bool:
    """A completed job is done; a non-retryable failure cannot be claimed anyway."""
    state = job.get("state")
    if state == states.COMPLETED:
        return False
    if state == states.FAILED:
        return job.get("failure_retryable") is True
    return True


def _extension(job: dict[str, Any]) -> str:
    """The media key extension, derived from the job's media kind."""
    return _MP3 if job.get("media_kind") == _AUDIO else _MP4


def _existing_fields(
    media: str,
    head: dict[str, Any],
    now_epoch: int,
    started: float,
) -> dict[str, Any]:
    """Completion fields for an object that already exists with a non-zero size."""
    audio, height = _existing_media(head)
    fields: dict[str, Any] = {
        "media_key": media,
        "media_size_bytes": _size(head),
        "media_created_at": now_epoch,
        "media_expires_at": now_epoch + MEDIA_RETENTION_SECONDS,
        "processing_ms": max(0, int((time.monotonic() - started) * 1000)),
    }
    if audio:
        fields["produced_quality"] = _MP3
        return fields
    label = quality.label_for(height)
    if height is not None:
        fields["produced_height"] = height
    if label is not None:
        fields["produced_quality"] = label
    return fields


def _existing_media(head: dict[str, Any]) -> tuple[bool, int | None]:
    """Whether the stored object is audio, and its produced height when it is video."""
    metadata = head.get("metadata")
    if isinstance(metadata, dict) and metadata.get("media-kind") == _AUDIO:
        return True, None
    return False, _metadata_height(head)


def _metadata_height(head: dict[str, Any]) -> int | None:
    metadata = head.get("metadata")
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("produced-height")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        height = int(value)
        return height if height > 0 else None
    return None


def _audit(
    job: dict[str, Any],
    fields: dict[str, Any] | None,
    outcome: str,
) -> dict[str, Any]:
    """The audit fields of ``data-model.md``, and nothing else."""
    values = fields or {}
    return {
        "job_id": job.get("job_id"),
        "source_host": job.get("source_host"),
        "video_id": job.get("video_id"),
        "requested_quality": job.get("requested_quality"),
        "produced_quality": values.get("produced_quality"),
        "outcome": outcome,
        "processing_ms": values.get("processing_ms"),
        "media_size_bytes": values.get("media_size_bytes"),
        "attempt": job.get("attempt"),
    }


def _size(head: dict[str, Any]) -> int:
    value = head.get("size")
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _attempt(job: dict[str, Any]) -> int:
    value = job.get("attempt")
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _default_workdir() -> str:
    return tempfile.mkdtemp(dir="/tmp")


def _now_epoch() -> int:
    return int(time.time())


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime(_ISO_FORMAT)
