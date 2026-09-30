"""Spawn-chokepoint outbound expectation (#2708, owner ruling 2026-08-13).

The session-create core records what a spawned lane owes back on the PM's
bound Job — as a null-fallback only (PM-authored is canonical). Covered
here: the per-lane trigger, provenance-derived placeholder marker, the
Job-resolution chain (explicit job_id → ancestor walk → skip-and-log),
and the never-blocks-creation contract.
"""

import uuid
from datetime import UTC, datetime

import pytest

from models.agent_session import AgentSession
from models.job import Job
from models.room import room_id as make_room_id
from tools.valor_session import _record_spawn_expectation, _resolve_spawn_job


@pytest.fixture
def pm_session_with_job():
    """A test- PM session whose trigger message is bound to a Job."""
    from bridge.job_router import bind_message_to_job, telegram_message_key

    key = f"test-spawn-{uuid.uuid4().hex[:8]}"
    chat_id = "88"
    msg_id = 9
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
    job = Job.mint(rid, "ship the fix end to end")
    message_key = telegram_message_key(chat_id, msg_id)
    bind_message_to_job(message_key, job.job_id, room_id=rid)

    yield session, job

    from popoto.redis_db import POPOTO_REDIS_DB

    POPOTO_REDIS_DB.delete(f"reply:{message_key}")
    for j in Job.query.filter(room_id=rid):
        j.delete()
    session.delete()


def _fresh(job: Job) -> Job:
    return Job.query.get(id=job.id, room_id=job.room_id)


class TestJobResolutionChain:
    def test_explicit_job_id_wins(self, pm_session_with_job):
        _session, job = pm_session_with_job
        resolved = _resolve_spawn_job(job.job_id, None)
        assert resolved is not None
        assert resolved.job_id == job.job_id

    def test_ancestor_walk_resolves_through_the_parent(self, pm_session_with_job):
        session, job = pm_session_with_job
        resolved = _resolve_spawn_job(None, session.session_id)
        assert resolved is not None
        assert resolved.job_id == job.job_id

    def test_no_resolution_returns_none(self):
        assert _resolve_spawn_job(None, None) is None
        assert _resolve_spawn_job(None, "no-such-session-anywhere") is None


class TestNullFallbackWrite:
    def test_mechanical_fallback_is_marked_placeholder(self, pm_session_with_job):
        """No --expect-what ⇒ mechanical summary, placeholder=True (the PM
        prime's refine nudge keys on it — provenance-derived)."""
        session, job = pm_session_with_job
        note = _record_spawn_expectation(
            lane_owner="session/some-lane",
            message="fix issue #999 end to end with tests",
            parent_id=session.session_id,
            job_id=None,
            expect_what=None,
        )
        assert note is not None
        entries = _fresh(job).open_expectations(direction="outbound")
        assert len(entries) == 1
        entry = entries[0]
        assert entry["owner"] == "session/some-lane"
        assert entry["holder"] == session.session_id
        assert entry["placeholder"] is True
        assert entry["what"].startswith("lane delivers: ")
        assert "fix issue #999" in entry["what"]

    def test_pm_authored_expect_what_is_not_placeholder(self, pm_session_with_job):
        """--expect-what present ⇒ deliberately authored: recorded verbatim,
        placeholder=False so the refine nudge skips it."""
        session, job = pm_session_with_job
        _record_spawn_expectation(
            lane_owner="session/some-lane",
            message="fix issue #999",
            parent_id=session.session_id,
            job_id=None,
            expect_what="lane delivers the merged PR for #999",
        )
        entry = _fresh(job).open_expectations(direction="outbound")[0]
        assert entry["what"] == "lane delivers the merged PR for #999"
        assert entry["placeholder"] is False

    def test_per_lane_trigger_not_job_wide(self, pm_session_with_job):
        """An existing open outbound expectation suppresses the fallback ONLY
        for its own lane — a second lane still gets recorded (a Job-wide
        any-exists check would reproduce the #2494 silent-loss shape)."""
        session, job = pm_session_with_job
        job.add_expectation("lane one delivers the fix", direction="outbound", owner="lane-1")

        # Same lane: fallback declines (PM-authored entry is canonical).
        assert (
            _record_spawn_expectation(
                lane_owner="lane-1",
                message="spawn lane one",
                parent_id=session.session_id,
                job_id=None,
                expect_what=None,
            )
            is None
        )
        # Different lane: fallback fires.
        assert (
            _record_spawn_expectation(
                lane_owner="lane-2",
                message="spawn lane two",
                parent_id=session.session_id,
                job_id=None,
                expect_what=None,
            )
            is not None
        )
        owners = {e["owner"] for e in _fresh(job).open_expectations(direction="outbound")}
        assert owners == {"lane-1", "lane-2"}

    def test_unresolved_job_skips_and_never_raises(self):
        """Job resolution is best-effort: no Job ⇒ skip-and-log, never an
        exception (creation must never block; drift advisory backstops)."""
        assert (
            _record_spawn_expectation(
                lane_owner="lane-x",
                message="do a thing",
                parent_id=None,
                job_id=None,
                expect_what=None,
            )
            is None
        )

    def test_expectation_surfaces_through_job_tool_show(self, pm_session_with_job):
        """End to end through the PM's own surface: the recorded outbound
        expectation appears in job_tool's Job summary."""
        from tools.job_tool import _job_summary

        session, job = pm_session_with_job
        _record_spawn_expectation(
            lane_owner="session/shown-lane",
            message="ship it",
            parent_id=session.session_id,
            job_id=None,
            expect_what=None,
        )
        summary = _job_summary(_fresh(job))
        owners = {e["owner"] for e in summary["open_expectations"]}
        assert "session/shown-lane" in owners
