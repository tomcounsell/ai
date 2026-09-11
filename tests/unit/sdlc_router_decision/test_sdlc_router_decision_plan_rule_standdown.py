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

from agent import sdlc_router
from agent.sdlc_router import (
    MAX_CONCERN_RECRITIQUE_ROUNDS,
    NO_RULE_GUARD_ID,
    SKILL_DO_BUILD,
    SKILL_DO_DOCS,
    SKILL_DO_MERGE,
    SKILL_DO_PATCH,
    SKILL_DO_PLAN,
    SKILL_DO_PLAN_CRITIQUE,
    SKILL_DO_PR_REVIEW,
    Blocked,
    _review_verdict_head_is_stale,
    _rule_critique_ready_no_concerns,
    _rule_critique_ready_with_concerns_no_revision,
    _rule_critique_ready_with_concerns_revision_applied,
    _rule_review_verdict_head_stale,
    decide_next_dispatch,
    guard_g3_pr_lock,
    guard_g6_terminal_merge_ready,
)

_PLAN_HASH = "sha256:cafe"
_REVISED_PLAN_HASH = "sha256:beef"
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


# ---------------------------------------------------------------------------
# #3249 — the plan-stage stand-down sweep (rows 1/2/2c/3 + the 4b/4c fold)
# ---------------------------------------------------------------------------


def _sweep_states(plan: str = "completed", critique: str = "pending", build: str = "pending"):
    """Verdict-free stage states for the stand-down sweep probes.

    ``_verdicts`` is empty so G5 (which reads the CRITIQUE record directly)
    and every verdict-keyed row stay out of the way; the probes pin the
    marker-keyed rows 1/2/2c, which answer on stage status alone.
    """
    return {
        "ISSUE": "completed",
        "PLAN": plan,
        "CRITIQUE": critique,
        "BUILD": build,
        "TEST": "pending",
        "PATCH": "pending",
        "REVIEW": "pending",
        "DOCS": "pending",
        "MERGE": "pending",
        "_verdicts": {},
    }


def _sweep_meta(**extra) -> dict:
    """Meta for the stand-down sweep probes."""
    meta = {
        "pr_number": None,
        "same_stage_dispatch_count": 0,
        "last_dispatched_skill": None,
    }
    meta.update(extra)
    return meta


def _needs_revision_states(build_status: str = "pending") -> dict:
    """A recorded NEEDS REVISION verdict, stamped with the plan hash it judged."""
    states = _sweep_states(plan="completed", critique="completed", build=build_status)
    states["_verdicts"]["CRITIQUE"] = {
        "verdict": "NEEDS REVISION",
        "recorded_at": "2026-09-07T02:00:00",
        "artifact_hash": _PLAN_HASH,
    }
    return states


def _stale_needs_revision_states(build_status: str = "pending") -> dict:
    """A NEEDS REVISION verdict that a later /do-plan revision made stale.

    Row 2b's other input shape: the dispatch history carries a /do-plan entry
    timestamped after the verdict, so the verdict predates the plan it would
    judge and row 2b (not row 3) owns the state.
    """
    states = _needs_revision_states(build_status=build_status)
    states["_sdlc_dispatches"] = [{"skill": SKILL_DO_PLAN, "at": "2026-09-07T04:00:00"}]
    return states


def _revised_plan_context(branch_exists: bool) -> dict:
    """A context whose plan hash differs from the recorded verdict's hash.

    Load-bearing for every row-3 probe (T5, T6): row 3 sits behind
    ``guard_g5_artifact_hash_cache``, which short-circuits whenever the
    verdict's ``artifact_hash`` equals ``context["current_plan_hash"]`` and
    answers with the cached verdict itself. On a matching hash the probe
    returns ``Dispatch('/do-plan', row_id='G5')`` on BOTH sides of the fix —
    green before and after, proving nothing. A deliberately different hash
    makes G5 step aside so row 3 is the row under test.
    """
    return {"current_plan_hash": _REVISED_PLAN_HASH, "branch_exists": branch_exists}


