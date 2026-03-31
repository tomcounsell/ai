"""Tests for stage-aware auto-continue logic.

Verifies that SDLC jobs use pipeline stage progress from AgentSession.stage_states
(via PipelineStateMachine) as the primary auto-continue signal, falling back to
the classifier for non-SDLC jobs.

Tests use Redis db=1 via the autouse redis_test_db fixture in conftest.py.
"""

import json

# claude_agent_sdk mock is centralized in conftest.py
from agent.agent_session_queue import MAX_NUDGE_COUNT
from models.agent_session import AgentSession


def _make_stages(**overrides):
    """Build a stage_states JSON string with defaults as pending."""
    from bridge.pipeline_state import ALL_STAGES

    states = {s: "pending" for s in ALL_STAGES}
    states["ISSUE"] = "ready"
    states.update(overrides)
    return json.dumps(states)


class TestIsSDLCJob:
    def test_no_stage_states_returns_false(self):
        session = AgentSession()
        session.stage_states = None
        assert session.is_sdlc is False

    def test_classification_type_sdlc_returns_true(self):
        session = AgentSession()
        session.stage_states = None
        session.classification_type = "sdlc"
        assert session.is_sdlc is True

    def test_active_stage_states_returns_true(self):
        session = AgentSession()
        session.stage_states = _make_stages(ISSUE="completed")
        assert session.is_sdlc is True

    def test_all_pending_returns_false(self):
        session = AgentSession()
        session.stage_states = _make_stages()
        assert session.is_sdlc is False


class TestHasRemainingStages:
    def test_no_stage_states_returns_true(self):
        session = AgentSession()
        session.stage_states = None
        assert session.has_remaining_stages() is True

    def test_all_stages_completed(self):
        session = AgentSession()
        session.stage_states = _make_stages(
            ISSUE="completed",
            PLAN="completed",
            CRITIQUE="completed",
            BUILD="completed",
            TEST="completed",
            PATCH="completed",
            REVIEW="completed",
            DOCS="completed",
            MERGE="completed",
        )
        assert session.has_remaining_stages() is False

    def test_some_stages_remaining(self):
        session = AgentSession()
        session.stage_states = _make_stages(
            ISSUE="completed", PLAN="completed", BUILD="in_progress"
        )
        assert session.has_remaining_stages() is True


class TestHasFailedStage:
    def test_no_stage_states_returns_false(self):
        session = AgentSession()
        session.stage_states = None
        assert session.has_failed_stage() is False

    def test_failed_stage_detected(self):
        session = AgentSession()
        session.stage_states = _make_stages(ISSUE="completed", BUILD="failed")
        assert session.has_failed_stage() is True


class TestStageAwareDecisionMatrix:
    def test_stage_states_remaining_auto_continues(self):
        session = AgentSession()
        session.stage_states = _make_stages(
            ISSUE="completed", PLAN="completed", BUILD="in_progress"
        )
        assert session.is_sdlc is True
        assert session.has_remaining_stages() is True
        assert session.has_failed_stage() is False

    def test_sdlc_all_stages_done_falls_to_classifier(self):
        session = AgentSession()
        session.stage_states = _make_stages(
            ISSUE="completed",
            PLAN="completed",
            CRITIQUE="completed",
            BUILD="completed",
            TEST="completed",
            PATCH="completed",
            REVIEW="completed",
            DOCS="completed",
            MERGE="completed",
        )
        assert session.is_sdlc is True
        assert session.has_remaining_stages() is False

    def test_sdlc_failed_stage_delivers_to_user(self):
        session = AgentSession()
        session.stage_states = _make_stages(ISSUE="completed", PLAN="completed", BUILD="failed")
        assert session.is_sdlc is True
        assert session.has_failed_stage() is True


class TestMaxAutoContinuesConstants:
    def test_max_auto_continues_value(self):
        assert MAX_NUDGE_COUNT == 50

    def test_both_caps_positive(self):
        assert isinstance(MAX_NUDGE_COUNT, int)
        assert MAX_NUDGE_COUNT > 0
