"""PM Job tools: create / author goal / promise add+remove (Tasks 13-14, #2494).

Room scope is enforced AT THE TOOL LAYER, not in the system prompt
(prompt-level constraints drift — see the teammate-permissions precedent):
every Job lookup is scoped to the calling session's own Room, so a Job in
another Room is structurally unreachable and the tool refuses loudly.
"""

import uuid
from datetime import UTC, datetime

import pytest

from models.agent_session import AgentSession
from models.job import Job
from models.room import room_id as make_room_id
from tools.job_tool import (
    JobToolError,
    add_promise,
    author_goal,
    create_job,
    remove_promise,
)


@pytest.fixture
def scratch_session():
    """A test- prefixed AgentSession with a Telegram chat (its own Room)."""
    key = f"test-jobtool-{uuid.uuid4().hex[:8]}"
    session = AgentSession.create(
        session_id=f"tg_{key}_42_1",
        project_key=key,
        status="active",
        chat_id="42",
        message_text="x",
        working_dir="/tmp",
        created_at=datetime.now(tz=UTC),
    )
    rid = make_room_id(key, "telegram:42")
    yield session, rid
    for job in Job.query.filter(room_id=rid):
        job.delete()
    session.delete()


class TestCreateJob:
    def test_creates_pm_authored_job_in_own_room(self, scratch_session):
        session, rid = scratch_session
        job = create_job(session.session_id, "Ship the durability milestone")

        assert job.room_id == rid
        assert job.current_goal() == "Ship the durability milestone"
        assert not job.goal_is_placeholder()
        assert job.goal_versions()[0]["author"] == "pm"

    def test_unknown_session_refused(self):
        with pytest.raises(JobToolError):
            create_job("no-such-session", "goal")


class TestRoomScope:
    def test_cross_room_job_is_unreachable(self, scratch_session):
        session, _rid = scratch_session
        other_rid = f"test-jobtool-other-{uuid.uuid4().hex[:8]}|telegram:9"
        foreign = Job.mint(other_rid, "someone else's work")
        try:
            with pytest.raises(JobToolError):
                add_promise(session.session_id, foreign.job_id, "I'll report back")
            with pytest.raises(JobToolError):
                author_goal(session.session_id, foreign.job_id, "hijacked goal")
        finally:
            foreign.delete()

    def test_own_room_job_is_reachable(self, scratch_session):
        session, _rid = scratch_session
        job = create_job(session.session_id, "Own-room job")

        pid = add_promise(session.session_id, job.job_id, "I'll report back at 5pm")
        assert pid
        assert remove_promise(session.session_id, job.job_id, pid) is True

    def test_author_goal_appends_pm_version(self, scratch_session):
        session, _rid = scratch_session
        job = create_job(session.session_id, "v1 goal")
        author_goal(session.session_id, job.job_id, "v2 goal, sharper")

        fresh = Job.query.filter(room_id=job.room_id, id=job.job_id)[0]
        assert fresh.current_goal() == "v2 goal, sharper"
        assert len(fresh.goal_versions()) == 2
