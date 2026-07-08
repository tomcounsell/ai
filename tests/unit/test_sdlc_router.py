"""Unit tests for agent.sdlc_router — G7 plan-revising lock guard.

Tests the guard_g7_plan_revising function in isolation and through
decide_next_dispatch().

The existing router decision tests live in test_sdlc_router_decision.py.
This file focuses exclusively on the G7 guard added for issue #1302.
"""

from __future__ import annotations

from agent.sdlc_router import (
    MAX_PLAN_REVISING_DISPATCHES,
    SKILL_DO_BUILD,
    SKILL_DO_DOCS,
    SKILL_DO_PLAN,
    SKILL_DO_PLAN_CRITIQUE,
    SKILL_DO_PR_REVIEW,
    SKILL_DO_TEST,
    STATUS_COMPLETED,
    STATUS_FAILED,
    Blocked,
    Dispatch,
    _rule_pr_exists_no_review,
    _rule_review_approved_docs_not_done,
    decide_next_dispatch,
    guard_g7_plan_revising,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_meta(**overrides) -> dict:
    """Return a minimal meta dict with G7-relevant defaults."""
    base = {
        "patch_cycle_count": 0,
        "critique_cycle_count": 0,
        "latest_critique_verdict": "READY TO BUILD",
        "latest_review_verdict": None,
        "revision_applied": False,
        "pr_number": None,
        "pr_merge_state": None,
        "ci_all_passing": None,
        "same_stage_dispatch_count": 0,
        "last_dispatched_skill": None,
        "plan_revising": False,
        "plan_hash_at_build_start": None,
    }
    base.update(overrides)
    return base


def _base_states(**overrides) -> dict:
    """Return minimal stage_states with CRITIQUE completed and no BUILD yet."""
    base = {
        "ISSUE": "completed",
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": "pending",
        "TEST": "pending",
        "REVIEW": "pending",
        "DOCS": "pending",
        "MERGE": "pending",
    }
    base.update(overrides)
    return base


def _dispatch_history(*skills) -> list[dict]:
    """Build a simple dispatch history list from skill names."""
    return [{"skill": s, "at": "2026-05-06T00:00:00Z", "stage_snapshot": {}} for s in skills]


# ---------------------------------------------------------------------------
# G7 guard — direct unit tests
# ---------------------------------------------------------------------------


class TestG7PlanRevisingGuardDirect:
    """Direct unit tests for guard_g7_plan_revising."""

    def test_no_lock_returns_none(self):
        """G7 falls through when plan_revising is False."""
        states = _base_states()
        meta = _base_meta(plan_revising=False)
        result = guard_g7_plan_revising(states, meta, {})
        assert result is None

    def test_pr_open_returns_none(self):
        """G7 falls through when pr_number is set (PR-stage routing takes over)."""
        states = _base_states()
        meta = _base_meta(plan_revising=True, pr_number=42)
        result = guard_g7_plan_revising(states, meta, {})
        assert result is None

    def test_self_heal_revision_applied_returns_none(self):
        """G7 self-heals when plan_revising=True but revision_applied=True."""
        states = _base_states()
        meta = _base_meta(plan_revising=True, revision_applied=True)
        result = guard_g7_plan_revising(states, meta, {})
        assert result is None

    def test_lock_plus_critique_just_ran_returns_do_plan(self):
        """G7 routes to /do-plan when lock is set and last dispatch was /do-plan-critique."""
        states = _base_states()
        meta = _base_meta(
            plan_revising=True,
            last_dispatched_skill=SKILL_DO_PLAN_CRITIQUE,
        )
        result = guard_g7_plan_revising(states, meta, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN
        assert result.row_id == "G7"

    def test_lock_no_recent_plan_dispatch_returns_blocked(self):
        """G7 escalates to Blocked when lock is set and no /do-plan in recent history."""
        states = _base_states(
            _sdlc_dispatches=_dispatch_history(
                SKILL_DO_BUILD,
                SKILL_DO_BUILD,
                SKILL_DO_PLAN_CRITIQUE,
            )
        )
        meta = _base_meta(
            plan_revising=True,
            last_dispatched_skill=SKILL_DO_BUILD,
        )
        result = guard_g7_plan_revising(states, meta, {})
        assert isinstance(result, Blocked)
        assert result.guard_id == "G7"
        assert "G7" in result.reason
        assert "plan_revising" in result.reason

    def test_lock_with_recent_plan_dispatch_returns_none(self):
        """G7 falls through when lock is set but /do-plan is in recent history."""
        # Build a history where /do-plan appears within the look-back window.
        history = _dispatch_history(*([SKILL_DO_PLAN] + [SKILL_DO_BUILD]))
        states = _base_states(_sdlc_dispatches=history)
        meta = _base_meta(
            plan_revising=True,
            last_dispatched_skill=SKILL_DO_BUILD,
        )
        result = guard_g7_plan_revising(states, meta, {})
        assert result is None

    def test_lock_empty_dispatch_history_returns_blocked(self):
        """G7 escalates when lock is set and dispatch history is empty (no /do-plan found)."""
        states = _base_states()  # no _sdlc_dispatches key
        meta = _base_meta(
            plan_revising=True,
            last_dispatched_skill=SKILL_DO_BUILD,
        )
        result = guard_g7_plan_revising(states, meta, {})
        # No /do-plan in history → Blocked
        assert isinstance(result, Blocked)
        assert result.guard_id == "G7"

    def test_max_plan_revising_dispatches_constant_is_positive(self):
        """MAX_PLAN_REVISING_DISPATCHES must be a positive integer."""
        assert isinstance(MAX_PLAN_REVISING_DISPATCHES, int)
        assert MAX_PLAN_REVISING_DISPATCHES > 0


# ---------------------------------------------------------------------------
# G7 through decide_next_dispatch()
# ---------------------------------------------------------------------------


class TestG7ThroughDecideNextDispatch:
    """Integration-style tests driving G7 through the full router."""

    def test_lock_set_critique_just_ran_routes_to_plan_via_g7(self):
        """G7 routes to /do-plan when lock is set and critique just ran (via guard, not table).

        Uses READY TO BUILD (with concerns) verdict so G1 doesn't fire first.
        G1 only fires on NEEDS REVISION / MAJOR REWORK; G7 fires on any
        plan_revising=True with last_dispatched_skill=critique.
        """
        states = _base_states()
        meta = _base_meta(
            plan_revising=True,
            # Use a verdict that does NOT trigger G1 (G1 needs NEEDS REVISION/MAJOR REWORK)
            latest_critique_verdict="READY TO BUILD (with concerns)",
            last_dispatched_skill=SKILL_DO_PLAN_CRITIQUE,
        )
        result = decide_next_dispatch(states, meta, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN
        assert result.row_id == "G7"

    def test_lock_set_no_recent_plan_dispatch_is_blocked(self):
        """Router blocks when lock is set and no /do-plan in recent dispatch history."""
        states = _base_states(
            _sdlc_dispatches=_dispatch_history(
                SKILL_DO_BUILD,
                SKILL_DO_BUILD,
                SKILL_DO_PLAN_CRITIQUE,
            )
        )
        meta = _base_meta(
            plan_revising=True,
            latest_critique_verdict="READY TO BUILD",
            last_dispatched_skill=SKILL_DO_BUILD,
        )
        result = decide_next_dispatch(states, meta, {})
        assert isinstance(result, Blocked)
        assert result.guard_id == "G7"

    def test_no_lock_routes_to_build(self):
        """Router routes to /do-build normally when G7 lock is clear."""
        states = _base_states()
        meta = _base_meta(
            plan_revising=False,
            latest_critique_verdict="READY TO BUILD",
            revision_applied=False,
        )
        result = decide_next_dispatch(states, meta, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_BUILD

    def test_lock_with_revision_applied_routes_to_build(self):
        """G7 self-heals when revision_applied=True; router routes to /do-build."""
        states = _base_states()
        meta = _base_meta(
            plan_revising=True,
            revision_applied=True,
            latest_critique_verdict="READY TO BUILD",
        )
        result = decide_next_dispatch(states, meta, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_BUILD

    def test_pr_open_ignores_lock(self):
        """G7 does not fire when pr_number is set; router continues to PR-stage."""
        states = _base_states(REVIEW="pending")
        meta = _base_meta(
            plan_revising=True,
            pr_number=99,
            latest_critique_verdict="READY TO BUILD",
        )
        result = decide_next_dispatch(states, meta, {})
        # With pr_number set and REVIEW pending, router should route to /do-pr-review
        assert isinstance(result, Dispatch)
        assert result.skill != SKILL_DO_PLAN  # G7 did not fire


# ---------------------------------------------------------------------------
# Cross-repo UNKNOWN merge-state — distinguishable Blocked reason
# ---------------------------------------------------------------------------


def _no_rule_matches_states() -> dict:
    """Return stage_states where pr_number is relevant but no dispatch rule fires.

    Scenario: REVIEW is completed with APPROVED verdict, DOCS is completed,
    but BUILD and TEST are still pending. Rule 10 (_rule_ready_to_merge)
    requires BUILD+TEST completed, so it won't fire. Rule 9
    (_rule_review_approved_docs_not_done) requires DOCS NOT completed, so it
    won't fire either. No other rule matches with pr_number set and
    REVIEW="completed". This forces primary=None in decide_next_dispatch,
    so the UNKNOWN merge-state Blocked branch is reachable.
    """
    return {
        "ISSUE": "completed",
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": "pending",
        "TEST": "pending",
        "REVIEW": "completed",
        "DOCS": "completed",
        "MERGE": "pending",
    }


class TestUnknownMergeStateBlocked:
    """PR with UNKNOWN/None merge state emits a distinguishable Blocked reason.

    These tests exercise the branch at the end of decide_next_dispatch that
    fires when primary=None AND pr_number is set AND pr_merge_state is
    None/"UNKNOWN". The state fixture _no_rule_matches_states() creates a
    configuration where every dispatch rule fails to match, so the fallback
    Blocked path is reached.
    """

    def test_blocked_unknown_is_distinguishable(self):
        """PR with UNKNOWN merge state emits distinguishable reason with PR#, UNKNOWN, and repo."""
        states = _no_rule_matches_states()
        meta = _base_meta(
            pr_number=42,
            pr_merge_state="UNKNOWN",
            _resolved_target_repo="tomcounsell/popoto",
            latest_critique_verdict="READY TO BUILD",
            latest_review_verdict="APPROVED",
            ci_all_passing=True,
        )
        result = decide_next_dispatch(states, meta, {})
        assert isinstance(result, Blocked)
        assert "42" in result.reason
        assert "UNKNOWN" in result.reason
        assert "tomcounsell/popoto" in result.reason

    def test_blocked_none_merge_state_is_distinguishable(self):
        """PR with None merge state (gh lookup failed) emits distinguishable reason."""
        states = _no_rule_matches_states()
        meta = _base_meta(
            pr_number=77,
            pr_merge_state=None,
            _resolved_target_repo="tomcounsell/popoto",
            latest_critique_verdict="READY TO BUILD",
            latest_review_verdict="APPROVED",
            ci_all_passing=None,
        )
        result = decide_next_dispatch(states, meta, {})
        assert isinstance(result, Blocked)
        assert "77" in result.reason
        assert "None" in result.reason

    def test_dirty_state_does_not_emit_unknown_message(self):
        """PR with DIRTY merge state does NOT emit UNKNOWN-specific message."""
        states = _no_rule_matches_states()
        meta = _base_meta(
            pr_number=42,
            pr_merge_state="DIRTY",
            _resolved_target_repo="tomcounsell/popoto",
            latest_critique_verdict="READY TO BUILD",
            latest_review_verdict="APPROVED",
            ci_all_passing=True,
        )
        result = decide_next_dispatch(states, meta, {})
        # DIRTY is a real state — should NOT trigger the UNKNOWN-specific Blocked message
        if isinstance(result, Blocked):
            assert "could not resolve mergeability" not in result.reason

    def test_no_pr_number_no_unknown_blocked(self):
        """Without a pr_number, the UNKNOWN branch never fires."""
        # Use base_states (no PR in context) to avoid any pr-specific rule firing
        states = _base_states()
        meta = _base_meta(
            pr_number=None,
            pr_merge_state=None,
            latest_critique_verdict="READY TO BUILD",
        )
        result = decide_next_dispatch(states, meta, {})
        # No PR number → UNKNOWN branch must not fire
        if isinstance(result, Blocked):
            assert "could not resolve mergeability" not in result.reason

    def test_blocked_unknown_no_resolved_repo_shows_placeholder(self):
        """When _resolved_target_repo is absent, placeholder appears in reason."""
        states = _no_rule_matches_states()
        meta = _base_meta(
            pr_number=55,
            pr_merge_state="UNKNOWN",
            latest_critique_verdict="READY TO BUILD",
            latest_review_verdict="APPROVED",
            ci_all_passing=True,
            # _resolved_target_repo deliberately absent
        )
        result = decide_next_dispatch(states, meta, {})
        assert isinstance(result, Blocked)
        assert "55" in result.reason
        assert "UNKNOWN" in result.reason
        # Placeholder should appear since no repo was resolved
        assert "none" in result.reason.lower() or "cwd" in result.reason.lower()


# ---------------------------------------------------------------------------
# G5 artifact-hash cache — defers once build has produced a PR (#1710)
# ---------------------------------------------------------------------------


class TestG5DefersAfterBuild:
    """G5 must not re-dispatch /do-build once BUILD is done or a PR exists.

    Regression for the #1710 pipeline: a cached READY TO BUILD verdict on an
    unchanged plan hash caused G5 to fire /do-build forever after the PR was
    already open, never letting the downstream PR-stage rows (review/docs/merge)
    run.
    """

    def _g5_inputs(self, **state_overrides):
        from agent.sdlc_router import guard_g5_artifact_hash_cache

        states = _base_states(
            _verdicts={
                "CRITIQUE": {
                    "verdict": "READY TO BUILD (WITH CONCERNS)",
                    "artifact_hash": "sha256:abc",
                }
            },
            **state_overrides,
        )
        context = {"current_plan_hash": "sha256:abc"}
        return guard_g5_artifact_hash_cache, states, context

    def test_dispatches_build_before_pr(self):
        """With no PR and BUILD pending, G5 routes straight to /do-build."""
        g5, states, context = self._g5_inputs(BUILD="pending")
        meta = _base_meta(pr_number=None)
        result = g5(states, meta, context)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_BUILD

    def test_defers_when_pr_open(self):
        """With a PR open, G5 returns None so PR-stage rows take over."""
        g5, states, context = self._g5_inputs(BUILD="pending")
        meta = _base_meta(pr_number=1717)
        assert g5(states, meta, context) is None

    def test_defers_when_build_completed(self):
        """With BUILD completed, G5 returns None even before a PR is recorded."""
        g5, states, context = self._g5_inputs(BUILD="completed")
        meta = _base_meta(pr_number=None)
        assert g5(states, meta, context) is None


# ---------------------------------------------------------------------------
# Issue #1932 gap (a): crashed re-review after a patch used to either
# dead-end the router, or (for a spuriously-completed REVIEW marker) silently
# misroute to /do-docs instead of recovering. Row 8d
# (_rule_review_crashed_after_dispatch) now recovers both cases by
# re-dispatching /do-pr-review.
# ---------------------------------------------------------------------------


class TestReReviewCrashRecovery:
    """Row 8d recovery for #1932 gap (a): re-review crash after PATCH.

    Shared repro state: PATCH completed, PR open, last dispatch was
    /do-pr-review, no recorded REVIEW verdict, DOCS still pending. The only
    axis that varies is the REVIEW stage marker left behind by the crashed
    /do-pr-review run.
    """

    def _repro_states(self, review_status: str) -> dict:
        return _base_states(
            PATCH=STATUS_COMPLETED,
            REVIEW=review_status,
            DOCS="pending",
        )

    def _repro_meta(self) -> dict:
        return _base_meta(
            pr_number=1234,
            pr_merge_state="DIRTY",
            last_dispatched_skill=SKILL_DO_PR_REVIEW,
            latest_review_verdict=None,
        )

    def test_review_failed_recovers_via_row_8d(self):
        """REVIEW=failed, no verdict recorded: row 8d recovers by re-dispatching review."""
        states = self._repro_states(STATUS_FAILED)
        meta = self._repro_meta()
        result = decide_next_dispatch(states, meta, {})
        assert result == Dispatch(
            skill=SKILL_DO_PR_REVIEW,
            reason="Review dispatch crashed without recording a verdict — re-run review",
            row_id="8d",
        )

        # Companion assertion proving the repro state sits outside row 7's
        # coverage (PR exists, no review) for the FAILED case.
        assert _rule_pr_exists_no_review(states, meta, {}) is False

    def test_review_completed_recovers_via_row_8d(self):
        """REVIEW=completed, no verdict recorded: row 8d recovers by re-dispatching review.

        This is the WORSE half of gap (a): row 9 only checks
        stage_states["REVIEW"] == "completed" — it never checks that a verdict
        was actually recorded. A crashed /do-pr-review that happened to leave
        the marker at "completed" (e.g. a partial write) used to silently skip
        review entirely and proceed straight to docs. Row 8d now intercepts
        this state (it is ordered before row 9) and re-dispatches review.
        """
        states = self._repro_states(STATUS_COMPLETED)
        meta = self._repro_meta()
        result = decide_next_dispatch(states, meta, {})
        assert result == Dispatch(
            skill=SKILL_DO_PR_REVIEW,
            reason="Review dispatch crashed without recording a verdict — re-run review",
            row_id="8d",
        )

        # Companion assertions: row 7 still doesn't cover this state (REVIEW
        # is "completed", not None/pending/ready). Row 9's predicate is now
        # False in isolation (post-fix c: row 9 requires a recorded APPROVED
        # verdict, and none was recorded here) — 8d and row 9 are disjoint by
        # verdict, not by table-position luck.
        assert _rule_pr_exists_no_review(states, meta, {}) is False
        assert _rule_review_approved_docs_not_done(states, meta, {}) is False


# ---------------------------------------------------------------------------
# Issue #1932 gap (b): a NEEDS REVISION critique verdict must never route back
# to /do-plan when a PR is already open. Three independent routes could each
# produce that misroute: row 3, guard G1, guard G5. Each is fixed with an
# open-PR step-aside that defers to G3 (guard_g3_pr_lock), the canonical
# open-PR plan-stage redirect. These tests assert the FIXED (post-b1/b2/b3)
# behavior, plus no-PR regressions proving each route's normal contract is
# preserved when no PR exists.
# ---------------------------------------------------------------------------


class TestRow3OpenPrStepAside:
    """#1932 gap (b1): row 3 must step aside to row 7 when a PR is open.

    Row 3 (_rule_critique_needs_revision) now checks meta["pr_number"] first
    and returns False when a PR exists, letting row 7
    (_rule_pr_exists_no_review) own the PR-open path instead.
    """

    def test_row3_steps_aside_to_pr_review_when_pr_open(self):
        states = _base_states(
            CRITIQUE=STATUS_COMPLETED,
            REVIEW="pending",
        )
        meta = _base_meta(
            pr_number=4321,
            latest_critique_verdict="NEEDS REVISION",
            last_dispatched_skill=SKILL_DO_TEST,  # non-plan-family
        )
        # No proposed_skill in context — G3 requires last OR proposed to be
        # in the plan family to trip, and neither is here, so G3 doesn't
        # intercept; row 3 steps aside and row 7 picks it up.
        result = decide_next_dispatch(states, meta, {})
        assert result == Dispatch(
            skill=SKILL_DO_PR_REVIEW,
            reason="Code is ready for review",
            row_id="7",
        )
        assert result.skill != SKILL_DO_PLAN


class TestG1OpenPrStepAside:
    """#1932 gap (b2): G1 must defer to G3 when a PR is open.

    G1 (guard_g1_critique_loop) now checks meta["pr_number"] first and
    returns None when a PR exists, deferring to G3 (guard_g3_pr_lock), the
    canonical open-PR plan-stage redirect.
    """

    def test_g1_defers_to_g3_when_pr_open(self):
        states = _base_states(
            CRITIQUE=STATUS_COMPLETED,
            REVIEW="pending",
        )
        meta = _base_meta(
            pr_number=5555,
            latest_critique_verdict="NEEDS REVISION",
            last_dispatched_skill=SKILL_DO_PLAN_CRITIQUE,
        )
        result = decide_next_dispatch(states, meta, {})
        assert isinstance(result, Dispatch)
        assert result.row_id == "G3"
        assert result.skill != SKILL_DO_PLAN

    def test_g1_still_dispatches_do_plan_without_open_pr(self):
        """No-PR regression: G1's normal contract is preserved without a PR."""
        states = _base_states(
            CRITIQUE=STATUS_COMPLETED,
            REVIEW="pending",
        )
        meta = _base_meta(
            pr_number=None,
            latest_critique_verdict="NEEDS REVISION",
            last_dispatched_skill=SKILL_DO_PLAN_CRITIQUE,
        )
        result = decide_next_dispatch(states, meta, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN
        assert result.row_id == "G1"


class TestG5OpenPrStepAside:
    """#1932 gap (b3): G5 must defer to G3/row 7 when a PR is open.

    G5 (guard_g5_artifact_hash_cache) now checks meta["pr_number"] in the
    NEEDS_REVISION/MAJOR_REWORK branch (mirroring the existing pr_number
    defer in its READY_TO_BUILD branch) and returns None when a PR exists.
    """

    def test_g5_defers_to_pr_review_when_pr_open(self):
        plan_hash = "sha256:deadbeef"
        states = _base_states(
            CRITIQUE=STATUS_COMPLETED,
            REVIEW="pending",
            _verdicts={
                "CRITIQUE": {
                    "verdict": "NEEDS REVISION",
                    "artifact_hash": plan_hash,
                }
            },
        )
        meta = _base_meta(
            pr_number=6789,
            latest_critique_verdict="NEEDS REVISION",
            last_dispatched_skill=SKILL_DO_TEST,  # non-plan-family
        )
        context = {"current_plan_hash": plan_hash}  # cache hit
        result = decide_next_dispatch(states, meta, context)
        assert result == Dispatch(
            skill=SKILL_DO_PR_REVIEW,
            reason="Code is ready for review",
            row_id="7",
        )
        assert result.skill != SKILL_DO_PLAN

    def test_g5_major_rework_defers_to_pr_review_when_pr_open(self):
        """The gate covers MAJOR REWORK as well as NEEDS REVISION."""
        plan_hash = "sha256:deadbeef"
        states = _base_states(
            CRITIQUE=STATUS_COMPLETED,
            REVIEW="pending",
            _verdicts={
                "CRITIQUE": {
                    "verdict": "MAJOR REWORK",
                    "artifact_hash": plan_hash,
                }
            },
        )
        meta = _base_meta(
            pr_number=6790,
            latest_critique_verdict="MAJOR REWORK",
            last_dispatched_skill=SKILL_DO_TEST,  # non-plan-family
        )
        context = {"current_plan_hash": plan_hash}  # cache hit
        result = decide_next_dispatch(states, meta, context)
        assert result == Dispatch(
            skill=SKILL_DO_PR_REVIEW,
            reason="Code is ready for review",
            row_id="7",
        )
        assert result.skill != SKILL_DO_PLAN

    def test_g5_defers_to_g3_when_pr_open_and_last_dispatch_critique(self):
        """G1 defers via b2, then G3 trips before G5 is reached."""
        plan_hash = "sha256:deadbeef"
        states = _base_states(
            CRITIQUE=STATUS_COMPLETED,
            REVIEW="pending",
            _verdicts={
                "CRITIQUE": {
                    "verdict": "NEEDS REVISION",
                    "artifact_hash": plan_hash,
                }
            },
        )
        meta = _base_meta(
            pr_number=6791,
            latest_critique_verdict="NEEDS REVISION",
            last_dispatched_skill=SKILL_DO_PLAN_CRITIQUE,
        )
        context = {"current_plan_hash": plan_hash}  # cache hit
        result = decide_next_dispatch(states, meta, context)
        assert isinstance(result, Dispatch)
        assert result.row_id == "G3"
        assert result.skill != SKILL_DO_PLAN

    def test_g5_still_dispatches_do_plan_without_open_pr(self):
        """No-PR regression: G5's cache-reuse contract is preserved without a PR."""
        plan_hash = "sha256:deadbeef"
        states = _base_states(
            CRITIQUE=STATUS_COMPLETED,
            REVIEW="pending",
            _verdicts={
                "CRITIQUE": {
                    "verdict": "NEEDS REVISION",
                    "artifact_hash": plan_hash,
                }
            },
        )
        meta = _base_meta(
            pr_number=None,
            latest_critique_verdict="NEEDS REVISION",
            last_dispatched_skill=SKILL_DO_TEST,  # non-plan-family
        )
        context = {"current_plan_hash": plan_hash}  # cache hit
        result = decide_next_dispatch(states, meta, context)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN
        assert result.row_id == "G5"


# ---------------------------------------------------------------------------
# Issue #1932 gap (c): row 9 dispatches /do-docs purely off the REVIEW stage
# marker, without checking that a REVIEW verdict was actually recorded. This
# is the same underlying defect exercised via TestReReviewCrashRecovery's
# COMPLETED case, isolated here with a distinct (non-review) last dispatch to
# show row 8d could never have recovered this state either.
# ---------------------------------------------------------------------------


class TestRow9VerdictGate:
    """Row 9 now requires a recorded APPROVED review verdict (#1932 gap c fix)."""

    def test_row9_blocked_without_recorded_review_verdict(self):
        """No recorded verdict: row 9 must step aside, not silently dispatch /do-docs.

        Same repro state as TestReReviewCrashRecovery's COMPLETED case, but
        with a non-review last dispatch so row 8d cannot recover it either —
        this isolates row 9's own verdict gate. Post-fix, neither row fires
        and the router falls through to Blocked.
        """
        states = _base_states(
            REVIEW=STATUS_COMPLETED,
            DOCS="pending",
        )
        meta = _base_meta(
            pr_number=9101,
            last_dispatched_skill=SKILL_DO_BUILD,  # not a review-family skill
            latest_review_verdict=None,
        )
        result = decide_next_dispatch(states, meta, {})
        assert isinstance(result, Blocked)

    def test_row9_fires_with_recorded_approved_verdict(self):
        """Legitimate case: an APPROVED verdict IS recorded — row 9 still dispatches /do-docs."""
        states = _base_states(
            REVIEW=STATUS_COMPLETED,
            DOCS="pending",
        )
        meta = _base_meta(
            pr_number=9101,
            last_dispatched_skill=SKILL_DO_BUILD,
            latest_review_verdict="APPROVED",
        )
        result = decide_next_dispatch(states, meta, {})
        assert result == Dispatch(
            skill=SKILL_DO_DOCS,
            reason="Docs are required before merge",
            row_id="9",
        )

    def test_rule_review_approved_docs_not_done_false_without_verdict(self):
        """Direct predicate call: False when REVIEW completed but no verdict recorded (post-fix)."""
        states = _base_states(REVIEW=STATUS_COMPLETED, DOCS="pending")
        meta = _base_meta(pr_number=9101, latest_review_verdict=None)
        assert _rule_review_approved_docs_not_done(states, meta, {}) is False

    def test_rule_review_approved_docs_not_done_true_with_approved_verdict(self):
        """Direct predicate call: True when REVIEW completed and an APPROVED verdict is recorded."""
        states = _base_states(REVIEW=STATUS_COMPLETED, DOCS="pending")
        meta = _base_meta(pr_number=9101, latest_review_verdict="APPROVED")
        assert _rule_review_approved_docs_not_done(states, meta, {}) is True
