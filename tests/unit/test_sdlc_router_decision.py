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
    _critique_verdict_is_stale,
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
        # #1932 gap (c): row 9 now requires a recorded APPROVED verdict, not
        # just REVIEW==completed — record one so this exercises row 9's real
        # (fixed) contract instead of the old verdict-blind misroute.
        meta = {"pr_number": 7, "latest_review_verdict": "APPROVED"}
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
        # WS3a (#2062): row 10 requires a recorded APPROVED verdict, mirroring
        # row 9 -- REVIEW==completed alone is no longer merge-ready.
        meta = {"pr_number": 42, "latest_review_verdict": "APPROVED"}
        result = decide_next_dispatch(states, meta)
        assert result.skill == SKILL_DO_MERGE
        assert result.row_id == "10"

    def test_all_completed_without_verdict_routes_to_review_not_merge(self):
        """WS3a/b (#2062): the same all-completed state with NO recorded
        verdict must re-review (row 8e), never merge."""
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
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "8e"


class TestStageStatesUnavailableNoMergeDispatch:
    """Row 10b deleted (#2003): stage_states-unavailable + open PR must never
    fall back to dispatching /do-merge. Enforcement of the merge predicate
    lives in the merge-guard hook (tools.merge_predicate); the router only
    schedules merge via row 10 (all stages confirmed completed) or G6.
    """

    def test_row_10b_removed_from_dispatch_rules(self):
        assert "10b" not in {r.row_id for r in DISPATCH_RULES}

    def test_empty_states_pr_open_routes_to_review_not_merge(self):
        # Without stage_states we cannot confirm docs/review are done, so the
        # safest dispatch is /do-pr-review — never merge.
        result = decide_next_dispatch({}, {"pr_number": 1234})
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "7"

    def test_empty_states_past_docs_never_dispatches_merge(self):
        # Pre-#2003 this state could reach row 10b's merge fallback. Now the
        # router must either dispatch a non-merge skill or report Blocked —
        # stage_states-unavailable is never sufficient evidence to merge.
        result = decide_next_dispatch(
            {},
            {
                "pr_number": 1234,
                "latest_review_verdict": "APPROVED",
                "last_dispatched_skill": SKILL_DO_DOCS,
            },
        )
        if isinstance(result, Dispatch):
            assert result.skill != SKILL_DO_MERGE
        else:
            assert isinstance(result, Blocked)


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


