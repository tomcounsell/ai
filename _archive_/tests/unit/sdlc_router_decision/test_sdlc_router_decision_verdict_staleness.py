"""Review/critique verdict-staleness tests for agent.sdlc_router.decide_next_dispatch() (#2879)."""

from __future__ import annotations

from agent.sdlc_router import (
    SKILL_DO_PATCH,
    SKILL_DO_PLAN,
    SKILL_DO_PLAN_CRITIQUE,
    SKILL_DO_PR_REVIEW,
    Dispatch,
    decide_next_dispatch,
)

# ---------------------------------------------------------------------------
# #1641 — patch-supersedes-stale-verdict timestamp early-exit
# ---------------------------------------------------------------------------


class TestReviewVerdictStaleness:
    """Stale REVIEW verdict (older than latest /do-patch dispatch) must be superseded."""

    def _base_states(self, verdict_at: str, patch_at: str) -> dict:
        return {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "failed",
            "PATCH": "completed",
            "_verdicts": {
                "REVIEW": {
                    "verdict": "CHANGES REQUESTED",
                    "recorded_at": verdict_at,
                }
            },
            "_sdlc_dispatches": [
                {"skill": "/do-patch", "at": patch_at},
            ],
        }

    def test_stale_review_verdict_after_patch_dispatches_review(self):
        """verdict T0 < patch T1 → stale → /do-pr-review (row 8b)."""
        states = self._base_states(
            verdict_at="2026-01-01T10:00:00",
            patch_at="2026-01-01T11:00:00",
        )
        meta = {"pr_number": 99, "last_dispatched_skill": SKILL_DO_PATCH}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PR_REVIEW

    def test_fresh_review_verdict_after_patch_dispatches_patch(self):
        """verdict T2 > patch T1 → fresh → /do-patch (row 8)."""
        states = self._base_states(
            verdict_at="2026-01-01T12:00:00",
            patch_at="2026-01-01T11:00:00",
        )
        meta = {"pr_number": 99, "latest_review_verdict": "CHANGES REQUESTED"}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PATCH

    def test_review_verdict_stale_missing_recorded_at_not_suppressed(self):
        """Missing recorded_at → not stale → row 8 fires normally."""
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "failed",
            "PATCH": "completed",
            "_verdicts": {
                "REVIEW": {
                    "verdict": "CHANGES REQUESTED",
                    # no recorded_at
                }
            },
            "_sdlc_dispatches": [{"skill": "/do-patch", "at": "2026-01-01T11:00:00"}],
        }
        meta = {"pr_number": 99, "latest_review_verdict": "CHANGES REQUESTED"}
        result = decide_next_dispatch(states, meta)
        # Not stale (missing recorded_at) → row 8 fires → /do-patch
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PATCH

    def test_review_verdict_stale_no_prior_patch_not_suppressed(self):
        """No /do-patch in dispatch history → not stale → row 8 fires."""
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "failed",
            "_verdicts": {
                "REVIEW": {
                    "verdict": "CHANGES REQUESTED",
                    "recorded_at": "2026-01-01T10:00:00",
                }
            },
            "_sdlc_dispatches": [],  # no /do-patch entries
        }
        meta = {"pr_number": 99, "latest_review_verdict": "CHANGES REQUESTED"}
        result = decide_next_dispatch(states, meta)
        # No prior /do-patch → not stale → row 8 fires → /do-patch
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PATCH

    def test_review_verdict_stale_equal_timestamps_fresh(self):
        """Equal timestamps → not stale (strict <) → row 8 fires."""
        ts = "2026-01-01T10:00:00"
        states = self._base_states(verdict_at=ts, patch_at=ts)
        meta = {"pr_number": 99, "latest_review_verdict": "CHANGES REQUESTED"}
        result = decide_next_dispatch(states, meta)
        # Equal timestamps → not stale → row 8 → /do-patch
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PATCH

    def test_review_verdict_stale_non_iso_timestamp_not_suppressed(self):
        """Malformed recorded_at → parse failure → not stale → row 8 fires."""
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "failed",
            "_verdicts": {
                "REVIEW": {
                    "verdict": "CHANGES REQUESTED",
                    "recorded_at": "not-a-date",
                }
            },
            "_sdlc_dispatches": [{"skill": "/do-patch", "at": "2026-01-01T11:00:00"}],
        }
        meta = {"pr_number": 99, "latest_review_verdict": "CHANGES REQUESTED"}
        result = decide_next_dispatch(states, meta)
        # Parse failure → not stale → row 8 → /do-patch
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PATCH

    def test_fresh_review_verdict_after_patch_still_dispatches_patch(self):
        """patch T1 < verdict T2 → fresh verdict → row 8 wins → /do-patch."""
        states = self._base_states(
            verdict_at="2026-01-01T12:00:00",
            patch_at="2026-01-01T11:00:00",
        )
        # Override latest_review_verdict directly so meta takes precedence.
        meta = {"pr_number": 99, "latest_review_verdict": "CHANGES REQUESTED"}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PATCH


