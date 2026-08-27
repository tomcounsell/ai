"""Terminal lane state for agent.sdlc_router (#2894, #2817).

The router had no way to say "this lane is finished". It never read
``stage_states["MERGE"]`` and ``decide_next_dispatch`` returned only ``Dispatch``
or ``Blocked``, so a merged, closed, shipped lane kept matching dispatch rules
forever. Recon on 2026-08-26 measured **four** distinct post-merge misroutes on
real lanes, not the one originally filed:

===========  =========================  ==================================
Row          Skill it wrongly dispatched  Observed on
===========  =========================  ==================================
10           ``/do-merge``               #2853 / PR #2884 (the filed #2894)
8f           ``/do-pr-review``           #2734 / PR #2844, #2741 / PR #2842
5            ``/do-build``               #2817's measured matrix
1            ``/do-plan``                #2853 after its ledger emptied
===========  =========================  ==================================

That is why terminality is a **guard** that preempts the whole dispatch table
rather than a condition bolted onto row 10: a per-row fix would have to be
written four times and rewritten for every future row.

Two classes of test live here and must not be confused:

* **Terminal cases** — were RED before the fix (each returned a ``Dispatch``).
* **Negative controls** — #2817's measured four-cell pre-merge ``/do-merge``
  matrix, plus a fresh lane at ISSUE. These were GREEN before the fix and
  MUST STAY GREEN. A terminal guard that swallows a live pre-merge lane is
  strictly worse than the infinite re-dispatch it replaces.
"""

from __future__ import annotations

import pytest

from agent.sdlc_router import (
    SKILL_DO_MERGE,
    SKILL_DO_PLAN,
    Blocked,
    Dispatch,
    Terminal,
    decide_next_dispatch,
)

APPROVED_VERDICT = "APPROVED"
HEAD = "c488ce14ebc7f0b9da8028c6b6156389df7ad4c8"


def _states(**overrides) -> dict:
    base = {
        "ISSUE": "completed",
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": "completed",
        "TEST": "completed",
        "REVIEW": "completed",
        "DOCS": "completed",
        "MERGE": "pending",
    }
    base.update(overrides)
    return base


def _meta(**overrides) -> dict:
    base = {
        "pr_number": 2884,
        "pr_state": "OPEN",
        "pr_merge_state": "CLEAN",
        "ci_all_passing": True,
        "latest_review_verdict": APPROVED_VERDICT,
        "latest_review_head_sha": HEAD,
        "latest_critique_verdict": "READY TO BUILD (NO CONCERNS)",
        "plan_exists": True,
        "revision_applied": True,
    }
    base.update(overrides)
    return base


def _ctx(**overrides) -> dict:
    base = {"pr_head_sha": HEAD}
    base.update(overrides)
    return base


class TestTerminalOnMergeMarker:
    """MERGE == completed is positive evidence the lane is finished."""

    def test_merged_lane_is_terminal_not_do_merge(self):
        """#2894 as filed: row 10 re-dispatched /do-merge forever."""
        d = decide_next_dispatch(_states(MERGE="completed"), _meta(), _ctx())
        assert isinstance(d, Terminal), f"expected Terminal, got {d!r}"
        assert d.evidence == "merge_marker"

    def test_merged_lane_without_pr_number_is_terminal_not_no_rule(self):
        """#2817 as filed: Blocked(NO_RULE) on a MERGE-completed pipeline."""
        d = decide_next_dispatch(
            _states(MERGE="completed"),
            _meta(pr_number=None, pr_state=None, pr_merge_state=None),
            {},
        )
        assert isinstance(d, Terminal), f"expected Terminal, got {d!r}"

    def test_merged_lane_with_branch_and_no_pr_does_not_rebuild(self):
        """#2817's matrix: row 5 dispatched /do-build on shipped work.

        ``branch_exists`` is read from **context**, not meta — see
        ``_rule_branch_exists_no_pr``. Passing it in meta silently exercises a
        different (NO_RULE) path and proves nothing about row 5.
        """
        d = decide_next_dispatch(
            _states(MERGE="completed"),
            _meta(pr_number=None, pr_state=None, pr_merge_state=None),
            {"branch_exists": True},
        )
        assert isinstance(d, Terminal), f"expected Terminal, got {d!r}"

    def test_merged_lane_with_stale_verdict_does_not_rereview(self):
        """The #2734 / #2741 shape: row 8f re-reviewed two merged PRs."""
        d = decide_next_dispatch(
            _states(MERGE="completed"),
            _meta(latest_review_head_sha="a3f2ea432890f2273877f201f9dc207b75f5a5fd"),
            _ctx(pr_head_sha=HEAD),
        )
        assert isinstance(d, Terminal), f"expected Terminal, got {d!r}"

    @pytest.mark.parametrize("settled", ["completed", "skipped"])
    def test_settled_merge_marker_counts(self, settled):
        d = decide_next_dispatch(_states(MERGE=settled), _meta(), _ctx())
        assert isinstance(d, Terminal)