class TestConvergenceLatchRevisionAppliedAt:
    """Event-scoped convergence latch (#1760).

    A bare ``revision_applied`` boolean can't distinguish "this is the
    settle-and-build dispatch" from "this is some later unrelated /do-plan
    dispatch" -- /do-plan sets it true on every revision pass. Pairing it
    with an event-scoped ``meta["revision_applied_at"]`` timestamp lets
    ``_critique_verdict_is_stale`` suppress staleness only when the latest
    ``/do-plan`` dispatch is NOT LATER than ``revision_applied_at``.
    """

    def test_1760_loop_replay_settles_to_build(self):
        """The exact #1760 loop replay: notes-only revision converges to
        /do-build instead of re-dispatching /do-plan-critique forever.

        READY TO BUILD verdict recorded at T0. A notes-only revision runs
        /do-plan (dispatch at T0.5, postdating the verdict) and sets
        revision_applied_at to T1 (postdating the dispatch). The latch must
        suppress staleness so row 2b steps aside and row 4a routes to
        /do-build.
        """
        states = {
            "PLAN": "completed",
            "CRITIQUE": "in_progress",
            "_verdicts": {
                "CRITIQUE": {
                    "verdict": "READY TO BUILD",
                    "recorded_at": "2026-01-01T10:00:00",  # T0
                }
            },
            "_sdlc_dispatches": [
                {"skill": "/do-plan", "at": "2026-01-01T10:30:00"},  # T0.5 > T0
            ],
        }
        meta = {
            "last_dispatched_skill": SKILL_DO_PLAN,
            "latest_critique_verdict": "READY TO BUILD",
            "revision_applied": True,
            "revision_applied_at": "2026-01-01T11:00:00",  # T1 > T0.5 (dispatch)
        }
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_BUILD
        assert result.skill != SKILL_DO_PLAN_CRITIQUE

    def test_predicate_returns_false_on_settle_and_build_case(self):
        """Isolated-predicate test: calling _critique_verdict_is_stale directly
        (not via the row-2b wrapper) proves the PREDICATE changed, not just a
        caller.
        """
        states = {
            "_verdicts": {
                "CRITIQUE": {
                    "verdict": "READY TO BUILD",
                    "recorded_at": "2026-01-01T10:00:00",  # T0
                }
            },
            "_sdlc_dispatches": [
                {"skill": "/do-plan", "at": "2026-01-01T10:30:00"},  # T0.5 > T0
            ],
        }
        meta = {"revision_applied_at": "2026-01-01T11:00:00"}  # T1 > T0.5
        assert _critique_verdict_is_stale(states, meta) is False

    def test_later_unrelated_dispatch_re_stales_normally(self):
        """A /do-plan dispatch postdating revision_applied_at must re-stale
        normally -- a genuinely-stale verdict must not be routed to BUILD
        just because revision_applied happens to still read true.
        """
        states = {
            "PLAN": "completed",
            "CRITIQUE": "in_progress",
            "_verdicts": {
                "CRITIQUE": {
                    "verdict": "READY TO BUILD",
                    "recorded_at": "2026-01-01T10:00:00",  # T0
                }
            },
            "_sdlc_dispatches": [
                {"skill": "/do-plan", "at": "2026-01-02T00:00:00"},  # T2 > T1
            ],
        }
        meta = {
            "last_dispatched_skill": SKILL_DO_PLAN,
            "latest_critique_verdict": "READY TO BUILD",
            "revision_applied": True,
            "revision_applied_at": "2026-01-01T11:00:00",  # T1 < T2 (dispatch)
        }
        assert _critique_verdict_is_stale(states, meta) is True
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN_CRITIQUE

    def test_predicate_malformed_recorded_at_still_false(self):
        """Existing fail-safe preserved: malformed recorded_at → False, even
        with a well-formed revision_applied_at present in meta.
        """
        states = {
            "_verdicts": {
                "CRITIQUE": {
                    "verdict": "READY TO BUILD",
                    "recorded_at": "not-a-date",
                }
            },
            "_sdlc_dispatches": [{"skill": "/do-plan", "at": "2026-01-01T11:00:00"}],
        }
        meta = {"revision_applied_at": "2026-01-01T11:30:00"}
        assert _critique_verdict_is_stale(states, meta) is False

    def test_predicate_malformed_revision_applied_at_falls_back_to_timestamp_only(self):
        """Malformed/absent revision_applied_at leaves the latch inert -- the
        predicate falls back to the pre-#1760 timestamp-only staleness check
        (never wrongly reports "not stale" when the latch signal is missing).
        """
        states = {
            "_verdicts": {
                "CRITIQUE": {
                    "verdict": "READY TO BUILD",
                    "recorded_at": "2026-01-01T10:00:00",  # T0
                }
            },
            "_sdlc_dispatches": [
                {"skill": "/do-plan", "at": "2026-01-01T11:00:00"},  # T1 > T0
            ],
        }
        # Malformed revision_applied_at -> latch inert -> normal staleness (True).
        assert _critique_verdict_is_stale(states, {"revision_applied_at": "not-a-date"}) is True
        # Absent revision_applied_at -> latch inert -> normal staleness (True).
        assert _critique_verdict_is_stale(states, {}) is True
        assert _critique_verdict_is_stale(states, None) is True


