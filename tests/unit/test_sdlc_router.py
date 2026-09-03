"""Unit tests for agent.sdlc_router — G7 plan-revising lock guard.

Tests the guard_g7_plan_revising function in isolation and through
decide_next_dispatch().

The existing router decision tests live in the sdlc_router_decision/ package.
This file focuses exclusively on the G7 guard added for issue #1302.
"""

from __future__ import annotations

from agent.sdlc_router import (
    G3_REDIRECT_REASON_DOCS_PENDING,
    GUARDS,
    MAX_PLAN_REVISING_DISPATCHES,
    MAX_SAME_STAGE_DISPATCHES,
    SKILL_DO_BUILD,
    SKILL_DO_DOCS,
    SKILL_DO_MERGE,
    SKILL_DO_PATCH,
    SKILL_DO_PLAN,
    SKILL_DO_PLAN_CRITIQUE,
    SKILL_DO_PR_REVIEW,
    SKILL_DO_TEST,
    STATUS_COMPLETED,
    STATUS_FAILED,
    Blocked,
    Dispatch,
    Terminal,
    _rule_pr_exists_no_review,
    _rule_review_approved_docs_not_done,
    build_stage_snapshot,
    compute_same_stage_count,
    decide_next_dispatch,
    evaluate_guards,
    guard_g2_critique_cycle_cap,
    guard_g3_pr_lock,
    guard_g5_artifact_hash_cache,
    guard_g7_plan_revising,
    reconcile_dispatch,
    record_dispatch,
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

    def test_self_heal_requires_a_revision_since_the_verdict(self):
        """G7 gate 3 is event-scoped, not keyed on the sticky boolean (#2787).

        `revision_applied` is set by /do-plan on every revision pass and never
        resets, so keying the self-heal on it meant that after the very first
        revision the lock could never bind again — the mechanism #1302 built
        was permanently inert. With only the sticky boolean set and no verdict
        to scope against, the lock now survives and G7's own deadlock backstop
        owns the escalation — with the documented manual recovery named in the
        reason. That is the backstop working as designed, not a regression.
        """
        states = _base_states()
        meta = _base_meta(plan_revising=True, revision_applied=True)
        result = guard_g7_plan_revising(states, meta, {})
        assert isinstance(result, Blocked)
        assert result.guard_id == "G7"
        assert "plan_revising" in result.reason
        # The escalation is only defensible because it names the way out.
        assert "sdlc-tool meta-set" in result.reason

    def test_self_heal_fires_when_revision_landed_since_the_verdict(self):
        """A revision that postdates the CRITIQUE verdict does release the lock.

        Verdict-kind-agnostic on purpose: this is a NEEDS REVISION lock, and
        gate 3 must keep self-healing it or G7 escalates to Blocked on a lane
        that already did the work.
        """
        states = _base_states(
            _verdicts={
                "CRITIQUE": {
                    "verdict": "NEEDS REVISION",
                    "recorded_at": "2026-08-17T10:00:00",
                }
            }
        )
        meta = _base_meta(
            plan_revising=True,
            revision_applied=True,
            revision_applied_at="2026-08-17T11:00:00",
        )
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

    def test_lock_with_event_scoped_revision_routes_to_build(self):
        """G7 releases on an event-scoped revision; router then routes to /do-build.

        #2787: the release is keyed on a revision that landed AFTER the latest
        CRITIQUE verdict, not on the sticky `revision_applied` boolean.
        """
        states = _base_states(
            _verdicts={
                "CRITIQUE": {
                    "verdict": "READY TO BUILD",
                    "recorded_at": "2026-08-17T10:00:00",
                }
            }
        )
        meta = _base_meta(
            plan_revising=True,
            revision_applied=True,
            revision_applied_at="2026-08-17T11:00:00",
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

    def _g5_inputs(self, verdict="READY TO BUILD", **state_overrides):
        states = _base_states(
            _verdicts={
                "CRITIQUE": {
                    "verdict": verdict,
                    "artifact_hash": "sha256:abc",
                }
            },
            **state_overrides,
        )
        context = {"current_plan_hash": "sha256:abc"}
        return guard_g5_artifact_hash_cache, states, context

    def test_dispatches_build_before_pr(self):
        """With no PR and BUILD pending, G5 routes straight to /do-build.

        Fixture uses a NO-CONCERNS verdict: since #2787 the cached-verdict
        branch steps aside unconditionally on with-concerns, so a with-concerns
        fixture would no longer exercise the #1710 deferral this class covers.
        """
        g5, states, context = self._g5_inputs(BUILD="pending")
        meta = _base_meta(pr_number=None)
        result = g5(states, meta, context)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_BUILD

    def test_steps_aside_on_with_concerns_so_rows_own_the_state(self):
        """#2787: G5 never serves a with-concerns verdict.

        Guards run before the dispatch table, so without this step-aside rows
        2b/4b/4c are unreachable in production and the whole re-critique gate
        is dead while still passing its own unit tests.
        """
        g5, states, context = self._g5_inputs(
            verdict="READY TO BUILD (WITH CONCERNS)", BUILD="pending"
        )
        meta = _base_meta(pr_number=None)
        assert g5(states, meta, context) is None

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
        this isolates row 9's own verdict gate. Row 9 steps aside; since
        #2062 (WS3b) the no-verdict recovery row 8e owns this state and
        re-dispatches /do-pr-review (previously it fell through to Blocked).
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
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "8e"

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


# ---------------------------------------------------------------------------
# Issue #1932: G4 loop-bound coverage for row 8d. compute_same_stage_count's
# D5 self-clearing behavior (see agent/sdlc_router.py) means G4 only latches
# closed when the SAME stage_snapshot repeats across consecutive dispatches.
# These tests build REAL dispatch history via record_dispatch() + derive the
# streak via compute_same_stage_count() — never hand-set
# meta["same_stage_dispatch_count"] directly — so the assertions actually
# exercise the loop-detection machinery row 8d depends on.
# ---------------------------------------------------------------------------


def _row8d_crash_states(review_status: str = STATUS_COMPLETED, docs: str = "pending") -> dict:
    """A stage_states dict matching row 8d's crash predicate.

    PATCH completed, REVIEW terminal (completed or failed) with no recorded
    verdict, PR open — the #1932 gap (a) crash state.
    """
    return _base_states(
        PATCH=STATUS_COMPLETED,
        REVIEW=review_status,
        DOCS=docs,
    )


def _current_snapshot_for(states: dict, pr_number: int) -> dict:
    """Build the current-turn stage_snapshot the same way the router does."""
    view = {k: v for k, v in states.items() if k != "_sdlc_dispatches"}
    return build_stage_snapshot(view, meta={"pr_number": pr_number})


class TestRow8dLoopBound:
    """D5-aware G4 loop-bound test for row 8d (#1932).

    A genuinely stable crash — the review skill crashes the same way every
    turn, leaving an identical stage_snapshot — must eventually trip G4
    rather than looping forever on row 8d.
    """

    def test_stable_snapshot_reaches_cap_and_g4_blocks(self):
        """Identical stage_snapshot across MAX_SAME_STAGE_DISPATCHES turns: G4 blocks.

        Records MAX_SAME_STAGE_DISPATCHES - 1 history entries, then derives
        the count for the *impending* (about-to-dispatch) turn via
        ``current_snapshot`` — mirroring how the router computes the count
        before making its Nth dispatch. That impending turn is the one that
        should trip G4.
        """
        pr_number = 1234
        states = _row8d_crash_states()
        for _ in range(MAX_SAME_STAGE_DISPATCHES - 1):
            record_dispatch(states, SKILL_DO_PR_REVIEW, pr_number=pr_number)

        current_snapshot = _current_snapshot_for(states, pr_number)
        count, skill = compute_same_stage_count(states, current_snapshot=current_snapshot)

        # (i) the derived count reaches the cap — D5 does NOT reset it because
        # the snapshot never changed across any of the recorded dispatches.
        assert count == MAX_SAME_STAGE_DISPATCHES
        assert skill == SKILL_DO_PR_REVIEW

        meta = _base_meta(
            pr_number=pr_number,
            last_dispatched_skill=SKILL_DO_PR_REVIEW,
            latest_review_verdict=None,
            same_stage_dispatch_count=count,
        )
        result = decide_next_dispatch(states, meta, {})

        # (ii) the router blocks on G4 rather than re-dispatching row 8d.
        assert isinstance(result, Blocked)
        assert result.guard_id == "G4"
        assert not (isinstance(result, Dispatch) and result.row_id == "8d")

    def test_snapshot_move_resets_streak_and_g4_does_not_block(self):
        """Contrast case: the snapshot moves between two consecutive dispatches.

        D5 resets the streak on the divergent turn, so the derived count never
        reaches the cap and G4 stays silent — the router keeps routing to row
        8d instead of escalating.
        """
        pr_number = 1234
        states = _row8d_crash_states()
        for _ in range(MAX_SAME_STAGE_DISPATCHES - 1):
            record_dispatch(states, SKILL_DO_PR_REVIEW, pr_number=pr_number)
        # Move the snapshot (an unrelated stage_states field changes) before
        # the final recorded dispatch, breaking the identical-snapshot streak.
        states["DOCS"] = "in_progress"
        record_dispatch(states, SKILL_DO_PR_REVIEW, pr_number=pr_number)

        current_snapshot = _current_snapshot_for(states, pr_number)
        count, skill = compute_same_stage_count(states, current_snapshot=current_snapshot)

        assert count < MAX_SAME_STAGE_DISPATCHES
        assert skill == SKILL_DO_PR_REVIEW

        meta = _base_meta(
            pr_number=pr_number,
            last_dispatched_skill=SKILL_DO_PR_REVIEW,
            latest_review_verdict=None,
            same_stage_dispatch_count=count,
        )
        result = decide_next_dispatch(states, meta, {})

        assert not (isinstance(result, Blocked) and result.guard_id == "G4")
        assert result == Dispatch(
            skill=SKILL_DO_PR_REVIEW,
            reason="Review dispatch crashed without recording a verdict — re-run review",
            row_id="8d",
        )


class TestRow8dChurnLimitation:
    """KNOWN, DEFERRED limitation (#1932): G4 does not bound review-marker churn.

    D5 resets the same-stage streak whenever the stage_snapshot changes
    between consecutive dispatches. Row 8d's predicate accepts REVIEW in
    either STATUS_COMPLETED or STATUS_FAILED (both are "terminal, no verdict
    recorded" crash markers). If the crashed /do-pr-review skill happened to
    alternate which terminal marker it left behind on each retry, the
    snapshot would move on every turn, D5 would reset the streak every turn,
    and G4 would never reach its cap — the router would keep re-dispatching
    row 8d indefinitely for that specific churn pattern.

    This is NOT a bug this fix (#1932) is required to close: the expected
    real-world crash mode is a STABLE marker (the same partial-write leaves
    the same value every time — see TestRow8dLoopBound above, which IS
    bounded by G4). Bounding review-marker churn specifically was called out
    as out of scope in the plan's No-Gos. This test exists to document the
    limitation so a future change to G4/D5 does not silently reintroduce it
    without noticing the tradeoff.
    """

    def test_alternating_review_marker_never_reaches_g4_cap(self):
        pr_number = 5678
        states = _base_states(PATCH=STATUS_COMPLETED, DOCS="pending")
        review_values = [STATUS_COMPLETED, STATUS_FAILED, STATUS_COMPLETED]
        assert len(review_values) == MAX_SAME_STAGE_DISPATCHES
        for value in review_values:
            states["REVIEW"] = value
            record_dispatch(states, SKILL_DO_PR_REVIEW, pr_number=pr_number)

        current_snapshot = _current_snapshot_for(states, pr_number)
        count, skill = compute_same_stage_count(states, current_snapshot=current_snapshot)

        # The streak never reaches the cap — D5 resets it on every alternation.
        assert count < MAX_SAME_STAGE_DISPATCHES
        assert skill == SKILL_DO_PR_REVIEW

        meta = _base_meta(
            pr_number=pr_number,
            last_dispatched_skill=SKILL_DO_PR_REVIEW,
            latest_review_verdict=None,
            same_stage_dispatch_count=count,
        )
        result = decide_next_dispatch(states, meta, {})

        # G4 does NOT bound this churn case — the router keeps re-dispatching
        # row 8d rather than escalating. See class docstring for why this is
        # an accepted, documented tradeoff rather than a regression.
        assert result == Dispatch(
            skill=SKILL_DO_PR_REVIEW,
            reason="Review dispatch crashed without recording a verdict — re-run review",
            row_id="8d",
        )


# ---------------------------------------------------------------------------
# Issue #1871: G7 must be reordered ahead of G5 so a plan_revising lock is not
# bypassed by G5's cached READY-TO-BUILD fast path. G5 also gains a
# present-gap short-circuit for the state where G7's own Gate 6 falls
# through (a /do-plan dispatch already sits in recent history).
# ---------------------------------------------------------------------------


class TestG7BeforeG5Ordering:
    """(i) With the reorder, a plan_revising lock intercepts a cached
    READY-TO-BUILD verdict via G7 before G5 ever gets a chance to dispatch
    /do-build.
    """

    def test_plan_revising_with_cached_ready_to_build_routes_to_do_plan_not_build(self):
        plan_hash = "sha256:cafef00d"
        states = _base_states(
            CRITIQUE=STATUS_COMPLETED,
            BUILD="pending",
            _verdicts={
                "CRITIQUE": {
                    "verdict": "READY TO BUILD",
                    "artifact_hash": plan_hash,
                }
            },
        )
        meta = _base_meta(
            plan_revising=True,
            revision_applied=False,
            latest_critique_verdict="READY TO BUILD",
            last_dispatched_skill=SKILL_DO_PLAN_CRITIQUE,
            pr_number=None,
        )
        context = {"current_plan_hash": plan_hash}  # cache hit
        result = decide_next_dispatch(states, meta, context)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN
        assert result.row_id == "G7"
        assert result.skill != SKILL_DO_BUILD


class TestG7Gate6FallthroughRequiresG5ShortCircuit:
    """(ii) Load-bearing test: G7's Gate 6 returns None (a /do-plan dispatch
    is already in recent history), so without G5's own present-gap
    short-circuit the router would fall through to G5's cached
    READY-TO-BUILD branch and dispatch /do-build under an active revision
    lock. This test proves G5's short-circuit — not G7 — is what prevents
    that dispatch.

    Verdict text uses the "(WITH CONCERNS)" variant so that dispatch-table
    row 4a (``_rule_critique_ready_no_concerns``, which has the identical
    pre-existing gap of not reading ``plan_revising`` — out of scope for
    this fix, see #1871 plan) does not independently also dispatch
    /do-build and confound the isolation this test is proving. G5's cached
    READY-TO-BUILD branch matches on the "READY TO BUILD" substring
    regardless of the concerns suffix (see TestG5DefersAfterBuild above),
    so the short-circuit under test is still fully exercised.
    """

    def _repro(self):
        plan_hash = "sha256:g7gate6"
        history = _dispatch_history(SKILL_DO_PLAN, SKILL_DO_BUILD)
        states = _base_states(
            CRITIQUE=STATUS_COMPLETED,
            BUILD="pending",
            _sdlc_dispatches=history,
            _verdicts={
                "CRITIQUE": {
                    "verdict": "READY TO BUILD (WITH CONCERNS)",
                    "artifact_hash": plan_hash,
                }
            },
        )
        meta = _base_meta(
            plan_revising=True,
            revision_applied=False,
            latest_critique_verdict="READY TO BUILD (WITH CONCERNS)",
            last_dispatched_skill=SKILL_DO_BUILD,  # NOT /do-plan-critique — Gate 4 doesn't fire
            pr_number=None,
        )
        context = {"current_plan_hash": plan_hash}  # cache hit
        return states, meta, context

    def test_g7_gate6_returns_none_fallthrough(self):
        """Confirm the fallthrough precondition: G7 itself returns None here."""
        states, meta, context = self._repro()
        assert guard_g7_plan_revising(states, meta, context) is None

    def test_g5_short_circuit_prevents_do_build_dispatch(self):
        """G5's own plan_revising check — not G7 — blocks /do-build here."""
        states, meta, context = self._repro()
        # G5 in isolation must defer (its short-circuit fires).
        assert guard_g5_artifact_hash_cache(states, meta, context) is None
        # The full guard chain (G1-G9) must not dispatch /do-build either —
        # confirming no other guard picks up the slack G5 just gave up.
        guard_result = evaluate_guards(states, meta, context)
        assert not (isinstance(guard_result, Dispatch) and guard_result.skill == SKILL_DO_BUILD)
        # And the full router must not dispatch /do-build in this state.
        result = decide_next_dispatch(states, meta, context)
        assert not (isinstance(result, Dispatch) and result.skill == SKILL_DO_BUILD)


class TestG6NotCrossedByReorder:
    """(iii) The reorder must not cross G6: an already-mergeable PR still
    fast-paths to /do-merge via G6, even with plan_revising=True, because
    G7 defers at Gate 1 (pr_number is set) before G6 is ever reached.
    """

    def test_terminal_merge_ready_dispatches_merge_despite_plan_revising(self):
        states = _base_states(
            CRITIQUE=STATUS_COMPLETED,
            BUILD=STATUS_COMPLETED,
            TEST=STATUS_COMPLETED,
            REVIEW=STATUS_COMPLETED,
            DOCS=STATUS_COMPLETED,
            _verdicts={"REVIEW": {"verdict": "APPROVED"}},
        )
        meta = _base_meta(
            plan_revising=True,
            revision_applied=False,
            pr_number=8080,
            pr_merge_state="CLEAN",
            ci_all_passing=True,
            latest_review_verdict="APPROVED",
        )
        # G7 defers at Gate 1 because pr_number is set.
        assert guard_g7_plan_revising(states, meta, {}) is None
        result = decide_next_dispatch(states, meta, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_MERGE
        assert result.row_id == "G6"


class TestEmptyStateRegression:
    """(iv) Regression: the guard chain itself (not the full dispatch table,
    which legitimately routes an empty state to /do-plan via row 1) must
    still return None — no guard tries to dispatch or block — on an empty
    stage_states/meta after the reorder + G8 insertion + G5 short-circuit.
    """

    def test_empty_states_and_meta_guard_chain_returns_none(self):
        result = evaluate_guards({}, {}, {})
        assert result is None


class TestGuardsListOrder:
    """Pin the exact GUARDS list order established by #1871.

    G9 (#2796) is inserted immediately AFTER G4 so an already-oscillating lane
    keeps G4's existing precedence — no state that escalates today changes its
    guard_id — while a newly-conflicted lane escalates at G9 on turn 1, before
    it can burn a patch/review cycle.
    """

    def test_guards_pinned_order(self):
        names = [g.__name__ for g in GUARDS]
        assert names == [
            # T (#2894, #2817) runs first: a finished lane has no correct
            # dispatch, so no other guard's verdict is worth computing.
            "guard_terminal_lane",
            # G2 before G1 (#2885 via #3065): G1 dispatches /do-plan on every
            # revision-demanding verdict, so with G1 first the cycle cap can
            # never fire. G2 is None below the cap, so precedence is unchanged
            # until the bound is spent.
            "guard_g2_critique_cycle_cap",
            "guard_g1_critique_loop",
            "guard_g3_pr_lock",
            "guard_g4_oscillation",
            "guard_g9_blocked_on_conflict",
            "guard_g8_artifact_verification",
            "guard_g7_plan_revising",
            "guard_g5_artifact_hash_cache",
            "guard_g6_terminal_merge_ready",
        ]


class TestG2RevisionRoundCap:
    """#2885 via #3065: G2 must bound the NEEDS REVISION <-> revise loop.

    The skill-driven loop marks CRITIQUE ``completed`` every round while
    recording NEEDS REVISION, and ``critique_cycle_count`` stays 0 (its only
    incrementer is ``fail_stage``, which that flow never calls). G2 now also
    reads ``revision_round_count`` and trips on a still-revising verdict even
    with CRITIQUE completed.
    """

    def _states(self, critique="completed"):
        return {"PLAN": "completed", "CRITIQUE": critique}

    def test_trips_at_cap_despite_completed_marker(self):
        meta = _base_meta(
            revision_round_count=9,
            latest_critique_verdict="NEEDS REVISION",
            last_dispatched_skill=SKILL_DO_PLAN_CRITIQUE,
        )
        result = guard_g2_critique_cycle_cap(self._states(), meta, {})
        assert isinstance(result, Blocked)
        assert result.guard_id == "G2"

    def test_below_cap_stands_down(self):
        meta = _base_meta(
            revision_round_count=1,
            latest_critique_verdict="NEEDS REVISION",
        )
        assert guard_g2_critique_cycle_cap(self._states(), meta, {}) is None

    def test_accepted_verdict_stands_down_even_with_spent_count(self):
        """A lane whose loop terminated (verdict accepted) must not re-block."""
        meta = _base_meta(
            revision_round_count=9,
            latest_critique_verdict="READY TO BUILD",
        )
        assert guard_g2_critique_cycle_cap(self._states(), meta, {}) is None

    def test_g2_precedes_g1_end_to_end(self):
        """Through decide_next_dispatch: at the cap the router escalates
        instead of letting G1 dispatch /do-plan for a tenth round."""
        meta = _base_meta(
            revision_round_count=9,
            latest_critique_verdict="NEEDS REVISION",
            last_dispatched_skill=SKILL_DO_PLAN_CRITIQUE,
        )
        result = decide_next_dispatch(self._states(), meta, {})
        assert isinstance(result, Blocked)
        assert result.guard_id == "G2"

    def test_below_cap_g1_still_routes_to_plan(self):
        meta = _base_meta(
            revision_round_count=1,
            latest_critique_verdict="NEEDS REVISION",
            last_dispatched_skill=SKILL_DO_PLAN_CRITIQUE,
        )
        result = decide_next_dispatch(self._states(), meta, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN
        assert result.row_id == "G1"


class TestCrashedPlanStageRecovery:
    """#3078: PLAN="in_progress" after a dead plan subagent must route, not wedge.

    Replays the #2771 wedge: two consecutive /do-plan subagents died
    mid-flight, leaving the ledger asserting PLAN=in_progress with no plan
    doc on disk. The router returned Blocked('no matching dispatch rule') and
    the lane's only exits were outside the pipeline's contract.
    """

    def test_in_progress_no_doc_redispatches_plan(self):
        states = {"PLAN": "in_progress"}
        meta = _base_meta(issue_number=2771, plan_exists=False)
        result = decide_next_dispatch(states, meta, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN
        assert result.row_id == "1"

    def test_in_progress_doc_on_disk_advances_to_critique(self):
        """Died after writing the doc but before marking completed — the doc
        is the artifact critique reads, so the lane advances."""
        states = {"PLAN": "in_progress"}
        meta = _base_meta(issue_number=2771, plan_exists=True, latest_critique_verdict=None)
        result = decide_next_dispatch(states, meta, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN_CRITIQUE
        assert result.row_id == "2"

    def test_no_doc_and_no_issue_number_stays_blocked(self):
        """Without an issue_number the absence of a plan doc is unverifiable
        (#1640 evidence discipline) — refuse to guess."""
        states = {"PLAN": "in_progress"}
        meta = _base_meta(issue_number=None, plan_exists=False, latest_critique_verdict=None)
        result = decide_next_dispatch(states, meta, {})
        assert isinstance(result, Blocked)

    def test_open_pr_defers_to_pr_stage_rows(self):
        states = {"PLAN": "in_progress"}
        meta = _base_meta(issue_number=2771, plan_exists=False, pr_number=42)
        result = decide_next_dispatch(states, meta, {})
        assert not (isinstance(result, Dispatch) and result.row_id in ("1", "2"))


class TestCrashedPatchStageRecovery:
    """Row 8g: a dispatched /do-patch that died before its marker must route.

    Replays the #2754 wedge (2026-09-02): /do-patch wrote its dispatch record
    then crashed. The recorded CHANGES REQUESTED verdict became classified
    stale (staleness compares against the patch DISPATCH, not a landed
    patch), row 8 stepped aside, 8b needed PATCH=completed, 8c/8d/8e needed
    an absent verdict, 8f/9/10/G6 needed APPROVED — NO_RULE.
    """

    def _states(self):
        return {
            "ISSUE": "completed",
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "ready",
            "PATCH": "pending",
            "REVIEW": "pending",
            "_verdicts": {
                "REVIEW": {
                    "verdict": "CHANGES REQUESTED",
                    "recorded_at": "2026-09-02T07:43:32",
                }
            },
            "_sdlc_dispatches": [
                {
                    "skill": SKILL_DO_PATCH,
                    "at": "2026-09-02T09:36:50",
                    "stage_snapshot": {},
                }
            ],
        }

    def _meta(self, **overrides):
        base = dict(
            pr_number=3077,
            pr_merge_state="CLEAN",
            ci_all_passing=True,
            last_dispatched_skill=SKILL_DO_PATCH,
            latest_review_verdict="CHANGES REQUESTED",
            latest_critique_verdict="READY TO BUILD (NO CONCERNS)",
        )
        base.update(overrides)
        return _base_meta(**base)

    def test_crashed_patch_redispatches_patch(self):
        result = decide_next_dispatch(self._states(), self._meta(), {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PATCH
        assert result.row_id == "8g"

    def test_completed_patch_defers_to_8b(self):
        states = self._states()
        states["PATCH"] = "completed"
        result = decide_next_dispatch(states, self._meta(), {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "8b"

    def test_no_recorded_verdict_does_not_match_8g(self):
        """Absent verdict belongs to rows 8c/8d/8e — 8g must stand down."""
        states = self._states()
        del states["_verdicts"]
        meta = self._meta(latest_review_verdict=None)
        result = decide_next_dispatch(states, meta, {})
        assert not (isinstance(result, Dispatch) and result.row_id == "8g")

    def test_last_dispatch_not_patch_does_not_match_8g(self):
        meta = self._meta(last_dispatched_skill=SKILL_DO_PR_REVIEW)
        result = decide_next_dispatch(self._states(), meta, {})
        assert not (isinstance(result, Dispatch) and result.row_id == "8g")


# ---------------------------------------------------------------------------
# Issue #2062 (WS3a/b/d): row 10 verdict gate, row 8e no-verdict recovery,
# row 8f head_sha staleness. The #1897 misroute replay is pinned by name in
# the plan's Verification table: test_review_completed_no_verdict_routes_to_review.
# ---------------------------------------------------------------------------

_ALL_COMPLETED = {
    "ISSUE": "completed",
    "PLAN": "completed",
    "CRITIQUE": "completed",
    "BUILD": "completed",
    "TEST": "completed",
    "REVIEW": "completed",
    "DOCS": "completed",
    "MERGE": "pending",
}

_SHA_A = "a" * 40
_SHA_B = "b" * 40


def _approved_with_trailer(sha: str) -> str:
    return f"APPROVED\nREVIEW_CONTEXT head_sha={sha}"


class TestRow10VerdictGate:
    """WS3a (#2062): row 10 requires a recorded APPROVED verdict, mirroring row 9."""

    def test_review_completed_no_verdict_routes_to_review(self):
        """THE #1897 replay: REVIEW=completed, DOCS=completed, PATCH=pending,
        no recorded verdict, last=/do-build. Pre-fix this fell through 8c/8d/9
        to row 10 and misrouted to /do-merge. Post-fix it routes to
        /do-pr-review via the no-verdict recovery row."""
        states = dict(_ALL_COMPLETED, PATCH="pending")
        meta = _base_meta(
            pr_number=1897,
            last_dispatched_skill=SKILL_DO_BUILD,
            latest_review_verdict=None,
        )
        result = decide_next_dispatch(states, meta, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.skill != SKILL_DO_MERGE

    def test_row10_fires_with_recorded_approved_verdict(self):
        states = dict(_ALL_COMPLETED, PATCH="completed")
        meta = _base_meta(
            pr_number=1897,
            last_dispatched_skill=SKILL_DO_DOCS,
            latest_review_verdict="APPROVED",
        )
        result = decide_next_dispatch(states, meta, {})
        assert result == Dispatch(
            skill=SKILL_DO_MERGE,
            reason="Execute programmatic merge gate",
            row_id="10",
        )

    def test_rule_ready_to_merge_false_without_verdict(self):
        from agent.sdlc_router import _rule_ready_to_merge

        states = dict(_ALL_COMPLETED)
        meta = _base_meta(pr_number=1897, latest_review_verdict=None)
        assert _rule_ready_to_merge(states, meta, {}) is False

    def test_rule_ready_to_merge_false_with_changes_requested(self):
        from agent.sdlc_router import _rule_ready_to_merge

        states = dict(_ALL_COMPLETED)
        meta = _base_meta(pr_number=1897, latest_review_verdict="CHANGES REQUESTED")
        assert _rule_ready_to_merge(states, meta, {}) is False

    def test_rule_ready_to_merge_true_with_approved(self):
        from agent.sdlc_router import _rule_ready_to_merge

        states = dict(_ALL_COMPLETED)
        meta = _base_meta(pr_number=1897, latest_review_verdict="APPROVED")
        assert _rule_ready_to_merge(states, meta, {}) is True


class TestRow8eNoVerdictRecovery:
    """WS3b (#2062): the recovery row owning REVIEW==completed + no recorded
    verdict — the state 8c (in_progress) and 8d (PATCH completed + last ==
    /do-pr-review) both exclude."""

    def test_dispatches_pr_review_row_8e(self):
        states = _base_states(BUILD="completed", TEST="completed", REVIEW=STATUS_COMPLETED)
        meta = _base_meta(
            pr_number=2062,
            last_dispatched_skill=SKILL_DO_BUILD,
            latest_review_verdict=None,
        )
        result = decide_next_dispatch(states, meta, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "8e"

    def test_steps_aside_for_row_8d(self):
        """PATCH completed + last==/do-pr-review is row 8d's crash state; 8e
        must not shadow it."""
        states = _base_states(
            BUILD="completed",
            TEST="completed",
            PATCH=STATUS_COMPLETED,
            REVIEW=STATUS_COMPLETED,
        )
        meta = _base_meta(
            pr_number=2062,
            last_dispatched_skill=SKILL_DO_PR_REVIEW,
            latest_review_verdict=None,
        )
        result = decide_next_dispatch(states, meta, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "8d"

    def test_does_not_fire_with_recorded_verdict(self):
        from agent.sdlc_router import _rule_review_completed_no_verdict

        states = _base_states(REVIEW=STATUS_COMPLETED)
        meta = _base_meta(pr_number=2062, latest_review_verdict="APPROVED")
        assert _rule_review_completed_no_verdict(states, meta, {}) is False

    def test_does_not_fire_when_review_in_progress(self):
        from agent.sdlc_router import _rule_review_completed_no_verdict

        states = _base_states(REVIEW="in_progress")
        meta = _base_meta(pr_number=2062, latest_review_verdict=None)
        assert _rule_review_completed_no_verdict(states, meta, {}) is False


class TestHeadShaStaleness:
    """WS3d (#2062): the router consumes the pr_head_sha context signal so it
    agrees with tools/merge_predicate on verdict freshness. Fail-closed: a
    lookup failure or an absent/malformed trailer is STALE (re-review), never
    fresh."""

    def _helper(self, verdict, context):
        from agent.sdlc_router import _review_verdict_head_is_stale

        states = dict(_ALL_COMPLETED)
        meta = _base_meta(pr_number=2062, latest_review_verdict=verdict)
        return _review_verdict_head_is_stale(states, meta, context)

    def test_inert_when_context_signal_absent(self):
        assert self._helper(_approved_with_trailer(_SHA_A), {}) is False

    def test_fresh_when_trailer_matches_head(self):
        assert self._helper(_approved_with_trailer(_SHA_A), {"pr_head_sha": _SHA_A}) is False

    def test_trailer_match_is_case_insensitive(self):
        assert (
            self._helper(_approved_with_trailer(_SHA_A.upper()), {"pr_head_sha": _SHA_A}) is False
        )

    def test_stale_when_trailer_mismatches_head(self):
        assert self._helper(_approved_with_trailer(_SHA_A), {"pr_head_sha": _SHA_B}) is True

    def test_stale_when_lookup_failed(self):
        """Fail-closed: empty pr_head_sha (lookup failure) is stale."""
        context = {"pr_head_sha": "", "pr_head_sha_lookup_failed": True}
        assert self._helper(_approved_with_trailer(_SHA_A), context) is True

    def test_stale_when_trailer_absent(self):
        """A verdict with no head_sha trailer is treated as stale, never fresh."""
        assert self._helper("APPROVED", {"pr_head_sha": _SHA_A}) is True

    def test_inert_when_no_verdict_recorded(self):
        """No verdict: the no-verdict rows own the state, not the staleness helper."""
        assert self._helper(None, {"pr_head_sha": _SHA_A}) is False

    def test_stale_approved_routes_to_re_review_not_merge(self):
        """Post-approval commit: all stages completed, APPROVED verdict whose
        trailer names the OLD head — routes to /do-pr-review at the new head."""
        states = dict(_ALL_COMPLETED, PATCH="completed")
        meta = _base_meta(
            pr_number=2062,
            last_dispatched_skill=SKILL_DO_DOCS,
            latest_review_verdict=_approved_with_trailer(_SHA_A),
        )
        result = decide_next_dispatch(states, meta, {"pr_head_sha": _SHA_B})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PR_REVIEW

    def test_fresh_approved_still_merges(self):
        states = dict(_ALL_COMPLETED, PATCH="completed")
        meta = _base_meta(
            pr_number=2062,
            last_dispatched_skill=SKILL_DO_DOCS,
            latest_review_verdict=_approved_with_trailer(_SHA_A),
        )
        result = decide_next_dispatch(states, meta, {"pr_head_sha": _SHA_A})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_MERGE

    def test_g6_fast_path_suppressed_on_stale_head(self):
        """G6 (CLEAN + CI green + DOCS done + APPROVED) must not fast-path to
        /do-merge when the verdict's head_sha trailer mismatches the live head."""
        from agent.sdlc_router import guard_g6_terminal_merge_ready

        states = dict(_ALL_COMPLETED, PATCH="completed")
        meta = _base_meta(
            pr_number=2062,
            pr_merge_state="CLEAN",
            ci_all_passing=True,
            latest_review_verdict=_approved_with_trailer(_SHA_A),
        )
        assert guard_g6_terminal_merge_ready(states, meta, {"pr_head_sha": _SHA_B}) is None

    def test_g6_fast_path_fires_on_fresh_head(self):
        from agent.sdlc_router import guard_g6_terminal_merge_ready

        states = dict(_ALL_COMPLETED, PATCH="completed")
        meta = _base_meta(
            pr_number=2062,
            pr_merge_state="CLEAN",
            ci_all_passing=True,
            latest_review_verdict=_approved_with_trailer(_SHA_A),
        )
        result = guard_g6_terminal_merge_ready(states, meta, {"pr_head_sha": _SHA_A})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_MERGE


# ---------------------------------------------------------------------------
# #3065 task 3: G3's docs-pending redirect arm
#
# G3's ladder previously had exactly three arms and no ``/do-docs`` arm, so a
# lane with REVIEW complete, APPROVED, and DOCS pending fell to the ``else``
# and was sent back to ``/do-pr-review`` it had already passed.
# ---------------------------------------------------------------------------


class TestG3DocsArm:
    def _meta(self, **overrides):
        return _base_meta(
            pr_number=3065,
            last_dispatched_skill=SKILL_DO_PLAN,
            **overrides,
        )

    def test_docs_pending_after_approved_review_redirects_to_docs(self):
        """The state that previously fell to the ``else`` and produced a
        redundant /do-pr-review dispatch."""
        states = {"REVIEW": STATUS_COMPLETED, "DOCS": "pending"}
        meta = self._meta(latest_review_verdict="APPROVED")
        result = guard_g3_pr_lock(states, meta, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_DOCS
        assert G3_REDIRECT_REASON_DOCS_PENDING in result.reason

    def test_docs_complete_after_approved_review_redirects_to_merge(self):
        states = {"REVIEW": STATUS_COMPLETED, "DOCS": STATUS_COMPLETED}
        meta = self._meta(latest_review_verdict="APPROVED")
        result = guard_g3_pr_lock(states, meta, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_MERGE

    def test_review_completed_without_recorded_verdict_does_not_merge(self):
        """Arm 1 now requires a recorded APPROVED verdict, matching rows 9/10
        (#1932 gap c) -- REVIEW==completed with no verdict is an unearned
        marker, not evidence of approval."""
        states = {"REVIEW": STATUS_COMPLETED, "DOCS": STATUS_COMPLETED}
        meta = self._meta(latest_review_verdict=None)
        result = guard_g3_pr_lock(states, meta, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PR_REVIEW

    def test_review_completed_without_recorded_verdict_does_not_route_to_docs_either(self):
        """Same unearned-marker state, docs pending: still no dispatch to
        /do-docs without a recorded APPROVED verdict."""
        states = {"REVIEW": STATUS_COMPLETED, "DOCS": "pending"}
        meta = self._meta(latest_review_verdict=None)
        result = guard_g3_pr_lock(states, meta, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PR_REVIEW

    def test_changes_requested_still_routes_to_patch(self):
        """Regression guard: the changes-requested arm is untouched by the
        new docs arm."""
        states = {"REVIEW": STATUS_COMPLETED, "DOCS": "pending"}
        meta = self._meta(latest_review_verdict="CHANGES REQUESTED")
        result = guard_g3_pr_lock(states, meta, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PATCH

    def test_review_not_yet_complete_still_routes_to_pr_review(self):
        """Regression guard: the default arm is untouched."""
        states = {"REVIEW": "pending", "DOCS": "pending"}
        meta = self._meta(latest_review_verdict=None)
        result = guard_g3_pr_lock(states, meta, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PR_REVIEW

    def test_through_decide_next_dispatch_docs_pending_state(self):
        """End-to-end: the docs arm actually reaches the caller via
        decide_next_dispatch, not just the guard in isolation."""
        states = dict(_ALL_COMPLETED, DOCS="pending", MERGE="pending")
        meta = _base_meta(
            pr_number=3065,
            last_dispatched_skill=SKILL_DO_PLAN,
            latest_review_verdict="APPROVED",
        )
        result = decide_next_dispatch(states, meta, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_DOCS


# ---------------------------------------------------------------------------
# #3065 task 2: NO_RULE decision_inputs evidence
#
# A NO_RULE block previously carried no evidence of what the router actually
# read, so a field report claiming a NO_RULE on a state a rule demonstrably
# owns could not be checked. decision_inputs closes that gap.
# ---------------------------------------------------------------------------


def _unowned_state() -> tuple[dict, dict]:
    """A genuine hole in the dispatch table (mirrors
    sdlc_router_decision/test_sdlc_router_decision_post_patch.py's
    TestNoRuleBlockIsDistinguishable._unowned_state): REVIEW pending while an
    APPROVED verdict is already recorded -- row 8e needs REVIEW completed,
    row 10 needs it settled, row 7 needs no verdict, so nothing owns it.
    """
    states = {
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": "completed",
        "REVIEW": "pending",
        "DOCS": "completed",
        "MERGE": "pending",
        "_verdicts": {"REVIEW": {"verdict": "APPROVED", "recorded_at": "2026-05-06T00:00:00Z"}},
    }
    meta = _base_meta(
        pr_number=4242,
        last_dispatched_skill=SKILL_DO_MERGE,
        pr_merge_state="BLOCKED",
    )
    return states, meta


class TestNoRuleDecisionInputs:
    def test_no_rule_block_carries_decision_inputs(self):
        states, meta = _unowned_state()
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Blocked)
        assert result.guard_id == "NO_RULE"
        assert result.decision_inputs is not None
        # build_decision_inputs shallow-copies (a snapshot, not a live alias
        # of the caller's dicts) -- compare by value, not identity.
        assert result.decision_inputs["stage_states"] == states
        assert result.decision_inputs["meta"] == meta

    def test_decision_inputs_round_trips_through_cli_json(self, monkeypatch):
        """Drives the fallthrough through the actual sdlc-tool next-skill
        JSON surface (tools.sdlc_next_skill.decide), not just
        decide_next_dispatch directly, so the CLI wiring a supervisor
        actually reads is verified too."""
        import tools.sdlc_next_skill as sdlc_next_skill

        states, meta = _unowned_state()
        monkeypatch.setattr(
            sdlc_next_skill,
            "_resolve_enriched",
            lambda issue_number, session_id: {"stages": states, "_meta": meta},
        )
        monkeypatch.setattr(
            sdlc_next_skill,
            "_build_context",
            lambda proposed_skill, issue_number, stage_states=None, meta=None: {},
        )

        result = sdlc_next_skill.decide(issue_number=4242)

        assert result["decision"] == "blocked"
        assert result["guard_id"] == "NO_RULE"
        assert "decision_inputs" in result
        assert result["decision_inputs"]["meta"]["pr_number"] == 4242
        assert result["decision_inputs"]["stage_states"]["REVIEW"] == "pending"

    def test_unrecorded_dispatch_present_when_last_slot_unconfirmed(self):
        """The caller ran dispatch record but the stage never started --
        surfaced so this no longer has to be reverse-engineered from a later
        G4 oscillation block attributed to the wrong cause."""
        states, meta = _unowned_state()
        states["_sdlc_dispatches"] = [
            {"skill": SKILL_DO_MERGE, "stage": "MERGE", "confirmed": False}
        ]
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Blocked)
        signal = result.decision_inputs["unrecorded_dispatch"]
        assert signal is not None
        assert signal["confirmed"] is False
        assert signal["last_dispatched_skill"] == SKILL_DO_MERGE

    def test_unrecorded_dispatch_absent_when_last_slot_confirmed(self):
        states, meta = _unowned_state()
        states["_sdlc_dispatches"] = [
            {"skill": SKILL_DO_MERGE, "stage": "MERGE", "confirmed": True}
        ]
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Blocked)
        assert result.decision_inputs["unrecorded_dispatch"] is None

    def test_unrecorded_dispatch_present_when_caller_skipped_dispatch_record(self):
        """No _sdlc_dispatches entry at all for a meta that names a
        last_dispatched_skill -- the caller skipped `sdlc-tool dispatch
        record` entirely, not merely left a slot unconfirmed."""
        states, meta = _unowned_state()
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Blocked)
        signal = result.decision_inputs["unrecorded_dispatch"]
        assert signal is not None
        assert signal["recorded_skill"] is None

    def test_unrecorded_dispatch_absent_when_nothing_dispatched_yet(self):
        states, meta = _unowned_state()
        meta["last_dispatched_skill"] = None
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Blocked)
        assert result.decision_inputs["unrecorded_dispatch"] is None


# ---------------------------------------------------------------------------
# #3065 task 4: reconcile_dispatch — the keystone reconciliation step
#
# Guards previously validated only whatever the caller PROPOSED via
# context["proposed_skill"], never the skill the dispatch table actually
# SELECTED (:2246-2248 runs before the table at :2250-2255, and nothing
# re-validated in between). reconcile_dispatch re-runs the guard list
# against the table's selection, terminating after at most one redirect.
# ---------------------------------------------------------------------------


class TestReconcileDispatch:
    def test_accepts_selection_untouched_when_no_guard_objects(self):
        primary = Dispatch(skill=SKILL_DO_BUILD, reason="test", row_id="X")
        result = reconcile_dispatch({}, _base_meta(), {}, primary)
        assert result is primary

    def test_2771_2334_lane_shape_routes_to_docs_not_plan_critique(self):
        """The #2771/#2334 lane shape: open PR, REVIEW APPROVED, DOCS
        pending, and a CRITIQUE verdict stale enough that row 2b (which sits
        fifteen positions before row 9 in table order) would otherwise
        preempt DOCS unconditionally. Reconciliation lets G3 veto row 2b's
        redirect target (/do-plan-critique) on this shipped lane, without
        editing row 2b's predicate (#1639)."""
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": STATUS_COMPLETED,
            "DOCS": "pending",
            "MERGE": "pending",
            "_verdicts": {
                "CRITIQUE": {"verdict": "READY TO BUILD", "recorded_at": "2026-01-01T00:00:00Z"},
                "REVIEW": {"verdict": "APPROVED", "recorded_at": "2026-05-01T00:00:00Z"},
            },
            "_sdlc_dispatches": [
                {"skill": SKILL_DO_PLAN, "at": "2026-06-01T00:00:00Z", "stage_snapshot": {}},
            ],
        }
        meta = _base_meta(
            pr_number=2771,
            last_dispatched_skill=SKILL_DO_MERGE,
            latest_review_verdict="APPROVED",
        )

        result = decide_next_dispatch(states, meta, {})

        assert isinstance(result, Dispatch), f"expected Dispatch, got {result!r}"
        assert result.skill == SKILL_DO_DOCS
        assert result.skill != SKILL_DO_PLAN_CRITIQUE

    def test_plan_critique_needed_with_no_pr_still_dispatched(self):
        """A lane genuinely needing /do-plan-critique, with no PR open, must
        still get it -- reconciliation must not withhold a dispatch G3 has
        no jurisdiction over (G3 steps aside unconditionally when
        meta['pr_number'] is falsy)."""
        states = {"PLAN": "completed", "CRITIQUE": "pending"}
        meta = _base_meta(pr_number=None, last_dispatched_skill=None)

        result = decide_next_dispatch(states, meta, {})

        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN_CRITIQUE

    def test_double_veto_produces_blocked_with_both_verdicts_and_terminates(self):
        """A contrived guard pair that vetoes both the selection and its own
        redirect must terminate as a single Blocked carrying both verdicts,
        never a third pass."""
        primary = Dispatch(skill=SKILL_DO_BUILD, reason="table selected build", row_id="4a")
        call_count = {"n": 0}

        def _always_redirect(stage_states, meta, context):
            call_count["n"] += 1
            proposed = context.get("proposed_skill")
            # Redirect every proposal to a DIFFERENT skill, so a naive loop
            # would never converge -- proves this function bounds itself
            # rather than relying on the guard to stop redirecting.
            target = SKILL_DO_PATCH if proposed != SKILL_DO_PATCH else SKILL_DO_PR_REVIEW
            return Dispatch(skill=target, reason=f"vetoing {proposed}", row_id="TESTGUARD")

        original_guards = list(GUARDS)
        GUARDS.clear()
        GUARDS.append(_always_redirect)
        try:
            result = reconcile_dispatch({}, _base_meta(), {}, primary)
        finally:
            GUARDS[:] = original_guards

        assert call_count["n"] == 2, "reconciliation must run the guard list at most twice"
        assert isinstance(result, Blocked)
        assert result.guard_id == "RECONCILE_DEADLOCK"
        assert result.decision_inputs["selected_row"] == "4a"
        assert result.decision_inputs["selected_skill"] == SKILL_DO_BUILD
        assert result.decision_inputs["first_redirect"]["skill"] == SKILL_DO_PATCH
        assert result.decision_inputs["vetoing_guard"]["skill"] == SKILL_DO_PR_REVIEW

    def test_raising_guard_during_reconciliation_is_not_swallowed(self):
        """Preserves the existing asymmetry: rule predicates are
        try/except-wrapped, guards are not. A raising guard must propagate
        out of reconciliation rather than being folded into a NO_RULE."""

        def _raises(stage_states, meta, context):
            raise RuntimeError("boom")

        original_guards = list(GUARDS)
        GUARDS.clear()
        GUARDS.append(_raises)
        try:
            import pytest

            with pytest.raises(RuntimeError, match="boom"):
                reconcile_dispatch({}, _base_meta(), {}, Dispatch(SKILL_DO_BUILD, "x", "4a"))
        finally:
            GUARDS[:] = original_guards

    def test_terminal_veto_on_reconciliation_returns_terminal_unmodified(self):
        primary = Dispatch(skill=SKILL_DO_BUILD, reason="table selected build", row_id="4a")
        terminal = Terminal(reason="pipeline complete", evidence="merge_marker", row_id="T")

        def _terminal_guard(stage_states, meta, context):
            return terminal

        original_guards = list(GUARDS)
        GUARDS.clear()
        GUARDS.append(_terminal_guard)
        try:
            result = reconcile_dispatch({}, _base_meta(), {}, primary)
        finally:
            GUARDS[:] = original_guards

        assert result is terminal


class TestG5DoubleInvocationDuringReconciliation:
    """Regression test (#3065 task 4): guard_g5_artifact_hash_cache's
    legacy-hash migration branch must execute EXACTLY ONCE per
    decide_next_dispatch call, even though reconciliation runs the guard
    list a second time on the SAME stage_states/meta objects. Asserted via
    the WARNING log-record count, not the return value -- the return value
    is identical whether the migration ran once or twice, which is why this
    needs its own test."""

    def test_migration_warning_logged_exactly_once(self, caplog):
        legacy_hash = "sha256:legacy-full-bytes"
        current_hash = "sha256:body-only"
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": STATUS_COMPLETED if False else "in_progress",
            "_verdicts": {
                "CRITIQUE": {
                    # Neither NEEDS REVISION/MAJOR REWORK nor READY TO BUILD --
                    # G5 must migrate the hash and then still step aside (return
                    # None), so the table (not G5) selects the primary dispatch
                    # and reconciliation genuinely re-runs G5 a second time.
                    "verdict": "SOME OTHER VERDICT TEXT",
                    "artifact_hash": legacy_hash,
                }
            },
        }
        meta = _base_meta(pr_number=None, last_dispatched_skill=None)
        context = {"current_plan_hash": current_hash, "legacy_plan_hash": legacy_hash}

        with caplog.at_level("WARNING", logger="agent.sdlc_router"):
            result = decide_next_dispatch(states, meta, context)

        assert isinstance(result, Dispatch), f"expected Dispatch, got {result!r}"
        assert result.skill == SKILL_DO_BUILD
        assert result.row_id == "5"

        migration_warnings = [r for r in caplog.records if "G5 migration" in r.message]
        assert len(migration_warnings) == 1, (
            f"expected exactly one G5 migration WARNING, got {len(migration_warnings)}: "
            f"{[r.message for r in migration_warnings]}"
        )
        # The mutation itself is by-reference and idempotent: the second
        # (reconciliation) pass must see the already-migrated hash.
        assert states["_verdicts"]["CRITIQUE"]["artifact_hash"] == current_hash
