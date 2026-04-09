"""Tests for agent/output_handler.py.

Tests the OutputHandler protocol, FileOutputHandler, LoggingOutputHandler,
and TelegramRelayOutputHandler implementations.
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from agent.output_handler import (
    FileOutputHandler,
    LoggingOutputHandler,
    OutputHandler,
    TelegramRelayOutputHandler,
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

    def test_telegram_relay_handler_is_output_handler(self):
        """TelegramRelayOutputHandler must satisfy the OutputHandler protocol."""
        handler = TelegramRelayOutputHandler.__new__(TelegramRelayOutputHandler)
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


class TestTelegramRelayOutputHandler:
    """Test TelegramRelayOutputHandler writes to Redis outbox."""

    def _make_handler(self, mock_redis=None, file_handler=None):
        """Create a handler with a mocked Redis connection."""
        handler = TelegramRelayOutputHandler(
            redis_url="redis://localhost:6379/0",
            file_handler=file_handler,
        )
        if mock_redis is not None:
            handler._redis = mock_redis
        return handler

    def _mock_redis(self):
        """Return a MagicMock that behaves like a Redis client."""
        r = MagicMock()
        r.rpush = MagicMock()
        r.expire = MagicMock()
        return r

    def test_send_writes_correct_payload(self):
        """send() should rpush a JSON payload matching tools/send_telegram.py format."""
        mock_r = self._mock_redis()
        handler = self._make_handler(mock_redis=mock_r)

        class FakeSession:
            session_id = "sess-abc"

        asyncio.run(
            handler.send(
                chat_id="12345",
                text="Hello world",
                reply_to_msg_id=99,
                session=FakeSession(),
            )
        )

        # Verify rpush was called with the correct key
        mock_r.rpush.assert_called_once()
        call_args = mock_r.rpush.call_args
        assert call_args[0][0] == "telegram:outbox:sess-abc"

        # Verify payload structure matches tools/send_telegram.py
        payload = json.loads(call_args[0][1])
        assert payload["chat_id"] == "12345"
        assert payload["reply_to"] == 99
        assert payload["text"] == "Hello world"
        assert payload["session_id"] == "sess-abc"
        assert "timestamp" in payload
        assert isinstance(payload["timestamp"], float)

        # Verify TTL was set
        mock_r.expire.assert_called_once_with("telegram:outbox:sess-abc", 3600)

    def test_send_empty_text_noop(self):
        """send() with empty text should not write to Redis."""
        mock_r = self._mock_redis()
        handler = self._make_handler(mock_redis=mock_r)

        asyncio.run(handler.send("12345", "", 1))

        mock_r.rpush.assert_not_called()

    def test_send_extracts_session_id_from_session(self):
        """send() should use session.session_id for the outbox key."""
        mock_r = self._mock_redis()
        handler = self._make_handler(mock_redis=mock_r)

        class FakeSession:
            session_id = "my-session"

        asyncio.run(handler.send("chat-1", "msg", 1, FakeSession()))

        key = mock_r.rpush.call_args[0][0]
        assert key == "telegram:outbox:my-session"

    def test_send_falls_back_to_chat_id(self):
        """When session is None, use chat_id as session_id."""
        mock_r = self._mock_redis()
        handler = self._make_handler(mock_redis=mock_r)

        asyncio.run(handler.send("fallback-chat", "msg", 1, session=None))

        key = mock_r.rpush.call_args[0][0]
        assert key == "telegram:outbox:fallback-chat"
        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["session_id"] == "fallback-chat"

    def test_send_reply_to_none(self):
        """send() with reply_to_msg_id=None should set reply_to to None."""
        mock_r = self._mock_redis()
        handler = self._make_handler(mock_redis=mock_r)

        asyncio.run(handler.send("chat-1", "msg", None))

        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["reply_to"] is None

    def test_react_writes_reaction_payload(self):
        """react() should write a payload with type='reaction'."""
        mock_r = self._mock_redis()
        handler = self._make_handler(mock_redis=mock_r)

        asyncio.run(handler.react("chat-1", 42, "\U0001f44d"))

        mock_r.rpush.assert_called_once()
        key = mock_r.rpush.call_args[0][0]
        assert key == "telegram:outbox:chat-1"

        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["type"] == "reaction"
        assert payload["chat_id"] == "chat-1"
        assert payload["reply_to"] == 42
        assert payload["emoji"] == "\U0001f44d"
        assert "timestamp" in payload

    def test_redis_failure_does_not_propagate(self):
        """Redis errors should be caught and logged, never raised."""
        mock_r = self._mock_redis()
        mock_r.rpush.side_effect = ConnectionError("Redis down")
        handler = self._make_handler(mock_redis=mock_r)

        # Should not raise
        asyncio.run(handler.send("chat-1", "msg", 1))

    def test_redis_failure_on_react_does_not_propagate(self):
        """Redis errors in react() should be caught and logged."""
        mock_r = self._mock_redis()
        mock_r.rpush.side_effect = ConnectionError("Redis down")
        handler = self._make_handler(mock_redis=mock_r)

        # Should not raise
        asyncio.run(handler.react("chat-1", 42, "\U0001f44d"))

    def test_dual_write_sends_to_both(self):
        """When file_handler is provided, send() should write to both Redis and file."""
        mock_r = self._mock_redis()

        with tempfile.TemporaryDirectory() as tmp:
            file_handler = FileOutputHandler(log_dir=Path(tmp))
            handler = self._make_handler(mock_redis=mock_r, file_handler=file_handler)

            class FakeSession:
                session_id = "dual-test"

            asyncio.run(handler.send("chat-1", "dual write test", 1, FakeSession()))

            # Redis got the write
            mock_r.rpush.assert_called_once()

            # File also got the write
            log_file = Path(tmp) / "dual-test.log"
            assert log_file.exists()
            assert "dual write test" in log_file.read_text()

    def test_dual_write_react(self):
        """When file_handler is provided, react() should write to both."""
        mock_r = self._mock_redis()

        with tempfile.TemporaryDirectory() as tmp:
            file_handler = FileOutputHandler(log_dir=Path(tmp))
            handler = self._make_handler(mock_redis=mock_r, file_handler=file_handler)

            asyncio.run(handler.react("chat-1", 42, "\U0001f44d"))

            # Redis got the write
            mock_r.rpush.assert_called_once()

            # File also got the write
            log_file = Path(tmp) / "chat-1.log"
            assert log_file.exists()
            assert "REACTION" in log_file.read_text()
