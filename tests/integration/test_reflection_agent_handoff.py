"""Real-Redis handoff (#3588): a reflection finding becomes an agent session.

``hand_off`` enqueues a real ``AgentSession`` scoped to the finding's Room, marks
its origin, and writes nothing to any ``telegram:outbox:*`` list: the persona
layer, not the reflection, decides whether a human hears anything.
"""

import asyncio
import json
import threading
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from reflections import agent_handoff as ah
from reflections.agent_handoff import Finding, hand_off

pytestmark = pytest.mark.integration

CHAT = "-100777"


def _cleanup(key: str) -> None:
    """Delete every test row for ``key`` through the ORM and free its handoff bindings."""
    from agent.enqueue_idempotency import release_if_bound_to
    from models.agent_session import AgentSession

    for row in AgentSession.query.filter(project_key=key):
        release_if_bound_to(f"handoff:it:{key}|telegram:{CHAT}:k1", row.agent_session_id)
        row.delete()


@pytest.fixture
def project_key(monkeypatch):
    key = f"test-handoff-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(ah, "machine_owns_project", lambda _k: True)
    monkeypatch.setattr(ah, "_live_session_in_room", lambda _r, _h: None)
    yield key
    _cleanup(key)


@pytest.fixture
def room_key(monkeypatch):
    """Like ``project_key`` but the REAL ``_live_session_in_room`` selector runs."""
    key = f"test-handoff-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(ah, "machine_owns_project", lambda _k: True)
    yield key
    _cleanup(key)


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


# --- The real steer-target selector (#3588 B1/B3) ----------------------------


def _live_row(key: str, *, origin: str | None = None, chat: str = CHAT, **extra):
    """A live eng session in the Room ``{key}|telegram:{chat}``."""
    from models.agent_session import AgentSession

    extra_context = dict(extra)
    if origin:
        extra_context["origin"] = origin
    return AgentSession.create(
        session_id=f"sel-{uuid.uuid4().hex[:8]}",
        session_type="eng",
        project_key=key,
        working_dir="/tmp",
        status="running",
        chat_id=chat,
        message_text="live",
        sender_name="tester",
        created_at=datetime.now(tz=UTC),
        extra_context=extra_context,
    )


def _finding(key: str, **kw) -> Finding:
    base = dict(
        source="it",
        project={"slug": key, "working_directory": "/tmp"},
        room_id=f"{key}|telegram:{CHAT}",
        facts=["something needs a look"],
        dedup_key="k1",
    )
    base.update(kw)
    return Finding(**base)


@pytest.fixture
def steer_spy(monkeypatch):
    """Record steers instead of delivering them; every steer 'succeeds'."""
    steered: list[str] = []
    monkeypatch.setattr(ah, "_steer", lambda s, m: steered.append(s.session_id) or True)
    return steered


@pytest.mark.parametrize("holder", [None, "pm", "dev", "no-such-session-id"])
def test_unresolvable_holder_never_steers_a_live_human_conversation(room_key, steer_spy, holder):
    """A human conversation or lane PM that merely shares the Room is not a steer target."""
    _live_row(room_key)  # a live human-conversation eng session, no handoff origin
    room = f"{room_key}|telegram:{CHAT}"
    assert ah._live_session_in_room(room, holder) is None

    result = hand_off(_finding(room_key, holder=holder))
    assert result.kind == "created", result
    assert steer_spy == []


def test_holder_match_steers_the_holder(room_key, steer_spy):
    holder_row = _live_row(room_key)
    _live_row(room_key)  # bystander
    room = f"{room_key}|telegram:{CHAT}"
    assert ah._live_session_in_room(room, holder_row.session_id).session_id == holder_row.session_id

    result = hand_off(_finding(room_key, holder=holder_row.session_id))
    assert result.kind == "steered" and result.session_id == holder_row.session_id
    assert steer_spy == [holder_row.session_id]


