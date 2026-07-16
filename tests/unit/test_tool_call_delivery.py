"""Tests for the tool-call delivery contract (plan #1035 §D4).

Covers classify_delivery_outcome's four outcomes:
  send | react | continue | silent

Also runs an end-to-end smoke of the stop_hook review gate:
- First stop with non-empty output → returns {"decision": "block"} and caches
  review state.
- Second stop with a send_message tool invocation → clears state and
  returns {} (review gate complete).

The transcript tails in these tests mimic the JSONL structure the Claude
Agent SDK writes, since stop_hook reads a raw chunk of transcript bytes
via _read_transcript_tail.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

from agent.hooks.stop import (
    _review_state,
    classify_delivery_outcome,
    stop_hook,
)


class TestClassifyDeliveryOutcome:
    """Five outcomes from plan §D4, one test each."""

    def test_send_as_is(self):
        # Transcript contains send_message.py with the draft text verbatim.
        draft = "All 42 tests passed, committed abc1234."
        tail = (
            '{"type":"tool_use","name":"Bash","input":{"command":'
            f'"python tools/send_message.py {draft!r}"' + "}}"
        )
        assert classify_delivery_outcome(tail) == "send"

    def test_edit_and_send(self):
        # Agent invoked send_message with DIFFERENT text than the draft.
        # classify_delivery_outcome doesn't compare text — presence of the
        # tool invocation alone = "send".
        tail = (
            '{"type":"tool_use","name":"Bash","input":{"command":'
            "\"python tools/send_message.py 'revised text — I edited the draft'\"}}"
        )
        assert classify_delivery_outcome(tail) == "send"

    def test_react(self):
        tail = (
            '{"type":"tool_use","name":"Bash","input":{"command":'
            '"python tools/react_with_emoji.py excited"}}'
        )
        assert classify_delivery_outcome(tail) == "react"

    def test_silent(self):
        # No send, no react, no other tool_use activity.
        tail = "the agent just emitted prose with no tool call whatsoever"
        assert classify_delivery_outcome(tail) == "silent"

    def test_continue(self):
        # Some other tool_use block (Bash grep) but no delivery tool.
        tail = '{"type":"tool_use","name":"Bash","input":{"command":"grep -r foo src/"}}'
        assert classify_delivery_outcome(tail) == "continue"

    def test_empty_transcript_is_silent(self):
        assert classify_delivery_outcome("") == "silent"

    def test_send_and_react_both_present_prefers_send(self):
        # If somehow both tools appear, "send" wins (it's checked first).
        tail = "python tools/send_message.py 'hi'\npython tools/react_with_emoji.py 'excited'"
        assert classify_delivery_outcome(tail) == "send"


# ---------------------------------------------------------------------------
# End-to-end smoke of stop_hook review gate (first stop → block, second
# stop with send tool_use → clear state + return empty dict).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_review_state():
    """Reset the module-level review state between tests."""
    _review_state.clear()
    yield
    _review_state.clear()


@pytest.fixture
def telegram_env(monkeypatch):
    """Mark the session as user-triggered so the review gate runs."""
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.delenv("EMAIL_REPLY_TO", raising=False)
    monkeypatch.delenv("VALOR_TRANSPORT", raising=False)
    yield


class TestStopHookReviewGateFlow:
    """First stop blocks with a draft; second stop (with send) clears the gate."""

    @pytest.mark.asyncio
    async def test_first_stop_blocks_with_draft(self, telegram_env):
        session_id = "smoke-session-first"
        # Write a transcript tail containing some substantive agent output.
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("All 42 tests passed. Committed abc1234 and pushed.\n")
            path = f.name
        try:
            input_data = {
                "session_id": session_id,
                "transcript_path": path,
            }

            # Mock draft_message and _is_child_session so we don't hit the
            # Haiku API or AgentSession.query.
            fake_draft = "All 42 tests passed (committed abc1234)."

            async def _fake_generate(*args, **kwargs):
                return fake_draft

            with (
                patch("agent.hooks.stop._is_child_session", return_value=False),
                patch(
                    "agent.hooks.stop._generate_draft",
                    new=AsyncMock(side_effect=_fake_generate),
                ),
                # Short-circuit the SDLC branch check — it tries to import
                # agent.sdk_client which is heavy and unrelated to this test.
                patch(
                    "agent.sdk_client._check_no_direct_main_push",
                    return_value=None,
                ),
            ):
                result = await stop_hook(input_data, tool_use_id=None, context=None)

            assert result.get("decision") == "block"
            assert "reason" in result
            # Draft text must appear in the review prompt the agent sees.
            assert fake_draft in result["reason"]
            assert "tools/send_message.py" in result["reason"]
            # Review state is now cached for this session
            assert session_id in _review_state
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_second_stop_with_send_clears_state(self, telegram_env):
        session_id = "smoke-session-second"
        # Pre-seed review state as if the first stop already ran.
        import time

        _review_state[session_id] = {
            "timestamp": time.time(),
            "draft": "previous draft",
            "medium": "telegram",
        }

        # Transcript tail showing the agent invoked send_message.py.
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(
                '{"type":"tool_use","name":"Bash","input":{"command":'
                "\"python tools/send_message.py 'the final message'\"}}\n"
            )
            path = f.name
        try:
            input_data = {
                "session_id": session_id,
                "transcript_path": path,
            }

            with (
                patch("agent.hooks.stop._is_child_session", return_value=False),
                patch(
                    "agent.sdk_client._check_no_direct_main_push",
                    return_value=None,
                ),
            ):
                result = await stop_hook(input_data, tool_use_id=None, context=None)

            # Gate cleared: empty dict, session removed from review state.
            assert result == {}
            assert session_id not in _review_state
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_second_stop_with_continue_blocks_and_resets(self, telegram_env):
        """When the agent kept working (other tool_use, no send/react), the gate
        blocks with a 'Resuming work' reason and resets the state so the NEXT
        stop re-enters the gate."""
        session_id = "smoke-session-continue"
        import time

        _review_state[session_id] = {
            "timestamp": time.time(),
            "draft": "previous draft",
            "medium": "telegram",
        }

        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write('{"type":"tool_use","name":"Bash","input":{"command":"grep -r foo src/"}}\n')
            path = f.name
        try:
            input_data = {
                "session_id": session_id,
                "transcript_path": path,
            }
            with (
                patch("agent.hooks.stop._is_child_session", return_value=False),
                patch(
                    "agent.sdk_client._check_no_direct_main_push",
                    return_value=None,
                ),
            ):
                result = await stop_hook(input_data, tool_use_id=None, context=None)

            assert result.get("decision") == "block"
            assert "Resuming work" in result["reason"]
            # Continue path resets state so next stop re-enters the gate.
            assert session_id not in _review_state
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Tool -> canonical-handler routing (issue #1369)
#
# These tests assert that ``tools/send_message.py`` ALWAYS routes through
# ``agent.output_handler.TelegramRelayOutputHandler.send`` for both telegram
# and email transports — there must be exactly one canonical handler call
# site, no raw rpush in the default tool path, and no import of the
# synchronous SMTP ``EmailOutputHandler`` from the tool process.
# ---------------------------------------------------------------------------


class TestToolCallHandlerRouting:
    """The tool process delegates to TelegramRelayOutputHandler.send for both
    transports. Each test mocks the handler and asserts call arity + kwargs.
    """

    def _stub_session(self, monkeypatch, session=object()):
        """Patch ``tools.send_message._lookup_session`` to return ``session``."""
        import tools.send_message as sm

        monkeypatch.setattr(sm, "_lookup_session", lambda sid: session)
        # Bypass the promise gate; it's tested elsewhere.
        from bridge import promise_gate

        monkeypatch.setattr(promise_gate, "cli_check_or_exit", lambda *a, **kw: None)

    def test_telegram_path_invokes_canonical_handler(self, monkeypatch):
        """``_send_via_telegram`` must call TelegramRelayOutputHandler.send
        exactly once with the chat_id, drafted text, reply-to int, the
        reconstituted session, and the CLI-supplied file_paths."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import tools.send_message as sm

        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setenv("TELEGRAM_REPLY_TO", "7")
        monkeypatch.setenv("VALOR_SESSION_ID", "sess-tg")
        monkeypatch.delenv("ALLOW_LEGACY_RPUSH_FALLBACK", raising=False)

        fake_session = MagicMock()
        fake_session.session_id = "sess-tg"
        fake_session.extra_context = {"transport": "telegram"}
        self._stub_session(monkeypatch, session=fake_session)

        mock_send = AsyncMock(return_value=None)
        with patch("agent.output_handler.TelegramRelayOutputHandler.send", new=mock_send):
            sm._send_via_telegram("hello world", None)

        mock_send.assert_awaited_once()
        args, kwargs = mock_send.call_args
        # When ``send`` is patched on the class, the bound-method call from
        # the tool's handler instance descriptor-binds ``self`` AHEAD of
        # AsyncMock, so call_args records (self, chat_id, text, reply_to_msg_id).
        # Confirm by searching for the chat_id wherever it landed.
        positional = list(args)
        assert "12345" in positional, f"chat_id missing from args: {positional}"
        assert "hello world" in positional, f"text missing from args: {positional}"
        assert 7 in positional, f"reply_to_msg_id missing from args: {positional}"
        assert kwargs["session"] is fake_session
        assert kwargs.get("file_paths") is None

    def test_email_path_invokes_same_canonical_handler(self, monkeypatch):
        """``_send_via_email`` must call THE SAME handler class
        (TelegramRelayOutputHandler.send) — NOT EmailOutputHandler.send.
        This asserts the single-canonical-handler convergence."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import tools.send_message as sm

        monkeypatch.setenv("VALOR_SESSION_ID", "sess-email")
        monkeypatch.setenv("EMAIL_REPLY_TO", "customer@example.com")
        monkeypatch.setenv("EMAIL_SUBJECT", "Re: hi")
        monkeypatch.delenv("ALLOW_LEGACY_RPUSH_FALLBACK", raising=False)

        fake_session = MagicMock()
        fake_session.session_id = "sess-email"
        fake_session.extra_context = {"transport": "email"}
        self._stub_session(monkeypatch, session=fake_session)

        mock_send = AsyncMock(return_value=None)
        with patch("agent.output_handler.TelegramRelayOutputHandler.send", new=mock_send):
            sm._send_via_email("hello via email", None)

        mock_send.assert_awaited_once()
        args, kwargs = mock_send.call_args
        positional = list(args)
        assert "customer@example.com" in positional, f"recipient missing from args: {positional}"
        assert "hello via email" in positional, f"text missing from args: {positional}"
        assert 0 in positional, f"reply_to_msg_id sentinel missing: {positional}"
        assert kwargs["session"] is fake_session

    def test_missing_session_fails_closed_by_default(self, monkeypatch):
        """Missing AgentSession + ALLOW_LEGACY_RPUSH_FALLBACK unset must
        cause a non-zero exit — the tool refuses to silently bypass the
        canonical handler."""
        import tools.send_message as sm

        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setenv("VALOR_SESSION_ID", "missing-sess")
        monkeypatch.delenv("ALLOW_LEGACY_RPUSH_FALLBACK", raising=False)
        monkeypatch.setattr(sm, "_lookup_session", lambda sid: None)
        from bridge import promise_gate

        monkeypatch.setattr(promise_gate, "cli_check_or_exit", lambda *a, **kw: None)

        with pytest.raises(SystemExit) as excinfo:
            sm._send_via_telegram("hi", None)
        assert excinfo.value.code != 0

    def test_missing_session_with_fallback_flag_uses_raw_rpush(self, monkeypatch):
        """Missing AgentSession + ALLOW_LEGACY_RPUSH_FALLBACK=1 must call the
        legacy raw-rpush helper and exit 0 (diagnostic opt-in path)."""
        from unittest.mock import patch

        import tools.send_message as sm

        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setenv("VALOR_SESSION_ID", "missing-sess")
        monkeypatch.setenv("ALLOW_LEGACY_RPUSH_FALLBACK", "1")
        monkeypatch.setattr(sm, "_lookup_session", lambda sid: None)
        from bridge import promise_gate

        monkeypatch.setattr(promise_gate, "cli_check_or_exit", lambda *a, **kw: None)

        with patch.object(sm, "_legacy_telegram_rpush") as mock_legacy:
            sm._send_via_telegram("hi", None)
            mock_legacy.assert_called_once()

    def test_tool_does_not_import_email_output_handler(self):
        """Static guarantee: ``tools/send_message.py`` must NOT reference
        EmailOutputHandler. The tool talks only to the queue-side handler;
        importing the synchronous SMTP handler would couple it to the wrong
        layer (per #1369 design decision)."""
        from pathlib import Path

        src = Path(__file__).resolve().parents[2] / "tools" / "send_message.py"
        contents = src.read_text()
        assert "EmailOutputHandler" not in contents, (
            "tools/send_message.py must not reference EmailOutputHandler — "
            "the canonical queue-side handler is TelegramRelayOutputHandler."
        )
