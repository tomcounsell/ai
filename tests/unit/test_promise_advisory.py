"""Advisory promise flow (Task 14, docs/plans/durability-room-job-agentrun.md).

The promise gate is ADVISORY: on a deferral-shaped outbound it returns a
revise-or-override suggestion to the PM and performs zero writes — no
obligation model, no mechanical promise writes (Risk 4). The PM either
revises, or stands by the promise by recording it on the Job
(tools/job_tool promise-add), which the gate honors as an override on the
resend. The same outbound pass carries the goal-reset nudge while the
bound Job's goal is still the mint placeholder.

Builds on PR #2621's chokepoints (_gate_empty_promise in the drafter,
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
        assert "promise-add" in advisory
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
        Redis mutations. Any write attempt fails the test loudly."""
        session, _job = scratch_session_with_job

        def explode(*a, **k):
            raise AssertionError("advisory path must not write")

        monkeypatch.setattr(Job, "save", explode, raising=True)
        from popoto.redis_db import POPOTO_REDIS_DB

        for cmd in ("set", "rpush", "hset", "sadd", "incr", "delete", "zadd"):
            monkeypatch.setattr(POPOTO_REDIS_DB, cmd, explode, raising=True)

        advisory = build_promise_advisory("I'll follow up.", _DEFERRAL_VERDICT, session)
        assert advisory is not None
        assert not promise_override_active(session)


class TestPromiseOverride:
    def test_recorded_open_promise_overrides_the_gate(self, scratch_session_with_job):
        """The PM stood by the promise: an open promise on the bound Job
        turns the drafter gate's block into an allow on resend."""
        from bridge.message_drafter import _gate_empty_promise

        session, job = scratch_session_with_job
        deferral = "I'll report back once the deploy finishes."
        # Without a recorded promise: blocked.
        assert _gate_empty_promise(deferral, medium="telegram", session=session) is True

        job.add_promise("Report back once the deploy finishes")
        assert promise_override_active(session)
        # With the promise recorded: allowed through.
        assert _gate_empty_promise(deferral, medium="telegram", session=session) is False

    def test_discharged_promise_does_not_override(self, scratch_session_with_job):
        session, job = scratch_session_with_job
        pid = job.add_promise("Report back")
        job.remove_promise(pid)
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
        assert "promise-add" in draft.promise_advisory


class TestAtRestBackstop:
    async def test_backstop_surfaces_at_rest_jobs_with_open_promises(self, caplog):
        import logging

        from agent.session_health import _check_jobs_at_rest_with_open_promises

        rid = f"test-backstop-{uuid.uuid4().hex[:8]}|telegram:1"
        job = Job.mint(rid, "check the deploy")
        job.add_promise("I'll report back")
        job.mark_at_rest()
        try:
            with caplog.at_level(logging.WARNING):
                flagged = await _check_jobs_at_rest_with_open_promises()
            assert flagged >= 1
            assert any(job.job_id in rec.message for rec in caplog.records)
        finally:
            job.delete()

    def test_backstop_is_invoked_from_the_periodic_sweep(self):
        """No correct-logic-dead-caller: the health sweep calls the backstop."""
        import pathlib

        src = (
            pathlib.Path(__file__).resolve().parents[2] / "agent" / "session_health.py"
        ).read_text(encoding="utf-8")
        at_rest_call = src.index("await _check_at_rest_owed_communication()")
        assert "await _check_jobs_at_rest_with_open_promises()" in src[at_rest_call:]
