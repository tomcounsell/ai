"""PM Job tools: create / author goal / expectation add+remove (#2708).

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
    add_expectation,
    author_goal,
    create_job,
    remove_expectation,
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
            with pytest.raises(JobToolError, match="not found in your Room"):
                add_expectation(
                    session.session_id,
                    foreign.job_id,
                    "I'll report back",
                    direction="inbound",
                    owner="pm",
                )
            with pytest.raises(JobToolError):
                author_goal(session.session_id, foreign.job_id, "hijacked goal")
        finally:
            foreign.delete()

    def test_own_room_job_is_reachable(self, scratch_session):
        session, _rid = scratch_session
        job = create_job(session.session_id, "Own-room job")

        eid = add_expectation(
            session.session_id,
            job.job_id,
            "I'll report back at 5pm",
            direction="inbound",
            owner="pm",
        )
        assert eid
        assert remove_expectation(session.session_id, job.job_id, eid) is True

    def test_author_goal_appends_pm_version(self, scratch_session):
        session, _rid = scratch_session
        job = create_job(session.session_id, "v1 goal")
        author_goal(session.session_id, job.job_id, "v2 goal, sharper")

        fresh = Job.query.filter(room_id=job.room_id, id=job.job_id)[0]
        assert fresh.current_goal() == "v2 goal, sharper"
        assert len(fresh.goal_versions()) == 2


class TestExpectations:
    def test_outbound_expectation_names_the_lane_that_owes_it(self, scratch_session):
        session, _rid = scratch_session
        job = create_job(session.session_id, "Ship the reconciler")

        add_expectation(
            session.session_id,
            job.job_id,
            "the migration PR",
            direction="outbound",
            owner="session/job-expectations",
        )

        fresh = Job.query.filter(room_id=job.room_id, id=job.job_id)[0]
        entry = fresh.open_expectations()[0]
        assert entry["direction"] == "outbound"
        assert entry["owner"] == "session/job-expectations"
        assert entry["what"] == "the migration PR"
        # PM-authored through the tool is never a mechanical placeholder.
        assert entry["placeholder"] is False

    def test_discharged_expectation_leaves_the_open_set(self, scratch_session):
        session, _rid = scratch_session
        job = create_job(session.session_id, "Ship the reconciler")
        eid = add_expectation(
            session.session_id, job.job_id, "I'll report back", direction="inbound", owner="pm"
        )

        assert remove_expectation(session.session_id, job.job_id, eid) is True

        fresh = Job.query.filter(room_id=job.room_id, id=job.job_id)[0]
        assert fresh.open_expectations() == []
        assert len(fresh.all_expectations()) == 1  # history survives

    def test_unknown_expectation_id_reports_false_not_success(self, scratch_session):
        """The CLI turns this into an actionable non-zero exit rather than
        silently claiming a discharge."""
        session, _rid = scratch_session
        job = create_job(session.session_id, "Ship the reconciler")

        assert remove_expectation(session.session_id, job.job_id, "no-such-id") is False

    @pytest.mark.parametrize(
        ("kwargs", "fragment"),
        [
            ({"text": "  ", "direction": "inbound", "owner": "pm"}, "text"),
            ({"text": "deliver", "direction": "outbound", "owner": " "}, "owner"),
            ({"text": "deliver", "direction": "sideways", "owner": "pm"}, "direction"),
        ],
    )
    def test_unusable_expectation_is_refused_loudly(self, scratch_session, kwargs, fragment):
        session, _rid = scratch_session
        job = create_job(session.session_id, "Ship the reconciler")

        with pytest.raises(JobToolError, match=fragment):
            add_expectation(session.session_id, job.job_id, **kwargs)

        fresh = Job.query.filter(room_id=job.room_id, id=job.job_id)[0]
        assert fresh.open_expectations() == []
