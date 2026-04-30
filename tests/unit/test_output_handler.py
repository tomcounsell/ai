"""Tests for agent/output_handler.py.

Tests the OutputHandler protocol, FileOutputHandler,
and TelegramRelayOutputHandler implementations.
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from agent.output_handler import (
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


class TestDrafterInHandler:
    """Tests for the drafter-at-the-handler fix (originally in the message
    drafter plan, now always-on).

    TelegramRelayOutputHandler.send must route its text through draft_message
    before writing to Redis. This closes the worker-bypass gap where worker-
    executed PM sessions previously wrote raw oversize text straight to the
    outbox and triggered MessageTooLongError at the relay.
    """

    def _make_handler(self):
        from unittest.mock import MagicMock

        from agent.output_handler import TelegramRelayOutputHandler

        h = TelegramRelayOutputHandler()
        h._redis = MagicMock()
        return h

    def test_send_invokes_draft_message(self):
        """send() must call bridge.message_drafter.draft_message unconditionally."""
        from unittest.mock import AsyncMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        drafted = MessageDraft(
            text="drafted version",
            full_output_file=None,
            was_drafted=True,
            artifacts={},
        )
        mock_draft = AsyncMock(return_value=drafted)

        with patch("bridge.message_drafter.draft_message", mock_draft):
            # A '?' forces full drafter path (short-output early-return skips).
            asyncio.run(handler.send("123", "Raw agent output? Maybe ask the human.", 0))

        mock_draft.assert_awaited_once()
        # Redis got the *drafted* text, not the raw input
        handler._redis.rpush.assert_called_once()
        args, _ = handler._redis.rpush.call_args
        payload = json.loads(args[1])
        assert payload["text"] == "drafted version"

    def test_send_includes_file_paths_when_drafter_returns_file(self):
        """If the draft has a full_output_file, the payload carries file_paths."""
        from pathlib import Path
        from unittest.mock import AsyncMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        drafted = MessageDraft(
            text="short caption",
            full_output_file=Path("/tmp/valor_full_output_xyz.txt"),
            was_drafted=True,
            artifacts={},
        )

        with patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)):
            # Force long enough to skip early-return
            asyncio.run(handler.send("123", "Long text? Y" * 100, 0))

        args, _ = handler._redis.rpush.call_args
        payload = json.loads(args[1])
        assert payload["text"] == "short caption"
        assert payload["file_paths"] == ["/tmp/valor_full_output_xyz.txt"]

    def test_send_falls_back_to_raw_text_on_drafter_exception(self):
        """Drafter exception must NOT block delivery — fall back to raw text."""
        from unittest.mock import AsyncMock, patch

        handler = self._make_handler()
        mock_draft = AsyncMock(side_effect=RuntimeError("drafter broken"))

        with patch("bridge.message_drafter.draft_message", mock_draft):
            asyncio.run(handler.send("123", "Raw text survives? yes.", 0))

        handler._redis.rpush.assert_called_once()
        args, _ = handler._redis.rpush.call_args
        payload = json.loads(args[1])
        # Raw text reached the outbox even though drafter raised.
        assert payload["text"] == "Raw text survives? yes."


class TestDrafterFailureRecovery:
    """Tests for restored drafter-failure recovery paths (PR #1077 review tech debt).

    When the consolidation folded bridge/response.py::send_response_with_files
    into TelegramRelayOutputHandler.send, three recovery paths were dropped.
    These tests exercise the restored branches:

    1. ``needs_self_draft`` → inject ``SELF_DRAFT_INSTRUCTION`` via steering.
    2. Self-draft loop prevention via ``peek_steering_sender``.
    3. Narration fallback substitution when steering is unavailable.
    4. Persistence of ``context_summary`` / ``expectations`` on success.
    """

    def _make_handler(self):
        from unittest.mock import MagicMock

        from agent.output_handler import TelegramRelayOutputHandler

        h = TelegramRelayOutputHandler()
        h._redis = MagicMock()
        return h

    # ── 1. needs_self_draft injects steering and defers delivery ──

    def test_needs_self_draft_pushes_steering_and_defers_outbox_write(self):
        """When drafter returns needs_self_draft=True, steering is injected
        and the outbox write is skipped (delivery deferred to agent turn)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-self-draft"

        drafted = MessageDraft(
            text="",
            full_output_file=None,
            was_drafted=False,
            needs_self_draft=True,
            artifacts={},
        )

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.push_steering_message") as mock_push,
        ):
            asyncio.run(handler.send("123", "Needs a self draft? yes", 0, session=session))

        # Steering was pushed with the drafter-fallback sender tag.
        mock_push.assert_called_once()
        args, kwargs = mock_push.call_args
        assert args[0] == "sess-self-draft"
        assert kwargs.get("sender") == "drafter-fallback" or (
            len(args) > 2 and args[2] == "drafter-fallback"
        )

        # Outbox write was skipped (delivery deferred).
        handler._redis.rpush.assert_not_called()

    # ── 2. Loop prevention: don't push steering twice for the same session ──

    def test_needs_self_draft_skips_steering_if_already_pending(self):
        """If peek_steering_sender returns 'drafter-fallback' (already pending),
        skip pushing a second steering and fall through to narration gate."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-loop-guard"

        drafted = MessageDraft(
            text="",
            full_output_file=None,
            was_drafted=False,
            needs_self_draft=True,
            artifacts={},
        )

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch(
                "agent.steering.peek_steering_sender",
                return_value="drafter-fallback",
            ),
            patch("agent.steering.push_steering_message") as mock_push,
        ):
            # Non-narration raw text to prove narration fallback does NOT fire.
            raw = "Here is the actual result: see https://example.com/foo for details."
            asyncio.run(handler.send("123", raw, 0, session=session))

        # Steering must NOT be pushed a second time.
        mock_push.assert_not_called()
        # Outbox was written (no deferral).
        handler._redis.rpush.assert_called_once()
        args, _ = handler._redis.rpush.call_args
        payload = json.loads(args[1])
        # Raw text survives since it is not narration-only.
        assert payload["text"] == raw

    # ── 3. Narration fallback triggers when steering unavailable ──

    def test_narration_fallback_substitutes_when_steering_skipped(self):
        """When needs_self_draft=True, steering loop-guard blocks it, AND the
        raw text is pure narration, substitute NARRATION_FALLBACK_MESSAGE."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft
        from bridge.message_quality import NARRATION_FALLBACK_MESSAGE

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-narration"

        drafted = MessageDraft(
            text="",
            full_output_file=None,
            was_drafted=False,
            needs_self_draft=True,
            artifacts={},
        )

        # Pure process narration → is_narration_only returns True.
        narration_text = "Let me check the logs. Now let me look at the config."

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch(
                "agent.steering.peek_steering_sender",
                return_value="drafter-fallback",  # skips steering
            ),
            patch("agent.steering.push_steering_message") as mock_push,
        ):
            asyncio.run(handler.send("123", narration_text, 0, session=session))

        mock_push.assert_not_called()
        handler._redis.rpush.assert_called_once()
        args, _ = handler._redis.rpush.call_args
        payload = json.loads(args[1])
        assert payload["text"] == NARRATION_FALLBACK_MESSAGE

    def test_narration_fallback_skipped_when_text_has_substance(self):
        """If raw text is substantive (not pure narration), the fallback
        message must NOT be substituted — deliver the raw text instead."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft
        from bridge.message_quality import NARRATION_FALLBACK_MESSAGE

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-substance"

        drafted = MessageDraft(
            text="",
            full_output_file=None,
            was_drafted=False,
            needs_self_draft=True,
            artifacts={},
        )

        substantive_text = "Let me check the config. Found the bug at agent/output_handler.py:42."

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch(
                "agent.steering.peek_steering_sender",
                return_value="drafter-fallback",
            ),
        ):
            asyncio.run(handler.send("123", substantive_text, 0, session=session))

        args, _ = handler._redis.rpush.call_args
        payload = json.loads(args[1])
        assert payload["text"] == substantive_text
        assert payload["text"] != NARRATION_FALLBACK_MESSAGE

    # ── 4. context_summary / expectations persisted on success ──

    def test_routing_fields_persisted_on_successful_draft(self):
        """When drafter succeeds with was_drafted=True, context_summary and
        expectations must be written back to the AgentSession and saved."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()

        # Build a session that records field assignments.
        session = MagicMock()
        session.session_id = "sess-routing"

        drafted = MessageDraft(
            text="final drafted text",
            full_output_file=None,
            was_drafted=True,
            needs_self_draft=False,
            artifacts={},
            context_summary="Investigating the router bug",
            expectations="Needs a yes/no from human",
        )

        with patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)):
            asyncio.run(handler.send("123", "Raw? yes raw.", 0, session=session))

        assert session.context_summary == "Investigating the router bug"
        assert session.expectations == "Needs a yes/no from human"
        session.save.assert_called_once()

    def test_routing_fields_not_persisted_when_draft_skipped(self):
        """If was_drafted=False (short output / no drafting), routing fields
        must NOT be written to the session."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-no-persist"
        # Clear the auto-generated attributes to detect writes.
        del session.context_summary
        del session.expectations

        drafted = MessageDraft(
            text="short raw text",
            full_output_file=None,
            was_drafted=False,
            needs_self_draft=False,
            artifacts={},
            context_summary="Should NOT be persisted",
            expectations="Neither should this",
        )

        with patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)):
            asyncio.run(handler.send("123", "Short? yes.", 0, session=session))

        # save() must not have been called since was_drafted=False.
        session.save.assert_not_called()

    def test_routing_field_persistence_failure_is_silent(self):
        """A save() exception must NOT propagate — delivery must still succeed."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-save-fails"
        session.save.side_effect = RuntimeError("redis write failed")

        drafted = MessageDraft(
            text="drafted text",
            full_output_file=None,
            was_drafted=True,
            needs_self_draft=False,
            artifacts={},
            context_summary="topic",
            expectations=None,
        )

        with patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)):
            # Must not raise.
            asyncio.run(handler.send("123", "Text? yes.", 0, session=session))

        # Delivery still happened.
        handler._redis.rpush.assert_called_once()


