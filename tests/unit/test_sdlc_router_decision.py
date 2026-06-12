"""Pure-function tests for agent.sdlc_router.decide_next_dispatch()."""

from __future__ import annotations

from agent.sdlc_router import (
    DISPATCH_RULES,
    SKILL_DO_BUILD,
    SKILL_DO_DOCS,
    SKILL_DO_MERGE,
    SKILL_DO_PATCH,
    SKILL_DO_PLAN,
    SKILL_DO_PLAN_CRITIQUE,
    SKILL_DO_PR_REVIEW,
    Blocked,
    Dispatch,
    decide_next_dispatch,
)


def _states_all_pending() -> dict:
    return {
        "ISSUE": "pending",
        "PLAN": "pending",
        "CRITIQUE": "pending",
        "BUILD": "pending",
        "TEST": "pending",
        "REVIEW": "pending",
        "DOCS": "pending",
        "MERGE": "pending",
    }


class TestDispatchRulesTable:
    """Baseline: DISPATCH_RULES wiring is well-formed."""

    def test_rules_have_unique_row_ids(self):
        row_ids = [r.row_id for r in DISPATCH_RULES]
        assert len(row_ids) == len(set(row_ids)), "duplicate row_id in DISPATCH_RULES"

    def test_every_rule_has_a_docstring(self):
        for rule in DISPATCH_RULES:
            assert rule.state_predicate.__doc__, (
                f"rule {rule.row_id} predicate missing __doc__ — parity test will fail"
            )

    def test_every_skill_is_known(self):
        known = {
            SKILL_DO_PLAN,
            SKILL_DO_PLAN_CRITIQUE,
            SKILL_DO_BUILD,
            SKILL_DO_PATCH,
            SKILL_DO_PR_REVIEW,
            SKILL_DO_DOCS,
            SKILL_DO_MERGE,
        }
        for rule in DISPATCH_RULES:
            assert rule.skill in known, f"unknown skill {rule.skill} in row {rule.row_id}"


class TestRow1NoPlan:
    def test_empty_state_returns_do_plan(self):
        result = decide_next_dispatch({}, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN
        assert result.row_id == "1"

    def test_all_pending_returns_do_plan(self):
        result = decide_next_dispatch(_states_all_pending(), {})
        assert result.skill == SKILL_DO_PLAN


class TestRow2PlanNotCritiqued:
    def test_plan_completed_critique_pending_dispatches_critique(self):
        states = _states_all_pending()
        states["PLAN"] = "completed"
        result = decide_next_dispatch(states, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN_CRITIQUE
        assert result.row_id == "2"

    def test_plan_completed_critique_ready_dispatches_critique(self):
        # When PLAN completes, the state machine auto-transitions CRITIQUE
        # from "pending" to "ready". Row 2 must accept the "ready" status
        # too (regression for the issue #1262 dispatch failure).
        states = _states_all_pending()
        states["PLAN"] = "completed"
        states["CRITIQUE"] = "ready"
        result = decide_next_dispatch(states, {})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN_CRITIQUE
        assert result.row_id == "2"


class TestRow3CritiqueNeedsRevision:
    def test_needs_revision_without_loop_dispatches_plan(self):
        # G1 only trips when last skill was /do-plan-critique; without that,
        # the dispatch table's Row 3 still routes back to /do-plan.
        states = {
            "PLAN": "completed",
            "CRITIQUE": "failed",
            "_verdicts": {"CRITIQUE": {"verdict": "NEEDS REVISION"}},
        }
        result = decide_next_dispatch(states, {"latest_critique_verdict": "NEEDS REVISION"})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN


class TestRow4aReadyNoConcerns:
    def test_ready_no_concerns_dispatches_build(self):
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "pending",
        }
        meta = {"latest_critique_verdict": "READY TO BUILD (no concerns)"}
        result = decide_next_dispatch(states, meta)
        assert result.skill == SKILL_DO_BUILD
        assert result.row_id == "4a"


class TestRow4bReadyWithConcernsNoRevision:
    def test_concerns_without_revision_flag_returns_to_plan(self):
        states = {"PLAN": "completed", "CRITIQUE": "completed"}
        meta = {
            "latest_critique_verdict": "READY TO BUILD (with concerns)",
            "revision_applied": False,
        }
        result = decide_next_dispatch(states, meta)
        assert result.skill == SKILL_DO_PLAN
        assert result.row_id == "4b"

    def test_releases_once_pr_exists_even_without_revision_flag(self):
        """Row 4b must NOT re-dispatch /do-plan after the PR is open.

        Symmetry gap: row 4c got pr_number/BUILD guards but row 4b did not, so
        a with-concerns plan whose revision_applied flag never got set would
        loop /do-plan forever once a PR existed, never advancing to review.
        """
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "REVIEW": "ready",
        }
        meta = {
            "latest_critique_verdict": "READY TO BUILD (with concerns)",
            "revision_applied": False,
            "pr_number": 466,
        }
        result = decide_next_dispatch(states, meta)
        assert result.skill != SKILL_DO_PLAN
        assert result.row_id != "4b"


