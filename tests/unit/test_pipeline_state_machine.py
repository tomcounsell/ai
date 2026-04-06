"""Tests for bridge.pipeline_state — PipelineStateMachine.

Tests cover:
- Initialization (default, from JSON, from dict, from None)
- start_stage() ordering enforcement
- complete_stage() transitions and next-stage marking
- fail_stage() and PATCH/CRITIQUE cycle handling
- get_display_progress() excludes PATCH
- get_display_progress(slug=...) artifact-based inference
- current_stage() and next_stage() queries
- has_remaining_stages() and has_failed_stage()
- classify_outcome() three-tier classification (OUTCOME contract, stop_reason, text patterns)
- Edge cases: double-complete, invalid stages, cycle re-entry
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from bridge.pipeline_state import PipelineStateMachine, StageStates


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
        assert sm.states["CRITIQUE"] == "pending"
        assert sm.states["MERGE"] == "pending"
        assert sm.patch_cycle_count == 0
        assert sm.critique_cycle_count == 0

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

    def test_preserves_critique_cycle_count(self):
        """Loads _critique_cycle_count from state."""
        states = {"ISSUE": "completed", "_critique_cycle_count": 1}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        assert sm.critique_cycle_count == 1

    def test_missing_stages_get_defaults(self):
        """Stages not in the loaded state get pending."""
        states = {"ISSUE": "completed"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        assert sm.states["MERGE"] == "pending"
        assert sm.states["CRITIQUE"] == "pending"


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

    def test_start_critique_requires_plan_completed(self):
        """CRITIQUE requires PLAN to be completed."""
        states = {"ISSUE": "completed", "PLAN": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        with pytest.raises(ValueError, match="Cannot start CRITIQUE"):
            sm.start_stage("CRITIQUE")

    def test_start_critique_after_plan_completed(self):
        """CRITIQUE can start when PLAN is completed."""
        states = {"ISSUE": "completed", "PLAN": "completed"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.start_stage("CRITIQUE")
        assert sm.states["CRITIQUE"] == "in_progress"

    def test_start_build_requires_critique_completed(self):
        """BUILD requires CRITIQUE to be completed (not PLAN directly)."""
        states = {"ISSUE": "completed", "PLAN": "completed", "CRITIQUE": "pending"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        with pytest.raises(ValueError, match="Cannot start BUILD"):
            sm.start_stage("BUILD")

    def test_start_build_after_critique_completed(self):
        """BUILD can start when CRITIQUE is completed."""
        states = {"ISSUE": "completed", "PLAN": "completed", "CRITIQUE": "completed"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.start_stage("BUILD")
        assert sm.states["BUILD"] == "in_progress"

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
            "CRITIQUE": "completed",
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
            "CRITIQUE": "completed",
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
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "failed",
            "PATCH": "completed",
        }
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.start_stage("TEST")
        assert sm.states["TEST"] == "in_progress"

    def test_plan_restart_after_critique_failure(self):
        """PLAN can restart after CRITIQUE fails (revision cycle)."""
        states = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "CRITIQUE": "failed",
        }
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.start_stage("PLAN")
        assert sm.states["PLAN"] == "in_progress"


class TestCompleteStage:
    """Test PipelineStateMachine.complete_stage()."""

    def test_complete_in_progress_stage(self):
        """Completing an in_progress stage sets it to completed."""
        states = {"ISSUE": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.complete_stage("ISSUE")
        assert sm.states["ISSUE"] == "completed"

    def test_complete_issue_marks_plan_ready(self):
        """Completing ISSUE marks PLAN as ready."""
        states = {"ISSUE": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.complete_stage("ISSUE")
        assert sm.states["PLAN"] == "ready"

    def test_complete_plan_marks_critique_ready(self):
        """Completing PLAN marks CRITIQUE as ready (not BUILD)."""
        states = {"ISSUE": "completed", "PLAN": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.complete_stage("PLAN")
        assert sm.states["CRITIQUE"] == "ready"

    def test_complete_critique_marks_build_ready(self):
        """Completing CRITIQUE marks BUILD as ready."""
        states = {"ISSUE": "completed", "PLAN": "completed", "CRITIQUE": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.complete_stage("CRITIQUE")
        assert sm.states["BUILD"] == "ready"

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

    def test_fail_critique_marks_plan_ready(self):
        """Failing CRITIQUE marks PLAN as ready (revision cycle)."""
        states = {"ISSUE": "completed", "PLAN": "completed", "CRITIQUE": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.fail_stage("CRITIQUE")
        assert sm.states["PLAN"] == "ready"

    def test_fail_critique_increments_critique_cycle_count(self):
        """Failing CRITIQUE increments the critique cycle counter."""
        states = {"CRITIQUE": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.fail_stage("CRITIQUE")
        assert sm.critique_cycle_count == 1

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

    def test_includes_critique(self):
        """CRITIQUE is included in display progress."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        progress = sm.get_display_progress()
        assert "CRITIQUE" in progress

    def test_returns_all_display_stages(self):
        """Returns exactly DISPLAY_STAGES."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        progress = sm.get_display_progress()
        assert list(progress.keys()) == [
            "ISSUE",
            "PLAN",
            "CRITIQUE",
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
            "CRITIQUE": "completed",
            "BUILD": "in_progress",
        }
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        progress = sm.get_display_progress()
        assert progress["ISSUE"] == "completed"
        assert progress["PLAN"] == "completed"
        assert progress["CRITIQUE"] == "completed"
        assert progress["BUILD"] == "in_progress"
        assert progress["TEST"] == "pending"

    def test_no_transitions_returns_all_pending(self):
        """With no transitions, ISSUE=ready, rest pending."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        progress = sm.get_display_progress()
        assert progress["ISSUE"] == "ready"
        assert progress["PLAN"] == "pending"

    def test_no_slug_parameter_accepted(self):
        """get_display_progress() takes no slug parameter (artifact inference removed)."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        with pytest.raises(TypeError):
            sm.get_display_progress(slug="some-slug")

    def test_infer_method_deleted(self):
        """_infer_stage_from_artifacts() no longer exists (deleted in #729)."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        assert not hasattr(sm, "_infer_stage_from_artifacts"), (
            "_infer_stage_from_artifacts still exists — should have been deleted"
        )

    def test_returns_stored_state_only(self):
        """get_display_progress returns exactly what is stored — no inference."""
        # Mark DOCS as pending — even if docs/ files exist on disk, it must stay pending
        session = _make_session(stage_states=json.dumps({"DOCS": "pending"}))
        sm = PipelineStateMachine(session)
        progress = sm.get_display_progress()
        assert progress["DOCS"] in ("pending", "ready"), (
            "DOCS was inferred as completed from artifacts — inference must be removed"
        )


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
        assert result[0] == "CRITIQUE"

    def test_next_stage_from_last_completed(self):
        """next_stage finds last completed when nothing in_progress."""
        states = {"ISSUE": "completed", "PLAN": "completed"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        result = sm.next_stage("success")
        assert result is not None
        assert result[0] == "CRITIQUE"

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
            for s in [
                "ISSUE",
                "PLAN",
                "CRITIQUE",
                "BUILD",
                "TEST",
                "REVIEW",
                "DOCS",
                "MERGE",
            ]
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
        assert sm.classify_outcome("BUILD", "timeout") == "fail"
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

    def test_critique_ready_to_build_is_success(self):
        """CRITIQUE with 'ready to build' pattern is success."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        result = sm.classify_outcome("CRITIQUE", "end_turn", "Verdict: READY TO BUILD")
        assert result == "success"

    def test_critique_needs_revision_is_fail(self):
        """CRITIQUE with 'needs revision' pattern is fail."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        result = sm.classify_outcome(
            "CRITIQUE", "end_turn", "Verdict: NEEDS REVISION - 2 blockers found"
        )
        assert result == "fail"

    def test_critique_major_rework_is_ambiguous(self):
        """CRITIQUE with 'major rework' pattern is ambiguous (escalate to human)."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        result = sm.classify_outcome("CRITIQUE", "end_turn", "Verdict: MAJOR REWORK required")
        assert result == "ambiguous"


class TestClassifyOutcomeContract:
    """Test Tier 0 OUTCOME contract parsing in classify_outcome()."""

    def test_valid_outcome_success(self):
        """Valid OUTCOME block with status=success returns success."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        tail = 'Build complete. <!-- OUTCOME {"status":"success","stage":"BUILD"} -->'
        assert sm.classify_outcome("BUILD", "end_turn", tail) == "success"

    def test_valid_outcome_fail(self):
        """Valid OUTCOME block with status=fail returns fail."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        tail = 'Tests failed. <!-- OUTCOME {"status":"fail","stage":"TEST"} -->'
        assert sm.classify_outcome("TEST", "end_turn", tail) == "fail"

    def test_valid_outcome_partial(self):
        """Valid OUTCOME block with status=partial returns partial."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        tail = (
            "Review done. "
            '<!-- OUTCOME {"status":"partial","stage":"REVIEW",'
            '"artifacts":{"findings":3}} -->'
        )
        assert sm.classify_outcome("REVIEW", "end_turn", tail) == "partial"

    def test_malformed_json_falls_through(self):
        """Malformed JSON in OUTCOME block falls through to Tier 2."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        tail = "<!-- OUTCOME {not valid json} --> 42 passed, 0 warnings"
        assert sm.classify_outcome("TEST", "end_turn", tail) == "success"

    def test_missing_status_key_falls_through(self):
        """OUTCOME block without status key falls through to Tier 2."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        tail = '<!-- OUTCOME {"stage":"BUILD"} --> PR created: https://github.com/org/repo/pull/42'
        assert sm.classify_outcome("BUILD", "end_turn", tail) == "success"

    def test_no_outcome_block_falls_through(self):
        """No OUTCOME block in output falls through to Tier 2."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        tail = "42 passed, 0 warnings"
        assert sm.classify_outcome("TEST", "end_turn", tail) == "success"

    def test_stage_mismatch_falls_through(self):
        """OUTCOME block with mismatched stage falls through to Tier 2."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        # OUTCOME says BUILD but we expect REVIEW
        tail = '<!-- OUTCOME {"status":"success","stage":"BUILD"} --> approved'
        assert sm.classify_outcome("REVIEW", "end_turn", tail) == "success"

    def test_multiple_outcome_blocks_uses_last(self):
        """Multiple OUTCOME blocks: uses the last one."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        tail = (
            '<!-- OUTCOME {"status":"fail","stage":"TEST"} --> '
            '<!-- OUTCOME {"status":"success","stage":"TEST"} -->'
        )
        assert sm.classify_outcome("TEST", "end_turn", tail) == "success"

    def test_outcome_takes_priority_over_text_patterns(self):
        """OUTCOME contract is used even when text patterns would match differently."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        # Text says "approved" (would match success in Tier 2) but OUTCOME says partial
        tail = 'approved <!-- OUTCOME {"status":"partial","stage":"REVIEW"} -->'
        assert sm.classify_outcome("REVIEW", "end_turn", tail) == "partial"

    def test_outcome_without_stage_field_still_works(self):
        """OUTCOME block without stage field is accepted (no mismatch check)."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        tail = '<!-- OUTCOME {"status":"success"} -->'
        assert sm.classify_outcome("BUILD", "end_turn", tail) == "success"

    def test_empty_output_tail(self):
        """Empty output_tail returns ambiguous (no OUTCOME, no patterns)."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        assert sm.classify_outcome("BUILD", "end_turn", "") == "ambiguous"

    def test_outcome_contract_takes_priority_over_sdk_failure(self):
        """Tier 0 (OUTCOME contract) fires before Tier 1 (SDK stop_reason).

        Even with a non-end_turn stop_reason like "timeout", a valid OUTCOME
        contract in the output takes priority and returns its status.
        """
        session = _make_session()
        sm = PipelineStateMachine(session)
        tail = '<!-- OUTCOME {"status":"success","stage":"BUILD"} -->'
        result = sm.classify_outcome("BUILD", "timeout", tail)
        assert result == "success"


