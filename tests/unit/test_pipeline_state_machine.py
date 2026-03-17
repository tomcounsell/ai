"""Tests for bridge.pipeline_state — PipelineStateMachine.

Tests cover:
- Initialization (default, from JSON, from dict, from None)
- start_stage() ordering enforcement
- complete_stage() transitions and next-stage marking
- fail_stage() and PATCH cycle handling
- get_display_progress() excludes PATCH
- current_stage() and next_stage() queries
- has_remaining_stages() and has_failed_stage()
- classify_outcome() two-tier classification
- Edge cases: double-complete, invalid stages, cycle re-entry
"""

import json
from unittest.mock import MagicMock

import pytest

from bridge.pipeline_state import PipelineStateMachine


def _make_session(stage_states=None, **kwargs):
    """Create a mock AgentSession with stage_states."""
    session = MagicMock()
    session.session_id = "test-session-123"
    session.stage_states = stage_states
    session.save = MagicMock()
    for k, v in kwargs.items():
        setattr(session, k, v)
    return session


class TestInitialization:
    """Test PipelineStateMachine.__init__()."""

    def test_default_initialization(self):
        """All stages pending, ISSUE ready when no prior state."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        assert sm.states["ISSUE"] == "ready"
        assert sm.states["PLAN"] == "pending"
        assert sm.states["MERGE"] == "pending"
        assert sm.patch_cycle_count == 0

    def test_from_json_string(self):
        """Loads state from JSON string on session."""
        states = {"ISSUE": "completed", "PLAN": "in_progress", "BUILD": "pending"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        assert sm.states["ISSUE"] == "completed"
        assert sm.states["PLAN"] == "in_progress"
        assert sm.states["BUILD"] == "pending"

    def test_from_dict(self):
        """Loads state from dict on session (popoto may return dict)."""
        states = {"ISSUE": "completed", "PLAN": "completed", "BUILD": "ready"}
        session = _make_session(stage_states=states)
        sm = PipelineStateMachine(session)
        assert sm.states["ISSUE"] == "completed"
        assert sm.states["BUILD"] == "ready"

    def test_from_none(self):
        """Handles None stage_states gracefully."""
        session = _make_session(stage_states=None)
        sm = PipelineStateMachine(session)
        assert sm.states["ISSUE"] == "ready"

    def test_invalid_json(self):
        """Handles corrupt JSON gracefully."""
        session = _make_session(stage_states="not valid json{{{")
        sm = PipelineStateMachine(session)
        # Falls back to defaults
        assert sm.states["ISSUE"] == "ready"

    def test_preserves_patch_cycle_count(self):
        """Loads _patch_cycle_count from state."""
        states = {"ISSUE": "completed", "_patch_cycle_count": 2}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        assert sm.patch_cycle_count == 2

    def test_missing_stages_get_defaults(self):
        """Stages not in the loaded state get pending."""
        states = {"ISSUE": "completed"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        assert sm.states["MERGE"] == "pending"


class TestStartStage:
    """Test PipelineStateMachine.start_stage()."""

    def test_start_issue(self):
        """ISSUE can always be started."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        sm.start_stage("ISSUE")
        assert sm.states["ISSUE"] == "in_progress"

    def test_start_plan_requires_issue_completed(self):
        """PLAN requires ISSUE to be completed."""
        states = {"ISSUE": "pending"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        with pytest.raises(ValueError, match="Cannot start PLAN"):
            sm.start_stage("PLAN")

    def test_start_plan_after_issue_completed(self):
        """PLAN can start when ISSUE is completed."""
        states = {"ISSUE": "completed"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.start_stage("PLAN")
        assert sm.states["PLAN"] == "in_progress"

    def test_start_build_requires_plan_completed(self):
        """BUILD requires PLAN to be completed."""
        states = {"ISSUE": "completed", "PLAN": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        with pytest.raises(ValueError, match="Cannot start BUILD"):
            sm.start_stage("BUILD")

    def test_start_invalid_stage(self):
        """Invalid stage name raises ValueError."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        with pytest.raises(ValueError, match="Invalid stage"):
            sm.start_stage("INVALID")

    def test_start_empty_string(self):
        """Empty string raises ValueError."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        with pytest.raises(ValueError, match="Invalid stage"):
            sm.start_stage("")

    def test_start_already_in_progress_is_noop(self):
        """Starting an already in_progress stage is a no-op."""
        states = {"ISSUE": "completed", "PLAN": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.start_stage("PLAN")  # should not raise
        assert sm.states["PLAN"] == "in_progress"

    def test_start_patch_after_test_failure(self):
        """PATCH can start after TEST fails."""
        states = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "completed",
            "TEST": "failed",
        }
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.start_stage("PATCH")
        assert sm.states["PATCH"] == "in_progress"

    def test_start_patch_without_failure_raises(self):
        """PATCH cannot start if TEST/REVIEW haven't completed or failed."""
        states = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "completed",
            "TEST": "pending",
        }
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        with pytest.raises(ValueError, match="Cannot start PATCH"):
            sm.start_stage("PATCH")

    def test_test_restart_after_patch(self):
        """TEST can restart after PATCH completes (cycle)."""
        states = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "completed",
            "TEST": "failed",
            "PATCH": "completed",
        }
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.start_stage("TEST")
        assert sm.states["TEST"] == "in_progress"


class TestCompleteStage:
    """Test PipelineStateMachine.complete_stage()."""

    def test_complete_in_progress_stage(self):
        """Completing an in_progress stage sets it to completed."""
        states = {"ISSUE": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.complete_stage("ISSUE")
        assert sm.states["ISSUE"] == "completed"

    def test_complete_marks_next_stage_ready(self):
        """Completing a stage marks the next stage as ready."""
        states = {"ISSUE": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.complete_stage("ISSUE")
        assert sm.states["PLAN"] == "ready"

    def test_double_complete_is_noop(self):
        """Completing an already completed stage is a no-op."""
        states = {"ISSUE": "completed"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.complete_stage("ISSUE")  # no-op
        assert sm.states["ISSUE"] == "completed"

    def test_complete_pending_raises(self):
        """Cannot complete a stage that's still pending."""
        states = {"PLAN": "pending"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        with pytest.raises(ValueError, match="Cannot complete stage PLAN"):
            sm.complete_stage("PLAN")

    def test_complete_invalid_stage(self):
        """Invalid stage name raises ValueError."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        with pytest.raises(ValueError, match="Invalid stage"):
            sm.complete_stage("BOGUS")

    def test_complete_patch_increments_cycle_count(self):
        """Completing PATCH increments the patch cycle counter."""
        states = {"PATCH": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.complete_stage("PATCH")
        assert sm.patch_cycle_count == 1

    def test_saves_to_session(self):
        """complete_stage persists state via session.save()."""
        states = {"ISSUE": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.complete_stage("ISSUE")
        session.save.assert_called()
        # Verify the saved data
        saved = json.loads(session.stage_states)
        assert saved["ISSUE"] == "completed"


class TestFailStage:
    """Test PipelineStateMachine.fail_stage()."""

    def test_fail_in_progress_stage(self):
        """Failing an in_progress stage sets it to failed."""
        states = {"TEST": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.fail_stage("TEST")
        assert sm.states["TEST"] == "failed"

    def test_fail_marks_patch_ready(self):
        """Failing TEST marks PATCH as ready (via failure edge)."""
        states = {"TEST": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.fail_stage("TEST")
        assert sm.states["PATCH"] == "ready"

    def test_fail_completed_is_noop(self):
        """Failing an already completed stage is a no-op."""
        states = {"TEST": "completed"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.fail_stage("TEST")
        assert sm.states["TEST"] == "completed"

    def test_fail_invalid_stage(self):
        """Invalid stage name raises ValueError."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        with pytest.raises(ValueError, match="Invalid stage"):
            sm.fail_stage("NOPE")


class TestDisplayProgress:
    """Test get_display_progress() and rendering helpers."""

    def test_excludes_patch(self):
        """PATCH is excluded from display progress."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        progress = sm.get_display_progress()
        assert "PATCH" not in progress
        assert "ISSUE" in progress
        assert "MERGE" in progress

    def test_returns_all_display_stages(self):
        """Returns exactly DISPLAY_STAGES."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        progress = sm.get_display_progress()
        assert list(progress.keys()) == [
            "ISSUE",
            "PLAN",
            "BUILD",
            "TEST",
            "REVIEW",
            "DOCS",
            "MERGE",
        ]

    def test_reflects_current_state(self):
        """Display progress reflects actual state."""
        states = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "in_progress",
        }
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        progress = sm.get_display_progress()
        assert progress["ISSUE"] == "completed"
        assert progress["PLAN"] == "completed"
        assert progress["BUILD"] == "in_progress"
        assert progress["TEST"] == "pending"

    def test_no_transitions_returns_all_pending(self):
        """With no transitions, ISSUE=ready, rest pending."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        progress = sm.get_display_progress()
        assert progress["ISSUE"] == "ready"
        assert progress["PLAN"] == "pending"


class TestCurrentAndNextStage:
    """Test current_stage() and next_stage()."""

    def test_current_stage_returns_in_progress(self):
        """current_stage returns the in_progress stage."""
        states = {"BUILD": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        assert sm.current_stage() == "BUILD"

    def test_current_stage_none_when_no_active(self):
        """current_stage returns None when no stage is in_progress."""
        states = {"ISSUE": "completed", "PLAN": "ready"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        assert sm.current_stage() is None

    def test_next_stage_from_current(self):
        """next_stage returns the next stage from the current in_progress."""
        states = {"ISSUE": "completed", "PLAN": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        result = sm.next_stage("success")
        assert result is not None
        assert result[0] == "BUILD"

    def test_next_stage_from_last_completed(self):
        """next_stage finds last completed when nothing in_progress."""
        states = {"ISSUE": "completed", "PLAN": "completed"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        result = sm.next_stage("success")
        assert result is not None
        assert result[0] == "BUILD"

    def test_next_stage_nothing_started(self):
        """next_stage returns ISSUE when nothing started."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        result = sm.next_stage()
        assert result is not None
        assert result[0] == "ISSUE"


class TestHasRemainingStages:
    """Test has_remaining_stages()."""

    def test_true_when_stages_pending(self):
        """Returns True when stages are pending."""
        states = {"ISSUE": "completed"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        assert sm.has_remaining_stages() is True

    def test_false_when_merge_completed(self):
        """Returns False when MERGE is completed."""
        states = {
            s: "completed"
            for s in ["ISSUE", "PLAN", "BUILD", "TEST", "REVIEW", "DOCS", "MERGE"]
        }
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        assert sm.has_remaining_stages() is False

    def test_true_when_in_progress(self):
        """Returns True when a stage is in_progress."""
        states = {"ISSUE": "completed", "PLAN": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        assert sm.has_remaining_stages() is True


class TestHasFailedStage:
    """Test has_failed_stage()."""

    def test_true_when_failed(self):
        """Returns True when a stage has failed."""
        states = {"TEST": "failed"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        assert sm.has_failed_stage() is True

    def test_false_when_no_failures(self):
        """Returns False when no stages have failed."""
        states = {"ISSUE": "completed", "PLAN": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        assert sm.has_failed_stage() is False


class TestClassifyOutcome:
    """Test classify_outcome() two-tier classification."""

    def test_non_end_turn_is_fail(self):
        """Non-end_turn stop_reason is classified as fail."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        assert sm.classify_outcome("BUILD", "budget_exceeded") == "fail"
        assert sm.classify_outcome("TEST", "rate_limited") == "fail"

    def test_end_turn_with_test_pass(self):
        """end_turn with test pass pattern is success."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        result = sm.classify_outcome("TEST", "end_turn", "42 passed, 0 warnings")
        assert result == "success"

    def test_end_turn_with_test_fail(self):
        """end_turn with test failure pattern is fail."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        result = sm.classify_outcome("TEST", "end_turn", "3 failed, 2 passed")
        assert result == "fail"

    def test_end_turn_build_with_pr(self):
        """end_turn BUILD with PR URL is success."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        result = sm.classify_outcome(
            "BUILD",
            "end_turn",
            "PR created: https://github.com/org/repo/pull/42",
        )
        assert result == "success"

    def test_ambiguous_when_no_pattern(self):
        """Returns ambiguous when no pattern matches."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        result = sm.classify_outcome("BUILD", "end_turn", "some random output")
        assert result == "ambiguous"

    def test_none_stop_reason_uses_patterns(self):
        """None stop_reason falls through to pattern matching."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        result = sm.classify_outcome("ISSUE", None, "issue created #42")
        assert result == "success"


class TestToDict:
    """Test serialization."""

    def test_to_dict_includes_all_fields(self):
        """to_dict includes states, cycle count, current, and remaining."""
        states = {"ISSUE": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        d = sm.to_dict()
        assert "states" in d
        assert "patch_cycle_count" in d
        assert "current_stage" in d
        assert "has_remaining" in d
        assert d["current_stage"] == "ISSUE"
