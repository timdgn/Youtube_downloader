"""Line-oriented JSON logging with a fixed field allowlist (T017)."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

ALLOWED_FIELDS = frozenset(
    {
        "job_id",
        "source_host",
        "video_id",
        "requested_quality",
        "produced_quality",
        "outcome",
        "processing_ms",
        "media_size_bytes",
        "attempt",
        "state",
        "failure_category",
        "retryable",
        "duration_ms",
        "queue",
        "message",
    }
)


def get_logger(function: str) -> StructuredLogger:
    """Return a logger that tags every record with the emitting function."""
    return StructuredLogger(function=function, environment=os.environ.get("ENVIRONMENT", ""))


class StructuredLogger:
    """Writes one JSON object per line to stdout, dropping unknown fields."""

    def __init__(self, function: str, environment: str) -> None:
        self._function = function
        self._environment = environment

    def info(self, event: str, **fields: object) -> None:
        self._log("INFO", event, fields)

    def warning(self, event: str, **fields: object) -> None:
        self._log("WARNING", event, fields)

    def error(self, event: str, **fields: object) -> None:
        self._log("ERROR", event, fields)

    def metric(self, name: str, value: float, unit: str, **fields: object) -> None:
        """Emit one CloudWatch Embedded Metric Format record."""
        timestamp = datetime.now(UTC)
        payload: dict[str, object] = {
            "_aws": {
                "Timestamp": int(timestamp.timestamp() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": f"Ytdl/{self._environment}",
                        "Dimensions": [["Environment", "Function"]],
                        "Metrics": [{"Name": name, "Unit": unit}],
                    }
                ],
            },
            "level": "INFO",
            "event": name,
            "function": self._function,
            "environment": self._environment,
            "timestamp": _timestamp(timestamp),
            "Environment": self._environment,
            "Function": self._function,
            name: value,
        }
        payload.update(_allowed(fields))
        _write(payload)

    def _log(self, level: str, event: str, fields: dict[str, object]) -> None:
        payload: dict[str, object] = {
            "level": level,
            "event": event,
            "function": self._function,
            "environment": self._environment,
            "timestamp": _timestamp(datetime.now(UTC)),
        }
        payload.update(_allowed(fields))
        _write(payload)


def _allowed(fields: dict[str, object]) -> dict[str, object]:
    return {name: value for name, value in fields.items() if name in ALLOWED_FIELDS}


def _timestamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":"), default=str) + "\n")
    sys.stdout.flush()
