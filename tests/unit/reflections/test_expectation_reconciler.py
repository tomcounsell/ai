"""Expectation reconciler (#2708): orphaned-lane recovery from Job expectations.

Mirrors tests/unit/reflections/test_sdlc_progress_check.py's posture: every
external boundary is faked via monkeypatch; failures log-and-continue; the
shipped-work guard is the sole respawn collision guard.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest

import reflections.expectation_reconciler as er
from models.job import Job

pytestmark = pytest.mark.unit


def _project(key: str) -> dict:
    return {"slug": key, "working_directory": "/tmp"}


def _mint_job_with_outbound(
    rid: str, owner: str, what: str = "deliver the fix", *, age_hours: float = 2.0
) -> tuple[Job, str]:
    """A Job carrying one open outbound expectation, backdated past min-age."""
    import json

    job = Job.mint(rid, "ship it end to end")
    eid = job.add_expectation(what, direction="outbound", owner=owner)
    data = json.loads(job.goal)
    for entry in data["expectations"]:
        if entry["id"] == eid:
            entry["ts"] = (datetime.now(tz=UTC) - timedelta(hours=age_hours)).isoformat()
    job._write_goal_data(data)
    return job, eid


@pytest.fixture
def owned_project(monkeypatch):
    """Machine owns the project; jobs scoped to a scratch room."""
    key = f"test-reconcile-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(er, "machine_owns_project", lambda _k: True)
    yield key
    for j in Job.query.filter(room_id=f"{key}|telegram:1"):
        j.delete()


class _NoSubprocess:
    def __call__(self, *args, **kwargs):  # pragma: no cover - failure surface
        raise AssertionError(f"subprocess spawned on a no-op tick: {args}")


class TestAttemptsTtlFloor:
    def test_attempts_ttl_floored_at_escalation_ttl(self, monkeypatch):
        """Risk 2: max(configured, escalation_ttl) — never merely > cadence."""
        monkeypatch.setenv("EXPECTATION_ATTEMPTS_TTL_DAYS", "1")
        monkeypatch.setenv("EXPECTATION_ESCALATION_TTL_DAYS", "30")
        assert er._attempts_ttl_seconds() == er._escalation_ttl_seconds()

    def test_attempts_ttl_keeps_larger_configured_value(self, monkeypatch):
        monkeypatch.setenv("EXPECTATION_ATTEMPTS_TTL_DAYS", "60")
        monkeypatch.setenv("EXPECTATION_ESCALATION_TTL_DAYS", "30")
        assert er._attempts_ttl_seconds() == 60 * 86400


class TestNoOpTick:
    def test_zero_open_expectations_spawns_no_subprocess(self, owned_project, monkeypatch):
        monkeypatch.setattr(er.subprocess, "run", _NoSubprocess())
        result = er._reconcile_project(_project(owned_project))
        assert result["status"] == "ok"
        assert "0 job(s)" in result["summary"]

    def test_disabled_kill_switch_skips(self, owned_project, monkeypatch):
        monkeypatch.setenv("EXPECTATION_RECONCILER_ENABLED", "false")
        result = er._reconcile_project(_project(owned_project))
        assert result["status"] == "skipped"

    def test_fresh_expectation_is_left_alone(self, owned_project, monkeypatch):
        """Min-age gate: a just-spawned lane's expectation is not acted on."""
        rid = f"{owned_project}|telegram:1"
        _mint_job_with_outbound(rid, "lane-fresh", age_hours=0.0)
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o: True)
        monkeypatch.setattr(er.subprocess, "run", _NoSubprocess())
        result = er._reconcile_project(_project(owned_project))
        assert result["status"] == "ok"
        assert not any("steered" in f or "respawned" in f for f in result["findings"])

    def test_live_owner_row_blocks_action(self, owned_project, monkeypatch):
        """A row claiming life is respawn-blocking (never discharge evidence)."""
        rid = f"{owned_project}|telegram:1"
        _mint_job_with_outbound(rid, "lane-live")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o: False)
        monkeypatch.setattr(er.subprocess, "run", _NoSubprocess())
        result = er._reconcile_project(_project(owned_project))
        assert result["findings"] == []


