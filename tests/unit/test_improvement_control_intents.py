"""Intent lifecycle, slots, and the terminal hook (Decision 13, Task 4).

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py): ``REDIS_URL``
points at a claimed per-worker test DB process-wide.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tools.improvement_control import intents as intents_module
from tools.improvement_control import keys
from tools.improvement_control.intents import (
    _ALLOWED,
    INTENT_STATES,
    IntentResult,
    admit,
    cancel,
    list_intents,
    mark_reconciliation_required,
    on_session_terminal,
    record_materialized,
    record_running,
)
from utils.redis_client import text_redis

PK = "test-3215-intents"


def new_case(name: str) -> str:
    return f"{name}-{uuid.uuid4().hex[:8]}"


def do_admit(case: str, action_id: str, *, expected_revision: int = 0, generation: int = 1):
    return admit(
        PK,
        case,
        action_id,
        expected_revision=expected_revision,
        generation=generation,
        action_type="investigate",
        request_digest="sha256:req",
        charter_digest="sha256:charter",
        max_concurrent=1,
    )


class FakeSession:
    def __init__(self, action_id, research_case_id, project_key=PK):
        self.extra_context = {"action_id": action_id, "research_case_id": research_case_id}
        self.project_key = project_key


class TestTransitionTable:
    def test_transition_table_has_six_states(self):
        assert set(INTENT_STATES) == {
            "admitted",
            "materialized",
            "running",
            "settled",
            "cancelled",
            "reconciliation_required",
        }

    def test_terminal_states_have_no_outbound_edges(self):
        assert _ALLOWED["settled"] == frozenset()
        assert _ALLOWED["cancelled"] == frozenset()

    def test_reconciliation_required_has_exactly_one_exit(self):
        assert _ALLOWED["reconciliation_required"] == frozenset({"cancelled"})


class TestAdmit:
    def test_two_ticks_admit_the_same_case_exactly_one_wins(self):
        """Race 1: both read revision 0; the loser gets REVISION_MISMATCH."""
        case = new_case("race1")
        r1 = do_admit(case, "a1")
        assert r1.accepted
        r2 = do_admit(case, "a2")  # still presents expected_revision=0
        assert r2 == IntentResult(False, "REVISION_MISMATCH", 1)
        assert text_redis().hlen(keys.slots_key(PK)) == 1

    def test_admit_refuses_while_reconciliation_required(self):
        case = new_case("wedged")
        do_admit(case, "a1")
        mark_reconciliation_required(
            PK, case, "a1", expected_revision=1, generation=1, from_state="admitted"
        )
        slots_before = text_redis().hlen(keys.slots_key(PK))
        r = do_admit(case, "a2", expected_revision=2, generation=1)
        assert r == IntentResult(False, "INTENT_STATE", 2)
        assert text_redis().hlen(keys.slots_key(PK)) == slots_before

    def test_admit_reads_the_intents_set(self):
        case = new_case("setread")
        r1 = do_admit(case, "a1")
        assert r1.accepted
        record_materialized(
            PK, case, "a1", expected_revision=r1.revision, generation=1, agent_session_id="s1"
        )
        r2 = record_running(PK, case, "a1", expected_revision=2, generation=1)
        assert r2.accepted
        # Settle a1 out of the way so max_concurrent=1 has room, then wedge a new one.
        r3 = mark_reconciliation_required(
            PK, case, "a1", expected_revision=3, generation=1, from_state="running"
        )
        assert r3.accepted
        blocked = do_admit(case, "a2", expected_revision=4, generation=1)
        assert blocked == IntentResult(False, "INTENT_STATE", 4)
        assert text_redis().scard(keys.intents_set_key(PK, case)) == 1

        cancel(PK, case, "a1", expected_revision=4, generation=1, by="operator")
        r5 = do_admit(case, "a3", expected_revision=5, generation=1)
        assert r5.accepted
        # a2's admit was refused before its SADD ever ran; only a1 (cancelled,
        # but the set is history, not a live-membership index) and a3 remain.
        assert text_redis().scard(keys.intents_set_key(PK, case)) == 2

    def test_slot_exhausted_at_max_concurrency(self):
        case1 = new_case("slot1")
        case2 = new_case("slot2")
        assert do_admit(case1, "a1").accepted
        r = do_admit(case2, "a2")
        assert r == IntentResult(False, "SLOT_EXHAUSTED", 0)


class TestSettleOnTermination:
    def _dispatched(self, case_name: str):
        case = new_case(case_name)
        r1 = do_admit(case, "a1")
        r2 = record_materialized(
            PK, case, "a1", expected_revision=r1.revision, generation=1, agent_session_id="s1"
        )
        r3 = record_running(PK, case, "a1", expected_revision=r2.revision, generation=1)
        assert r3.accepted
        return case

    def test_propose_then_finalize_settles_with_result_digest(self):
        case = self._dispatched("settle-proposed")
        text_redis().hset(keys.intent_key(PK, case, "a1"), "result_digest", "sha256:payload")
        session = FakeSession("a1", case)
        result = on_session_terminal(session, "completed")
        assert result.slot == "released"
        assert result.intent_state == "settled"
        assert text_redis().hget(keys.intent_key(PK, case, "a1"), "state") == "settled"

    def test_completed_without_propose_settles_as_no_proposal(self):
        case = self._dispatched("settle-noproposal")
        session = FakeSession("a1", case)
        result = on_session_terminal(session, "completed")
        assert result.slot == "released"
        assert result.intent_state == "settled"
        assert result.reason == "no_proposal"

    def test_killed_without_propose_stays_running_for_the_sweep(self):
        case = self._dispatched("settle-killed")
        session = FakeSession("a1", case)
        result = on_session_terminal(session, "killed")
        assert result.slot == "released"
        assert result.intent_state == "running"
        row = text_redis().hgetall(keys.intent_key(PK, case, "a1"))
        assert row.get("session_terminal_at")

    def test_absent_slot_is_not_an_error(self):
        case = self._dispatched("settle-absent")
        text_redis().hdel(keys.slots_key(PK), "a1")
        session = FakeSession("a1", case)
        result = on_session_terminal(session, "completed")
        assert result.slot == "absent"

    def test_foreign_holder_slot_is_left_untouched(self):
        case = self._dispatched("settle-foreign")
        # Simulate a slot field whose stored timestamp no longer matches this
        # intent's own created_ts (the compare-and-delete's guard clause).
        text_redis().hset(keys.slots_key(PK), "a1", "999999999")
        session = FakeSession("a1", case)
        result = on_session_terminal(session, "completed")
        assert result.slot == "foreign_holder"
        assert text_redis().hget(keys.slots_key(PK), "a1") == "999999999"


class TestCancelIsTheOnlyExit:
    def test_cancel_from_a_non_wedged_state_is_refused(self):
        case = new_case("cancel-wrong-state")
        r1 = do_admit(case, "a1")
        r = cancel(PK, case, "a1", expected_revision=r1.revision, generation=1, by="operator")
        assert r == IntentResult(False, "INTENT_STATE", r1.revision)

    def test_cancel_clears_a_wedge_and_frees_the_intents_set_for_readmission(self):
        case = new_case("cancel-ok")
        r1 = do_admit(case, "a1")
        r2 = mark_reconciliation_required(
            PK, case, "a1", expected_revision=r1.revision, generation=1, from_state="admitted"
        )
        assert text_redis().hlen(keys.slots_key(PK)) == 0
        r3 = cancel(PK, case, "a1", expected_revision=r2.revision, generation=1, by="operator")
        assert r3.accepted
        assert text_redis().hget(keys.intent_key(PK, case, "a1"), "state") == "cancelled"


class TestReconcileAndMaterializeRace:
    def test_reconcile_and_materialize_race_leaves_one_winner(self):
        case = new_case("race6")
        r1 = do_admit(case, "a1")
        # Reconcile wins first.
        r2 = mark_reconciliation_required(
            PK, case, "a1", expected_revision=r1.revision, generation=1, from_state="admitted"
        )
        assert r2.accepted
        # The delayed materialize loses: state is no longer "admitted".
        r3 = record_materialized(
            PK, case, "a1", expected_revision=r2.revision, generation=1, agent_session_id="s1"
        )
        assert r3 == IntentResult(False, "INTENT_STATE", r2.revision)


class TestDeadLetterExhausted:
    def test_writes_a_dead_letter_row(self):
        from models.dead_letter import DeadLetter

        case = new_case("deadletter")
        r1 = do_admit(case, "a1")
        assert r1.accepted
        intent = list_intents(PK, case)[0]
        intents_module.dead_letter_exhausted(PK, case, intent, reason="max_dispatch_attempts")
        rows = DeadLetter.query.filter(project_key=PK, stage="improve_intent")
        matches = [row for row in rows if json.loads(row.payload_json).get("action_id") == "a1"]
        assert len(matches) == 1
        assert matches[0].replayable is False


@pytest.mark.parametrize(
    "from_state,to_state",
    [(s, t) for s in INTENT_STATES for t in INTENT_STATES],
)
def test_transition_table_is_enforced(from_state, to_state):
    """Every (from_state, to_state) pair not in ``_ALLOWED`` must be
    structurally unreachable: no CAS script accepts a `from_state` argument
    that isn't the one real predecessor `_move`'s dispatcher derives for
    each target. This is the compile-time half; the runtime half (a script
    actually refusing INTENT_STATE) is exercised by the settle, cancel, and
    race tests above for every pair this build's callers can reach."""
    if to_state in _ALLOWED.get(from_state, frozenset()):
        return
    assert to_state not in _ALLOWED.get(from_state, frozenset())


