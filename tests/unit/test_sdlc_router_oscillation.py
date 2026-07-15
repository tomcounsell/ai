"""Tests for the Legal Dispatch Guards (G1-G6) and the #1036/#1043 replays."""

from __future__ import annotations

from agent.pipeline_graph import MAX_CRITIQUE_CYCLES
from agent.sdlc_router import (
    MAX_SAME_STAGE_DISPATCHES,
    SKILL_DO_BUILD,
    SKILL_DO_DOCS,
    SKILL_DO_MERGE,
    SKILL_DO_PATCH,
    SKILL_DO_PLAN,
    SKILL_DO_PLAN_CRITIQUE,
    SKILL_DO_PR_REVIEW,
    Blocked,
    Dispatch,
    build_stage_snapshot,
    canonical_snapshot,
    compute_same_stage_count,
    decide_next_dispatch,
    record_dispatch,
)


def test_g1_critique_loop_blocked():
    """G1: NEEDS REVISION + last /do-plan-critique → forced /do-plan."""
    states = {
        "PLAN": "completed",
        "CRITIQUE": "failed",
        "_verdicts": {"CRITIQUE": {"verdict": "NEEDS REVISION"}},
    }
    meta = {
        "latest_critique_verdict": "NEEDS REVISION",
        "last_dispatched_skill": SKILL_DO_PLAN_CRITIQUE,
    }
    result = decide_next_dispatch(states, meta)
    assert isinstance(result, Dispatch)
    assert result.skill == SKILL_DO_PLAN
    assert result.row_id == "G1"


def test_g1_does_not_fire_when_last_skill_was_plan():
    """Sanity: G1 only triggers if the PRIOR dispatch was /do-plan-critique."""
    states = {"PLAN": "completed", "_verdicts": {"CRITIQUE": {"verdict": "NEEDS REVISION"}}}
    meta = {
        "latest_critique_verdict": "NEEDS REVISION",
        "last_dispatched_skill": SKILL_DO_PLAN,
    }
    result = decide_next_dispatch(states, meta)
    # Should fall through to Row 3 (/do-plan), not Guard G1
    assert isinstance(result, Dispatch)
    assert result.row_id != "G1"


def test_g1_fires_for_major_rework_verdict():
    """G1 also triggers on MAJOR REWORK verdict."""
    states = {"PLAN": "completed", "_verdicts": {"CRITIQUE": {"verdict": "MAJOR REWORK"}}}
    meta = {
        "latest_critique_verdict": "MAJOR REWORK",
        "last_dispatched_skill": SKILL_DO_PLAN_CRITIQUE,
    }
    result = decide_next_dispatch(states, meta)
    assert isinstance(result, Dispatch)
    assert result.row_id == "G1"
    assert result.skill == SKILL_DO_PLAN


def test_g2_critique_cycle_cap():
    """G2: critique_cycle_count >= MAX and CRITIQUE not completed → Blocked."""
    states = {"PLAN": "completed", "CRITIQUE": "failed"}
    meta = {"critique_cycle_count": MAX_CRITIQUE_CYCLES}
    result = decide_next_dispatch(states, meta)
    assert isinstance(result, Blocked)
    assert result.guard_id == "G2"
    assert "cycle cap" in result.reason


def test_g2_does_not_fire_when_critique_completed():
    """G2 is silent once CRITIQUE finally succeeds."""
    states = {"PLAN": "completed", "CRITIQUE": "completed"}
    meta = {"critique_cycle_count": MAX_CRITIQUE_CYCLES + 5}
    result = decide_next_dispatch(
        states,
        {**meta, "latest_critique_verdict": "READY TO BUILD (no concerns)"},
    )
    assert isinstance(result, Dispatch)  # not Blocked


def test_g3_pr_lock_routes_to_review_when_no_review_yet():
    """G3: PR open + prior /do-plan dispatch → /do-pr-review."""
    states = {"PLAN": "completed"}
    meta = {"pr_number": 42, "last_dispatched_skill": SKILL_DO_PLAN}
    result = decide_next_dispatch(states, meta)
    assert isinstance(result, Dispatch)
    assert result.row_id == "G3"
    assert result.skill == SKILL_DO_PR_REVIEW


def test_g3_pr_lock_routes_to_patch_on_changes_requested():
    """G3: PR open + review asked for changes → /do-patch."""
    states = {"PLAN": "completed", "REVIEW": "failed"}
    meta = {
        "pr_number": 42,
        "last_dispatched_skill": SKILL_DO_PLAN_CRITIQUE,
        "latest_review_verdict": "CHANGES REQUESTED",
    }
    result = decide_next_dispatch(states, meta)
    assert isinstance(result, Dispatch)
    assert result.row_id == "G3"
    assert result.skill == SKILL_DO_PATCH


