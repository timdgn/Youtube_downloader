"""Unit tests for the DynamoDB, S3, SQS, and yt-dlp adapters (T023 to T026)."""

from __future__ import annotations

import inspect
import json
import subprocess
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.extractor_fake import FakeExtractor
from tests.fakes.s3_fake import FakeMediaStore
from tests.fakes.sqs_fake import FakeJobQueue
from ytdl import states
from ytdl.adapters.dynamo import DynamoRepository, RateLimited
from ytdl.adapters.extractor import ExtractionError, Extractor
from ytdl.adapters.s3 import MediaStore
from ytdl.adapters.sqs import JobQueue

TABLE = "ytdl-dev-jobs"
BUCKET = "ytdl-dev-media"
QUEUE_URL = "https://sqs.eu-west-1.amazonaws.com/000000000000/ytdl-dev-jobs"
NOW_EPOCH = 1_700_000_800
WINDOW = 1_699_999_200
NOW_ISO = "2023-11-14T22:13:20Z"
STALE_ISO = "2023-11-14T22:11:40Z"
RATE_PK = f"RATE#{WINDOW}#0123456789abcdef"

JOB = {
    "job_id": "m3x9k2q1f7abc",
    "state": "accepted",
    "created_at": NOW_ISO,
    "updated_at": NOW_ISO,
    "source_host": "www.youtube.com",
    "video_id": "dQw4w9WgXcQ",
    "attempt": 1,
}


_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()


def _typed(item: dict) -> dict:
    """Encode a plain item the way the low-level client expects it."""
    return {
        name: _SERIALIZER.serialize(Decimal(str(value)) if isinstance(value, float) else value)
        for name, value in item.items()
    }


def _plain(item: dict) -> dict:
    """Decode an AttributeValue map back to plain Python values."""
    return {name: _DESERIALIZER.deserialize(value) for name, value in item.items()}


def _client_error(code: str, message: str = "boom") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "operation")


def _transaction_cancelled(reasons: list[str]) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": "TransactionCanceledException", "Message": "cancelled"},
            "CancellationReasons": [{"Code": reason} for reason in reasons],
        },
        "TransactWriteItems",
    )


def _repo() -> tuple[DynamoRepository, MagicMock]:
    client = MagicMock()
    return DynamoRepository(client, TABLE), client


# --- create_job_with_rate_increment -----------------------------------------


def test_create_job_writes_one_transaction_with_the_job_and_the_counter() -> None:
    repo, client = _repo()

    repo.create_job_with_rate_increment(JOB, RATE_PK, 10, NOW_EPOCH)

    client.transact_write_items.assert_called_once()
    transact_items = client.transact_write_items.call_args.kwargs["TransactItems"]

    assert len(transact_items) == 2
    put = transact_items[0]["Put"]
    assert put["TableName"] == TABLE
    item = _plain(put["Item"])
    assert item["pk"] == "JOB#m3x9k2q1f7abc"
    assert item["ttl"] == NOW_EPOCH + 2_592_000 + 604_800
    assert item["state"] == "accepted"

    update = transact_items[1]["Update"]
    assert update["TableName"] == TABLE
    assert _plain(update["Key"]) == {"pk": RATE_PK}
    assert "ADD #count :one" in update["UpdateExpression"]
    assert update["ConditionExpression"] == ("attribute_not_exists(#count) OR #count < :limit")
    values = _plain(update["ExpressionAttributeValues"])
    assert values[":one"] == 1
    assert values[":limit"] == 10
    assert values[":ttl"] == WINDOW + 7200


def test_create_job_does_not_write_null_fields() -> None:
    repo, client = _repo()
    job = {**JOB, "requested_quality": None}

    repo.create_job_with_rate_increment(job, RATE_PK, 10, NOW_EPOCH)

    item = client.transact_write_items.call_args.kwargs["TransactItems"][0]["Put"]["Item"]
    assert "requested_quality" not in item


def test_rate_limited_reports_the_end_of_the_window_and_raises() -> None:
    repo, client = _repo()
    client.transact_write_items.side_effect = _transaction_cancelled(
        ["None", "ConditionalCheckFailed"]
    )

    with pytest.raises(RateLimited) as excinfo:
        repo.create_job_with_rate_increment(JOB, RATE_PK, 10, NOW_EPOCH)

    assert excinfo.value.retry_at_epoch == WINDOW + 3600
    client.transact_write_items.assert_called_once()


def test_other_transaction_cancellations_are_reraised() -> None:
    repo, client = _repo()
    client.transact_write_items.side_effect = _transaction_cancelled(
        ["TransactionConflict", "None"]
    )

    with pytest.raises(ClientError):
        repo.create_job_with_rate_increment(JOB, RATE_PK, 10, NOW_EPOCH)


