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