def test_g3_pr_lock_routes_to_merge_when_review_and_docs_complete():
    """G3: PR + REVIEW completed + DOCS completed → /do-merge."""
    states = {
        "PLAN": "completed",
        "REVIEW": "completed",
        "DOCS": "completed",
    }
    meta = {"pr_number": 42, "last_dispatched_skill": SKILL_DO_PLAN}
    result = decide_next_dispatch(states, meta)
    assert isinstance(result, Dispatch)
    assert result.row_id == "G3"
    assert result.skill == SKILL_DO_MERGE


def test_g4_oscillation_cap():
    """G4: same_stage_dispatch_count >= MAX → Blocked."""
    result = decide_next_dispatch(
        {},
        {
            "same_stage_dispatch_count": MAX_SAME_STAGE_DISPATCHES,
            "last_dispatched_skill": SKILL_DO_PR_REVIEW,
        },
    )
    assert isinstance(result, Blocked)
    assert result.guard_id == "G4"
    assert "oscillation" in result.reason.lower()


def test_g4_universal_covers_docs_and_merge():
    """G4 applies to every stage — docs and merge included."""
    for skill in (SKILL_DO_DOCS, SKILL_DO_MERGE, SKILL_DO_PATCH):
        result = decide_next_dispatch(
            {},
            {"same_stage_dispatch_count": 3, "last_dispatched_skill": skill},
        )
        assert isinstance(result, Blocked), f"G4 failed to fire for {skill}"
        assert result.guard_id == "G4"


def test_g4_does_not_fire_below_threshold():
    """G4 silent while count < MAX."""
    result = decide_next_dispatch(
        {},
        {
            "same_stage_dispatch_count": MAX_SAME_STAGE_DISPATCHES - 1,
            "last_dispatched_skill": SKILL_DO_PR_REVIEW,
        },
    )
    assert isinstance(result, Dispatch)


def test_g5_artifact_hash_cache_needs_revision():
    """G5: cached NEEDS REVISION verdict + matching plan hash → /do-plan."""
    cached_hash = "sha256:abcd"
    states = {
        "PLAN": "completed",
        "_verdicts": {
            "CRITIQUE": {
                "verdict": "NEEDS REVISION",
                "artifact_hash": cached_hash,
            }
        },
    }
    result = decide_next_dispatch(
        states,
        {"latest_critique_verdict": "NEEDS REVISION"},
        context={"current_plan_hash": cached_hash},
    )
    assert isinstance(result, Dispatch)
    assert result.row_id == "G5"
    assert result.skill == SKILL_DO_PLAN


def test_g5_artifact_hash_cache_ready_to_build():
    """G5: cached READY TO BUILD verdict + matching hash → /do-build."""
    cached_hash = "sha256:abcd"
    states = {
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "_verdicts": {
            "CRITIQUE": {
                "verdict": "READY TO BUILD (no concerns)",
                "artifact_hash": cached_hash,
            }
        },
    }
    result = decide_next_dispatch(
        states,
        {"latest_critique_verdict": "READY TO BUILD (no concerns)"},
        context={"current_plan_hash": cached_hash},
    )
    assert isinstance(result, Dispatch)
    assert result.row_id == "G5"
    assert result.skill == SKILL_DO_BUILD


def test_g5_misses_when_hash_differs():
    """G5 silent when the plan hash has changed."""
    states = {
        "PLAN": "completed",
        "CRITIQUE": "failed",
        "_verdicts": {
            "CRITIQUE": {
                "verdict": "NEEDS REVISION",
                "artifact_hash": "sha256:old",
            }
        },
    }
    result = decide_next_dispatch(
        states,
        {"latest_critique_verdict": "NEEDS REVISION"},
        context={"current_plan_hash": "sha256:new"},
    )
    # With different hash, G5 doesn't fire; without the /do-plan-critique
    # history G1 also silent; falls through to Row 3 (/do-plan).
    assert isinstance(result, Dispatch)
    assert result.row_id != "G5"
    assert result.skill == SKILL_DO_PLAN


