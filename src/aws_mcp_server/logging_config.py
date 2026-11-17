"""Configure safe, structured, timestamped logging for the AWS MCP server."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime


class JsonLogFormatter(logging.Formatter):
    """Format log records as one-line JSON without serializing secret payloads."""

    def format(self, record: logging.LogRecord) -> str:
        """Return a structured record with timestamp, module, and safe context."""

        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }
        for field in ("operation", "resource", "context"):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging() -> None:
    """Configure one structured stderr handler without writing to MCP stdout."""

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonLogFormatter())
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
