"""Regression + failure-path tests for the deferred self-draft completed-path flush.

Issue #1794: a session that defers a reply for self-draft and then reaches a
terminal status (``completed``, ``failed``, ``abandoned``) without redrafting
must flush the held text to the human exactly once, on EVERY terminal path,
via the single ``finalize_session`` chokepoint.

The chokepoint invokes the synchronous helper
``agent.session_health.flush_deferred_self_draft_sync(session)`` (placed in
``models.session_lifecycle.finalize_session`` AFTER the idempotency early-return
and the ``reject_from_terminal`` guard, BEFORE ``session.save()``). The helper:

  * reads the deferral flags from a FRESH authoritative session,
  * gates telegram-only (``transport == "email"`` early-returns),
  * SETNX-dedups on ``self_draft_completed_flush_sent:{session_id}`` (1 h),
  * applies the narration gate + empty-text canned notice,
  * writes a telegram outbox payload via ``rpush`` to
    ``telegram:outbox:{session_id}``.

The async email-only helper ``_deliver_deferred_self_draft_fallback`` early-returns
for telegram/None transport and dedups on a DISTINCT key
``self_draft_fallback_sent:{session_id}``.

These tests use REAL Redis (the autouse ``redis_test_db`` fixture switches popoto
to a per-worker test db), create REAL ``AgentSession`` records via the ORM, and
assert on the actual outbox payload body. Test session_ids use the
``test-dsd-completed-`` prefix and are cleaned up in teardown.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from models.agent_session import AgentSession
from models.session_lifecycle import finalize_session

# A substantive (non-narration, no file-path / URL / code-fence) reply body.
# is_narration_only() must return False for this so it is delivered VERBATIM.
ORIGINAL_REPLY = (
    "I committed the card and opened the pull request as requested. "
    "The change is ready for your review whenever you have a moment."
)

CANNED_NOTICE = "I couldn't finish responding to that — please try again."

SID_PREFIX = "test-dsd-completed-"


def _redis():
    """Return the live (test-db) Redis handle the helper writes through.

    The autouse ``redis_test_db`` fixture rebinds ``popoto.redis_db.POPOTO_REDIS_DB``
    to a per-worker test client; the helper imports it lazily at call time, so
    reading the same attribute here yields the exact handle it wrote to.
    """
    import popoto.redis_db as rdb

    return rdb.POPOTO_REDIS_DB


def _outbox_payloads(session_id: str) -> list[dict]:
    """Return the decoded payloads in ``telegram:outbox:{session_id}`` (FIFO order)."""
    raw = _redis().lrange(f"telegram:outbox:{session_id}", 0, -1)
    out = []
    for item in raw:
        if isinstance(item, bytes):
            item = item.decode("utf-8")
        out.append(json.loads(item))
    return out


def _outbox_count(session_id: str) -> int:
    return _redis().llen(f"telegram:outbox:{session_id}")


def _make_session(
    session_id: str,
    *,
    pending: bool = True,
    text: str | None = ORIGINAL_REPLY,
    transport: str | None = "telegram",
    status: str = "running",
    chat_id: str = "12345",
    telegram_message_id: int = 263,
) -> AgentSession:
    """Create and SAVE a real running AgentSession with deferral flags.

    The session MUST be saved so the helper's authoritative re-read
    (``get_authoritative_session``) sees the deferral flags.
    """
    extra_context: dict = {}
    if transport is not None:
        extra_context["transport"] = transport
    if pending:
        extra_context["deferred_self_draft_pending"] = True
        extra_context["deferred_self_draft_text"] = text if text is not None else ""

    return AgentSession.create(
        session_id=session_id,
        session_type="eng",
        project_key="test-dsd",
        status=status,
        chat_id=chat_id,
        telegram_message_id=telegram_message_id,
        sender_name="TestUser",
        message_text="commit the card and open a PR",
        extra_context=extra_context,
        created_at=datetime.now(tz=UTC),
        started_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        turn_count=1,
        tool_call_count=0,
    )


@pytest.fixture
def cleanup(redis_test_db):
    """Track created session_ids; delete records + dedup keys + outbox in teardown."""
    created: list[str] = []
    yield created
    r = _redis()
    for sid in created:
        try:
            for rec in list(AgentSession.query.filter(session_id=sid)):
                rec.delete()
        except Exception:
            pass
        try:
            r.delete(f"telegram:outbox:{sid}")
            r.delete(f"self_draft_completed_flush_sent:{sid}")
            r.delete(f"self_draft_fallback_sent:{sid}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 1. Completed-path delivery, payload content asserted
# ---------------------------------------------------------------------------


def test_completed_path_delivers_held_text_verbatim_exactly_once(cleanup):
    """A telegram deferral reaching ``completed`` via finalize_session delivers the
    held text VERBATIM, exactly once, to the session's outbox."""
    sid = f"{SID_PREFIX}deliver-once"
    cleanup.append(sid)
    session = _make_session(sid, text=ORIGINAL_REPLY)

    finalize_session(session, "completed", reason="test completed")

    payloads = _outbox_payloads(sid)
    assert len(payloads) == 1, f"expected exactly one outbox write, got {len(payloads)}"
    assert payloads[0]["text"] == ORIGINAL_REPLY, (
        "the held deferred_self_draft_text must be delivered VERBATIM, not a placeholder"
    )
    # Payload routing fields mirror the defer-time session.
    assert payloads[0]["chat_id"] == "12345"
    assert payloads[0]["reply_to"] == 263
    assert payloads[0]["session_id"] == sid
    # The terminal status write still happened.
    fresh = list(AgentSession.query.filter(session_id=sid))[0]
    assert fresh.status == "completed"


