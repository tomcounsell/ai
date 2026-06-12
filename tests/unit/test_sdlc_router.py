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
    SKILL_DO_PLAN,
    SKILL_DO_PLAN_CRITIQUE,
    Blocked,
    Dispatch,
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