class TestParseOutcomeContract:
    """Test the _parse_outcome_contract() module-level function directly."""

    def test_none_input(self):
        """None-like input returns None."""
        from bridge.pipeline_state import _parse_outcome_contract

        assert _parse_outcome_contract("") is None
        assert _parse_outcome_contract(None) is None

    def test_valid_contract(self):
        """Valid contract is parsed correctly."""
        from bridge.pipeline_state import _parse_outcome_contract

        result = _parse_outcome_contract('<!-- OUTCOME {"status":"success","stage":"BUILD"} -->')
        assert result == {"status": "success", "stage": "BUILD"}

    def test_with_artifacts(self):
        """Contract with artifacts field is parsed correctly."""
        from bridge.pipeline_state import _parse_outcome_contract

        result = _parse_outcome_contract(
            '<!-- OUTCOME {"status":"partial","stage":"REVIEW","artifacts":{"findings":3}} -->'
        )
        assert result["status"] == "partial"
        assert result["artifacts"]["findings"] == 3


class TestToDict:
    """Test serialization."""

    def test_to_dict_includes_all_fields(self):
        """to_dict includes states, cycle counts, current, and remaining."""
        states = {"ISSUE": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        d = sm.to_dict()
        assert "states" in d
        assert "patch_cycle_count" in d
        assert "critique_cycle_count" in d
        assert "current_stage" in d
        assert "has_remaining" in d
        assert d["current_stage"] == "ISSUE"


class TestStageStatesValidation:
    """Test StageStates Pydantic model validation."""

    def test_valid_stages(self):
        """Valid stage names and statuses pass validation."""
        data = {"ISSUE": "completed", "PLAN": "in_progress", "BUILD": "pending"}
        ss = StageStates.from_dict(data)
        assert ss.stages["ISSUE"] == "completed"
        assert ss.stages["PLAN"] == "in_progress"
        assert ss.stages["BUILD"] == "pending"

    def test_unknown_stage_names_dropped(self):
        """Unknown stage names are silently dropped."""
        data = {"ISSUE": "completed", "UNKNOWN_STAGE": "pending", "BUILD": "ready"}
        ss = StageStates.from_dict(data)
        assert "UNKNOWN_STAGE" not in ss.stages
        assert ss.stages["ISSUE"] == "completed"
        assert ss.stages["BUILD"] == "ready"

    def test_unknown_status_defaults_to_pending(self):
        """Unknown status values default to 'pending'."""
        data = {"ISSUE": "completed", "PLAN": "some_invalid_status"}
        ss = StageStates.from_dict(data)
        assert ss.stages["PLAN"] == "pending"

    def test_metadata_keys_skipped(self):
        """Internal metadata keys (starting with _) are skipped."""
        data = {"ISSUE": "completed", "_patch_cycle_count": 2, "_critique_cycle_count": 1}
        ss = StageStates.from_dict(data)
        assert "_patch_cycle_count" not in ss.stages
        assert ss.stages["ISSUE"] == "completed"

    def test_empty_dict(self):
        """Empty dict produces empty stages."""
        ss = StageStates.from_dict({})
        assert ss.stages == {}

    def test_to_dict_roundtrip(self):
        """to_dict returns a plain dict."""
        data = {"ISSUE": "completed", "PLAN": "ready"}
        ss = StageStates.from_dict(data)
        result = ss.to_dict()
        assert isinstance(result, dict)
        assert result == {"ISSUE": "completed", "PLAN": "ready"}

    def test_save_validates_states(self):
        """_save() validates via StageStates before persisting."""
        states = {"ISSUE": "completed", "PLAN": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        sm.complete_stage("PLAN")
        # After save, the persisted data should be valid
        saved = json.loads(session.stage_states)
        assert saved["PLAN"] == "completed"
        assert saved["CRITIQUE"] == "ready"

    def test_save_handles_validation_error_gracefully(self):
        """If StageStates validation fails, _save still persists data."""
        states = {"ISSUE": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        sm = PipelineStateMachine(session)
        # Force an invalid state to test error handling
        sm.states["ISSUE"] = "in_progress"
        sm._save()
        # Should not raise, data should still be saved
        session.save.assert_called()


class TestArtifactInferenceDeleted:
    """Tests verifying that artifact inference was removed (issue #729).

    _infer_stage_from_artifacts() and the slug= parameter to get_display_progress()
    were deleted as part of SDLC Stage Skip Prevention. These tests confirm deletion.
    """

    def test_infer_method_does_not_exist(self):
        """_infer_stage_from_artifacts() must not exist on PipelineStateMachine."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        assert not hasattr(sm, "_infer_stage_from_artifacts"), (
            "_infer_stage_from_artifacts still exists — must be deleted per #729"
        )

    def test_get_display_progress_no_slug_parameter(self):
        """get_display_progress() must not accept a slug parameter."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        with pytest.raises(TypeError):
            sm.get_display_progress(slug="some-slug")

    def test_display_progress_returns_stored_state_only(self):
        """get_display_progress() returns stored state — no artifact inference."""
        session = _make_session(stage_states=json.dumps({"DOCS": "pending"}))
        sm = PipelineStateMachine(session)
        progress = sm.get_display_progress()
        # DOCS must reflect stored pending, not inferred from any artifacts
        assert progress["DOCS"] in ("pending", "ready"), (
            "DOCS was inferred as completed — artifact inference must be removed"
        )

    def test_display_progress_plan_not_inferred_from_files(self):
        """PLAN must not be inferred from docs/plans/ files."""
        session = _make_session()
        sm = PipelineStateMachine(session)
        progress = sm.get_display_progress()
        # With empty stored state, PLAN should be pending — not completed from plan files
        assert progress["PLAN"] == "pending", (
            "PLAN was inferred as completed from plan file — artifact inference must be removed"
        )

    def test_subprocess_not_used_in_get_display_progress(self):
        """get_display_progress() does not call subprocess (no gh CLI calls)."""
        import subprocess

        session = _make_session(stage_states=json.dumps({"BUILD": "completed"}))
        sm = PipelineStateMachine(session)
        # Should not make any subprocess calls
        original_run = subprocess.run
        calls = []

        def mock_run(*args, **kwargs):
            calls.append(args)
            return original_run(*args, **kwargs)

        subprocess.run = mock_run
        try:
            sm.get_display_progress()
        finally:
            subprocess.run = original_run
        assert len(calls) == 0, "get_display_progress() made subprocess calls — inference removed"


class TestSaveWarningOnFailure:
    """Test that _save() logs warning when session.save() raises."""

    def test_save_logs_warning_on_session_save_failure(self):
        """_save() logs warning when session.save() raises an exception."""
        states = {"ISSUE": "in_progress"}
        session = _make_session(stage_states=json.dumps(states))
        session.save.side_effect = Exception("Redis connection failed")

        sm = PipelineStateMachine(session)
        import logging

        with patch.object(logging.getLogger("bridge.pipeline_state"), "warning") as mock_warn:
            sm._save()
            mock_warn.assert_called_once()
            assert "Failed to save" in mock_warn.call_args[0][0]


class TestRecordStageCompletionDeleted:
    """Test that record_stage_completion is no longer importable."""

    def test_record_stage_completion_not_importable(self):
        """record_stage_completion has been deleted from the module."""
        import bridge.pipeline_state as mod

        assert not hasattr(mod, "record_stage_completion")
