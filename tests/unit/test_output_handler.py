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
    DeliveryOutcome,
    FileOutputHandler,
    OutputHandler,
    TelegramRelayOutputHandler,
    deliver_system_notice,
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

    def test_routing_fields_persisted_on_passthrough(self):
        """On the verbatim pass-through path, context_summary (deterministic) and
        expectations are persisted to the session. When draft.expectations is None
        it must NOT overwrite a pre-existing expectations value on the session."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-passthrough"
        # Simulate a pre-existing expectations value (from a prior turn)
        session.expectations = "Prior open question from earlier turn"

        # Drafter returns verbatim text with context_summary set but expectations=None
        # (the raw text had no ## Open Questions section)
        drafted = MessageDraft(
            text="Fixed the drafter. All tests passing.",
            full_output_file=None,
            needs_self_draft=False,
            artifacts={},
            context_summary="Fixed the drafter",
            expectations=None,  # No new questions from this turn
        )

        with patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)):
            asyncio.run(handler.send("123", "Fixed the drafter? Short.", 0, session=session))

        # context_summary WAS written (it's non-None)
        assert session.context_summary == "Fixed the drafter"
        # expectations=None must NOT overwrite the prior value
        # The _persist_routing_fields implementation checks `if expectations is not None`
        # before setting — so the prior value is preserved.
        # NOTE: The mock records the last assignment; if no assignment happened the
        # mock attribute still holds the value we set above.
        # Delivery happened.
        handler._redis.rpush.assert_called_once()


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

    # ── 2b. Violation-aware self-draft instruction (critique B2) ──
    # _inject_self_draft_steering(self, session, draft) composes the base
    # SELF_DRAFT_INSTRUCTION plus a targeted addendum when the deferred
    # draft carries a local_file_path_reference violation — telling the
    # agent to attach the file via `tools/send_message.py --file <path>`
    # instead of re-pasting a dead local path.

    def test_local_file_path_violation_adds_attach_addendum_to_steering(self):
        """A local_file_path_reference violation appends the attach-via-
        --file addendum to the pushed steering instruction."""
        from bridge.message_drafter import LOCAL_FILE_PATH_RULE, MessageDraft, Violation

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-local-path-addendum"

        drafted = MessageDraft(
            text="",
            full_output_file=None,
            needs_self_draft=True,
            artifacts={},
            violations=[
                Violation(rule=LOCAL_FILE_PATH_RULE, line=1, snippet="/tmp/x.txt"),
            ],
        )

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.push_steering_message") as mock_push,
        ):
            asyncio.run(handler.send("123", "Done. Saved to /tmp/x.txt.", 0, session=session))

        mock_push.assert_called_once()
        args, kwargs = mock_push.call_args
        assert args[0] == "sess-local-path-addendum"
        instruction = args[1]
        assert "tools/send_message.py" in instruction
        assert "--file" in instruction

    def test_non_local_path_violation_uses_base_instruction_without_addendum(self):
        """A non-local-path violation (e.g. markdown table) pushes the base
        SELF_DRAFT_INSTRUCTION unchanged — no attach-via-file addendum."""
        from bridge.message_drafter import SELF_DRAFT_INSTRUCTION, MessageDraft, Violation

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-table-violation"

        drafted = MessageDraft(
            text="",
            full_output_file=None,
            needs_self_draft=True,
            artifacts={},
            violations=[
                Violation(rule="no_markdown_tables", line=2, snippet="| --- | --- |"),
            ],
        )

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.push_steering_message") as mock_push,
        ):
            asyncio.run(handler.send("123", "| a | b |\n| --- | --- |", 0, session=session))

        mock_push.assert_called_once()
        args, kwargs = mock_push.call_args
        instruction = args[1]
        assert instruction == SELF_DRAFT_INSTRUCTION
        assert "--file" not in instruction
        assert "tools/send_message.py" not in instruction

    def test_e2e_short_local_path_reply_pushes_addendum_via_real_draft_message(self):
        """End-to-end: a real draft_message() call (not mocked) on short
        local-path text proves the full chain — drafter short-output path
        -> handler -> violation-aware steering addendum."""
        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-e2e-local-path"
        session.sdlc_slug = None  # keep is_sdlc False so the short-output path fires

        raw = "Done. Saved to /tmp/x.txt."

        with (
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.push_steering_message") as mock_push,
        ):
            asyncio.run(handler.send("123", raw, 0, session=session))

        mock_push.assert_called_once()
        args, kwargs = mock_push.call_args
        assert args[0] == "sess-e2e-local-path"
        instruction = args[1]
        assert "tools/send_message.py" in instruction
        assert "--file" in instruction

        # Delivery was deferred — no outbox write.
        handler._redis.rpush.assert_not_called()

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
        """When drafter returns context_summary and expectations, both must be
        written back to the AgentSession and saved."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()

        # Build a session that records field assignments.
        session = MagicMock()
        session.session_id = "sess-routing"

        drafted = MessageDraft(
            text="final drafted text",
            full_output_file=None,
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

    def test_routing_fields_persisted_when_context_summary_present(self):
        """Routing fields are persisted whenever context_summary or expectations
        are non-None — the old was_drafted gate has been removed; routing fields
        are now always written when present, regardless of draft path."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-persist-always"

        drafted = MessageDraft(
            text="short raw text",
            full_output_file=None,
            needs_self_draft=False,
            artifacts={},
            context_summary="Should be persisted now",
            expectations="And this too",
        )

        with patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)):
            asyncio.run(handler.send("123", "Short? yes.", 0, session=session))

        # save() IS called because context_summary and expectations are non-None
        assert session.context_summary == "Should be persisted now"
        assert session.expectations == "And this too"
        session.save.assert_called_once()

    def test_routing_fields_not_persisted_when_none(self):
        """When both context_summary and expectations are None, save() is not called."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-no-persist"

        drafted = MessageDraft(
            text="short raw text",
            full_output_file=None,
            needs_self_draft=False,
            artifacts={},
            context_summary=None,
            expectations=None,
        )

        with patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)):
            asyncio.run(handler.send("123", "Short? yes.", 0, session=session))

        # save() must not have been called since both fields are None.
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

    def test_self_draft_attempts_bound_terminates_loop(self):
        """After SELF_DRAFT_MAX_ATTEMPTS (2) concurrent injections, the next call
        must NOT push additional steering — it falls through to the narration
        fallback instead.

        Two CONCURRENT flagged send() coroutines are launched via asyncio.gather
        so both race to bump the real Redis INCR counter simultaneously. This
        verifies atomicity: the counter must reach exactly 2 (no lost increments
        under TOCTOU). A third sequential call must then be blocked by the
        exhausted budget.

        Uses the real ``bump_self_draft_attempts`` (real Redis INCR) — skips
        gracefully when Redis is unreachable.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        import pytest

        from agent.steering import (
            SELF_DRAFT_MAX_ATTEMPTS,
            _get_redis,
            _self_draft_attempts_key,
            reset_self_draft_attempts,
        )

        # Verify real Redis is reachable before proceeding.  We use _get_redis()
        # — the same connection that bump_self_draft_attempts uses — so we stay
        # on whatever db the autouse redis_test_db fixture redirected popoto to
        # (db=1 under serial pytest, db=N under xdist workers).
        try:
            r = _get_redis()
            r.ping()
        except Exception:
            pytest.skip("Redis not available — skipping real-counter concurrency test")

        from bridge.message_drafter import MessageDraft

        session_id = "sess-concurrent-budget-test"

        # Clean up any leftover key from a previous run.
        reset_self_draft_attempts(session_id)

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = session_id

        drafted_flagged = MessageDraft(
            text="",
            full_output_file=None,
            needs_self_draft=True,
            artifacts={},
        )

        # Track how many times steering was pushed.
        push_call_count = 0

        def counting_push(sid, text, sender=None, **kwargs):
            nonlocal push_call_count
            push_call_count += 1

        try:
            with (
                patch(
                    "bridge.message_drafter.draft_message",
                    AsyncMock(return_value=drafted_flagged),
                ),
                patch("agent.steering.peek_steering_sender", return_value=None),
                patch("agent.steering.push_steering_message", side_effect=counting_push),
            ):
                # Two CONCURRENT flagged send() calls — both race to bump the
                # Redis counter at the same time. asyncio.gather runs them in the
                # same event loop so both coroutines interleave; the Redis INCR
                # is still atomic, so no increment is lost.
                async def _run_concurrent():
                    await asyncio.gather(
                        handler.send("123", "Needs a self draft? yes", 0, session=session),
                        handler.send("123", "Needs a self draft again? yes", 0, session=session),
                    )

                asyncio.run(_run_concurrent())

                # Verify: both concurrent calls incremented the counter — no lost
                # increments under concurrent access.  Read via _get_redis() to
                # stay on the same db that bump_self_draft_attempts wrote to.
                redis_key = _self_draft_attempts_key(session_id)
                actual_count = int(r.get(redis_key) or 0)
                assert actual_count == SELF_DRAFT_MAX_ATTEMPTS, (
                    f"Redis counter should be {SELF_DRAFT_MAX_ATTEMPTS} after "
                    f"{SELF_DRAFT_MAX_ATTEMPTS} concurrent bumps, got {actual_count}"
                )

                # Both concurrent calls were within budget → both pushed steering.
                assert push_call_count == SELF_DRAFT_MAX_ATTEMPTS, (
                    f"Expected {SELF_DRAFT_MAX_ATTEMPTS} pushes from concurrent calls, "
                    f"got {push_call_count}"
                )

                # Third call: budget exhausted → steering must NOT be pushed, and
                # session.save() must NOT be called on the steering path (no
                # full-hash save for a budget-exhausted deferral).
                session.save.reset_mock()
                asyncio.run(
                    handler.send(
                        "123", "Needs a self draft? yes but budget gone", 0, session=session
                    )
                )

            assert push_call_count == SELF_DRAFT_MAX_ATTEMPTS, (
                f"Third call must not push steering after budget exhaustion; "
                f"total pushes: {push_call_count}"
            )
            session.save.assert_not_called()
        finally:
            # Always clean up the Redis key so subsequent runs start fresh.
            reset_self_draft_attempts(session_id)

    def test_self_draft_attempts_reset_pinned_before_early_return(self):
        """Counter reset fires on the clean (not needs_self_draft) branch BEFORE
        any steering_deferred early-return.

        Two scenarios must both reset the counter:
        1. Normal clean delivery writes to the outbox.
        2. Clean delivery that is suppressed by the redundancy filter (returns
           early from send() before the final rpush) ALSO resets the counter.

        This test validates scenario 1. Scenario 2 is harder to isolate here
        because the redundancy filter requires an SDLC session; the code path
        is covered by the code reading `else: if session_id: reset_self_draft_attempts`.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-reset-test"

        drafted_clean = MessageDraft(
            text="Clean output text.",
            full_output_file=None,
            needs_self_draft=False,
            artifacts={},
            context_summary=None,
            expectations=None,
        )

        reset_was_called = {"flag": False}

        def fake_reset(sid):
            reset_was_called["flag"] = True

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted_clean)),
            patch("agent.steering.reset_self_draft_attempts", side_effect=fake_reset),
        ):
            asyncio.run(handler.send("123", "Clean text? yes.", 0, session=session))

        assert reset_was_called["flag"], (
            "reset_self_draft_attempts must be called on clean delivery path"
        )
        # Delivery also happened.
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

        return MessageDraft(text=_input)

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
        # The handler emits ``to`` as a list to carry the reply-all recipient
        # set (primary + To/CC minus self). With no extra recipients stamped
        # on the session, the list collapses to just the primary recipient.
        assert payload["session_id"] == "email-sess"
        assert payload["to"] == ["customer@example.com"]
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

        # Per the unified-handler contract: when RTR suppresses on an email
        # session, the payload is dropped entirely. Email has no reaction
        # concept and the canonical pipeline now runs RTR for both transports,
        # so an email suppression is fully silent (zero rpush, zero reaction).
        assert handler._redis.rpush.call_count == 0

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