class TestTerminalOnMergedPr:
    """A merged PR is terminal even when the ledger has been lost."""

    def test_emptied_ledger_with_merged_pr_is_terminal(self):
        """#2853: ledger emptied post-merge, so row 1 dispatched /do-plan."""
        d = decide_next_dispatch({}, _meta(pr_state="MERGED", plan_exists=False), {})
        assert isinstance(d, Terminal), f"expected Terminal, got {d!r}"
        assert d.evidence == "merged_pr"

    def test_merged_pr_without_merge_marker_is_terminal(self):
        d = decide_next_dispatch(_states(), _meta(pr_state="MERGED"), _ctx())
        assert isinstance(d, Terminal), f"expected Terminal, got {d!r}"
        assert d.evidence == "merged_pr"

    def test_unknown_merge_state_alone_is_not_terminal(self):
        """UNKNOWN means three things; only pr_state can disambiguate."""
        d = decide_next_dispatch(
            _states(),
            _meta(pr_merge_state="UNKNOWN", pr_state="OPEN", latest_review_verdict=None),
            _ctx(),
        )
        assert not isinstance(d, Terminal), "UNKNOWN mergeability is not evidence of merge"


class TestNegativeControls:
    """#2817's four measured pre-merge /do-merge cells. GREEN before AND after."""

    @pytest.mark.parametrize("branch_exists", [True, False])
    @pytest.mark.parametrize("pr_number", [555, 2884])
    def test_pre_merge_approved_lane_still_dispatches_do_merge(self, branch_exists, pr_number):
        d = decide_next_dispatch(
            _states(MERGE="pending"),
            _meta(pr_number=pr_number, branch_exists=branch_exists),
            _ctx(),
        )
        assert isinstance(d, Dispatch), f"expected Dispatch, got {d!r}"
        assert d.skill == SKILL_DO_MERGE, f"expected /do-merge, got {d.skill}"

    def test_fresh_lane_still_routes_to_do_plan(self):
        """A brand-new issue must not be swallowed by the terminal guard."""
        d = decide_next_dispatch(
            {},
            {
                "pr_number": None,
                "pr_state": None,
                "pr_merge_state": None,
                "plan_exists": False,
            },
            {},
        )
        assert isinstance(d, Dispatch), f"expected Dispatch, got {d!r}"
        assert d.skill == SKILL_DO_PLAN

    def test_genuine_unresolvable_merge_state_still_blocks(self):
        """The GH_REPO / SDLC_TARGET_REPO message must survive for real misconfig.

        #2894's core warning: a naive terminal fix makes the ``primary is None``
        fallback reachable for merged lanes, and because a merged PR reports
        ``mergeable: UNKNOWN`` every finished lane would emit a spurious
        "go check your env" error. The terminal guard now absorbs merged-and-done
        upstream, so this message must fire ONLY for a genuinely open PR whose
        mergeability could not be resolved. That is this test.

        REVIEW pending + an APPROVED verdict is a real no-rule state: row 8e
        needs REVIEW completed, row 10 needs REVIEW settled, and row 7 needs no
        verdict. Verified by enumeration rather than guessed.
        """
        d = decide_next_dispatch(
            _states(REVIEW="pending", DOCS="pending", MERGE="pending"),
            _meta(
                pr_number=555,
                pr_state="OPEN",
                pr_merge_state="UNKNOWN",
                ci_all_passing=None,
                latest_review_verdict=APPROVED_VERDICT,
            ),
            {},
        )
        assert isinstance(d, Blocked), f"expected Blocked, got {d!r}"
        assert "could not resolve mergeability" in d.reason
        assert "GH_REPO" in d.reason


class TestTerminalKillSwitch:
    """Risk 1 mitigation: the guard is switchable without a revert."""

    def test_disabling_the_guard_restores_prior_routing(self, monkeypatch):
        import agent.sdlc_router as router

        monkeypatch.setattr(router, "TERMINAL_GUARD_ENABLED", False)
        d = decide_next_dispatch(_states(MERGE="completed"), _meta(), _ctx())
        assert not isinstance(d, Terminal)
        assert isinstance(d, Dispatch) and d.skill == SKILL_DO_MERGE


class TestTerminalIsObservable:
    """Risk 1 mitigation: a false positive must be greppable, not silent."""

    def test_terminal_decision_names_its_evidence_branch(self):
        by_marker = decide_next_dispatch(_states(MERGE="completed"), _meta(), _ctx())
        by_pr = decide_next_dispatch({}, _meta(pr_state="MERGED", plan_exists=False), {})
        assert by_marker.evidence == "merge_marker"
        assert by_pr.evidence == "merged_pr"
        assert by_marker.reason and by_pr.reason

    def test_terminal_decision_is_logged(self, caplog):
        import logging

        with caplog.at_level(logging.INFO, logger="agent.sdlc_router"):
            decide_next_dispatch(_states(MERGE="completed"), _meta(), _ctx())
        assert any("terminal" in r.message.lower() for r in caplog.records), (
            "terminal decisions must be logged so a false positive is greppable"
        )
