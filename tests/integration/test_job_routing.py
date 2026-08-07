"""Integration: inbound flow through the Job router + PM Room scope (Task 13).

Covers the target ordering's route/bind leg against real Redis:
message → bind-or-mint → permanent reply index → reply-to rebind, and the
Agent Integration requirement that a PM can create a Job in its own Room
and is refused for another Room (enforced at the tool layer).

No live granite: the classifier leg is monkeypatched; everything else is
real (Job model, reply index, AgentSession).
"""

import uuid
from datetime import UTC, datetime

import pytest

from bridge.job_router import (
    lookup_job_for_message,
    route_message,
    telegram_message_key,
)
from models.job import Job


@pytest.fixture
def scratch_room_id():
    rid = f"test-jobflow-{uuid.uuid4().hex[:8]}|telegram:7"
    yield rid
    for job in Job.query.filter(room_id=rid):
        job.delete()


@pytest.fixture
def clean_reply_keys():
    keys: list[str] = []
    yield keys
    from popoto.redis_db import POPOTO_REDIS_DB

    for key in keys:
        POPOTO_REDIS_DB.delete(f"reply:{key}")


class TestInboundFlow:
    async def test_message_then_reply_lands_on_the_same_job(
        self, monkeypatch, scratch_room_id, clean_reply_keys
    ):
        """First message mints; a reply to it binds via the permanent index
        with no model call — the reply-resume leg of one-code-path routing."""

        async def exploding(prompt, output_type, **kwargs):
            raise AssertionError("model must not be called in this flow")

        monkeypatch.setattr("bridge.job_router.run_typed_local", exploding)

        chat_id = 7
        first_key = telegram_message_key(chat_id, 101)
        reply_key = telegram_message_key(chat_id, 102)
        clean_reply_keys.extend([first_key, reply_key])

        job = await route_message(scratch_room_id, "please fix the flaky test", first_key)
        assert job.goal_is_placeholder()

        follow_up = await route_message(
            scratch_room_id,
            "any progress?",
            reply_key,
            reply_to_message_key=first_key,
        )

        assert follow_up.job_id == job.job_id
        assert lookup_job_for_message(reply_key) == (job.job_id, scratch_room_id)
        # Exactly one Job exists — the reply revived, not re-minted.
        assert len(list(Job.query.filter(room_id=scratch_room_id))) == 1

    async def test_steer_revives_an_at_rest_job(
        self, monkeypatch, scratch_room_id, clean_reply_keys
    ):
        """A Job that went to rest by age is revived by a reply regardless
        of age — the never-hard-closed contract."""

        async def exploding(prompt, output_type, **kwargs):
            raise AssertionError("model must not be called in this flow")

        monkeypatch.setattr("bridge.job_router.run_typed_local", exploding)

        chat_id = 7
        first_key = telegram_message_key(chat_id, 201)
        reply_key = telegram_message_key(chat_id, 202)
        clean_reply_keys.extend([first_key, reply_key])

        job = await route_message(scratch_room_id, "deploy the fix", first_key)
        job.mark_at_rest()

        revived = await route_message(
            scratch_room_id,
            "actually one more thing",
            reply_key,
            reply_to_message_key=first_key,
        )

        assert revived.job_id == job.job_id
        assert revived.status == "active"


class TestPMRoomScope:
    def test_pm_creates_in_own_room_and_is_refused_for_another(self):
        from models.agent_session import AgentSession
        from models.room import room_id as make_room_id
        from tools.job_tool import JobToolError, add_promise, create_job

        key = f"test-jobflow-pm-{uuid.uuid4().hex[:8]}"
        session = AgentSession.create(
            session_id=f"tg_{key}_11_1",
            project_key=key,
            status="active",
            chat_id="11",
            message_text="x",
            working_dir="/tmp",
            created_at=datetime.now(tz=UTC),
        )
        own_rid = make_room_id(key, "telegram:11")
        foreign = Job.mint(f"{key}-other|telegram:99", "foreign work")
        try:
            job = create_job(session.session_id, "Own-room durable goal")
            assert job.room_id == own_rid

            with pytest.raises(JobToolError):
                add_promise(session.session_id, foreign.job_id, "I'll report back")
        finally:
            foreign.delete()
            for job in Job.query.filter(room_id=own_rid):
                job.delete()
            session.delete()
