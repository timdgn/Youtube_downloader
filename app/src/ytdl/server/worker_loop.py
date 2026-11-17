"""SQS consumer for the Docker deployment.

Lambda's event source mapping did four things: receive one message, call the
worker, delete the message when the batch item succeeded, and leave a failed
one for redelivery. This loop does the same, one message at a time, so the
retry, visibility and dead-letter behaviour stay those of the queue.
"""

from __future__ import annotations

import signal
import threading
from typing import Any

import boto3

from ytdl.config import load_config
from ytdl.logging import StructuredLogger, get_logger
from ytdl.worker.handler import build_worker

WAIT_SECONDS = 20
ERROR_SLEEP_SECONDS = 5


def run(
    worker: Any,
    client: Any,
    queue_url: str,
    stop: threading.Event,
    logger: StructuredLogger | None = None,
) -> None:
    """Poll until ``stop`` is set, finishing any job already running."""
    log = logger if logger is not None else get_logger("worker-loop")
    while not stop.is_set():
        try:
            response = client.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=WAIT_SECONDS,
            )
        except Exception as error:
            # A network or credential fault must not end the process: the queue
            # keeps the message and the next poll tries again.
            log.error("receive_failed", message=type(error).__name__)
            stop.wait(ERROR_SLEEP_SECONDS)
            continue

        for message in response.get("Messages") or []:
            _handle(worker, client, queue_url, message, log)


def _handle(
    worker: Any,
    client: Any,
    queue_url: str,
    message: dict[str, Any],
    logger: StructuredLogger,
) -> None:
    message_id = str(message.get("MessageId", ""))
    receipt = message.get("ReceiptHandle")
    record = {"messageId": message_id, "body": message.get("Body", ""), "receiptHandle": receipt}

    try:
        result = worker.handle({"Records": [record]})
    except Exception as error:
        # The worker records its own failures; anything escaping means no
        # outcome was written, so the message must stay for a redelivery.
        logger.error("worker_failed", job_message_id=message_id, message=type(error).__name__)
        return

    failed = {
        str(item.get("itemIdentifier"))
        for item in (result.get("batchItemFailures") or [])
        if isinstance(item, dict)
    }
    if message_id in failed:
        logger.warning("job_redelivery", job_message_id=message_id)
        return
    if not receipt:
        logger.warning("message_without_receipt", job_message_id=message_id)
        return
    client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
    logger.info("job_message_deleted", job_message_id=message_id)


def main() -> None:
    config = load_config()
    logger = get_logger("worker-loop")
    stop = threading.Event()

    def request_stop(_signum: int, _frame: Any) -> None:
        stop.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, request_stop)

    logger.info("polling", queue_url=config.queue_url, environment=config.environment)
    run(build_worker(), boto3.client("sqs"), config.queue_url, stop, logger)
    logger.info("stopped")


if __name__ == "__main__":
    main()