class TestPlanRowsStandDownSweep:
    """#3249: plan-stage rows must not answer for a lane that left the plan stage.

    Row 1 carried only the ``pr_number`` step-aside, row 2 carried none, rows
    2c and 3 only the ``pr_number`` one. After the sweep each also stands down
    on a started/completed BUILD, landing on row 5 (pre-PR) or row 7
    (post-PR) — the rows that own a lane that is past the plan stage.
    """

    def test_t1_row_one_stands_down_once_build_in_progress(self):
        """T1: crashed-PLAN recovery must not pull a running BUILD back to /do-plan."""
        states = _sweep_states(plan="in_progress", critique="pending", build="in_progress")
        meta = _sweep_meta(issue_number=3249)
        result = decide_next_dispatch(states, meta, _plan_context(branch_exists=True))
        assert result.skill == SKILL_DO_BUILD
        assert result.row_id == "5"

    def test_t2_row_two_stands_down_once_pr_open(self):
        """T2: an open PR with CRITIQUE pending belongs to review, not critique."""
        states = _sweep_states(plan="completed", critique="pending", build="pending")
        meta = _sweep_meta(pr_number=999, last_dispatched_skill=SKILL_DO_BUILD)
        result = decide_next_dispatch(states, meta, _plan_context(branch_exists=True))
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "7"

    def test_t3_row_two_stands_down_once_build_in_progress(self):
        """T3: no PR yet and the build already running -> row 5 resumes it."""
        states = _sweep_states(plan="completed", critique="pending", build="in_progress")
        meta = _sweep_meta(last_dispatched_skill=SKILL_DO_BUILD)
        result = decide_next_dispatch(states, meta, _plan_context(branch_exists=True))
        assert result.skill == SKILL_DO_BUILD
        assert result.row_id == "5"

    def test_t4_row_2c_stands_down_once_build_in_progress(self):
        """T4: a stalled critique must not outrank a build that already started."""
        states = _sweep_states(plan="completed", critique="in_progress", build="in_progress")
        result = decide_next_dispatch(states, _sweep_meta(), _plan_context(branch_exists=True))
        assert result.skill == SKILL_DO_BUILD
        assert result.row_id == "5"

    def test_t5_row_three_stands_down_once_build_in_progress(self):
        """T5: a NEEDS REVISION verdict must not send a running build back to /do-plan.

        The context hash deliberately differs from the verdict's
        ``artifact_hash`` (see ``_revised_plan_context``) — on a matching hash
        G5 answers instead and the probe is green on both sides of the fix.
        """
        states = _needs_revision_states(build_status="in_progress")
        meta = _sweep_meta(
            last_dispatched_skill=SKILL_DO_BUILD, latest_critique_verdict="NEEDS REVISION"
        )
        result = decide_next_dispatch(states, meta, _revised_plan_context(branch_exists=True))
        assert result.skill == SKILL_DO_BUILD
        assert result.row_id == "5"


class TestRow2bSharedHelperConversion:
    """T5b/T5c: row 2b's inline stand-down pair becomes the shared helper, unchanged.

    Row 2b already carries the exact condition the sweep expresses once; the
    conversion is behavior-identical by construction. Both sides of the
    boundary are pinned so the conversion cannot move them.
    """

    def test_t5b_row_2b_stands_down_once_build_in_progress(self):
        """T5b: the #3195 shape still lands on row 5 after the conversion."""
        result = decide_next_dispatch(
            _build_in_progress_states(),
            _armed_concern_meta(),
            _plan_context(branch_exists=True),
        )
        assert result.skill == SKILL_DO_BUILD
        assert result.row_id == "5"

    def test_t5c_row_2b_still_recritiques_with_build_pending(self):
        """T5c: negative control — below the bound, row 2b keeps firing."""
        result = decide_next_dispatch(
            _build_in_progress_states(build_status="pending"),
            _armed_concern_meta(),
            _plan_context(branch_exists=False),
        )
        assert result.skill == SKILL_DO_PLAN_CRITIQUE
        assert result.row_id == "2b"


class TestSweepNegativeControls:
    """T6: the sweep must not disable the rows it stands down.

    With BUILD pending and no PR the lane is genuinely still in the plan
    stage, so every swept row keeps its own answer. Proven per row — a
    stand-down bug would strand each row's state somewhere different. The
    row-3 leg needs the different-hash setup (see ``_revised_plan_context``);
    the others are reached without touching the hash.
    """

    def test_t6_row_one_still_dispatches_plan(self):
        states = _sweep_states(plan="in_progress", critique="pending", build="pending")
        result = decide_next_dispatch(
            states, _sweep_meta(issue_number=3249), _plan_context(branch_exists=False)
        )
        assert result.skill == SKILL_DO_PLAN
        assert result.row_id == "1"

    def test_t6_row_two_still_dispatches_critique(self):
        states = _sweep_states(plan="completed", critique="pending", build="pending")
        result = decide_next_dispatch(states, _sweep_meta(), _plan_context(branch_exists=False))
        assert result.skill == SKILL_DO_PLAN_CRITIQUE
        assert result.row_id == "2"

    def test_t6_row_2b_still_dispatches_critique(self):
        """The stale-NEEDS-REVISION shape — row 2b's own reason to exist."""
        states = _stale_needs_revision_states(build_status="pending")
        meta = _sweep_meta(latest_critique_verdict="NEEDS REVISION")
        result = decide_next_dispatch(states, meta, _revised_plan_context(branch_exists=False))
        assert result.skill == SKILL_DO_PLAN_CRITIQUE
        assert result.row_id == "2b"

    def test_t6_row_2c_still_dispatches_critique(self):
        states = _sweep_states(plan="completed", critique="in_progress", build="pending")
        result = decide_next_dispatch(states, _sweep_meta(), _plan_context(branch_exists=False))
        assert result.skill == SKILL_DO_PLAN_CRITIQUE
        assert result.row_id == "2c"

    def test_t6_row_three_still_dispatches_plan(self):
        states = _needs_revision_states(build_status="pending")
        meta = _sweep_meta(latest_critique_verdict="NEEDS REVISION")
        result = decide_next_dispatch(states, meta, _revised_plan_context(branch_exists=False))
        assert result.skill == SKILL_DO_PLAN
        assert result.row_id == "3"


