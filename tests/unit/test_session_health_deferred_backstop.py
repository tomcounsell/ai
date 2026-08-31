"""Tests for the deferred self-draft terminal backstop sweep (#3053).

``agent.session_health._sweep_stranded_deferred_self_drafts`` is the third,
independent layer that closes any path bypassing the hoisted
``finalize_session`` chokepoint flush -- including the three last-resort
``"failed"`` bypass writes in ``agent/session_executor.py`` and any future
bypass. It scans ``DEFERRED_FLUSH_BACKSTOP_STATUSES`` (``completed``,
``failed``, ``abandoned`` -- never ``killed``/``cancelled``, per the
delivery-posture principle) for rows still carrying
``deferred_self_draft_pending=True`` within a bounded ``completed_at``
lookback window, and delegates delivery to ``flush_deferred_self_draft_sync``
(no second delivery implementation).

Real Redis (autouse ``redis_test_db`` fixture), real ``AgentSession`` ORM
records. Session ids use the ``test-backstop-`` prefix and are cleaned up in
teardown.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import pytest

from agent import session_health
from agent.session_health import _sweep_stranded_deferred_self_drafts
from models.agent_session import AgentSession

ORIGINAL_REPLY = (
    "I committed the card and opened the pull request as requested. "
    "The change is ready for your review whenever you have a moment."
)

SID_PREFIX = "test-backstop-"


def _redis():
    import popoto.redis_db as rdb

    return rdb.POPOTO_REDIS_DB


def _outbox_payloads(session_id: str) -> list[dict]:
    raw = _redis().lrange(f"telegram:outbox:{session_id}", 0, -1)
    out = []
    for item in raw:
        if isinstance(item, bytes):
            item = item.decode("utf-8")
        out.append(json.loads(item))
    return out


def _outbox_count(session_id: str) -> int:
    return _redis().llen(f"telegram:outbox:{session_id}")


def _make_terminal_session(
    session_id: str,
    *,
    status: str = "completed",
    pending: bool = True,
    text: str | None = ORIGINAL_REPLY,
    completed_at: float | None = None,
    transport: str | None = "telegram",
) -> AgentSession:
    extra_context: dict = {}
    if transport is not None:
        extra_context["transport"] = transport
    if pending:
        extra_context["deferred_self_draft_pending"] = True
        extra_context["deferred_self_draft_text"] = text if text is not None else ""

    kwargs = dict(
        session_id=session_id,
        session_type="eng",
        project_key="test-backstop",
        status=status,
        chat_id="12345",
        telegram_message_id=263,
        sender_name="TestUser",
        message_text="commit the card and open a PR",
        extra_context=extra_context,
        created_at=datetime.now(tz=UTC),
        started_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        turn_count=1,
        tool_call_count=0,
    )
    if completed_at is not None:
        kwargs["completed_at"] = completed_at
    return AgentSession.create(**kwargs)


@pytest.fixture
def cleanup(redis_test_db):
    created: list[str] = []
    yield created
    r = _redis()
    for sid in created:
        run_ids: list[str] = []
        try:
            for rec in list(AgentSession.query.filter(session_id=sid)):
                run_ids.append(str(getattr(rec, "id", "") or ""))
                rec.delete()
        except Exception:
            pass
        try:
            r.delete(f"telegram:outbox:{sid}")
            for rid in {*run_ids, ""}:
                r.delete(f"self_draft_completed_flush_sent:{sid}:{rid}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Basic hit: a stranded completed session gets delivered, WARNING logged,
# counter incremented.
# ---------------------------------------------------------------------------


def test_sweep_delivers_stranded_completed_session(cleanup, caplog):
    sid = f"{SID_PREFIX}basic-hit"
    cleanup.append(sid)
    _make_terminal_session(sid, status="completed", completed_at=time.time())

    with caplog.at_level("WARNING", logger="agent.session_health"):
        _sweep_stranded_deferred_self_drafts()

    payloads = _outbox_payloads(sid)
    assert len(payloads) == 1
    assert payloads[0]["text"] == ORIGINAL_REPLY
    assert any(
        "backstop sweep hit" in rec.message
        for rec in caplog.records
        if rec.name == "agent.session_health"
    )

    fresh = list(AgentSession.query.filter(session_id=sid))[0]
    assert not fresh.extra_context.get("deferred_self_draft_pending"), (
        "a delivered row must leave the sweep predicate (flag cleared)"
    )


def test_sweep_increments_backstop_hits_counter(cleanup):
    sid = f"{SID_PREFIX}counter"
    cleanup.append(sid)
    session = _make_terminal_session(sid, status="failed", completed_at=time.time())
    project_key = session.project_key
    counter_key = f"{project_key}:session-health:deferred_flush_backstop_hits"
    _redis().delete(counter_key)

    _sweep_stranded_deferred_self_drafts()

    assert int(_redis().get(counter_key) or 0) >= 1


# ---------------------------------------------------------------------------
# Delivery-posture: killed/cancelled are never swept.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["killed", "cancelled"])
def test_sweep_never_delivers_killed_or_cancelled(cleanup, status):
    sid = f"{SID_PREFIX}posture-{status}"
    cleanup.append(sid)
    _make_terminal_session(sid, status=status, completed_at=time.time())

    _sweep_stranded_deferred_self_drafts()

    assert _outbox_count(sid) == 0, (
        f"the sweep must never deliver a held reply on a {status} session "
        "(delivery-posture principle: a human called it off)"
    )


# ---------------------------------------------------------------------------
# Lookback window boundary
# ---------------------------------------------------------------------------


def test_sweep_skips_row_outside_lookback_window(cleanup, monkeypatch):
    monkeypatch.setattr(session_health, "DEFERRED_FLUSH_BACKSTOP_LOOKBACK_SECONDS", 100)
    sid = f"{SID_PREFIX}outside-window"
    cleanup.append(sid)
    _make_terminal_session(sid, status="completed", completed_at=time.time() - 1000)

    _sweep_stranded_deferred_self_drafts()

    assert _outbox_count(sid) == 0, "a row past the lookback window must be skipped, not delivered"


def test_sweep_delivers_row_inside_lookback_window(cleanup, monkeypatch):
    monkeypatch.setattr(session_health, "DEFERRED_FLUSH_BACKSTOP_LOOKBACK_SECONDS", 3600)
    sid = f"{SID_PREFIX}inside-window"
    cleanup.append(sid)
    _make_terminal_session(sid, status="completed", completed_at=time.time() - 100)

    _sweep_stranded_deferred_self_drafts()

    assert _outbox_count(sid) == 1


# ---------------------------------------------------------------------------
# Anchorless row (completed_at=None): acted on exactly once, then drops out
# of the predicate on the next tick (flush's own clear, not the window,
# bounds it).
# ---------------------------------------------------------------------------


def test_sweep_acts_on_anchorless_row_exactly_once(cleanup, monkeypatch):
    monkeypatch.setattr(session_health, "DEFERRED_FLUSH_BACKSTOP_LOOKBACK_SECONDS", 3600)
    sid = f"{SID_PREFIX}anchorless"
    cleanup.append(sid)
    # completed_at intentionally omitted -> None (legacy row / pre-Task-1 writer).
    _make_terminal_session(sid, status="completed", completed_at=None)
    fresh = list(AgentSession.query.filter(session_id=sid))[0]
    assert fresh.completed_at is None

    _sweep_stranded_deferred_self_drafts()

    assert _outbox_count(sid) == 1, "an anchorless row must be acted on, not permanently skipped"

    # Second tick: the flush cleared the pending flag on the first hit, so the
    # row drops out of the predicate -- it must NOT be treated as
    # always-in-range and re-delivered.
    _sweep_stranded_deferred_self_drafts()

    assert _outbox_count(sid) == 1, (
        "an anchorless row must not be permanently re-swept once delivered"
    )


# ---------------------------------------------------------------------------
# Concurrent chokepoint-flush + sweep-flush: exactly once (Race 1)
# ---------------------------------------------------------------------------


def test_chokepoint_and_sweep_flush_same_session_exactly_once(cleanup):
    """The chokepoint flush and a sweep flush targeting the SAME session are
    both covered by the flush's own SETNX dedup -- exactly one outbox write."""
    from models.session_lifecycle import finalize_session

    sid = f"{SID_PREFIX}race1"
    cleanup.append(sid)
    session = _make_terminal_session(sid, status="running", completed_at=None)

    # Chokepoint flush via the real finalize_session call.
    finalize_session(session, "completed", reason="race1 chokepoint")
    assert _outbox_count(sid) == 1

    # A sweep tick immediately after must not double-deliver -- the flag is
    # already cleared, so the sweep's own predicate excludes this row.
    _sweep_stranded_deferred_self_drafts()

    assert _outbox_count(sid) == 1, (
        "chokepoint flush + sweep flush on the same session must deliver exactly once"
    )


