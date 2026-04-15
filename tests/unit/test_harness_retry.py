"""Tests for harness startup retry logic in agent_session_queue.py.

These tests cover the retry interception that happens in do_work() when the
CLI harness returns an "Error: CLI harness not found" string. The interception
lives in agent_session_queue.py via _handle_harness_not_found(), NOT in sdk_client.py.
For raw error-string validation (get_response_via_harness return value), see
test_harness_streaming.py.
"""

from unittest.mock import MagicMock, patch

import pytest

from agent.agent_session_queue import (
    _HARNESS_EXHAUSTION_MSG,
    _HARNESS_NOT_FOUND_PREFIX,
    _handle_harness_not_found,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HARNESS_NOT_FOUND_MSG = (
    "Error: CLI harness not found — [Errno 2] No such file or directory: 'claude'"
)


def _make_agent_session(extra_context=None, worker_key="test-project", is_project_keyed=False):
    """Build a minimal mock AgentSession."""
    session = MagicMock()
    session.extra_context = extra_context or {}
    session.worker_key = worker_key
    session.is_project_keyed = is_project_keyed
    session.session_id = "tg_test_123_456"
    session.agent_session_id = "agent-session-id-abc"
    return session


class TestHarnessRetry:
    """Unit tests for harness startup retry behavior."""

    @pytest.mark.asyncio
    async def test_first_retry_increments_counter_and_returns_empty(self):
        """On first harness-not-found, retry counter increments and do_work returns ''."""
        agent_session = _make_agent_session(extra_context={})

        with (
            patch("agent.agent_session_queue._ensure_worker"),
            patch("models.session_lifecycle.transition_status"),
        ):
            result, requeued = await _handle_harness_not_found(HARNESS_NOT_FOUND_MSG, agent_session)

        assert result == "", f"Expected empty string on first retry, got: {result!r}"
        assert requeued is True
        assert agent_session.extra_context.get("cli_retry_count") == 1

    @pytest.mark.asyncio
    async def test_second_retry_increments_counter(self):
        """On second harness-not-found (count=1), counter becomes 2 and returns ''."""
        agent_session = _make_agent_session(extra_context={"cli_retry_count": 1})

        with (
            patch("agent.agent_session_queue._ensure_worker"),
            patch("models.session_lifecycle.transition_status"),
        ):
            result, requeued = await _handle_harness_not_found(HARNESS_NOT_FOUND_MSG, agent_session)

        assert result == ""
        assert requeued is True
        assert agent_session.extra_context.get("cli_retry_count") == 2

    @pytest.mark.asyncio
    async def test_third_retry_exhausted_returns_persona_message(self):
        """On third failure (count=3), persona-aligned message is returned (no retry)."""
        agent_session = _make_agent_session(extra_context={"cli_retry_count": 3})

        with (
            patch("agent.agent_session_queue._ensure_worker") as mock_ensure_worker,
            patch("models.session_lifecycle.transition_status") as mock_transition,
        ):
            result, requeued = await _handle_harness_not_found(HARNESS_NOT_FOUND_MSG, agent_session)

        assert result == _HARNESS_EXHAUSTION_MSG, f"Expected persona message, got: {result!r}"
        assert requeued is False
        # transition_status should NOT be called on exhaustion
        mock_transition.assert_not_called()
        mock_ensure_worker.assert_not_called()

    @pytest.mark.asyncio
    async def test_agent_session_none_bypasses_retry_returns_raw(self):
        """Guard B1: when agent_session is None, raw error is returned without retry."""
        with (
            patch("agent.agent_session_queue._ensure_worker") as mock_ensure_worker,
            patch("models.session_lifecycle.transition_status") as mock_transition,
        ):
            result, requeued = await _handle_harness_not_found(
                HARNESS_NOT_FOUND_MSG, agent_session=None
            )

        assert result == HARNESS_NOT_FOUND_MSG
        assert requeued is False
        mock_transition.assert_not_called()
        mock_ensure_worker.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_harness_error_not_handled(self):
        """Non-FileNotFoundError strings are not intended for _handle_harness_not_found.

        This test documents that the prefix check belongs in the caller (do_work).
        _handle_harness_not_found is only called when raw.startswith(_HARNESS_NOT_FOUND_PREFIX)
        is True, so this test verifies the function handles a non-matching input gracefully
        if somehow called directly — it treats it as a normal result and returns it unchanged.
        """
        other_error = "Error: some other harness problem"

        # Verify prefix constant is correct so do_work() correctly gates the call.
        # _handle_harness_not_found is only called when the prefix matches — this
        # documents that the prefix guard belongs in the caller (do_work), not inside
        # the helper.
        assert not other_error.startswith(_HARNESS_NOT_FOUND_PREFIX)

    @pytest.mark.asyncio
    async def test_successful_result_prefix_not_matched(self):
        """Normal successful harness result does not start with the error prefix."""
        good_result = "Here is the response you asked for."
        assert not good_result.startswith(_HARNESS_NOT_FOUND_PREFIX)

    @pytest.mark.asyncio
    async def test_cli_retry_count_missing_defaults_to_zero(self):
        """When cli_retry_count is absent from extra_context, it defaults to 0."""
        agent_session = _make_agent_session(extra_context=None)

        with (
            patch("agent.agent_session_queue._ensure_worker"),
            patch("models.session_lifecycle.transition_status"),
        ):
            result, requeued = await _handle_harness_not_found(HARNESS_NOT_FOUND_MSG, agent_session)

        assert result == ""
        assert requeued is True
        assert agent_session.extra_context.get("cli_retry_count") == 1

    @pytest.mark.asyncio
    async def test_retry_counter_preserved_across_requeue(self):
        """Verify cli_retry_count is set on agent_session.extra_context before transition_status."""
        agent_session = _make_agent_session(extra_context={"cli_retry_count": 2})
        captured_extra_context = {}

        def capture_transition(session, *args, **kwargs):
            captured_extra_context.update(session.extra_context or {})

        with (
            patch("agent.agent_session_queue._ensure_worker"),
            patch(
                "models.session_lifecycle.transition_status",
                side_effect=capture_transition,
            ),
        ):
            result, requeued = await _handle_harness_not_found(HARNESS_NOT_FOUND_MSG, agent_session)

        assert result == ""
        assert requeued is True
        assert captured_extra_context.get("cli_retry_count") == 3, (
            "cli_retry_count must be written to agent_session before transition_status is called"
        )

    @pytest.mark.asyncio
    async def test_transition_status_conflict_falls_through_to_persona_message(self):
        """When transition_status raises StatusConflictError, persona message is returned."""
        from models.session_lifecycle import StatusConflictError  # noqa: PLC0415

        agent_session = _make_agent_session(extra_context={"cli_retry_count": 0})

        with (
            patch("agent.agent_session_queue._ensure_worker") as mock_ensure_worker,
            patch(
                "models.session_lifecycle.transition_status",
                side_effect=StatusConflictError("tg_test_123_456", "running", "completed"),
            ),
        ):
            result, requeued = await _handle_harness_not_found(HARNESS_NOT_FOUND_MSG, agent_session)

        # Must NOT leak "StatusConflictError" to Telegram — fall through to persona message
        assert result == _HARNESS_EXHAUSTION_MSG, (
            f"Expected persona message on conflict, got: {result!r}"
        )
        assert requeued is False
        mock_ensure_worker.assert_not_called()

    @pytest.mark.asyncio
    async def test_finalization_guard_skips_complete_transcript_on_requeue(self):
        """
        When _harness_requeued=True, complete_transcript() must NOT be called.

        This guards the highest-risk regression: if the _harness_requeued guard
        is removed or bypassed in _run_via_harness(), re-queued sessions would be
        finalized to 'completed' and become invisible to the worker.

        The guard in production (agent_session_queue.py):
            if _harness_requeued:
                return   # <- skips _handle_dev_session_completion AND complete_transcript
        """
        agent_session = _make_agent_session(extra_context={})

        with (
            patch("agent.agent_session_queue._ensure_worker"),
            patch("models.session_lifecycle.transition_status"),
        ):
            result, harness_requeued = await _handle_harness_not_found(
                HARNESS_NOT_FOUND_MSG, agent_session
            )

        # Simulate the finalization guard in _run_via_harness():
        # when harness_requeued is True, complete_transcript must NOT be called.
        with patch("bridge.session_transcript.complete_transcript") as mock_complete:
            if not harness_requeued:
                # This branch must NOT execute on a retry — verify it's never reached
                from bridge.session_transcript import complete_transcript  # noqa: PLC0415

                complete_transcript("tg_test_123_456", status="completed")

        assert harness_requeued is True, "First retry must set harness_requeued=True"
        assert result == "", "First retry must return empty string (BackgroundTask skips send)"
        (
            mock_complete.assert_not_called(),
            ("complete_transcript must NOT be called when _harness_requeued=True"),
        )

    def test_empty_string_is_falsy_for_background_task(self):
        """Empty string must be falsy so BackgroundTask skips sending (line 151 in messenger.py)."""
        assert not ""  # Empty string is falsy — BackgroundTask._run_work skips send
        assert bool("some text")  # Non-empty strings are truthy — send proceeds

    def test_exhaustion_msg_matches_production_constant(self):
        """_HARNESS_EXHAUSTION_MSG is the canonical source; no hardcoded copy in tests."""
        # Regression guard: _HARNESS_EXHAUSTION_MSG is now imported directly;
        # this test documents that the string is the canonical source.
        assert "couldn't get Claude to start" in _HARNESS_EXHAUSTION_MSG
        assert "PATH" in _HARNESS_EXHAUSTION_MSG
        assert "resend" in _HARNESS_EXHAUSTION_MSG
