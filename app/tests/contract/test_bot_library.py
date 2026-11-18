"""Contract tests for /list, /delete and /empty (T050)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.s3_fake import FakeMediaStore
from tests.fakes.sqs_fake import FakeJobQueue
from tests.fakes.telegram_fake import FakeTelegramClient
from ytdl import keys
from ytdl.bot import messages
from ytdl.bot.handler import Bot
from ytdl.config import load_config

SECRET = "contract-test-webhook-secret"
USER_ID = 123456789
CHAT_ID = 123456789
NOW = datetime(2026, 11, 16, 10, 30, 0, tzinfo=UTC)
NOW_EPOCH = int(NOW.timestamp())
DAY = 86400
VIDEO_ID = "dQw4w9WgXcQ"
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


def _bot(
    dynamo: FakeDynamoRepository,
    media: FakeMediaStore,
    queue: FakeJobQueue | None = None,
    telegram: FakeTelegramClient | None = None,
) -> Bot:
    return Bot(
        dynamo,
        queue or FakeJobQueue(),
        media,
        lambda token: telegram or FakeTelegramClient(),
        _Policy(),
        load_config(dict(ENV)),
        now=lambda: NOW,
        logger=_Logger(),
    )


def _event(text: str, update_id: int = 812345678) -> dict:
    return {
        "version": "2.0",
        "rawPath": "/telegram/webhook",
        "headers": {"x-telegram-bot-api-secret-token": SECRET},
        "requestContext": {"http": {"method": "POST", "path": "/telegram/webhook"}},
        "body": json.dumps(
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


def _seed(
    dynamo: FakeDynamoRepository,
    media: FakeMediaStore,
    *,
    index: int = 1,
    tier: str = "high",
    owner: int = USER_ID,
    video_id: str = VIDEO_ID,
    deleted_at: str | None = None,
    seq: int | None = None,
) -> dict[str, Any]:
    job_id = f"m3x9k2q1f{index:04d}"
    job = {
        "job_id": job_id,
        "state": "completed",
        "created_at": f"2026-11-16T09:{index % 60:02d}:00Z",
        "updated_at": "2026-11-16T09:05:00Z",
        "source_host": "www.youtube.com",
        "video_id": video_id,
        "owner_key": keys.owner_job_key(owner),
        "chat_id": str(owner),
        "update_id": 1,
        "requested_tier": tier,
        "seq": index if seq is None else seq,
        "media_kind": "video",
        "produced_quality": "720p",
        "video_title": f"Stored video {index}",
        "media_key": keys.media_key(job_id, video_id),
        "media_size_bytes": 1_572_864,
        "media_created_at": NOW_EPOCH - DAY,
        "media_expires_at": NOW_EPOCH + 3 * DAY,
    }
    if deleted_at is not None:
        job["deleted_at"] = deleted_at
    dynamo.items["JOB#" + job_id] = dict(job)
    media.seed(job["media_key"], b"x" * 16)
    return job


def _is_deleted(dynamo: FakeDynamoRepository, job: dict[str, Any]) -> bool:
    return dynamo.items["JOB#" + job["job_id"]].get("deleted_at") is not None


# --- /list -------------------------------------------------------------------


def test_list_shows_one_line_per_live_item_of_the_caller() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    _seed(dynamo, media, index=1, tier="high")
    _seed(dynamo, media, index=2, tier="medium")
    _seed(dynamo, media, index=3, owner=999999999)

    _bot(dynamo, media, telegram=telegram).handle(_event("/list"))

    text = telegram.messages[0]["text"]
    lines = text.split("\n\n")
    assert len(lines) == 2
    assert any("Stored video 1" in line for line in lines)
    assert any("Stored video 2" in line for line in lines)
    assert "Stored video 3" not in text


def test_list_answers_an_empty_library() -> None:
    telegram = FakeTelegramClient()

    _bot(FakeDynamoRepository(), FakeMediaStore(), telegram=telegram).handle(_event("/list"))

    assert telegram.messages == [{"chat_id": CHAT_ID, "text": messages.NO_VIDEOS}]


def test_list_reads_only_the_owner_prefix() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()

    _bot(dynamo, media).handle(_event("/list"))

    query = next(call for call in dynamo.calls if call[0] == "query_owner_jobs")
    assert query[1]["owner_key"] == keys.owner_job_key(USER_ID)


# --- /delete -----------------------------------------------------------------


def test_delete_removes_the_object_and_marks_the_item() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    job = _seed(dynamo, media)
    name = keys.display_name(job)

    _bot(dynamo, media, telegram=telegram).handle(_event(f"/delete {name}"))

    assert telegram.messages == [{"chat_id": CHAT_ID, "text": f"Deleted {name}."}]
    assert job["media_key"] not in media.objects
    assert _is_deleted(dynamo, job)


def test_delete_of_an_unknown_name_writes_nothing() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    job = _seed(dynamo, media)

    _bot(dynamo, media, telegram=telegram).handle(_event("/delete dQw4w9WgXcQ-high-zzzz.mp4"))

    assert telegram.messages == [
        {"chat_id": CHAT_ID, "text": "No item is named dQw4w9WgXcQ-high-zzzz.mp4."}
    ]
    assert job["media_key"] in media.objects
    assert not _is_deleted(dynamo, job)
    assert [call[0] for call in media.writes] == []


def test_delete_cannot_reach_another_accounts_item() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    other = _seed(dynamo, media, owner=999999999)
    name = keys.display_name(other)

    _bot(dynamo, media, telegram=telegram).handle(_event(f"/delete {name}"))

    assert telegram.messages == [{"chat_id": CHAT_ID, "text": f"No item is named {name}."}]
    assert other["media_key"] in media.objects
    assert not _is_deleted(dynamo, other)


def test_delete_without_a_name_deletes_nothing() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    job = _seed(dynamo, media)

    _bot(dynamo, media, telegram=telegram).handle(_event("/delete"))

    assert telegram.messages == [{"chat_id": CHAT_ID, "text": messages.FORMAT_HINT}]
    assert job["media_key"] in media.objects
    assert not _is_deleted(dynamo, job)


def test_list_after_a_delete_no_longer_shows_the_item() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    job = _seed(dynamo, media)
    bot = _bot(dynamo, media, telegram=telegram)

    bot.handle(_event(f"/delete {keys.display_name(job)}", update_id=1))
    bot.handle(_event("/list", update_id=2))

    assert telegram.messages[-1]["text"] == messages.NO_VIDEOS


def test_a_request_repeating_a_deleted_item_creates_a_new_job() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    queue = FakeJobQueue()
    job = _seed(dynamo, media, tier="high")
    bot = _bot(dynamo, media, queue=queue)

    bot.handle(_event(f"/delete {keys.display_name(job)}", update_id=1))
    bot.handle(_event(f"https://www.youtube.com/watch?v={VIDEO_ID} high", update_id=2))

    assert len(queue.messages) == 1
    assert len([key for key in dynamo.items if key.startswith("JOB#")]) == 2


# --- /empty ------------------------------------------------------------------


def test_empty_alone_deletes_nothing() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    first = _seed(dynamo, media, index=1)
    second = _seed(dynamo, media, index=2, tier="medium")

    _bot(dynamo, media, telegram=telegram).handle(_event("/empty"))

    assert telegram.messages == [
        {
            "chat_id": CHAT_ID,
            "text": 'This will delete 2 items. Reply "/empty confirm" to proceed.',
        }
    ]
    assert not _is_deleted(dynamo, first)
    assert not _is_deleted(dynamo, second)
    assert [call[0] for call in media.writes] == []


@pytest.mark.parametrize("argument", ["yes", "confirm please", "Confirm!"])
def test_any_other_argument_is_treated_as_a_bare_empty(argument: str) -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    job = _seed(dynamo, media)

    _bot(dynamo, media, telegram=telegram).handle(_event(f"/empty {argument}"))

    assert "This will delete 1 items." in telegram.messages[0]["text"]
    assert not _is_deleted(dynamo, job)


def test_empty_confirm_deletes_every_live_item_and_reports_the_count() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    first = _seed(dynamo, media, index=1)
    second = _seed(dynamo, media, index=2, tier="medium")

    _bot(dynamo, media, telegram=telegram).handle(_event("/empty confirm"))

    assert telegram.messages == [{"chat_id": CHAT_ID, "text": "Deleted 2 items."}]
    assert _is_deleted(dynamo, first)
    assert _is_deleted(dynamo, second)
    assert media.objects == {}


def test_empty_confirm_stops_at_two_hundred_and_states_the_remainder() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    for index in range(205):
        _seed(dynamo, media, index=index)

    _bot(dynamo, media, telegram=telegram).handle(_event("/empty confirm"))

    assert telegram.messages == [
        {
            "chat_id": CHAT_ID,
            "text": 'Deleted 200 items; 5 remain, reply "/empty confirm" again.',
        }
    ]


def test_empty_confirm_with_nothing_live_says_so() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    _seed(dynamo, media, deleted_at="2026-11-16T09:30:00Z")

    _bot(dynamo, media, telegram=telegram).handle(_event("/empty confirm"))

    assert telegram.messages == [{"chat_id": CHAT_ID, "text": messages.EMPTY_NOTHING}]


# --- /link -------------------------------------------------------------------


def test_link_signs_a_fresh_url_for_the_numbered_item_without_downloading() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram, queue = FakeTelegramClient(), FakeJobQueue()
    job = _seed(dynamo, media, index=1, tier="medium")

    _bot(dynamo, media, queue=queue, telegram=telegram).handle(_event("/link 1"))

    text = telegram.messages[0]["text"]
    assert text.startswith(f"Link for 1: {keys.display_name(job)}\n")
    assert "Quality: 720p" in text
    assert job["media_key"] in text
    assert queue.writes == []
    assert media.writes == []


def test_link_of_an_unknown_number_issues_nothing() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram, queue = FakeTelegramClient(), FakeJobQueue()
    _seed(dynamo, media, index=1)

    _bot(dynamo, media, queue=queue, telegram=telegram).handle(_event("/link 99"))

    assert telegram.messages == [{"chat_id": CHAT_ID, "text": "No item is numbered 99."}]
    assert [call for call in media.calls if call[0] == "presign_get"] == []
    assert queue.writes == []


@pytest.mark.parametrize("text", ["/link", "/link abc", "/link 1 2"])
def test_link_without_one_number_states_the_usage(text: str) -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    _seed(dynamo, media, index=1)

    _bot(dynamo, media, telegram=telegram).handle(_event(text))

    assert telegram.messages == [{"chat_id": CHAT_ID, "text": messages.LINK_USAGE}]


def test_link_cannot_reach_another_accounts_item() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    _seed(dynamo, media, owner=999999999, seq=1)

    _bot(dynamo, media, telegram=telegram).handle(_event("/link 1"))

    assert telegram.messages == [{"chat_id": CHAT_ID, "text": "No item is numbered 1."}]


def test_link_reports_expiry_when_the_object_is_already_gone() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    job = _seed(dynamo, media, index=1)
    media.objects.pop(job["media_key"])

    _bot(dynamo, media, telegram=telegram).handle(_event("/link 1"))

    assert telegram.messages == [{"chat_id": CHAT_ID, "text": messages.EXPIRED}]
    assert [call for call in media.calls if call[0] == "presign_get"] == []


# --- interaction lines -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/list", "/list"),
        ("/link 1", "/link 1"),
        ("/delete name.mp4", "/delete name.mp4"),
        ("/empty", "/empty"),
        ("/empty confirm", "/empty confirm"),
    ],
)
def test_a_command_writes_one_interaction_line(text: str, expected: str) -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()

    _bot(dynamo, media).handle(_event(text))

    (interaction,) = [item for key, item in dynamo.items.items() if key.startswith("UPD#")]
    assert interaction["text"] == expected
    assert interaction["outcome"].startswith("command:")


# --- numbers -----------------------------------------------------------------


def test_list_prints_each_item_number_newest_first() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    _seed(dynamo, media, index=1, tier="medium")
    _seed(dynamo, media, index=2, tier="high")

    _bot(dynamo, media, telegram=telegram).handle(_event("/list"))

    lines = telegram.messages[0]["text"].split("\n\n")
    assert lines[0].startswith("2. ")
    assert lines[1].startswith("1. ")


def test_delete_accepts_the_number_the_list_printed() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    first = _seed(dynamo, media, index=1, tier="medium")
    second = _seed(dynamo, media, index=2, tier="high")
    bot = _bot(dynamo, media, telegram=telegram)

    bot.handle(_event("/list", update_id=1))
    bot.handle(_event("/delete 2", update_id=2))

    assert telegram.messages[-1]["text"] == (f"Deleted 2: {keys.display_name(second)}.")
    assert _is_deleted(dynamo, second)
    assert not _is_deleted(dynamo, first)
    assert second["media_key"] not in media.objects
    assert first["media_key"] in media.objects


def test_delete_by_unknown_number_deletes_nothing() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    job = _seed(dynamo, media, index=1)

    _bot(dynamo, media, telegram=telegram).handle(_event("/delete 99"))

    assert telegram.messages == [{"chat_id": CHAT_ID, "text": "No item is numbered 99."}]
    assert not _is_deleted(dynamo, job)
    assert job["media_key"] in media.objects
    assert [call[0] for call in media.writes] == []


def test_delete_by_number_cannot_reach_another_account() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    other = _seed(dynamo, media, owner=999999999, seq=1)

    _bot(dynamo, media, telegram=telegram).handle(_event("/delete 1"))

    assert telegram.messages == [{"chat_id": CHAT_ID, "text": "No item is numbered 1."}]
    assert not _is_deleted(dynamo, other)


def test_the_name_still_deletes_the_same_item() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    job = _seed(dynamo, media, index=1)

    _bot(dynamo, media, telegram=telegram).handle(_event(f"/delete {keys.display_name(job)}"))

    assert telegram.messages == [{"chat_id": CHAT_ID, "text": f"Deleted {keys.display_name(job)}."}]
    assert _is_deleted(dynamo, job)


def test_a_number_still_names_the_same_item_after_a_new_download() -> None:
    dynamo, media = FakeDynamoRepository(), FakeMediaStore()
    telegram = FakeTelegramClient()
    first = _seed(dynamo, media, index=1, tier="medium")
    bot = _bot(dynamo, media, telegram=telegram)

    bot.handle(_event("/list", update_id=1))
    bot.handle(_event(f"https://www.youtube.com/watch?v={VIDEO_ID} high", update_id=2))
    bot.handle(_event("/delete 1", update_id=3))

    assert telegram.messages[-1]["text"] == f"Deleted 1: {keys.display_name(first)}."
    assert _is_deleted(dynamo, first)