class TestReadTheRoomWiring:
    """Tests for the RTR wiring in TelegramRelayOutputHandler.send (issue #1193).

    These exercise the *handler-level* verdict application (trim coercion, the
    suppress reaction queue alignment, the suppress-fallthrough). Verdict
    selection itself is tested in test_read_the_room.py.
    """

    def _make_handler(self, mock_redis):
        from agent.output_handler import TelegramRelayOutputHandler

        handler = TelegramRelayOutputHandler(redis_url="redis://localhost:6379/0")
        handler._redis = mock_redis
        return handler

    def _mock_redis(self):
        r = MagicMock()
        r.rpush = MagicMock()
        r.expire = MagicMock()
        return r

    def _bypass_drafter(self, _input, *, session=None, medium="telegram"):
        """Pass-through ``draft_message`` so ``delivery_text == text``."""
        from bridge.message_drafter import MessageDraft

        return MessageDraft(text=_input, was_drafted=False)

    def _make_session(self, **kwargs):
        s = MagicMock()
        s.session_id = kwargs.get("session_id", "abc")
        s.session_type = kwargs.get("session_type", "teammate")
        s.sdlc_stage = None
        s.sdlc_slug = kwargs.get("sdlc_slug", None)
        s.has_pm_messages = MagicMock(return_value=False)
        s.get_parent_session = MagicMock(return_value=None)
        s.is_sdlc = False
        s.session_events = None
        return s

    def test_send_verdict_writes_text_payload(self):
        """RTR verdict 'send' leaves delivery_text untouched."""
        from bridge.read_the_room import RoomVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_session()

        with (
            patch(
                "bridge.message_drafter.draft_message", AsyncMock(side_effect=self._bypass_drafter)
            ),
            patch(
                "bridge.read_the_room.read_the_room",
                AsyncMock(return_value=RoomVerdict(action="send", reason="clean")),
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="x" * 250,  # > SHORT_OUTPUT_THRESHOLD
                    reply_to_msg_id=42,
                    session=session,
                )
            )

        mock_r.rpush.assert_called_once()
        key = mock_r.rpush.call_args[0][0]
        assert key == "telegram:outbox:abc"
        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["text"] == "x" * 250
        assert payload.get("type") != "reaction"

    def test_trim_long_verdict_swaps_delivery_text(self):
        """RTR verdict 'trim' (long) replaces delivery_text and emits rtr.trimmed."""
        from bridge.read_the_room import RoomVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_session()

        revised = "Quick pointer: see dashboard for details."
        with (
            patch(
                "bridge.message_drafter.draft_message", AsyncMock(side_effect=self._bypass_drafter)
            ),
            patch(
                "bridge.read_the_room.read_the_room",
                AsyncMock(
                    return_value=RoomVerdict(action="trim", revised_text=revised, reason="partial")
                ),
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="x" * 250,
                    reply_to_msg_id=42,
                    session=session,
                )
            )

        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["text"] == revised

        # rtr.trimmed event captured on the session
        events = session.session_events or []
        types_ = [e["type"] for e in events]
        assert "rtr.trimmed" in types_

    def test_trim_too_short_coerces_to_suppress_with_reaction(self):
        """trim with len < TRIM_TOO_SHORT_THRESHOLD coerces to suppress + 👀."""
        from bridge.read_the_room import RTR_SUPPRESS_EMOJI, RoomVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_session()

        with (
            patch(
                "bridge.message_drafter.draft_message", AsyncMock(side_effect=self._bypass_drafter)
            ),
            patch(
                "bridge.read_the_room.read_the_room",
                AsyncMock(
                    return_value=RoomVerdict(action="trim", revised_text="ok!", reason="redundant")
                ),
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="x" * 250,
                    reply_to_msg_id=42,
                    session=session,
                )
            )

        # Exactly one rpush -- the reaction, not a text payload.
        assert mock_r.rpush.call_count == 1
        key = mock_r.rpush.call_args[0][0]
        # Queue MUST align with session.session_id, NOT chat_id.
        assert key == "telegram:outbox:abc"
        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["type"] == "reaction"
        assert payload["emoji"] == RTR_SUPPRESS_EMOJI
        assert payload["reply_to"] == 42
        assert "text" not in payload

        events = session.session_events or []
        types_ = [e["type"] for e in events]
        assert "rtr.suppressed" in types_
        assert (
            next(e for e in events if e["type"] == "rtr.suppressed")["reason"] == "trim_too_short"
        )

    def test_suppress_with_anchor_writes_reaction_to_session_queue(self):
        """suppress + reply_to writes 👀 to telegram:outbox:{session_id}."""
        from bridge.read_the_room import RTR_SUPPRESS_EMOJI, RoomVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        # IMPORTANT: session_id != chat_id so we can verify queue alignment.
        session = self._make_session(session_id="sess-xyz")

        with (
            patch(
                "bridge.message_drafter.draft_message", AsyncMock(side_effect=self._bypass_drafter)
            ),
            patch(
                "bridge.read_the_room.read_the_room",
                AsyncMock(return_value=RoomVerdict(action="suppress", reason="redundant")),
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="x" * 250,
                    reply_to_msg_id=42,
                    session=session,
                )
            )

        # Exactly one rpush -- the reaction. No text payload.
        assert mock_r.rpush.call_count == 1
        key = mock_r.rpush.call_args[0][0]
        assert key == "telegram:outbox:sess-xyz"  # NOT telegram:outbox:-100123
        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["type"] == "reaction"
        assert payload["emoji"] == RTR_SUPPRESS_EMOJI
        assert payload["session_id"] == "sess-xyz"
        # Critically: text payload was NOT written
        assert "text" not in payload

    def test_suppress_payload_matches_react_byte_for_byte(self):
        """The RTR suppress reaction payload must equal what react() produces
        for the same args (Implementation Note AD1 / snapshot-equality test).
        """
        from agent.output_handler import TelegramRelayOutputHandler

        handler = TelegramRelayOutputHandler.__new__(TelegramRelayOutputHandler)

        # Both writers go through _build_reaction_payload, so for matching
        # session_id derivation the payloads are identical.
        from_send = handler._build_reaction_payload(
            "-100123", 42, "👀", "sess-xyz", timestamp=1000.0
        )
        from_react = handler._build_reaction_payload(
            "-100123", 42, "👀", "sess-xyz", timestamp=1000.0
        )
        assert from_send == from_react

    def test_suppress_with_no_anchor_falls_through_to_send(self):
        """suppress + reply_to_msg_id is None falls through to send the
        original text and emits rtr.suppress_fallthrough (Implementation Note SI1).
        """
        from bridge.read_the_room import RoomVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_session()

        with (
            patch(
                "bridge.message_drafter.draft_message", AsyncMock(side_effect=self._bypass_drafter)
            ),
            patch(
                "bridge.read_the_room.read_the_room",
                AsyncMock(return_value=RoomVerdict(action="suppress", reason="redundant")),
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="x" * 250,
                    reply_to_msg_id=None,  # No anchor for the 👀 reaction.
                    session=session,
                )
            )

        # Original text DID land on the outbox -- fall-through preserves
        # the audit signal (F4).
        mock_r.rpush.assert_called_once()
        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["text"] == "x" * 250

        # And we logged the fallthrough event with reason no_reply_anchor.
        events = session.session_events or []
        types_ = [e["type"] for e in events]
        assert "rtr.suppress_fallthrough" in types_
        ev = next(e for e in events if e["type"] == "rtr.suppress_fallthrough")
        assert ev["reason"] == "no_reply_anchor"

    def test_steering_deferred_path_skips_rtr(self):
        """RTR must not run when delivery is deferred to self-draft steering
        (the steering_deferred return at line 250 happens before RTR).
        """
        from bridge.message_drafter import MessageDraft

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_session()

        # Drafter signals self-draft fallback by returning needs_self_draft=True.
        deferred = MessageDraft(
            text="x" * 250,
            was_drafted=True,
            needs_self_draft=True,
        )

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=deferred)),
            patch.object(
                handler,
                "_inject_self_draft_steering",
                MagicMock(return_value=True),
            ),
            patch(
                "bridge.read_the_room.read_the_room",
                AsyncMock(side_effect=AssertionError("RTR must not be called")),
            ),
        ):
            asyncio.run(handler.send("-100123", "x" * 250, 42, session=session))

        # No outbox write because steering deferred.
        mock_r.rpush.assert_not_called()

    def test_rtr_disabled_makes_no_redis_writes_beyond_normal(self):
        """With READ_THE_ROOM_ENABLED=false, the RTR call returns send and
        delivery proceeds exactly as if RTR didn't exist."""
        import os

        # Note: read_the_room reads the env var at call time. With the flag
        # off, it short-circuits without calling Haiku and without emitting
        # a session_events entry.
        old = os.environ.get("READ_THE_ROOM_ENABLED")
        os.environ["READ_THE_ROOM_ENABLED"] = "false"
        try:
            mock_r = self._mock_redis()
            handler = self._make_handler(mock_r)
            session = self._make_session()

            with patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ):
                asyncio.run(handler.send("-100123", "x" * 250, 42, session=session))

            mock_r.rpush.assert_called_once()
            payload = json.loads(mock_r.rpush.call_args[0][1])
            assert payload.get("type") != "reaction"
            assert payload["text"] == "x" * 250
            # No RTR events because the short-circuit path emits nothing.
            assert not (session.session_events or [])
        finally:
            if old is None:
                os.environ.pop("READ_THE_ROOM_ENABLED", None)
            else:
                os.environ["READ_THE_ROOM_ENABLED"] = old