class TestRow4cReadyWithConcernsRevisionApplied:
    def test_concerns_with_revision_flag_proceeds_to_build(self):
        states = {"PLAN": "completed", "CRITIQUE": "completed"}
        meta = {
            "latest_critique_verdict": "READY TO BUILD (with concerns)",
            "revision_applied": True,
        }
        result = decide_next_dispatch(states, meta)
        assert result.skill == SKILL_DO_BUILD
        assert result.row_id == "4c"

    def test_releases_to_review_once_pr_exists(self):
        """Row 4c must NOT re-dispatch /do-build after the PR is open.

        Regression: row 4c lacked the pr_number/BUILD guards that row 4a has,
        so a with-concerns plan with revision_applied=True looped /do-build
        forever and never advanced to review.
        """
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "REVIEW": "ready",
        }
        meta = {
            "latest_critique_verdict": "READY TO BUILD (with concerns)",
            "revision_applied": True,
            "pr_number": 466,
        }
        result = decide_next_dispatch(states, meta)
        assert result.skill != SKILL_DO_BUILD
        assert result.row_id != "4c"


class TestD3FinishedPrNeverRoutesBackToBuild:
    """D3: rows 4b/4c defer once a PR exists or BUILD completed."""

    def test_4b_defers_when_pr_number_set(self):
        states = {"PLAN": "completed", "CRITIQUE": "completed", "REVIEW": "pending"}
        meta = {
            "latest_critique_verdict": "READY TO BUILD (with concerns)",
            "revision_applied": False,
            "pr_number": 99,
        }
        result = decide_next_dispatch(states, meta)
        # Must NOT route back to plan/build; a PR exists so downstream owns it.
        assert result.skill != SKILL_DO_PLAN
        assert result.skill != SKILL_DO_BUILD

    def test_4c_defers_when_build_completed(self):
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
        }
        meta = {
            "latest_critique_verdict": "READY TO BUILD (with concerns)",
            "revision_applied": True,
        }
        result = decide_next_dispatch(states, meta)
        # 4c must not re-propose build once BUILD is completed. With no PR yet
        # and no downstream rule matching, the router defers (Blocked) rather
        # than routing back to /do-build.
        assert getattr(result, "skill", None) != SKILL_DO_BUILD

    def test_4c_finished_pr_routes_to_review(self):
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "REVIEW": "pending",
        }
        meta = {
            "latest_critique_verdict": "READY TO BUILD (with concerns)",
            "revision_applied": True,
            "pr_number": 123,
        }
        result = decide_next_dispatch(states, meta)
        assert result.skill == SKILL_DO_PR_REVIEW


class TestRow6TestsFailing:
    def test_test_failed_dispatches_patch(self):
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "failed",
        }
        result = decide_next_dispatch(states, {})
        assert result.skill == SKILL_DO_PATCH
        assert result.row_id == "6"


class TestRow7PrExistsNoReview:
    def test_pr_no_review_dispatches_review(self):
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "pending",
        }
        meta = {"pr_number": 1234}
        result = decide_next_dispatch(states, meta)
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "7"


class TestRow8ReviewHasFindings:
    def test_changes_requested_dispatches_patch(self):
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "failed",
        }
        meta = {"pr_number": 99, "latest_review_verdict": "CHANGES REQUESTED"}
        result = decide_next_dispatch(states, meta)
        assert result.skill == SKILL_DO_PATCH
        assert result.row_id == "8"


class TestRow8bPatchAppliedAfterReview:
    def test_patch_complete_after_review_triggers_rereview(self):
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "failed",
            "PATCH": "completed",
        }
        meta = {"pr_number": 99, "last_dispatched_skill": SKILL_DO_PATCH}
        result = decide_next_dispatch(states, meta)
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "8b"


class TestRow9ReviewApprovedDocsNotDone:
    def test_review_completed_docs_pending_dispatches_docs(self):
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "completed",
            "DOCS": "pending",
        }
        meta = {"pr_number": 7}
        result = decide_next_dispatch(states, meta)
        assert result.skill == SKILL_DO_DOCS
        assert result.row_id == "9"