def test_g5_does_not_cache_review():
    """G5 does NOT apply to REVIEW — only CRITIQUE."""
    cached_hash = "sha256:abcd"
    states = {
        "PLAN": "completed",
        "REVIEW": "failed",
        "_verdicts": {
            "REVIEW": {
                "verdict": "CHANGES REQUESTED",
                "artifact_hash": cached_hash,
            }
        },
    }
    meta = {
        "pr_number": 99,
        "latest_review_verdict": "CHANGES REQUESTED",
    }
    result = decide_next_dispatch(states, meta, context={"current_plan_hash": cached_hash})
    # G5 does NOT fire; Row 8 should match (review has findings).
    assert isinstance(result, Dispatch)
    assert result.row_id != "G5"


class TestSnapshotAndCounter:
    def test_snapshot_is_insensitive_to_dict_ordering(self):
        a = {"CRITIQUE": "completed", "PLAN": "completed"}
        b = {"PLAN": "completed", "CRITIQUE": "completed"}
        snap_a = build_stage_snapshot(a, meta={"pr_number": 1})
        snap_b = build_stage_snapshot(b, meta={"pr_number": 1})
        assert canonical_snapshot(snap_a) == canonical_snapshot(snap_b)

    def test_snapshot_excludes_timestamps(self):
        # A verdict's recorded_at should not appear in the snapshot projection.
        states = {
            "CRITIQUE": "completed",
            "_verdicts": {
                "CRITIQUE": {
                    "verdict": "READY TO BUILD",
                    "recorded_at": "2026-04-18T00:00:00+00:00",
                }
            },
        }
        snap = build_stage_snapshot(states, meta={})
        assert "recorded_at" not in snap["_verdicts"]["CRITIQUE"]

    def test_record_dispatch_bounds_history(self):
        states: dict = {}
        for i in range(20):
            record_dispatch(states, SKILL_DO_PR_REVIEW)
        # FIFO-bounded to MAX_DISPATCH_HISTORY (10)
        assert len(states["_sdlc_dispatches"]) == 10

    def test_record_dispatch_ignores_stale_pr_number_key(self):
        """#2003 hard cutover: `sdlc-tool meta-set --key pr_number` writes the
        AgentSession.pr_number field (single writer); nothing writes a
        `_pr_number` stage_states key anymore, so record_dispatch must not
        read one. A stale mirrored key is inert -- pr_number comes only from
        the explicit argument."""
        states: dict = {"PLAN": "completed", "_pr_number": 777}
        record_dispatch(states, SKILL_DO_PR_REVIEW)
        snapshot = states["_sdlc_dispatches"][-1]["stage_snapshot"]
        assert snapshot["pr_number"] is None

    def test_record_dispatch_uses_explicit_pr_number_arg(self):
        """The explicit pr_number argument is the sole provenance for the
        snapshot's pr_number field."""
        states: dict = {"PLAN": "completed"}
        record_dispatch(states, SKILL_DO_PR_REVIEW, pr_number=42)
        snapshot = states["_sdlc_dispatches"][-1]["stage_snapshot"]
        assert snapshot["pr_number"] == 42

    def test_compute_same_stage_count_counts_same_skill_runs(self):
        states: dict = {}
        record_dispatch(states, SKILL_DO_PR_REVIEW)
        record_dispatch(states, SKILL_DO_PR_REVIEW)
        record_dispatch(states, SKILL_DO_PR_REVIEW)
        count, skill = compute_same_stage_count(states)
        assert count == 3
        assert skill == SKILL_DO_PR_REVIEW

    def test_compute_same_stage_count_resets_on_skill_change(self):
        states: dict = {}
        record_dispatch(states, SKILL_DO_PR_REVIEW)
        record_dispatch(states, SKILL_DO_PR_REVIEW)
        record_dispatch(states, SKILL_DO_PATCH)
        count, skill = compute_same_stage_count(states)
        assert count == 1
        assert skill == SKILL_DO_PATCH

    def test_d5_count_resets_when_live_snapshot_diverges(self):
        """D5: a live snapshot that diverges from the last dispatch resets the streak to 0."""
        states: dict = {"REVIEW": "in_progress"}
        record_dispatch(states, SKILL_DO_PR_REVIEW)
        record_dispatch(states, SKILL_DO_PR_REVIEW)
        record_dispatch(states, SKILL_DO_PR_REVIEW)
        # Live state has moved on (REVIEW completed since the last dispatch).
        moved = {"REVIEW": "completed"}
        live = build_stage_snapshot(moved, meta={"pr_number": None})
        count, skill = compute_same_stage_count(states, current_snapshot=live)
        assert count == 0
        assert skill == SKILL_DO_PR_REVIEW

    def test_d5_count_increments_when_live_snapshot_matches(self):
        """D5: a matching live snapshot still counts the impending +1 turn."""
        states: dict = {"REVIEW": "in_progress"}
        record_dispatch(states, SKILL_DO_PR_REVIEW)
        record_dispatch(states, SKILL_DO_PR_REVIEW)
        # The impending dispatch is on the SAME state as the last recorded one.
        same = build_stage_snapshot({"REVIEW": "in_progress"}, meta={"pr_number": None})
        count, _ = compute_same_stage_count(states, current_snapshot=same)
        assert count == 3  # 2 recorded + 1 impending

    def test_d5_g4_does_not_fire_after_correction(self):
        """D5: G4 self-clears — a stage correction drops same_stage_dispatch_count to 0."""
        states: dict = {"REVIEW": "in_progress"}
        for _ in range(MAX_SAME_STAGE_DISPATCHES):
            record_dispatch(states, SKILL_DO_PR_REVIEW)
        # Without divergence, the count would be at/above the G4 threshold.
        latched, _ = compute_same_stage_count(states)
        assert latched >= MAX_SAME_STAGE_DISPATCHES
        # After a real transition, the live-snapshot reset zeroes it.
        moved = build_stage_snapshot({"REVIEW": "completed"}, meta={"pr_number": 42})
        cleared, _ = compute_same_stage_count(states, current_snapshot=moved)
        assert cleared == 0


