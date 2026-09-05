"""Advisory expectation flow (#2494 Task 14, generalized by #2708).

The promise gate is ADVISORY: on a deferral-shaped outbound it returns a
revise-or-override suggestion to the PM and performs zero writes — no
mechanical obligation writes from any verdict. The PM either revises, or
stands by the obligation by recording an INBOUND expectation on the Job
(tools/job_tool expectation-add --direction inbound), which the gate
honors as an override on the resend. Outbound expectations (what a lane
owes the PM) never clear the gate. The same outbound pass carries the
goal-reset nudge while the bound Job's goal is still the mint placeholder.

Builds on PR #2621's chokepoints (_evaluate_drafter_promise in the drafter,
_gate_terminal_promise in session_health) — extended, not duplicated.
"""

import uuid
from datetime import UTC, datetime

import pytest

from bridge.promise_gate import (
    PromiseVerdict,
    build_promise_advisory,
    promise_override_active,
)
from models.agent_session import AgentSession
from models.job import Job
from models.room import room_id as make_room_id


@pytest.fixture
def scratch_session_with_job():
    """A test- session whose trigger message is bound to a Job."""
    from bridge.job_router import bind_message_to_job, telegram_message_key

    key = f"test-advisory-{uuid.uuid4().hex[:8]}"
    chat_id = "77"
    msg_id = 5
    session = AgentSession.create(
        session_id=f"tg_{key}_{chat_id}_{msg_id}",
        project_key=key,
        status="active",
        chat_id=chat_id,
        message_text="x",
        working_dir="/tmp",
        created_at=datetime.now(tz=UTC),
    )
    rid = make_room_id(key, f"telegram:{chat_id}")
    job = Job.mint(rid, "check the deploy status")
    message_key = telegram_message_key(chat_id, msg_id)
    bind_message_to_job(message_key, job.job_id, room_id=rid)

    yield session, job

    from popoto.redis_db import POPOTO_REDIS_DB

    POPOTO_REDIS_DB.delete(f"reply:{message_key}")
    for j in Job.query.filter(room_id=rid):
        j.delete()
    session.delete()


_DEFERRAL_VERDICT = PromiseVerdict(
    action="block",
    reason="Forward-deferral without verifiable scheduled-delivery reference",
    class_="forward_deferral",
)


class TestJobForSession:
    def test_resolves_bound_job_from_session(self, scratch_session_with_job):
        from bridge.job_router import job_for_session

        session, job = scratch_session_with_job
        resolved = job_for_session(session)
        assert resolved is not None
        assert resolved.job_id == job.job_id

    def test_unbound_session_resolves_none(self):
        from bridge.job_router import job_for_session

        class _S:
            session_id = "local-abc"
            chat_id = None

        assert job_for_session(_S()) is None


class TestAdvisoryContent:
    def test_advisory_is_revise_or_override(self, scratch_session_with_job):
        session, job = scratch_session_with_job
        advisory = build_promise_advisory(
            "I'll report back once the deploy finishes.",
            _DEFERRAL_VERDICT,
            session,
        )
        assert advisory is not None
        # Suggestion, not a command: names both legs of revise-or-override.
        assert "revise" in advisory.lower()
        assert "expectation-add" in advisory
        assert job.job_id in advisory

    def test_goal_placeholder_nudge_present_when_placeholder(self, scratch_session_with_job):
        session, job = scratch_session_with_job
        assert job.goal_is_placeholder()
        advisory = build_promise_advisory("I'll follow up.", _DEFERRAL_VERDICT, session)
        assert "author-goal" in advisory

    def test_goal_nudge_absent_when_goal_authored(self, scratch_session_with_job):
        session, job = scratch_session_with_job
        job.append_goal_version("Deliver the deploy check end to end", author="pm")
        advisory = build_promise_advisory("I'll follow up.", _DEFERRAL_VERDICT, session)
        assert "author-goal" not in advisory

    def test_advisory_without_bound_job_still_advises_revision(self):
        class _S:
            session_id = "local-abc"
            chat_id = None

        advisory = build_promise_advisory("I'll follow up.", _DEFERRAL_VERDICT, _S())
        assert advisory is not None
        assert "revise" in advisory.lower()


