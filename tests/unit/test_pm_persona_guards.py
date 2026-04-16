"""Tests for PM persona hardening — completion guards, child session timeout, pipeline stage assertion.

Verifies that the PM persona file (config/personas/project-manager.md) contains the
three self-monitoring guard sections required by issue #1007:
1. Pre-Completion Checklist (completion guard — checks for open PRs before exiting)
2. Child Session Monitoring (timeout — fallback if child session is stuck)
3. Exit Validation (pipeline stage assertion — refuses to exit with incomplete stages)

These are prompt-level tests — they validate that the persona text contains the
required behavioral instructions, not that infrastructure enforces them.
"""

import pytest

# Path to the PM persona file
PM_PERSONA_PATH = "config/personas/project-manager.md"


@pytest.fixture
def pm_persona_text():
    """Load the PM persona markdown file."""
    with open(PM_PERSONA_PATH) as f:
        return f.read()


class TestCompletionGuard:
    """Rule: PM must check for open PRs before exiting and invoke /do-merge if any exist."""

    def test_pre_completion_section_exists(self, pm_persona_text):
        """PM persona must have a Pre-Completion Checklist section."""
        assert "Pre-Completion Checklist" in pm_persona_text

    def test_gh_pr_list_check_mentioned(self, pm_persona_text):
        """PM must be instructed to run gh pr list to check for open PRs."""
        assert "gh pr list" in pm_persona_text
        # Specifically checking for the slug branch pattern
        assert "session/{slug}" in pm_persona_text

    def test_do_merge_invocation_mentioned(self, pm_persona_text):
        """PM must be instructed to invoke /do-merge if open PRs exist."""
        # The completion guard must reference /do-merge as the action
        assert "/do-merge" in pm_persona_text

    def test_refuses_to_exit_with_open_prs(self, pm_persona_text):
        """PM persona must explicitly state it refuses to exit with open PRs."""
        # Look for language about refusing to exit / not completing with open PRs
        lower = pm_persona_text.lower()
        assert "refuse" in lower or "must not exit" in lower or "do not exit" in lower


class TestChildSessionTimeout:
    """Rule: PM must check child status after dispatch and fallback if stuck >5 min."""

    def test_child_monitoring_section_exists(self, pm_persona_text):
        """PM persona must have a Child Session Monitoring section."""
        assert "Child Session Monitoring" in pm_persona_text

    def test_timeout_threshold_specified(self, pm_persona_text):
        """PM must specify the 5-minute timeout threshold for pending children."""
        assert "5 minute" in pm_persona_text or "5-minute" in pm_persona_text

    def test_valor_session_status_check(self, pm_persona_text):
        """PM must be instructed to check child status via valor_session status."""
        assert (
            "valor_session status" in pm_persona_text or "valor-session status" in pm_persona_text
        )

    def test_fallback_for_read_only_stages(self, pm_persona_text):
        """PM must describe fallback for stages that don't require dev permissions."""
        lower = pm_persona_text.lower()
        # Should mention running stages directly for read-only work
        assert "directly" in lower or "fallback" in lower

    def test_escalation_for_dev_stages(self, pm_persona_text):
        """PM must describe escalation path when dev-permission stages are stuck."""
        lower = pm_persona_text.lower()
        assert "escalat" in lower  # escalate, escalation


class TestPipelineStageAssertion:
    """Rule: PM must validate all display stages completed before exiting."""

    def test_exit_validation_section_exists(self, pm_persona_text):
        """PM persona must have an Exit Validation section."""
        assert "Exit Validation" in pm_persona_text

    def test_sdlc_stage_query_referenced(self, pm_persona_text):
        """PM must be instructed to query sdlc_stage_query on exit."""
        assert "sdlc_stage_query" in pm_persona_text

    def test_display_stages_referenced(self, pm_persona_text):
        """PM must reference the canonical display stages list."""
        # Must mention the key stages that need to be completed
        for stage in [
            "ISSUE",
            "PLAN",
            "CRITIQUE",
            "BUILD",
            "TEST",
            "REVIEW",
            "DOCS",
            "MERGE",
        ]:
            assert stage in pm_persona_text

    def test_refuses_exit_with_incomplete_stages(self, pm_persona_text):
        """PM must refuse to exit if stages are incomplete."""
        lower = pm_persona_text.lower()
        # Should contain language about not exiting with incomplete stages
        has_refuse = "refuse" in lower
        has_must_not = "must not exit" in lower or "do not exit" in lower
        has_incomplete = "incomplete" in lower or "not completed" in lower
        assert has_refuse or has_must_not or has_incomplete

    def test_skip_justification_required(self, pm_persona_text):
        """PM must explain why a stage was legitimately skipped if any are missing."""
        lower = pm_persona_text.lower()
        assert "skip" in lower and ("justif" in lower or "explain" in lower or "reason" in lower)


class TestExistingRulesPreserved:
    """Verify the three new sections do not weaken existing Rules 1-4."""

    def test_rule_1_critique_mandatory(self, pm_persona_text):
        """Rule 1 — CRITIQUE is Mandatory After PLAN must still exist."""
        assert "Rule 1" in pm_persona_text
        assert "CRITIQUE is Mandatory After PLAN" in pm_persona_text

    def test_rule_2_review_mandatory(self, pm_persona_text):
        """Rule 2 — REVIEW is Mandatory After TEST must still exist."""
        assert "Rule 2" in pm_persona_text
        assert "REVIEW is Mandatory After TEST" in pm_persona_text

    def test_rule_3_single_issue_scoping(self, pm_persona_text):
        """Rule 3 — Single-Issue Scoping must still exist."""
        assert "Rule 3" in pm_persona_text
        assert "Single-Issue Scoping" in pm_persona_text

    def test_rule_4_wait_for_dev_session(self, pm_persona_text):
        """Rule 4 — Wait for Dev Session After Dispatch must still exist."""
        assert "Rule 4" in pm_persona_text
        assert "Wait for Dev Session" in pm_persona_text