# ---------------------------------------------------------------------------
# 2. Both completion entry points / exactly-once across them
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exactly_once_across_both_completion_entry_points(cleanup):
    """Exercise the completed path through BOTH real entry points
    (``_complete_agent_session`` then ``complete_transcript``) finalizing the SAME
    session_id. The original reply body is delivered exactly ONE time total: the
    first genuine finalize flushes; the second short-circuits at the idempotency
    return (and the SETNX completed-flush key also guards)."""
    from agent.session_completion import _complete_agent_session
    from bridge.session_transcript import complete_transcript

    sid = f"{SID_PREFIX}both-entry-points"
    cleanup.append(sid)
    session = _make_session(sid, text=ORIGINAL_REPLY)

    # Entry point A: async _complete_agent_session -> finalize_session("completed").
    await _complete_agent_session(session)

    # Entry point B: sync complete_transcript -> finalize_session("completed").
    # It re-reads the (now terminal) session and short-circuits at the
    # idempotency early-return, so no second flush occurs.
    complete_transcript(sid, status="completed")

    payloads = _outbox_payloads(sid)
    assert len(payloads) == 1, (
        f"original reply must be delivered exactly once across both entry points; "
        f"got {len(payloads)} writes"
    )
    assert payloads[0]["text"] == ORIGINAL_REPLY


def test_exactly_once_when_finalize_completed_called_twice(cleanup):
    """Double-finalize on the SAME session via finalize_session('completed') twice:
    the second call short-circuits at the idempotency return -> exactly one write."""
    sid = f"{SID_PREFIX}double-finalize"
    cleanup.append(sid)
    session = _make_session(sid, text=ORIGINAL_REPLY)

    finalize_session(session, "completed", reason="first")
    # Second finalize on the same (now completed) object: current_status == status
    # hits the idempotency early-return before the flush runs.
    finalize_session(session, "completed", reason="second")

    assert _outbox_count(sid) == 1


# ---------------------------------------------------------------------------
# 3. No-double-send across recovery (completed flush + later failed recovery)
# ---------------------------------------------------------------------------


def test_no_double_send_completion_then_failed_recovery(cleanup):
    """A telegram completion-flush followed by a later telegram ``failed`` recovery
    on the same session_id yields exactly ONE outbox write — the completed-flush
    SETNX (``self_draft_completed_flush_sent``) dedups the second attempt."""
    sid = f"{SID_PREFIX}no-double-recovery"
    cleanup.append(sid)
    session = _make_session(sid, text=ORIGINAL_REPLY)

    # First terminal transition: completed -> flush A.
    finalize_session(session, "completed", reason="completed flush")
    assert _outbox_count(sid) == 1

    # Re-read a fresh running copy to simulate a later, independent recovery that
    # observes the same deferral flag. (reject_from_terminal=False so we can drive
    # a second terminal transition through the chokepoint.)
    fresh = list(AgentSession.query.filter(session_id=sid))[0]
    # Force the on-disk status back to running so the CAS + idempotency guards do
    # not short-circuit; we want to PROVE the SETNX dedup (not the status guard)
    # is what prevents the double-send.
    fresh.status = "running"
    fresh.save()

    fresh2 = list(AgentSession.query.filter(session_id=sid))[0]
    finalize_session(fresh2, "failed", reason="later recovery", reject_from_terminal=False)

    assert _outbox_count(sid) == 1, (
        "the completed-flush SETNX must dedup the later recovery flush — exactly one write total"
    )


# ---------------------------------------------------------------------------
# 4. Telegram failed/abandoned exactly-once
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("terminal_status", ["failed", "abandoned"])
def test_telegram_failed_or_abandoned_delivers_exactly_once(cleanup, terminal_status):
    """A telegram deferral reaching ``failed`` or ``abandoned`` via finalize_session
    delivers exactly ONE outbox write: the sync flush delivers; the async email-only
    helper early-returns at its telegram gate (and is not even invoked here)."""
    sid = f"{SID_PREFIX}{terminal_status}-once"
    cleanup.append(sid)
    session = _make_session(sid, text=ORIGINAL_REPLY)

    finalize_session(session, terminal_status, reason=f"test {terminal_status}")

    payloads = _outbox_payloads(sid)
    assert len(payloads) == 1
    assert payloads[0]["text"] == ORIGINAL_REPLY
    fresh = list(AgentSession.query.filter(session_id=sid))[0]
    assert fresh.status == terminal_status


