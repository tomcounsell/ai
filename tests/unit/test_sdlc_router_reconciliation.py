"""Router tasks 2-4 of #3065, second layer: the properties the row-level tests
in ``tests/unit/test_sdlc_router.py`` do not pin.

That file already covers the headline behaviors — G3's docs arm, the NO_RULE
evidence payload, the #2771/#2334 shipped-lane redirect, the double-veto bound,
and the single G5 migration WARNING. This module deliberately does NOT repeat
them. It covers what is left, all of which is about the *mechanism* rather than
the routing answer:

  - ``detect_unrecorded_dispatch`` as a unit, across all three "no confirming
    record" shapes and its silent cases.
  - The evidence payload being a snapshot rather than a live alias, and being
    JSON-serializable — it is worthless if it cannot reach the CLI output.
  - Reconciliation's *agreement* cases, where a guard names the skill the table
    already chose. Blocking those would turn self-consistent decisions into
    hard stops, the over-reach Risk 2 warns about.
  - The by-reference invariant, asserted on object identity rather than on the
    log count.
  - Every context shape ``decide_next_dispatch`` is called with, including the
    ``context=None`` / ``context={}`` shape at
    ``agent/session_runner/runner.py:1408``.

Two-pole per #2658: each new gate has the state that must fire and the
neighbouring state that must not.
"""

from __future__ import annotations

import json
import logging

import pytest

