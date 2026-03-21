"""Tests for the deterministic Observer.

Tests the Observer's routing decision framework. The Observer is now fully
deterministic (no LLM calls). Covers:
- Deterministic stop_reason routing (rate_limited, timeout)
- State machine outcome classification integration
- SDLC steer when stages remain
- Human input detection bypass
- Non-SDLC job always delivers
- Failed stage delivers
- Pipeline complete delivers
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.observer import Observer, _output_needs_human_input
from models.agent_session import SDLC_STAGES

# ============================================================================
# Helper: build a mock session for Observer tests
# ============================================================================


def _make_session(
    *,
    is_sdlc=True,
    classification_type="sdlc",
    stage_states=None,
    issue_url=None,
    pr_url=None,
    plan_url=None,
    branch_name=None,
    work_item_slug=None,
    slug=None,
    context_summary=None,
    expectations=None,
    queued_steering_messages=None,
    correlation_id=None,
):
    """Create a mock AgentSession for Observer tests."""
    session = MagicMock()
    session.session_id = "test-session"
    session.job_id = "test-job"
    session.classification_type = classification_type
    session.is_sdlc = is_sdlc
    session.issue_url = issue_url
    session.pr_url = pr_url
    session.plan_url = plan_url
    session.branch_name = branch_name
    session.work_item_slug = work_item_slug
    session.slug = slug
    session.context_summary = context_summary
    session.expectations = expectations
    session.queued_steering_messages = queued_steering_messages or []
    session.correlation_id = correlation_id
    session.history = []
    session.stage_states = stage_states
    session.status = "running"
    session.created_at = 1000.0
    session.save = MagicMock()
    session.get_history_list.return_value = []
    session.get_links.return_value = {}
    session.get_stage_progress.return_value = {s: "pending" for s in SDLC_STAGES}
    session.has_remaining_stages.return_value = True
    session.has_failed_stage.return_value = False
    session.pop_steering_messages.return_value = []
    return session


def _make_observer(session, worker_output="test output", auto_continue_count=0, stop_reason=None):
    """Create an Observer instance with mocked callbacks."""
    return Observer(
        session=session,
        worker_output=worker_output,
        auto_continue_count=auto_continue_count,
        send_cb=AsyncMock(),
        enqueue_fn=AsyncMock(),
        stop_reason=stop_reason,
    )


# ============================================================================
# Human Input Detection
# ============================================================================


class TestHumanInputDetection:
    """Test _output_needs_human_input() heuristic patterns."""

    def test_question_for_human(self):
        assert _output_needs_human_input("Should I proceed with the merge?")

    def test_fatal_error(self):
        assert _output_needs_human_input("FATAL: cannot proceed without credentials")

    def test_options_presented(self):
        assert _output_needs_human_input("Option A) Do this\nOption B) Do that")

    def test_normal_output_no_match(self):
        assert not _output_needs_human_input("All 42 tests passed.")

    def test_empty_output(self):
        assert not _output_needs_human_input("")


# ============================================================================
# Observer Deterministic Routing (stop_reason)
# ============================================================================


class TestStopReasonRouting:
    """Test deterministic routing based on SDK stop_reason."""

    @pytest.mark.asyncio
    async def test_rate_limited_steers(self):
        session = _make_session(stage_states=json.dumps({"BUILD": "in_progress"}))
        observer = _make_observer(session, stop_reason="rate_limited")
        decision = await observer.run()
        assert decision["action"] == "steer"
        assert "rate limited" in decision["coaching_message"].lower()

    @pytest.mark.asyncio
    async def test_timeout_delivers(self):
        session = _make_session(stage_states=json.dumps({"BUILD": "in_progress"}))
        observer = _make_observer(session, stop_reason="timeout")
        decision = await observer.run()
        assert decision["action"] == "deliver"
        assert "timed out" in decision["reason"].lower()

    @pytest.mark.asyncio
    async def test_unknown_stop_reason_delivers(self):
        session = _make_session(stage_states=json.dumps({"BUILD": "in_progress"}))
        observer = _make_observer(session, stop_reason="some_unknown_reason")
        decision = await observer.run()
        assert decision["action"] == "deliver"


# ============================================================================
# Observer Deterministic SDLC Routing
# ============================================================================


class TestDeterministicSDLCRouting:
    """Test deterministic SDLC routing (no LLM fallback)."""

    @pytest.mark.asyncio
    async def test_sdlc_with_remaining_stages_steers(self):
        """SDLC job with remaining stages should steer deterministically."""
        states = {"ISSUE": "completed", "PLAN": "completed", "BUILD": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        observer = _make_observer(
            session, worker_output="PR created: https://github.com/org/repo/pull/42"
        )
        decision = await observer.run()
        assert decision["action"] == "steer"
        assert decision.get("deterministic_guard") is True

    @pytest.mark.asyncio
    async def test_non_sdlc_delivers(self):
        """Non-SDLC job should deliver immediately (no LLM)."""
        session = _make_session(is_sdlc=False, classification_type="casual")
        observer = _make_observer(session)
        decision = await observer.run()
        assert decision["action"] == "deliver"
        assert "non-sdlc" in decision["reason"].lower()

    @pytest.mark.asyncio
    async def test_human_input_delivers(self):
        """When worker asks a question, deliver to human."""
        states = {"ISSUE": "completed", "PLAN": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        observer = _make_observer(session, worker_output="Should I proceed with this approach?")
        decision = await observer.run()
        assert decision["action"] == "deliver"
        assert "human" in decision["reason"].lower()

    @pytest.mark.asyncio
    async def test_failed_stage_delivers(self):
        """When a stage has failed, deliver to human."""
        states = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "completed",
            "TEST": "failed",
        }
        session = _make_session(stage_states=json.dumps(states))
        observer = _make_observer(session)
        decision = await observer.run()
        assert decision["action"] == "deliver"
        assert "failed" in decision["reason"].lower()

    @pytest.mark.asyncio
    async def test_all_stages_complete_delivers(self):
        """When all stages are complete, deliver to human."""
        states = {s: "completed" for s in SDLC_STAGES}
        session = _make_session(stage_states=json.dumps(states))
        session.has_remaining_stages.return_value = False
        observer = _make_observer(session)
        decision = await observer.run()
        assert decision["action"] == "deliver"

    @pytest.mark.asyncio
    async def test_steering_messages_cleared(self):
        """Observer should clear queued steering messages."""
        states = {"ISSUE": "completed", "PLAN": "in_progress"}
        session = _make_session(
            stage_states=json.dumps(states),
            queued_steering_messages=["focus on tests"],
        )
        session.pop_steering_messages.return_value = ["focus on tests"]
        observer = _make_observer(session)
        await observer.run()
        session.pop_steering_messages.assert_called_once()


# ============================================================================
# Observer No LLM calls
# ============================================================================


class TestNoLLMCalls:
    """Verify the deterministic observer never calls an LLM."""

    @pytest.mark.asyncio
    async def test_no_anthropic_import(self):
        """Observer should not import or use anthropic."""
        import bridge.observer as obs_module

        # The module should not have anthropic imported
        assert not hasattr(obs_module, "anthropic"), "Observer should not import anthropic"

    @pytest.mark.asyncio
    async def test_no_llm_observer_method(self):
        """Observer should not have _run_llm_observer method."""
        session = _make_session()
        observer = _make_observer(session)
        assert not hasattr(observer, "_run_llm_observer"), (
            "Deterministic Observer should not have _run_llm_observer"
        )

    @pytest.mark.asyncio
    async def test_no_dispatch_tool_method(self):
        """Observer should not have _dispatch_tool method (was for LLM tool use)."""
        session = _make_session()
        observer = _make_observer(session)
        assert not hasattr(observer, "_dispatch_tool"), (
            "Deterministic Observer should not have _dispatch_tool"
        )


# ============================================================================
# Circuit Breaker (No-op stubs)
# ============================================================================


class TestCircuitBreakerStubs:
    """Test that circuit breaker functions are no-ops."""

    def test_observer_record_success_noop(self):
        from bridge.observer import observer_record_success

        observer_record_success("test-session")  # Should not raise

    def test_clear_observer_state_noop(self):
        from bridge.observer import clear_observer_state

        clear_observer_state("test-session")  # Should not raise

    def test_observer_record_failure_returns_no_retry(self):
        from bridge.observer import observer_record_failure

        result = observer_record_failure("test-session")
        assert result["should_retry"] is False
        assert result["should_escalate"] is False
        assert result["failure_count"] == 0

    def test_get_observer_failure_count_returns_zero(self):
        from bridge.observer import get_observer_failure_count

        assert get_observer_failure_count("test-session") == 0
