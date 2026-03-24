"""Tests for bridge.log_format.StructuredJsonFormatter."""

import json
import logging

from bridge.log_format import StructuredJsonFormatter


def test_basic_json_format():
    """Formatter produces valid JSON with required fields."""
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        name="bridge.test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    output = formatter.format(record)
    data = json.loads(output)
    assert data["level"] == "INFO"
    assert data["logger"] == "bridge.test"
    assert data["message"] == "Test message"
    assert "timestamp" in data


def test_extra_fields_included():
    """Extra fields (job_id, session_id, etc.) are included when present."""
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        name="agent.job_queue",
        level=logging.WARNING,
        pathname="job_queue.py",
        lineno=42,
        msg="Job recovered",
        args=(),
        exc_info=None,
    )
    record.job_id = "abc123"
    record.session_id = "sess-456"
    record.correlation_id = "corr-789"
    record.chat_id = "chat-100"
    output = formatter.format(record)
    data = json.loads(output)
    assert data["job_id"] == "abc123"
    assert data["session_id"] == "sess-456"
    assert data["correlation_id"] == "corr-789"
    assert data["chat_id"] == "chat-100"


def test_missing_extra_fields_omitted():
    """Fields not present on the record are omitted from output."""
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.DEBUG,
        pathname="test.py",
        lineno=1,
        msg="Simple log",
        args=(),
        exc_info=None,
    )
    output = formatter.format(record)
    data = json.loads(output)
    assert "job_id" not in data
    assert "session_id" not in data
    assert "correlation_id" not in data


def test_exception_info_included():
    """Exception info is included when present."""
    formatter = StructuredJsonFormatter()
    try:
        raise ValueError("test error")
    except ValueError:
        import sys

        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname="test.py",
        lineno=1,
        msg="Error occurred",
        args=(),
        exc_info=exc_info,
    )
    output = formatter.format(record)
    data = json.loads(output)
    assert "exception" in data
    assert "ValueError" in data["exception"]


def test_output_is_single_line():
    """Each log line is a single JSON line (no embedded newlines in the JSON)."""
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Line1\nLine2\nLine3",
        args=(),
        exc_info=None,
    )
    output = formatter.format(record)
    # json.dumps escapes newlines as \n, so the output should be one line
    assert "\n" not in output
    data = json.loads(output)
    assert "Line1\nLine2\nLine3" == data["message"]