# ---------------------------------------------------------------------------
# 5. Email-transport gate
# ---------------------------------------------------------------------------


def test_email_transport_gate_writes_zero_telegram_outbox(cleanup):
    """A session with extra_context['transport']=='email' reaching ``completed``
    writes ZERO telegram outbox entries — the sync flush early-returns at the
    transport gate (email coverage stays on the async helper)."""
    sid = f"{SID_PREFIX}email-gate"
    cleanup.append(sid)
    session = _make_session(sid, text=ORIGINAL_REPLY, transport="email")

    finalize_session(session, "completed", reason="email completed")

    assert _outbox_count(sid) == 0, (
        "email transport must not produce a telegram outbox write at the sync chokepoint"
    )
    fresh = list(AgentSession.query.filter(session_id=sid))[0]
    assert fresh.status == "completed", "status write must still happen for email transport"


# ---------------------------------------------------------------------------
# 6. Exception isolation: a flush failure must not block the terminal status write
# ---------------------------------------------------------------------------


def test_flush_exception_does_not_block_terminal_status(cleanup, monkeypatch):
    """If ``flush_deferred_self_draft_sync`` raises, finalize_session STILL writes
    the terminal status — the chokepoint invocation is exception-isolated."""
    sid = f"{SID_PREFIX}exception-isolation"
    cleanup.append(sid)
    session = _make_session(sid, text=ORIGINAL_REPLY)

    import agent.session_health as session_health

    def _boom(_session):
        raise RuntimeError("simulated flush failure")

    # finalize_session imports the symbol lazily from agent.session_health, so
    # patching the module attribute is sufficient.
    monkeypatch.setattr(session_health, "flush_deferred_self_draft_sync", _boom)

    # Must NOT raise.
    finalize_session(session, "completed", reason="exception isolation")

    fresh = list(AgentSession.query.filter(session_id=sid))[0]
    assert fresh.status == "completed", "a raising flush must not prevent the terminal status write"
    # No outbox write since the flush was replaced by the raising stub.
    assert _outbox_count(sid) == 0


# ---------------------------------------------------------------------------
# 7. Empty-text canned notice
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("empty_text", ["", "   ", "\n\t "])
def test_empty_deferred_text_delivers_canned_notice(cleanup, empty_text):
    """deferred_self_draft_pending=True but empty/whitespace text → the canned
    notice string is the delivered ``text``."""
    sid = f"{SID_PREFIX}canned-notice-{len(empty_text)}"
    cleanup.append(sid)
    session = _make_session(sid, text=empty_text)

    finalize_session(session, "completed", reason="empty text")

    payloads = _outbox_payloads(sid)
    assert len(payloads) == 1
    assert payloads[0]["text"] == CANNED_NOTICE, (
        f"empty deferred text must yield the canned notice; got {payloads[0]['text']!r}"
    )


# ---------------------------------------------------------------------------
# 8. No deferral → zero writes
# ---------------------------------------------------------------------------


def test_no_deferral_writes_zero_outbox(cleanup):
    """A normal ``completed`` session with no deferred_self_draft_pending produces
    zero flush-originated outbox writes."""
    sid = f"{SID_PREFIX}no-deferral"
    cleanup.append(sid)
    session = _make_session(sid, pending=False)

    finalize_session(session, "completed", reason="normal completion")

    assert _outbox_count(sid) == 0


# ---------------------------------------------------------------------------
# 9. Re-finalize idempotency
# ---------------------------------------------------------------------------


def test_re_finalize_already_completed_does_not_reflush(cleanup):
    """Finalizing an already-``completed`` session a second time short-circuits at
    the idempotency return and does NOT re-flush — exactly one write total from the
    first genuine finalize."""
    sid = f"{SID_PREFIX}re-finalize-idempotent"
    cleanup.append(sid)
    session = _make_session(sid, text=ORIGINAL_REPLY)

    finalize_session(session, "completed", reason="first genuine")
    assert _outbox_count(sid) == 1

    # Re-read the now-terminal session and finalize again to 'completed'. The
    # idempotency early-return (current_status == status) fires before the flush.
    fresh = list(AgentSession.query.filter(session_id=sid))[0]
    assert fresh.status == "completed"
    finalize_session(fresh, "completed", reason="second re-finalize")

    assert _outbox_count(sid) == 1, "re-finalize must not produce a second outbox write"
