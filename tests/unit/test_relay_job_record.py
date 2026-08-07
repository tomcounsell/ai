"""Relay-side durable records (Task 13/14, #2494): reactions + outbound bindings.

Spike-2 found sent reactions had NO durable record anywhere. Owner ruling:
no new subsystem — a sent reaction is recorded as a reply-to message in the
existing message log with escaped, parseable content
(``<reaction>…</reaction>``), at the relay's send-success site.

The permanent ``message_id → job_id`` reply index is written for OUTBOUND
messages too, so a user reply to Valor's message routes to the same Job
with no model call.
"""

import uuid
from datetime import UTC, datetime

import pytest

from models.telegram import TelegramMessage


@pytest.fixture
def scratch_chat_id():
    chat_id = f"-100{uuid.uuid4().int % 10**8}"
    yield chat_id
    for msg in TelegramMessage.query.filter(chat_id=chat_id):
        msg.delete()


class TestReactionRecord:
    def test_sent_reaction_recorded_as_reply_to_message(self, scratch_chat_id):
        from bridge.telegram_relay import _record_sent_reaction

        _record_sent_reaction({"chat_id": scratch_chat_id, "reply_to": 314, "emoji": "👀"})

        msgs = list(TelegramMessage.query.filter(chat_id=scratch_chat_id))
        assert len(msgs) == 1
        record = msgs[0]
        assert record.content == "<reaction>👀</reaction>"
        assert record.reply_to_msg_id == 314
        assert record.direction == "out"
        assert record.message_type == "reaction"

    def test_record_failure_never_raises(self, monkeypatch):
        from bridge import telegram_relay

        def explode(**kwargs):
            raise RuntimeError("log store down")

        monkeypatch.setattr("tools.telegram_history.store_message", explode)
        # Malformed payload + broken store: still no exception.
        telegram_relay._record_sent_reaction({"chat_id": None, "reply_to": None, "emoji": None})


class TestOutboundJobBinding:
    def test_outbound_message_binds_to_the_sessions_job(self):
        from popoto.redis_db import POPOTO_REDIS_DB

        from bridge.job_router import (
            bind_message_to_job,
            lookup_job_for_message,
            telegram_message_key,
        )
        from bridge.telegram_relay import _bind_outbound_message_to_job
        from models.agent_session import AgentSession
        from models.job import Job
        from models.room import room_id as make_room_id

        key = f"test-outbind-{uuid.uuid4().hex[:8]}"
        chat_id = "55"
        session = AgentSession.create(
            session_id=f"tg_{key}_{chat_id}_9",
            project_key=key,
            status="active",
            chat_id=chat_id,
            message_text="x",
            working_dir="/tmp",
            created_at=datetime.now(tz=UTC),
        )
        rid = make_room_id(key, f"telegram:{chat_id}")
        job = Job.mint(rid, "the work")
        inbound_key = telegram_message_key(chat_id, 9)
        outbound_key = telegram_message_key(chat_id, 500)
        bind_message_to_job(inbound_key, job.job_id, room_id=rid)
        try:
            _bind_outbound_message_to_job(
                {"session_id": session.session_id, "chat_id": chat_id}, 500
            )
            assert lookup_job_for_message(outbound_key) == (job.job_id, rid)
        finally:
            POPOTO_REDIS_DB.delete(f"reply:{inbound_key}", f"reply:{outbound_key}")
            job.delete()
            session.delete()

    def test_unbound_session_is_a_noop_and_never_raises(self):
        from bridge.telegram_relay import _bind_outbound_message_to_job

        _bind_outbound_message_to_job({"session_id": "no-such", "chat_id": "1"}, 1)
        _bind_outbound_message_to_job({}, None)

    def test_relay_success_path_invokes_both_records(self):
        """No correct-logic-dead-caller: the relay actually calls both."""
        import pathlib

        src = (
            pathlib.Path(__file__).resolve().parents[2] / "bridge" / "telegram_relay.py"
        ).read_text(encoding="utf-8")
        assert src.count("_record_sent_reaction") >= 2  # def + call site
        assert src.count("_bind_outbound_message_to_job") >= 2  # def + call site
