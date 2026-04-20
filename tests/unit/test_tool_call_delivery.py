"""Tests for the tool-call delivery contract (plan #1035 §D4).

Covers classify_delivery_outcome's five outcomes:
  send | react | continue | silent (+ legacy send_telegram alias for "send")

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

    def test_legacy_send_telegram_still_classifies_as_send(self):
        # The legacy tool path must remain valid for the transition window.
        tail = 'python tools/send_telegram.py "hello from the old tool"'
        assert classify_delivery_outcome(tail) == "send"

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
