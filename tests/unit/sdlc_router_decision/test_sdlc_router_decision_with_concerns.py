"""With-concerns re-critique gate tests for agent.sdlc_router.decide_next_dispatch() (#2879)."""

from __future__ import annotations

from agent.sdlc_router import (
    DISPATCH_RULES,
    MAX_CONCERN_RECRITIQUE_ROUNDS,
    MAX_SAME_STAGE_DISPATCHES,
    SKILL_DO_BUILD,
    SKILL_DO_PLAN,
    SKILL_DO_PLAN_CRITIQUE,
    Dispatch,
    _concern_revision_is_unjudged,
    _critique_verdict_is_stale,
    decide_next_dispatch,
)

# ---------------------------------------------------------------------------
# #2787: with-concerns re-critique gate.
#
# The must-pass gate set from the plan's task 10a. The G5 alive states are the
# load-bearing ones: guards run to completion BEFORE the dispatch table, and
# `CRITIQUE_READY_TO_BUILD in verdict_text` matches "READY TO BUILD (WITH
# CONCERNS)" too, so without G5's step-aside every row below is unreachable in
# production while still passing its own unit tests.
# ---------------------------------------------------------------------------


def _iso(ts: str) -> str:
    """Return an ISO-8601 timestamp string (thin wrapper for readability)."""
    return ts


_WC = "READY TO BUILD (with concerns)"
_PLAN_HASH = "sha256:cafe"


def _wc_states(recorded_at: str, plan_dispatch_at: str | None = None) -> dict:
    """Ledger with a with-concerns CRITIQUE verdict whose hash matches the plan."""
    states = {
        "ISSUE": "completed",
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": "pending",
        "_verdicts": {
            "CRITIQUE": {
                "verdict": "READY TO BUILD (WITH CONCERNS)",
                "recorded_at": recorded_at,
                "artifact_hash": _PLAN_HASH,
            }
        },
    }
    if plan_dispatch_at:
        states["_sdlc_dispatches"] = [
            {"skill": SKILL_DO_PLAN, "at": plan_dispatch_at, "stage": "PLAN"}
        ]
    return states


def _wc_meta(revision_applied_at: str | None, count: int = 0, **extra) -> dict:
    meta = {
        "latest_critique_verdict": _WC,
        "revision_applied_at": revision_applied_at,
        "concern_round_count": count,
        # Sticky and deliberately WRONG for the routing under test: every row
        # keyed on it before #2787 sent round 2+ straight to /do-build.
        "revision_applied": True,
    }
    meta.update(extra)
    return meta