class TestZeroWrites:
    def test_advisory_performs_zero_writes(self, scratch_session_with_job, monkeypatch):
        """Risk 4: the advisory path never writes — not Job.save, not raw
        Redis mutations, not INDEX_SWAP_LUA (eval/evalsha/register_script),
        not write commands inside a pipeline (how popoto actually writes).
        Any write attempt fails the test loudly. Pipelines themselves stay
        constructible because popoto READS via pipeline too — only their
        write commands are poisoned."""
        session, _job = scratch_session_with_job

        def explode(*a, **k):
            raise AssertionError("advisory path must not write")

        monkeypatch.setattr(Job, "save", explode, raising=True)
        from popoto.redis_db import POPOTO_REDIS_DB

        write_cmds = (
            "set",
            "rpush",
            "lpush",
            "hset",
            "hdel",
            "sadd",
            "srem",
            "incr",
            "delete",
            "zadd",
            "zrem",
            "expire",
        )
        for cmd in write_cmds:
            monkeypatch.setattr(POPOTO_REDIS_DB, cmd, explode, raising=True)
        for cmd in ("eval", "evalsha", "register_script"):
            monkeypatch.setattr(POPOTO_REDIS_DB, cmd, explode, raising=True)

        real_pipeline = POPOTO_REDIS_DB.pipeline

        def guarded_pipeline(*args, **kwargs):
            pipe = real_pipeline(*args, **kwargs)
            for cmd in (*write_cmds, "eval", "evalsha"):
                setattr(pipe, cmd, explode)
            return pipe

        monkeypatch.setattr(POPOTO_REDIS_DB, "pipeline", guarded_pipeline)

        advisory = build_promise_advisory("I'll follow up.", _DEFERRAL_VERDICT, session)
        assert advisory is not None
        assert not promise_override_active(session)


class TestPromiseOverride:
    def test_recorded_open_inbound_expectation_overrides_the_gate(self, scratch_session_with_job):
        """The PM stood by the obligation: an open inbound expectation on
        the bound Job turns the drafter gate's block into an allow on
        resend. The override is JOB-scoped by design — any open inbound
        expectation on the bound Job clears the gate until discharge."""
        from bridge.message_drafter import _evaluate_drafter_promise

        session, job = scratch_session_with_job
        deferral = "I'll report back once the deploy finishes."
        # Without a recorded expectation: blocked.
        assert _evaluate_drafter_promise(deferral, medium="telegram", session=session).action == (
            "block"
        )

        job.add_expectation("Report back once the deploy finishes")
        assert promise_override_active(session)
        # With the expectation recorded: allowed through, with the audit reason.
        verdict = _evaluate_drafter_promise(deferral, medium="telegram", session=session)
        assert verdict.action == "allow"
        assert verdict.reason == "promise_recorded_override"

    def test_discharged_expectation_does_not_override(self, scratch_session_with_job):
        session, job = scratch_session_with_job
        eid = job.add_expectation("Report back")
        job.discharge_expectation(eid)
        assert not promise_override_active(session)

    def test_outbound_expectation_never_clears_the_gate(self, scratch_session_with_job):
        """A spawned lane's obligation (including the spawn chokepoint's
        mechanical null-fallback) says nothing about what we owe the
        requester — it must not clear the honesty gate."""
        session, job = scratch_session_with_job
        job.add_expectation(
            "lane delivers the fix", direction="outbound", owner="session/some-lane"
        )
        assert not promise_override_active(session)


class TestDrafterCarriesAdvisory:
    async def test_blocked_draft_carries_the_advisory(self, scratch_session_with_job):
        from bridge.message_drafter import draft_message

        session, _job = scratch_session_with_job
        draft = await draft_message(
            "I'll report back soon.",
            session=session,
            medium="telegram",
        )
        assert draft.needs_self_draft is True
        assert draft.promise_advisory
        assert "expectation-add" in draft.promise_advisory


