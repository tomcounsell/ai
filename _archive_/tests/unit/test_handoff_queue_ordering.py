"""A pending low-priority reflection handoff never holds a human message (#3588).

Real Redis (test DB): the handoff session and a human message sit in the same
project queue; the pop returns the human message first whatever the arrival order.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from agent.session_pickup import _pop_agent_session
from config.enums import REFLECTION_HANDOFF_ORIGIN
from models.agent_session import AgentSession


def test_pending_low_priority_handoff_does_not_hold_a_human_message():
    pk = f"test-handoff-order-{uuid.uuid4().hex[:8]}"
    now = datetime.now(tz=UTC)
    try:
        handoff = AgentSession.create(
            session_id=f"handoff-{uuid.uuid4().hex[:8]}",
            session_type="eng",
            project_key=pk,
            working_dir="/tmp",
            status="pending",
            priority="low",
            chat_id="-100555",
            message_text="finding",
            sender_name="reflection (t)",
            created_at=now - timedelta(minutes=5),
            extra_context={"origin": REFLECTION_HANDOFF_ORIGIN},
        )
        human = AgentSession.create(
            session_id=f"human-{uuid.uuid4().hex[:8]}",
            session_type="eng",
            project_key=pk,
            working_dir="/tmp",
            status="pending",
            priority="normal",
            chat_id="-100555",
            message_text="hello",
            sender_name="human",
            created_at=now,
        )
        first = asyncio.run(_pop_agent_session(pk, is_project_keyed=True))
        assert first is not None and first.session_id == human.session_id
        assert handoff.session_id != first.session_id
    finally:
        for row in AgentSession.query.filter(project_key=pk):
            row.delete()
