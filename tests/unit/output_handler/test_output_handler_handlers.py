"""Tests for agent/output_handler.py: handler implementations.

Covers the OutputHandler protocol, FileOutputHandler,
TelegramRelayOutputHandler, and the system-room sink.
Split out of the former ``tests/unit/test_output_handler.py`` monolith (#2879). The
``output_handler`` filename prefix is load-bearing: ``tests/conftest.py``
derives feature markers from the module basename via ``FEATURE_MAP``.
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from agent.output_handler import (
    DeliveryOutcome,
    FileOutputHandler,
    OutputHandler,
    TelegramRelayOutputHandler,
)


class TestOutputHandlerProtocol:
    """Test OutputHandler protocol compliance."""

    def test_file_output_handler_is_output_handler(self):
        """FileOutputHandler must satisfy the OutputHandler protocol."""
        handler = FileOutputHandler()
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

            async def react(self, chat_id, msg_id, emoji=None, session=None):
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

    def test_react_log_filename_prefers_session_id_over_chat_id(self):
        """Success Criterion: the log filename is
        ``getattr(session, "session_id", None) or chat_id`` -- when a
        session's session_id differs from chat_id, the reaction line lands
        in ``logs/worker/{session_id}.log``, NOT ``{chat_id}.log``. This is
        the only criterion that catches the three-arg
        ``self._file_handler.react(...)`` call site at
        agent/output_handler.py:1475 (TelegramRelayOutputHandler's dual-write)
        being left unchanged -- case (e) above passes with session=None and
        cannot see it, so this must be driven directly against
        FileOutputHandler.react with a real session object."""
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            handler = FileOutputHandler(log_dir=log_dir)

            class FakeSession:
                session_id = "session-abc-123"

            asyncio.run(handler.react("chat-999", 42, "\U0001f44d", FakeSession()))

            session_log = log_dir / "session-abc-123.log"
            chat_log = log_dir / "chat-999.log"
            assert session_log.exists()
            assert "REACTION" in session_log.read_text()
            assert not chat_log.exists()


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
        """send() should rpush a JSON payload built by build_telegram_outbox_payload."""
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

        # Verify payload structure matches build_telegram_outbox_payload
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
        """react() should write a payload with type='reaction'.

        session=None (unset) back-compat pin: _resolve_transport(None, ...)
        returns "telegram" by design, so a caller that never learned about
        the widened signature (session defaults to None) keeps the exact
        pre-widening RPUSH behavior. See also
        TestReactTransportDerivation.test_b_session_none_back_compat_unchanged_telegram_rpush.
        """
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

    def test_dual_write_react_forwards_session_for_log_filename(self):
        """Success Criterion (log-filename): a telegram-transport reaction
        whose session.session_id differs from chat_id must land in
        logs/worker/{session_id}.log, not logs/worker/{chat_id}.log. Pins the
        four-arg dual-write at agent/output_handler.py:1475
        (self._file_handler.react(chat_id, msg_id, emoji, session))."""
        mock_r = self._mock_redis()

        with tempfile.TemporaryDirectory() as tmp:
            file_handler = FileOutputHandler(log_dir=Path(tmp))
            handler = self._make_handler(mock_redis=mock_r, file_handler=file_handler)

            class FakeSession:
                session_id = "session-xyz-789"

            asyncio.run(handler.react("chat-1", 42, "\U0001f44d", FakeSession()))

            # Redis outbox key is still keyed by chat_id (Rabbit Holes: react()
            # keeps session_id = chat_id on the telegram path).
            assert mock_r.rpush.call_args[0][0] == "telegram:outbox:chat-1"

            # But the file dual-write prefers the session's own session_id.
            session_log = Path(tmp) / "session-xyz-789.log"
            chat_log = Path(tmp) / "chat-1.log"
            assert session_log.exists()
            assert "REACTION" in session_log.read_text()
            assert not chat_log.exists()


class TestSystemRoomSink:
    """Issue #2497: chatless sessions (reflections) route to the system Room.

    A reflection session carries the placeholder chat_id="0" and no human
    addressee. Its output must NOT be enqueued to the telegram outbox (the
    relay's zero-guard always drops it); instead the handler derives the
    "system" transport from the Room model's ``system`` addressee convention
    and durably records the output in the per-project system Room's inbox.
    """

    def _make_handler(self, mock_redis=None, file_handler=None):
        handler = TelegramRelayOutputHandler(
            redis_url="redis://localhost:6379/0",
            file_handler=file_handler,
        )
        handler._redis = mock_redis if mock_redis is not None else MagicMock()
        return handler

    class _ReflectionSession:
        """Shape of a reflection-scheduler session: placeholder chat, no transport."""

        session_id = "0_1234567890"
        chat_id = "0"
        project_key = "valor"
        extra_context: dict = {}

    # ── transport derivation ─────────────────────────────────────────────

    def test_resolve_transport_derives_system_for_placeholder_chat(self):
        """chat_id="0" + no explicit transport → system (not telegram)."""
        transport = TelegramRelayOutputHandler._resolve_transport(
            self._ReflectionSession(), chat_id="0"
        )
        assert transport == "system"

    def test_resolve_transport_explicit_transport_wins(self):
        """An explicit extra_context transport overrides the derivation."""

        class EmailSession(self._ReflectionSession):
            extra_context = {"transport": "email"}

        transport = TelegramRelayOutputHandler._resolve_transport(EmailSession(), chat_id="0")
        assert transport == "email"

    def test_resolve_transport_real_peer_argument_stays_telegram(self):
        """A chatless session sending to an explicit real chat_id (CLI
        cross-chat send) must still reach telegram."""
        transport = TelegramRelayOutputHandler._resolve_transport(
            self._ReflectionSession(), chat_id="-100123"
        )
        assert transport == "telegram"

    def test_resolve_transport_without_project_key_falls_back_to_telegram(self):
        """No project_key → no Room to record into → status-quo telegram."""

        class NoProjectSession:
            session_id = "sess-x"
            chat_id = "0"
            extra_context: dict = {}

        transport = TelegramRelayOutputHandler._resolve_transport(NoProjectSession(), chat_id="0")
        assert transport == "telegram"

    def test_resolve_transport_numeric_chat_session_stays_telegram(self):
        """A real Telegram session is untouched by the system derivation."""

        class RealChatSession:
            session_id = "sess-y"
            chat_id = "-100123"
            project_key = "valor"
            extra_context: dict = {}

        transport = TelegramRelayOutputHandler._resolve_transport(
            RealChatSession(), chat_id="-100123"
        )
        assert transport == "telegram"

    def test_deliverable_peer_multi_hyphen_pseudo_numeric_is_not_deliverable(self):
        """ "--5" passes lstrip("-").isdigit() but is not an int — the guard
        must return False, not raise ValueError on the send hot path."""
        assert TelegramRelayOutputHandler._deliverable_telegram_peer("--5") is False
        # And the derivation that consumes it must not crash either.
        transport = TelegramRelayOutputHandler._resolve_transport(
            self._ReflectionSession(), chat_id="--5"
        )
        assert transport == "system"

    # ── send() routing ───────────────────────────────────────────────────

    def test_send_records_to_system_room_not_telegram_outbox(self):
        """Reflection output goes to the system Room inbox; no outbox write,
        no drafter call (there is no human audience to draft for)."""
        mock_r = MagicMock()
        handler = self._make_handler(mock_redis=mock_r)

        mock_room = MagicMock()
        mock_room.append_inbox = MagicMock(return_value=True)
        with (
            patch("models.room.Room.resolve", return_value=mock_room) as mock_resolve,
            patch("bridge.message_drafter.draft_message", new_callable=AsyncMock) as mock_draft,
        ):
            outcome = asyncio.run(
                handler.send("0", "reflection findings", 0, self._ReflectionSession())
            )

        assert outcome == DeliveryOutcome.sent
        mock_r.rpush.assert_not_called()
        mock_draft.assert_not_called()
        mock_resolve.assert_called_once_with("valor", "system")
        entry = mock_room.append_inbox.call_args[0][0]
        assert entry["text"] == "reflection findings"
        assert entry["session_id"] == "0_1234567890"
        assert entry["direction"] == "outbound"

    def test_send_routes_chat_id_none_session_to_system_room(self):
        """The chat_id=None chatless population also reaches the system sink."""
        mock_r = MagicMock()
        handler = self._make_handler(mock_redis=mock_r)

        class NoChatSession:
            session_id = "sess-nochat"
            chat_id = None
            project_key = "valor"
            extra_context: dict = {}

        mock_room = MagicMock()
        mock_room.append_inbox = MagicMock(return_value=True)
        with patch("models.room.Room.resolve", return_value=mock_room) as mock_resolve:
            outcome = asyncio.run(handler.send("", "chatless output", 0, NoChatSession()))

        assert outcome == DeliveryOutcome.sent
        mock_r.rpush.assert_not_called()
        mock_resolve.assert_called_once_with("valor", "system")
        assert mock_room.append_inbox.call_args[0][0]["session_id"] == "sess-nochat"

    def test_send_routes_chat_id_session_id_synthetic_to_system_room(self):
        """The chat_id=session_id synthetic (e.g. sdlc-local-N) reaches the
        system sink too — its non-numeric chat_id was always relay-dropped."""
        mock_r = MagicMock()
        handler = self._make_handler(mock_redis=mock_r)

        class SyntheticSession:
            session_id = "sdlc-local-42"
            chat_id = "sdlc-local-42"
            project_key = "valor"
            extra_context: dict = {}

        mock_room = MagicMock()
        mock_room.append_inbox = MagicMock(return_value=True)
        with patch("models.room.Room.resolve", return_value=mock_room) as mock_resolve:
            outcome = asyncio.run(
                handler.send("sdlc-local-42", "synthetic output", 0, SyntheticSession())
            )

        assert outcome == DeliveryOutcome.sent
        mock_r.rpush.assert_not_called()
        mock_resolve.assert_called_once_with("valor", "system")
        assert mock_room.append_inbox.call_args[0][0]["text"] == "synthetic output"

    def test_send_system_sink_dual_writes_to_file_handler(self):
        """The file dual-write audit trail is preserved for system-sink sends."""
        with tempfile.TemporaryDirectory() as tmp:
            file_handler = FileOutputHandler(log_dir=Path(tmp))
            handler = self._make_handler(file_handler=file_handler)

            mock_room = MagicMock()
            mock_room.append_inbox = MagicMock(return_value=True)
            with patch("models.room.Room.resolve", return_value=mock_room):
                asyncio.run(handler.send("0", "audit me", 0, self._ReflectionSession()))

            log_file = Path(tmp) / "0_1234567890.log"
            assert log_file.exists()
            assert "audit me" in log_file.read_text()

    def test_send_system_sink_never_raises_on_room_failure(self):
        """A Room outage must not crash the send; the file log still records."""
        with tempfile.TemporaryDirectory() as tmp:
            file_handler = FileOutputHandler(log_dir=Path(tmp))
            handler = self._make_handler(file_handler=file_handler)

            with patch("models.room.Room.resolve", side_effect=RuntimeError("redis down")):
                outcome = asyncio.run(
                    handler.send("0", "still recorded", 0, self._ReflectionSession())
                )

            assert outcome == DeliveryOutcome.sent
            assert "still recorded" in (Path(tmp) / "0_1234567890.log").read_text()
