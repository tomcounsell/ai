"""Plan-stage stand-down tests for agent.sdlc_router (#3237, #3227).

Two symptoms of one shape: a plan-stage decision path that keeps answering for
a lane whose real state has moved past the plan stage.

- #3237 (row 2b, pre-PR): with the with-concerns re-critique gate armed and
  BUILD already ``in_progress``, row 2b proposed ``/do-plan-critique`` at every
  decision point. Row 4c is gated on ``build_status in (None, pending, ready)``
  so it cannot answer, and row 5 ("Build must create the PR") sits below 2b.
  The lane had no exit from the plan loop.

- #3227 (G3, post-PR): ``guard_g3_pr_lock``'s ladder had no leg for
  "REVIEW completed and APPROVED, DOCS not completed", so an approved PR with
  docs pending fell to the ladder's default and re-dispatched ``/do-pr-review``
  forever. Row 9 would answer ``/do-docs`` but the guard runs first.

The two fixes are independent: G3 keys on ``last_dispatched_skill`` /
``proposed_skill``, not on whether row 2b fired, so the DOCS leg is reachable
regardless of row 2b's stand-downs.
"""

from __future__ import annotations

from agent.sdlc_router import (
    NO_RULE_GUARD_ID,
    SKILL_DO_BUILD,
    SKILL_DO_DOCS,
    SKILL_DO_MERGE,
    SKILL_DO_PLAN_CRITIQUE,
    SKILL_DO_PR_REVIEW,
    Blocked,
    decide_next_dispatch,
    guard_g3_pr_lock,
)

_PLAN_HASH = "sha256:cafe"
_HEAD = "a" * 40
_OTHER_HEAD = "b" * 40


# ---------------------------------------------------------------------------
# #3237 — row 2b stands down once BUILD has started
# ---------------------------------------------------------------------------


def _build_in_progress_states(build_status: str = "in_progress") -> dict:
    """Lane #3195's shape: with-concerns verdict recorded, BUILD already running."""
    return {
        "ISSUE": "completed",
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": build_status,
        "TEST": "pending",
        "REVIEW": "pending",
        "DOCS": "pending",
        "MERGE": "pending",
        "_verdicts": {
            "CRITIQUE": {
                "verdict": "READY TO BUILD (WITH CONCERNS)",
                "recorded_at": "2026-09-07T02:00:00",
                "artifact_hash": _PLAN_HASH,
            }
        },
    }


def _armed_concern_meta(**extra) -> dict:
    """Concern gate armed: a revision landed after the verdict, bound not spent."""
    meta = {
        "latest_critique_verdict": "READY TO BUILD (with concerns)",
        # Postdates the verdict -> `_concern_revision_is_unjudged` is True, which
        # is what makes `_critique_verdict_is_stale` report stale on this path.
        "revision_applied_at": "2026-09-07T03:00:00",
        "revision_applied": True,
        "concern_round_count": 1,
        "pr_number": None,
        "same_stage_dispatch_count": 0,
        "last_dispatched_skill": None,
    }
    meta.update(extra)
    return meta


class TestRow2bStandsDownOnceBuildStarted:
    """#3237: an in-progress BUILD must not be pulled back into the plan loop."""

    def test_build_in_progress_resumes_build_not_recritique(self):
        """The exact #3195 state: BUILD in_progress, gate armed, no PR -> row 5."""
        result = decide_next_dispatch(
            _build_in_progress_states(),
            _armed_concern_meta(),
            {"current_plan_hash": _PLAN_HASH},
        )
        assert result.skill == SKILL_DO_BUILD
        assert result.row_id == "5"

    def test_build_completed_escalates_rather_than_recritiquing(self):
        """A completed BUILD with no PR escalates — it does not re-enter the plan loop.

        ``Blocked(NO_RULE)`` is already the router's answer for this anomalous
        state on the no-concerns path: rows 4a/4b/4c all step aside on
        ``BUILD == completed``, and row 5 requires ``in_progress`` or a live
        branch. Standing 2b down makes the with-concerns path agree with that
        established answer instead of masking a build that never opened its PR
        behind an endless re-critique.
        """
        result = decide_next_dispatch(
            _build_in_progress_states(build_status="completed"),
            _armed_concern_meta(),
            {"current_plan_hash": _PLAN_HASH},
        )
        assert isinstance(result, Blocked)
        assert result.guard_id == NO_RULE_GUARD_ID

    def test_build_pending_still_recritiques(self):
        """Regression fence: below the bound with BUILD not started, 2b still owns it."""
        result = decide_next_dispatch(
            _build_in_progress_states(build_status="pending"),
            _armed_concern_meta(),
            {"current_plan_hash": _PLAN_HASH},
        )
        assert result.skill == SKILL_DO_PLAN_CRITIQUE
        assert result.row_id == "2b"

    def test_row_2b_stands_down_once_a_pr_exists(self):
        """A PR-stage lane has no plan-stage verdict to refresh (#3227 alt fix).

        Every sibling plan-stage row (1, 3, 4a, 4b, 4c) already steps aside on
        ``pr_number``; 2b was the outlier.
        """
        states = _build_in_progress_states(build_status="completed")
        states["REVIEW"] = "pending"
        result = decide_next_dispatch(
            states,
            _armed_concern_meta(pr_number=4242, last_dispatched_skill=SKILL_DO_BUILD),
            {},
        )
        assert result.skill != SKILL_DO_PLAN_CRITIQUE


