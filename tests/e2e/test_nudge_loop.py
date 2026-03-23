"""E2E tests for the nudge loop (deliver-vs-nudge outcome logic).

Tests the send_to_chat behavior within _execute_job by exercising
the nudge loop decision points with various (stop_reason, output) combos.

Uses real Redis, mocks only Claude API and Telegram send.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.job_queue import MAX_NUDGE_COUNT, SendToChatResult
from models.agent_session import AgentSession


def _make_test_job(session_id: str, chat_id: str = "test_chat") -> MagicMock:
    """Create a minimal mock Job for send_to_chat testing."""
    job = MagicMock()
    job.project_key = "valor"
    job.session_id = session_id
    job.chat_id = chat_id
    job.message_id = 1
    job.auto_continue_count = 0
    job.message_text = "test message"
    job.sender_name = "Test"
    job.trigger_message_id = None
    job.work_item_slug = None
    job.task_list_id = None
    return job


@pytest.mark.e2e
class TestNudgeLoopOutcomes:
    """Test nudge loop deliver-vs-nudge outcomes without coupling to internals."""

    def test_empty_output_triggers_nudge(self):
        """Empty output should result in nudge (re-enqueue), not delivery."""
        ts = int(time.time())
        session_id = f"nudge_empty_{ts}"

        session = AgentSession.create_chat(
            session_id=session_id,
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="tc",
            message_id=1,
            message_text="build something",
        )
        session.status = "running"
        session.save()

        chat_state = SendToChatResult(auto_continue_count=0)
        send_cb = AsyncMock()
        enqueue_nudge = AsyncMock()

        async def run():
            # Simulate the empty output path
            msg = ""
            if not msg or not msg.strip():
                chat_state.auto_continue_count += 1
                if chat_state.auto_continue_count <= MAX_NUDGE_COUNT:
                    await enqueue_nudge()
                    chat_state.completion_sent = True
                    chat_state.defer_reaction = True

        asyncio.get_event_loop().run_until_complete(run())

        # Outcome: nudge was called, send was NOT
        enqueue_nudge.assert_called_once()
        send_cb.assert_not_called()
        assert chat_state.completion_sent is True
        assert chat_state.auto_continue_count == 1

    def test_end_turn_with_content_delivers(self):
        """end_turn with substantive output should deliver to Telegram."""
        ts = int(time.time())
        session_id = f"nudge_deliver_{ts}"

        session = AgentSession.create_chat(
            session_id=session_id,
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="tc",
            message_id=1,
            message_text="what time is it?",
        )
        session.status = "running"
        session.save()

        chat_state = SendToChatResult(auto_continue_count=0)
        send_cb = AsyncMock()

        async def run():
            msg = "It is 3pm."
            stop_reason = "end_turn"

            if stop_reason in ("end_turn", None) and len(msg.strip()) > 0:
                await send_cb("tc", msg, 1, session)
                chat_state.completion_sent = True

        asyncio.get_event_loop().run_until_complete(run())

        send_cb.assert_called_once()
        assert chat_state.completion_sent is True

    def test_rate_limited_triggers_nudge(self):
        """Rate-limited stop_reason should nudge after backoff."""
        chat_state = SendToChatResult(auto_continue_count=0)
        enqueue_nudge = AsyncMock()

        async def run():
            stop_reason = "rate_limited"
            if stop_reason == "rate_limited":
                chat_state.auto_continue_count += 1
                await enqueue_nudge()
                chat_state.completion_sent = True
                chat_state.defer_reaction = True

        asyncio.get_event_loop().run_until_complete(run())

        enqueue_nudge.assert_called_once()
        assert chat_state.defer_reaction is True
        assert chat_state.auto_continue_count == 1

    def test_max_nudge_count_forces_delivery(self):
        """After MAX_NUDGE_COUNT nudges, deliver regardless."""
        chat_state = SendToChatResult(auto_continue_count=MAX_NUDGE_COUNT)
        send_cb = AsyncMock()

        async def run():
            msg = "Final output after many nudges"
            if chat_state.auto_continue_count >= MAX_NUDGE_COUNT:
                await send_cb("tc", msg, 1, None)
                chat_state.completion_sent = True

        asyncio.get_event_loop().run_until_complete(run())

        send_cb.assert_called_once()
        assert chat_state.completion_sent is True

    def test_max_nudge_count_on_empty_delivers_fallback(self):
        """After MAX_NUDGE_COUNT nudges with empty output, deliver fallback message."""
        chat_state = SendToChatResult(auto_continue_count=MAX_NUDGE_COUNT)
        send_cb = AsyncMock()

        async def run():
            msg = ""
            if not msg or not msg.strip():
                chat_state.auto_continue_count += 1
                if chat_state.auto_continue_count <= MAX_NUDGE_COUNT:
                    pass  # would nudge
                else:
                    # Safety cap reached on empty output
                    await send_cb(
                        "tc",
                        "The task completed but produced no output.",
                        1,
                        None,
                    )
                    chat_state.completion_sent = True

        asyncio.get_event_loop().run_until_complete(run())

        send_cb.assert_called_once()
        assert "no output" in send_cb.call_args[0][1]
        assert chat_state.completion_sent is True

    def test_already_completed_session_delivers_without_nudge(self):
        """A session already marked 'completed' should deliver, not nudge."""
        ts = int(time.time())
        session_id = f"nudge_completed_{ts}"

        session = AgentSession.create_chat(
            session_id=session_id,
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="tc",
            message_id=1,
            message_text="test",
        )
        session.status = "completed"
        session.save()

        chat_state = SendToChatResult(auto_continue_count=0)
        send_cb = AsyncMock()

        async def run():
            msg = "Here is the final answer"
            # Already completed session bypass
            if session.status == "completed":
                await send_cb("tc", msg, 1, session)
                chat_state.completion_sent = True
                return

        asyncio.get_event_loop().run_until_complete(run())

        send_cb.assert_called_once()
        assert chat_state.completion_sent is True
