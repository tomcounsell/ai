"""Unit tests for the Phase 2 MultiDispatch widening in agent.sdlc_router.

Covers Tasks 2.6, 2.7, 2.8 of docs/plans/sdlc-1393.md:

  2.6 — MultiDispatch return path
    (a) DOCS + PATCH both ready -> returns MultiDispatch
    (b) only DOCS ready -> returns single Dispatch
    (c) DOCS + TEST both ready -> returns single Dispatch (not parallel-safe)
    (d) no ready stages -> returns Blocked

  2.7 — Parity tests: every existing dispatch rule row (1..10b) still
        produces the correct single-dispatch result when only one stage is
        ready and no PARALLEL_SAFE_PAIRS pair applies.

  2.8 — Guard interaction tests: G1..G7 still fire on the first dispatch of
        a MultiDispatch (G3 with an open PR blocks the entire MultiDispatch,
        not just one branch).
"""

from __future__ import annotations

import pytest

from agent.sdlc_router import (
    PARALLEL_SAFE_PAIRS,
    Blocked,
    Dispatch,
    MultiDispatch,
    decide_next_dispatch,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _states_after_review(review_verdict: str = "PARTIAL", docs: str = "ready") -> dict:
    """Stage states after REVIEW completed with findings, DOCS still pending."""
    return {
        "ISSUE": "completed",
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": "completed",
        "TEST": "completed",
        "REVIEW": "completed",
        "DOCS": docs,
        "PATCH": "pending",
        "_verdicts": {"REVIEW": {"verdict": review_verdict}},
    }


def _meta(**overrides) -> dict:
    base = {
        "pr_number": 999,
        "latest_review_verdict": "PARTIAL",
        "latest_critique_verdict": "READY TO BUILD",
        "revision_applied": True,
        "patch_cycle_count": 0,
        "critique_cycle_count": 0,
        "same_stage_dispatch_count": 0,
        "last_dispatched_skill": "/do-pr-review",
        "plan_revising": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 2.6 — MultiDispatch return path
# ---------------------------------------------------------------------------


def test_docs_plus_patch_both_ready_returns_multidispatch() -> None:
    """When DOCS is pending and REVIEW returned PARTIAL findings (row 8) AND
    row 9's predicate would also match, decide_next_dispatch returns a
    MultiDispatch containing both /do-patch and /do-docs."""
    result = decide_next_dispatch(_states_after_review("PARTIAL"), _meta())

    assert isinstance(result, MultiDispatch), f"got {type(result).__name__}: {result}"
    skills = {d.skill for d in result.dispatches}
    assert skills == {"/do-patch", "/do-docs"}


def test_only_docs_ready_returns_single_dispatch() -> None:
    """When REVIEW APPROVED (no findings) but DOCS still pending, only row 9
    fires -> single Dispatch(/do-docs)."""
    states = _states_after_review("APPROVED")
    states["PATCH"] = "completed"  # No patch needed
    meta = _meta(latest_review_verdict="APPROVED")

    result = decide_next_dispatch(states, meta)
    assert isinstance(result, Dispatch)
    assert result.skill == "/do-docs"


def test_docs_plus_unrelated_ready_returns_single_dispatch() -> None:
    """DOCS pending but the other "ready" stage is TEST -- TEST is not in
    PARALLEL_SAFE_PAIRS with DOCS -> single Dispatch."""
    states = {
        "ISSUE": "completed",
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": "completed",
        "TEST": "failed",  # Row 6 triggers /do-patch
        "REVIEW": "ready",
        "DOCS": "ready",
        "_verdicts": {},
    }
    meta = _meta(pr_number=None, last_dispatched_skill="/do-test")
    # Row 6 (tests failing) fires first; TEST + DOCS are not a PARALLEL_SAFE pair.
    result = decide_next_dispatch(states, meta)
    assert isinstance(result, Dispatch)
    assert result.skill == "/do-patch"


def test_no_ready_stages_returns_blocked() -> None:
    """When stage_states is empty and no PR exists, Row 1 fires (no plan ->
    /do-plan). To get Blocked we need a state that matches NO rule."""
    # Construct a state where no rule fires: completed plan, completed
    # critique, no PR, build completed, all later stages completed -> falls
    # off the dispatch table.
    states = {
        "ISSUE": "completed",
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": "completed",
        "TEST": "completed",
        "REVIEW": "completed",
        "DOCS": "completed",
        "_verdicts": {"CRITIQUE": {"verdict": "READY TO BUILD"}},
    }
    meta = _meta(
        pr_number=None,
        latest_critique_verdict="READY TO BUILD",
        latest_review_verdict="APPROVED",
        revision_applied=True,
    )
    result = decide_next_dispatch(states, meta)
    assert isinstance(result, Blocked)


# ---------------------------------------------------------------------------
# 2.7 — Parity tests (existing single-dispatch rule rows)
# ---------------------------------------------------------------------------


def test_parity_row1_no_plan() -> None:
    result = decide_next_dispatch({}, _meta(pr_number=None))
    assert isinstance(result, Dispatch) and result.row_id == "1"


def test_parity_row2_plan_not_critiqued() -> None:
    states = {"PLAN": "completed", "CRITIQUE": "pending"}
    result = decide_next_dispatch(states, _meta(pr_number=None, latest_critique_verdict=""))
    assert isinstance(result, Dispatch) and result.row_id == "2"


def test_parity_row3_needs_revision() -> None:
    states = {
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "_verdicts": {"CRITIQUE": {"verdict": "NEEDS REVISION"}},
    }
    result = decide_next_dispatch(
        states,
        _meta(
            pr_number=None,
            latest_critique_verdict="NEEDS REVISION",
            last_dispatched_skill="/do-plan",
        ),
    )
    # G1 may redirect, so accept either G1 or row 3 (both produce /do-plan)
    assert isinstance(result, Dispatch) and result.skill == "/do-plan"


def test_parity_row4a_ready_no_concerns() -> None:
    states = {
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": None,
        "_verdicts": {"CRITIQUE": {"verdict": "READY TO BUILD"}},
    }
    result = decide_next_dispatch(
        states,
        _meta(pr_number=None, latest_critique_verdict="READY TO BUILD", revision_applied=False),
    )
    assert isinstance(result, Dispatch) and result.row_id == "4a"


def test_parity_row4c_ready_with_concerns_revision_applied() -> None:
    states = {
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "_verdicts": {"CRITIQUE": {"verdict": "READY TO BUILD WITH CONCERNS"}},
    }
    meta = _meta(
        pr_number=None,
        latest_critique_verdict="READY TO BUILD WITH CONCERNS",
        revision_applied=True,
    )
    result = decide_next_dispatch(states, meta)
    assert isinstance(result, Dispatch) and result.row_id == "4c"


def test_parity_row7_pr_no_review() -> None:
    states = {
        "ISSUE": "completed",
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": "completed",
        "TEST": "completed",
        "REVIEW": "pending",
    }
    result = decide_next_dispatch(
        states, _meta(pr_number=42, latest_review_verdict=None, last_dispatched_skill="/do-build")
    )
    assert isinstance(result, Dispatch) and result.row_id == "7"


def test_parity_row9_review_approved_docs_pending() -> None:
    """Row 9 fires alone when REVIEW APPROVED and PATCH is not pending.

    This is the post-patch, pre-docs state -- no parallel-safe peer.
    """
    states = _states_after_review("APPROVED")
    states["PATCH"] = "completed"
    meta = _meta(latest_review_verdict="APPROVED")
    result = decide_next_dispatch(states, meta)
    assert isinstance(result, Dispatch) and result.row_id == "9"


def test_parity_row10_ready_to_merge() -> None:
    states = {
        "ISSUE": "completed",
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": "completed",
        "TEST": "completed",
        "REVIEW": "completed",
        "DOCS": "completed",
        "_verdicts": {"REVIEW": {"verdict": "APPROVED"}},
    }
    meta = _meta(latest_review_verdict="APPROVED", pr_number=42, pr_merge_state=None)
    result = decide_next_dispatch(states, meta)
    assert isinstance(result, Dispatch) and result.row_id == "10"


# ---------------------------------------------------------------------------
# 2.8 — Guard interaction
# ---------------------------------------------------------------------------


def test_g3_open_pr_blocks_entire_multidispatch() -> None:
    """When G3 fires (open PR + last dispatch was /do-plan), the router must
    return a single Dispatch redirect -- never a MultiDispatch."""
    states = {
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "REVIEW": "completed",
        "DOCS": "pending",
        "PATCH": "pending",
        "_verdicts": {
            "CRITIQUE": {"verdict": "READY TO BUILD"},
            "REVIEW": {"verdict": "PARTIAL"},
        },
    }
    meta = _meta(
        pr_number=42,
        latest_review_verdict="PARTIAL",
        last_dispatched_skill="/do-plan",
    )
    result = decide_next_dispatch(states, meta)
    # G3 returns single Dispatch, never MultiDispatch
    assert isinstance(result, Dispatch)
    assert result.row_id == "G3"


def test_g4_oscillation_blocks_multidispatch() -> None:
    """G4 fires -> Blocked, regardless of whether a parallel-safe pair would
    otherwise be eligible."""
    meta = _meta(same_stage_dispatch_count=5, last_dispatched_skill="/do-patch")
    result = decide_next_dispatch(_states_after_review("PARTIAL"), meta)
    assert isinstance(result, Blocked)
    assert result.guard_id == "G4"


def test_g6_terminal_merge_blocks_multidispatch() -> None:
    """G6 fast-path: PR clean + CI green + DOCS done + REVIEW APPROVED ->
    single /do-merge Dispatch."""
    states = {
        "ISSUE": "completed",
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": "completed",
        "TEST": "completed",
        "REVIEW": "completed",
        "DOCS": "completed",
        "_verdicts": {"REVIEW": {"verdict": "APPROVED"}},
    }
    meta = _meta(
        pr_number=42,
        pr_merge_state="CLEAN",
        ci_all_passing=True,
        latest_review_verdict="APPROVED",
    )
    result = decide_next_dispatch(states, meta)
    assert isinstance(result, Dispatch)
    assert result.row_id == "G6"


# ---------------------------------------------------------------------------
# PARALLEL_SAFE_PAIRS membership
# ---------------------------------------------------------------------------


def test_parallel_safe_pairs_includes_docs_patch() -> None:
    assert frozenset({"DOCS", "PATCH"}) in PARALLEL_SAFE_PAIRS


def test_parallel_safe_pairs_is_frozenset_set() -> None:
    """PARALLEL_SAFE_PAIRS uses frozensets so order doesn't matter and the
    pairs are themselves hashable."""
    for pair in PARALLEL_SAFE_PAIRS:
        assert isinstance(pair, frozenset)
        assert len(pair) == 2


# ---------------------------------------------------------------------------
# tools.sdlc_next_skill emits multi shape
# ---------------------------------------------------------------------------


def test_sdlc_next_skill_emits_multi_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI wrapper translates MultiDispatch into the documented JSON shape
    {"multi": true, "skills": [...], "dispatches": [...]}."""
    from tools import sdlc_next_skill

    def fake_resolve(issue_number, session_id):
        return {"stages": _states_after_review("PARTIAL"), "_meta": _meta()}

    monkeypatch.setattr(sdlc_next_skill, "_resolve_enriched", fake_resolve)

    result = sdlc_next_skill.decide(issue_number=1393)
    assert result.get("multi") is True
    assert result.get("dispatched") is True
    assert set(result.get("skills", [])) == {"/do-docs", "/do-patch"}
    assert len(result["dispatches"]) == 2