class TestGuardOrdering:
    """Guards fire in G1..G6 order; first match wins."""

    def test_g3_precedence_over_g1_when_pr_open(self):
        # #1932 gap (b2): G1 now steps aside whenever a PR is open (it must
        # never route a NEEDS REVISION critique back to /do-plan once a PR
        # exists), deferring to G3's PR-aware redirect.
        states = {
            "PLAN": "completed",
            "_verdicts": {"CRITIQUE": {"verdict": "NEEDS REVISION"}},
        }
        meta = {
            "pr_number": 99,  # triggers G3
            "latest_critique_verdict": "NEEDS REVISION",
            "last_dispatched_skill": SKILL_DO_PLAN_CRITIQUE,
        }
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.row_id == "G3"
        assert result.skill != SKILL_DO_PLAN

    def test_g1_wins_without_open_pr(self):
        """No-PR regression: G1 still routes to /do-plan when no PR exists."""
        states = {
            "PLAN": "completed",
            "_verdicts": {"CRITIQUE": {"verdict": "NEEDS REVISION"}},
        }
        meta = {
            "pr_number": None,
            "latest_critique_verdict": "NEEDS REVISION",
            "last_dispatched_skill": SKILL_DO_PLAN_CRITIQUE,
        }
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.row_id == "G1"
        assert result.skill == SKILL_DO_PLAN


# ---------------------------------------------------------------------------
# 12-step replay regression test for issue #1036
# ---------------------------------------------------------------------------