class TestG5AliveOnWithConcerns:
    """G5 must never serve a with-concerns verdict, in any revision state."""

    def _ctx(self):
        return {"current_plan_hash": _PLAN_HASH}

    def test_a_state_s1_unlocked_routes_to_row_4b(self):
        """S1: verdict is newest, no revision since, lock cleared -> row 4b."""
        states = _wc_states(_iso("2026-08-17T02:00:00"))
        meta = _wc_meta(_iso("2026-08-17T01:00:00"))
        result = decide_next_dispatch(states, meta, self._ctx())
        assert result.row_id != "G5"
        assert result.skill == SKILL_DO_PLAN
        assert result.row_id == "4b"

    def test_a2_state_s1_with_step_5_6_lock_routes_to_do_plan(self):
        """S1 as Step 5.6 actually leaves it: the lock is set.

        G7 gate 4 legitimately owns this turn and dispatches /do-plan with
        row_id="G7" before the dispatch table is consulted. Assert the SKILL,
        not the row — but still pin that G5 did not ship a build.
        """
        states = _wc_states(_iso("2026-08-17T02:00:00"))
        meta = _wc_meta(
            _iso("2026-08-17T01:00:00"),
            plan_revising=True,
            last_dispatched_skill=SKILL_DO_PLAN_CRITIQUE,
        )
        result = decide_next_dispatch(states, meta, self._ctx())
        assert result.row_id != "G5"
        assert result.skill == SKILL_DO_PLAN
        assert result.row_id in {"4b", "G7"}

    def test_a3_plan_dispatched_but_no_revision_landed_stays_on_row_4b(self):
        """Row 4b fired, /do-plan crashed before writing revision_applied_at.

        The dispatch record's `at` postdates the verdict, so a fallthrough to
        `verdict_dt < plan_dt` would call the verdict stale and let row 2b
        re-critique a plan nobody revised. It must resolve to row 4b instead.
        """
        states = _wc_states(
            _iso("2026-08-17T02:00:00"),
            plan_dispatch_at=_iso("2026-08-17T02:30:00"),
        )
        meta = _wc_meta(_iso("2026-08-17T01:00:00"))
        result = decide_next_dispatch(states, meta, self._ctx())
        assert result.row_id != "G5"
        assert result.row_id != "2b"
        assert result.skill == SKILL_DO_PLAN
        assert result.row_id == "4b"

    def test_b_state_s2_below_bound_routes_to_row_2b(self):
        """S2 below the bound: the revision landed and must be re-critiqued."""
        states = _wc_states(
            _iso("2026-08-17T02:00:00"),
            plan_dispatch_at=_iso("2026-08-17T02:30:00"),
        )
        meta = _wc_meta(_iso("2026-08-17T03:00:00"), count=1)
        result = decide_next_dispatch(states, meta, self._ctx())
        assert result.row_id != "G5"
        assert result.skill == SKILL_DO_PLAN_CRITIQUE
        assert result.row_id == "2b"

    def test_c_state_s2_at_bound_routes_to_row_4c(self):
        """S2 with the bound spent: build, with the acceptance in the reason."""
        states = _wc_states(
            _iso("2026-08-17T02:00:00"),
            plan_dispatch_at=_iso("2026-08-17T02:30:00"),
        )
        meta = _wc_meta(_iso("2026-08-17T03:00:00"), count=3)
        result = decide_next_dispatch(states, meta, self._ctx())
        assert result.row_id != "G5"
        assert result.skill == SKILL_DO_BUILD
        assert result.row_id == "4c"
        assert "bound" in result.reason.lower()
        assert "accepted" in result.reason.lower()

    def test_g5_still_fires_on_a_no_concerns_cache_hit(self):
        """The step-aside must not disarm G5 for clean verdicts."""
        states = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "pending",
            "_verdicts": {
                "CRITIQUE": {
                    "verdict": "READY TO BUILD",
                    "recorded_at": _iso("2026-08-17T02:00:00"),
                    "artifact_hash": _PLAN_HASH,
                }
            },
        }
        meta = {"latest_critique_verdict": "READY TO BUILD"}
        result = decide_next_dispatch(states, meta, self._ctx())
        assert result.skill == SKILL_DO_BUILD
        assert result.row_id == "G5"