# ---------------------------------------------------------------------------
# #3227 — G3's ladder needs a DOCS leg
# ---------------------------------------------------------------------------


def _approved_pr_states(docs: str = "pending", head_sha: str = _HEAD) -> dict:
    return {
        "ISSUE": "completed",
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": "completed",
        "TEST": "completed",
        "PATCH": "pending",
        "REVIEW": "completed",
        "DOCS": docs,
        "MERGE": "pending",
        "_verdicts": {
            "REVIEW": {
                "verdict": "APPROVED",
                "recorded_at": "2026-09-07T05:00:00",
                "head_sha": head_sha,
            }
        },
    }


def _approved_pr_meta(**extra) -> dict:
    meta = {
        "pr_number": 3219,
        "latest_review_verdict": "APPROVED",
        "latest_review_head_sha": _HEAD,
        "last_dispatched_skill": SKILL_DO_PLAN_CRITIQUE,
        "same_stage_dispatch_count": 0,
    }
    meta.update(extra)
    return meta


class TestG3DocsLeg:
    """#3227: REVIEW approved + DOCS pending must route to /do-docs, not re-review."""

    def test_guard_directly_returns_docs(self):
        result = guard_g3_pr_lock(_approved_pr_states(), _approved_pr_meta(), {})
        assert result is not None
        assert result.skill == SKILL_DO_DOCS
        assert result.row_id == "G3"

    def test_end_to_end_dispatch_returns_docs(self):
        """Lane #3181's shape through the public entry point."""
        result = decide_next_dispatch(_approved_pr_states(), _approved_pr_meta(), {})
        assert result.skill == SKILL_DO_DOCS

    def test_proposed_plan_skill_also_reaches_the_docs_leg(self):
        """The DOCS leg is reachable through the proposed_skill channel too.

        This is why the leg is NOT made redundant by row 2b's stand-downs: G3
        keys on what the caller proposes, independently of the dispatch table.
        """
        result = decide_next_dispatch(
            _approved_pr_states(),
            _approved_pr_meta(last_dispatched_skill=SKILL_DO_PR_REVIEW),
            {"proposed_skill": SKILL_DO_PLAN_CRITIQUE},
        )
        assert result.skill == SKILL_DO_DOCS

    def test_docs_completed_still_merges(self):
        """Regression fence: leg 1 (merge) keeps precedence when docs are done."""
        result = guard_g3_pr_lock(_approved_pr_states(docs="completed"), _approved_pr_meta(), {})
        assert result.skill == SKILL_DO_MERGE

    def test_changes_requested_still_patches(self):
        """Regression fence: a findings verdict is not diverted to docs."""
        states = _approved_pr_states()
        states["_verdicts"]["REVIEW"]["verdict"] = "CHANGES REQUESTED"
        result = guard_g3_pr_lock(
            states, _approved_pr_meta(latest_review_verdict="CHANGES REQUESTED"), {}
        )
        assert result.skill != SKILL_DO_DOCS

    def test_head_stale_approval_re_reviews_instead_of_docs(self):
        """An approval that predates the live head is not a licence to write docs.

        Agrees with row 8f / G6, which both refuse a head_sha-stale APPROVED
        verdict — the DOCS leg must not become the one path that trusts it.
        """
        result = guard_g3_pr_lock(
            _approved_pr_states(head_sha=_OTHER_HEAD),
            _approved_pr_meta(latest_review_head_sha=_OTHER_HEAD),
            {"pr_head_sha": _HEAD},
        )
        assert result.skill == SKILL_DO_PR_REVIEW

    def test_review_failed_status_still_patches(self):
        """REVIEW==failed keeps the patch leg even with an APPROVED-looking verdict."""
        states = _approved_pr_states()
        states["REVIEW"] = "failed"
        result = guard_g3_pr_lock(states, _approved_pr_meta(), {})
        assert result.skill != SKILL_DO_DOCS