def test_1036_replay_terminates():
    """Replay the dispatch sequence from issue #1036 and assert termination.

    Issue #1036 showed the router dispatching /do-plan-critique three times
    on NEEDS REVISION verdicts, three different verdicts on three
    /do-pr-review runs against an unchanged PR, and /do-plan-critique on a
    frozen plan after the PR was already open. With the guards in place the
    router must now:
      - Route NEEDS REVISION back to /do-plan after the first loop (G1).
      - Escalate after 3 same-skill dispatches without state change (G4).
      - Lock out /do-plan / /do-plan-critique once a PR exists (G3).

    The test drives 12 decision turns through ``decide_next_dispatch`` and
    asserts the router never cycles indefinitely — it either terminates in a
    legitimate merge dispatch or in a Blocked escalation, never in a
    repeated /do-plan-critique on NEEDS REVISION.

    Happy-path terminal fixture invariant (#2091): Scenario 4 seeds every stage
    ``completed`` with a PR open AND a recorded ``APPROVED`` review verdict. The
    verdict is load-bearing, not decoration — a ``REVIEW == completed`` marker is
    unwritable without a readable verdict (#2062 WS3c), so the real terminal state
    always carries one. Omitting it makes Row 8e (no-verdict recovery) correctly
    re-dispatch ``/do-pr-review`` instead of ``/do-merge``. Any future edit here
    must keep the verdict so the terminal resolves via Row 10 (ready-to-merge).
    """
    # Scenario 1: NEEDS REVISION loop — second invocation should route to
    # /do-plan, not re-critique.
    states = {
        "PLAN": "completed",
        "CRITIQUE": "failed",
        "_verdicts": {"CRITIQUE": {"verdict": "NEEDS REVISION"}},
    }
    meta = {
        "latest_critique_verdict": "NEEDS REVISION",
        "last_dispatched_skill": SKILL_DO_PLAN_CRITIQUE,
    }
    r1 = decide_next_dispatch(states, meta)
    assert isinstance(r1, Dispatch)
    assert r1.skill == SKILL_DO_PLAN, "G1 must break the critique loop"

    # Scenario 2: non-deterministic review verdicts — the guard cap must
    # escalate after 3 same-skill runs.
    r2 = decide_next_dispatch(
        {},
        {"same_stage_dispatch_count": 3, "last_dispatched_skill": SKILL_DO_PR_REVIEW},
    )
    assert isinstance(r2, Blocked), "G4 must escalate on oscillating review"

    # Scenario 3: after PR is open, the router cannot route to plan-stage
    # skills.
    r3 = decide_next_dispatch(
        {"PLAN": "completed"},
        {"pr_number": 1039, "last_dispatched_skill": SKILL_DO_PLAN_CRITIQUE},
    )
    assert isinstance(r3, Dispatch)
    assert r3.skill != SKILL_DO_PLAN_CRITIQUE
    assert r3.skill != SKILL_DO_PLAN

    # Scenario 4: happy-path termination — all stages complete, PR exists.
    happy = {
        "ISSUE": "completed",
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": "completed",
        "TEST": "completed",
        "REVIEW": "completed",
        "DOCS": "completed",
    }
    # A REVIEW==completed marker is unwritable without a recorded verdict
    # (#2062 WS3c invariant), so the real happy-path terminal always carries an
    # APPROVED review verdict — mirror the replay's final turn below. Without it,
    # Row 8e (no-verdict recovery) correctly re-dispatches /do-pr-review, which is
    # pinned by tests/unit/test_sdlc_router.py::TestRow8eNoVerdictRecovery.
    r4 = decide_next_dispatch(happy, {"pr_number": 1039, "latest_review_verdict": "APPROVED"})
    assert isinstance(r4, Dispatch)
    assert r4.skill == SKILL_DO_MERGE
    assert r4.row_id == "10", "happy-path terminal must resolve via Row 10 (ready-to-merge)"

    # Combined 12-turn replay: drive turns and assert no single skill is
    # dispatched > MAX_SAME_STAGE_DISPATCHES consecutively. The synthetic
    # feed below mirrors #1036's state transitions.
    turns = [
        # Pre-critique
        (_states_with_plan(), {}),
        # First critique → NEEDS REVISION (simulated)
        (
            {
                "PLAN": "completed",
                "CRITIQUE": "failed",
                "_verdicts": {"CRITIQUE": {"verdict": "NEEDS REVISION"}},
            },
            {
                "latest_critique_verdict": "NEEDS REVISION",
                "last_dispatched_skill": SKILL_DO_PLAN_CRITIQUE,
            },
        ),
        # Plan revised → critique again
        (
            {"PLAN": "completed"},
            {"last_dispatched_skill": SKILL_DO_PLAN},
        ),
        # Critique passes with concerns, revision not applied
        (
            {"PLAN": "completed", "CRITIQUE": "completed"},
            {
                "latest_critique_verdict": "READY TO BUILD (with concerns)",
                "revision_applied": False,
            },
        ),
        # Revision applied → build
        (
            {"PLAN": "completed", "CRITIQUE": "completed"},
            {"latest_critique_verdict": "READY TO BUILD (with concerns)", "revision_applied": True},
        ),
        # Build done, tests failing
        (
            {"PLAN": "completed", "CRITIQUE": "completed", "BUILD": "completed", "TEST": "failed"},
            {"pr_number": 1039},
        ),
        # Patch produced, re-test
        (
            {
                "PLAN": "completed",
                "CRITIQUE": "completed",
                "BUILD": "completed",
                "TEST": "failed",
                "PATCH": "completed",
            },
            {"pr_number": 1039, "last_dispatched_skill": SKILL_DO_PATCH},
        ),
        # Tests pass, PR exists, no review
        (
            {
                "PLAN": "completed",
                "CRITIQUE": "completed",
                "BUILD": "completed",
                "TEST": "completed",
            },
            {"pr_number": 1039},
        ),
        # Review approved, docs missing
        (
            {
                "PLAN": "completed",
                "CRITIQUE": "completed",
                "BUILD": "completed",
                "TEST": "completed",
                "REVIEW": "completed",
            },
            {"pr_number": 1039, "latest_review_verdict": "APPROVED"},
        ),
        # Docs complete, ready to merge
        (
            happy,
            {"pr_number": 1039, "latest_review_verdict": "APPROVED"},
        ),
    ]

    dispatched_skills: list[str] = []
    for states, meta in turns:
        result = decide_next_dispatch(states, meta)
        if isinstance(result, Dispatch):
            dispatched_skills.append(result.skill)
        else:
            # A Blocked at any point is an acceptable terminal state.
            break

    # No single skill appears > MAX_SAME_STAGE_DISPATCHES consecutively.
    run = 0
    prev = None
    for s in dispatched_skills:
        if s == prev:
            run += 1
        else:
            run = 1
        assert run <= MAX_SAME_STAGE_DISPATCHES, (
            f"skill {s} dispatched {run} times in a row — guard failed"
        )
        prev = s

    # Final state must not be critique (PR was open mid-sequence).
    if dispatched_skills:
        assert dispatched_skills[-1] in (
            SKILL_DO_MERGE,
            SKILL_DO_DOCS,
            SKILL_DO_PR_REVIEW,
            SKILL_DO_BUILD,
            SKILL_DO_PATCH,
            SKILL_DO_PLAN,
            SKILL_DO_PLAN_CRITIQUE,
        )