class TestConcernBoundScoping:
    """The bound counts with-concerns rounds only, and terminates the loop."""

    def test_a_lane_with_a_long_needs_revision_history_still_gets_re_critique(self):
        """A NEEDS REVISION history must not arrive at the loop with the bound spent.

        The counter is written only on a WITH CONCERNS verdict, so `count` is 1
        on the first with-concerns round no matter how many NEEDS REVISION
        rounds preceded it. Here the ledger carries a dispatch history of five
        prior /do-plan-critique turns; routing must still be row 2b, not the
        bound-exhausted row 4c. (That the *counter* ignores NEEDS REVISION is
        proven writer-side in
        tests/unit/test_sdlc_verdict.py::TestConcernRoundCounter.)
        """
        states = _wc_states(
            _iso("2026-08-17T02:00:00"),
            plan_dispatch_at=_iso("2026-08-17T02:30:00"),
        )
        states["_sdlc_dispatches"] = [
            {"skill": SKILL_DO_PLAN_CRITIQUE, "at": _iso("2026-08-17T00:00:00")},
            {"skill": SKILL_DO_PLAN, "at": _iso("2026-08-17T00:10:00")},
            {"skill": SKILL_DO_PLAN_CRITIQUE, "at": _iso("2026-08-17T00:20:00")},
            {"skill": SKILL_DO_PLAN, "at": _iso("2026-08-17T00:30:00")},
            {"skill": SKILL_DO_PLAN_CRITIQUE, "at": _iso("2026-08-17T00:40:00")},
            {"skill": SKILL_DO_PLAN, "at": _iso("2026-08-17T02:30:00")},
        ]
        meta = _wc_meta(_iso("2026-08-17T03:00:00"), count=1)
        result = decide_next_dispatch(states, meta, {"current_plan_hash": _PLAN_HASH})
        assert result.skill == SKILL_DO_PLAN_CRITIQUE
        assert result.row_id == "2b"

    def test_mixed_timezone_awareness_falls_safe_to_a_revision_pass(self):
        """A naive frontmatter timestamp vs a tz-aware recorded_at must not build.

        `recorded_at` is always tz-aware (`datetime.now(UTC).isoformat()`), while
        `revision_applied_at` is parsed out of hand-editable plan frontmatter and
        may be naive. Comparing the two raises TypeError; the predicate swallows
        it to False, so the lane routes to a revision pass rather than to a build.
        """
        states = _wc_states("2026-08-17T02:00:00+00:00")
        meta = _wc_meta("2026-08-17T03:00:00", count=3)
        assert _concern_revision_is_unjudged(states, meta) is False
        result = decide_next_dispatch(states, meta, {"current_plan_hash": _PLAN_HASH})
        assert result.skill == SKILL_DO_PLAN
        assert result.row_id == "4b"

    def test_no_concerns_verdict_with_settled_revision_still_routes_to_4a(self):
        """The with-concerns branch must not leak into the clean path."""
        states = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "pending",
            "_verdicts": {
                "CRITIQUE": {
                    "verdict": "READY TO BUILD",
                    "recorded_at": _iso("2026-08-17T02:00:00"),
                }
            },
        }
        meta = {
            "latest_critique_verdict": "READY TO BUILD",
            "revision_applied_at": _iso("2026-08-17T03:00:00"),
        }
        result = decide_next_dispatch(states, meta)
        assert result.skill == SKILL_DO_BUILD
        assert result.row_id == "4a"

    def test_with_concerns_no_plan_dispatch_falls_safe_to_row_4b(self):
        """Bound spent but no /do-plan dispatch recorded and no revision landed.

        Pins that the with-concerns decision is control-flow independent of
        `_sdlc_dispatches`: the branch sits ahead of the `latest_plan_at` early
        return. Fail-safe direction is a revision pass, never a build.
        """
        states = _wc_states(_iso("2026-08-17T02:00:00"))
        meta = _wc_meta(None, count=3)
        result = decide_next_dispatch(states, meta, {"current_plan_hash": _PLAN_HASH})
        assert result.skill == SKILL_DO_PLAN
        assert result.row_id == "4b"

    def test_kill_switch_restores_pre_2787_routing(self, monkeypatch):
        """MAX_CONCERN_RECRITIQUE_ROUNDS=0 keeps the latch permanently engaged."""
        import agent.sdlc_router as router

        monkeypatch.setattr(router, "MAX_CONCERN_RECRITIQUE_ROUNDS", 0)
        states = _wc_states(
            _iso("2026-08-17T02:00:00"),
            plan_dispatch_at=_iso("2026-08-17T02:30:00"),
        )
        meta = _wc_meta(_iso("2026-08-17T03:00:00"), count=0)
        assert _critique_verdict_is_stale(states, meta) is False
        result = decide_next_dispatch(states, meta, {"current_plan_hash": _PLAN_HASH})
        assert result.skill == SKILL_DO_BUILD
        assert result.row_id == "4c"


class TestDispatchRuleOrderingIsLoadBearing:
    def test_row_2b_precedes_rows_4b_and_4c(self):
        """The design depends on first-match ordering; pin it with a test."""
        ids = [r.row_id for r in DISPATCH_RULES]
        assert ids.index("2b") < ids.index("4b")
        assert ids.index("2b") < ids.index("4c")


