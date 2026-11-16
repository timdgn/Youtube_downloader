"""Contract tests for one accepted Telegram request (T027)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.s3_fake import FakeMediaStore
from tests.fakes.sqs_fake import FakeJobQueue
from tests.fakes.telegram_fake import FakeTelegramClient
from ytdl import errors, keys
from ytdl.bot import messages
from ytdl.bot.handler import Bot
from ytdl.config import load_config

SECRET = "contract-test-webhook-secret"
USER_ID = 123456789
CHAT_ID = 123456789
NOW = datetime(2026, 11, 16, 10, 30, 0, tzinfo=UTC)
NOW_EPOCH = int(NOW.timestamp())
VIDEO_ID = "dQw4w9WgXcQ"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
ENV = {
    "ENVIRONMENT": "dev",
    "JOBS_TABLE": "ytdl-test-jobs",
    "MEDIA_BUCKET": "ytdl-test-media",
    "JOBS_QUEUE_URL": "https://sqs.eu-west-3.amazonaws.com/000000000000/ytdl-test-jobs",
    "TOKEN_PARAM_NAME": "/ytdl/test/api-token",
    "TELEGRAM_TOKEN_PARAM": "/ytdl/test/telegram-bot-token",
    "TELEGRAM_WEBHOOK_SECRET_PARAM": "/ytdl/test/telegram-webhook-secret",
    "TELEGRAM_APPROVED_PARAM": "/ytdl/test/telegram-approved-user-ids",
    "TELEGRAM_WEBHOOK_URL": "https://api.example.invalid/telegram/webhook",
    "TELEGRAM_DEFAULT_TIER": "high",
    "TELEGRAM_TEST_VIDEO_ID": VIDEO_ID,
    "JOB_EVENTS_TOPIC_ARN": "arn:aws:sns:eu-west-3:000000000000:ytdl-test-job-events",
    "YTDLP_VERSION": "2026.10.26",
}


class _Logger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **fields: object) -> None:
        self.calls.append((event, fields))

    def warning(self, event: str, **fields: object) -> None:
        self.calls.append((event, fields))

    def error(self, event: str, **fields: object) -> None:
        self.calls.append((event, fields))


class _Policy:
    token = "test-token"

    def read(self) -> None:
        return None

    def check_secret(self, presented: str | None) -> bool:
        return presented == SECRET

    def is_approved(self, user_id: int | str) -> bool:
        return str(user_id) == str(USER_ID)

    def is_private(self, chat_type: str | None) -> bool:
        return chat_type == "private"


class _TimedTelegram(FakeTelegramClient):
    def __init__(self, timeline: list[str]) -> None:
        super().__init__()
        self.timeline = timeline

    def send_message(self, chat_id: int | str, text: str) -> None:
        self.timeline.append("send")
        super().send_message(chat_id, text)


class _TimedQueue(FakeJobQueue):
    def __init__(self, timeline: list[str]) -> None:
        super().__init__()
        self.timeline = timeline

    def enqueue(self, body: dict) -> None:
        self.timeline.append("enqueue")
        super().enqueue(body)


def _event(text: str, update_id: int = 812345678) -> dict:
    return {
        "version": "2.0",
        "rawPath": "/telegram/webhook",
        "headers": {"x-telegram-bot-api-secret-token": SECRET},
        "requestContext": {"http": {"method": "POST", "path": "/telegram/webhook"}},
        "body": __import__("json").dumps(
            {
                "update_id": update_id,
                "message": {
                    "message_id": 4412,
                    "date": NOW_EPOCH,
                    "chat": {"id": CHAT_ID, "type": "private", "first_name": "Operator"},
                    "from": {"id": USER_ID, "is_bot": False, "first_name": "Operator"},
                    "text": text,
                },
            }
        ),
        "isBase64Encoded": False,
    }


def _bot(
    dynamo: FakeDynamoRepository,
    media: FakeMediaStore,
    queue: FakeJobQueue,
    telegram: FakeTelegramClient,
) -> Bot:
    return Bot(
        dynamo,
        queue,
        media,
        lambda token: telegram,
        _Policy(),
        load_config(dict(ENV)),
        now=lambda: NOW,
        logger=_Logger(),
    )


def _jobs(dynamo: FakeDynamoRepository) -> list[dict]:
    return [item for key, item in dynamo.items.items() if key.startswith("JOB#")]


def _interactions(dynamo: FakeDynamoRepository) -> list[dict]:
    return [item for key, item in dynamo.items.items() if key.startswith("UPD#")]


def _seed_stored(
    dynamo: FakeDynamoRepository,
    media: FakeMediaStore,
    *,
    state: str = "completed",
    tier: str = "medium",
    job_id: str = "m3x9k2q1f7k2q1",
    expired: bool = False,
) -> dict:
    job = {
        "job_id": job_id,
        "state": state,
        "created_at": "2026-11-16T09:00:00Z",
        "updated_at": "2026-11-16T09:05:00Z",
        "source_host": "www.youtube.com",
        "video_id": VIDEO_ID,
        "owner_key": keys.owner_job_key(USER_ID),
        "chat_id": str(CHAT_ID),
        "update_id": 1,
        "requested_tier": tier,
        "requested_quality": "480p",
        "requested_height": 480,
        "media_kind": "video",
        "produced_quality": "480p",
        "produced_height": 480,
        "video_title": "A stored video",
        "media_key": keys.media_key(job_id, VIDEO_ID),
        "media_size_bytes": 191_260_000,
        "media_created_at": NOW_EPOCH - 7200,
        "media_expires_at": NOW_EPOCH - 60 if expired else NOW_EPOCH + 3 * 86400,
    }
    dynamo.items["JOB#" + job_id] = dict(job)
    media.seed(job["media_key"], b"stored-bytes")
    return job


# --- acceptance --------------------------------------------------------------


def test_an_accepted_request_writes_one_job_and_acknowledges_it() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()

    response = _bot(dynamo, media, queue, telegram).handle(_event(f"{VIDEO_URL} medium"))

    assert response["statusCode"] == 200
    (job,) = _jobs(dynamo)
    assert job["owner_key"] == keys.owner_job_key(USER_ID)
    assert job["chat_id"] == str(CHAT_ID)
    assert job["update_id"] == 812345678
    assert job["requested_tier"] == "medium"
    assert job["media_kind"] == "video"
    assert job["requested_quality"] == "480p"
    assert job["requested_height"] == 480
    assert "is_test" not in job

    (interaction,) = _interactions(dynamo)
    assert interaction["kind"] == "interaction"
    assert interaction["outcome"] == "accepted"
    assert interaction["job_id"] == job["job_id"]
    assert interaction["text"] == f"{VIDEO_ID} medium"

    assert telegram.messages == [
        {
            "chat_id": CHAT_ID,
            "text": messages.ACCEPTED_VIDEO.format(video_id=VIDEO_ID, tier="medium", ceiling=480),
        }
    ]
    assert len(queue.messages) == 1


def test_the_acknowledgement_is_sent_before_the_queue_call() -> None:
    timeline: list[str] = []
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()

    _bot(dynamo, media, _TimedQueue(timeline), _TimedTelegram(timeline)).handle(
        _event(f"{VIDEO_URL} medium")
    )

    assert timeline == ["send", "enqueue"]


def test_the_rate_window_is_incremented_once() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()

    _bot(dynamo, media, queue, FakeTelegramClient()).handle(_event(f"{VIDEO_URL} medium"))

    rate_pk = keys.rate_key(keys.rate_window(NOW_EPOCH), keys.owner_job_key(USER_ID))
    assert dynamo.rate_counts == {rate_pk: 1}


def test_the_queue_message_carries_the_height_and_the_media_kind() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()

    _bot(dynamo, media, queue, FakeTelegramClient()).handle(_event(f"{VIDEO_URL} medium"))

    (message,) = queue.messages
    assert message["requested_height"] == 480
    assert message["media_kind"] == "video"
    assert message["video_id"] == VIDEO_ID
    assert message["version"] == 1


def test_a_request_without_a_word_uses_the_default_tier_and_says_which() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()

    _bot(dynamo, media, queue, telegram).handle(_event(VIDEO_URL))

    (job,) = _jobs(dynamo)
    assert job["requested_tier"] == "high"
    assert "at high" in telegram.messages[0]["text"]


def test_an_mp3_request_creates_an_audio_job() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()

    _bot(dynamo, media, queue, telegram).handle(_event(f"{VIDEO_URL} mp3"))

    (job,) = _jobs(dynamo)
    assert job["media_kind"] == "audio"
    assert job["requested_tier"] == "mp3"
    assert "requested_quality" not in job
    assert "requested_height" not in job
    assert telegram.messages == [
        {"chat_id": CHAT_ID, "text": messages.ACCEPTED_AUDIO.format(video_id=VIDEO_ID)}
    ]
    assert queue.messages[0]["media_kind"] == "audio"


def test_a_duplicate_update_produces_one_job_one_enqueue_and_one_reply() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()
    bot = _bot(dynamo, media, queue, telegram)
    event = _event(f"{VIDEO_URL} medium")

    bot.handle(event)
    bot.handle(event)

    assert len(_jobs(dynamo)) == 1
    assert len(queue.messages) == 1
    assert len(telegram.messages) == 1


# --- reuse and in-progress ---------------------------------------------------


def test_a_stored_item_is_reissued_without_a_second_download() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()
    job = _seed_stored(dynamo, media)

    _bot(dynamo, media, queue, telegram).handle(_event(f"{VIDEO_URL} medium"))

    assert len(_jobs(dynamo)) == 1
    assert queue.messages == []
    assert dynamo.rate_counts == {}

    (message,) = telegram.messages
    text = message["text"]
    assert text.startswith("Already stored: A stored video")
    assert "Quality: 480p" in text
    assert "https://" in text
    assert "expires 2026-11-16 11:30 UTC" in text
    assert "stays available until 2026-11-19" in text

    (interaction,) = _interactions(dynamo)
    assert interaction["outcome"] == "reissued"
    assert interaction["job_id"] == job["job_id"]


def test_a_stored_item_uses_its_display_name_and_content_type() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    job = _seed_stored(dynamo, media)

    _bot(dynamo, media, queue, FakeTelegramClient()).handle(_event(f"{VIDEO_URL} medium"))

    (presign,) = [call for call in media.calls if call[0] == "presign_get"]
    assert presign[1]["filename"] == keys.display_name(job)
    assert presign[1]["content_type"] == "video/mp4"


def test_an_expired_item_is_downloaded_again_instead_of_linking_a_dead_object() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_stored(dynamo, media, expired=True)

    _bot(dynamo, media, queue, FakeTelegramClient()).handle(_event(f"{VIDEO_URL} medium"))

    assert len(_jobs(dynamo)) == 2
    assert len(queue.messages) == 1


def test_an_in_progress_item_is_answered_without_a_second_job() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()
    _seed_stored(dynamo, media, state="processing")

    _bot(dynamo, media, queue, telegram).handle(_event(f"{VIDEO_URL} medium"))

    assert len(_jobs(dynamo)) == 1
    assert queue.messages == []
    assert telegram.messages == [
        {
            "chat_id": CHAT_ID,
            "text": messages.IN_PROGRESS.format(video_id=VIDEO_ID, tier="medium"),
        }
    ]


def test_a_stored_item_at_another_tier_is_not_reused() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    _seed_stored(dynamo, media, tier="high")

    _bot(dynamo, media, queue, FakeTelegramClient()).handle(_event(f"{VIDEO_URL} medium"))

    assert len(_jobs(dynamo)) == 2
    assert len(queue.messages) == 1


def test_a_deleted_item_is_never_reused() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    job = _seed_stored(dynamo, media)
    dynamo.items["JOB#" + job["job_id"]]["deleted_at"] = "2026-11-16T09:30:00Z"

    _bot(dynamo, media, queue, FakeTelegramClient()).handle(_event(f"{VIDEO_URL} medium"))

    assert len(_jobs(dynamo)) == 2


# --- refusals ----------------------------------------------------------------


def test_an_exhausted_rate_limit_creates_no_job() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()
    rate_pk = keys.rate_key(keys.rate_window(NOW_EPOCH), keys.owner_job_key(USER_ID))
    dynamo.rate_counts[rate_pk] = 10

    _bot(dynamo, media, queue, telegram).handle(_event(f"{VIDEO_URL} medium"))

    assert _jobs(dynamo) == []
    assert queue.messages == []
    returns_at = messages.format_minute(keys.rate_window(NOW_EPOCH) + 3600)
    assert telegram.messages == [
        {"chat_id": CHAT_ID, "text": messages.RATE_LIMITED.format(retry_at=returns_at)}
    ]
    (interaction,) = _interactions(dynamo)
    assert interaction["outcome"] == "rate_limited"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(f"{VIDEO_URL} {VIDEO_URL}", messages.TWO_LINKS, id="two-links"),
        pytest.param(
            f"{VIDEO_URL} hq",
            messages.UNKNOWN_RESOLUTION.format(word="hq"),
            id="unknown-word",
        ),
        pytest.param("https://vimeo.com/123456789", messages.INVALID_LINK, id="foreign-host"),
        pytest.param("just some words", messages.FORMAT_HINT, id="no-link"),
    ],
)
def test_a_refused_message_creates_no_job(text: str, expected: str) -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()

    response = _bot(dynamo, media, queue, telegram).handle(_event(text))

    assert response["statusCode"] == 200
    assert telegram.messages == [{"chat_id": CHAT_ID, "text": expected}]
    assert _jobs(dynamo) == []
    assert queue.messages == []
    assert dynamo.rate_counts == {}


def test_the_invalid_link_sentence_is_the_service_sentence() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    telegram = FakeTelegramClient()

    _bot(dynamo, media, queue, telegram).handle(_event("https://vimeo.com/123456789"))

    assert telegram.messages[0]["text"] == errors.MESSAGES[errors.INVALID_INPUT]


# --- sequence numbers --------------------------------------------------------


def test_the_first_request_of_an_account_gets_number_one() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()

    _bot(dynamo, media, queue, FakeTelegramClient()).handle(_event(f"{VIDEO_URL} medium"))

    (job,) = _jobs(dynamo)
    assert job["seq"] == 1
    assert dynamo.items["SEQ#" + str(USER_ID)]["seq"] == 1


def test_a_second_request_gets_the_next_number() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    bot = _bot(dynamo, media, queue, FakeTelegramClient())

    bot.handle(_event(f"{VIDEO_URL} medium", update_id=1))
    bot.handle(_event(f"{VIDEO_URL} high", update_id=2))

    numbered = sorted(job["seq"] for job in _jobs(dynamo))
    assert numbered == [1, 2]


def test_a_refused_request_burns_a_number_without_creating_a_job() -> None:
    dynamo, media, queue = FakeDynamoRepository(), FakeMediaStore(), FakeJobQueue()
    bot = _bot(dynamo, media, queue, FakeTelegramClient())
    rate_pk = keys.rate_key(keys.rate_window(NOW_EPOCH), keys.owner_job_key(USER_ID))
    dynamo.rate_counts[rate_pk] = 10

    bot.handle(_event(f"{VIDEO_URL} medium"))
    dynamo.rate_counts[rate_pk] = 0
    bot.handle(_event(f"{VIDEO_URL} high", update_id=2))

    (job,) = _jobs(dynamo)
    assert job["seq"] == 2
    assert dynamo.items["SEQ#" + str(USER_ID)]["seq"] == 2
