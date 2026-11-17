"""API Gateway v2 HTTP handler: routes, auth, and the three endpoints (T051, T055)."""

from __future__ import annotations

import base64
import json
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote

import boto3

from ytdl import keys, quality, submissions
from ytdl.adapters.dynamo import DynamoRepository, RateLimited
from ytdl.adapters.s3 import MediaStore, s3_client
from ytdl.adapters.sqs import JobQueue
from ytdl.api import routes
from ytdl.auth import TokenProvider, Unauthorized, check_bearer
from ytdl.config import Config, load_config
from ytdl.logging import StructuredLogger, get_logger
from ytdl.states import COMPLETED, FAILED
from ytdl.urls import InvalidVideoURL, parse_video_url

CONTENT_TYPE_MEDIA = "video/mp4"

_CREATE_PATH = "/v1/jobs"
_JOB_PATH = re.compile(r"^/v1/jobs/([^/]+)$")
_LINK_PATH = re.compile(r"^/v1/jobs/([^/]+)/link$")

_RETRYABLE = {
    "invalid_input": False,
    "unauthorized": False,
    "rate_limited": True,
    "not_found": False,
    "not_completed": False,
    "result_expired": False,
    "internal_error": True,
}

_api: Api | None = None


class _BadRequest(Exception):
    """A body that cannot be read as the documented request shape."""