from agent.sdlc_router import (
    NO_RULE_GUARD_ID,
    SKILL_DO_DOCS,
    SKILL_DO_MERGE,
    SKILL_DO_PATCH,
    SKILL_DO_PLAN,
    SKILL_DO_PLAN_CRITIQUE,
    SKILL_DO_PR_REVIEW,
    STATUS_COMPLETED,
    Blocked,
    Dispatch,
    _rule_critique_verdict_stale,
    build_decision_inputs,
    decide_next_dispatch,
    detect_unrecorded_dispatch,
    evaluate_guards,
    guard_g3_pr_lock,
    guard_g5_artifact_hash_cache,
    reconcile_dispatch,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _shipped_lane_states(**overrides) -> dict:
    """A lane that has shipped: PR open, REVIEW approved, DOCS still pending.

    The #2771 / #2334 shape. ``last_dispatched_skill`` is a PR-stage skill, not
    a plan-family one — that is spike-2's actual hole. With a plan-family
    ``last``, G3 trips in the pre-table pass and reconciliation is never the
    thing under test.
    """
    states = {
        "ISSUE": STATUS_COMPLETED,
        "PLAN": STATUS_COMPLETED,
        "CRITIQUE": STATUS_COMPLETED,
        "BUILD": STATUS_COMPLETED,
        "TEST": STATUS_COMPLETED,
        "REVIEW": STATUS_COMPLETED,
        "DOCS": "pending",
        "MERGE": "pending",
        "_verdicts": {
            # Recorded BEFORE the /do-plan dispatch below, which is what makes
            # row 2b classify it stale.
            "CRITIQUE": {
                "verdict": "READY TO BUILD",
                "recorded_at": "2026-09-01T00:00:00+00:00",
            },
            "REVIEW": {
                "verdict": "APPROVED",
                "recorded_at": "2026-09-03T00:00:00+00:00",
            },
        },
        "_sdlc_dispatches": [
            {
                "skill": SKILL_DO_PLAN,
                "at": "2026-09-02T00:00:00+00:00",
                "stage_snapshot": {"PLAN": STATUS_COMPLETED},
                "confirmed": True,
            },
            {
                "skill": SKILL_DO_PR_REVIEW,
                "at": "2026-09-03T00:00:00+00:00",
                "stage_snapshot": {"REVIEW": STATUS_COMPLETED},
                "confirmed": True,
            },
        ],
    }
    states.update(overrides)
    return states


def _shipped_lane_meta(**overrides) -> dict:
    meta = {
        "pr_number": 2771,
        "pr_merge_state": "CLEAN",
        "pr_state": "OPEN",
        "latest_critique_verdict": "READY TO BUILD",
        "latest_review_verdict": "APPROVED",
        "last_dispatched_skill": SKILL_DO_PR_REVIEW,
        "ci_all_passing": True,
        "same_stage_dispatch_count": 0,
    }
    meta.update(overrides)
    return meta


def _no_rule_states(**overrides) -> dict:
    """A state no dispatch-table row owns — an unrecognized REVIEW verdict.

    Not APPROVED (rows 8f/9/10 and G6 step aside), not CHANGES REQUESTED (rows
    8/8b step aside), and present (rows 8c/8d/8e require its absence).
    """
    states = {
        "ISSUE": STATUS_COMPLETED,
        "PLAN": STATUS_COMPLETED,
        "CRITIQUE": STATUS_COMPLETED,
        "BUILD": STATUS_COMPLETED,
        "TEST": STATUS_COMPLETED,
        "REVIEW": STATUS_COMPLETED,
        "DOCS": STATUS_COMPLETED,
        "MERGE": "pending",
        "_verdicts": {"REVIEW": {"verdict": "LGTM", "recorded_at": "2026-09-01T00:00:00+00:00"}},
    }
    states.update(overrides)
    return states


def _no_rule_meta(**overrides) -> dict:
    meta = {
        "pr_number": 77,
        "pr_merge_state": "CLEAN",
        "pr_state": "OPEN",
        "latest_review_verdict": "LGTM",
        "last_dispatched_skill": "/do-test",
        "ci_all_passing": True,
    }
    meta.update(overrides)
    return meta


# ---------------------------------------------------------------------------
# Task 2 — the evidence payload as a mechanism
# ---------------------------------------------------------------------------


class TestDecisionInputsPayload:
    def test_fixture_really_is_a_no_rule_block(self):
        """Guard the fixture: if a future row claims this state, the rest of
        this class silently stops testing anything."""
        result = decide_next_dispatch(_no_rule_states(), _no_rule_meta())
        assert isinstance(result, Blocked)
        assert result.guard_id == NO_RULE_GUARD_ID

    def test_payload_is_json_serializable(self):
        """It has to survive to the CLI's JSON output to be worth anything."""
        result = decide_next_dispatch(_no_rule_states(), _no_rule_meta())
        round_tripped = json.loads(json.dumps(result.decision_inputs))
        assert round_tripped["meta"]["pr_number"] == 77
        assert round_tripped["stage_states"]["REVIEW"] == STATUS_COMPLETED

    def test_payload_is_a_snapshot_not_a_live_alias(self):
        """A reader must see what the router saw, not what the dict became."""
        states = _no_rule_states()
        result = decide_next_dispatch(states, _no_rule_meta())
        states["REVIEW"] = "mutated-after-the-decision"
        assert result.decision_inputs["stage_states"]["REVIEW"] == STATUS_COMPLETED

    def test_a_routable_lane_produces_no_block_at_all(self):
        """Negative pole: the evidence path must not fire on a routable lane."""
        assert isinstance(
            decide_next_dispatch(_shipped_lane_states(), _shipped_lane_meta()), Dispatch
        )

    def test_extra_named_evidence_is_carried_through(self):
        payload = build_decision_inputs({"PLAN": "completed"}, {"pr_number": 1}, selected_row="2b")
        assert payload["selected_row"] == "2b"
        assert payload["stage_states"] == {"PLAN": "completed"}
        assert payload["meta"] == {"pr_number": 1}


class TestDetectUnrecordedDispatch:
    """The signal fires exactly when the last dispatch has no confirming
    record. Unit-level: the router-level wiring is covered in
    ``test_sdlc_router.py::TestNoRuleDecisionInputs``.
    """

    def _history(self, skill, confirmed):
        return [
            {
                "skill": skill,
                "at": "2026-09-03T00:00:00+00:00",
                "stage_snapshot": {},
                "confirmed": confirmed,
            }
        ]

    def test_fires_when_the_recorded_slot_was_never_confirmed(self):
        signal = detect_unrecorded_dispatch(
            {"_sdlc_dispatches": self._history(SKILL_DO_PR_REVIEW, False)},
            {"last_dispatched_skill": SKILL_DO_PR_REVIEW},
        )
        assert signal is not None
        assert signal["confirmed"] is False
        assert "never confirmed" in signal["reason"]

    def test_silent_when_the_slot_was_confirmed(self):
        """Negative pole for the same state, one field apart."""
        signal = detect_unrecorded_dispatch(
            {"_sdlc_dispatches": self._history(SKILL_DO_PR_REVIEW, True)},
            {"last_dispatched_skill": SKILL_DO_PR_REVIEW},
        )
        assert signal is None

    def test_fires_when_no_record_exists_at_all(self):
        signal = detect_unrecorded_dispatch(
            {"_sdlc_dispatches": []},
            {"last_dispatched_skill": SKILL_DO_PR_REVIEW},
        )
        assert signal is not None
        assert signal["recorded_skill"] is None
        assert "dispatch record" in signal["reason"]

    def test_fires_when_the_newest_record_names_another_skill(self):
        signal = detect_unrecorded_dispatch(
            {"_sdlc_dispatches": self._history(SKILL_DO_PLAN, True)},
            {"last_dispatched_skill": SKILL_DO_PR_REVIEW},
        )
        assert signal is not None
        assert signal["recorded_skill"] == SKILL_DO_PLAN

    def test_silent_when_nothing_has_been_dispatched_yet(self):
        """An empty ledger is not an unrecorded dispatch."""
        assert detect_unrecorded_dispatch({}, {}) is None
        assert detect_unrecorded_dispatch({}, {"last_dispatched_skill": None}) is None

    def test_silent_on_a_malformed_history(self):
        assert (
            detect_unrecorded_dispatch(
                {"_sdlc_dispatches": "not-a-list"},
                {"last_dispatched_skill": None},
            )
            is None
        )

    def test_the_signal_never_changes_which_skill_is_dispatched(self):
        """Purely diagnostic — the two poles must agree on the skill."""
        confirmed = decide_next_dispatch(_shipped_lane_states(), _shipped_lane_meta())
        states = _shipped_lane_states()
        states["_sdlc_dispatches"][-1]["confirmed"] = False
        unconfirmed = decide_next_dispatch(states, _shipped_lane_meta())
        assert unconfirmed.unrecorded_dispatch is not None
        assert confirmed.unrecorded_dispatch is None
        assert confirmed.skill == unconfirmed.skill == SKILL_DO_DOCS


# ---------------------------------------------------------------------------
# Task 3 — the G3 arms the row-level tests do not pin
# ---------------------------------------------------------------------------


class TestG3LadderEdges:
    def _states(self, review, docs, verdict):
        verdicts = {}
        if verdict is not None:
            verdicts["REVIEW"] = {"verdict": verdict}
        return {"PLAN": STATUS_COMPLETED, "REVIEW": review, "DOCS": docs, "_verdicts": verdicts}

    def _meta(self, verdict):
        return {
            "pr_number": 42,
            "last_dispatched_skill": SKILL_DO_PLAN,
            "latest_review_verdict": verdict,
        }

    def test_changes_requested_beats_a_completed_docs_marker(self):
        """Arm ordering: a completed DOCS marker must not launder a CHANGES
        REQUESTED verdict into /do-merge."""
        result = guard_g3_pr_lock(
            self._states(STATUS_COMPLETED, STATUS_COMPLETED, "CHANGES REQUESTED"),
            self._meta("CHANGES REQUESTED"),
            {},
        )
        assert result.skill == SKILL_DO_PATCH
        assert result.skill != SKILL_DO_MERGE

    def test_guard_steps_aside_entirely_without_a_pr(self):
        """Negative pole for the whole guard, not just an arm."""
        meta = self._meta("APPROVED")
        meta["pr_number"] = None
        assert (
            guard_g3_pr_lock(self._states(STATUS_COMPLETED, "pending", "APPROVED"), meta, {})
            is None
        )

    def test_guard_steps_aside_when_nothing_plan_family_is_in_play(self):
        meta = self._meta("APPROVED")
        meta["last_dispatched_skill"] = "/do-test"
        assert (
            guard_g3_pr_lock(self._states(STATUS_COMPLETED, "pending", "APPROVED"), meta, {})
            is None
        )


# ---------------------------------------------------------------------------
# Task 4 — reconciliation mechanics
# ---------------------------------------------------------------------------


class TestReconciliationPremises:
    """If either premise below stops holding, the shipped-lane tests in
    ``test_sdlc_router.py`` silently stop exercising reconciliation."""

    def test_the_table_really_does_select_row_2b_here(self):
        states, meta = _shipped_lane_states(), _shipped_lane_meta()
        assert _rule_critique_verdict_stale(states, meta, {}) is True

    def test_the_guards_do_not_trip_before_the_table_runs(self):
        """The pre-table pass must be silent, or G3 would already have fired
        on ``last_dispatched_skill`` and reconciliation would be untested."""
        states, meta = _shipped_lane_states(), _shipped_lane_meta()
        assert evaluate_guards(states, meta, {}) is None

    @pytest.mark.parametrize("context", [None, {}, {"proposed_skill": SKILL_DO_PLAN_CRITIQUE}])
    def test_identical_answer_for_every_context_shape(self, context):
        """``agent/session_runner/runner.py:1408`` passes no context at all.
        Reconciliation must not silently no-op on that path."""
        result = decide_next_dispatch(_shipped_lane_states(), _shipped_lane_meta(), context)
        assert result.skill == SKILL_DO_DOCS

    def test_a_lane_with_no_pr_still_gets_its_plan_critique(self):
        """Negative pole. Reconciliation may only WITHHOLD a dispatch a guard
        would already refuse; it must never take away a legitimate one."""
        meta = _shipped_lane_meta(pr_number=None, pr_merge_state=None, pr_state=None)
        states = _shipped_lane_states(REVIEW="pending", DOCS="pending")
        states["_verdicts"].pop("REVIEW")
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PLAN_CRITIQUE


class TestReconciliationAgreementCases:
    """A guard naming the skill the table already chose is agreeing with it,
    not vetoing it. Treating that as a veto would either relabel a decision
    nobody objected to (Risk 1) or hard-stop a self-consistent lane (Risk 2).
    """

    def test_a_guard_agreeing_with_the_table_keeps_the_table_row(self, monkeypatch):
        def agrees(stage_states, meta, context):
            proposed = context.get("proposed_skill")
            return Dispatch(skill=proposed, reason="agree", row_id="GX") if proposed else None

        monkeypatch.setattr("agent.sdlc_router.GUARDS", [agrees])
        primary = Dispatch(skill=SKILL_DO_DOCS, reason="row 9", row_id="9")
        result = reconcile_dispatch({}, {}, {}, primary)
        assert result.row_id == "9"
        assert result.reason == "row 9"

    def test_a_guard_agreeing_with_the_redirect_is_not_a_second_veto(self, monkeypatch):
        def redirect_then_agree(stage_states, meta, context):
            proposed = context.get("proposed_skill")
            if proposed == SKILL_DO_PLAN_CRITIQUE:
                return Dispatch(skill=SKILL_DO_DOCS, reason="veto", row_id="GX")
            if proposed == SKILL_DO_DOCS:
                return Dispatch(skill=SKILL_DO_DOCS, reason="agree", row_id="GX")
            return None

        monkeypatch.setattr("agent.sdlc_router.GUARDS", [redirect_then_agree])
        result = reconcile_dispatch(
            {}, {}, {}, Dispatch(skill=SKILL_DO_PLAN_CRITIQUE, reason="r", row_id="2b")
        )
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_DOCS

    def test_a_single_veto_returns_the_redirect(self, monkeypatch):
        def once(stage_states, meta, context):
            if context.get("proposed_skill") == SKILL_DO_PLAN_CRITIQUE:
                return Dispatch(skill=SKILL_DO_DOCS, reason="veto", row_id="GX")
            return None

        monkeypatch.setattr("agent.sdlc_router.GUARDS", [once])
        result = reconcile_dispatch(
            {}, {}, {}, Dispatch(skill=SKILL_DO_PLAN_CRITIQUE, reason="r", row_id="2b")
        )
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_DOCS

    def test_a_guards_own_block_is_returned_unwrapped(self, monkeypatch):
        def blocks(stage_states, meta, context):
            if context.get("proposed_skill"):
                return Blocked(reason="G4: capped", guard_id="G4")
            return None

        monkeypatch.setattr("agent.sdlc_router.GUARDS", [blocks])
        result = reconcile_dispatch(
            {}, {}, {}, Dispatch(skill=SKILL_DO_DOCS, reason="r", row_id="9")
        )
        assert isinstance(result, Blocked)
        assert result.guard_id == "G4"


class TestReconciliationPassesInputsByReference:
    """STATED INVARIANT, not an accident: the guards are not pure and
    reconciliation runs them twice. ``guard_g5_artifact_hash_cache`` mutates
    ``record["artifact_hash"]`` in place and logs a WARNING on a legacy-hash
    migration. Double invocation is idempotent ONLY because ``stage_states`` is
    passed by reference and the second pass sees the already-migrated record.
    A defensive copy anywhere between the passes re-runs the migration.

    ``test_sdlc_router.py::TestG5DoubleInvocationDuringReconciliation`` asserts
    the resulting WARNING count. These two assert the mechanism underneath it.
    """

    def test_guards_receive_the_caller_s_own_objects(self, monkeypatch):
        """Identity, not equality. A defensive copy would still compare equal
        and would still break G5's in-place migration."""
        seen: list[tuple[int, int]] = []

        def spy(stage_states, meta, context):
            seen.append((id(stage_states), id(meta)))
            return None

        monkeypatch.setattr("agent.sdlc_router.GUARDS", [spy])
        states, meta = _shipped_lane_states(), _shipped_lane_meta()
        decide_next_dispatch(states, meta, {})
        assert len(seen) == 2, "one pre-table pass plus exactly one reconciliation pass"
        assert seen[0] == seen[1] == (id(states), id(meta))

    def test_a_second_g5_invocation_alone_emits_no_further_warning(self, caplog):
        """Pins the idempotence the invariant relies on, without going through
        the router at all."""
        states = _shipped_lane_states()
        states["_verdicts"]["CRITIQUE"]["artifact_hash"] = "sha256:legacy"
        meta = _shipped_lane_meta()
        context = {"current_plan_hash": "sha256:new", "legacy_plan_hash": "sha256:legacy"}

        with caplog.at_level(logging.WARNING, logger="agent.sdlc_router"):
            guard_g5_artifact_hash_cache(states, meta, context)
            first = len([r for r in caplog.records if "G5 migration" in r.getMessage()])
            guard_g5_artifact_hash_cache(states, meta, context)
            second = len([r for r in caplog.records if "G5 migration" in r.getMessage()])

        assert first == 1, "the migration branch must actually have run"
        assert second == 1, "the second invocation must see the already-migrated hash"
        assert states["_verdicts"]["CRITIQUE"]["artifact_hash"] == "sha256:new"