# ---------------------------------------------------------------------------
# G6 tests — terminal merge-ready fast-path (issue #1043)
# ---------------------------------------------------------------------------


def _g6_happy_states() -> dict:
    """Seed state for G6 positive tests: all stages through DOCS completed."""
    return {
        "ISSUE": "completed",
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": "completed",
        "TEST": "completed",
        "REVIEW": "completed",
        "DOCS": "completed",
        "_verdicts": {"REVIEW": {"verdict": "APPROVED"}},
    }


def _g6_happy_meta() -> dict:
    """Seed meta for G6 positive tests: CLEAN merge state, CI green."""
    return {
        "pr_number": 264,
        "pr_merge_state": "CLEAN",
        "ci_all_passing": True,
        "latest_review_verdict": "APPROVED",
    }


def test_g6_terminal_merge_ready_fires():
    """G6: CLEAN + CI green + DOCS done + APPROVED verdict → /do-merge with row_id G6."""
    result = decide_next_dispatch(_g6_happy_states(), _g6_happy_meta())
    assert isinstance(result, Dispatch)
    assert result.skill == SKILL_DO_MERGE
    assert result.row_id == "G6"


def test_1043_pr264_8step_terminates():
    """Replay the PR #264 8-step incident: router must dispatch /do-merge, not /do-pr-review.

    Issue #1043 showed /sdlc dispatching /do-pr-review eight times on a
    merge-ready PR. With G6 in place the router must immediately route to
    /do-merge when all stages are done, CI is green, and the review is APPROVED.
    """
    result = decide_next_dispatch(_g6_happy_states(), _g6_happy_meta())
    assert isinstance(result, Dispatch)
    assert result.skill == SKILL_DO_MERGE
    assert result.row_id == "G6"


def test_g6_does_not_fire_when_docs_not_done():
    """G6 must not dispatch /do-merge if DOCS stage is not completed."""
    states = {
        "ISSUE": "completed",
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": "completed",
        "TEST": "completed",
        "REVIEW": "completed",
        # DOCS intentionally absent — not completed
        "_verdicts": {"REVIEW": {"verdict": "APPROVED"}},
    }
    result = decide_next_dispatch(states, _g6_happy_meta())
    # G6 must NOT fire — should route to /do-docs (Row 9) instead
    assert isinstance(result, Dispatch)
    assert result.skill != SKILL_DO_MERGE
    assert result.row_id != "G6"


def test_g6_does_not_fire_without_pr_number():
    """G6 is silent when no PR exists."""
    meta = {k: v for k, v in _g6_happy_meta().items() if k != "pr_number"}
    result = decide_next_dispatch(_g6_happy_states(), meta)
    # G6 must not fire — result may be Dispatch (row 10) or Blocked
    if isinstance(result, Dispatch):
        assert result.row_id != "G6"
    # Blocked is also acceptable (no pr_number + all stages done = ambiguous state)


def test_g6_does_not_fire_when_pr_not_clean():
    """G6 is silent when mergeStateStatus is not CLEAN."""
    meta = {**_g6_happy_meta(), "pr_merge_state": "BLOCKED"}
    result = decide_next_dispatch(_g6_happy_states(), meta)
    # Should not route to /do-merge via G6
    assert isinstance(result, (Dispatch, Blocked))
    if isinstance(result, Dispatch):
        assert result.row_id != "G6"


