"""SQS consumer for the worker Lambda (T054)."""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

import boto3

from ytdl import states
from ytdl.adapters.dynamo import DynamoRepository
from ytdl.adapters.extractor import Extractor
from ytdl.adapters.s3 import MediaStore
from ytdl.config import Config, load_config
from ytdl.logging import get_logger
from ytdl.worker.pipeline import process_job
from ytdl.worker.remux import probe_output, remux_to_mp4

SUPPORTED_VERSION = 1
FUNCTION_NAME = "worker"

_TERMINAL_STATES = frozenset({states.COMPLETED, states.FAILED})
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class Logger(Protocol):
    """The subset of the structured logger the worker uses."""

    def info(self, event: str, **fields: object) -> None: ...

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
        probe_fn: Callable[[str], dict[str, Any]] = probe_output,
        logger: Logger | None = None,
        workdir_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repo = repo
        self._store = store
        self._extractor = extractor
        self._config = config
        self._remux_fn = remux_fn
        self._probe_fn = probe_fn
        self._logger = logger
        self._workdir_factory = workdir_factory or _default_workdir

    def handle(self, event: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
        failures: list[dict[str, str]] = []
        for record in event.get("Records") or []:
            message_id = str(record.get("messageId", ""))
            try:
                self._process_record(record)
            except Exception:
                failures.append({"itemIdentifier": message_id})
        return {"batchItemFailures": failures}

    def _process_record(self, record: dict[str, Any]) -> None:
        message = _parse_message(record)
        job_id = message["job_id"]

        job = self._repo.get_job(job_id)
        if job is None or job.get("state") in _TERMINAL_STATES:
            return

        now_epoch = _now_epoch()
        claimed = self._repo.claim_job(job_id, now_epoch, _iso(now_epoch))
        if claimed is None:
            return

        self._log(
            "job_started",
            job_id=job_id,
            state=claimed.get("state"),
            attempt=claimed.get("attempt"),
        )

        workdir = self._workdir_factory()
        try:
            fields = process_job(
                claimed,
                store=self._store,
                extractor=self._extractor,
                config=self._config,
                workdir=workdir,
                remux_fn=self._remux_fn,
                probe_fn=self._probe_fn,
            )
            self._repo.complete_job(job_id, fields, _iso(_now_epoch()))
            self._log(
                "job_completed",
                job_id=job_id,
                state=states.COMPLETED,
                processing_ms=fields.get("processing_ms"),
                media_size_bytes=fields.get("media_size_bytes"),
            )
        except Exception as error:
            self._log(
                "job_failed",
                job_id=job_id,
                state=states.PROCESSING,
                message=type(error).__name__,
            )
            raise
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _log(self, event: str, **fields: object) -> None:
        if self._logger is not None:
            self._logger.info(event, **fields)


_WORKER: Worker | None = None


def handler(event: dict[str, Any], context: object = None) -> dict[str, list[dict[str, str]]]:
    """Lambda entry point: build the dependencies once per cold start, then handle."""
    global _WORKER
    if _WORKER is None:
        _WORKER = _build_worker()
    return _WORKER.handle(event)


def _build_worker() -> Worker:
    config = load_config()
    return Worker(
        DynamoRepository(boto3.client("dynamodb"), config.table_name),
        MediaStore(boto3.client("s3"), config.media_bucket),
        Extractor(),
        config,
        logger=get_logger(FUNCTION_NAME),
    )


def _parse_message(record: dict[str, Any]) -> dict[str, Any]:
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


def _default_workdir() -> str:
    return tempfile.mkdtemp(dir="/tmp")


def _now_epoch() -> int:
    return int(time.time())


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime(_ISO_FORMAT)