# --- reads and transitions ---------------------------------------------------


def test_get_job_returns_the_item_without_the_partition_key() -> None:
    repo, client = _repo()
    client.get_item.return_value = {"Item": _typed({"pk": "JOB#m3x9k2q1f7abc", **JOB})}

    assert repo.get_job("m3x9k2q1f7abc") == JOB
    assert _plain(client.get_item.call_args.kwargs["Key"]) == {"pk": "JOB#m3x9k2q1f7abc"}


def test_get_job_returns_none_for_an_unknown_id() -> None:
    repo, client = _repo()
    client.get_item.return_value = {}

    assert repo.get_job("unknown") is None


def test_claim_job_uses_the_stale_claim_condition() -> None:
    repo, client = _repo()
    client.update_item.return_value = {"Attributes": _typed({"pk": "JOB#a", "state": "processing"})}

    item = repo.claim_job("a", NOW_EPOCH, NOW_ISO)

    assert item == {"state": "processing"}
    kwargs = client.update_item.call_args.kwargs
    values = _plain(kwargs["ExpressionAttributeValues"])
    assert kwargs["ConditionExpression"] == (
        "#state = :accepted OR (#state = :processing AND #updated_at < :stale) OR "
        "(#state = :failed AND #failure_retryable = :true AND "
        "(attribute_not_exists(#attempt) OR #attempt < :max))"
    )
    assert "if_not_exists(#attempt" in kwargs["UpdateExpression"]
    assert "+ :one" in kwargs["UpdateExpression"]
    assert values[":stale"] == STALE_ISO
    assert values[":accepted"] == "accepted"
    assert values[":processing"] == "processing"
    assert values[":now"] == NOW_ISO
    assert kwargs["ReturnValues"] == "ALL_NEW"


def test_claim_job_carries_the_retry_branch_and_its_values() -> None:
    repo, client = _repo()
    client.update_item.return_value = {"Attributes": _typed({"pk": "JOB#a", "state": "processing"})}

    repo.claim_job("a", NOW_EPOCH, NOW_ISO)

    kwargs = client.update_item.call_args.kwargs
    condition = kwargs["ConditionExpression"]
    values = _plain(kwargs["ExpressionAttributeValues"])
    names = kwargs["ExpressionAttributeNames"]

    assert "#state = :failed AND #failure_retryable = :true" in condition
    assert "attribute_not_exists(#attempt) OR #attempt < :max" in condition
    assert values[":failed"] == "failed"
    assert values[":true"] is True
    assert values[":max"] == 2
    assert names["#failure_retryable"] == "failure_retryable"


def test_claim_job_cannot_claim_a_non_retryable_failure() -> None:
    """The retry branch binds :true, so a stored false can never satisfy it."""
    repo, client = _repo()
    client.update_item.return_value = {"Attributes": _typed({"pk": "JOB#a", "state": "processing"})}

    repo.claim_job("a", NOW_EPOCH, NOW_ISO)

    values = _plain(client.update_item.call_args.kwargs["ExpressionAttributeValues"])
    assert values[":true"] is True
    assert (
        states.can_transition(states.FAILED, states.PROCESSING, retryable=False, attempt=1) is False
    )


def test_claim_job_returns_none_when_the_guard_fails() -> None:
    repo, client = _repo()
    client.update_item.side_effect = _client_error("ConditionalCheckFailedException")

    assert repo.claim_job("a", NOW_EPOCH, NOW_ISO) is None


def _updated_fields(kwargs: dict) -> dict:
    names = kwargs["ExpressionAttributeNames"]
    values = _plain(kwargs["ExpressionAttributeValues"])
    updated = {}
    for name_key, name in names.items():
        value_key = name_key.replace("#f", ":f")
        if value_key in values:
            updated[name] = values[value_key]
    return updated


def test_complete_job_writes_the_media_fields_and_the_completion_ttl() -> None:
    repo, client = _repo()
    fields = {
        "media_key": "output/m3x9k2q1f7abc/dQw4w9WgXcQ.mp4",
        "media_size_bytes": 1234,
        "media_created_at": 1_700_000_900,
        "media_expires_at": 1_700_000_900 + 604_800,
        "produced_height": 720,
    }

    repo.complete_job("m3x9k2q1f7abc", fields, NOW_ISO)

    kwargs = client.update_item.call_args.kwargs
    assert _plain(kwargs["Key"]) == {"pk": "JOB#m3x9k2q1f7abc"}
    assert kwargs["ConditionExpression"] == "#state = :processing"
    assert _plain(kwargs["ExpressionAttributeValues"])[":processing"] == "processing"
    updated = _updated_fields(kwargs)
    assert updated["state"] == "completed"
    assert updated["updated_at"] == NOW_ISO
    assert updated["ttl"] == 1_700_000_900 + 604_800 + 2_592_000
    assert updated["media_size_bytes"] == 1234
    assert updated["produced_height"] == 720


