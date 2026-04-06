"""Integration tests for pipeline stage display — stored-state-only behavior.

Tests verify that `get_display_progress()` returns stored state exclusively,
with no artifact inference. The `_infer_stage_from_artifacts()` method has been
deleted as part of issue #729 (SDLC Stage Skip Prevention).

All tests use mocked AgentSession (no Redis required) since we are testing
the state machine logic, not storage.
"""

import json
from unittest.mock import MagicMock

import pytest

from bridge.pipeline_graph import DISPLAY_STAGES
from bridge.pipeline_state import PipelineStateMachine


def _make_state_machine(stage_states=None):
    """Create a PipelineStateMachine with a mock AgentSession.

    The session is mocked because it requires Redis.
    """
    session = MagicMock()
    if stage_states is None:
        session.stage_states = None
    elif isinstance(stage_states, dict):
        session.stage_states = json.dumps(stage_states)
    else:
        session.stage_states = stage_states
    return PipelineStateMachine(session=session)


# ---------------------------------------------------------------------------
# Tests: _infer_stage_from_artifacts is deleted
# ---------------------------------------------------------------------------


class TestArtifactInferenceDeleted:
    """Verify that artifact inference was removed entirely."""

    def test_infer_method_does_not_exist(self):
        """_infer_stage_from_artifacts should not exist on PipelineStateMachine."""
        sm = _make_state_machine()
        assert not hasattr(sm, "_infer_stage_from_artifacts"), (
            "_infer_stage_from_artifacts() still exists — it should have been deleted"
        )

    def test_get_display_progress_takes_no_slug(self):
        """get_display_progress() should not accept a slug parameter."""
        sm = _make_state_machine()
        # Calling with slug= should raise TypeError (unexpected keyword argument)
        with pytest.raises(TypeError):
            sm.get_display_progress(slug="some-slug")


# ---------------------------------------------------------------------------
# Tests: get_display_progress returns stored state only
# ---------------------------------------------------------------------------


class TestDisplayProgress:
    """Test get_display_progress() with stored state only."""

    def test_display_progress_returns_stored_state(self):
        """get_display_progress returns exactly the stored state for known stages."""
        sm = _make_state_machine({"PLAN": "completed", "BUILD": "in_progress"})
        progress = sm.get_display_progress()

        assert progress["PLAN"] == "completed"
        assert progress["BUILD"] == "in_progress"

    def test_display_progress_returns_all_display_stages(self):
        """Result contains exactly the DISPLAY_STAGES keys."""
        sm = _make_state_machine()
        progress = sm.get_display_progress()

        assert set(progress.keys()) == set(DISPLAY_STAGES)

    def test_display_progress_excludes_patch(self):
        """PATCH is not a display stage and should not appear in the result."""
        sm = _make_state_machine({"PATCH": "completed"})
        progress = sm.get_display_progress()

        assert "PATCH" not in progress

    def test_display_progress_missing_stages_are_pending(self):
        """Stages not in stored state default to 'pending'."""
        sm = _make_state_machine({"ISSUE": "completed"})
        progress = sm.get_display_progress()

        for stage in DISPLAY_STAGES:
            if stage == "ISSUE":
                assert progress[stage] == "completed"
            else:
                assert progress[stage] in ("pending", "ready")

    def test_display_progress_no_slug_argument(self):
        """get_display_progress() takes no arguments."""
        sm = _make_state_machine({"DOCS": "completed"})
        # Should not raise
        progress = sm.get_display_progress()
        assert progress["DOCS"] == "completed"

    def test_display_progress_with_empty_state(self):
        """Empty stored state returns all stages as pending/ready."""
        sm = _make_state_machine({})
        progress = sm.get_display_progress()

        assert isinstance(progress, dict)
        assert set(progress.keys()) == set(DISPLAY_STAGES)
        # Fresh state machine marks ISSUE as ready
        assert progress.get("ISSUE") in ("pending", "ready")

    def test_display_progress_full_pipeline_completed(self):
        """All display stages completed — no artifact inference needed."""
        all_completed = {stage: "completed" for stage in DISPLAY_STAGES}
        sm = _make_state_machine(all_completed)
        progress = sm.get_display_progress()

        for stage in DISPLAY_STAGES:
            assert progress[stage] == "completed", (
                f"Expected {stage}=completed but got {progress[stage]}"
            )

    def test_display_progress_does_not_infer_from_plan_file(self):
        """PLAN should NOT be inferred as completed just because a plan file exists.

        This was the root cause of issue #729. A plan file in docs/plans/ must NOT
        cause PLAN to appear as completed — only stored state counts.
        """
        # Empty stored state — PLAN should remain pending even if plan files exist on disk
        sm = _make_state_machine({})
        progress = sm.get_display_progress()

        # PLAN must not be completed from artifact inference
        assert progress.get("PLAN") != "completed", (
            "PLAN was inferred as completed from artifacts — artifact inference must be deleted"
        )

    def test_display_progress_does_not_infer_docs_from_pr_files(self):
        """DOCS should NOT be inferred from PR file changes.

        This was the specific failure in issue #723 that triggered #729.
        BUILD creating a docs/ file must not satisfy the DOCS stage.
        """
        # State with BUILD completed but DOCS still pending
        sm = _make_state_machine({"BUILD": "completed"})
        progress = sm.get_display_progress()

        # DOCS must reflect stored state, not inferred from PR files
        assert progress.get("DOCS") != "completed", (
            "DOCS was inferred as completed from PR files — artifact inference must be deleted"
        )

    def test_display_progress_stored_state_is_authoritative(self):
        """Stored state is the only source of truth — no fallback or override."""
        # Explicitly mark REVIEW as in_progress in stored state
        sm = _make_state_machine({"REVIEW": "in_progress"})
        progress = sm.get_display_progress()

        # Even if a PR were approved in GitHub, stored in_progress must not be overridden
        assert progress["REVIEW"] == "in_progress", (
            "Stored in_progress was overridden — artifact inference must not exist"
        )
