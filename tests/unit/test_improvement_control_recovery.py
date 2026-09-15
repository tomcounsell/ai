"""The reconcile pass: stale sweeps, forced terminal, slot release (Task 7)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from models.agent_session import AgentSession, SessionType
from models.improvement_case import ImprovementCase
from tools.improvement_control import keys
from tools.improvement_control.intents import (
    admit,
    list_intents,
    record_materialized,
    record_running,
)
from tools.improvement_control.journal import transition
from tools.improvement_control.lease import CaseLease
from tools.improvement_control.recovery import reconcile
from utils.redis_client import text_redis

PK = "test-3215-recovery"
LEASE_TTL = 10  # small, so the age threshold (4x) is a small, test-friendly number


def new_case() -> ImprovementCase:
    return ImprovementCase.create(
        project_key=PK, state="investigating", title="t", created_at=datetime.now(UTC)
    )


def propose(case_id: str, action_id: str) -> None:
    r = transition(
        PK,
        case_id,
        expected_revision=0,
        generation=1,
        event="action_proposed",
        payload_digest="sha256:d",
        action_id=action_id,
    )
    assert r.accepted, r


def admitted_intent(case_id: str, action_id: str):
    propose(case_id, action_id)
    r = admit(
        PK,
        case_id,
        action_id,
        expected_revision=1,
        generation=1,
        action_type="investigate",
        max_concurrent=5,
    )
    assert r.accepted
    return r


def age_intent(case_id: str, action_id: str, seconds_old: float) -> None:
    import time

    text_redis().hset(
        keys.intent_key(PK, case_id, action_id), "updated_ts", str(time.time() - seconds_old)
    )


def fake_lease() -> CaseLease:
    return CaseLease(text_redis(), default_ttl_seconds=90)


class TestFreshIntentsAreLeftAlone:
    def test_a_fresh_admitted_intent_is_untouched(self):
        case = new_case()
        admitted_intent(case.id, "a1")
        result = reconcile(PK, now=None, lease_ttl=LEASE_TTL, lease=fake_lease())
        assert "a1" not in result.stale_sweeps
        assert "a1" not in result.forced_reconciliation
        assert list_intents(PK, case.id)[0].state == "admitted"


class TestStaleSweepBudget:
    def test_swept_only_once_stale_sweeps_reaches_max_dispatch_attempts(self):
        case = new_case()
        r1 = admitted_intent(case.id, "a1")
        age_intent(case.id, "a1", LEASE_TTL * 4 + 1)

        import time as time_module

        now = time_module.time()
        r_pass1 = reconcile(PK, now=now, lease_ttl=LEASE_TTL, lease=fake_lease())
        assert "a1" in r_pass1.stale_sweeps
        assert list_intents(PK, case.id)[0].stale_sweeps == 1
        assert list_intents(PK, case.id)[0].state == "admitted"

        age_intent(case.id, "a1", LEASE_TTL * 4 + 1)
        reconcile(PK, now=now, lease_ttl=LEASE_TTL, lease=fake_lease())
        assert list_intents(PK, case.id)[0].stale_sweeps == 2
        assert list_intents(PK, case.id)[0].state == "admitted"

        age_intent(case.id, "a1", LEASE_TTL * 4 + 1)
        r_pass3 = reconcile(PK, now=now, lease_ttl=LEASE_TTL, lease=fake_lease())
        wedged = list_intents(PK, case.id)[0]
        assert wedged.state == "reconciliation_required"
        assert wedged.reason == "max_dispatch_attempts"
        assert "a1" in r_pass3.forced_reconciliation
        assert "a1" not in r_pass3.forced_terminal  # no bound row to finalize

        # The no-row branch still writes the one improve_intent dead letter:
        # an `admitted` intent that never materialized has no session to
        # finalize, and the exhaustion must be on record regardless.
        import json

        from models.dead_letter import DeadLetter

        rows = [
            row
            for row in DeadLetter.query.filter(project_key=PK, stage="improve_intent")
            if json.loads(row.payload_json).get("case_id") == case.id
        ]
        assert len(rows) == 1
        assert json.loads(rows[0].payload_json)["action_id"] == "a1"
        assert rows[0].replayable is False
        assert rows[0].reason == "max_dispatch_attempts"
        del r1  # only used to admit; assertions are on the re-read intent


class TestRace3UnreleasedSlotOnRestart:
    def test_unreleased_slot_is_freed_after_one_pass(self):
        case = new_case()
        r1 = admitted_intent(case.id, "a1")
        r2 = record_materialized(
            PK,
            case.id,
            "a1",
            expected_revision=r1.revision,
            generation=1,
            agent_session_id="ghost-session",
        )
        assert r2.accepted
        r_run = record_running(PK, case.id, "a1", expected_revision=r2.revision, generation=1)
        assert r_run.accepted
        age_intent(case.id, "a1", LEASE_TTL * 4 + 1)
        assert text_redis().hexists(keys.slots_key(PK), "a1")

        result = reconcile(PK, lease_ttl=LEASE_TTL, lease=fake_lease())

        assert "a1" in result.released_slots
        assert not text_redis().hexists(keys.slots_key(PK), "a1")
        wedged = list_intents(PK, case.id)[0]
        assert wedged.state == "reconciliation_required"
        # The move script writes the reason in the same call as the state
        # move and the slot release; `case explain` reads it from here.
        assert wedged.reason == "session_gone_or_terminal"


class TestRunningWithLiveRowIsUntouched:
    def test_running_intent_with_a_live_row_is_never_touched_regardless_of_age(self):
        case = new_case()
        r1 = admitted_intent(case.id, "a1")
        session_id = f"test-recovery-{uuid.uuid4().hex[:8]}"
        AgentSession.create(
            project_key=PK,
            chat_id="0",
            session_type=SessionType.ENG,
            message_text="x",
            sender_name="x",
            session_id=session_id,
            working_dir=".",
            status="running",
        )
        r2 = record_materialized(
            PK,
            case.id,
            "a1",
            expected_revision=r1.revision,
            generation=1,
            agent_session_id=session_id,
        )
        assert r2.accepted
        r_run = record_running(PK, case.id, "a1", expected_revision=r2.revision, generation=1)
        assert r_run.accepted
        age_intent(case.id, "a1", LEASE_TTL * 100)

        result = reconcile(PK, lease_ttl=LEASE_TTL, lease=fake_lease())

        assert "a1" not in result.forced_reconciliation
        assert list_intents(PK, case.id)[0].state == "running"


class TestReconciliationRequiredHasOneExit:
    def test_reconciliation_required_intent_is_never_touched_again_by_the_pass(self):
        case = new_case()
        r1 = admitted_intent(case.id, "a1")
        age_intent(case.id, "a1", LEASE_TTL * 4 + 1)
        for _ in range(3):
            age_intent(case.id, "a1", LEASE_TTL * 4 + 1)
            reconcile(PK, lease_ttl=LEASE_TTL, lease=fake_lease())
        assert list_intents(PK, case.id)[0].state == "reconciliation_required"

        # A further pass must not touch it (only `cancel` moves it).
        age_intent(case.id, "a1", LEASE_TTL * 4 + 1)
        result = reconcile(PK, lease_ttl=LEASE_TTL, lease=fake_lease())
        assert "a1" not in result.forced_reconciliation
        assert list_intents(PK, case.id)[0].state == "reconciliation_required"
        del r1


class TestForcedFinalizeLogging:
    def test_exhausted_intent_with_terminal_row_skips_finalize(self, caplog):
        """A bound row already terminal: the intent still moves to
        reconciliation_required and the slot is released, but finalize_session
        is never called (it would raise StatusConflictError on a terminal
        row) and no ERROR record is logged."""
        case = new_case()
        r1 = admitted_intent(case.id, "a1")
        session_id = f"test-recovery-{uuid.uuid4().hex[:8]}"
        AgentSession.create(
            project_key=PK,
            chat_id="0",
            session_type=SessionType.ENG,
            message_text="x",
            sender_name="x",
            session_id=session_id,
            working_dir=".",
            status="completed",
        )
        record_materialized(
            PK,
            case.id,
            "a1",
            expected_revision=r1.revision,
            generation=1,
            agent_session_id=session_id,
        )
        for _ in range(3):
            age_intent(case.id, "a1", LEASE_TTL * 4 + 1)
            with caplog.at_level(logging.WARNING):
                reconcile(PK, lease_ttl=LEASE_TTL, lease=fake_lease())

        assert list_intents(PK, case.id)[0].state == "reconciliation_required"
        assert not any(rec.levelno >= logging.ERROR for rec in caplog.records)

    def test_reconcile_forced_finalize_logs_no_warning(self, caplog):
        """The slot's own on_session_terminal call (triggered by the forced
        finalize_session, when the row is not already terminal) sees
        slot == "absent" (this pass already released it) at DEBUG, not
        WARNING."""
        case = new_case()
        r1 = admitted_intent(case.id, "a1")
        session_id = f"test-recovery-{uuid.uuid4().hex[:8]}"
        AgentSession.create(
            project_key=PK,
            chat_id="0",
            session_type=SessionType.ENG,
            message_text="x",
            sender_name="x",
            session_id=session_id,
            working_dir=".",
            status="admitted",
            extra_context={"action_id": "a1", "research_case_id": case.id},
        )
        record_materialized(
            PK,
            case.id,
            "a1",
            expected_revision=r1.revision,
            generation=1,
            agent_session_id=session_id,
        )
        for _ in range(3):
            age_intent(case.id, "a1", LEASE_TTL * 4 + 1)
            with caplog.at_level(logging.WARNING):
                reconcile(PK, lease_ttl=LEASE_TTL, lease=fake_lease())

        # bridge.dead_letters logs its own WARNING on every recorded row --
        # ordinary and expected. What must NOT appear is intents.py's
        # `foreign_holder` WARNING (Decision 6): the slot was already
        # released by mark_reconciliation_required before finalize_session's
        # step 7 ran, so on_session_terminal must see "absent" (DEBUG), never
        # "foreign_holder".
        intents_warnings = [
            rec
            for rec in caplog.records
            if rec.name == "tools.improvement_control.intents" and rec.levelno >= logging.WARNING
        ]
        assert intents_warnings == []
