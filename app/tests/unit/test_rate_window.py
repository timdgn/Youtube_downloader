"""Unit tests for the fixed one-hour rate window (T042)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.s3_fake import FakeMediaStore
from tests.fakes.sqs_fake import FakeJobQueue
from tests.fakes.ssm_fake import FakeSsmClient
from ytdl import keys
from ytdl.api.handler import Api
from ytdl.auth import TokenProvider
from ytdl.config import load_config

TOKEN = "rate-window-token"
NOW = datetime(2026, 11, 15, 14, 45, 30, tzinfo=UTC)
NOW_EPOCH = int(NOW.timestamp())
VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

CONFIG_ENV = {
    "ENVIRONMENT": "dev",
    "JOBS_TABLE": "ytdl-test-jobs",
    "MEDIA_BUCKET": "ytdl-test-media",
    "JOBS_QUEUE_URL": "https://sqs.eu-west-1.amazonaws.com/000000000000/ytdl-test-jobs",
    "TOKEN_PARAM_NAME": "/ytdl/test/api-token",
    "RATE_LIMIT_PER_HOUR": "10",
}


class _Clock:
    """A fixed clock that tests can advance."""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


class _Logger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **fields: object) -> None:
        self.calls.append((event, fields))

    def error(self, event: str, **fields: object) -> None:
        self.calls.append((event, fields))


def _api(clock: _Clock) -> tuple[Api, FakeDynamoRepository, FakeJobQueue]:
    dynamo = FakeDynamoRepository()
    media = FakeMediaStore()
    queue = FakeJobQueue()
    config = load_config(dict(CONFIG_ENV))
    tokens = TokenProvider(
        config.token_param_name,
        FakeSsmClient({config.token_param_name: TOKEN}),
    )
    api = Api(dynamo, queue, media, tokens, config, now=clock, logger=_Logger())
    return api, dynamo, queue


def _submit(api: Api) -> dict:
    event = {
        "version": "2.0",
        "rawPath": "/v1/jobs",
        "headers": {"authorization": f"Bearer {TOKEN}"},
        "requestContext": {"http": {"method": "POST", "path": "/v1/jobs"}},
        "body": json.dumps({"url": VIDEO_URL}),
        "isBase64Encoded": False,
    }
    return api.handle(event)


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_window_start_is_the_utc_hour_boundary() -> None:
    moments = [
        datetime(2026, 11, 15, 0, 0, 0, tzinfo=UTC),
        datetime(2026, 11, 15, 14, 45, 30, tzinfo=UTC),
        datetime(2026, 11, 15, 23, 59, 59, tzinfo=UTC),
    ]

    for moment in moments:
        epoch = int(moment.timestamp())
        window = keys.rate_window(epoch)

        assert window == int(moment.replace(minute=0, second=0, microsecond=0).timestamp())
        assert window % 3600 == 0
        assert window <= epoch < window + 3600
        assert window == keys.RATE_WINDOW_SECONDS * (epoch // keys.RATE_WINDOW_SECONDS)


def test_window_is_stable_inside_one_hour_and_changes_at_the_next() -> None:
    window = keys.rate_window(NOW_EPOCH)

    assert keys.rate_window(window) == window
    assert keys.rate_window(window + 1799) == window
    assert keys.rate_window(window + 3599) == window
    assert keys.rate_window(window + 3600) == window + 3600
    assert keys.rate_window(window + 3601) == window + 3600


def test_rate_key_is_stable_inside_the_window_and_changes_at_the_next() -> None:
    window = keys.rate_window(NOW_EPOCH)

    assert keys.rate_key(window, TOKEN) == keys.rate_key(keys.rate_window(NOW_EPOCH), TOKEN)
    assert keys.rate_key(window, TOKEN) != keys.rate_key(window + 3600, TOKEN)


def test_ttl_is_window_start_plus_two_hours() -> None:
    window = keys.rate_window(NOW_EPOCH)

    assert keys.RATE_TTL_SECONDS == 7200
    assert window + keys.RATE_TTL_SECONDS == window + 7200
    assert window + keys.RATE_TTL_SECONDS == window + 3600 + 3600


def test_tenth_submission_is_accepted_and_the_eleventh_is_refused() -> None:
    clock = _Clock(NOW)
    api, dynamo, queue = _api(clock)
    rate_pk = keys.rate_key(keys.rate_window(NOW_EPOCH), TOKEN)

    for _ in range(9):
        assert _submit(api)["statusCode"] == 202
    assert dynamo.rate_counts[rate_pk] == 9

    tenth = _submit(api)
    assert tenth["statusCode"] == 202
    assert len(queue.messages) == 10

    eleventh = _submit(api)
    assert eleventh["statusCode"] == 429
    assert len(queue.messages) == 10
    assert dynamo.rate_counts[rate_pk] == 10

    window_end = keys.rate_window(NOW_EPOCH) + 3600
    body = json.loads(eleventh["body"])
    assert body["error"]["retry_after"] == _iso(window_end)
    assert eleventh["headers"]["Retry-After"] == str(window_end - NOW_EPOCH)


def test_counter_starts_afresh_in_the_next_window() -> None:
    clock = _Clock(NOW)
    api, _dynamo, _queue = _api(clock)

    for _ in range(10):
        assert _submit(api)["statusCode"] == 202
    assert _submit(api)["statusCode"] == 429

    clock.moment = NOW.replace(minute=0, second=0) + timedelta(hours=1)

    assert _submit(api)["statusCode"] == 202