# ---------------------------------------------------------------------------
# Bounded work: per-tick cap
# ---------------------------------------------------------------------------


def test_sweep_caps_rows_per_tick(cleanup, monkeypatch, caplog):
    monkeypatch.setattr(session_health, "DEFERRED_FLUSH_BACKSTOP_MAX_ROWS_PER_TICK", 2)
    sids = [f"{SID_PREFIX}cap-{i}" for i in range(4)]
    for sid in sids:
        cleanup.append(sid)
        _make_terminal_session(sid, status="completed", completed_at=time.time())

    with caplog.at_level("WARNING", logger="agent.session_health"):
        _sweep_stranded_deferred_self_drafts()

    delivered = sum(_outbox_count(sid) for sid in sids)
    assert delivered == 2, "the sweep must stop at its per-tick cap"
    assert any(
        "per-tick cap" in rec.message
        for rec in caplog.records
        if rec.name == "agent.session_health"
    )

    # The remaining rows are still pending and will be picked up on a later tick.
    monkeypatch.setattr(session_health, "DEFERRED_FLUSH_BACKSTOP_MAX_ROWS_PER_TICK", 10)
    _sweep_stranded_deferred_self_drafts()
    delivered_total = sum(_outbox_count(sid) for sid in sids)
    assert delivered_total == 4, "a later tick must pick up rows the cap deferred"


