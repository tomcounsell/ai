"""Structured JSON log formatter for the bridge.

Provides a JSON formatter that includes correlation_id, agent_session_id, session_id,
and chat_id fields on every log line for observability.

Usage:
    from bridge.log_format import StructuredJsonFormatter
    handler.setFormatter(StructuredJsonFormatter())

The formatter outputs one JSON object per line, making logs parseable by
standard log aggregation tools while remaining greppable.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any


class StructuredJsonFormatter(logging.Formatter):
    """JSON log formatter with structured fields.

    Outputs one JSON object per line with fields:
    - timestamp: ISO 8601 UTC timestamp with Z suffix
    - utc: always True (explicit marker for log consumers)
    - level: log level name
    - logger: logger name
    - function: function name
    - message: formatted message
    - agent_session_id: if present in LogRecord extras
    - session_id: if present in LogRecord extras
    - correlation_id: if present in LogRecord extras
    - chat_id: if present in LogRecord extras
    """

    EXTRA_FIELDS = ("agent_session_id", "session_id", "correlation_id", "chat_id")

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        """Format time as UTC ISO 8601 with Z suffix."""
        ct = datetime.fromtimestamp(record.created, tz=UTC)
        if datefmt:
            return ct.strftime(datefmt)
        return ct.isoformat().replace("+00:00", "Z")

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "utc": True,
            "level": record.levelname,
            "logger": record.name,
            "function": record.funcName,
            "message": record.getMessage(),
        }

        # Include structured fields if present
        for field in self.EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                data[field] = str(value)

        # Include exception info if present
        if record.exc_info and record.exc_info[1]:
            data["exception"] = self.formatException(record.exc_info)

        return json.dumps(data, default=str)