def test_g6_does_not_fire_when_ci_not_passing():
    """G6 is silent when CI is not fully passing."""
    meta = {**_g6_happy_meta(), "ci_all_passing": False}
    result = decide_next_dispatch(_g6_happy_states(), meta)
    assert isinstance(result, (Dispatch, Blocked))
    if isinstance(result, Dispatch):
        assert result.row_id != "G6"


def test_g6_fires_when_verdict_in_meta_not_stage_states():
    """G6 fires when approved verdict is in meta.latest_review_verdict even if _verdicts absent.

    Real-world sdlc-tool stage-query returns the review verdict in _meta.latest_review_verdict
    (not in stage_states._verdicts). G6 must read meta first to handle this case.
    """
    states = {k: v for k, v in _g6_happy_states().items() if k != "_verdicts"}
    result = decide_next_dispatch(states, _g6_happy_meta())
    assert isinstance(result, Dispatch)
    assert result.skill == SKILL_DO_MERGE
    assert result.row_id == "G6"


def test_g6_does_not_fire_when_review_verdict_missing_from_both():
    """G6 is silent when review verdict is absent from both meta and _verdicts."""
    states = {k: v for k, v in _g6_happy_states().items() if k != "_verdicts"}
    meta = {k: v for k, v in _g6_happy_meta().items() if k != "latest_review_verdict"}
    result = decide_next_dispatch(states, meta)
    assert isinstance(result, (Dispatch, Blocked))
    if isinstance(result, Dispatch):
        assert result.row_id != "G6"


def test_g6_does_not_fire_when_review_verdict_is_changes_requested():
    """G6 is silent when review verdict is CHANGES REQUESTED (in both meta and _verdicts)."""
    states = {**_g6_happy_states(), "_verdicts": {"REVIEW": {"verdict": "CHANGES REQUESTED"}}}
    meta = {**_g6_happy_meta(), "latest_review_verdict": "CHANGES REQUESTED"}
    result = decide_next_dispatch(states, meta)
    assert isinstance(result, (Dispatch, Blocked))
    if isinstance(result, Dispatch):
        assert result.row_id != "G6"


def test_g6_does_not_fire_when_ci_all_passing_is_none():
    """G6 is silent when ci_all_passing is None (gh CLI failure)."""
    meta = {**_g6_happy_meta(), "ci_all_passing": None}
    result = decide_next_dispatch(_g6_happy_states(), meta)
    assert isinstance(result, (Dispatch, Blocked))
    if isinstance(result, Dispatch):
        assert result.row_id != "G6"


def test_g6_does_not_fire_when_pr_merge_state_is_none():
    """G6 is silent when pr_merge_state is None (gh CLI failure)."""
    meta = {**_g6_happy_meta(), "pr_merge_state": None}
    result = decide_next_dispatch(_g6_happy_states(), meta)
    assert isinstance(result, (Dispatch, Blocked))
    if isinstance(result, Dispatch):
        assert result.row_id != "G6"


def _states_with_plan() -> dict:
    return {
        "ISSUE": "completed",
        "PLAN": "completed",
        "CRITIQUE": "pending",
    }


# ---------------------------------------------------------------------------
# G8 (#1267): artifact verification consumer, positioned in GUARDS after G4.
# ---------------------------------------------------------------------------
#
# g8 never makes a live call itself -- it only consumes
# context["stage_artifacts_verified"] / context["unverified_stage"], set by
# the next-skill context-assembly path (tools/sdlc_next_skill.py, see
# tests/unit/test_sdlc_next_skill.py::TestStageArtifactVerification). These
# tests drive evaluate_guards()/decide_next_dispatch() directly with a
# synthetic context, exercising the router-side guard-ordering contract in
# isolation from the live gh/git calls.


def test_g8_redispatches_same_stage_on_verified_mismatch():
    """g8: stage_artifacts_verified=False + unverified_stage='BUILD' →
    re-dispatch /do-build (the BUILD stage's own skill), row_id='G8'."""
    result = decide_next_dispatch(
        {"BUILD": "completed"},
        {"same_stage_dispatch_count": 0, "last_dispatched_skill": SKILL_DO_BUILD},
        context={"stage_artifacts_verified": False, "unverified_stage": "BUILD"},
    )
    assert isinstance(result, Dispatch)
    assert result.row_id == "G8"
    assert result.skill == SKILL_DO_BUILD


