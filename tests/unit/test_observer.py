"""Tests for the simplified Observer Agent.

Tests the Observer's routing decision framework using PipelineStateMachine
for stage tracking. Covers:
- Deterministic stop_reason routing (budget_exceeded, rate_limited)
- State machine outcome classification integration
- Deterministic SDLC guard (steer when stages remain)
- Human input detection bypass
- Non-SDLC job handling
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.observer import Observer, _output_needs_human_input
from config.models import HAIKU
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
    session.is_sdlc_job.return_value = is_sdlc
    session.issue_url = issue_url
    session.pr_url = pr_url
    session.plan_url = plan_url
    session.branch_name = branch_name
    session.work_item_slug = work_item_slug
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
        model=HAIKU,
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
    async def test_budget_exceeded_delivers(self):
        session = _make_session(stage_states=json.dumps({"BUILD": "in_progress"}))
        observer = _make_observer(session, stop_reason="budget_exceeded")
        decision = await observer.run()
        assert decision["action"] == "deliver"
        assert "budget exceeded" in decision["reason"].lower()

    @pytest.mark.asyncio
    async def test_rate_limited_steers(self):
        session = _make_session(stage_states=json.dumps({"BUILD": "in_progress"}))
        observer = _make_observer(session, stop_reason="rate_limited")
        decision = await observer.run()
        assert decision["action"] == "steer"
        assert "rate limited" in decision["coaching_message"].lower()


# ============================================================================
# Observer State Machine Integration
# ============================================================================


class TestStateMachineIntegration:
    """Test Observer's integration with PipelineStateMachine."""

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
    async def test_non_sdlc_falls_to_llm(self):
        """Non-SDLC job should fall through to LLM Observer."""
        session = _make_session(is_sdlc=False, classification_type="casual")
        observer = _make_observer(session)
        # Mock the LLM call to avoid actual API calls
        with patch.object(observer, "_run_llm_observer") as mock_llm:
            mock_llm.return_value = {
                "action": "deliver",
                "reason": "casual conversation",
                "resolved_stage": None,
                "stage_outcome": None,
                "next_stage": None,
            }
            decision = await observer.run()
            assert decision["action"] == "deliver"
            mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_human_input_bypasses_guard(self):
        """When worker asks a question, guard is bypassed to LLM."""
        states = {"ISSUE": "completed", "PLAN": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        observer = _make_observer(session, worker_output="Should I proceed with this approach?")
        with patch.object(observer, "_run_llm_observer") as mock_llm:
            mock_llm.return_value = {
                "action": "deliver",
                "reason": "human input needed",
                "resolved_stage": None,
                "stage_outcome": None,
                "next_stage": None,
            }
            await observer.run()
            mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_cap_reached_bypasses_guard(self):
        """When auto-continue cap is reached, guard is bypassed."""
        states = {"ISSUE": "completed", "PLAN": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        observer = _make_observer(session, auto_continue_count=10)
        with patch.object(observer, "_run_llm_observer") as mock_llm:
            mock_llm.return_value = {
                "action": "deliver",
                "reason": "cap reached",
                "resolved_stage": None,
                "stage_outcome": None,
                "next_stage": None,
            }
            await observer.run()
            mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_stage_bypasses_guard(self):
        """When a stage has failed, guard is bypassed."""
        states = {"ISSUE": "completed", "PLAN": "completed", "BUILD": "completed", "TEST": "failed"}
        session = _make_session(stage_states=json.dumps(states))
        observer = _make_observer(session)
        with patch.object(observer, "_run_llm_observer") as mock_llm:
            mock_llm.return_value = {
                "action": "deliver",
                "reason": "test failed",
                "resolved_stage": None,
                "stage_outcome": None,
                "next_stage": None,
            }
            await observer.run()
            mock_llm.assert_called_once()


# ============================================================================
# Observer Tool Handlers
# ============================================================================


class TestToolHandlers:
    """Test Observer tool dispatch and handlers."""

    def test_read_session_returns_stage_progress(self):
        states = {"ISSUE": "completed", "PLAN": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        observer = _make_observer(session)
        result = observer._handle_read_session()
        assert result["is_sdlc"] is True
        assert result["stage_progress"]["ISSUE"] == "completed"
        assert result["stage_progress"]["PLAN"] == "in_progress"
        assert result["has_remaining_stages"] is True
        assert result["current_stage"] == "PLAN"

    def test_read_session_non_sdlc(self):
        session = _make_session(is_sdlc=False, classification_type="casual")
        observer = _make_observer(session)
        result = observer._handle_read_session()
        assert result["is_sdlc"] is False
        assert result["current_stage"] is None

    def test_dispatch_enqueue_continuation(self):
        session = _make_session()
        observer = _make_observer(session)
        result_str = observer._dispatch_tool(
            "enqueue_continuation", {"coaching_message": "continue with /do-build"}
        )
        result = json.loads(result_str)
        assert result["action"] == "enqueue_continuation"
        assert observer._decision_made is True
        assert observer._action_taken == "steer"

    def test_dispatch_deliver_to_telegram(self):
        session = _make_session()
        observer = _make_observer(session)
        result_str = observer._dispatch_tool(
            "deliver_to_telegram",
            {"reason": "all done", "message_for_user": "Build complete!"},
        )
        result = json.loads(result_str)
        assert result["action"] == "deliver_to_telegram"
        assert result["message_for_user"] == "Build complete!"
        assert observer._decision_made is True
        assert observer._action_taken == "deliver"

    def test_dispatch_unknown_tool(self):
        session = _make_session()
        observer = _make_observer(session)
        result_str = observer._dispatch_tool("nonexistent", {})
        result = json.loads(result_str)
        assert result["status"] == "error"

    def test_update_session_persists_context(self):
        session = _make_session()
        # Mock AgentSession.query.filter to return the session
        with patch("bridge.observer.AgentSession") as mock_as:
            mock_as.query.filter.return_value = [session]
            observer = _make_observer(session)
            result = observer._handle_update_session(
                context_summary="Building auth feature",
                expectations="Need PR approval",
            )
        assert result["status"] == "ok"
        assert "context_summary" in result["updated_fields"]
        assert "expectations" in result["updated_fields"]


# ============================================================================
# Observer Decision Output Structure
# ============================================================================


class TestDecisionStructure:
    """Test that Observer decisions include required fields."""

    @pytest.mark.asyncio
    async def test_steer_decision_has_stage_fields(self):
        states = {"ISSUE": "completed", "PLAN": "completed", "BUILD": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        observer = _make_observer(
            session, worker_output="PR created: https://github.com/org/repo/pull/42"
        )
        decision = await observer.run()
        assert "resolved_stage" in decision
        assert "next_stage" in decision

    @pytest.mark.asyncio
    async def test_deliver_decision_has_stage_fields(self):
        session = _make_session(stage_states=json.dumps({"BUILD": "in_progress"}))
        observer = _make_observer(session, stop_reason="budget_exceeded")
        decision = await observer.run()
        assert "resolved_stage" in decision
        assert "next_stage" in decision
