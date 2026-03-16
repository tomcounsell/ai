"""Tests for bridge.pipeline_graph — canonical SDLC pipeline routing."""

from bridge.pipeline_graph import (
    DISPLAY_STAGES,
    MAX_PATCH_CYCLES,
    PIPELINE_EDGES,
    STAGE_TO_SKILL,
    get_next_stage,
)


class TestHappyPath:
    """Verify the happy path: ISSUE -> PLAN -> BUILD -> TEST -> REVIEW -> DOCS."""

    def test_issue_to_plan(self):
        result = get_next_stage("ISSUE", "success")
        assert result == ("PLAN", "/do-plan")

    def test_plan_to_build(self):
        result = get_next_stage("PLAN", "success")
        assert result == ("BUILD", "/do-build")

    def test_build_to_test(self):
        result = get_next_stage("BUILD", "success")
        assert result == ("TEST", "/do-test")

    def test_test_to_review(self):
        result = get_next_stage("TEST", "success")
        assert result == ("REVIEW", "/do-pr-review")

    def test_review_to_docs(self):
        result = get_next_stage("REVIEW", "success")
        assert result == ("DOCS", "/do-docs")

    def test_docs_to_merge(self):
        """DOCS success leads to MERGE stage with /do-merge skill."""
        result = get_next_stage("DOCS", "success")
        assert result == ("MERGE", "/do-merge")

    def test_merge_is_terminal(self):
        """MERGE has no outgoing success edge — it is the terminal stage."""
        result = get_next_stage("MERGE", "success")
        assert result is None

    def test_full_happy_path_traversal(self):
        """Walk the entire happy path and verify each transition."""
        expected_path = [
            ("ISSUE", "PLAN", "/do-plan"),
            ("PLAN", "BUILD", "/do-build"),
            ("BUILD", "TEST", "/do-test"),
            ("TEST", "REVIEW", "/do-pr-review"),
            ("REVIEW", "DOCS", "/do-docs"),
            ("DOCS", "MERGE", "/do-merge"),
        ]
        for current, expected_next, expected_skill in expected_path:
            result = get_next_stage(current, "success")
            assert result is not None, f"Expected transition from {current}"
            assert result == (expected_next, expected_skill), (
                f"From {current}: expected ({expected_next}, {expected_skill}), got {result}"
            )


class TestFailureCycles:
    """Verify cycle support for test failures and review feedback."""

    def test_test_failure_routes_to_patch(self):
        result = get_next_stage("TEST", "fail")
        assert result == ("PATCH", "/do-patch")

    def test_review_failure_routes_to_patch(self):
        result = get_next_stage("REVIEW", "fail")
        assert result == ("PATCH", "/do-patch")

    def test_patch_success_routes_to_test(self):
        result = get_next_stage("PATCH", "success")
        assert result == ("TEST", "/do-test")

    def test_patch_fail_routes_to_test(self):
        """Even a failed patch routes to TEST for re-verification."""
        result = get_next_stage("PATCH", "fail")
        assert result == ("TEST", "/do-test")

    def test_test_fail_patch_test_cycle(self):
        """Simulate: TEST fails -> PATCH -> TEST (cycle)."""
        # TEST fails
        step1 = get_next_stage("TEST", "fail")
        assert step1 == ("PATCH", "/do-patch")

        # PATCH completes
        step2 = get_next_stage("PATCH", "success", cycle_count=1)
        assert step2 == ("TEST", "/do-test")

        # TEST succeeds this time
        step3 = get_next_stage("TEST", "success")
        assert step3 == ("REVIEW", "/do-pr-review")

    def test_review_fail_patch_test_review_cycle(self):
        """Simulate: REVIEW fails -> PATCH -> TEST -> REVIEW (cycle)."""
        step1 = get_next_stage("REVIEW", "fail")
        assert step1 == ("PATCH", "/do-patch")

        step2 = get_next_stage("PATCH", "success", cycle_count=1)
        assert step2 == ("TEST", "/do-test")

        step3 = get_next_stage("TEST", "success")
        assert step3 == ("REVIEW", "/do-pr-review")


class TestMaxCycleLimit:
    """Verify max cycle counter prevents infinite loops."""

    def test_patch_within_limit(self):
        result = get_next_stage("PATCH", "success", cycle_count=MAX_PATCH_CYCLES - 1)
        assert result == ("TEST", "/do-test")

    def test_patch_at_limit_returns_none(self):
        result = get_next_stage("PATCH", "success", cycle_count=MAX_PATCH_CYCLES)
        assert result is None

    def test_patch_over_limit_returns_none(self):
        result = get_next_stage("PATCH", "success", cycle_count=MAX_PATCH_CYCLES + 1)
        assert result is None

    def test_max_patch_cycles_is_3(self):
        """Verify the default limit matches the plan specification."""
        assert MAX_PATCH_CYCLES == 3


class TestEdgeCases:
    """Handle unknown stages, None inputs, and invalid outcomes gracefully."""

    def test_none_stage_returns_first_stage(self):
        result = get_next_stage(None, "success")
        assert result == ("ISSUE", "/do-issue")

    def test_none_outcome_defaults_to_success(self):
        result = get_next_stage("TEST", None)
        assert result == ("REVIEW", "/do-pr-review")

    def test_unknown_stage_returns_none(self):
        result = get_next_stage("UNKNOWN", "success")
        assert result is None

    def test_unknown_outcome_falls_back_to_success(self):
        """Unknown outcomes should fall back to the success transition."""
        result = get_next_stage("TEST", "partial")
        assert result == ("REVIEW", "/do-pr-review")

    def test_unknown_outcome_on_unknown_stage(self):
        result = get_next_stage("NONEXISTENT", "weird_outcome")
        assert result is None


class TestExports:
    """Verify module exports are consistent and correct."""

    def test_display_stages_excludes_patch(self):
        assert "PATCH" not in DISPLAY_STAGES

    def test_display_stages_order(self):
        assert DISPLAY_STAGES == ["ISSUE", "PLAN", "BUILD", "TEST", "REVIEW", "DOCS", "MERGE"]

    def test_display_stages_includes_merge(self):
        assert "MERGE" in DISPLAY_STAGES

    def test_stage_to_skill_has_all_stages(self):
        """All stages in edges should have a skill mapping."""
        all_stages = set()
        for (stage, _), next_stage in PIPELINE_EDGES.items():
            all_stages.add(stage)
            all_stages.add(next_stage)
        for stage in all_stages:
            assert stage in STAGE_TO_SKILL, f"Stage {stage} missing from STAGE_TO_SKILL"

    def test_pipeline_edges_are_complete(self):
        """Every display stage (except MERGE which is terminal) should have at least a success edge."""
        for stage in DISPLAY_STAGES:
            if stage == "MERGE":
                continue  # MERGE is terminal, no outgoing edges
            assert (stage, "success") in PIPELINE_EDGES, f"Stage {stage} missing success edge"

    def test_stage_to_skill_values(self):
        """Verify specific skill command mappings."""
        assert STAGE_TO_SKILL["ISSUE"] == "/do-issue"
        assert STAGE_TO_SKILL["PLAN"] == "/do-plan"
        assert STAGE_TO_SKILL["BUILD"] == "/do-build"
        assert STAGE_TO_SKILL["TEST"] == "/do-test"
        assert STAGE_TO_SKILL["PATCH"] == "/do-patch"
        assert STAGE_TO_SKILL["REVIEW"] == "/do-pr-review"
        assert STAGE_TO_SKILL["DOCS"] == "/do-docs"
        assert STAGE_TO_SKILL["MERGE"] == "/do-merge"