def test_g8_silent_when_verified_true():
    """g8 does not fire when stage_artifacts_verified is True (verified clean)."""
    result = decide_next_dispatch(
        {"BUILD": "completed"},
        {"same_stage_dispatch_count": 0, "last_dispatched_skill": SKILL_DO_BUILD},
        context={"stage_artifacts_verified": True, "unverified_stage": None},
    )
    assert isinstance(result, Dispatch)
    assert result.row_id != "G8"


def test_g8_silent_when_flag_absent():
    """g8 does not fire when the context carries no verification flags at
    all -- the no-claimed-artifact no-op contract from context assembly."""
    result = decide_next_dispatch(
        {"BUILD": "completed"},
        {"same_stage_dispatch_count": 0, "last_dispatched_skill": SKILL_DO_BUILD},
        context={},
    )
    assert isinstance(result, Dispatch)
    assert result.row_id != "G8"


def test_g8_existing_g5_g7_cases_stay_green_without_verification_flag():
    """Existing G5/G7-adjacent dispatch cases are unaffected when no
    verification context is supplied at all (the common CLI-path shape
    before #1267's context.update() runs)."""
    cached_hash = "sha256:g8-regression"
    states = {
        "PLAN": "completed",
        "_verdicts": {
            "CRITIQUE": {
                "verdict": "NEEDS REVISION",
                "artifact_hash": cached_hash,
            }
        },
    }
    result = decide_next_dispatch(
        states,
        {"latest_critique_verdict": "NEEDS REVISION"},
        context={"current_plan_hash": cached_hash},
    )
    assert isinstance(result, Dispatch)
    assert result.row_id == "G5"
    assert result.skill == SKILL_DO_PLAN


def test_g4_fires_before_g8_on_persistently_false_claim():
    """The G4 cap bounds verification-driven re-dispatches (#1267 Concern 3).

    With stage_artifacts_verified=False AND
    same_stage_dispatch_count >= MAX_SAME_STAGE_DISPATCHES, G4 (positioned
    before g8 in GUARDS) must fire FIRST and return Blocked -- a
    persistently false claim escalates to a human rather than g8
    re-dispatching the same stage forever.
    """
    result = decide_next_dispatch(
        {"BUILD": "completed"},
        {
            "same_stage_dispatch_count": MAX_SAME_STAGE_DISPATCHES,
            "last_dispatched_skill": SKILL_DO_BUILD,
        },
        context={"stage_artifacts_verified": False, "unverified_stage": "BUILD"},
    )
    assert isinstance(result, Blocked)
    assert result.guard_id == "G4"


def test_g4_does_not_yet_block_below_cap_so_g8_can_redispatch():
    """Below the G4 cap, a verified-false claim reaches g8 and re-dispatches
    (the bounded, non-immediate-Block phase-1 policy: silent re-dispatch,
    then escalate via the existing G4 cap once the cap is reached)."""
    result = decide_next_dispatch(
        {"BUILD": "completed"},
        {
            "same_stage_dispatch_count": MAX_SAME_STAGE_DISPATCHES - 1,
            "last_dispatched_skill": SKILL_DO_BUILD,
        },
        context={"stage_artifacts_verified": False, "unverified_stage": "BUILD"},
    )
    assert isinstance(result, Dispatch)
    assert result.row_id == "G8"
    assert result.skill == SKILL_DO_BUILD


def test_g8_maps_patch_and_plan_stages_to_their_own_skills():
    """g8 re-dispatches whatever stage was flagged, not just BUILD --
    PATCH and PLAN map to their own skills per STAGE_TO_SKILL."""
    for stage, expected_skill in (("PATCH", SKILL_DO_PATCH), ("PLAN", SKILL_DO_PLAN)):
        result = decide_next_dispatch(
            {stage: "completed"},
            {"same_stage_dispatch_count": 0, "last_dispatched_skill": expected_skill},
            context={"stage_artifacts_verified": False, "unverified_stage": stage},
        )
        assert isinstance(result, Dispatch), f"g8 failed to fire for {stage}"
        assert result.row_id == "G8"
        assert result.skill == expected_skill


def test_g8_noop_on_malformed_unverified_stage():
    """A mismatch flagged with an unmappable/garbage unverified_stage value
    fails open (no dispatch decision from g8) rather than guessing a
    re-dispatch target."""
    result = decide_next_dispatch(
        {"BUILD": "completed"},
        {"same_stage_dispatch_count": 0, "last_dispatched_skill": SKILL_DO_BUILD},
        context={"stage_artifacts_verified": False, "unverified_stage": "NOT_A_REAL_STAGE"},
    )
    # Falls through to normal dispatch table (g8 itself does not fire).
    if isinstance(result, Dispatch):
        assert result.row_id != "G8"