class TestShippedWorkGuard:
    def test_shipped_work_is_steered_never_respawned(self, owned_project, monkeypatch):
        rid = f"{owned_project}|telegram:1"
        job, eid = _mint_job_with_outbound(rid, "session/shipped-lane")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o: True)
        monkeypatch.setattr(er, "_shipped_evidence", lambda _wd, _s: "PR #42 (merged)")
        steered: list[str] = []
        monkeypatch.setattr(er, "_live_pm_session", lambda _k, _h: object())
        monkeypatch.setattr(er, "_steer", lambda _s, m: steered.append(m) or True)
        monkeypatch.setattr(
            er, "_respawn_lane", lambda *a, **k: pytest.fail("respawned shipped work")
        )
        result = er._reconcile_project(_project(owned_project))
        assert any("steered-evidence" in f for f in result["findings"])
        assert len(steered) == 1
        assert "PR #42" in steered[0]
        assert "expectation-remove" in steered[0]
        # Never discharged mechanically: the expectation is still open.
        fresh = Job.query.get(id=job.id, room_id=rid)
        assert any(e["id"] == eid for e in fresh.open_expectations(direction="outbound"))

    def test_unshipped_orphan_respawns_when_no_pm(self, owned_project, monkeypatch):
        rid = f"{owned_project}|telegram:1"
        _mint_job_with_outbound(rid, "session/dead-lane", what="deliver the migration PR")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o: True)
        monkeypatch.setattr(er, "_shipped_evidence", lambda _wd, _s: None)
        monkeypatch.setattr(er, "_live_pm_session", lambda _k, _h: None)
        respawns: list[tuple] = []
        monkeypatch.setattr(
            er, "_respawn_lane", lambda pk, slug, what, jid: respawns.append((slug, what)) or True
        )
        result = er._reconcile_project(_project(owned_project))
        assert any("respawned" in f for f in result["findings"])
        assert respawns == [("dead-lane", "deliver the migration PR")]

    def test_unshipped_orphan_prefers_steering_a_live_pm(self, owned_project, monkeypatch):
        rid = f"{owned_project}|telegram:1"
        _mint_job_with_outbound(rid, "session/dead-lane")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o: True)
        monkeypatch.setattr(er, "_shipped_evidence", lambda _wd, _s: None)
        monkeypatch.setattr(er, "_live_pm_session", lambda _k, _h: object())
        steered: list[str] = []
        monkeypatch.setattr(er, "_steer", lambda _s, m: steered.append(m) or True)
        monkeypatch.setattr(
            er, "_respawn_lane", lambda *a, **k: pytest.fail("respawned despite live PM")
        )
        result = er._reconcile_project(_project(owned_project))
        assert any("steered-orphan" in f for f in result["findings"])
        assert len(steered) == 1


class TestLadderBookkeeping:
    def test_discharged_between_scan_and_act_is_a_no_op(self, owned_project, monkeypatch):
        """Race 3: re-fetch before acting; the PM's discharge wins."""
        rid = f"{owned_project}|telegram:1"
        job, eid = _mint_job_with_outbound(rid, "session/raced-lane")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o: True)
        original_claim = er._cooldown_claim

        def claim_then_discharge(job_id, e):
            job.discharge_expectation(eid)
            return original_claim(job_id, e)

        monkeypatch.setattr(er, "_cooldown_claim", claim_then_discharge)
        monkeypatch.setattr(
            er, "_shipped_evidence", lambda *_a: pytest.fail("acted on a discharged expectation")
        )
        result = er._reconcile_project(_project(owned_project))
        assert not any("steered" in f or "respawned" in f for f in result["findings"])

    def test_attempt_cap_escalates_once_then_stops(self, owned_project, monkeypatch):
        rid = f"{owned_project}|telegram:1"
        job, eid = _mint_job_with_outbound(rid, "session/capped-lane")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o: True)
        monkeypatch.setattr(er, "_attempts_count", lambda _j, _e: er._max_attempts())
        pages: list[str] = []
        monkeypatch.setattr(
            er,
            "_escalate_once",
            lambda j, e, m: (pages.append(m) or True) if len(pages) == 0 else False,
        )
        first = er._reconcile_project(_project(owned_project))
        assert any("escalated" in f for f in first["findings"])
        second = er._reconcile_project(_project(owned_project))
        assert not any("escalated" in f for f in second["findings"])
        assert len(pages) == 1

    def test_per_expectation_failure_logs_and_continues(self, owned_project, monkeypatch, caplog):
        """Every except in the loop: warn and keep going, never raise."""
        rid = f"{owned_project}|telegram:1"
        _mint_job_with_outbound(rid, "session/exploding-lane")

        def boom(_o):
            raise RuntimeError("owner query exploded")

        monkeypatch.setattr(er, "_owner_is_gone", boom)
        with caplog.at_level(logging.WARNING):
            result = er._reconcile_project(_project(owned_project))
        assert result["status"] == "ok"
        assert any("per-expectation pass failed" in r.message for r in caplog.records)

    def test_unknown_owner_liveness_declines(self, owned_project, monkeypatch):
        rid = f"{owned_project}|telegram:1"
        _mint_job_with_outbound(rid, "session/unknown-lane")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o: None)
        monkeypatch.setattr(er.subprocess, "run", _NoSubprocess())
        result = er._reconcile_project(_project(owned_project))
        assert any("gate-unknown: owner-liveness" in f for f in result["findings"])


