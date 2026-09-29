"""Real-Redis handoff (#3588): a reflection finding becomes an agent session.

``hand_off`` enqueues a real ``AgentSession`` scoped to the finding's Room, marks
its origin, and writes nothing to any ``telegram:outbox:*`` list: the persona
layer, not the reflection, decides whether a human hears anything.
"""

import uuid

import pytest

from reflections import agent_handoff as ah
from reflections.agent_handoff import Finding, hand_off

pytestmark = pytest.mark.integration

CHAT = "-100777"


@pytest.fixture
def project_key(monkeypatch):
    key = f"test-handoff-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(ah, "machine_owns_project", lambda _k: True)
    monkeypatch.setattr(ah, "_live_session_in_room", lambda _r, _h: None)
    yield key
    from agent.enqueue_idempotency import release_if_bound_to
    from models.agent_session import AgentSession

    for row in AgentSession.query.filter(project_key=key):
        release_if_bound_to(f"handoff:it:{key}|telegram:{CHAT}:k1", row.agent_session_id)
        row.delete()


def _outbox_keys() -> set[str]:
    from popoto.redis_db import POPOTO_REDIS_DB

    return {
        k.decode() if isinstance(k, bytes) else k for k in POPOTO_REDIS_DB.keys("telegram:outbox:*")
    }


def test_handoff_enqueues_marked_session_without_a_chat_write(project_key):
    from models.agent_session import AgentSession

    before = _outbox_keys()
    finding = Finding(
        source="it",
        project={"slug": project_key, "working_directory": "/tmp"},
        room_id=f"{project_key}|telegram:{CHAT}",
        facts=["PR 42 merged but the issue is still open"],
        dedup_key="k1",
        evidence={"pr_number": 42},
    )
    result = hand_off(finding)
    assert result.kind == "created", result
    row = AgentSession.get_by_id(result.session_id)
    assert row is not None
    extra = row.extra_context or {}
    assert extra.get("origin") == ah.HANDOFF_ORIGIN
    assert extra.get("handoff_source") == "it"
    assert str(row.chat_id) == CHAT
    assert "PR 42 merged" in (row.message_text or "")
    assert _outbox_keys() == before

    # Same dedup key: the bound live session is returned, no second row.
    again = hand_off(finding)
    assert again.delivered and again.session_id == result.session_id
    assert len(list(AgentSession.query.filter(project_key=project_key))) == 1