def _concerns_row_meta(**extra) -> dict:
    """Meta for the rows-4b/4c controls: a with-concerns verdict, state S1.

    S1 — no revision landed since the verdict — is row 4b's own state; the
    row-4c legs override to S2 and spend the concern bound so only the gate
    under test can decline.
    """
    meta = {
        "latest_critique_verdict": "READY TO BUILD (with concerns)",
        "pr_number": None,
        "same_stage_dispatch_count": 0,
        "last_dispatched_skill": None,
    }
    meta.update(extra)
    return meta


class TestConcernsRowsFoldUnchanged:
    """T7/T8: folding rows 4b/4c's duplicated ``pr_number`` checks changes nothing.

    Rows 4b and 4c each test ``meta.get("pr_number")`` twice today; the sweep
    folds the leading check into the shared stand-down helper and deletes the
    redundant second one. The helper is broader — it also stands down on a
    started/completed BUILD — but both rows' trailing
    ``build_status in (None, "pending", "ready")`` gates already exclude that
    input, so no state changes answer. Pinned at the predicate, per row: an
    end-to-end routing assertion could pass with the step-aside merely moved
    rather than deleted.
    """

    def test_t7_row_4b_refuses_open_pr_and_completed_build(self):
        """T7: the two states 4b already refuses, still refused after the fold."""
        context = _plan_context(branch_exists=False)
        assert (
            _rule_critique_ready_with_concerns_no_revision(
                _build_in_progress_states(build_status="pending"),
                _concerns_row_meta(pr_number=4242),
                context,
            )
            is False
        )
        assert (
            _rule_critique_ready_with_concerns_no_revision(
                _build_in_progress_states(build_status="completed"),
                _concerns_row_meta(),
                context,
            )
            is False
        )

    def test_t7_row_4c_refuses_open_pr_and_completed_build(self):
        """T7: the same two states, on the bound-spent S2 shape row 4c owns."""
        spent = _concerns_row_meta(
            revision_applied=True,
            revision_applied_at="2026-09-07T03:00:00",
            concern_round_count=MAX_CONCERN_RECRITIQUE_ROUNDS,
        )
        context = _plan_context(branch_exists=False)
        assert (
            _rule_critique_ready_with_concerns_revision_applied(
                _build_in_progress_states(build_status="pending"),
                dict(spent, pr_number=4242),
                context,
            )
            is False
        )
        assert (
            _rule_critique_ready_with_concerns_revision_applied(
                _build_in_progress_states(build_status="completed"),
                spent,
                context,
            )
            is False
        )

    def test_t8_failed_build_declines_rows_4a_4b_and_4c(self):
        """T8: BUILD==failed declines on the narrow build gate, not the helper.

        Each leg's verdict gates pass so only the trailing
        ``build_status in (None, "pending", "ready")`` gate can produce the
        decline — 4a with a clean READY TO BUILD verdict, 4b/4c with the
        with-concerns one. Row 4b is asserted explicitly, not inferred from
        its siblings.
        """
        context = _plan_context(branch_exists=False)
        clean = _build_in_progress_states(build_status="failed")
        clean["_verdicts"]["CRITIQUE"]["verdict"] = "READY TO BUILD"
        assert (
            _rule_critique_ready_no_concerns(
                clean, _concerns_row_meta(latest_critique_verdict="READY TO BUILD"), context
            )
            is False
        )
        concerns = _build_in_progress_states(build_status="failed")
        assert (
            _rule_critique_ready_with_concerns_no_revision(concerns, _concerns_row_meta(), context)
            is False
        )
        spent = _concerns_row_meta(
            revision_applied=True,
            revision_applied_at="2026-09-07T03:00:00",
            concern_round_count=MAX_CONCERN_RECRITIQUE_ROUNDS,
        )
        assert (
            _rule_critique_ready_with_concerns_revision_applied(concerns, spent, context) is False
        )


