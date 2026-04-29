"""Tests for PM persona hardening.

Covers completion guards, child session timeout, and pipeline stage assertion.

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


class TestGateRecoveryBehavior:
    """Item 5 of sdlc-1155: PM persona documents gate-recovery behavior.

    The section must list blocker categories (including PARTIAL_PIPELINE_STATE),
    cross-link ``docs/sdlc/merge-troubleshooting.md``, and reference the G4
    convergence rule.

    Note (#1207): The Plan Completion Gate has been deleted; ``COMPLETION_GATE``
    is no longer a recognized blocker category, and the ``allow_unchecked: true``
    plan-frontmatter override no longer exists in any consumer. Tests for those
    have been removed accordingly.
    """

    def test_gate_recovery_section_exists(self, pm_persona_text):
        assert "Gate-Recovery Behavior" in pm_persona_text

    def test_rule_5_text_unchanged(self, pm_persona_text):
        # Rule 5 heading and its exact first sentence must both survive.
        assert "MERGE is Mandatory Before Dev-Session Sign-Off" in pm_persona_text
        assert (
            "If an open PR exists for the current issue, you must dispatch `/do-merge`"
            in pm_persona_text
        )

    def test_cross_link_to_merge_troubleshooting(self, pm_persona_text):
        assert "merge-troubleshooting.md" in pm_persona_text

    def test_blocker_categories_enumerated(self, pm_persona_text):
        for category in (
            "PIPELINE_STATE",
            "PARTIAL_PIPELINE_STATE",
            "REVIEW_COMMENT",
            "LOCKFILE",
            "FULL_SUITE",
            "MERGE_CONFLICT",
        ):
            assert category in pm_persona_text, f"Missing category: {category}"

    def test_g4_convergence_rule_referenced(self, pm_persona_text):
        assert "G4" in pm_persona_text or "oscillation" in pm_persona_text.lower()


class TestMergeTroubleshootingDoc:
    """Item 6 of sdlc-1155: the troubleshooting playbook exists with the
    expected sections.

    Note (#1207): originally seven sections; the unchecked-plan-checkboxes
    section was removed when the gate was deleted. The remaining six
    sections stay.
    """

    def _playbook_text(self):
        from pathlib import Path

        p = Path("docs/sdlc/merge-troubleshooting.md")
        return p.read_text()

    def test_playbook_exists(self):
        from pathlib import Path

        assert Path("docs/sdlc/merge-troubleshooting.md").exists()

    def test_expected_sections_present(self):
        text = self._playbook_text()
        for heading in (
            "Merge Conflict",
            "G4 Oscillation",
            "Stale Review",
            "Lockfile Drift",
            "Flake False Regression",
            "Partial Pipeline State",
        ):
            assert f"## {heading}" in text, f"Missing section: {heading}"

    def test_tokeniser_helper_reachable_from_validator(self):
        """Item 7: the tokeniser helper exists and is reachable."""
        import importlib.util
        from pathlib import Path

        module_path = Path(".claude/hooks/validators/validate_merge_guard.py")
        spec = importlib.util.spec_from_file_location("vmg", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert hasattr(module, "_extract_executed_commands")
        assert hasattr(module, "_merge_cmd_in_command")
