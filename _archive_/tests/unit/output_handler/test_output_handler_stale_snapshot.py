"""Red-first regression test for the stale-snapshot lifecycle clobber (#3270, A3a).

**A bare ``save()`` on an AgentSession is a lifecycle write.** ``status`` is an
``IndexedField`` (``models/agent_session.py``), and popoto's
``save(update_fields=None)`` path encodes the *entire* instance into one HSET
and runs ``on_save()`` for every field. So any code that reads a row, mutates
one unrelated field, and calls a bare ``save()`` is silently authorized to
rewrite the session's lifecycle state from a stale snapshot -- with no
LIFECYCLE log line and no ``session_events`` entry to show for it.

``agent/output_handler.py`` had two such writers on the incident path:

* ``_persist_routing_fields`` -- writes ``context_summary``
* ``_rtr_emit_event``        -- writes ``session_events``

Both are narrowed to ``save(update_fields=[...])``. This test encodes the rule
rather than the site count: it is parametrized over both call sites because the
field sets differ, so a test written against one proves nothing about the other.

Real Redis (per-worker test db via the autouse ``redis_test_db`` fixture) and
real ``AgentSession`` rows. Session ids use the ``test-oh-stale-`` prefix.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent.output_handler import TelegramRelayOutputHandler
from models.agent_session import AgentSession

SID_PREFIX = "test-oh-stale-"


@pytest.fixture
def cleanup(redis_test_db):
    created: list[str] = []
    yield created
    for sid in created:
        try:
            for rec in list(AgentSession.query.filter(session_id=sid)):
                rec.delete()
        except Exception:
            pass


def _make_session(session_id: str) -> AgentSession:
    now = datetime.now(tz=UTC)
    return AgentSession.create(
        session_id=session_id,
        session_type="teammate",
        project_key="test-oh-stale",
        status="running",
        chat_id="test-oh-stale-chat",
        telegram_message_id=99,
        sender_name="TestUser",
        message_text="hello",
        created_at=now,
        started_at=now,
        updated_at=now,
    )


def _invoke_persist_routing_fields(handler, session):
    """Drive the production ``_persist_routing_fields`` write path."""

    class _Draft:
        context_summary = "routing summary written from a stale snapshot"

    handler._persist_routing_fields(session, _Draft())


def _invoke_rtr_emit_event(handler, session):
    """Drive the production ``_rtr_emit_event`` write path."""
    handler._rtr_emit_event(
        session,
        "rtr.suppressed",
        chat_id="test-oh-stale-chat",
        draft_text="a draft the room did not need",
        reason="stale-snapshot regression",
    )


@pytest.mark.parametrize(
    ("invoke", "persisted_field"),
    [
        pytest.param(
            _invoke_persist_routing_fields, "context_summary", id="persist_routing_fields"
        ),
        pytest.param(_invoke_rtr_emit_event, "session_events", id="rtr_emit_event"),
    ],
)
def test_stale_snapshot_cannot_clobber_a_concurrently_written_status(
    cleanup, invoke, persisted_field
):
    """A stale in-memory copy must not rewrite ``status`` through these writers.

    Sequence: load a row, hold a stale in-memory copy, let a *different*
    writer move the row's status in Redis, then drive the production save path
    on the stale copy. The status in Redis must be unchanged, and the writer's
    own field must still have landed.

    Red before the fix: the bare ``save()`` HSETs the whole stale instance, so
    ``status`` reverts to the snapshot's value and the ``status`` index follows
    it -- silently, with no lifecycle log and no ``session_events`` entry.
    """
    sid = f"{SID_PREFIX}{persisted_field}"
    cleanup.append(sid)
    _make_session(sid)

    # The stale caller's snapshot: read while the row still says "running".
    stale = next(iter(AgentSession.query.filter(session_id=sid)))
    assert stale.status == "running", "precondition: snapshot captured at running"

    # A concurrent lifecycle write moves the row on. Narrow by construction so
    # the only candidate clobber in this test is the one under examination.
    concurrent = next(iter(AgentSession.query.filter(session_id=sid)))
    concurrent.status = "completed"
    concurrent.save(update_fields=["status"])

    handler = TelegramRelayOutputHandler()
    invoke(handler, stale)

    after = next(iter(AgentSession.query.filter(session_id=sid)))
    assert after.status == "completed", (
        f"a bare save() in the {persisted_field!r} writer resurrected the stale "
        f"snapshot's status -- got {after.status!r}, expected 'completed'. A bare "
        "save() on an AgentSession is a lifecycle write."
    )
    assert getattr(after, persisted_field, None), (
        f"the narrowed save must still persist {persisted_field!r}"
    )
