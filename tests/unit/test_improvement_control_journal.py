"""The control journal's transition script (Decision 3, Task 1).

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py): ``REDIS_URL``
points at a claimed per-worker test DB process-wide, so every ``improve:*``
key here never touches production. These are plain (non-Popoto) Redis keys,
so seeding fixtures write them directly through the private client rather
than through the ORM — see the package docstring for why that is the
sanctioned shape here, not a raw-Redis-guard violation.
"""

from __future__ import annotations

import logging

import pytest

from tools.improvement_control import keys
from tools.improvement_control.journal import (
    KNOWN_EVENTS,
    TransitionResult,
    journal_length,
    journal_tail,
    read_head,
    transition,
)
from tools.improvement_control.lease import CaseLease
from utils.redis_client import text_redis

PK = "test-3215-journal"


def new_case(name: str) -> str:
    """A fresh case id, scoped per test so keys never collide across tests."""
    return f"{name}-{id(object())}"


def lease() -> CaseLease:
    return CaseLease(text_redis(), default_ttl_seconds=90)


class TestAcceptedTransition:
    def test_first_transition_creates_the_head_at_revision_one(self):
        case = new_case("accept")
        result = transition(
            PK,
            case,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="sha256:abc",
            action_id="a1",
        )
        assert result == TransitionResult(True, "OK", 1)
        head = read_head(PK, case)
        assert head is not None
        assert head.revision == 1
        assert head.highest_accepted == 1

    def test_journal_entry_is_appended(self):
        case = new_case("entry")
        transition(
            PK,
            case,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="sha256:abc",
            action_id="a1",
        )
        tail = journal_tail(PK, case, 10)
        assert len(tail) == 1
        assert tail[0]["event"] == "action_proposed"
        assert tail[0]["revision"] == 1
        assert tail[0]["action_id"] == "a1"

    def test_second_write_by_the_same_holder_is_accepted(self):
        case = new_case("holder")
        r1 = transition(
            PK,
            case,
            expected_revision=0,
            generation=5,
            event="action_proposed",
            payload_digest="d1",
            action_id="a1",
        )
        assert r1.accepted
        r2 = transition(
            PK,
            case,
            expected_revision=1,
            generation=5,
            event="action_proposed",
            payload_digest="d2",
            action_id="a2",
        )
        assert r2.accepted
        assert r2.revision == 2


class TestBoundedJournal:
    def test_journal_is_trimmed_to_max_entries(self):
        case = new_case("bounded")
        rev = 0
        for i in range(5):
            r = transition(
                PK,
                case,
                expected_revision=rev,
                generation=1,
                event="action_proposed",
                payload_digest=f"d{i}",
                action_id=f"a{i}",
                journal_max_entries=3,
            )
            assert r.accepted
            rev = r.revision
        assert journal_length(PK, case) == 3
        tail = journal_tail(PK, case, 10)
        assert [e["payload_digest"] for e in tail] == ["d2", "d3", "d4"]


class TestReasonCodes:
    def test_revision_mismatch(self):
        case = new_case("revmismatch")
        transition(
            PK,
            case,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d1",
            action_id="a1",
        )
        r = transition(
            PK,
            case,
            expected_revision=0,
            generation=2,
            event="action_proposed",
            payload_digest="d2",
            action_id="a2",
        )
        assert r == TransitionResult(False, "REVISION_MISMATCH", 1)

    def test_stale_controller_generation_is_refused(self):
        """Race 4a: two CaseLease holders, the newer wins, the older is refused."""
        case = new_case("stalegen")
        lease_key = f"improve:{PK}:{case}:lease"
        holder = lease()
        older = holder.acquire(lease_key, ttl=90)
        assert older is not None
        assert holder.release(lease_key, older)
        newer = holder.acquire(lease_key, ttl=90)
        assert newer is not None and newer > older

        # The newer holder transitions first.
        r_newer = transition(
            PK,
            case,
            expected_revision=0,
            generation=newer,
            event="action_proposed",
            payload_digest="from-newer",
            action_id="a-newer",
        )
        assert r_newer.accepted
        length_before = journal_length(PK, case)

        # The stale (older) holder's write is refused, journal length unchanged.
        r_older = transition(
            PK,
            case,
            expected_revision=r_newer.revision,
            generation=older,
            event="action_proposed",
            payload_digest="from-older",
            action_id="a-older",
        )
        assert r_older == TransitionResult(False, "STALE_GENERATION", r_newer.revision)
        assert journal_length(PK, case) == length_before

    def test_paused_head_refuses_further_transitions(self):
        case = new_case("paused")
        transition(
            PK,
            case,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d1",
            action_id="a1",
        )
        p = transition(
            PK,
            case,
            expected_revision=1,
            generation=1,
            event="paused",
            payload_digest="operator says so",
        )
        assert p.accepted
        blocked = transition(
            PK,
            case,
            expected_revision=p.revision,
            generation=1,
            event="action_proposed",
            payload_digest="d2",
            action_id="a2",
        )
        assert blocked == TransitionResult(False, "PAUSED", p.revision)

    def test_resumed_clears_the_pause(self):
        case = new_case("resume")
        transition(
            PK,
            case,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d1",
            action_id="a1",
        )
        p = transition(
            PK,
            case,
            expected_revision=1,
            generation=1,
            event="paused",
            payload_digest="reason",
        )
        r = transition(
            PK,
            case,
            expected_revision=p.revision,
            generation=1,
            event="resumed",
            payload_digest="",
        )
        assert r.accepted
        head = read_head(PK, case)
        assert head.paused is False