class TestForeverWithConcernsTerminates:
    """#2787 gate item 5: the loop terminates, and says so out loud.

    ``compute_same_stage_count`` breaks its streak on every skill change and
    this loop alternates ``/do-plan`` and ``/do-plan-critique``, so G4 can
    never fire on it. ``MAX_CONCERN_RECRITIQUE_ROUNDS`` is the ONLY
    terminator. This simulation is the proof that it actually terminates: it
    drives the real router turn by turn against a plan whose critique returns
    ``READY TO BUILD (with concerns)`` forever.
    """

    def _simulate(self, max_turns: int = 40) -> list:
        """Drive decide_next_dispatch, applying each dispatch's real effect.

        - ``/do-plan-critique`` records a fresh with-concerns verdict:
          ``recorded_at`` advances and ``record_verdict`` bumps the counter.
        - ``/do-plan`` settles a revision: ``revision_applied_at`` advances.
        - ``/do-build`` is terminal.
        """
        clock = [0]

        def tick() -> str:
            clock[0] += 1
            return _iso(f"2026-08-17T{clock[0]:02d}:00:00")

        states = _wc_states(tick())
        meta = _wc_meta(None, count=1)  # the first verdict already counted
        trail = []
        for _ in range(max_turns):
            result = decide_next_dispatch(states, meta, {"current_plan_hash": _PLAN_HASH})
            assert isinstance(result, Dispatch), f"loop escalated instead of terminating: {result}"
            trail.append(result)
            if result.skill == SKILL_DO_BUILD:
                return trail
            if result.skill == SKILL_DO_PLAN:
                # /do-plan Phase 4 step 2a co-writes both fields.
                meta["revision_applied"] = True
                meta["revision_applied_at"] = tick()
            elif result.skill == SKILL_DO_PLAN_CRITIQUE:
                # A fresh with-concerns verdict: new recorded_at, counter +1.
                states["_verdicts"]["CRITIQUE"]["recorded_at"] = tick()
                meta["concern_round_count"] += 1
            else:  # pragma: no cover - defensive
                raise AssertionError(f"unexpected skill in the loop: {result.skill}")
            # The router's own dispatch record, as record_dispatch would write it.
            states.setdefault("_sdlc_dispatches", []).append(
                {"skill": result.skill, "at": _iso(f"2026-08-17T{clock[0]:02d}:00:00")}
            )
            meta["last_dispatched_skill"] = result.skill
        raise AssertionError(f"never terminated in {max_turns} turns: {[d.row_id for d in trail]}")

    def test_terminates_at_the_bound_with_a_build(self):
        trail = self._simulate()
        assert trail[-1].skill == SKILL_DO_BUILD
        assert trail[-1].row_id == "4c"
        # It must re-critique at least once before giving up, or the bound is
        # not doing the job the feature exists to do.
        assert any(d.row_id == "2b" for d in trail), [d.row_id for d in trail]

    def test_consumes_exactly_the_bounded_number_of_recritiques(self):
        """Round 1's verdict is already counted, so the loop buys the rest."""
        trail = self._simulate()
        recritiques = [d for d in trail if d.skill == SKILL_DO_PLAN_CRITIQUE]
        assert len(recritiques) == MAX_CONCERN_RECRITIQUE_ROUNDS - 1, [d.row_id for d in trail]

    def test_row_4c_reason_records_the_accepted_residual_concerns(self):
        """Gate item 5: a silent build at the cap is the failure mode one level up.

        The reason string is the accountability record the supervisor sees, so
        assert the string itself — the bound, the count, and the fact that
        residual concerns were accepted WITHOUT review.
        """
        reason = self._simulate()[-1].reason
        lowered = reason.lower()
        assert "bound" in lowered, reason
        assert str(MAX_CONCERN_RECRITIQUE_ROUNDS) in reason, reason
        assert "with-concerns rounds" in lowered, reason
        assert "residual concerns accepted unreviewed" in lowered, reason

    def test_g4_cannot_see_this_loop(self):
        """The bound is the only terminator — pinned so nobody deletes it.

        A future reader will assume G4's oscillation cap backstops this. It
        does not: ``compute_same_stage_count`` counts CONSECUTIVE same-skill
        dispatches and breaks the streak on any skill change.
        """
        from agent.sdlc_router import compute_same_stage_count

        # An UNCHANGED snapshot throughout: the only thing breaking the streak
        # is the alternating skill, which is exactly the claim under test.
        snapshot = {"PLAN": "completed", "CRITIQUE": "completed", "BUILD": "pending"}
        history = [
            {
                "skill": SKILL_DO_PLAN if i % 2 == 0 else SKILL_DO_PLAN_CRITIQUE,
                "at": _iso("2026-08-17T01:00:00"),
                "stage_snapshot": snapshot,
            }
            for i in range(12)
        ]
        count, skill = compute_same_stage_count({"_sdlc_dispatches": history}, snapshot)
        # +1 for the "about to dispatch" turn; the recorded streak itself is 1.
        assert count <= 2, (count, skill)
        assert count < MAX_SAME_STAGE_DISPATCHES, (
            f"G4 would fire on the alternation at {count}; the bound is supposed "
            "to be this loop's only terminator"
        )