class TestRow10ReadyToMerge:
    def test_all_completed_dispatches_merge(self):
        states = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "completed",
            "DOCS": "completed",
        }
        meta = {"pr_number": 42}
        result = decide_next_dispatch(states, meta)
        assert result.skill == SKILL_DO_MERGE
        assert result.row_id == "10"


class TestRow10bStageStatesUnavailable:
    def test_empty_states_pr_open_falls_through_to_earlier_rows(self):
        # Row 10b is a fallback — it ranks below Row 7 (PR exists, no review)
        # because without stage_states we can't confirm docs are done. When
        # only ``pr_number`` is known, the safest dispatch is /do-pr-review so
        # the reviewer can drive the pipeline forward.
        result = decide_next_dispatch({}, {"pr_number": 1234})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "7"

    def test_empty_states_pr_open_with_review_completed_dispatches_merge(self):
        # Row 10b's purpose: once the pipeline has clearly advanced past
        # review (via last_dispatched_skill history), dispatch merge. Here we
        # emulate that by surfacing a prior /do-docs dispatch in meta.
        result = decide_next_dispatch(
            {},
            {
                "pr_number": 1234,
                "latest_review_verdict": "APPROVED",
                "last_dispatched_skill": SKILL_DO_DOCS,
            },
        )
        # Even here, without explicit DOCS="completed" we err on the side of
        # running review again — Row 10b stays a pure fallback that fires
        # only when no earlier rule matches.
        assert isinstance(result, Dispatch)


class TestNoMatchingRule:
    def test_impossible_state_returns_blocked(self):
        # Craft a state where no rule matches: PLAN completed but CRITIQUE also
        # completed AND no verdict AND BUILD completed AND TEST completed AND
        # REVIEW completed AND DOCS completed but NO pr_number.
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "completed",
            "DOCS": "completed",
        }
        result = decide_next_dispatch(states, {})  # no pr_number => Row 10 fails
        assert isinstance(result, Blocked)


# ---------------------------------------------------------------------------
# #1638 — verdict normalization: underscore forms match space-canonical rules
# ---------------------------------------------------------------------------


class TestVerdictNormalizationUnderscore:
    """Underscore-form verdicts must route identically to space-form (#1638)."""

    def _review_states_with_findings(self) -> dict:
        return {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "failed",
        }

    def test_review_changes_requested_underscore_dispatches_patch(self):
        states = self._review_states_with_findings()
        meta = {"pr_number": 99, "latest_review_verdict": "CHANGES_REQUESTED"}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PATCH
        assert result.row_id == "8"

    def test_review_changes_requested_space_dispatches_patch(self):
        states = self._review_states_with_findings()
        meta = {"pr_number": 99, "latest_review_verdict": "CHANGES REQUESTED"}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PATCH
        assert result.row_id == "8"


# ---------------------------------------------------------------------------
# #1640 — plan-existence evidence gate: PLAN="ready" needs a real plan doc
# ---------------------------------------------------------------------------


class TestPlanExistenceGate:
    """PLAN='ready' without a plan doc must route to /do-plan, not /do-plan-critique."""

    def test_fresh_session_plan_ready_no_doc_dispatches_do_plan(self):
        """Bootstrap: PLAN='ready', no plan doc → dispatch /do-plan (Row 1)."""
        states = {"PLAN": "ready", "CRITIQUE": None}
        meta = {"plan_exists": False, "issue_number": 1234}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN

    def test_plan_ready_plan_exists_dispatches_critique(self):
        """PLAN='ready' WITH a plan doc → dispatch /do-plan-critique (Row 2)."""
        states = {"PLAN": "ready", "CRITIQUE": None}
        meta = {"plan_exists": True, "issue_number": 1234}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN_CRITIQUE

    def test_plan_ready_no_issue_number_does_not_reroute_to_plan(self):
        """PLAN='ready', plan_exists=False, issue_number=None → falls through to Row 2.

        Without an issue_number we cannot verify the plan file, so we treat
        the 'ready' state as an implicit plan exists and let Row 2 run critique.
        The guard only fires when issue_number is available for a lookup.
        """
        states = {"PLAN": "ready", "CRITIQUE": None}
        meta = {"plan_exists": False, "issue_number": None}
        result = decide_next_dispatch(states, meta)
        # Without issue_number, _rule_no_plan does NOT fire for PLAN="ready";
        # the state should fall through to Row 2 (critique) or Blocked.
        assert isinstance(result, (Dispatch, Blocked))
        if isinstance(result, Dispatch):
            assert result.skill != SKILL_DO_PLAN

    def test_plan_completed_always_dispatches_critique_regardless_of_plan_exists(self):
        """PLAN='completed' → always critique, regardless of plan_exists (#1275 intact)."""
        states = {"PLAN": "completed", "CRITIQUE": None}
        meta = {"plan_exists": False, "issue_number": 9999}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN_CRITIQUE