class TestCritiqueVerdictStaleness:
    """Stale CRITIQUE verdict (older than latest /do-plan dispatch) must re-critique (#1639).

    Mirrors TestReviewVerdictStaleness for the CRITIQUE path. A NEEDS REVISION
    verdict recorded before the plan was revised (a later /do-plan dispatch)
    routes to /do-plan-critique (row 2b) instead of dead-ending on /do-plan
    (row 3).
    """

    def _base_states(self, verdict_at: str, plan_at: str) -> dict:
        return {
            "PLAN": "completed",
            "CRITIQUE": "in_progress",
            "_verdicts": {
                "CRITIQUE": {
                    "verdict": "NEEDS REVISION",
                    "recorded_at": verdict_at,
                }
            },
            "_sdlc_dispatches": [
                {"skill": "/do-plan", "at": plan_at},
            ],
        }

    def test_stale_critique_verdict_after_plan_dispatches_recritique(self):
        """verdict T0 < plan T1 → stale → /do-plan-critique (row 2b)."""
        states = self._base_states(
            verdict_at="2026-01-01T10:00:00",
            plan_at="2026-01-01T11:00:00",
        )
        meta = {"last_dispatched_skill": SKILL_DO_PLAN}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN_CRITIQUE

    def test_fresh_critique_verdict_after_plan_dispatches_plan(self):
        """verdict T2 > plan T1 → fresh → /do-plan (row 3), no over-suppression."""
        states = self._base_states(
            verdict_at="2026-01-01T12:00:00",
            plan_at="2026-01-01T11:00:00",
        )
        meta = {"last_dispatched_skill": SKILL_DO_PLAN}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN

    def test_critique_verdict_stale_missing_recorded_at_not_suppressed(self):
        """Missing recorded_at → not stale → row 3 fires (/do-plan)."""
        states = {
            "PLAN": "completed",
            "CRITIQUE": "in_progress",
            "_verdicts": {
                "CRITIQUE": {
                    "verdict": "NEEDS REVISION",
                    # no recorded_at
                }
            },
            "_sdlc_dispatches": [{"skill": "/do-plan", "at": "2026-01-01T11:00:00"}],
        }
        meta = {"last_dispatched_skill": SKILL_DO_PLAN}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN

    def test_critique_verdict_stale_no_prior_plan_not_suppressed(self):
        """No /do-plan in dispatch history → not stale → row 3 fires."""
        states = {
            "PLAN": "completed",
            "CRITIQUE": "in_progress",
            "_verdicts": {
                "CRITIQUE": {
                    "verdict": "NEEDS REVISION",
                    "recorded_at": "2026-01-01T10:00:00",
                }
            },
            "_sdlc_dispatches": [],  # no /do-plan entries
        }
        meta = {"last_dispatched_skill": SKILL_DO_PLAN}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN

    def test_critique_verdict_equal_timestamps_fresh(self):
        """Equal timestamps → not stale (strict <) → row 3 fires (/do-plan)."""
        ts = "2026-01-01T10:00:00"
        states = self._base_states(verdict_at=ts, plan_at=ts)
        meta = {"last_dispatched_skill": SKILL_DO_PLAN}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN

    def test_critique_verdict_stale_non_iso_timestamp_not_suppressed(self):
        """Malformed recorded_at → parse failure → not stale → row 3 fires."""
        states = {
            "PLAN": "completed",
            "CRITIQUE": "in_progress",
            "_verdicts": {
                "CRITIQUE": {
                    "verdict": "NEEDS REVISION",
                    "recorded_at": "not-a-date",
                }
            },
            "_sdlc_dispatches": [{"skill": "/do-plan", "at": "2026-01-01T11:00:00"}],
        }
        meta = {"last_dispatched_skill": SKILL_DO_PLAN}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN

    def test_combined_sourcing_dead_end_routes_recritique(self):
        """#1639 dead-end: BOTH _verdicts.CRITIQUE and meta.latest_critique_verdict set.

        Staleness sources recorded_at from _verdicts only; row-3 text sources from
        meta. Populate both so the test cannot pass for the wrong reason (Concern 2).
        """
        states = {
            "PLAN": "completed",
            "CRITIQUE": "in_progress",
            "_verdicts": {
                "CRITIQUE": {
                    "verdict": "NEEDS REVISION",
                    "recorded_at": "2026-01-01T10:00:00",  # T0
                }
            },
            "_sdlc_dispatches": [
                {"skill": "/do-plan", "at": "2026-01-01T11:00:00"},  # T1 > T0
            ],
        }
        meta = {
            "latest_critique_verdict": "NEEDS REVISION",
            "last_dispatched_skill": SKILL_DO_PLAN,
            "revision_applied": True,
        }
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN_CRITIQUE
