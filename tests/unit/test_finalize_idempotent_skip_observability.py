"""Red-first observability test for ``finalize_session``'s idempotent skip (#3270, A2).

``finalize_session`` treats "already in this terminal state" as success and
returns silently. In the #3270 incident that silence is what stranded the
session: a stale full save had already written ``completed`` onto a row whose
harness was still mid-turn, so the real turn-end finalize hit the idempotency
skip, never set ``completed_at``, never emitted a lifecycle event -- and a later
stale save put the row back to ``running``, where the orphan net found it. The
chain ran for days with nothing above DEBUG to show for it.

A skip that coincides with a LIVE execution fence is the anomalous case: the
row is already terminal while the runner that owns it is still alive. That is
the turn-end race, and it now logs at WARNING naming the session id, the status
found on the authoritative record, and the status requested.

**Observability only.** The idempotency semantics are unchanged and out of
scope here (#3253) -- the skip still returns without side effects.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from models.agent_session import AgentSession
from models.session_lifecycle import finalize_session

SID_PREFIX = "test-idem-warn-"


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


def _make_terminal_session(session_id: str, *, with_fence: bool) -> AgentSession:
    now = datetime.now(tz=UTC)
    kwargs: dict = {}
    if with_fence:
        # Our own pid, with its real create_time, is a fence that resolves live.
        kwargs["exec_pid"] = os.getpid()
        kwargs["pid_create_time"] = _own_create_time()
    return AgentSession.create(
        session_id=session_id,
        session_type="teammate",
        project_key="test-idem-warn",
        status="completed",
        chat_id="test-idem-warn-chat",
        sender_name="TestUser",
        message_text="hello",
        created_at=now,
        started_at=now,
        updated_at=now,
        **kwargs,
    )


def _own_create_time() -> float:
    from agent.pid_fence import proc_create_time

    return proc_create_time(os.getpid())


def test_idempotent_skip_with_a_live_fence_warns(cleanup, caplog):
    """A turn-end finalize skipped as idempotent while its runner is alive WARNs.

    Red before the fix: the skip logs at DEBUG, so the strand is invisible.
    """
    sid = f"{SID_PREFIX}live-fence"
    cleanup.append(sid)
    session = _make_terminal_session(sid, with_fence=True)

    with caplog.at_level(logging.DEBUG, logger="models.session_lifecycle"):
        finalize_session(session, "completed", reason="transcript completed: completed")

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING and sid in r.getMessage()]
    assert warnings, (
        "an idempotent skip racing a live runner fence must be visible at WARNING; "
        f"got only {[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )
    msg = warnings[0].getMessage()
    assert "completed" in msg, "the message must name the status found and requested"

    # Observability only: the skip still returns without side effects (#3253).
    assert session.completed_at is None, "the idempotency semantics must not change"


def test_idempotent_skip_without_a_live_fence_does_not_warn(cleanup, caplog):
    """No bound runner pid means an ordinary double-finalize -- no WARNING."""
    sid = f"{SID_PREFIX}no-fence"
    cleanup.append(sid)
    session = _make_terminal_session(sid, with_fence=False)

    with caplog.at_level(logging.DEBUG, logger="models.session_lifecycle"):
        finalize_session(session, "completed", reason="ordinary double finalize")

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING and sid in r.getMessage()]
    assert not warnings, (
        "a routine double-finalize with no live runner must stay below WARNING; "
        f"got {[r.getMessage() for r in warnings]}"
    )


def test_idempotent_skip_with_a_dead_fence_does_not_warn(cleanup, caplog):
    """A bound but DEAD/recycled pid is residue, not a racing runner."""
    sid = f"{SID_PREFIX}dead-fence"
    cleanup.append(sid)
    session = _make_terminal_session(sid, with_fence=True)

    with (
        patch("agent.pid_fence.fence_is_live", return_value=False),
        caplog.at_level(logging.DEBUG, logger="models.session_lifecycle"),
    ):
        finalize_session(session, "completed", reason="dead fence residue")

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING and sid in r.getMessage()]
    assert not warnings, (
        "a dead or recycled fence pid is not a racing runner; "
        f"got {[r.getMessage() for r in warnings]}"
    )