class TestInvalidArgument:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {
                "expected_revision": None,
                "generation": 1,
                "event": "action_proposed",
                "payload_digest": "d",
                "action_id": "a",
            },
            {
                "expected_revision": -1,
                "generation": 1,
                "event": "action_proposed",
                "payload_digest": "d",
                "action_id": "a",
            },
            {
                "expected_revision": 0,
                "generation": -1,
                "event": "action_proposed",
                "payload_digest": "d",
                "action_id": "a",
            },
            {
                "expected_revision": 0,
                "generation": 1,
                "event": "not_a_real_event",
                "payload_digest": "d",
                "action_id": "a",
            },
            {
                "expected_revision": 0,
                "generation": 1,
                "event": "action_proposed",
                "payload_digest": "",
                "action_id": "a",
            },
            {
                "expected_revision": 0,
                "generation": 1,
                "event": "action_proposed",
                "payload_digest": "d",
                "agent_session_id": "s1",
            },  # no action_id
        ],
    )
    def test_invalid_argument_before_any_redis_call(self, kwargs):
        case = new_case("invalid")
        length_before_key = keys.journal_key(PK, case)
        assert text_redis().llen(length_before_key) == 0
        result = transition(PK, case, **kwargs)
        assert result.reason == "INVALID_ARGUMENT"
        assert result.accepted is False
        assert text_redis().llen(length_before_key) == 0


class TestLane5KnownEvents:
    """Lane 5 (#3217) writes nine case-lifecycle events through ``transition``.

    ``KNOWN_EVENTS`` is closed and ``transition`` refuses any other name
    ``INVALID_ARGUMENT`` (see ``TestInvalidArgument``), so every event the
    planner tick, the evaluation, and the unblock pass record must be
    declared here or the whole cycle is refused at its first write.
    """

    @pytest.mark.parametrize(
        "event",
        [
            "ranking_recorded",
            "case_opened",
            "evidence_attached",
            "evidence_attached_to_rejected",
            "investigation_opened",
            "hypothesis_proposed",
            "experiment_frozen",
            "verdict_applied",
            "case_unblocked",
        ],
    )
    def test_lane_5_event_is_known(self, event):
        assert event in KNOWN_EVENTS


class TestUnavailable:
    def test_connection_error_becomes_unavailable_reason_code(self, monkeypatch, caplog):
        import redis.exceptions

        from tools.improvement_control import journal as journal_module

        def boom():
            raise redis.exceptions.ConnectionError("simulated outage")

        monkeypatch.setattr(journal_module, "_control_redis", boom)
        with caplog.at_level(logging.WARNING):
            result = journal_module.transition(
                PK,
                new_case("outage"),
                expected_revision=0,
                generation=1,
                event="action_proposed",
                payload_digest="d",
                action_id="a1",
            )
        assert result == TransitionResult(False, "UNAVAILABLE", -1)
        assert any("unavailable" in rec.message for rec in caplog.records)


class TestSessionIntentBinding:
    """Race 4b's script half: the end-to-end half lives in the CLI's tests."""

    def _seed_running_intent(self, case: str, action_id: str, session_id: str) -> None:
        r = text_redis()
        r.hset(
            keys.intent_key(PK, case, action_id),
            mapping={"state": "running", "agent_session_id": session_id},
        )

    def test_wrong_intent_state_is_refused(self):
        case = new_case("wrongstate")
        transition(
            PK,
            case,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d0",
            action_id="a0",
        )
        self._seed_running_intent(case, "a1", "s1")
        text_redis().hset(keys.intent_key(PK, case, "a1"), "state", "reconciliation_required")
        r = transition(
            PK,
            case,
            expected_revision=1,
            generation=1,
            event="action_proposed",
            payload_digest="d1",
            action_id="a1",
            agent_session_id="s1",
        )
        assert r == TransitionResult(False, "INTENT_STATE", 1)
        assert text_redis().hget(keys.intent_key(PK, case, "a1"), "result_digest") is None

    def test_wrong_session_id_is_refused(self):
        case = new_case("wrongsession")
        transition(
            PK,
            case,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d0",
            action_id="a0",
        )
        self._seed_running_intent(case, "a1", "s1")
        r = transition(
            PK,
            case,
            expected_revision=1,
            generation=1,
            event="action_proposed",
            payload_digest="d1",
            action_id="a1",
            agent_session_id="s2",
        )
        assert r == TransitionResult(False, "INTENT_STATE", 1)
        assert text_redis().hget(keys.intent_key(PK, case, "a1"), "result_digest") is None

    def test_accepted_session_bound_transition_writes_result_digest(self):
        case = new_case("accepted-binding")
        transition(
            PK,
            case,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d0",
            action_id="a0",
        )
        self._seed_running_intent(case, "a1", "s1")
        r = transition(
            PK,
            case,
            expected_revision=1,
            generation=1,
            event="action_proposed",
            payload_digest="sha256:payload",
            action_id="a1",
            agent_session_id="s1",
        )
        assert r.accepted
        assert (
            text_redis().hget(keys.intent_key(PK, case, "a1"), "result_digest") == "sha256:payload"
        )