# ---------------------------------------------------------------------------
# #3260 — a terminal /do-merge requires a verified-fresh review head
# ---------------------------------------------------------------------------


def _g3_meta(**extra) -> dict:
    """Meta for the G3 probes: plan-family last dispatch, DIRTY merge state.

    G3-probe hygiene: a plan-family ``last_dispatched_skill`` so G3 engages,
    and ``pr_merge_state="DIRTY"`` so G6 cannot answer first — every G3 test
    must be provably answered by G3's own ladder, not by the merge fast-path.
    """
    return _approved_pr_meta(pr_merge_state="DIRTY", **extra)


def _g6_meta(**extra) -> dict:
    """Meta for the G6/row-10 probes: every G6 gate green, non-plan dispatch.

    G6-probe hygiene: ``pr_merge_state="CLEAN"`` and CI passing so the only
    thing that can stop G6 is the head evidence, and a non-plan
    ``last_dispatched_skill`` so G3 stays out of the way. Only the G6/row-10
    probes may carry ``pr_merge_state="CLEAN"``.
    """
    return _approved_pr_meta(
        last_dispatched_skill=SKILL_DO_PR_REVIEW,
        pr_merge_state="CLEAN",
        ci_all_passing=True,
        **extra,
    )


class TestG3MergeLegRequiresApprovedFreshVerdict:
    """T9-T13 (#3260): G3's merge leg must consult the verdict, not just the markers.

    G3 leg 1 dispatches /do-merge on ``REVIEW completed AND DOCS completed``
    alone; the APPROVED verdict computed beside it and the head freshness are
    never consulted, so the most-taken leg is the only one that skips both.
    """

    def test_t9_changes_requested_routes_to_patch_not_merge(self):
        """T9: a CHANGES REQUESTED verdict at the live head patches; leg 1 merges."""
        states = _approved_pr_states(docs="completed")
        states["_verdicts"]["REVIEW"]["verdict"] = "CHANGES REQUESTED"
        meta = _g3_meta(latest_review_verdict="CHANGES REQUESTED")
        result = decide_next_dispatch(states, meta, {"pr_head_sha": _HEAD})
        assert result.skill == SKILL_DO_PATCH
        assert result.row_id == "G3"

    def test_t10_stale_approval_routes_to_re_review_not_merge(self):
        """T10: an APPROVED recorded against an older head is not a licence to merge."""
        states = _approved_pr_states(docs="completed", head_sha=_OTHER_HEAD)
        meta = _g3_meta(latest_review_head_sha=_OTHER_HEAD)
        result = decide_next_dispatch(states, meta, {"pr_head_sha": _HEAD})
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "G3"

    def test_t11_absent_head_key_routes_to_re_review_not_merge(self):
        """T11: an ABSENT pr_head_sha with DOCS complete falls to leg 4, never leg 1.

        The expected answer is NOT /do-docs either: DOCS is already completed
        here, so without leg 3's DOCS clause the fall-through would dispatch
        /do-docs for a stage that is already done.
        """
        result = decide_next_dispatch(_approved_pr_states(docs="completed"), _g3_meta(), {})
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "G3"

    def test_t11b_docs_pending_at_live_head_still_routes_to_docs(self):
        """T11b: negative control — leg 3 keeps answering its own state."""
        result = decide_next_dispatch(_approved_pr_states(), _g3_meta(), {"pr_head_sha": _HEAD})
        assert result.skill == SKILL_DO_DOCS
        assert result.row_id == "G3"

    def test_t12_empty_sentinel_routes_to_re_review_not_merge(self):
        """T12: the fail-closed lookup-failure sentinel is not fresh evidence."""
        context = {"pr_head_sha": "", "pr_head_sha_lookup_failed": True}
        result = decide_next_dispatch(_approved_pr_states(docs="completed"), _g3_meta(), context)
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "G3"

    def test_t13_live_head_approval_still_merges(self):
        """T13: positive control — a fresh APPROVED at the live head still merges.

        Unlike ``TestG3DocsLeg::test_docs_completed_still_merges`` this probe
        supplies the live head in context, so it remains the merge positive
        control once leg 1 requires verified freshness.
        """
        states = _approved_pr_states(docs="completed")
        result = decide_next_dispatch(states, _g3_meta(), {"pr_head_sha": _HEAD})
        assert result.skill == SKILL_DO_MERGE
        assert result.row_id == "G3"


