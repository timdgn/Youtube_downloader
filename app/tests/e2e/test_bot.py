"""Opt-in end-to-end check of the deployed bot (T066).

This is the only test that reaches the deployed stack. It posts a synthetic API
Gateway event at the bot route, so it proves the route, the secret header and the
health metric without sending any chat traffic.

    BOT_WEBHOOK_URL=$(terraform -chdir=infra output -raw bot_webhook_url) \
    BOT_WEBHOOK_SECRET=$(aws ssm get-parameter --with-decryption --output text \
      --query Parameter.Value --name "$(terraform -chdir=infra output -raw \
      telegram_token_parameter_name | sed 's/bot-token/webhook-secret/')") \
    python -m pytest -m e2e tests/e2e/test_bot.py

One operator request is sent only when YTDL_E2E_SEND=1, and then exactly once:
E2E_CHAT_ID and E2E_VIDEO_URL must also be set. The bot's reply lands in that
chat, and the assertion here is the 200 that proves the update was handled. The
test never loops, never retries and never repeats a run: sending the same update
twice would prove nothing the offline suite has not already proven.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

import pytest

BOT_WEBHOOK_URL = os.environ.get("BOT_WEBHOOK_URL", "").rstrip("/")
BOT_WEBHOOK_SECRET = os.environ.get("BOT_WEBHOOK_SECRET", "")
E2E_CHAT_ID = os.environ.get("E2E_CHAT_ID", "")
E2E_VIDEO_URL = os.environ.get("E2E_VIDEO_URL", "")
SEND = os.environ.get("YTDL_E2E_SEND") == "1"

REQUEST_TIMEOUT_SECONDS = 30.0

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not (BOT_WEBHOOK_URL and BOT_WEBHOOK_SECRET),
        reason="BOT_WEBHOOK_URL and BOT_WEBHOOK_SECRET must both be set",
    ),
]


def _post(body: dict, secret: str) -> tuple[int, bytes]:
    """Post one synthetic API Gateway v2 event at the deployed route."""
    event = {
        "version": "2.0",
        "rawPath": "/telegram/webhook",
        "headers": {"x-telegram-bot-api-secret-token": secret},
        "requestContext": {"http": {"method": "POST", "path": "/telegram/webhook"}},
        "body": json.dumps(body),
        "isBase64Encoded": False,
    }
    request = urllib.request.Request(
        BOT_WEBHOOK_URL,
        data=json.dumps(event).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def test_a_wrong_secret_is_forbidden() -> None:
    status, body = _post({"action": "health"}, secret="not-the-webhook-secret")

    assert status == 403
    assert body == b""


def test_the_health_probe_answers_200() -> None:
    status, body = _post({"action": "health"}, secret=BOT_WEBHOOK_SECRET)

    assert status == 200
    assert body == b""


@pytest.mark.skipif(
    not (SEND and E2E_CHAT_ID and E2E_VIDEO_URL),
    reason="YTDL_E2E_SEND=1, E2E_CHAT_ID and E2E_VIDEO_URL must all be set",
)
def test_one_operator_request_is_acknowledged() -> None:
    update = {
        "update_id": int(time.time()),
        "message": {
            "message_id": 1,
            "date": int(time.time()),
            "chat": {"id": int(E2E_CHAT_ID), "type": "private"},
            "from": {"id": int(E2E_CHAT_ID), "is_bot": False},
            "text": f"{E2E_VIDEO_URL} low",
        },
    }

    status, body = _post(update, secret=BOT_WEBHOOK_SECRET)

    assert status == 200
    assert body == b""