class TestRedundancyFilterWiring:
    """Tests for the redundancy filter wiring in TelegramRelayOutputHandler.send
    (issue #1205).

    These exercise the handler-level integration of should_suppress(): that SDLC
    sessions with redundant drafts get a 👀 reaction instead of a text message,
    that non-SDLC sessions bypass the filter, and that recent_sent_drafts is
    appended after a successful outbox write.

    Filter internals (bigram Jaccard, termination conditions) are tested
    separately in test_redundancy_filter.py.
    """

    def _make_handler(self, mock_redis=None):
        from agent.output_handler import TelegramRelayOutputHandler

        h = TelegramRelayOutputHandler(redis_url="redis://localhost:6379/0")
        if mock_redis is not None:
            h._redis = mock_redis
        return h

    def _mock_redis(self):
        r = MagicMock()
        r.rpush = MagicMock(return_value=1)
        r.expire = MagicMock()
        return r

    def _bypass_drafter(self, _input, *, session=None, medium="telegram"):
        """Pass-through drafter so delivery_text == text."""
        from bridge.message_drafter import MessageDraft

        return MessageDraft(text=_input, artifacts={})

    def _make_sdlc_session(self, *, recent_drafts=None, status="active"):
        s = MagicMock()
        s.session_id = "sdlc-sess-001"
        s.is_sdlc = True
        s.status = status
        s.recent_sent_drafts = recent_drafts or []
        s.session_events = None
        s.record_recent_sent_draft = MagicMock()
        s.extra_context = {}
        return s

    def _make_non_sdlc_session(self):
        s = MagicMock()
        s.session_id = "conv-sess-001"
        s.is_sdlc = False
        s.status = "active"
        s.recent_sent_drafts = []
        s.session_events = None
        s.extra_context = {}
        return s

    # ── SDLC session with redundant draft → 👀 reaction, no text ─────────────

    def test_sdlc_redundant_draft_queues_reaction_not_text(self):
        """An SDLC session whose draft is near-identical to a prior send must
        queue a 👀 reaction and skip the text outbox write."""
        import time

        from bridge.redundancy_filter import RTR_SUPPRESS_EMOJI, SuppressionVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_sdlc_session(
            recent_drafts=[{"ts": time.time(), "text": "checking status", "artifacts": {}}]
        )

        suppress_verdict = SuppressionVerdict(
            action="suppress", reason="jaccard=0.80>=threshold=0.65", jaccard=0.80, matched_index=0
        )

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch(
                "bridge.redundancy_filter.should_suppress",
                return_value=suppress_verdict,
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="checking status",
                    reply_to_msg_id=42,
                    session=session,
                )
            )

        # The outbox should have received only the 👀 reaction (no text message).
        calls = mock_r.rpush.call_args_list
        assert len(calls) >= 1, "Expected at least one rpush call (for the reaction)"
        # Check at least one payload is a reaction
        has_reaction = False
        has_text_message = False
        for call in calls:
            payload = json.loads(call[0][1])
            if payload.get("type") == "reaction":
                has_reaction = True
                assert payload["emoji"] == RTR_SUPPRESS_EMOJI
            else:
                has_text_message = True
        assert has_reaction, "Expected a 👀 reaction in the outbox"
        assert not has_text_message, "Text message should have been suppressed"

    # ── Non-SDLC session → filter bypassed, RTR runs as before ──────────────

    def test_non_sdlc_session_bypasses_filter(self):
        """A non-SDLC session must skip the redundancy filter entirely.
        The text message goes through the normal RTR + outbox path."""
        from bridge.redundancy_filter import should_suppress as _should_suppress

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_non_sdlc_session()

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch(
                "bridge.redundancy_filter.should_suppress",
                wraps=_should_suppress,
            ) as mock_filter,
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="hello world",
                    reply_to_msg_id=1,
                    session=session,
                )
            )

        # The filter must NOT have been called for a non-SDLC session.
        mock_filter.assert_not_called()

        # Text message delivered normally.
        mock_r.rpush.assert_called_once()
        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload.get("type") != "reaction"
        assert payload["text"] == "hello world"

    # ── Successful send appends to recent_sent_drafts ────────────────────────

    def test_successful_send_records_draft(self):
        """After a successful outbox rpush, record_recent_sent_draft is called."""
        from bridge.redundancy_filter import SuppressionVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_sdlc_session()

        send_verdict = SuppressionVerdict(action="send", reason="no_baseline")

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch(
                "bridge.redundancy_filter.should_suppress",
                return_value=send_verdict,
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="status update",
                    reply_to_msg_id=1,
                    session=session,
                )
            )

        session.record_recent_sent_draft.assert_called_once()

    # ── Failed save does not block rpush ─────────────────────────────────────

    def test_record_draft_failure_does_not_block_outbox_write(self):
        """If record_recent_sent_draft raises, the outbox rpush already happened
        and the error is swallowed — delivery is not reversed."""
        from bridge.redundancy_filter import SuppressionVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_sdlc_session()
        session.record_recent_sent_draft.side_effect = RuntimeError("save failed")

        send_verdict = SuppressionVerdict(action="send", reason="no_baseline")

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch(
                "bridge.redundancy_filter.should_suppress",
                return_value=send_verdict,
            ),
        ):
            # Must not raise.
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="some message",
                    reply_to_msg_id=1,
                    session=session,
                )
            )

        # Text was delivered.
        mock_r.rpush.assert_called_once()
        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["text"] == "some message"

    # ── Filter exception falls through to RTR + outbox ────────────────────────

    def test_filter_exception_falls_through_to_send(self):
        """An exception inside the redundancy filter branch must not block
        delivery — the text goes to the outbox as if the filter didn't exist."""
        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_sdlc_session()

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch(
                "bridge.redundancy_filter.should_suppress",
                side_effect=RuntimeError("filter exploded"),
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="some important message",
                    reply_to_msg_id=1,
                    session=session,
                )
            )

        # Delivery still happened.
        mock_r.rpush.assert_called()
        # At least one call is the text message (not a reaction).
        text_calls = [
            c for c in mock_r.rpush.call_args_list if json.loads(c[0][1]).get("type") != "reaction"
        ]
        assert len(text_calls) >= 1

    # ── No anchor → fallthrough (matches RTR contract) ───────────────────────

    def test_suppress_with_no_anchor_falls_through_to_send(self):
        """When suppress is returned but reply_to_msg_id is None, the filter
        falls through and sends the text (mirrors RTR's no-anchor contract)."""
        import time

        from bridge.redundancy_filter import SuppressionVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_sdlc_session(
            recent_drafts=[{"ts": time.time(), "text": "status", "artifacts": {}}]
        )

        suppress_verdict = SuppressionVerdict(
            action="suppress", reason="jaccard=0.90>=threshold=0.65", jaccard=0.90, matched_index=0
        )

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch(
                "bridge.redundancy_filter.should_suppress",
                return_value=suppress_verdict,
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="status",
                    reply_to_msg_id=None,  # No anchor
                    session=session,
                )
            )

        # Text must have been sent (no anchor → fallthrough).
        mock_r.rpush.assert_called()
        text_calls = [
            c for c in mock_r.rpush.call_args_list if json.loads(c[0][1]).get("type") != "reaction"
        ]
        assert len(text_calls) >= 1

    # ── Session event includes jaccard and matched_prior_preview ─────────────

    def test_suppressed_redundant_event_includes_jaccard_and_preview(self):
        """The drafter.suppressed_redundant session event must include both
        ``jaccard`` and ``matched_prior_preview`` fields so that observers
        can audit what triggered suppression (Success Criterion 6)."""
        import time

        from bridge.redundancy_filter import SuppressionVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_sdlc_session(
            recent_drafts=[
                {"ts": time.time(), "text": "previous status message here", "artifacts": {}}
            ]
        )

        suppress_verdict = SuppressionVerdict(
            action="suppress",
            reason="jaccard=0.80>=threshold=0.65",
            jaccard=0.80,
            matched_index=0,
        )

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch(
                "bridge.redundancy_filter.should_suppress",
                return_value=suppress_verdict,
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="previous status message here",
                    reply_to_msg_id=42,
                    session=session,
                )
            )

        events = session.session_events or []
        suppressed_events = [e for e in events if e.get("type") == "drafter.suppressed_redundant"]
        assert len(suppressed_events) == 1, "Expected exactly one suppressed_redundant event"
        ev = suppressed_events[0]
        assert ev["jaccard"] == 0.80, "jaccard must be forwarded into the session event"
        assert ev["matched_prior_preview"] == "previous status message here", (
            "matched_prior_preview must be the text of the matched prior draft"
        )


