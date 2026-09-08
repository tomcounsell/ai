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
regardless of row 2b's stand-downs. Both of those channels are live routes to
the leg and both are pinned below — the ``proposed_skill`` one directly, the
``last_dispatched_skill`` one via row 2, whose missing ``pr_number`` step-aside
is tracked as #3249.
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


def _plan_context(branch_exists: bool) -> dict:
    """A context in the shape ``tools.sdlc_next_skill._build_context`` actually produces.

    ``_build_context`` sets ``branch_exists`` to ``True`` or ``False``
    *unconditionally* whenever ``issue_number`` is present, so the key is never
    absent on the real CLI path. Omitting it here would certify a context shape
    production cannot produce -- and row 5 (`_rule_branch_exists_no_pr`) reads it
    with ``is True``, so an omitted key silently changes the routing answer.
    """
    return {"current_plan_hash": _PLAN_HASH, "branch_exists": branch_exists}


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
            _plan_context(branch_exists=True),
        )
        assert result.skill == SKILL_DO_BUILD
        assert result.row_id == "5"

    def test_build_completed_with_live_branch_resumes_build(self):
        """The realistic crash-before-PR shape: BUILD completed, branch pushed -> row 5.

        This is the production case. A BUILD cannot reach ``completed`` without
        having pushed its lane branch, so a build that crashed after the push
        but before opening its PR carries ``branch_exists == True``. Row 5
        (`_rule_branch_exists_no_pr`) fires on the branch alone, independently of
        BUILD status, and answers ``/do-build`` ("Build must create the PR —
        resume build"). The lane auto-resumes; no human rescue is needed.

        Standing row 2b down is what lets the decision reach row 5 at all.
        """
        result = decide_next_dispatch(
            _build_in_progress_states(build_status="completed"),
            _armed_concern_meta(),
            _plan_context(branch_exists=True),
        )
        assert result.skill == SKILL_DO_BUILD
        assert result.row_id == "5"

    def test_build_completed_with_no_live_branch_escalates(self):
        """The no-branch subcase: nothing to resume, so the router escalates.

        With no live branch, row 5's predicate is False and rows 4a/4b/4c have
        all stepped aside on ``BUILD == completed``, so the decision falls
        through to ``Blocked(NO_RULE)`` — the same answer the no-concerns path
        already gives for this shape. Reachable when the lane's branch was never
        pushed or has since been deleted; it is the *minority* subcase, not the
        general answer for ``BUILD == completed`` with no PR.
        """
        result = decide_next_dispatch(
            _build_in_progress_states(build_status="completed"),
            _armed_concern_meta(),
            _plan_context(branch_exists=False),
        )
        assert isinstance(result, Blocked)
        assert result.guard_id == NO_RULE_GUARD_ID

    def test_build_pending_still_recritiques(self):
        """Regression fence: below the bound with BUILD not started, 2b still owns it."""
        result = decide_next_dispatch(
            _build_in_progress_states(build_status="pending"),
            _armed_concern_meta(),
            _plan_context(branch_exists=False),
        )
        assert result.skill == SKILL_DO_PLAN_CRITIQUE
        assert result.row_id == "2b"

    def test_row_2b_stands_down_once_a_pr_exists(self):
        """A PR-stage lane has no plan-stage verdict to refresh (#3227 alt fix).

        Every sibling plan-stage row (1, 3, 4a, 4b, 4c) already steps aside on
        ``pr_number``; 2b was the outlier. With REVIEW still pending the state
        lands on row 7, the PR-stage row that owns it.
        """
        states = _build_in_progress_states(build_status="completed")
        states["REVIEW"] = "pending"
        result = decide_next_dispatch(
            states,
            _armed_concern_meta(pr_number=4242, last_dispatched_skill=SKILL_DO_BUILD),
            _plan_context(branch_exists=True),
        )
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "7"


# ---------------------------------------------------------------------------
# #3227 — G3's ladder needs a DOCS leg
# ---------------------------------------------------------------------------


def _approved_pr_states(
    docs: str = "pending", head_sha: str = _HEAD, critique: str = "completed"
) -> dict:
    return {
        "ISSUE": "completed",
        "PLAN": "completed",
        "CRITIQUE": critique,
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

    def test_proposed_plan_skill_reaches_the_docs_leg(self):
        """Route 1 of 2 — the ``context["proposed_skill"]`` channel.

        ``guard_g3_pr_lock`` trips when *either* ``last_dispatched_skill`` or
        ``context["proposed_skill"]`` is plan-family. This pins the proposed
        channel, with ``last_dispatched_skill`` deliberately set to a non-plan
        skill so the other channel cannot be what carries the test.

        This is also why the leg is NOT made redundant by row 2b's stand-downs:
        G3 keys on what the caller proposes, independently of the dispatch table.
        """
        result = decide_next_dispatch(
            _approved_pr_states(),
            _approved_pr_meta(last_dispatched_skill=SKILL_DO_PR_REVIEW),
            {"proposed_skill": SKILL_DO_PLAN_CRITIQUE},
        )
        assert result.skill == SKILL_DO_DOCS

    def test_last_dispatched_plan_skill_reaches_the_docs_leg_via_row_2(self):
        """Route 2 of 2 — the ``last_dispatched_skill`` channel, with its provenance.

        A post-PR lane whose CRITIQUE marker still reads ``pending`` reaches the
        DOCS leg in two hops, with no caller proposing anything:

        1. Row 2 (``_rule_plan_not_critiqued``) has **no ``pr_number``
           step-aside**, so it dispatches ``/do-plan-critique`` even with the PR
           open and REVIEW approved. That dispatch is recorded as
           ``last_dispatched_skill``.
        2. The next decision enters G3 with a plan-family last dispatch, a PR
           open, REVIEW approved head-fresh, and DOCS pending — exactly leg 3's
           state.

        Row 2's missing step-aside is tracked separately as #3249 and is
        deliberately not fixed here; while it stands, this is the route that
        keeps the DOCS leg load-bearing rather than defence in depth.
        """
        states = _approved_pr_states(critique="pending")
        context = {"pr_head_sha": _HEAD}

        hop1 = decide_next_dispatch(
            states, _approved_pr_meta(last_dispatched_skill=SKILL_DO_PR_REVIEW), context
        )
        assert hop1.skill == SKILL_DO_PLAN_CRITIQUE
        assert hop1.row_id == "2"

        hop2 = decide_next_dispatch(
            states, _approved_pr_meta(last_dispatched_skill=hop1.skill), context
        )
        assert hop2.skill == SKILL_DO_DOCS
        assert hop2.row_id == "G3"

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
