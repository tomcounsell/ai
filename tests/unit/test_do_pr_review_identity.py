"""Tests for /do-pr-review bot-identity contract (#1300, opt-in per machine).

Validates that:
- When SDLC_AGENT_GH_TOKEN is set and non-empty, the review body contains
  the SDLC-AGENT-REVIEW marker and the skill doc specifies GH_TOKEN injection.
- When SDLC_AGENT_GH_TOKEN is unset or empty, no override is applied — the
  review posts under the operator credential (standard posture on non-bot
  machines).
- The marker grammar documented in SKILL.md includes the sha= attribute.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SKILL_MD = Path(".claude/skills-global/do-pr-review/SKILL.md")
POST_REVIEW_MD = Path(".claude/skills-global/do-pr-review/sub-skills/post-review.md")


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_MD.read_text()


@pytest.fixture(scope="module")
def post_review_text() -> str:
    return POST_REVIEW_MD.read_text()


# ---------------------------------------------------------------------------
# SKILL.md structural invariants
# ---------------------------------------------------------------------------


class TestSkillMdReviewIdentitySection:
    def test_review_identity_section_exists(self, skill_text: str) -> None:
        """SKILL.md must have a ## Review Identity section (plan requirement)."""
        assert "## Review Identity" in skill_text

    def test_marker_grammar_documented(self, skill_text: str) -> None:
        """Marker grammar must include 'SDLC-AGENT-REVIEW v1' and sha= attribute."""
        assert "SDLC-AGENT-REVIEW v1" in skill_text
        assert "sha=" in skill_text

    def test_sdlc_agent_gh_token_documented(self, skill_text: str) -> None:
        """SKILL.md must document SDLC_AGENT_GH_TOKEN env var."""
        assert "SDLC_AGENT_GH_TOKEN" in skill_text

    def test_claude_agent_review_documented(self, skill_text: str) -> None:
        """SKILL.md must document CLAUDE_AGENT_REVIEW env var."""
        assert "CLAUDE_AGENT_REVIEW" in skill_text

    def test_hard_rule_bot_identity(self, skill_text: str) -> None:
        """Hard Rules must document the opt-in bot-identity posture."""
        assert "bot identity" in skill_text.lower()
        assert "opt-in per machine" in skill_text.lower()

    def test_hard_rule_marker_presence(self, skill_text: str) -> None:
        """Hard Rules must require the marker when CLAUDE_AGENT_REVIEW=1."""
        assert "SDLC-AGENT-REVIEW v1" in skill_text
        # Marker rule is expressed somewhere in Hard Rules
        hard_rules_start = skill_text.find("## Hard Rules")
        assert hard_rules_start != -1
        hard_rules_section = skill_text[hard_rules_start : hard_rules_start + 2000]
        assert "SDLC-AGENT-REVIEW" in hard_rules_section

    def test_tier_decision_tree_deleted_from_skill_md(self, skill_text: str) -> None:
        """Tier 1/2/3 review-post block must be removed from SKILL.md (plan §6 deletion)."""
        assert "Tier 1: Blockers found" not in skill_text
        assert "Tier 2: No blockers" not in skill_text
        assert "Tier 3: Zero findings" not in skill_text

    def test_skill_md_points_to_post_review(self, skill_text: str) -> None:
        """After §6 deletion, SKILL.md must reference post-review.md as single source."""
        assert "post-review.md" in skill_text
        # Single source of truth language
        assert "single source of truth" in skill_text

    def test_unknown_mergeability_treated_as_conflicting(self, skill_text: str) -> None:
        """SKILL.md must document UNKNOWN-after-retry -> CONFLICTING treatment (C-9)."""
        assert "UNKNOWN" in skill_text
        # Conservative treatment documented
        assert "BLOCKED_ON_CONFLICT" in skill_text


# ---------------------------------------------------------------------------
# post-review.md identity setup (§0)
# ---------------------------------------------------------------------------


class TestPostReviewIdentitySetup:
    def test_section_0_exists(self, post_review_text: str) -> None:
        """post-review.md must have a §0 Identity Setup section."""
        assert "### 0. Identity Setup" in post_review_text

    def test_gh_token_for_review_variable(self, post_review_text: str) -> None:
        """§0 must define GH_TOKEN_FOR_REVIEW variable."""
        assert "GH_TOKEN_FOR_REVIEW" in post_review_text

    def test_token_optional_in_agent_context(self, post_review_text: str) -> None:
        """§0 must reference CLAUDE_AGENT_REVIEW and treat the token as opt-in.

        When the token is unset, §0 falls through to the operator credential
        rather than hard-failing — bot identity is opt-in per machine.
        """
        assert "CLAUDE_AGENT_REVIEW" in post_review_text
        assert "SDLC_AGENT_GH_TOKEN" in post_review_text
        # Must not hard-fail when token is missing
        assert "refusing to post under operator identity" not in post_review_text.lower()
        # Must document the fall-through to operator credential
        assert "operator credential" in post_review_text.lower()

    def test_empty_token_not_passed_to_gh(self, post_review_text: str) -> None:
        """C-5: empty GH_TOKEN must not be passed to gh (would corrupt credential store)."""
        # The guard should check non-empty before using the token
        assert (
            "-n " in post_review_text
            or "non-empty" in post_review_text
            or "[ -n" in post_review_text
        )

    def test_marker_preface_documented(self, post_review_text: str) -> None:
        """§0 must document the marker preface for agent-context reviews."""
        assert "SDLC-AGENT-REVIEW v1" in post_review_text

    def test_marker_includes_sha(self, post_review_text: str) -> None:
        """Marker must include sha= attribute (N-1: use PR head SHA)."""
        # The marker grammar with sha= must appear in the file
        marker_section_idx = post_review_text.find("SDLC-AGENT-REVIEW v1")
        assert marker_section_idx != -1
        # sha= should appear near the marker
        surrounding = post_review_text[marker_section_idx : marker_section_idx + 200]
        assert "sha=" in surrounding or "headRefOid" in post_review_text

    def test_gh_token_injection_in_section_3(self, post_review_text: str) -> None:
        """§3 must use GH_TOKEN_FOR_REVIEW for the gh subprocess."""
        # _gh_post helper or equivalent env-injection pattern
        assert "GH_TOKEN_FOR_REVIEW" in post_review_text
        # Injection pattern: env GH_TOKEN or wrapper function
        assert ("env GH_TOKEN" in post_review_text) or ("_gh_post" in post_review_text)


# ---------------------------------------------------------------------------
# Local developer (no agent context) invariants
# ---------------------------------------------------------------------------


class TestLocalDeveloperUnchanged:
    def test_no_override_when_agent_review_unset(self, post_review_text: str) -> None:
        """When CLAUDE_AGENT_REVIEW is unset/0, GH_TOKEN_FOR_REVIEW must be empty."""
        # The §0 block initializes GH_TOKEN_FOR_REVIEW="" unconditionally
        assert 'GH_TOKEN_FOR_REVIEW=""' in post_review_text

    def test_empty_string_treated_same_as_unset(self, post_review_text: str) -> None:
        """C-5: empty SDLC_AGENT_GH_TOKEN must be treated identically to unset."""
        # The guard uses [ -z ... ] or similar to detect empty/unset
        assert "SDLC_AGENT_GH_TOKEN" in post_review_text
        # Empty-string detection: either -z or :- expansion
        assert ("-z" in post_review_text) or (":-}" in post_review_text)