class Api:
    """The three routes, with every dependency injected for tests."""

    def __init__(
        self,
        repo: Any,
        queue: Any,
        store: Any,
        tokens: TokenProvider,
        config: Config,
        now: Any = None,
        logger: StructuredLogger | None = None,
    ) -> None:
        self._repo = repo
        self._queue = queue
        self._store = store
        self._tokens = tokens
        self._config = config
        self._now = now if now is not None else _utc_now
        self._logger = logger if logger is not None else get_logger("api")

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        """Dispatch one API Gateway v2 event and always return a JSON response."""
        started = time.monotonic()
        try:
            response = self._dispatch(event)
        except Exception as error:
            # A caught exception leaves no traceback in CloudWatch unless it is
            # logged here. Only the class name is recorded: a message could carry
            # a URL or a credential.
            self._logger.error("request_failed", message=type(error).__name__)
            response = _error("internal_error", 500)
        self._log_request(started, response)
        return response

    def _dispatch(self, event: dict[str, Any]) -> dict[str, Any]:
        route = _route(_method(event), _path(event))
        if route is None:
            return _error("not_found", 404)

        name, job_id = route
        try:
            expected_token = self._tokens.get()
        except Exception:
            return _error("internal_error", 500)

        try:
            check_bearer(_header(event, "authorization"), expected_token)
        except Unauthorized:
            return _error("unauthorized", 401)

        if name == "create":
            return self._create(event, expected_token)
        if name == "get":
            return self._get(job_id)
        return self._reissue(job_id)

    # --- POST /v1/jobs -----------------------------------------------------

    def _create(self, event: dict[str, Any], token: str) -> dict[str, Any]:
        try:
            payload = _payload(event)
            reference = parse_video_url(payload.get("url"))
            requested_quality, requested_height = _quality(payload, self._config.max_height)
        except (_BadRequest, InvalidVideoURL):
            return _error("invalid_input", 400)

        moment = self._now()
        now_epoch = int(moment.timestamp())
        created_at = _iso(moment)
        job = submissions.build_job(
            reference,
            created_at,
            requested_quality,
            requested_height,
        )

        rate_pk = keys.rate_key(keys.rate_window(now_epoch), token)
        message = {
            "version": 1,
            "job_id": job["job_id"],
            "video_id": reference.video_id,
            "source_host": reference.host,
            "requested_height": requested_height if requested_quality is not None else None,
            "enqueued_at": created_at,
        }
        try:
            submissions.submit(
                self._repo,
                self._queue,
                self._config,
                job,
                rate_pk,
                self._config.rate_limit_per_hour,
                now_epoch,
                None,
                message,
            )
        except RateLimited as limited:
            retry_after = _iso_epoch(limited.retry_at_epoch)
            seconds = max(1, limited.retry_at_epoch - now_epoch)
            return routes.response(
                429,
                routes.error_body(
                    "rate_limited",
                    routes.MESSAGES["rate_limited"].format(retry_after=retry_after),
                    True,
                    retry_after,
                ),
                {"Retry-After": str(seconds)},
            )

        return routes.response(202, routes.accepted_body(job))

    # --- GET /v1/jobs/{job_id} ---------------------------------------------

    def _get(self, job_id: str) -> dict[str, Any]:
        job = self._repo.get_job(job_id)
        if job is None:
            return _error("not_found", 404)

        result = self._result(job)
        body = routes.job_body(job, result, _failure(job))
        return routes.response(200, body)

    # --- POST /v1/jobs/{job_id}/link ---------------------------------------

    def _reissue(self, job_id: str) -> dict[str, Any]:
        job = self._repo.get_job(job_id)
        if job is None:
            return _error("not_found", 404)
        if job.get("state") != COMPLETED:
            return _error("not_completed", 409)
        # A file deleted through the bot is gone even if its object is still
        # readable for a moment, so the answer never depends on S3 timing.
        if job.get("deleted_at") is not None:
            return _error("result_expired", 410)

        moment = self._now()
        media_key, head = self._media(job)
        if not _inside_retention(job, head, moment):
            return _error("result_expired", 410)

        url = self._presign(media_key, job)
        expires_at = _iso(moment + timedelta(seconds=self._config.link_ttl_seconds))
        return routes.response(200, routes.link_body(job, url, expires_at, head["size"]))

    # --- shared read paths --------------------------------------------------

    def _result(self, job: dict[str, Any]) -> dict[str, Any] | None:
        if job.get("state") != COMPLETED:
            return None

        moment = self._now()
        if job.get("deleted_at") is not None:
            return {
                "status": "expired",
                "download_url": None,
                "expires_at": None,
                "size_bytes": job.get("media_size_bytes"),
                "content_type": CONTENT_TYPE_MEDIA,
            }

        media_key, head = self._media(job)
        if _inside_retention(job, head, moment):
            return {
                "status": "available",
                "download_url": self._presign(media_key, job),
                "expires_at": _iso(moment + timedelta(seconds=self._config.link_ttl_seconds)),
                "size_bytes": head["size"],
                "content_type": CONTENT_TYPE_MEDIA,
            }

        stored_size = job.get("media_size_bytes")
        if stored_size is None and head is not None:
            stored_size = head["size"]
        return {
            "status": "expired",
            "download_url": None,
            "expires_at": None,
            "size_bytes": stored_size,
            "content_type": CONTENT_TYPE_MEDIA,
        }

    def _media(self, job: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        key = job.get("media_key") or keys.media_key(job["job_id"], job["video_id"])
        return key, self._store.head_object(key)

    def _presign(self, media_key: str, job: dict[str, Any]) -> str:
        filename = f"{job['video_id']}-{job['job_id']}.mp4"
        return self._store.presign_get(media_key, filename, self._config.link_ttl_seconds)

    # --- logging ------------------------------------------------------------

    def _log_request(self, started: float, response: dict[str, Any]) -> None:
        duration_ms = int((time.monotonic() - started) * 1000)
        body = json.loads(response["body"]) if response.get("body") else {}
        error = body.get("error") if isinstance(body, dict) else None

        fields: dict[str, Any] = {"duration_ms": duration_ms}
        if isinstance(error, dict):
            fields["message"] = str(error.get("code", ""))
            fields["retryable"] = bool(error.get("retryable", False))
        else:
            if body.get("job_id"):
                fields["job_id"] = body["job_id"]
            if body.get("state"):
                fields["state"] = body["state"]

        if response["statusCode"] >= 500:
            self._logger.error("request", **fields)
        else:
            self._logger.info("request", **fields)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point; the dependencies are built once per cold start."""
    global _api
    if _api is None:
        _api = build_api()
    return _api.handle(event)


def build_api(tokens: TokenProvider | None = None) -> Api:
    config = load_config()
    return Api(
        repo=DynamoRepository(boto3.client("dynamodb"), config.table_name),
        queue=JobQueue(boto3.client("sqs"), config.queue_url),
        store=MediaStore(s3_client(), config.media_bucket),
        tokens=tokens or TokenProvider(config.token_param_name, boto3.client("ssm")),
        config=config,
    )


def _route(method: str, path: str) -> tuple[str, str] | None:
    if method == "POST" and path == _CREATE_PATH:
        return ("create", "")
    match = _LINK_PATH.match(path)
    if method == "POST" and match is not None:
        return ("link", unquote(match.group(1)))
    match = _JOB_PATH.match(path)
    if method == "GET" and match is not None:
        return ("get", unquote(match.group(1)))
    return None


def _method(event: dict[str, Any]) -> str:
    context = event.get("requestContext") or {}
    http = context.get("http") or {}
    method = http.get("method") or event.get("httpMethod") or ""
    return str(method).upper()


def _path(event: dict[str, Any]) -> str:
    return str(event.get("rawPath") or event.get("path") or "/")


def _header(event: dict[str, Any], name: str) -> str | None:
    headers = event.get("headers") or {}
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return value
    return None


def _payload(event: dict[str, Any]) -> dict[str, Any]:
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
    return payload


def _quality(payload: dict[str, Any], ceiling: int) -> tuple[str | None, int | None]:
    """Resolve the optional quality field into the capped label and its height.

    An absent or null value keeps no label and no height, so the job item gains
    neither field and the message carries ``requested_height: null``.
    """
    if "quality" not in payload or payload["quality"] is None:
        return None, None

    value = payload["quality"]
    if not isinstance(value, str):
        raise _BadRequest
    try:
        return quality.cap_requested(value, ceiling)
    except quality.InvalidQuality:
        raise _BadRequest from None


def _inside_retention(
    job: dict[str, Any],
    head: dict[str, Any] | None,
    moment: datetime,
) -> bool:
    if head is None:
        return False
    expires_at = job.get("media_expires_at")
    if expires_at is None:
        return False
    return int(moment.timestamp()) <= int(expires_at)


def _failure(job: dict[str, Any]) -> dict[str, Any] | None:
    if job.get("state") != FAILED:
        return None
    return {
        "category": job.get("failure_category") or "internal_error",
        "retryable": bool(job.get("failure_retryable", False)),
        "message": job.get("failure_message") or routes.MESSAGES["internal_error"],
    }


def _error(code: str, status: int) -> dict[str, Any]:
    return routes.response(
        status,
        routes.error_body(code, routes.MESSAGES[code], _RETRYABLE[code]),
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_epoch(epoch: int) -> str:
    return _iso(datetime.fromtimestamp(epoch, UTC))