class TestAtRestInvariantAlarm:
    async def test_forced_violation_fires_the_alarm(self, caplog):
        """The intersection is an invariant-violation alarm now: force the
        impossible state (direct mark_at_rest bypasses the goal chokepoint)
        and assert it is surfaced."""
        import logging

        from agent.session_health import _check_jobs_at_rest_with_open_expectations

        rid = f"test-backstop-{uuid.uuid4().hex[:8]}|telegram:1"
        job = Job.mint(rid, "check the deploy")
        job.add_expectation("I'll report back")
        job.mark_at_rest()  # forced drift: chokepoint never allows this
        try:
            with caplog.at_level(logging.WARNING):
                flagged = await _check_jobs_at_rest_with_open_expectations()
            assert flagged >= 1
            assert any(job.job_id in rec.message for rec in caplog.records)
        finally:
            job.delete()

    async def test_corrupt_goal_at_rest_is_named_not_counted_as_zero(self, caplog):
        """#2862: an at-rest Job whose goal no longer decodes is retained by
        the intersection (its flag is the last known truth) and the alarm
        names the corruption instead of reporting zero open expectations."""
        import logging

        from agent.session_health import _check_jobs_at_rest_with_open_expectations

        rid = f"test-backstop-corrupt-{uuid.uuid4().hex[:8]}|telegram:1"
        job = Job.mint(rid, "check the deploy")
        job.add_expectation("I'll report back")
        job.goal = '{"versions": [{"ts": "2026-08-01T00:00:00+00:00"'
        job.save()
        job.mark_at_rest()
        try:
            with caplog.at_level(logging.ERROR):
                flagged = await _check_jobs_at_rest_with_open_expectations()
            assert flagged >= 1
            ours = [r for r in caplog.records if job.job_id in r.message and "CORRUPT" in r.message]
            assert ours, "the alarm must name the corruption"
            assert not any(
                job.job_id in r.message and "0 open expectation" in r.message
                for r in caplog.records
            )
        finally:
            job.delete()

    async def test_steady_state_is_empty_and_open_expectation_blocks_rest(self, caplog):
        """Steady state: the sweep runs from the same invocation (rest-by-age
        end-to-end) but an open expectation PINS the Job active, so the
        flagged set stays empty — the invariant holds by construction."""
        import logging
        import time
        from datetime import UTC, datetime

        from agent.session_health import _check_jobs_at_rest_with_open_expectations
        from models.job import JOB_AT_REST_AGE_SECONDS

        rid = f"test-backstop-e2e-{uuid.uuid4().hex[:8]}|telegram:1"
        job = Job.mint(rid, "check the deploy")
        job.add_expectation("I'll report back")
        job.last_active_at = datetime.fromtimestamp(
            time.time() - JOB_AT_REST_AGE_SECONDS - 60, tz=UTC
        )
        job.save()
        assert job.status == "active"
        try:
            with caplog.at_level(logging.WARNING):
                await _check_jobs_at_rest_with_open_expectations()
            fresh = Job.query.filter(room_id=rid)[0]
            # The open expectation pinned it active — never swept to rest,
            # so it can never enter the flagged intersection.
            assert fresh.status == "active"
            assert not any(job.job_id in rec.message for rec in caplog.records)
        finally:
            job.delete()

    async def test_sweep_still_rests_expectation_less_jobs(self):
        """The renamed check remains the Job.sweep_to_rest() caller: an idle
        Job with no expectations still ages to rest through it."""
        import time
        from datetime import UTC, datetime

        from agent.session_health import _check_jobs_at_rest_with_open_expectations
        from models.job import JOB_AT_REST_AGE_SECONDS

        rid = f"test-backstop-sweep-{uuid.uuid4().hex[:8]}|telegram:1"
        job = Job.mint(rid, "idle thread")
        job.last_active_at = datetime.fromtimestamp(
            time.time() - JOB_AT_REST_AGE_SECONDS - 60, tz=UTC
        )
        job.save()
        try:
            await _check_jobs_at_rest_with_open_expectations()
            fresh = Job.query.filter(room_id=rid)[0]
            assert fresh.status == "at-rest"
        finally:
            job.delete()

    def test_backstop_is_invoked_from_the_periodic_sweep(self):
        """No correct-logic-dead-caller: the health sweep calls the alarm
        (sweep-then-scan ordering preserved)."""
        import pathlib

        src = (
            pathlib.Path(__file__).resolve().parents[2] / "agent" / "session_health.py"
        ).read_text(encoding="utf-8")
        at_rest_call = src.index("await _check_at_rest_owed_communication()")
        assert "await _check_jobs_at_rest_with_open_expectations()" in src[at_rest_call:]