# ---------------------------------------------------------------------------
# Robustness: one failing row never aborts the sweep
# ---------------------------------------------------------------------------


def test_one_failing_row_does_not_abort_the_sweep(cleanup, monkeypatch):
    sid_bad = f"{SID_PREFIX}bad-row"
    sid_good = f"{SID_PREFIX}good-row"
    cleanup.append(sid_bad)
    cleanup.append(sid_good)
    _make_terminal_session(sid_bad, status="completed", completed_at=time.time())
    _make_terminal_session(sid_good, status="completed", completed_at=time.time())

    original_flush = session_health.flush_deferred_self_draft_sync

    def _flaky_flush(entry, status=None):
        if getattr(entry, "session_id", None) == sid_bad:
            raise RuntimeError("simulated per-row failure")
        return original_flush(entry, status)

    monkeypatch.setattr(session_health, "flush_deferred_self_draft_sync", _flaky_flush)

    _sweep_stranded_deferred_self_drafts()

    assert _outbox_count(sid_good) == 1, "a failing row must not abort delivery for other rows"


# ---------------------------------------------------------------------------
# No cross-session double-scan: a session already delivered via chokepoint
# and NOT still pending is left alone by the sweep.
# ---------------------------------------------------------------------------


def test_sweep_ignores_non_pending_terminal_sessions(cleanup):
    sid = f"{SID_PREFIX}not-pending"
    cleanup.append(sid)
    _make_terminal_session(sid, status="completed", pending=False, completed_at=time.time())

    _sweep_stranded_deferred_self_drafts()

    assert _outbox_count(sid) == 0