class TestCritiqueInProgressNoVerdictDeadEnd:
    """CRITIQUE in_progress with NO recorded verdict must re-dispatch /do-plan-critique (#1668).

    Reconstructed from the real issue-1643 run (PR #1664). The
    ``/do-plan-critique`` skill ran but never persisted a verdict to
    ``_verdicts.CRITIQUE`` — the war-room cycles happened in-skill and the
    verdict-record step never fired, leaving CRITIQUE stuck at ``in_progress``.
    With no verdict text and no ``recorded_at``:

    - row 2 (``_rule_plan_not_critiqued``) excludes ``in_progress`` → no match
    - row 2b (``_rule_critique_verdict_stale``) needs ``recorded_at`` → no match
    - rows 3/4a/4b/4c need a recorded verdict → no match
    - G1 needs NEEDS REVISION / MAJOR REWORK verdict text → no match

    Result before the fix: ``Blocked('no matching dispatch rule')`` — the exact
    dead-end the supervisor navigated manually by re-running /do-plan-critique.
    This is a distinct variant from the row-2b NEEDS-REVISION staleness case.
    """

    def _dead_end_states(self) -> dict:
        """The exact reconstructed issue-1643 state at the dead-end moment."""
        return {
            "ISSUE": "ready",
            "PLAN": "completed",
            "CRITIQUE": "in_progress",
            "BUILD": "pending",
            "TEST": "pending",
            "PATCH": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
            "MERGE": "pending",
            "_verdicts": {},  # critique ran but never recorded a verdict
            "_sdlc_dispatches": [
                {"skill": "/do-plan", "at": "2026-06-12T19:25:35.011316+00:00"},
                {"skill": "/do-plan-critique", "at": "2026-06-12T19:32:07.722784+00:00"},
            ],
        }

    def test_in_progress_no_verdict_no_pr_redispatches_critique(self):
        """CRITIQUE in_progress + empty verdict + no PR → re-dispatch /do-plan-critique."""
        states = self._dead_end_states()
        meta = {
            "issue_number": 1643,
            "plan_exists": True,
            "revision_applied": False,
            "latest_critique_verdict": None,
            "pr_number": None,
            "last_dispatched_skill": SKILL_DO_PLAN_CRITIQUE,
        }
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch), f"expected Dispatch, got {result!r}"
        assert result.skill == SKILL_DO_PLAN_CRITIQUE

    def test_in_progress_no_verdict_with_pr_defers_to_pr_stage(self):
        """Once a PR exists, the stalled-critique fast-path must NOT fire.

        The real 1643 run progressed to a PR via PR-stage rows while CRITIQUE
        stayed in_progress. A PR open means plan/critique are behind us — the
        re-dispatch must defer to G3/PR-stage routing, never bounce back to
        /do-plan-critique.
        """
        states = self._dead_end_states()
        states["REVIEW"] = "pending"
        meta = {
            "issue_number": 1643,
            "plan_exists": True,
            "pr_number": 1664,
            "last_dispatched_skill": SKILL_DO_PLAN_CRITIQUE,
        }
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill != SKILL_DO_PLAN_CRITIQUE

    def test_in_progress_with_recorded_verdict_unaffected(self):
        """A recorded verdict must still route via rows 2b/3/4a — fast-path is verdict-gated."""
        states = self._dead_end_states()
        states["_verdicts"] = {
            "CRITIQUE": {"verdict": "READY TO BUILD", "recorded_at": "2026-06-12T19:40:00+00:00"}
        }
        meta = {
            "issue_number": 1643,
            "plan_exists": True,
            "latest_critique_verdict": "READY TO BUILD",
            "pr_number": None,
            "last_dispatched_skill": SKILL_DO_PLAN_CRITIQUE,
        }
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        # READY TO BUILD, no concerns → row 4a → /do-build (NOT a re-critique)
        assert result.skill == SKILL_DO_BUILD


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


