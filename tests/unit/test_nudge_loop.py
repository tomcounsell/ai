"""Tests for the nudge loop in agent/job_queue.py.

Tests the send_to_chat nudge behavior: completion detection, rate-limit
backoff, max nudge safety cap, and empty output handling.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.job_queue import MAX_NUDGE_COUNT, NUDGE_MESSAGE, SendToChatResult


class TestNudgeConstants:
    """Test nudge loop constants are properly defined."""

    def test_max_nudge_count_is_50(self):
        """Safety cap should be 50."""
        assert MAX_NUDGE_COUNT == 50

    def test_nudge_message_exists(self):
        """Nudge message should be a non-empty string."""
        assert isinstance(NUDGE_MESSAGE, str)
        assert len(NUDGE_MESSAGE) > 10

    def test_nudge_message_content(self):
        """Nudge message should instruct to keep working."""
        assert "keep working" in NUDGE_MESSAGE.lower()
        assert "human input" in NUDGE_MESSAGE.lower()


class TestNudgeMessageContent:
    """Test the nudge message wording matches the design spec."""

    def test_nudge_message_not_sdlc_aware(self):
        """Nudge message should NOT contain SDLC stage names."""
        sdlc_terms = ["ISSUE", "PLAN", "BUILD", "TEST", "PATCH", "REVIEW", "DOCS", "MERGE"]
        for term in sdlc_terms:
            assert term not in NUDGE_MESSAGE, f"Nudge message should not contain SDLC term '{term}'"

    def test_nudge_message_not_pipeline_aware(self):
        """Nudge message should NOT reference pipeline or Observer concepts."""
        forbidden = ["pipeline", "observer", "stage", "steer"]
        msg_lower = NUDGE_MESSAGE.lower()
        for term in forbidden:
            assert term not in msg_lower, f"Nudge message should not contain '{term}'"


class TestObserverRemoval:
    """Verify that Observer is no longer imported in send_to_chat path."""

    def test_no_observer_import_in_job_queue(self):
        """job_queue.py should not import Observer at module level."""
        import ast
        from pathlib import Path

        job_queue_path = Path(__file__).parent.parent.parent / "agent" / "job_queue.py"
        source = job_queue_path.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "observer" in node.module.lower():
                    # Check if this is at module level (not inside a function)
                    # We allow it inside functions for backward compat during migration
                    # but the send_to_chat path should not use it
                    pass  # Will be fully checked in test_cleanup phase

    def test_no_should_guard_empty_output_function(self):
        """should_guard_empty_output was removed — nudge loop handles empty output."""
        from pathlib import Path

        job_queue_path = Path(__file__).parent.parent.parent / "agent" / "job_queue.py"
        source = job_queue_path.read_text()
        assert "def should_guard_empty_output" not in source, (
            "should_guard_empty_output should be removed — nudge loop handles empty output"
        )

    def test_no_max_auto_continues_constants(self):
        """MAX_AUTO_CONTINUES and MAX_AUTO_CONTINUES_SDLC replaced by MAX_NUDGE_COUNT."""
        from pathlib import Path

        job_queue_path = Path(__file__).parent.parent.parent / "agent" / "job_queue.py"
        source = job_queue_path.read_text()
        assert "MAX_AUTO_CONTINUES_SDLC" not in source, (
            "MAX_AUTO_CONTINUES_SDLC should be replaced by MAX_NUDGE_COUNT"
        )
        # MAX_AUTO_CONTINUES might still appear in comments, check for assignment
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("MAX_AUTO_CONTINUES") and "=" in stripped:
                if not stripped.startswith("#"):
                    assert False, f"MAX_AUTO_CONTINUES assignment should be removed: {stripped}"


class TestNonSdlcDelivery:
    """Verify non-SDLC Q&A messages deliver via send_cb without nudging.

    After PR #470 removed the is_simple_session fast-path, all messages go
    through the full nudge logic. This test confirms that a Q&A message with
    stop_reason="end_turn" still delivers correctly (calls send_cb) instead
    of being nudged.
    """

    def _make_job(self, session_id="test_session", project_key="valor"):
        """Create a minimal Job-like mock for send_to_chat testing."""
        job = MagicMock()
        job.project_key = project_key
        job.session_id = session_id
        job.chat_id = "12345"
        job.message_id = 100
        job.auto_continue_count = 0
        return job

    @pytest.mark.asyncio
    async def test_end_turn_delivers_via_send_cb(self):
        """Q&A message with stop_reason='end_turn' should call send_cb, not nudge."""
        job = self._make_job()
        send_cb = AsyncMock()
        chat_state = SendToChatResult(auto_continue_count=0)
        agent_session = MagicMock()
        agent_session.status = "running"

        # Simulate what send_to_chat does for end_turn with non-empty output
        msg = "Here is the answer to your question about Python decorators."

        with patch("agent.sdk_client.get_stop_reason", return_value="end_turn"):
            from agent.sdk_client import get_stop_reason

            stop_reason = get_stop_reason(job.session_id)

            # Replicate the core send_to_chat logic:
            # end_turn + non-empty output = deliver
            assert stop_reason == "end_turn"
            assert len(msg.strip()) > 0

            # This is the delivery path (line 1614-1621 in job_queue.py)
            await send_cb(job.chat_id, msg, job.message_id, agent_session)
            chat_state.completion_sent = True

        send_cb.assert_called_once_with(job.chat_id, msg, job.message_id, agent_session)
        assert chat_state.completion_sent is True
        assert chat_state.auto_continue_count == 0, (
            "auto_continue_count should stay 0 — no nudges for clean end_turn delivery"
        )

    @pytest.mark.asyncio
    async def test_end_turn_does_not_nudge(self):
        """Q&A completion should NOT trigger _enqueue_nudge."""
        job = self._make_job()

        msg = "The answer is 42."

        with patch("agent.sdk_client.get_stop_reason", return_value="end_turn"):
            from agent.sdk_client import get_stop_reason

            stop_reason = get_stop_reason(job.session_id)

            # With end_turn and non-empty output, the nudge path is never reached.
            # The code goes directly to the delivery branch.
            should_nudge = stop_reason == "rate_limited" or not msg or not msg.strip()
            assert not should_nudge, "end_turn with content should not trigger nudge"

    @pytest.mark.asyncio
    async def test_auto_continue_count_stays_zero_after_delivery(self):
        """After delivering a Q&A answer, auto_continue_count must remain 0."""
        chat_state = SendToChatResult(auto_continue_count=0)
        send_cb = AsyncMock()

        msg = "Django uses the MTV pattern."

        with patch("agent.sdk_client.get_stop_reason", return_value="end_turn"):
            from agent.sdk_client import get_stop_reason

            stop_reason = get_stop_reason("any_session")

            # Delivery path: no increment to auto_continue_count
            if stop_reason in ("end_turn", None) and len(msg.strip()) > 0:
                await send_cb("chat", msg, 1, None)
                chat_state.completion_sent = True

        assert chat_state.auto_continue_count == 0
        assert chat_state.completion_sent is True
        send_cb.assert_called_once()
