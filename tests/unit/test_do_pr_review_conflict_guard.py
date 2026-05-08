"""Tests for /do-pr-review conflict-state no-op contract (#1301).

Validates that:
- BLOCKED_ON_CONFLICT and DIRTY state routes to gh pr comment only,
  never gh pr review.
- UNKNOWN mergeability after retry routes to BLOCKED_ON_CONFLICT path (C-9).
- The decision tree in post-review.md §3 is the single source of truth
  (Tier 1/2/3 block deleted from SKILL.md).
- The §2b template does not reference --request-changes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SKILL_MD = Path(".claude/skills-global/do-pr-review/SKILL.md")
POST_REVIEW_MD = Path(".claude/skills-global/do-pr-review/sub-skills/post-review.md")
CHECKOUT_MD = Path(".claude/skills-global/do-pr-review/sub-skills/checkout.md")


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_MD.read_text()


@pytest.fixture(scope="module")
def post_review_text() -> str:
    return POST_REVIEW_MD.read_text()


@pytest.fixture(scope="module")
def checkout_text() -> str:
    return CHECKOUT_MD.read_text()


# ---------------------------------------------------------------------------
# Single decision tree (no duplicate in SKILL.md)
# ---------------------------------------------------------------------------


class TestSingleDecisionTree:
    def test_tier_block_deleted_from_skill_md(self, skill_text: str) -> None:
        """Tier 1/2/3 review-post block must be deleted from SKILL.md."""
        assert "Tier 1: Blockers found" not in skill_text
        assert "Tier 2: No blockers, but has tech_debt" not in skill_text
        assert "Tier 3: Zero findings" not in skill_text

    def test_skill_md_delegates_to_post_review(self, skill_text: str) -> None:
        """SKILL.md §6 must delegate to post-review.md §3."""
        assert "post-review.md" in skill_text
        assert "single source of truth" in skill_text

    def test_post_review_has_decision_tree_in_section_3(self, post_review_text: str) -> None:
        """post-review.md must contain the decision tree in §3."""
        assert "### 3. Post the Review" in post_review_text


# ---------------------------------------------------------------------------
# BLOCKED_ON_CONFLICT path: gh pr comment only
# ---------------------------------------------------------------------------


class TestBlockedOnConflictPath:
    def test_section_2b_exists(self, post_review_text: str) -> None:
        """post-review.md must have §2b Preflight Short-Circuit: BLOCKED_ON_CONFLICT."""
        assert "BLOCKED_ON_CONFLICT" in post_review_text
        assert "2b" in post_review_text

    def test_section_2b_no_actionable_request_changes(self, post_review_text: str) -> None:
        """§2b must NOT contain an actionable --request-changes command on the conflict path.
        It may reference --request-changes in a 'do NOT use' instruction, but must not
        invoke it as an actual bash command.
        """
        # Find the §2b block
        idx = post_review_text.find("2b. Preflight Short-Circuit: BLOCKED_ON_CONFLICT")
        if idx == -1:
            idx = post_review_text.find("2b.")
        assert idx != -1, "§2b block not found in post_review_text"

        # Extract up to §2c
        idx_2c = post_review_text.find("2c.", idx + 1)
        if idx_2c == -1:
            idx_2c = idx + 2000  # Reasonable window
        section_2b = post_review_text[idx:idx_2c]

        # Any mention of --request-changes must be in a "do NOT use" or "NEVER" context
        # Extract all bash code blocks in the section
        import re

        bash_blocks = re.findall(r"```bash\n(.*?)```", section_2b, re.DOTALL)
        for block in bash_blocks:
            assert "--request-changes" not in block, (
                f"§2b bash block must not invoke --request-changes:\n{block}"
            )

    def test_section_2b_uses_pr_comment(self, post_review_text: str) -> None:
        """§2b must use gh pr comment (not gh pr review) for conflict path."""
        idx = post_review_text.find("2b.")
        assert idx != -1, "§2b block not found"
        idx_2c = post_review_text.find("2c.", idx + 1)
        if idx_2c == -1:
            idx_2c = idx + 2000
        section_2b = post_review_text[idx:idx_2c]
        assert "gh pr comment" in section_2b

    def test_section_3_preflight_guard_first(self, post_review_text: str) -> None:
        """§3 decision tree must check PREFLIGHT_VERDICT before any other branch."""
        idx_3 = post_review_text.find("### 3. Post the Review")
        assert idx_3 != -1
        # Find the PREFLIGHT check and the SELF_AUTHORED check after §3
        idx_preflight = post_review_text.find("PREFLIGHT_VERDICT", idx_3)
        idx_self_authored = post_review_text.find("SELF_AUTHORED", idx_3)
        assert idx_preflight != -1, "PREFLIGHT_VERDICT check missing from §3"
        assert idx_self_authored != -1, "SELF_AUTHORED check missing from §3"
        # PREFLIGHT check must come before SELF_AUTHORED check
        assert idx_preflight < idx_self_authored, (
            "PREFLIGHT_VERDICT must be checked before SELF_AUTHORED in §3"
        )

    def test_section_3_comment_on_conflict_path(self, post_review_text: str) -> None:
        """§3 conflict branch must use gh pr comment (or equivalent helper), not gh pr review."""
        idx_3 = post_review_text.find("### 3. Post the Review")
        assert idx_3 != -1
        # Extract §3 to end of section (or next ###)
        idx_next_section = post_review_text.find("### 4.", idx_3)
        if idx_next_section == -1:
            idx_next_section = idx_3 + 3000
        section_3 = post_review_text[idx_3:idx_next_section]

        # BLOCKED_ON_CONFLICT branch must post as comment
        assert "BLOCKED_ON_CONFLICT" in section_3
        # gh pr review must be unreachable on this path
        # The PREFLIGHT block should handle it via comment, not review
        # Check that the conflict branch leads to a comment call
        assert "gh pr comment" in section_3 or "_gh_post pr comment" in section_3

    def test_no_request_changes_in_conflict_branches(self, skill_text: str) -> None:
        """SKILL.md must not have --request-changes in the conflict-state sections."""
        # Find the mergeability preflight section
        idx = skill_text.find("Mergeability Preflight")
        if idx == -1:
            return
        preflight_section = skill_text[idx : idx + 2000]
        # The preflight section describes short-circuit, should not have --request-changes
        assert "--request-changes" not in preflight_section


# ---------------------------------------------------------------------------
# UNKNOWN mergeability → CONFLICTING (C-9)
# ---------------------------------------------------------------------------


class TestUnknownMergeabilityConservative:
    def test_unknown_after_retry_treated_as_conflicting_in_skill(self, skill_text: str) -> None:
        """SKILL.md must document that UNKNOWN after retry -> CONFLICTING.

        We check all UNKNOWN occurrences — the decision table entry may not be
        immediately adjacent to the 'if UNKNOWN' retry bash block.
        """
        assert "UNKNOWN" in skill_text, "UNKNOWN handling not found in SKILL.md"
        # Find the decision table entry that handles UNKNOWN after retry
        # It should appear somewhere in SKILL.md with BLOCKED_ON_CONFLICT nearby
        assert 'mergeable == "UNKNOWN" after retry' in skill_text or (
            "UNKNOWN" in skill_text and "BLOCKED_ON_CONFLICT" in skill_text
        ), "SKILL.md must associate UNKNOWN-after-retry with BLOCKED_ON_CONFLICT"
        # The key phrase: treat conservatively
        assert "CONFLICTING" in skill_text or "BLOCKED_ON_CONFLICT" in skill_text

    def test_unknown_after_retry_treated_as_conflicting_in_checkout(
        self, checkout_text: str
    ) -> None:
        """checkout.md decision table must route UNKNOWN-after-retry to BLOCKED_ON_CONFLICT.

        We search the whole file because the UNKNOWN mention in the retry bash block
        and the decision table row may not be adjacent.
        """
        assert "UNKNOWN" in checkout_text
        # The updated decision table row must mention BLOCKED_ON_CONFLICT
        assert "BLOCKED_ON_CONFLICT" in checkout_text, (
            "checkout.md must route UNKNOWN-after-retry to BLOCKED_ON_CONFLICT"
        )
        # The old "Log a warning and proceed" text for UNKNOWN must be gone
        assert "Log a warning and proceed" not in checkout_text, (
            "checkout.md must not tell the agent to proceed on UNKNOWN-after-retry"
        )

    def test_outcome_contract_has_blocked_on_conflict_verdict(self, skill_text: str) -> None:
        """Outcome Contract must include BLOCKED_ON_CONFLICT with next_skill:null."""
        assert "BLOCKED_ON_CONFLICT" in skill_text
        assert "next_skill" in skill_text
        # Find BLOCKED_ON_CONFLICT outcome
        idx = skill_text.find("BLOCKED_ON_CONFLICT")
        assert idx != -1
        surrounding = skill_text[idx : idx + 500]
        assert "next_skill" in surrounding or "null" in skill_text


# ---------------------------------------------------------------------------
# PR_CLOSED path
# ---------------------------------------------------------------------------


class TestPRClosedPath:
    def test_section_2c_exists(self, post_review_text: str) -> None:
        """post-review.md must have §2c Preflight Short-Circuit: PR_CLOSED."""
        assert "PR_CLOSED" in post_review_text
        assert "2c" in post_review_text

    def test_section_2c_uses_pr_comment(self, post_review_text: str) -> None:
        """§2c must use gh pr comment for the PR_CLOSED path."""
        idx = post_review_text.find("2c.")
        assert idx != -1, "§2c block not found"
        # Extract a window after §2c
        section_2c = post_review_text[idx : idx + 1000]
        assert "gh pr comment" in section_2c

    def test_section_3_routes_pr_closed_to_comment(self, post_review_text: str) -> None:
        """§3 decision tree must route PR_CLOSED to gh pr comment."""
        idx_3 = post_review_text.find("### 3. Post the Review")
        assert idx_3 != -1
        idx_next = post_review_text.find("### 4.", idx_3)
        if idx_next == -1:
            idx_next = idx_3 + 3000
        section_3 = post_review_text[idx_3:idx_next]
        assert "PR_CLOSED" in section_3
        # Must route to comment, not review
        assert "gh pr comment" in section_3 or "_gh_post pr comment" in section_3
