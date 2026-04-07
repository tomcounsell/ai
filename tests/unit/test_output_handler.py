"""Tests for agent/output_handler.py.

Tests the OutputHandler protocol, FileOutputHandler, and LoggingOutputHandler
implementations.
"""

import asyncio
import tempfile
from pathlib import Path

from agent.output_handler import (
    FileOutputHandler,
    LoggingOutputHandler,
    OutputHandler,
)


class TestOutputHandlerProtocol:
    """Test OutputHandler protocol compliance."""

    def test_file_output_handler_is_output_handler(self):
        """FileOutputHandler must satisfy the OutputHandler protocol."""
        handler = FileOutputHandler()
        assert isinstance(handler, OutputHandler)

    def test_logging_output_handler_is_output_handler(self):
        """LoggingOutputHandler must satisfy the OutputHandler protocol."""
        handler = LoggingOutputHandler()
        assert isinstance(handler, OutputHandler)

    def test_protocol_is_runtime_checkable(self):
        """OutputHandler should be usable with isinstance checks."""

        class BadHandler:
            pass

        assert not isinstance(BadHandler(), OutputHandler)

    def test_custom_handler_satisfies_protocol(self):
        """A custom class with send() and react() should satisfy the protocol."""

        class CustomHandler:
            async def send(self, chat_id, text, reply_to_msg_id, session=None):
                pass

            async def react(self, chat_id, msg_id, emoji=None):
                pass

        assert isinstance(CustomHandler(), OutputHandler)


class TestFileOutputHandler:
    """Test FileOutputHandler writes output to files."""

    def test_creates_log_directory(self):
        """Handler should create the log directory if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "worker_logs"
            FileOutputHandler(log_dir=log_dir)
            assert log_dir.exists()

    def test_send_writes_to_file(self):
        """send() should write text to a session-specific log file."""
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            handler = FileOutputHandler(log_dir=log_dir)

            class FakeSession:
                session_id = "test-session-123"

            asyncio.run(
                handler.send(
                    chat_id="chat-1",
                    text="Hello from worker",
                    reply_to_msg_id=42,
                    session=FakeSession(),
                )
            )

            log_file = log_dir / "test-session-123.log"
            assert log_file.exists()
            content = log_file.read_text()
            assert "Hello from worker" in content
            assert "chat=chat-1" in content
            assert "reply_to=42" in content

    def test_send_empty_text_noop(self):
        """send() with empty text should not create a file."""
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            handler = FileOutputHandler(log_dir=log_dir)

            asyncio.run(
                handler.send(
                    chat_id="chat-1",
                    text="",
                    reply_to_msg_id=42,
                )
            )

            # No files should have been created (except the dir itself)
            assert list(log_dir.glob("*.log")) == []

    def test_send_falls_back_to_chat_id(self):
        """When session has no session_id, use chat_id as filename."""
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            handler = FileOutputHandler(log_dir=log_dir)

            asyncio.run(
                handler.send(
                    chat_id="fallback-chat",
                    text="Test output",
                    reply_to_msg_id=1,
                )
            )

            log_file = log_dir / "fallback-chat.log"
            assert log_file.exists()

    def test_send_appends_multiple(self):
        """Multiple send() calls should append to the same file."""
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            handler = FileOutputHandler(log_dir=log_dir)

            class FakeSession:
                session_id = "multi-test"

            for msg in ["First", "Second", "Third"]:
                asyncio.run(handler.send("chat-1", msg, 1, FakeSession()))

            log_file = log_dir / "multi-test.log"
            content = log_file.read_text()
            assert "First" in content
            assert "Second" in content
            assert "Third" in content

    def test_send_includes_timestamp(self):
        """Output should include a human-readable timestamp."""
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            handler = FileOutputHandler(log_dir=log_dir)

            asyncio.run(handler.send("chat-1", "timestamped output", 1))

            log_file = log_dir / "chat-1.log"
            content = log_file.read_text()
            # Should contain a UTC timestamp in YYYY-MM-DD HH:MM:SS format
            import re

            assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", content)

    def test_react_writes_to_file(self):
        """react() should log the reaction to a file."""
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            handler = FileOutputHandler(log_dir=log_dir)

            asyncio.run(handler.react("chat-1", 42, "\U0001f44d"))

            log_file = log_dir / "chat-1.log"
            assert log_file.exists()
            content = log_file.read_text()
            assert "REACTION" in content


class TestLoggingOutputHandler:
    """Test LoggingOutputHandler logs via Python logging."""

    def test_send_does_not_raise(self):
        """send() should not raise exceptions."""
        handler = LoggingOutputHandler()
        asyncio.run(handler.send("chat-1", "test message", 1))

    def test_send_empty_noop(self):
        """send() with empty text should be a no-op."""
        handler = LoggingOutputHandler()
        asyncio.run(handler.send("chat-1", "", 1))

    def test_react_does_not_raise(self):
        """react() should not raise exceptions."""
        handler = LoggingOutputHandler()
        asyncio.run(handler.react("chat-1", 42, "\U0001f44d"))