def test_complete_job_is_a_no_op_when_the_guard_fails() -> None:
    repo, client = _repo()
    client.update_item.side_effect = _client_error("ConditionalCheckFailedException")

    assert repo.complete_job("a", {"media_expires_at": 1}, NOW_ISO) is None


def test_fail_job_writes_the_classification() -> None:
    repo, client = _repo()

    repo.fail_job("a", "unsupported_media", "The source refused this video.", False, NOW_ISO)

    kwargs = client.update_item.call_args.kwargs
    assert kwargs["ConditionExpression"] == "#state = :processing"
    assert _updated_fields(kwargs) == {
        "state": "failed",
        "updated_at": NOW_ISO,
        "failure_category": "unsupported_media",
        "failure_message": "The source refused this video.",
        "failure_retryable": False,
    }


def test_fail_job_is_a_no_op_when_the_guard_fails() -> None:
    repo, client = _repo()
    client.update_item.side_effect = _client_error("ConditionalCheckFailedException")

    assert repo.fail_job("a", "internal_error", "boom", True, NOW_ISO) is None


# --- MediaStore --------------------------------------------------------------


def _store() -> tuple[MediaStore, MagicMock]:
    client = MagicMock()
    return MediaStore(client, BUCKET), client


def test_put_file_returns_the_uploaded_byte_count(tmp_path: Path) -> None:
    store, client = _store()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"12345")

    size = store.put_file(
        str(source),
        "output/a/dQw4w9WgXcQ.mp4",
        "video/mp4",
        {"job-id": "a", "produced-height": "720"},
    )

    assert size == 5
    kwargs = client.upload_file.call_args.kwargs
    assert kwargs["Filename"] == str(source)
    assert kwargs["Bucket"] == BUCKET
    assert kwargs["Key"] == "output/a/dQw4w9WgXcQ.mp4"
    assert kwargs["ExtraArgs"] == {
        "ContentType": "video/mp4",
        "Metadata": {"job-id": "a", "produced-height": "720"},
    }


def test_head_object_maps_a_missing_object_to_none() -> None:
    store, client = _store()
    client.head_object.side_effect = _client_error("404")

    assert store.head_object("output/a/b.mp4") is None


def test_head_object_reports_size_etag_and_metadata() -> None:
    store, client = _store()
    client.head_object.return_value = {
        "ContentLength": 42,
        "ETag": '"abc123"',
        "Metadata": {"produced-height": "720"},
    }

    assert store.head_object("output/a/b.mp4") == {
        "size": 42,
        "etag": "abc123",
        "metadata": {"produced-height": "720"},
    }


def test_head_object_defaults_the_metadata_to_an_empty_mapping() -> None:
    store, client = _store()
    client.head_object.return_value = {"ContentLength": 7}

    assert store.head_object("output/a/b.mp4") == {"size": 7, "etag": "", "metadata": {}}


def test_head_object_does_not_swallow_other_errors() -> None:
    store, client = _store()
    client.head_object.side_effect = _client_error("AccessDenied")

    with pytest.raises(ClientError):
        store.head_object("output/a/b.mp4")


def test_presign_get_passes_the_download_contract() -> None:
    store, client = _store()
    client.generate_presigned_url.return_value = "https://example.invalid/signed"

    url = store.presign_get("output/a/dQw4w9WgXcQ.mp4", "dQw4w9WgXcQ-a.mp4", 3600)

    assert url == "https://example.invalid/signed"
    kwargs = client.generate_presigned_url.call_args.kwargs
    assert kwargs["ClientMethod"] == "get_object"
    assert kwargs["ExpiresIn"] == 3600
    params = kwargs["Params"]
    assert params["Bucket"] == BUCKET
    assert params["Key"] == "output/a/dQw4w9WgXcQ.mp4"
    assert params["ResponseContentDisposition"] == 'attachment; filename="dQw4w9WgXcQ-a.mp4"'
    assert params["ResponseContentType"] == "video/mp4"


def test_get_text_reads_the_cookies_file() -> None:
    store, client = _store()
    body = MagicMock()
    body.read.return_value = b"# Netscape HTTP Cookie File\n"
    client.get_object.return_value = {"Body": body}

    assert store.get_text("config/cookies.txt") == "# Netscape HTTP Cookie File\n"
    assert client.get_object.call_args.kwargs == {
        "Bucket": BUCKET,
        "Key": "config/cookies.txt",
    }