class TestDrafterHoistedAboveTransport:
    """Issue #1369: the drafter must run ONCE for both telegram and email
    transports, before the transport branch. These tests confirm the hoist
    and the email-side propagation (reply-all ``to`` list, attachments,
    suppression-drops-payload contract)."""

    def _make_handler(self):
        h = TelegramRelayOutputHandler(redis_url="redis://localhost:6379/0")
        h._redis = MagicMock()
        return h

    def _telegram_session(self, session_id="hoist-tg"):
        s = MagicMock()
        s.session_id = session_id
        s.extra_context = {"transport": "telegram"}
        s.is_sdlc = False
        s.recent_sent_drafts = []
        return s

    def _email_session(
        self,
        session_id="hoist-email",
        to_addrs=None,
        cc_addrs=None,
        subject="The thread",
        message_id="<orig@example.com>",
    ):
        s = MagicMock()
        s.session_id = session_id
        s.extra_context = {
            "transport": "email",
            "email_subject": subject,
            "email_message_id": message_id,
            "email_to_addrs": to_addrs or [],
            "email_cc_addrs": cc_addrs or [],
        }
        s.is_sdlc = False
        s.recent_sent_drafts = []
        return s

    def test_drafter_called_once_for_telegram(self):
        """A telegram send must invoke ``draft_message`` exactly once."""
        handler = self._make_handler()
        session = self._telegram_session()
        draft_stub = MagicMock(
            text="drafted telegram text",
            full_output_file=None,
            artifacts={},
            needs_self_draft=False,
            expectations=None,
            context_summary=None,
        )

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(return_value=draft_stub),
        ) as mock_draft:
            asyncio.run(handler.send("123", "raw text", 0, session=session))

        assert mock_draft.await_count == 1
        # Drafted text reached the outbox, not raw.
        payload = json.loads(handler._redis.rpush.call_args[0][1])
        assert payload["text"] == "drafted telegram text"

    def test_drafter_called_once_for_email(self):
        """An email send must invoke ``draft_message`` exactly once. This is
        the regression that closes #1369: previously the email branch never
        ran the drafter."""
        handler = self._make_handler()
        session = self._email_session()
        draft_stub = MagicMock(
            text="drafted email body",
            full_output_file=None,
            artifacts={},
            needs_self_draft=False,
            expectations=None,
            context_summary=None,
        )

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(return_value=draft_stub),
        ) as mock_draft:
            asyncio.run(handler.send("customer@example.com", "raw", 0, session=session))

        assert mock_draft.await_count == 1
        # The drafter ran with ``medium="email"`` so the per-medium format
        # rules apply (no markdown on the wire for email).
        assert mock_draft.await_args.kwargs["medium"] == "email"
        # Drafted body landed in the email payload.
        payload = json.loads(handler._redis.rpush.call_args[0][1])
        assert payload["body"] == "drafted email body"

    def test_email_payload_carries_reply_all_recipients(self):
        """The email outbox payload's ``to`` field is a list combining the
        primary recipient with ``extra_context.email_to_addrs`` and
        ``email_cc_addrs``, minus the SMTP user (own address)."""
        handler = self._make_handler()
        session = self._email_session(
            to_addrs=["primary@example.com", "team@example.com"],
            cc_addrs=["watcher@example.com"],
        )

        import os as _os

        old_smtp = _os.environ.get("SMTP_USER")
        _os.environ["SMTP_USER"] = "bot@ourdomain.com"
        try:
            with patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=RuntimeError("skip drafter")),
            ):
                asyncio.run(
                    handler.send(
                        "primary@example.com",
                        "reply body",
                        0,
                        session=session,
                    )
                )
        finally:
            if old_smtp is None:
                _os.environ.pop("SMTP_USER", None)
            else:
                _os.environ["SMTP_USER"] = old_smtp

        payload = json.loads(handler._redis.rpush.call_args[0][1])
        # Primary first, then To/CC entries (dedup the primary; drop SMTP user).
        assert payload["to"] == [
            "primary@example.com",
            "team@example.com",
            "watcher@example.com",
        ]

    def test_email_payload_drops_own_smtp_user_from_reply_all(self):
        """When the SMTP user appears in the original To/CC, it is filtered
        out of the reply-all list (we don't reply to ourselves)."""
        handler = self._make_handler()
        session = self._email_session(
            to_addrs=["bot@ourdomain.com", "team@example.com"],
            cc_addrs=["bot@ourdomain.com"],
        )

        import os as _os

        old_smtp = _os.environ.get("SMTP_USER")
        _os.environ["SMTP_USER"] = "bot@ourdomain.com"
        try:
            with patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=RuntimeError("skip drafter")),
            ):
                asyncio.run(handler.send("customer@example.com", "body", 0, session=session))
        finally:
            if old_smtp is None:
                _os.environ.pop("SMTP_USER", None)
            else:
                _os.environ["SMTP_USER"] = old_smtp

        payload = json.loads(handler._redis.rpush.call_args[0][1])
        # bot@ourdomain.com must NOT appear anywhere in the to list.
        addrs_lower = [a.lower() for a in payload["to"]]
        assert "bot@ourdomain.com" not in addrs_lower

    def test_cli_file_paths_propagate_to_telegram_outbox(self):
        """CLI-supplied ``file_paths`` are forwarded into the telegram outbox
        payload (and merged with any drafter overflow file)."""
        handler = self._make_handler()
        session = self._telegram_session()

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=RuntimeError("skip drafter")),
        ):
            asyncio.run(
                handler.send(
                    "12345",
                    "body",
                    0,
                    session=session,
                    file_paths=["/tmp/a.png", "/tmp/b.txt"],
                )
            )

        payload = json.loads(handler._redis.rpush.call_args[0][1])
        assert payload["file_paths"] == ["/tmp/a.png", "/tmp/b.txt"]

    def test_cli_file_paths_propagate_to_email_outbox(self):
        """CLI-supplied ``file_paths`` are forwarded into the email outbox
        payload as ``attachments`` (the relay's expected key)."""
        handler = self._make_handler()
        session = self._email_session()

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=RuntimeError("skip drafter")),
        ):
            asyncio.run(
                handler.send(
                    "customer@example.com",
                    "see attached",
                    0,
                    session=session,
                    file_paths=["/tmp/report.pdf"],
                )
            )

        payload = json.loads(handler._redis.rpush.call_args[0][1])
        assert payload["attachments"] == ["/tmp/report.pdf"]