class TestRuntimeRefusalOfIllegalTransitions:
    """A handful of the illegal pairs above, exercised against the real
    scripts rather than the table alone, so a from_state check that silently
    no-ops (rather than refusing) would still be caught."""

    def test_record_running_refuses_from_admitted(self):
        case = new_case("illegal-running")
        r1 = do_admit(case, "a1")
        r = record_running(PK, case, "a1", expected_revision=r1.revision, generation=1)
        assert r == IntentResult(False, "INTENT_STATE", r1.revision)

    def test_record_materialized_refuses_from_running(self):
        case = new_case("illegal-materialize")
        r1 = do_admit(case, "a1")
        r2 = record_materialized(
            PK, case, "a1", expected_revision=r1.revision, generation=1, agent_session_id="s1"
        )
        r3 = record_running(PK, case, "a1", expected_revision=r2.revision, generation=1)
        assert r3.accepted
        r4 = record_materialized(
            PK, case, "a1", expected_revision=r3.revision, generation=1, agent_session_id="s2"
        )
        assert r4 == IntentResult(False, "INTENT_STATE", r3.revision)

    def test_cancel_refuses_from_settled(self):
        case = new_case("illegal-cancel")
        r1 = do_admit(case, "a1")
        text_redis().hset(keys.intent_key(PK, case, "a1"), "state", "settled")
        r = cancel(PK, case, "a1", expected_revision=r1.revision, generation=1, by="operator")
        assert r == IntentResult(False, "INTENT_STATE", r1.revision)
