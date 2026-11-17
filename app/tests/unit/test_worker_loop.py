"""Unit tests for the SQS polling loop: delete, redeliver, survive."""

from __future__ import annotations

import threading
from typing import Any

from ytdl.server import worker_loop

QUEUE_URL = "https://sqs.eu-west-3.amazonaws.com/000000000000/ytdl-prod-jobs"
MESSAGE_ID = "7f1e6a3c-0000-4000-8000-000000000001"
RECEIPT = "receipt-handle-1"


class FakeQueue:
    """Returns the queued batches in order, then stops the loop on the next poll."""

    def __init__(self, batches: list[list[dict[str, Any]]], stop: threading.Event) -> None:
        self._batches = batches
        self._stop = stop
        self.deleted: list[str] = []
        self.polls = 0

    def receive_message(self, **_: Any) -> dict[str, Any]:
        self.polls += 1
        if self._batches:
            messages = self._batches.pop(0)
            self._stop.set()
            return {"Messages": messages}
        self._stop.set()
        return {}

    def delete_message(self, **kwargs: Any) -> None:
        self.deleted.append(kwargs["ReceiptHandle"])


class FakeWorker:
    def __init__(
        self, result: dict[str, Any] | None = None, error: Exception | None = None
    ) -> None:
        self.result = result if result is not None else {"batchItemFailures": []}
        self.error = error
        self.events: list[dict[str, Any]] = []

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        self.events.append(event)
        if self.error is not None:
            raise self.error
        return self.result


class QuietLogger:
    def info(self, *args: Any, **kwargs: Any) -> None: ...

    def warning(self, *args: Any, **kwargs: Any) -> None: ...

    def error(self, *args: Any, **kwargs: Any) -> None: ...


def message(receipt: str | None = RECEIPT) -> dict[str, Any]:
    return {"MessageId": MESSAGE_ID, "Body": '{"version":1}', "ReceiptHandle": receipt}


def run(worker: FakeWorker, queue: FakeQueue) -> None:
    worker_loop.run(worker, queue, QUEUE_URL, threading.Event(), QuietLogger())


def test_a_successful_batch_is_deleted() -> None:
    stop = threading.Event()
    queue = FakeQueue([[message()]], stop)
    worker = FakeWorker()

    worker_loop.run(worker, queue, QUEUE_URL, stop, QuietLogger())

    assert queue.deleted == [RECEIPT]
    record = worker.events[0]["Records"][0]
    assert record["messageId"] == MESSAGE_ID
    assert record["body"] == '{"version":1}'


def test_a_reported_failure_is_left_for_redelivery() -> None:
    stop = threading.Event()
    queue = FakeQueue([[message()]], stop)
    worker = FakeWorker({"batchItemFailures": [{"itemIdentifier": MESSAGE_ID}]})

    worker_loop.run(worker, queue, QUEUE_URL, stop, QuietLogger())

    assert queue.deleted == []


def test_an_exception_leaves_the_message_and_keeps_the_process_alive() -> None:
    stop = threading.Event()
    queue = FakeQueue([[message()]], stop)
    worker = FakeWorker(error=RuntimeError("adapter exploded"))

    worker_loop.run(worker, queue, QUEUE_URL, stop, QuietLogger())

    assert queue.deleted == []


def test_a_message_without_a_receipt_is_not_deleted() -> None:
    stop = threading.Event()
    queue = FakeQueue([[message(receipt=None)]], stop)

    worker_loop.run(FakeWorker(), queue, QUEUE_URL, stop, QuietLogger())

    assert queue.deleted == []


def test_an_empty_poll_exits_once_stopped() -> None:
    stop = threading.Event()
    queue = FakeQueue([], stop)

    worker_loop.run(FakeWorker(), queue, QUEUE_URL, stop, QuietLogger())

    assert queue.polls == 1
    assert queue.deleted == []