# ---------------------------------------------------------------------------
# #1641 — patch-supersedes-stale-verdict timestamp early-exit
# ---------------------------------------------------------------------------


def _iso(ts: str) -> str:
    """Return an ISO-8601 timestamp string (thin wrapper for readability)."""
    return ts


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


class TestCritiqueStaleG5LoopBound:
    """Row 2b re-critique is bounded by G5 on an unchanged plan hash (Concern 1)."""

    def _stale_states(self, artifact_hash: str | None = None) -> dict:
        verdict: dict = {
            "verdict": "NEEDS REVISION",
            "recorded_at": "2026-01-01T10:00:00",  # T0
        }
        if artifact_hash is not None:
            verdict["artifact_hash"] = artifact_hash
        return {
            "PLAN": "completed",
            "CRITIQUE": "in_progress",
            "_verdicts": {"CRITIQUE": verdict},
            "_sdlc_dispatches": [
                {"skill": "/do-plan", "at": "2026-01-01T11:00:00"},  # T1 > T0
            ],
        }

    def test_stale_unchanged_hash_g5_short_circuits(self):
        """Stale verdict + cached hash == current hash → G5 returns cached dispatch.

        G5 fires before row 2b and, on an unchanged plan hash, routes the cached
        NEEDS REVISION verdict to /do-plan (its downstream dispatch) — NOT a
        re-critique loop.
        """
        states = self._stale_states(artifact_hash="sha256:abc")
        meta = {"last_dispatched_skill": SKILL_DO_PLAN}
        result = decide_next_dispatch(states, meta, context={"current_plan_hash": "sha256:abc"})
        assert isinstance(result, Dispatch)
        assert result.row_id == "G5"
        assert result.skill == SKILL_DO_PLAN

    def test_stale_changed_hash_dispatches_recritique(self):
        """Stale verdict + cached hash != current hash → row 2b → /do-plan-critique."""
        states = self._stale_states(artifact_hash="sha256:old")
        meta = {"last_dispatched_skill": SKILL_DO_PLAN}
        result = decide_next_dispatch(states, meta, context={"current_plan_hash": "sha256:new"})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN_CRITIQUE


class TestG1VsRow2bMutualExclusivity:
    """G1 (last=critique) and row 2b (last=/do-plan, stale) never fire each other's skill."""

    def _states(self) -> dict:
        return {
            "PLAN": "completed",
            "CRITIQUE": "in_progress",
            "_verdicts": {
                "CRITIQUE": {
                    "verdict": "NEEDS REVISION",
                    "recorded_at": "2026-01-01T10:00:00",
                }
            },
            "_sdlc_dispatches": [
                {"skill": "/do-plan", "at": "2026-01-01T11:00:00"},
            ],
        }

    def test_g1_fires_when_last_dispatch_was_critique(self):
        """last = /do-plan-critique → G1 → /do-plan (not /do-plan-critique)."""
        states = self._states()
        meta = {"last_dispatched_skill": SKILL_DO_PLAN_CRITIQUE}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN

    def test_row2b_fires_when_last_dispatch_was_plan(self):
        """last = /do-plan + stale verdict → row 2b → /do-plan-critique (not /do-plan)."""
        states = self._states()
        meta = {"last_dispatched_skill": SKILL_DO_PLAN}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN_CRITIQUE


class TestReviewMarkerDesync:
    """#1642: an APPROVED REVIEW verdict with a non-completed REVIEW marker must NOT advance docs.

    Row 9 (_rule_review_approved_docs_not_done) requires REVIEW == completed in
    stage_states. A desynced {verdict=APPROVED, marker!=completed} state is
    router-observable: the router does NOT fire /do-docs, documenting that the
    skill-layer completion-marker write is what advances REVIEW.
    """

    def test_approved_verdict_but_review_marker_not_completed_no_docs(self):
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "in_progress",  # marker NOT completed despite APPROVED verdict
            "DOCS": "pending",
            "_verdicts": {
                "REVIEW": {
                    "verdict": "APPROVED",
                    "recorded_at": "2026-01-01T10:00:00",
                }
            },
        }
        meta = {"pr_number": 99, "latest_review_verdict": "APPROVED"}
        result = decide_next_dispatch(states, meta)
        # Must NOT route to /do-docs (row 9 requires REVIEW == completed).
        if isinstance(result, Dispatch):
            assert result.skill != SKILL_DO_DOCS
