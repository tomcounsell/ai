"""E2E tests for deterministic pipeline routing.

Verifies that the pipeline graph correctly routes between stages
based on outcomes, with no LLM calls involved.
Uses the canonical PIPELINE_EDGES from bridge/pipeline_graph.py.
"""

import pytest

from bridge.pipeline_graph import (
    DISPLAY_STAGES,
    MAX_PATCH_CYCLES,
    PIPELINE_EDGES,
    STAGE_TO_SKILL,
    get_next_stage,
)


@pytest.mark.e2e
class TestHappyPathRouting:
    """Test the linear happy path: ISSUE → PLAN → BUILD → TEST → REVIEW → DOCS → MERGE."""

    @pytest.mark.parametrize(
        "current,expected_next",
        [
            ("ISSUE", "PLAN"),
            ("PLAN", "BUILD"),
            ("BUILD", "TEST"),
            ("TEST", "REVIEW"),
            ("REVIEW", "DOCS"),
            ("DOCS", "MERGE"),
        ],
    )
    def test_success_transitions(self, current, expected_next):
        result = get_next_stage(current, "success")
        assert result is not None
        stage, skill = result
        assert stage == expected_next
        assert skill == STAGE_TO_SKILL[expected_next]

    def test_merge_is_terminal(self):
        """MERGE with success should return None (pipeline complete)."""
        assert get_next_stage("MERGE", "success") is None

    def test_none_stage_starts_at_issue(self):
        """No current stage should start at ISSUE."""
        result = get_next_stage(None)
        assert result == ("ISSUE", "/do-issue")


@pytest.mark.e2e
class TestFailureCycles:
    """Test failure routing: TEST/REVIEW fail → PATCH → TEST."""

    def test_test_failure_routes_to_patch(self):
        result = get_next_stage("TEST", "fail")
        assert result is not None
        assert result[0] == "PATCH"
        assert result[1] == "/do-patch"

    def test_review_failure_routes_to_patch(self):
        result = get_next_stage("REVIEW", "fail")
        assert result is not None
        assert result[0] == "PATCH"

    def test_review_partial_routes_to_patch(self):
        """Review approved with tech debt → PATCH for cleanup."""
        result = get_next_stage("REVIEW", "partial")
        assert result is not None
        assert result[0] == "PATCH"

    def test_patch_success_routes_to_test(self):
        """After patching, always re-run tests."""
        result = get_next_stage("PATCH", "success")
        assert result is not None
        assert result[0] == "TEST"

    def test_patch_fail_also_routes_to_test(self):
        """Even failed patches route to TEST for verification."""
        result = get_next_stage("PATCH", "fail")
        assert result is not None
        assert result[0] == "TEST"

    def test_full_failure_cycle(self):
        """TEST → PATCH → TEST is the standard retry loop."""
        step1 = get_next_stage("TEST", "fail")
        assert step1[0] == "PATCH"

        step2 = get_next_stage("PATCH", "success", cycle_count=0)
        assert step2[0] == "TEST"

        # After passing, continue to REVIEW
        step3 = get_next_stage("TEST", "success")
        assert step3[0] == "REVIEW"


@pytest.mark.e2e
class TestPatchCycleLimit:
    """Test that patch cycles are bounded to prevent infinite loops."""

    def test_under_limit_allows_transition(self):
        result = get_next_stage("PATCH", "success", cycle_count=MAX_PATCH_CYCLES - 1)
        assert result is not None
        assert result[0] == "TEST"

    def test_at_limit_returns_none(self):
        """At max cycles, escalate to human (return None)."""
        result = get_next_stage("PATCH", "success", cycle_count=MAX_PATCH_CYCLES)
        assert result is None

    def test_over_limit_returns_none(self):
        result = get_next_stage("PATCH", "fail", cycle_count=MAX_PATCH_CYCLES + 1)
        assert result is None


@pytest.mark.e2e
class TestEdgeCases:
    """Test edge cases and unknown inputs."""

    def test_unknown_stage_returns_none(self):
        assert get_next_stage("NONEXISTENT", "success") is None

    def test_unknown_outcome_falls_back_to_success(self):
        """Unknown outcomes should fall back to the success edge."""
        result = get_next_stage("BUILD", "banana")
        assert result is not None
        assert result[0] == "TEST"  # Same as BUILD success

    def test_none_outcome_defaults_to_success(self):
        result = get_next_stage("BUILD", None)
        assert result is not None
        assert result[0] == "TEST"

    def test_every_stage_has_a_skill(self):
        """All stages in the graph should have a corresponding /do-* skill."""
        all_stages = set()
        for (stage, _outcome), next_stage in PIPELINE_EDGES.items():
            all_stages.add(stage)
            all_stages.add(next_stage)

        for stage in all_stages:
            assert stage in STAGE_TO_SKILL, f"{stage} missing from STAGE_TO_SKILL"

    def test_display_stages_exclude_patch(self):
        """PATCH is routing-only, not shown in progress displays."""
        assert "PATCH" not in DISPLAY_STAGES

    def test_display_stages_are_ordered(self):
        """Display stages should follow pipeline order."""
        expected = ["ISSUE", "PLAN", "BUILD", "TEST", "REVIEW", "DOCS", "MERGE"]
        assert DISPLAY_STAGES == expected