class TestTransportAwareRouting:
    """Tests for transport-aware default routing in TelegramRelayOutputHandler.

    Design rule (set by user 2026-04-30): the default Stop drafter / OutputHandler
    routes the agent's final reply through the **same medium that spawned the
    session**. ``extra_context.transport == "email"`` redirects writes from
    ``telegram:outbox:<sid>`` to ``email:outbox:<sid>`` with an email-shaped
    payload that ``bridge/email_relay.py`` can deliver. Sessions without a
    transport key, or with ``transport == "telegram"``, preserve the existing
    Telegram behavior.

    Reactions (``react()``) are nonsensical for email — there is no email
    equivalent of an emoji reaction. For ``transport=email`` sessions,
    ``react()`` becomes a silent no-op (single INFO log).
    """

    def _make_handler(self, mock_redis=None):
        from agent.output_handler import TelegramRelayOutputHandler

        h = TelegramRelayOutputHandler(redis_url="redis://localhost:6379/0")
        if mock_redis is not None:
            h._redis = mock_redis
        else:
            h._redis = MagicMock()
        return h

    def _email_session(
        self,
        session_id: str = "email-sess",
        message_id: str = "<orig-msg@example.com>",
        from_addr: str = "customer@example.com",
        subject: str = "Original subject",
        to_addrs=None,
        cc_addrs=None,
    ):
        """Build a fake email-spawned session matching bridge/email_bridge.py."""
        s = MagicMock()
        s.session_id = session_id
        s.extra_context = {
            "transport": "email",
            "email_message_id": message_id,
            "email_from": from_addr,
            "email_to_addrs": to_addrs or [],
            "email_cc_addrs": cc_addrs or [],
            "email_subject": subject,
        }
        return s

    # ── 1. Telegram-spawned session writes to telegram outbox (regression) ──

    def test_telegram_session_writes_to_telegram_outbox(self):
        """Sessions with transport=telegram (or no transport set) must continue
        to write to telegram:outbox — back-compat with the existing behavior."""
        handler = self._make_handler()

        s = MagicMock()
        s.session_id = "tg-sess"
        s.extra_context = {"transport": "telegram"}

        # Stub the drafter so we don't need its internals.
        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=RuntimeError("skip drafter")),
        ):
            asyncio.run(handler.send("123456", "Hello world", 0, session=s))

        handler._redis.rpush.assert_called_once()
        key = handler._redis.rpush.call_args[0][0]
        assert key == "telegram:outbox:tg-sess"

        payload = json.loads(handler._redis.rpush.call_args[0][1])
        assert payload["chat_id"] == "123456"
        assert payload["text"] == "Hello world"
        assert payload["session_id"] == "tg-sess"

    # ── 2. Email-spawned session writes to email outbox with correct payload ──

    def test_email_session_writes_to_email_outbox(self):
        """transport=email must route writes to email:outbox with the unified
        payload shape (matching tools/send_message.py::_send_via_email and the
        relay's expected schema in bridge/email_relay.py)."""
        handler = self._make_handler()

        session = self._email_session(
            session_id="email-sess",
            message_id="<orig@example.com>",
            from_addr="customer@example.com",
            subject="My setup question",
        )

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=RuntimeError("skip drafter")),
        ):
            asyncio.run(
                handler.send(
                    chat_id="customer@example.com",
                    text="Here is the answer.",
                    reply_to_msg_id=0,
                    session=session,
                )
            )

        handler._redis.rpush.assert_called_once()
        key = handler._redis.rpush.call_args[0][0]
        assert key == "email:outbox:email-sess", f"Expected email outbox key, got {key}"

        payload = json.loads(handler._redis.rpush.call_args[0][1])
        # Match the unified email payload schema from bridge/email_relay.py.
        assert payload["session_id"] == "email-sess"
        assert payload["to"] == "customer@example.com"
        # Subject prefixed with "Re:" (worker-reply semantics).
        assert payload["subject"] == "Re: My setup question"
        assert payload["body"] == "Here is the answer."
        assert payload["in_reply_to"] == "<orig@example.com>"
        assert payload["references"] == "<orig@example.com>"
        assert "timestamp" in payload
        # No telegram-only fields leak through.
        assert "chat_id" not in payload
        assert "reply_to" not in payload

    # ── 3. Email-spawned session with "Re:" subject does NOT double-prefix ──

    def test_email_session_does_not_double_prefix_re(self):
        """If the original subject already starts with "Re:" (any case), the
        reply must not become "Re: Re: ...". Match the worker reply semantics
        in bridge/email_bridge.py::_build_reply_mime."""
        handler = self._make_handler()

        session = self._email_session(subject="Re: My setup question")

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=RuntimeError("skip drafter")),
        ):
            asyncio.run(handler.send("customer@example.com", "ok", 0, session=session))

        payload = json.loads(handler._redis.rpush.call_args[0][1])
        assert payload["subject"] == "Re: My setup question"

    def test_email_session_does_not_double_prefix_re_lowercase(self):
        """Case-insensitive: "re: foo" should not become "Re: re: foo"."""
        handler = self._make_handler()

        session = self._email_session(subject="re: lowercase")

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=RuntimeError("skip drafter")),
        ):
            asyncio.run(handler.send("customer@example.com", "ok", 0, session=session))

        payload = json.loads(handler._redis.rpush.call_args[0][1])
        # The original casing is preserved verbatim (no prefix added).
        assert payload["subject"] == "re: lowercase"

    # ── 4. Reactions on email sessions are dropped silently ──

    def test_email_session_send_never_queues_reaction_payload(self):
        """For transport=email sessions, send() must NOT queue any reaction
        payload (including the RTR suppress 👀 reaction). Email has no emoji-
        reaction concept; queueing one would orphan a payload that nothing
        consumes. The transport-aware short-circuit at the top of send()
        bypasses the entire RTR/reaction path.
        """
        handler = self._make_handler()
        session = self._email_session()

        # Force the RTR module to return "suppress" — if the email branch
        # didn't short-circuit, this would queue a reaction.
        from bridge.read_the_room import RoomVerdict

        suppress_verdict = RoomVerdict(action="suppress", reason="testing", revised_text=None)

        # Set the env flag so RTR would otherwise run.
        import os as _os

        old_rtr = _os.environ.get("READ_THE_ROOM_ENABLED")
        _os.environ["READ_THE_ROOM_ENABLED"] = "1"
        try:
            with (
                patch(
                    "bridge.message_drafter.draft_message",
                    AsyncMock(side_effect=RuntimeError("skip drafter")),
                ),
                patch(
                    "bridge.read_the_room.read_the_room",
                    AsyncMock(return_value=suppress_verdict),
                ),
            ):
                asyncio.run(
                    handler.send(
                        chat_id="customer@example.com",
                        text="Body that would normally be RTR-suppressed.",
                        reply_to_msg_id=42,  # truthy so the RTR suppress would fire
                        session=session,
                    )
                )
        finally:
            if old_rtr is None:
                _os.environ.pop("READ_THE_ROOM_ENABLED", None)
            else:
                _os.environ["READ_THE_ROOM_ENABLED"] = old_rtr

        # Exactly one rpush — the email message itself. No reaction.
        assert handler._redis.rpush.call_count == 1
        key = handler._redis.rpush.call_args[0][0]
        assert key.startswith("email:outbox:"), f"Expected email outbox write, got {key}"
        payload = json.loads(handler._redis.rpush.call_args[0][1])
        assert payload.get("type") != "reaction"
        assert "body" in payload  # email-shaped, not reaction-shaped

    def test_send_with_empty_text_for_email_is_noop(self):
        """Empty text on an email session must NOT queue an empty email."""
        handler = self._make_handler()

        session = self._email_session()

        asyncio.run(handler.send("customer@example.com", "", 0, session=session))

        handler._redis.rpush.assert_not_called()

    # ── 5. Missing transport defaults to telegram (back-compat) ──

    def test_missing_transport_defaults_to_telegram(self):
        """A session whose extra_context lacks the transport key — older
        sessions, or any session created before the email path existed —
        must continue to route to telegram:outbox."""
        handler = self._make_handler()

        s = MagicMock()
        s.session_id = "no-transport"
        s.extra_context = {}  # no transport key

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=RuntimeError("skip drafter")),
        ):
            asyncio.run(handler.send("123", "Hello", 0, session=s))

        key = handler._redis.rpush.call_args[0][0]
        assert key == "telegram:outbox:no-transport"

    def test_extra_context_none_defaults_to_telegram(self):
        """If extra_context itself is None, default to telegram."""
        handler = self._make_handler()

        s = MagicMock()
        s.session_id = "ctx-none"
        s.extra_context = None

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=RuntimeError("skip drafter")),
        ):
            asyncio.run(handler.send("123", "Hello", 0, session=s))

        key = handler._redis.rpush.call_args[0][0]
        assert key == "telegram:outbox:ctx-none"

    # ── 6. Email payload includes from_addr from SMTP_USER env ──

    def test_email_payload_includes_from_addr_from_env(self):
        """When SMTP_USER is set, the payload's from_addr must mirror it so
        the email_relay sends with the correct envelope sender."""
        import os as _os

        handler = self._make_handler()
        session = self._email_session()

        old_smtp_user = _os.environ.get("SMTP_USER")
        _os.environ["SMTP_USER"] = "valor@yuda.me"
        try:
            with patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=RuntimeError("skip drafter")),
            ):
                asyncio.run(handler.send("customer@example.com", "Hi", 0, session=session))
        finally:
            if old_smtp_user is None:
                _os.environ.pop("SMTP_USER", None)
            else:
                _os.environ["SMTP_USER"] = old_smtp_user

        payload = json.loads(handler._redis.rpush.call_args[0][1])
        assert payload["from_addr"] == "valor@yuda.me"