# --- JobQueue ----------------------------------------------------------------


def test_enqueue_sends_the_contract_body_without_attributes() -> None:
    client = MagicMock()
    queue = JobQueue(client, QUEUE_URL)
    body = {
        "version": 1,
        "job_id": "m3x9k2q1f7abc",
        "video_id": "dQw4w9WgXcQ",
        "source_host": "www.youtube.com",
        "requested_height": 720,
        "enqueued_at": NOW_ISO,
    }

    queue.enqueue(body)

    client.send_message.assert_called_once()
    kwargs = client.send_message.call_args.kwargs
    assert set(kwargs) == {"QueueUrl", "MessageBody"}
    assert kwargs["QueueUrl"] == QUEUE_URL
    assert json.loads(kwargs["MessageBody"]) == body


# --- Extractor ---------------------------------------------------------------


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def test_probe_runs_ytdlp_without_a_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = {"id": "dQw4w9WgXcQ", "duration": 212, "formats": []}
    runner = MagicMock(return_value=_completed(json.dumps(metadata)))
    monkeypatch.setattr("ytdl.adapters.extractor.subprocess.run", runner)
    extractor = Extractor(timeout_seconds=780)

    assert extractor.probe("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == metadata

    args, kwargs = runner.call_args
    command = args[0]
    assert command[0] == "yt-dlp"
    assert "--dump-single-json" in command
    assert "--no-playlist" in command
    assert "--no-warnings" in command
    assert command[-1] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert kwargs["timeout"] == 780
    assert kwargs["shell"] is False
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_probe_raises_with_the_captured_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = MagicMock(return_value=_completed(stderr="ERROR: Private video", returncode=1))
    monkeypatch.setattr("ytdl.adapters.extractor.subprocess.run", runner)

    with pytest.raises(ExtractionError) as excinfo:
        Extractor().probe("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert excinfo.value.returncode == 1
    assert "Private video" in excinfo.value.stderr


def test_probe_maps_a_timeout_to_an_extraction_error(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = MagicMock(side_effect=subprocess.TimeoutExpired(cmd=["yt-dlp"], timeout=3))
    monkeypatch.setattr("ytdl.adapters.extractor.subprocess.run", runner)

    with pytest.raises(ExtractionError) as excinfo:
        Extractor(timeout_seconds=3).probe("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert excinfo.value.returncode is None
    assert excinfo.value.stderr == "timeout"


def test_download_writes_source_mp4_and_returns_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(list(command))
        output = command[command.index("-o") + 1].replace("%(ext)s", "mp4")
        Path(output).write_bytes(b"media-bytes")
        return _completed()

    monkeypatch.setattr("ytdl.adapters.extractor.subprocess.run", fake_run)
    extractor = Extractor(timeout_seconds=780)

    produced = extractor.download(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "bv*[height<=720]+ba",
        str(tmp_path),
        extra_args=("--proxy", "http://127.0.0.1:8080"),
    )

    assert produced == str(tmp_path / "source.mp4")
    assert Path(produced).read_bytes() == b"media-bytes"
    (command,) = commands
    assert command[0] == "yt-dlp"
    assert "--no-playlist" in command
    assert "--merge-output-format" in command
    assert command[command.index("--merge-output-format") + 1] == "mp4"
    assert command[command.index("-o") + 1] == f"{tmp_path}/source.%(ext)s"
    assert command[command.index("-f") + 1] == "bv*[height<=720]+ba"
    assert "--proxy" in command
    assert command[-1] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_download_passes_the_timeout_and_never_uses_a_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict = {}

    def fake_run(command, **kwargs):
        observed.update(kwargs)
        output = command[command.index("-o") + 1].replace("%(ext)s", "mp4")
        Path(output).write_bytes(b"x")
        return _completed()

    monkeypatch.setattr("ytdl.adapters.extractor.subprocess.run", fake_run)

    Extractor(timeout_seconds=42).download(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "bv*+ba", str(tmp_path)
    )

    assert observed["timeout"] == 42
    assert observed["shell"] is False


def test_download_raises_on_a_non_zero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = MagicMock(return_value=_completed(stderr="ERROR: unavailable", returncode=2))
    monkeypatch.setattr("ytdl.adapters.extractor.subprocess.run", runner)

    with pytest.raises(ExtractionError) as excinfo:
        Extractor().download("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "bv*+ba", str(tmp_path))

    assert excinfo.value.returncode == 2
    assert "unavailable" in excinfo.value.stderr


def test_download_raises_when_ytdlp_produced_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "ytdl.adapters.extractor.subprocess.run", MagicMock(return_value=_completed())
    )

    with pytest.raises(ExtractionError):
        Extractor().download("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "bv*+ba", str(tmp_path))


# --- fake fidelity -----------------------------------------------------------


@pytest.mark.parametrize(
    ("real_class", "fake_class"),
    [
        (DynamoRepository, FakeDynamoRepository),
        (MediaStore, FakeMediaStore),
        (JobQueue, FakeJobQueue),
        (Extractor, FakeExtractor),
    ],
)
def test_fakes_expose_the_same_public_methods(real_class: type, fake_class: type) -> None:
    methods = [
        name
        for name, attribute in vars(real_class).items()
        if not name.startswith("_") and callable(attribute)
    ]
    assert methods

    for name in methods:
        real_parameters = inspect.signature(getattr(real_class, name)).parameters
        fake_parameters = inspect.signature(getattr(fake_class, name)).parameters
        assert list(real_parameters) == list(fake_parameters), name
        assert [parameter.kind for parameter in real_parameters.values()] == [
            parameter.kind for parameter in fake_parameters.values()
        ], name


def test_fake_extractor_returns_metadata_and_writes_a_file(tmp_path: Path) -> None:
    extractor = FakeExtractor()

    metadata = extractor.probe("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    produced = extractor.download(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "bv*+ba", str(tmp_path)
    )

    assert metadata["id"] == "dQw4w9WgXcQ"
    assert Path(produced).stat().st_size > 0
    assert [call[0] for call in extractor.calls] == ["probe", "download"]


def test_fakes_expose_a_writes_view() -> None:
    repo = FakeDynamoRepository()
    media = FakeMediaStore()
    queue = FakeJobQueue()

    repo.get_job("unknown")
    media.head_object("output/a/b.mp4")
    media.presign_get("output/a/b.mp4", "b.mp4", 3600)

    assert repo.writes == []
    assert media.writes == []
    assert queue.writes == []

    repo.create_job_with_rate_increment(JOB, RATE_PK, 10, NOW_EPOCH)
    assert [call[0] for call in repo.writes] == ["create_job_with_rate_increment"]


# --- the low-level client contract -------------------------------------------


def test_writes_use_attribute_value_maps_not_plain_python() -> None:
    """Regression: boto3.client("dynamodb") rejects plain values with a ParamValidationError."""
    repo, client = _repo()

    repo.create_job_with_rate_increment(JOB, RATE_PK, 10, NOW_EPOCH)

    items = client.transact_write_items.call_args.kwargs["TransactItems"]
    item = items[0]["Put"]["Item"]
    assert item["state"] == {"S": "accepted"}
    assert item["ttl"] == {"N": str(NOW_EPOCH + 2_592_000 + 604_800)}
    for value in item.values():
        assert isinstance(value, dict), value
    assert items[1]["Update"]["Key"] == {"pk": {"S": RATE_PK}}
    assert items[1]["Update"]["ExpressionAttributeValues"][":one"] == {"N": "1"}
    assert items[1]["Update"]["ExpressionAttributeValues"][":limit"] == {"N": "10"}


def test_reads_return_plain_numbers_that_json_can_serialise() -> None:
    """DynamoDB only has N, which botocore decodes as Decimal; JSON cannot encode it."""
    repo, client = _repo()
    client.get_item.return_value = {
        "Item": _typed({"pk": "JOB#x", "media_size_bytes": 1234, "produced_height": 720})
    }

    job = repo.get_job("x")

    assert job == {"media_size_bytes": 1234, "produced_height": 720}
    assert isinstance(job["media_size_bytes"], int)
    json.dumps(job)


def test_fractional_duration_survives_a_round_trip() -> None:
    repo, client = _repo()
    client.get_item.return_value = {"Item": _typed({"pk": "JOB#x", "duration_seconds": 12.5})}

    job = repo.get_job("x")

    assert job["duration_seconds"] == 12.5
    json.dumps(job)


def test_s3_client_pins_the_regional_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """A presigned URL must not mix the global host with a regional signature."""
    import ytdl.adapters.s3 as s3_module

    captured: dict[str, object] = {}

    def fake_client(name: str, **kwargs: object) -> object:
        captured["name"] = name
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(s3_module.boto3, "client", fake_client)

    s3_module.s3_client("eu-west-3")

    assert captured["name"] == "s3"
    assert captured["region_name"] == "eu-west-3"
    assert captured["endpoint_url"] == "https://s3.eu-west-3.amazonaws.com"
