"""Tests for the /do-patch tick-on-fix contract.

These are prompt-content tests + behavior simulations against the real helper:

- The skill's `SKILL.md` contains the new Step 3.5 ("Sync Plan Checkbox") with
  the atomic single-commit invariant (`git add -A && git commit && git push`).
- The Step 2 builder prompt requires `criterion_addressed` reporting and
  embeds the closed cosmetic-only exclusion list.
- Helper-failure path is non-fatal — the commit STILL happens with the code
  change only, the failure is logged.
- The cosmetic-only path (typo fix → null → no plan write) is documented.

Behavior tests simulate the patch-skill's helper invocation against a real
plan file using `tools.plan_checkbox_writer`.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tools import plan_checkbox_writer as pcw

DO_PATCH_SKILL = Path(".claude/skills/do-patch/SKILL.md")


@pytest.fixture(scope="module")
def patch_skill_text() -> str:
    return DO_PATCH_SKILL.read_text()


# ---------------------------------------------------------------------------
# Prompt-content invariants
# ---------------------------------------------------------------------------


class TestPatchSkillPromptInvariants:
    def test_step_3_5_exists(self, patch_skill_text: str) -> None:
        assert "### Step 3.5: Sync Plan Checkbox" in patch_skill_text

    def test_step_3_5_runs_after_test_pass(self, patch_skill_text: str) -> None:
        # The step explicitly fires AFTER Step 3 (test-pass) and BEFORE Step 4
        # (Report Completion).
        idx_step_3 = patch_skill_text.index("### Step 3: Re-run Tests to Verify")
        idx_step_3_5 = patch_skill_text.index("### Step 3.5: Sync Plan Checkbox")
        idx_step_4 = patch_skill_text.index("### Step 4: Report Completion")
        assert idx_step_3 < idx_step_3_5 < idx_step_4

    def test_atomic_single_commit_invariant(self, patch_skill_text: str) -> None:
        # `git add -A` captures BOTH the code edits AND the plan-file edit.
        assert "git add -A" in patch_skill_text
        assert "atomic" in patch_skill_text.lower() or "same commit" in patch_skill_text.lower()
        # No --amend (the rule is "Do NOT use `git commit --amend`").
        assert "Do NOT use `git commit --amend`" in patch_skill_text

    def test_helper_failure_is_non_fatal(self, patch_skill_text: str) -> None:
        # The helper failure path logs but doesn't abort the patch.
        assert "non-fatal" in patch_skill_text.lower() or "STILL happens" in patch_skill_text

    def test_criterion_addressed_required_in_builder_prompt(self, patch_skill_text: str) -> None:
        # Builder must report the mapping in completion summary.
        assert "criterion_addressed" in patch_skill_text
        # And the closed cosmetic-only exclusion list must be embedded.
        for cosmetic in (
            "lint",
            "comment",
            "typo",
            "__pycache__",
            "test-file-only",
        ):
            assert cosmetic in patch_skill_text

    def test_uses_plan_checkbox_writer(self, patch_skill_text: str) -> None:
        assert "plan_checkbox_writer" in patch_skill_text


# ---------------------------------------------------------------------------
# Behavioral simulation of the patch flow
# ---------------------------------------------------------------------------


def _make_plan(tmp_path: Path, body: str) -> Path:
    plan = tmp_path / "plan.md"
    plan.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return plan


class TestPatchTickFlow:
    """Simulate Step 3.5's helper invocation logic.

    Step 3.5 logic:
      if CRITERION_ADDRESSED is non-null and non-empty:
          run plan_checkbox_writer tick (success appends suffix to commit msg)
      git add -A && git commit && git push
    """

    def _simulate_step_3_5(self, plan: Path, criterion_addressed: str | None) -> tuple[int, bool]:
        """Returns (helper_exit_code, plan_was_mutated)."""
        before = plan.read_text() if plan.exists() else ""
        if criterion_addressed and criterion_addressed.strip():
            rc = pcw.main(["tick", str(plan), "--criterion", criterion_addressed])
        else:
            rc = 0  # no helper invocation; "success" by default
        after = plan.read_text() if plan.exists() else ""
        return rc, before != after

    def test_criterion_addressed_ticks_in_same_pass(self, tmp_path: Path) -> None:
        plan = _make_plan(
            tmp_path,
            """
            ## Success Criteria
            - [ ] Race condition in session lock fixed
            """,
        )
        rc, mutated = self._simulate_step_3_5(plan, "Race condition in session lock fixed")
        assert rc == 0
        assert mutated is True
        assert "- [x] Race condition in session lock fixed" in plan.read_text()

    def test_null_criterion_no_plan_write(self, tmp_path: Path) -> None:
        # Builder reports criterion_addressed: null → no plan write.
        plan = _make_plan(
            tmp_path,
            """
            ## Success Criteria
            - [ ] Some criterion
            """,
        )
        rc, mutated = self._simulate_step_3_5(plan, None)
        assert rc == 0
        assert mutated is False  # plan untouched

    def test_cosmetic_only_typo_fix_no_plan_write(self, tmp_path: Path) -> None:
        # The builder prompt forces null for typo fixes; simulate that path.
        # The patch skill never invokes the helper if criterion_addressed is null.
        plan = _make_plan(
            tmp_path,
            """
            ## Success Criteria
            - [ ] Real behavior criterion
            """,
        )
        rc, mutated = self._simulate_step_3_5(plan, None)
        assert rc == 0
        assert mutated is False

    def test_helper_failure_non_fatal(self, tmp_path: Path) -> None:
        # Builder reports a criterion that doesn't match anything in the plan
        # (e.g., the criterion text drifted). Helper exits with MATCH_NOT_FOUND;
        # the patch flow does NOT abort — the commit still happens with code only.
        plan = _make_plan(
            tmp_path,
            """
            ## Success Criteria
            - [ ] Different criterion
            """,
        )
        rc, mutated = self._simulate_step_3_5(plan, "Phantom criterion")
        assert rc == 2  # MATCH_NOT_FOUND
        assert mutated is False  # plan untouched (helper exited without writing)
        # In real flow, the patch skill catches rc != 0, logs the failure,
        # and proceeds to commit the code change anyway. The simulation here
        # doesn't run git but verifies the helper's contract: a failed lookup
        # leaves the plan file untouched, which is what the non-fatal path
        # requires.

    def test_empty_criterion_treated_as_null(self, tmp_path: Path) -> None:
        # Whitespace-only criterion strings are equivalent to null.
        plan = _make_plan(
            tmp_path,
            """
            ## Success Criteria
            - [ ] Some criterion
            """,
        )
        rc, mutated = self._simulate_step_3_5(plan, "   ")
        assert rc == 0  # we short-circuit before invoking the helper
        assert mutated is False