def test_live_handoff_row_is_steered_but_a_verbatim_row_is_not(room_key, steer_spy):
    plain = _live_row(room_key, origin=ah.HANDOFF_ORIGIN, handoff_source="other")
    digest = _live_row(
        room_key, origin=ah.HANDOFF_ORIGIN, handoff_source="digest", verbatim_payload="REPORT"
    )
    required = _live_row(
        room_key, origin=ah.HANDOFF_ORIGIN, handoff_source="ask", handoff_requires_delivery=True
    )
    room = f"{room_key}|telegram:{CHAT}"

    # The selector only ever offers the non-delivery handoff row.
    assert ah._live_session_in_room(room, None).session_id == plain.session_id
    result = hand_off(_finding(room_key))
    assert result.kind == "steered" and result.session_id == plain.session_id
    assert steer_spy == [plain.session_id]

    # With the plain row gone, a finding must not be steered into the digest row
    # (output_handler would replace the agent's reply with the digest).
    plain.delete()
    assert ah._live_session_in_room(room, None) is None
    steer_spy.clear()
    result = hand_off(_finding(room_key, dedup_key="k2"))
    assert result.kind == "created", result
    assert steer_spy == []
    assert digest.session_id and required.session_id


def test_release_if_bound_to_leaves_a_rebound_key_alone(room_key):
    from agent.enqueue_idempotency import bind, key_for, release_if_bound_to
    from utils.redis_client import text_redis

    idem = f"handoff:it:{room_key}|telegram:{CHAT}:cas"
    try:
        first, won = bind(idem)
        assert won
        assert release_if_bound_to(idem, "someone-else") is False
        assert text_redis().get(key_for(idem)) == first
        assert release_if_bound_to(idem, first) is True
        assert text_redis().get(key_for(idem)) is None
    finally:
        text_redis().delete(key_for(idem))


def test_concurrent_same_key_hand_offs_create_one_session(project_key):
    from models.agent_session import AgentSession

    finding = _finding(project_key)
    results: list = []
    barrier = threading.Barrier(4)

    def _go() -> None:
        barrier.wait()
        results.append(hand_off(finding))

    threads = [threading.Thread(target=_go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 4 and all(r.delivered for r in results), results
    assert len({r.session_id for r in results}) == 1
    assert len(list(AgentSession.query.filter(project_key=project_key))) == 1


# --- Verbatim delivery through the real digest and outbox ----------------------


def test_digest_is_delivered_byte_exact_whatever_the_agent_says(project_key):
    """A paraphrased agent reply delivers the real rendered digest, closing line
    included, byte-exact to ``telegram:outbox:<session>``."""
    from agent.output_handler import TelegramRelayOutputHandler
    from reflections.improvement_assumption_digest import (
        CLOSING_LINE,
        DigestInputs,
        render_digest,
    )
    from utils.redis_client import text_redis

    digest = render_digest(
        DigestInputs(
            project_key=project_key,
            since=None,
            resources_acquired=[
                {
                    "title": "Sentry token",
                    "fingerprint": "abc123",
                    "created_at": datetime.now(tz=UTC),
                }
            ],
        ),
        now=datetime.now(tz=UTC),
    )
    assert digest.endswith(CLOSING_LINE)
    result = hand_off(
        _finding(
            project_key,
            source="it",
            verbatim_payload=digest,
            requires_delivery=True,
        )
    )
    assert result.kind == "created", result

    from models.agent_session import AgentSession

    row = AgentSession.get_by_id(result.session_id)
    outbox = f"telegram:outbox:{row.session_id}"
    handler = TelegramRelayOutputHandler()
    try:
        with patch(
            "bridge.context_recall.check_outbound_context_recall",
            return_value=SimpleNamespace(advised=False, reason=""),
        ):
            asyncio.run(handler.send(CHAT, "Sending the report, in my own words.", 0, session=row))
        items = [json.loads(i) for i in text_redis().lrange(outbox, 0, -1)]
    finally:
        text_redis().delete(outbox)
    assert len(items) == 1
    assert items[0]["text"] == digest
    assert items[0]["text"].endswith(CLOSING_LINE)