class TestDriftAdvisory:
    """#2708 Risks 1 & 4 backstop in agent/session_health.py: advisory only."""

    @pytest.fixture
    def pm_with_child(self):
        from bridge.job_router import bind_message_to_job, telegram_message_key
        from models.agent_session import AgentSession
        from models.room import room_id as make_room_id

        key = f"test-drift-{uuid.uuid4().hex[:8]}"
        chat_id = "66"
        msg_id = 3
        parent = AgentSession.create(
            session_id=f"tg_{key}_{chat_id}_{msg_id}",
            project_key=key,
            status="active",
            session_type="eng",
            chat_id=chat_id,
            message_text="x",
            working_dir="/tmp",
            created_at=datetime.now(tz=UTC),
        )
        child = AgentSession.create(
            session_id=f"{chat_id}_{uuid.uuid4().hex[:10]}",
            project_key=key,
            status="active",
            session_type="eng",
            slug="drift-lane",
            parent_agent_session_id=parent.session_id,
            message_text="y",
            working_dir="/tmp",
            created_at=datetime.now(tz=UTC),
        )
        rid = make_room_id(key, f"telegram:{chat_id}")
        job = Job.mint(rid, "ship the thing")
        message_key = telegram_message_key(chat_id, msg_id)
        bind_message_to_job(message_key, job.job_id, room_id=rid)

        yield parent, child, job

        from popoto.redis_db import POPOTO_REDIS_DB

        POPOTO_REDIS_DB.delete(f"reply:{message_key}")
        for j in Job.query.filter(room_id=rid):
            j.delete()
        parent.delete()
        child.delete()

    async def test_fires_on_uncovered_child(self, pm_with_child, caplog):
        from agent.session_health import _check_expectation_drift_advisory

        _parent, child, job = pm_with_child
        with caplog.at_level(logging.WARNING):
            flagged = await _check_expectation_drift_advisory()
        assert flagged >= 1
        messages = [r.getMessage() for r in caplog.records]
        assert any(
            "expectation-drift" in m and child.session_id in m and job.job_id in m for m in messages
        )

    async def test_silent_when_child_is_covered(self, pm_with_child, caplog):
        from agent.session_health import _check_expectation_drift_advisory

        _parent, child, job = pm_with_child
        job.add_expectation("lane delivers it", direction="outbound", owner=child.slug)
        with caplog.at_level(logging.WARNING):
            await _check_expectation_drift_advisory()
        assert not any(
            "expectation-drift" in m and child.session_id in m
            for m in (r.getMessage() for r in caplog.records)
        )

    async def test_advisory_makes_no_writes(self, pm_with_child):
        from agent.session_health import _check_expectation_drift_advisory

        _parent, _child, job = pm_with_child
        before = job.goal
        await _check_expectation_drift_advisory()
        fresh = Job.query.get(id=job.id, room_id=job.room_id)
        assert fresh.goal == before
