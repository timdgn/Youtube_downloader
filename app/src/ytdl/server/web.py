"""Plain-HTTP front end for the Docker deployment.

One request becomes the API Gateway v2 event the handlers already understand,
and their response is written back verbatim. Nothing about routing, auth or
bodies is reimplemented here.

Routes:

- ``GET  /healthz``            liveness, no AWS call
- ``POST /v1/jobs``            the terminal API, bearer token required
- ``GET  /v1/jobs/{id}``       "
- ``POST /v1/jobs/{id}/link``  "
- ``POST /telegram/webhook``   the Telegram webhook, secret header required
- ``POST /internal/job-event`` the worker's terminal event, bearer token required
"""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.parse import urlsplit

import boto3

from ytdl.api.handler import build_api
from ytdl.auth import TokenProvider, Unauthorized, check_bearer
from ytdl.bot.handler import build_bot
from ytdl.config import load_config
from ytdl.logging import get_logger

MAX_BODY_BYTES = 1_048_576
BOT_PATH = "/telegram/webhook"
EVENT_PATH = "/internal/job-event"
HEALTH_PATH = "/healthz"
API_PREFIX = "/v1/jobs"

_JSON = "application/json; charset=utf-8"
_NOT_FOUND = {"error": {"code": "not_found", "message": "No route matches.", "retryable": False}}


class Handler(Protocol):
    """What the API and the bot have in common: one event, one response."""

    def handle(self, event: dict[str, Any]) -> dict[str, Any]: ...


class Gateway:
    """Route one request to the API, the bot, or the internal event hook."""

    def __init__(self, api: Handler, bot: Handler, tokens: Any) -> None:
        self._api = api
        self._bot = bot
        self._tokens = tokens

    def dispatch(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, str], bytes]:
        if method == "GET" and path == HEALTH_PATH:
            return 200, {"Content-Type": _JSON}, b'{"status":"ok"}'
        if method == "POST" and path == EVENT_PATH:
            return self._event(headers, body)
        if path == BOT_PATH:
            return self._call(self._bot, method, path, headers, body)
        if path == API_PREFIX or path.startswith(f"{API_PREFIX}/"):
            return self._call(self._api, method, path, headers, body)
        return _respond(404, _NOT_FOUND)

    def _call(
        self,
        handler: Handler,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, str], bytes]:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return _respond(400, {"error": {"code": "invalid_input", "retryable": False}})
        response = handler.handle(_event(method, path, headers, text or None))
        return (
            int(response.get("statusCode", 500)),
            dict(response.get("headers") or {}),
            str(response.get("body") or "").encode("utf-8"),
        )

    def _event(self, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], bytes]:
        """The worker's terminal job event, authenticated with the API token."""
        try:
            check_bearer(_header(headers, "authorization"), self._tokens.get())
        except Unauthorized:
            return _respond(401, {"error": {"code": "unauthorized", "retryable": False}})
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return _respond(400, {"error": {"code": "invalid_input", "retryable": False}})
        if not isinstance(payload, dict) or not isinstance(payload.get("event"), str):
            return _respond(400, {"error": {"code": "invalid_input", "retryable": False}})

        # The bot's SNS branch is the notification path, so the same envelope is
        # built here instead of teaching the bot a second shape.
        message = json.dumps(payload, separators=(",", ":"))
        self._bot.handle({"Records": [{"Sns": {"Message": message}}]})
        return 202, {}, b""


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], gateway: Gateway) -> None:
        super().__init__(address, _RequestHandler)
        self.gateway = gateway


class _RequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ytdl"
    sys_version = ""
    timeout = 30

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler naming)
        self._serve("GET")

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler naming)
        self._serve("POST")

    def _serve(self, method: str) -> None:
        started = time.monotonic()
        path = urlsplit(self.path).path
        try:
            body = self._read_body()
        except ValueError:
            self._write(413, {"Content-Type": _JSON}, _json({"error": {"code": "invalid_input"}}))
            _log(method, path, 413, started, self.client_address)
            return

        try:
            status, headers, payload = self.server.gateway.dispatch(  # type: ignore[attr-defined]
                method, path, dict(self.headers), body
            )
        except Exception as error:
            _log_error(method, path, error)
            status, headers, payload = 500, {"Content-Type": _JSON}, _json(_internal())

        self._write(status, headers, payload)
        _log(method, path, status, started, self.client_address)

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("bad content length") from None
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("body too large")
        return self.rfile.read(length) if length else b""

    def _write(self, status: int, headers: dict[str, str], payload: bytes) -> None:
        self.send_response(status)
        written = False
        for name, value in headers.items():
            if name.lower() == "content-length":
                continue
            self.send_header(name, value)
            if name.lower() == "content-type":
                written = True
        if not written:
            self.send_header("Content-Type", _JSON)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        """Silence BaseHTTPRequestHandler; ``_serve`` writes the one line we keep."""


def serve(host: str, port: int) -> None:
    config = load_config()
    tokens = TokenProvider(config.token_param_name, boto3.client("ssm"))
    gateway = Gateway(build_api(tokens), build_bot(), tokens)
    server = _Server((host, port), gateway)
    get_logger("web").info(
        "listening",
        host=host,
        port=port,
        environment=config.environment,
    )
    server.serve_forever()


def main() -> None:
    serve(os.environ.get("YTDL_WEB_HOST", "0.0.0.0"), int(os.environ.get("YTDL_WEB_PORT", "8090")))


def _event(method: str, path: str, headers: dict[str, str], body: str | None) -> dict[str, Any]:
    return {
        "version": "2.0",
        "rawPath": path,
        "headers": {str(name).lower(): value for name, value in headers.items()},
        "requestContext": {"http": {"method": method, "path": path}},
        "body": body,
        "isBase64Encoded": False,
    }


def _header(headers: dict[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def _respond(status: int, body: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
    return status, {"Content-Type": _JSON}, _json(body)


def _json(body: dict[str, Any]) -> bytes:
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _internal() -> dict[str, Any]:
    return {"error": {"code": "internal_error", "retryable": True}}


def _log(
    method: str,
    path: str,
    status: int,
    started: float,
    address: tuple[str, int],
) -> None:
    get_logger("web").info(
        "request",
        method=method,
        path=path,
        status=status,
        duration_ms=int((time.monotonic() - started) * 1000),
        source=address[0],
    )


def _log_error(method: str, path: str, error: BaseException) -> None:
    # Only the class name: a message could carry a token or a URL.
    get_logger("web").error(
        "request_failed", method=method, path=path, message=type(error).__name__
    )


if __name__ == "__main__":
    main()
