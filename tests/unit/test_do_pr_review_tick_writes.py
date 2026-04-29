"""Tests for the /do-pr-review tick/untick contract on Approved verdicts.

These are prompt-content tests + behavior simulations against the real helper
(`tools.plan_checkbox_writer`):

- The skill's `post-review.md` contains the new Step 2.5 ("Plan Checkbox Sync")
  with the four-value rubric mapping (pass/fail/acknowledged/n/a) and the
  non-negotiable commit-then-post-review ordering invariant.
- The four-value mapping produces the right writes against a real plan file:
  pass -> tick, fail -> untick, acknowledged -> untick, n/a -> no write.
- The disclosure-vs-pass override is documented in `code-review.md` Step 4.
- Push-failure handling is encoded in the prompt (abort review, emit
  next_skill: /do-patch).
- Pre-Verdict Checklist item 1 wording is "validated against diff", not
  "checked against diff" (Edit B-1).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tools import plan_checkbox_writer as pcw

POST_REVIEW = Path(".claude/skills/do-pr-review/sub-skills/post-review.md")
CODE_REVIEW = Path(".claude/skills/do-pr-review/sub-skills/code-review.md")


@pytest.fixture(scope="module")
def post_review_text() -> str:
    return POST_REVIEW.read_text()


@pytest.fixture(scope="module")
def code_review_text() -> str:
    return CODE_REVIEW.read_text()


# ---------------------------------------------------------------------------
# Prompt-content invariants
# ---------------------------------------------------------------------------


class TestPostReviewPromptInvariants:
    def test_step_2_5_exists(self, post_review_text: str) -> None:
        assert "### 2.5. Plan Checkbox Sync" in post_review_text

    def test_step_2_5_fires_only_on_approved(self, post_review_text: str) -> None:
        # The step must explicitly gate on APPROVED.
        assert "APPROVED" in post_review_text
        # And explicitly NOT fire on the other verdicts.
        for non_fire in ("CHANGES_REQUESTED", "BLOCKED_ON_CONFLICT", "PR_CLOSED"):
            assert non_fire in post_review_text

    def test_four_value_rubric_mapping(self, post_review_text: str) -> None:
        # The mapping table must encode all four rubric values and their
        # plan-file actions.
        for value in ("pass", "fail", "acknowledged", "n/a"):
            assert value in post_review_text
        # And reference the helper.
        assert "plan_checkbox_writer" in post_review_text

    def test_commit_before_post_review_ordering(self, post_review_text: str) -> None:
        # The non-negotiable invariant must appear verbatim, naming push.
        assert "git push origin" in post_review_text
        assert "BEFORE" in post_review_text or "before" in post_review_text
        # The push-failure path must abort and route to /do-patch.
        assert "/do-patch" in post_review_text

    def test_helper_failure_handling_documented(self, post_review_text: str) -> None:
        # All four failure tags must be enumerated so the caller can route.
        for tag in (
            "MATCH_AMBIGUOUS",
            "MATCH_NOT_FOUND",
            "MATCH_AMBIGUOUS_SECTION",
            "NO_CRITERIA_SECTION",
        ):
            assert tag in post_review_text


class TestCodeReviewPromptInvariants:
    def test_pre_verdict_checklist_uses_validated(self, code_review_text: str) -> None:
        # Edit B-1: "checked against diff" → "validated against diff".
        assert "All plan acceptance/success criteria validated against diff" in code_review_text
        assert "All plan acceptance criteria checked against diff" not in code_review_text

    def test_disclosure_vs_pass_override_documented(self, code_review_text: str) -> None:
        # Edit B-2: a satisfied-but-disclosed criterion classifies as pass.
        # The exact wording is flexible but the substance must be present.
        text = code_review_text.lower()
        assert "demonstrably satisfied" in text
        assert "informational" in text


# ---------------------------------------------------------------------------
# Behavioral simulation against the real helper
# ---------------------------------------------------------------------------


def _make_plan(tmp_path: Path, body: str) -> Path:
    plan = tmp_path / "plan.md"
    plan.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return plan


class TestRubricToPlanWrite:
    """Simulate the tick/untick contract end-to-end against a real plan file.

    The skill flow is: rubric returns per-criterion verdict -> if APPROVED,
    walk verdicts and invoke the helper. This test simulates the walk.
    """

    def _apply_rubric(self, plan: Path, verdicts: dict[str, str]) -> None:
        """Apply a rubric verdict map to ``plan`` using the helper.

        verdicts: {criterion_text: rubric_value}
        """
        for criterion, value in verdicts.items():
            if value == "pass":
                pcw.main(["tick", str(plan), "--criterion", criterion])
            elif value in ("fail", "acknowledged"):
                pcw.main(["untick", str(plan), "--criterion", criterion])
            # n/a -> no write (skip)

    def test_pass_ticks_unchecked(self, tmp_path: Path) -> None:
        plan = _make_plan(
            tmp_path,
            """
            ## Success Criteria
            - [ ] Tests pass
            - [ ] Lint clean
            """,
        )
        self._apply_rubric(plan, {"Tests pass": "pass", "Lint clean": "pass"})
        body = plan.read_text()
        assert "- [x] Tests pass" in body
        assert "- [x] Lint clean" in body

    def test_fail_unticks_dishonest_tick(self, tmp_path: Path) -> None:
        # The dishonest-tick unticking case: a plan starts with [x] for an
        # unsatisfied criterion, the rubric judges fail, the resulting plan
        # mutation has [ ].
        plan = _make_plan(
            tmp_path,
            """
            ## Success Criteria
            - [x] Premature tick — not actually satisfied
            """,
        )
        self._apply_rubric(plan, {"Premature tick — not actually satisfied": "fail"})
        body = plan.read_text()
        assert "- [ ] Premature tick — not actually satisfied" in body

    def test_acknowledged_unticks(self, tmp_path: Path) -> None:
        plan = _make_plan(
            tmp_path,
            """
            ## Success Criteria
            - [x] Was ticked but actually deferred
            """,
        )
        self._apply_rubric(plan, {"Was ticked but actually deferred": "acknowledged"})
        body = plan.read_text()
        assert "- [ ] Was ticked but actually deferred" in body

    def test_na_does_not_write(self, tmp_path: Path) -> None:
        plan = _make_plan(
            tmp_path,
            """
            ## Success Criteria
            - [x] Already done
            - [ ] Not yet
            """,
        )
        before = plan.read_text()
        self._apply_rubric(plan, {"Already done": "n/a", "Not yet": "n/a"})
        # No writes happened — file is byte-identical.
        assert plan.read_text() == before

    def test_disclosure_vs_pass_override_writes_tick(self, tmp_path: Path) -> None:
        # C6 case: a criterion has a verified disclosure but the diff now
        # satisfies it. The rubric MUST emit pass (per the documented override),
        # not acknowledged. The plan write reflects the pass.
        plan = _make_plan(
            tmp_path,
            """
            ## Success Criteria
            - [ ] Feature behind a flag (was deferred, now satisfied)
            """,
        )
        self._apply_rubric(plan, {"Feature behind a flag (was deferred, now satisfied)": "pass"})
        body = plan.read_text()
        assert "- [x] Feature behind a flag (was deferred, now satisfied)" in body

    def test_match_not_found_preserves_state(self, tmp_path: Path) -> None:
        # Simulate the MATCH_NOT_FOUND case — the rubric judged a criterion
        # but the helper can't find it. The skill must preserve the existing
        # checkbox state for the OTHER criteria and emit a manual-review
        # comment (we only test the preserve-state side here).
        plan = _make_plan(
            tmp_path,
            """
            ## Success Criteria
            - [ ] Real criterion
            """,
        )
        before = plan.read_text()
        rc = pcw.main(["tick", str(plan), "--criterion", "Phantom criterion"])
        assert rc == 2  # MATCH_NOT_FOUND
        assert plan.read_text() == before  # untouched
