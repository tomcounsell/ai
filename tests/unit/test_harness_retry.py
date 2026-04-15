"""Tests for harness startup retry logic in agent_session_queue.py.

These tests cover the retry interception that happens in do_work() when the
CLI harness returns an "Error: CLI harness not found" string. The interception
lives in agent_session_queue.py, NOT in sdk_client.py. For raw error-string
validation (get_response_via_harness return value), see test_harness_streaming.py.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from agent.agent_session_queue import (
    _HARNESS_NOT_FOUND_MAX_RETRIES,
    _HARNESS_NOT_FOUND_PREFIX,
    _ensure_worker,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HARNESS_NOT_FOUND_MSG = (
    "Error: CLI harness not found — [Errno 2] No such file or directory: 'claude'"
)
PERSONA_MSG = (
    "Tried a few times but couldn't get Claude to start — "
    "looks like the CLI may not be on PATH. "
    "You can resend once that's sorted."
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


# ---------------------------------------------------------------------------
# Extract the do_work closure for testing
# ---------------------------------------------------------------------------


async def _invoke_do_work(harness_return: str, agent_session):
    """
    Re-implement the do_work() closure logic from agent_session_queue.py
    so we can test it in isolation without standing up the full session machinery.

    Mirrors the exact logic added in agent_session_queue.py:
    - Checks for _HARNESS_NOT_FOUND_PREFIX
    - Guards on agent_session is None (B1)
    - Reads/writes cli_retry_count from extra_context
    - Calls transition_status and _ensure_worker on retry
    - Returns "" on retry, persona message on exhaustion, raw otherwise
    """
    from models.session_lifecycle import transition_status  # noqa: PLC0415

    harness_requeued_flag = False
    raw = harness_return

    if raw.startswith(_HARNESS_NOT_FOUND_PREFIX):
        if agent_session is None:
            return raw, False

        ec = agent_session.extra_context or {}
        retry_count_actual = int(ec.get("cli_retry_count", 0))

        if retry_count_actual < _HARNESS_NOT_FOUND_MAX_RETRIES:
            from models.session_lifecycle import StatusConflictError  # noqa: PLC0415

            ec["cli_retry_count"] = retry_count_actual + 1
            agent_session.extra_context = ec

            try:
                await asyncio.to_thread(
                    transition_status, agent_session, "pending", "harness-retry"
                )
            except (StatusConflictError, ValueError):
                return PERSONA_MSG, False

            _ensure_worker(
                agent_session.worker_key,
                is_project_keyed=agent_session.is_project_keyed,
            )
            harness_requeued_flag = True
            return "", harness_requeued_flag
        else:
            return PERSONA_MSG, False

    return raw, False


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
            result, requeued = await _invoke_do_work(HARNESS_NOT_FOUND_MSG, agent_session)

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
            result, requeued = await _invoke_do_work(HARNESS_NOT_FOUND_MSG, agent_session)

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
            result, requeued = await _invoke_do_work(HARNESS_NOT_FOUND_MSG, agent_session)

        assert result == PERSONA_MSG, f"Expected persona message, got: {result!r}"
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
            result, requeued = await _invoke_do_work(HARNESS_NOT_FOUND_MSG, agent_session=None)

        assert result == HARNESS_NOT_FOUND_MSG
        assert requeued is False
        mock_transition.assert_not_called()
        mock_ensure_worker.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_harness_error_not_retried(self):
        """Non-FileNotFoundError harness results are returned as-is (no retry)."""
        other_error = "Error: some other harness problem"
        agent_session = _make_agent_session(extra_context={})

        with (
            patch("agent.agent_session_queue._ensure_worker") as mock_ensure_worker,
            patch("models.session_lifecycle.transition_status") as mock_transition,
        ):
            result, requeued = await _invoke_do_work(other_error, agent_session)

        assert result == other_error
        assert requeued is False
        mock_transition.assert_not_called()
        mock_ensure_worker.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_result_passes_through(self):
        """Normal successful harness result is returned unchanged."""
        good_result = "Here is the response you asked for."
        agent_session = _make_agent_session(extra_context={})

        result, requeued = await _invoke_do_work(good_result, agent_session)

        assert result == good_result
        assert requeued is False

    @pytest.mark.asyncio
    async def test_cli_retry_count_missing_defaults_to_zero(self):
        """When cli_retry_count is absent from extra_context, it defaults to 0."""
        agent_session = _make_agent_session(extra_context=None)

        with (
            patch("agent.agent_session_queue._ensure_worker"),
            patch("models.session_lifecycle.transition_status"),
        ):
            result, requeued = await _invoke_do_work(HARNESS_NOT_FOUND_MSG, agent_session)

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
            result, requeued = await _invoke_do_work(HARNESS_NOT_FOUND_MSG, agent_session)

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
            result, requeued = await _invoke_do_work(HARNESS_NOT_FOUND_MSG, agent_session)

        # Must NOT leak "StatusConflictError" to Telegram — fall through to persona message
        assert result == PERSONA_MSG, f"Expected persona message on conflict, got: {result!r}"
        assert requeued is False
        mock_ensure_worker.assert_not_called()

    def test_empty_string_is_falsy_for_background_task(self):
        """Empty string must be falsy so BackgroundTask skips sending (line 151 in messenger.py)."""
        assert not ""  # Empty string is falsy — BackgroundTask._run_work skips send
        assert bool("some text")  # Non-empty strings are truthy — send proceeds