class TestTerminalMergeSitesRequireVerifiedFreshHead:
    """T14-T16b (#3260): a terminal /do-merge needs positive freshness evidence.

    An ABSENT ``pr_head_sha`` is not evidence of freshness. G6 and row 10 —
    the two terminal merge sites besides G3 leg 1 — both fail open on it
    today: each consults the inert staleness predicate, which reads "not
    stale". After the widening both decline, and because row 8f is inert on
    an absent key, row 9 declines on DOCS completed, and row 10 is the last
    rule, no rule owns the state: ``decide_next_dispatch`` escalates to
    ``Blocked(guard_id='NO_RULE')`` — fail-closed by design. Pinned by
    guard_id, never by asserting "not /do-merge": a relocated hole would
    still return /do-merge.
    """

    def test_t14_g6_and_row_ten_absent_key_escalates(self):
        """T14: end to end — G6 declines AND the decision is Blocked(NO_RULE)."""
        states = _approved_pr_states(docs="completed")
        meta = _g6_meta()
        context = {}  # pr_head_sha ABSENT — the #3260 hole
        assert guard_g6_terminal_merge_ready(states, meta, context) is None
        result = decide_next_dispatch(states, meta, context)
        assert isinstance(result, Blocked), f"expected Blocked(NO_RULE), got {result!r}"
        assert result.guard_id == NO_RULE_GUARD_ID

    def test_t14b_row_ten_in_isolation_absent_key_escalates(self, monkeypatch):
        """T14b: with G6 out of the guard list, row 10 must still decline.

        Pre-fix this probe answers ``Dispatch('/do-merge', row_id='10')`` —
        that RED is the proof the hole was closed at row 10 itself rather
        than relocated from G6. And a RED captured on the stale-key input
        would prove nothing: this probe is the ABSENT key specifically.
        """
        monkeypatch.setattr(
            sdlc_router,
            "GUARDS",
            [g for g in sdlc_router.GUARDS if g is not sdlc_router.guard_g6_terminal_merge_ready],
        )
        result = decide_next_dispatch(_approved_pr_states(docs="completed"), _g6_meta(), {})
        assert isinstance(result, Blocked), f"expected Blocked(NO_RULE), got {result!r}"
        assert result.guard_id == NO_RULE_GUARD_ID

    def test_t15_fresh_head_still_fast_paths_to_merge(self):
        """T15: positive control — a matching live head keeps the G6 fast-path."""
        states = _approved_pr_states(docs="completed")
        meta = _g6_meta()
        context = {"pr_head_sha": _HEAD}
        assert guard_g6_terminal_merge_ready(states, meta, context) is not None
        result = decide_next_dispatch(states, meta, context)
        assert result.skill == SKILL_DO_MERGE
        assert result.row_id == "G6"

    def test_t16_stale_head_declines_g6_and_lands_on_row_8f(self):
        """T16: stale-key control — the pre-existing G6 decline, pinned."""
        states = _approved_pr_states(docs="completed", head_sha=_OTHER_HEAD)
        meta = _g6_meta(latest_review_head_sha=_OTHER_HEAD)
        context = {"pr_head_sha": _HEAD}
        assert guard_g6_terminal_merge_ready(states, meta, context) is None
        result = decide_next_dispatch(states, meta, context)
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "8f"

    def test_t16b_empty_sentinel_lands_on_row_8f(self):
        """T16b: the empty sentinel keeps landing on 8f; only the ABSENT key escalates."""
        context = {"pr_head_sha": "", "pr_head_sha_lookup_failed": True}
        result = decide_next_dispatch(_approved_pr_states(docs="completed"), _g6_meta(), context)
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "8f"


class TestInertFreshnessSurfacesUnchanged:
    """T17/T18: the inert-on-absent-key reading survives where it is correct.

    ``_review_verdict_head_is_stale`` keeps its documented contract — False on
    an absent key — and row 8f, which dispatches /do-pr-review rather than a
    merge, keeps calling it. Only the three TERMINAL /do-merge sites take the
    strict predicate; the split is by dispatch terminality.
    """

    def test_t17_stale_predicate_still_inert_on_absent_key(self):
        states = _approved_pr_states(docs="completed")
        assert _review_verdict_head_is_stale(states, _g6_meta(), {}) is False

    def test_t18_row_8f_still_inert_on_absent_key(self):
        states = _approved_pr_states(docs="completed")
        assert _rule_review_verdict_head_stale(states, _g6_meta(), {}) is False