class TestReviewInProgressNoVerdictDeadEnd:
    """REVIEW in_progress with NO recorded verdict must re-dispatch /do-pr-review (#1687).

    Gap A (REVIEW path analogue of CRITIQUE row 2c / #1668): /do-pr-review ran
    (REVIEW marker == "in_progress") but never persisted a verdict, so
    _verdicts.REVIEW is empty and latest_review_verdict is None. With rows 7/8/8b
    all missing (no verdict, no completed PATCH), the router fell through to
    Blocked('no matching dispatch rule'). Row 8c recovers by re-dispatching
    /do-pr-review.

    Gap demonstration (concern #1): the UNFIXED router state (REVIEW in_progress,
    empty verdict, no completed PATCH, PR present) is confirmed below via the
    no-8c baseline fixture. The post-fix assertion documents the closed gap.
    """

    def _dead_end_states(self) -> dict:
        """Reconstructed dead-end state: REVIEW in_progress, no verdict recorded."""
        return {
            "ISSUE": "ready",
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "PATCH": "pending",
            "REVIEW": "in_progress",  # marker stuck — review ran but no verdict
            "DOCS": "pending",
            "MERGE": "pending",
            "_verdicts": {},  # review ran but never recorded a verdict
            "_sdlc_dispatches": [
                {"skill": "/do-build", "at": "2026-06-14T10:00:00+00:00"},
                {"skill": "/do-pr-review", "at": "2026-06-14T10:30:00+00:00"},
            ],
        }

    def test_in_progress_no_verdict_pr_present_redispatches_review(self):
        """REVIEW in_progress + empty verdict + PR present → re-dispatch /do-pr-review (row 8c).

        This is the fixed state: what was previously Blocked('no matching dispatch rule')
        (Gap A) now resolves to Dispatch(/do-pr-review) via new row 8c.
        """
        states = self._dead_end_states()
        meta = {
            "issue_number": 1687,
            "pr_number": 9999,
            "latest_review_verdict": None,
            "last_dispatched_skill": SKILL_DO_PR_REVIEW,
        }
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch), f"expected Dispatch, got {result!r}"
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "8c"

    def test_in_progress_no_verdict_no_pr_does_not_fire_8c(self):
        """No PR → row 8c must NOT fire (REVIEW only exists post-PR).

        Defensive safety gate: a REVIEW in_progress state with no PR is
        structurally impossible in a well-ordered pipeline, but the predicate
        guards against it explicitly.
        """
        states = self._dead_end_states()
        states["REVIEW"] = "in_progress"
        meta = {
            "issue_number": 1687,
            "pr_number": None,
            "latest_review_verdict": None,
            "last_dispatched_skill": SKILL_DO_PR_REVIEW,
        }
        result = decide_next_dispatch(states, meta)
        # Row 8c requires pr_number; without it, must NOT fire 8c.
        if isinstance(result, Dispatch):
            assert result.row_id != "8c", "8c fired without a PR — predicate gate broken"

    def test_whitespace_only_verdict_treated_as_empty_fires_8c(self):
        """A whitespace-only verdict must be treated as empty so row 8c fires.

        Mirrors the .strip() correctness requirement from critique concern #2:
        a verdict of " " or "\n" must NOT read as "recorded" — it must fire 8c.
        """
        states = self._dead_end_states()
        states["REVIEW"] = "in_progress"
        states["_verdicts"] = {"REVIEW": {"verdict": "   ", "recorded_at": None}}
        meta = {
            "issue_number": 1687,
            "pr_number": 9999,
            "latest_review_verdict": "   ",  # whitespace only
            "last_dispatched_skill": SKILL_DO_PR_REVIEW,
        }
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch), f"expected Dispatch, got {result!r}"
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "8c", f"expected row 8c, got {result.row_id!r}"

    def test_patch_applied_after_review_defers_to_8b_not_8c(self):
        """Row 8b (patch applied) owns states 8c must step aside from.

        8c steps aside for 8b when PATCH==completed AND last_dispatched_skill==/do-patch
        (the three-condition 8b predicate). Using _rule_patch_applied_after_review
        as the gate (not a bare PATCH==completed check) is load-bearing.
        """
        states = self._dead_end_states()
        states["REVIEW"] = "in_progress"
        states["PATCH"] = "completed"
        meta = {
            "issue_number": 1687,
            "pr_number": 9999,
            "latest_review_verdict": None,
            "last_dispatched_skill": SKILL_DO_PATCH,  # row 8b\'s third condition
        }
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch), f"expected Dispatch, got {result!r}"
        assert result.skill == SKILL_DO_PR_REVIEW
        # Row 8b owns this state (patch applied after review), not 8c.
        assert result.row_id == "8b", f"expected row 8b, got {result.row_id!r}"

    def test_patch_completed_wrong_last_skill_8c_owns_state(self):
        """PATCH completed but last_dispatched_skill != /do-patch → 8b does not own it, 8c must.

        This is the "Blocked leak" prevention case: if we used a bare
        PATCH==completed check in 8c, this state would fall through both 8b and 8c
        to Blocked. Gating on _rule_patch_applied_after_review() exactly means 8c
        correctly owns this case and re-dispatches /do-pr-review.
        """
        states = self._dead_end_states()
        states["REVIEW"] = "in_progress"
        states["PATCH"] = "completed"
        meta = {
            "issue_number": 1687,
            "pr_number": 9999,
            "latest_review_verdict": None,
            "last_dispatched_skill": SKILL_DO_BUILD,  # NOT /do-patch — 8b skips it
        }
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch), f"expected Dispatch, got {result!r}"
        assert result.skill == SKILL_DO_PR_REVIEW
        # 8b did not fire (wrong last_dispatched_skill), so 8c must own this state.
        assert result.row_id == "8c", f"expected row 8c, got {result.row_id!r}"

    def test_recorded_verdict_unaffected_routes_via_row8(self):
        """A recorded CHANGES REQUESTED verdict still routes via row 8 — not 8c."""
        states = self._dead_end_states()
        states["REVIEW"] = "in_progress"
        states["_verdicts"] = {
            "REVIEW": {"verdict": "CHANGES REQUESTED", "recorded_at": "2026-06-14T10:45:00+00:00"}
        }
        meta = {
            "issue_number": 1687,
            "pr_number": 9999,
            "latest_review_verdict": "CHANGES REQUESTED",
            "last_dispatched_skill": SKILL_DO_PR_REVIEW,
        }
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PATCH
        assert result.row_id == "8", f"expected row 8, got {result.row_id!r}"

    def test_ordering_8b_before_8c_before_9(self):
        """Row ordering: 8b must precede 8c, which must precede 9 in DISPATCH_RULES."""
        from agent.sdlc_router import DISPATCH_RULES

        row_ids = [r.row_id for r in DISPATCH_RULES]
        idx_8b = row_ids.index("8b")
        idx_8c = row_ids.index("8c")
        idx_9 = row_ids.index("9")
        assert idx_8b < idx_8c, f"8b ({idx_8b}) must come before 8c ({idx_8c})"
        assert idx_8c < idx_9, f"8c ({idx_8c}) must come before 9 ({idx_9})"