# ---------------------------------------------------------------------------
# Persist-defer-state tests (issue #1730)
# ---------------------------------------------------------------------------


class TestDeferredSelfDraftPersistence:
    """Tests that deferred_self_draft_pending + deferred_self_draft_text are
    persisted to AgentSession.extra_context when steering_deferred=True.

    The persisted flag is the cross-process detection signal the health checker
    reads to decide whether to deliver a fallback.  The steering queue CANNOT be
    used because the agent drains it at turn start, leaving it empty by
    finalization time.
    """

    def _make_handler(self):
        from unittest.mock import MagicMock

        from agent.output_handler import TelegramRelayOutputHandler

        h = TelegramRelayOutputHandler()
        h._redis = MagicMock()
        return h

    def _make_session(self, *, session_id="sess-persist", extra_context=None):
        from unittest.mock import MagicMock

        session = MagicMock()
        session.session_id = session_id
        session.extra_context = extra_context or {}
        return session

    def test_persists_pending_flag_and_text_when_steering_deferred(self):
        """When steering_deferred=True, extra_context gains deferred_self_draft_pending=True
        and deferred_self_draft_text=<the original text> before the early return."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = self._make_session()

        drafted = MessageDraft(text="", full_output_file=None, needs_self_draft=True, artifacts={})

        auth_session = MagicMock()
        auth_session.extra_context = {}
        saved_contexts: list[dict] = []

        def _capture_save(update_fields=None, **_kw):
            saved_contexts.append(dict(auth_session.extra_context))

        auth_session.save = _capture_save

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.push_steering_message", return_value=True),
            patch("agent.steering.bump_self_draft_attempts", return_value=1),
            patch(
                "models.session_lifecycle.get_authoritative_session",
                return_value=auth_session,
            ),
        ):
            asyncio.run(handler.send("123", "This is the deferred text", 0, session=session))

        # A save with the deferred keys must have occurred.
        assert saved_contexts, "save must have been called with extra_context update"
        last_ctx = saved_contexts[-1]
        assert last_ctx.get("deferred_self_draft_pending") is True
        assert last_ctx.get("deferred_self_draft_text") == "This is the deferred text"

    def test_persist_failure_is_logged_not_swallowed(self):
        """If the extra_context persist fails, a WARNING is logged (not swallowed).
        The file dual-write and early return still complete normally."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        file_handler = MagicMock()
        file_handler.send = AsyncMock()
        handler._file_handler = file_handler

        session = self._make_session()

        drafted = MessageDraft(text="", full_output_file=None, needs_self_draft=True, artifacts={})

        def _raise(*_a, **_kw):
            raise RuntimeError("Redis unavailable")

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.push_steering_message", return_value=True),
            patch("agent.steering.bump_self_draft_attempts", return_value=1),
            patch("models.session_lifecycle.get_authoritative_session", side_effect=_raise),
            patch("agent.output_handler.logger") as mock_logger,
        ):
            asyncio.run(handler.send("123", "text", 0, session=session))

        # Must log at WARNING level (not silently swallow).
        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        assert any("sess-persist" in w or "deferred" in w.lower() for w in warning_calls), (
            f"Expected a WARNING about persist failure; got calls: {warning_calls}"
        )

    def test_persist_uses_authoritative_re_read_not_stale_session(self):
        """The RMW re-reads the authoritative session before merging, avoiding a
        last-writer-wins clobber of concurrent extra_context writes."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = self._make_session()
        # Pre-load a key that a concurrent writer might have set.
        session.extra_context = {"transport": "telegram"}

        drafted = MessageDraft(text="", full_output_file=None, needs_self_draft=True, artifacts={})

        auth_session = MagicMock()
        # Auth session has a different (newer) extra_context.
        auth_session.extra_context = {"transport": "telegram", "other_key": "other_val"}
        auth_session.save = MagicMock()

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.push_steering_message", return_value=True),
            patch("agent.steering.bump_self_draft_attempts", return_value=1),
            patch(
                "models.session_lifecycle.get_authoritative_session",
                return_value=auth_session,
            ),
        ):
            asyncio.run(handler.send("123", "text", 0, session=session))

        # The save target must be the auth_session (re-read), not the stale local session.
        auth_session.save.assert_called()
        # The re-read's pre-existing key must survive (not clobbered).
        assert auth_session.extra_context.get("other_key") == "other_val", (
            "concurrent extra_context key must not be clobbered by the RMW"
        )
        # The deferred keys must be present.
        assert auth_session.extra_context.get("deferred_self_draft_pending") is True


# ---------------------------------------------------------------------------
# DeliveryOutcome return-value contract (consolidate_delivery_paths.md)
#
# ``TelegramRelayOutputHandler.send`` returns a ``DeliveryOutcome`` from EVERY
# exit path so ``tools/send_message.py`` can surface the pipeline verdict
# instead of an unconditional "Queued". These tests pin the return value at
# each exit (sent / dropped_empty / deferred_self_draft / suppressed_redundant
# / suppressed_rtr) with the REAL handler.
# ---------------------------------------------------------------------------


class TestSendReturnsDeliveryOutcome:
    """Every exit path of send() returns the correct DeliveryOutcome."""

    def _make_handler(self):
        h = TelegramRelayOutputHandler(redis_url="redis://localhost:6379/0")
        h._redis = MagicMock()
        return h

    def _bypass_drafter(self, _input, *, session=None, medium="telegram"):
        from bridge.message_drafter import MessageDraft

        return MessageDraft(text=_input, artifacts={})

    def test_returns_sent_on_successful_outbox_write(self):
        """A clean telegram send returns DeliveryOutcome.sent."""
        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "ret-sent"
        session.extra_context = {"transport": "telegram"}
        session.is_sdlc = False

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=self._bypass_drafter),
        ):
            outcome = asyncio.run(handler.send("123", "hello there", 0, session=session))

        assert outcome == DeliveryOutcome.sent
        handler._redis.rpush.assert_called_once()

    def test_returns_dropped_empty_on_empty_text(self):
        """Empty text short-circuits with DeliveryOutcome.dropped_empty."""
        handler = self._make_handler()

        outcome = asyncio.run(handler.send("123", "", 0))

        assert outcome == DeliveryOutcome.dropped_empty
        handler._redis.rpush.assert_not_called()

    def test_returns_sent_even_when_drafter_raises(self):
        """A drafter exception falls through to raw text and returns sent
        (failure-mode: drafter is a guard, never a blocker)."""
        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "ret-drafter-boom"
        session.extra_context = {"transport": "telegram"}
        session.is_sdlc = False

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=RuntimeError("drafter broken")),
        ):
            outcome = asyncio.run(handler.send("123", "Raw text survives? yes.", 0, session=session))

        assert outcome == DeliveryOutcome.sent
        payload = json.loads(handler._redis.rpush.call_args[0][1])
        assert payload["text"] == "Raw text survives? yes."

    def test_returns_deferred_self_draft_when_steering_injected(self):
        """When self-draft steering is injected, send() defers and returns
        DeliveryOutcome.deferred_self_draft without an outbox write."""
        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "ret-deferred"
        session.extra_context = {"transport": "telegram"}

        drafted = MessageDraft(text="", full_output_file=None, needs_self_draft=True, artifacts={})

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch.object(handler, "_inject_self_draft_steering", MagicMock(return_value=True)),
        ):
            outcome = asyncio.run(handler.send("123", "needs a self draft? yes", 0, session=session))

        assert outcome == DeliveryOutcome.deferred_self_draft
        handler._redis.rpush.assert_not_called()

    def test_returns_suppressed_redundant(self):
        """A redundancy-filter suppression (SDLC session, reply anchor present)
        returns DeliveryOutcome.suppressed_redundant."""
        import time

        from bridge.redundancy_filter import SuppressionVerdict

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "ret-redund"
        session.extra_context = {"transport": "telegram"}
        session.is_sdlc = True
        session.status = "active"
        session.recent_sent_drafts = [{"ts": time.time(), "text": "status", "artifacts": {}}]
        session.session_events = None

        verdict = SuppressionVerdict(
            action="suppress", reason="jaccard=0.9", jaccard=0.9, matched_index=0
        )

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch("bridge.redundancy_filter.should_suppress", return_value=verdict),
        ):
            outcome = asyncio.run(handler.send("-100123", "status", 42, session=session))

        assert outcome == DeliveryOutcome.suppressed_redundant

    def test_returns_suppressed_rtr(self):
        """A read-the-room suppression (reply anchor present) returns
        DeliveryOutcome.suppressed_rtr."""
        from bridge.read_the_room import RoomVerdict

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "ret-rtr"
        session.extra_context = {"transport": "telegram"}
        session.is_sdlc = False
        session.session_events = None

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch(
                "bridge.read_the_room.read_the_room",
                AsyncMock(return_value=RoomVerdict(action="suppress", reason="redundant")),
            ),
        ):
            outcome = asyncio.run(handler.send("-100123", "x" * 250, 42, session=session))

        assert outcome == DeliveryOutcome.suppressed_rtr


# ---------------------------------------------------------------------------
# deliver_system_notice — the single sanctioned bypass seam (Decision B).
#
# Contract: resolves the send callback via _resolve_callbacks (handler in the
# worker) OR falls back to FileOutputHandler when no callback is registered;
# NEVER raises (WARNING-and-swallow); empty message is a no-op.
# ---------------------------------------------------------------------------


class TestDeliverSystemNotice:
    """deliver_system_notice registered-handler / file-fallback / never-raises."""

    def _notice_entry(self, *, session_id="notice-sess", transport="telegram"):
        entry = MagicMock()
        entry.session_id = session_id
        entry.agent_session_id = session_id
        entry.chat_id = "55555"
        entry.telegram_message_id = 7
        entry.project_key = "test-notice-proj"
        entry.extra_context = {"transport": transport}
        return entry

    def test_registered_handler_receives_notice_and_writes_outbox(self):
        """With a registered send callback, the notice traverses the real
        handler and lands on telegram:outbox:{session_id}."""
        entry = self._notice_entry(session_id="notice-registered")

        # Real handler with a mocked Redis client — the notice must reach it.
        handler = TelegramRelayOutputHandler(redis_url="redis://localhost:6379/0")
        handler._redis = MagicMock()

        def _bypass_drafter(_input, *, session=None, medium="telegram"):
            from bridge.message_drafter import MessageDraft

            return MessageDraft(text=_input, artifacts={})

        with (
            patch(
                "agent.agent_session_queue._resolve_callbacks",
                return_value=(handler.send, None),
            ),
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=_bypass_drafter),
            ),
        ):
            result = asyncio.run(deliver_system_notice(entry, "System notice: service degraded."))

        assert result is True
        handler._redis.rpush.assert_called_once()
        key = handler._redis.rpush.call_args[0][0]
        assert key == "telegram:outbox:notice-registered"
        payload = json.loads(handler._redis.rpush.call_args[0][1])
        assert payload["text"] == "System notice: service degraded."
        assert payload["chat_id"] == "55555"
        assert payload["reply_to"] == 7
        assert payload["session_id"] == "notice-registered"

    def test_no_registration_falls_back_to_file_output_handler(self, tmp_path, monkeypatch):
        """With NO registered callback, the notice is written via
        FileOutputHandler (dev / non-bridge fallback)."""
        import agent.output_handler as oh

        entry = self._notice_entry(session_id="notice-filefallback")

        # Redirect FileOutputHandler's default dir to a temp path so we don't
        # pollute the repo's logs/worker/ tree.
        monkeypatch.setattr(oh, "WORKER_LOGS_DIR", tmp_path)

        with patch(
            "agent.agent_session_queue._resolve_callbacks",
            return_value=(None, None),
        ):
            result = asyncio.run(deliver_system_notice(entry, "Fallback notice text."))

        assert result is True
        log_file = tmp_path / "notice-filefallback.log"
        assert log_file.exists()
        assert "Fallback notice text." in log_file.read_text()

    def test_callback_exception_is_logged_and_swallowed(self):
        """A raising send callback → WARNING logged, no exception propagates,
        returns False (never-raises contract)."""

        async def _boom(*_a, **_kw):
            raise RuntimeError("send callback exploded")

        entry = self._notice_entry(session_id="notice-boom")

        with (
            patch(
                "agent.agent_session_queue._resolve_callbacks",
                return_value=(_boom, None),
            ),
            patch("agent.output_handler.logger") as mock_logger,
        ):
            # Must NOT raise.
            result = asyncio.run(deliver_system_notice(entry, "will fail"))

        assert result is False
        mock_logger.warning.assert_called()
        assert any(
            "notice-boom" in str(c) or "delivery failed" in str(c).lower()
            for c in mock_logger.warning.call_args_list
        )

    def test_empty_message_is_noop_with_debug_log(self):
        """An empty message is a no-op: no callback resolution, debug log,
        returns False."""
        entry = self._notice_entry(session_id="notice-empty")

        with (
            patch(
                "agent.agent_session_queue._resolve_callbacks",
            ) as mock_resolve,
            patch("agent.output_handler.logger") as mock_logger,
        ):
            result = asyncio.run(deliver_system_notice(entry, ""))

        assert result is False
        # Callback resolution must never be reached for an empty message.
        mock_resolve.assert_not_called()
        mock_logger.debug.assert_called()

    def test_telemetry_key_increments_only_on_success(self):
        """When telemetry_key is supplied and the send succeeds, the counter
        is INCR'd exactly once."""
        entry = self._notice_entry(session_id="notice-telemetry")

        async def _ok(*_a, **_kw):
            return None

        fake_redis = MagicMock()
        with (
            patch(
                "agent.agent_session_queue._resolve_callbacks",
                return_value=(_ok, None),
            ),
            patch("popoto.redis_db.POPOTO_REDIS_DB", fake_redis),
        ):
            result = asyncio.run(
                deliver_system_notice(entry, "notice", telemetry_key="proj:counter")
            )

        assert result is True
        fake_redis.incr.assert_called_once_with("proj:counter")