class TestNeedsRevisionInvalidatedByRevision:
    """WS4 (#2049): a plan revision INVALIDATES a NEEDS REVISION verdict.

    The #1760 latch exists to protect the settle-and-build path: a READY TO
    BUILD (with concerns) verdict stays fresh across its own settle revision
    so row 4c can route to BUILD. But the latch previously engaged for EVERY
    verdict kind — including NEEDS REVISION, where the requested revision is
    exactly what invalidates the verdict. Suppressing staleness there made
    row 2b step aside and row 3 re-dispatch ``/do-plan`` forever (the
    #1925/#1968 deadlock, recurring because ``/do-plan`` re-writes
    ``revision_applied_at`` on every pass, re-arming the suppression).

    Timestamp-only fix: the latch consumes ONLY ``revision_applied_at`` and
    now engages solely for non-revision-requiring verdicts. No boolean
    fallback exists on any path.
    """

    @staticmethod
    def _needs_revision_state(verdict_at, plan_dispatch_at, revision_applied_at):
        states = {
            "PLAN": "completed",
            "CRITIQUE": "in_progress",
            "_verdicts": {"CRITIQUE": {"verdict": "NEEDS REVISION", "recorded_at": verdict_at}},
            "_sdlc_dispatches": [{"skill": "/do-plan", "at": plan_dispatch_at}],
        }
        meta = {
            "last_dispatched_skill": SKILL_DO_PLAN,
            "latest_critique_verdict": "NEEDS REVISION",
            "revision_applied": True,
            "revision_applied_at": revision_applied_at,
        }
        return states, meta

    def test_settled_revision_routes_to_re_critique_first_round(self):
        """critique(NEEDS REVISION) → /do-plan revision (co-writes
        revision_applied_at) → next dispatch is /do-plan-critique, never
        /do-plan again."""
        states, meta = self._needs_revision_state(
            "2026-07-13T10:00:00",  # verdict T1
            "2026-07-13T10:10:00",  # /do-plan revision T2 > T1
            "2026-07-13T10:20:00",  # revision_applied_at T3 >= T2
        )
        assert _critique_verdict_is_stale(states, meta) is True
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN_CRITIQUE
        assert result.skill != SKILL_DO_PLAN

    def test_settled_revision_routes_to_re_critique_second_round(self):
        """The convergence must hold twice in a row: a SECOND NEEDS REVISION
        verdict followed by a second settled revision re-routes to
        /do-plan-critique again (no deadlock on round 2 either)."""
        states, meta = self._needs_revision_state(
            "2026-07-13T11:00:00",  # round-2 verdict T4
            "2026-07-13T11:10:00",  # round-2 /do-plan revision T5 > T4
            "2026-07-13T11:20:00",  # round-2 revision_applied_at T6 >= T5
        )
        # Round-1 history precedes round 2 in the dispatch list.
        states["_sdlc_dispatches"] = [
            {"skill": "/do-plan", "at": "2026-07-13T10:10:00"},
            {"skill": "/do-plan-critique", "at": "2026-07-13T10:30:00"},
            {"skill": "/do-plan", "at": "2026-07-13T11:10:00"},
        ]
        assert _critique_verdict_is_stale(states, meta) is True
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN_CRITIQUE

    def test_fresh_needs_revision_still_routes_to_do_plan(self):
        """Inverse: a NEEDS REVISION verdict with NO revision yet (verdict is
        the latest event) is NOT stale — row 3 (or G1) still routes to
        /do-plan for the revision itself."""
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "_verdicts": {
                "CRITIQUE": {
                    "verdict": "NEEDS REVISION",
                    "recorded_at": "2026-07-13T10:00:00",
                }
            },
            "_sdlc_dispatches": [{"skill": "/do-plan", "at": "2026-07-13T09:00:00"}],
        }
        meta = {
            "last_dispatched_skill": SKILL_DO_PLAN_CRITIQUE,
            "latest_critique_verdict": "NEEDS REVISION",
            "revision_applied": False,
        }
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN

    def test_1760_inverse_guarantee_preserved(self):
        """The settle-and-build latch still protects READY TO BUILD: a
        with-concerns verdict whose settle revision co-wrote
        revision_applied_at routes to /do-build, not back to re-critique."""
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "_verdicts": {
                "CRITIQUE": {
                    "verdict": "READY TO BUILD (with concerns)",
                    "recorded_at": "2026-07-13T10:00:00",
                }
            },
            "_sdlc_dispatches": [{"skill": "/do-plan", "at": "2026-07-13T10:10:00"}],
        }
        meta = {
            "last_dispatched_skill": SKILL_DO_PLAN,
            "latest_critique_verdict": "READY TO BUILD (with concerns)",
            "revision_applied": True,
            "revision_applied_at": "2026-07-13T10:20:00",
        }
        assert _critique_verdict_is_stale(states, meta) is False
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_BUILD

    def test_no_boolean_fallback_bare_revision_applied_changes_nothing(self):
        """Timestamp-only: with revision_applied=True but NO
        revision_applied_at, the latch is inert on both verdict kinds — the
        sticky boolean alone never suppresses staleness (no free pass to
        BUILD, no suppression of re-critique)."""
        states, meta = self._needs_revision_state(
            "2026-07-13T10:00:00",
            "2026-07-13T10:10:00",
            None,
        )
        del meta["revision_applied_at"]
        assert meta["revision_applied"] is True
        # Latch inert -> plain timestamp staleness -> stale -> re-critique.
        assert _critique_verdict_is_stale(states, meta) is True
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN_CRITIQUE
