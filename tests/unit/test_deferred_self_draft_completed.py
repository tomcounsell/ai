"""Regression + failure-path tests for the deferred self-draft completed-path flush.

Issue #1794 / #1797: a session that defers a reply for self-draft and then reaches a
terminal status (``completed``, ``failed``, ``abandoned``) without redrafting
must flush the held text to the human exactly once, on EVERY qualifying terminal
path, via the single ``finalize_session`` chokepoint.

The chokepoint invokes the synchronous helper
``agent.session_health.flush_deferred_self_draft_sync(session, status)`` (placed in
``models.session_lifecycle.finalize_session`` AFTER the idempotency early-return
and the ``reject_from_terminal`` guard, BEFORE ``session.save()``). The helper:

  * reads the deferral flags from a FRESH authoritative session,
  * gates on transport + status (see below),
  * SETNX-dedups on ``self_draft_completed_flush_sent:{session_id}:{run_id}``
    (1 h; run_id is the AgentSession record's AutoKey ``id``, so a reply-resumed
    session — same session_id, fresh record — gets a fresh dedup key),
  * applies the narration gate + empty-text canned notice,
  * writes the outbox payload via ``rpush``.

**Transport routing:**

  * telegram / None transport: proceeds for all terminal statuses; writes to
    ``telegram:outbox:{session_id}``.
  * email transport + ``status == "completed"``: proceeds; writes to
    ``email:outbox:{session_id}`` (issue #1797 — the completed-path fix).
  * email transport + other statuses (failed, abandoned, None): early-returns;
    the async ``_deliver_deferred_self_draft_fallback`` owns those paths.

The async email-only helper early-returns for telegram/None transport and dedups
on a DISTINCT key ``self_draft_fallback_sent:{session_id}:{run_id}``.

These tests use REAL Redis (the autouse ``redis_test_db`` fixture switches popoto
to a per-worker test db), create REAL ``AgentSession`` records via the ORM, and
assert on the actual outbox payload body. Test session_ids use the
``test-dsd-completed-`` prefix and are cleaned up in teardown.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agent.output_handler import DRAFTER_FALLBACK_SENDER
from agent.session_executor import _reenqueue_leftover_steering
from models.agent_session import AgentSession
from models.session_lifecycle import StatusConflictError, finalize_session

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


def _email_outbox_payloads(session_id: str) -> list[dict]:
    """Return the decoded payloads in ``email:outbox:{session_id}`` (FIFO order)."""
    raw = _redis().lrange(f"email:outbox:{session_id}", 0, -1)
    out = []
    for item in raw:
        if isinstance(item, bytes):
            item = item.decode("utf-8")
        out.append(json.loads(item))
    return out


def _email_outbox_count(session_id: str) -> int:
    return _redis().llen(f"email:outbox:{session_id}")


def _make_session(
    session_id: str,
    *,
    pending: bool = True,
    text: str | None = ORIGINAL_REPLY,
    transport: str | None = "telegram",
    status: str = "running",
    chat_id: str = "12345",
    telegram_message_id: int = 263,
    email_subject: str | None = None,
    email_message_id: str | None = None,
    email_to_addrs: list[str] | None = None,
    email_cc_addrs: list[str] | None = None,
) -> AgentSession:
    """Create and SAVE a real running AgentSession with deferral flags.

    The session MUST be saved so the helper's authoritative re-read
    (``get_authoritative_session``) sees the deferral flags.

    Email-specific parameters (``email_subject``, ``email_message_id``,
    ``email_to_addrs``, ``email_cc_addrs``) are stamped into ``extra_context``
    when provided, mirroring what ``bridge/email_bridge.py`` does at spawn time.
    """
    extra_context: dict = {}
    if transport is not None:
        extra_context["transport"] = transport
    if pending:
        extra_context["deferred_self_draft_pending"] = True
        extra_context["deferred_self_draft_text"] = text if text is not None else ""
    if email_subject is not None:
        extra_context["email_subject"] = email_subject
    if email_message_id is not None:
        extra_context["email_message_id"] = email_message_id
    if email_to_addrs is not None:
        extra_context["email_to_addrs"] = email_to_addrs
    if email_cc_addrs is not None:
        extra_context["email_cc_addrs"] = email_cc_addrs

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
    """Track created session_ids; delete records + dedup keys + outboxes in teardown."""
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
            r.delete(f"email:outbox:{sid}")
            for rid in {*run_ids, ""}:
                r.delete(f"self_draft_completed_flush_sent:{sid}:{rid}")
                r.delete(f"self_draft_fallback_sent:{sid}:{rid}")
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


def test_resumed_session_second_deferral_still_delivers(cleanup):
    """Regression: a reply-resume reuses the thread session_id but mints a FRESH
    AgentSession record (the reconciler deletes the prior terminal duplicate).
    Run 1's dedup key must NOT swallow run 2's deferred reply within the 1 h
    TTL — the key is scoped per-run via the record's AutoKey id."""
    second_reply = "Here is the reformatted report you asked for, attached properly this time."
    sid = f"{SID_PREFIX}resume-second-turn"
    cleanup.append(sid)

    # Run 1: deferral flushed on completion.
    run1 = _make_session(sid, text=ORIGINAL_REPLY)
    finalize_session(run1, "completed", reason="turn 1")
    assert _outbox_count(sid) == 1

    # Reply-resume: reconciler deletes the stale terminal duplicate, a new
    # record is enqueued for the SAME session_id, and it too defers a reply.
    for rec in list(AgentSession.query.filter(session_id=sid)):
        rec.delete()
    run2 = _make_session(sid, text=second_reply)
    assert run2.id != run1.id, "resume must mint a fresh AgentSession record id"

    finalize_session(run2, "completed", reason="turn 2")

    payloads = _outbox_payloads(sid)
    assert len(payloads) == 2, (
        "run 2's deferred reply must not be swallowed by run 1's dedup key "
        f"(got {len(payloads)} outbox writes)"
    )
    assert payloads[1]["text"] == second_reply


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
# 5. Email-transport delivery (#1797) — completed path writes to email:outbox
# ---------------------------------------------------------------------------


def test_email_completed_delivers_to_email_outbox(cleanup, monkeypatch):
    """Email + completed: the sync flush writes exactly one email:outbox entry
    with the correct body, to, subject, in_reply_to, and from_addr — and writes
    ZERO telegram:outbox entries.
    """
    monkeypatch.setenv("SMTP_USER", "robot@example.com")
    sid = f"{SID_PREFIX}email-deliver"
    cleanup.append(sid)
    session = _make_session(
        sid,
        text=ORIGINAL_REPLY,
        transport="email",
        chat_id="sender@example.com",
        email_subject="Sprint Review",
        email_message_id="<orig123@host.example>",
        email_to_addrs=["team@example.com"],
        email_cc_addrs=[],
    )

    finalize_session(session, "completed", reason="email completed")

    # Zero telegram outbox writes.
    assert _outbox_count(sid) == 0, "email transport must not produce a telegram:outbox write"

    # Exactly one email outbox entry.
    email_payloads = _email_outbox_payloads(sid)
    assert len(email_payloads) == 1, (
        f"expected exactly one email:outbox write, got {len(email_payloads)}"
    )
    p = email_payloads[0]
    assert p["body"] == ORIGINAL_REPLY, "body must be the verbatim held deferred text"
    assert "sender@example.com" in p["to"], "primary recipient must be first in to"
    assert p["subject"] == "Re: Sprint Review", "subject must have Re: prefix"
    assert p["in_reply_to"] == "<orig123@host.example>", "in_reply_to must thread correctly"
    assert p["from_addr"] == "robot@example.com", "from_addr must come from SMTP_USER"

    # Terminal status write still happened.
    fresh = list(AgentSession.query.filter(session_id=sid))[0]
    assert fresh.status == "completed"


def test_email_completed_exactly_once_across_re_finalize(cleanup, monkeypatch):
    """Calling flush_deferred_self_draft_sync twice (or finalize twice) for the
    same email+completed session writes exactly ONE email:outbox entry — SETNX
    dedup prevents the second write."""
    monkeypatch.delenv("SMTP_USER", raising=False)
    sid = f"{SID_PREFIX}email-once"
    cleanup.append(sid)
    session = _make_session(
        sid,
        text=ORIGINAL_REPLY,
        transport="email",
        chat_id="sender@example.com",
    )

    from agent.session_health import flush_deferred_self_draft_sync

    flush_deferred_self_draft_sync(session, "completed")
    # Second call: SETNX dedup should prevent a second write.
    flush_deferred_self_draft_sync(session, "completed")

    assert _email_outbox_count(sid) == 1, "SETNX dedup must prevent a second email:outbox write"


def test_email_failed_zero_sync_flush_writes(cleanup, monkeypatch):
    """Email + failed: the sync flush early-returns (status gate) so the
    email:outbox receives ZERO writes.  The async helper owns that path."""
    monkeypatch.delenv("SMTP_USER", raising=False)
    sid = f"{SID_PREFIX}email-failed"
    cleanup.append(sid)
    session = _make_session(
        sid,
        text=ORIGINAL_REPLY,
        transport="email",
        chat_id="sender@example.com",
    )

    finalize_session(session, "failed", reason="email failed gate test")

    assert _email_outbox_count(sid) == 0, (
        "email+failed must not produce an email:outbox write via the sync flush"
    )
    assert _outbox_count(sid) == 0, "no telegram:outbox write either"
    fresh = list(AgentSession.query.filter(session_id=sid))[0]
    assert fresh.status == "failed"


def test_email_abandoned_zero_sync_flush_writes(cleanup, monkeypatch):
    """Email + abandoned: the sync flush early-returns (status gate) so the
    email:outbox receives ZERO writes.  The async helper owns that path."""
    monkeypatch.delenv("SMTP_USER", raising=False)
    sid = f"{SID_PREFIX}email-abandoned"
    cleanup.append(sid)
    session = _make_session(
        sid,
        text=ORIGINAL_REPLY,
        transport="email",
        chat_id="sender@example.com",
    )

    finalize_session(session, "abandoned", reason="email abandoned gate test")

    assert _email_outbox_count(sid) == 0, (
        "email+abandoned must not produce an email:outbox write via the sync flush"
    )
    fresh = list(AgentSession.query.filter(session_id=sid))[0]
    assert fresh.status == "abandoned"


def test_email_completed_empty_text_canned_notice(cleanup, monkeypatch):
    """Email + completed with empty/whitespace deferred text → the canned notice
    is the email:outbox body, not a blank message."""
    monkeypatch.delenv("SMTP_USER", raising=False)
    sid = f"{SID_PREFIX}email-canned"
    cleanup.append(sid)
    session = _make_session(
        sid,
        text="   ",
        transport="email",
        chat_id="sender@example.com",
        email_subject="Follow Up",
    )

    finalize_session(session, "completed", reason="email empty text")

    email_payloads = _email_outbox_payloads(sid)
    assert len(email_payloads) == 1
    assert email_payloads[0]["body"] == CANNED_NOTICE, (
        f"empty email deferred text must yield canned notice; got {email_payloads[0]['body']!r}"
    )


def test_email_completed_status_none_no_write(cleanup, monkeypatch):
    """flush_deferred_self_draft_sync called directly with status=None for an
    email session → no email:outbox write (status gate requires 'completed')."""
    monkeypatch.delenv("SMTP_USER", raising=False)
    sid = f"{SID_PREFIX}email-status-none"
    cleanup.append(sid)
    session = _make_session(
        sid,
        text=ORIGINAL_REPLY,
        transport="email",
        chat_id="sender@example.com",
    )

    from agent.session_health import flush_deferred_self_draft_sync

    flush_deferred_self_draft_sync(session, status=None)

    assert _email_outbox_count(sid) == 0, "status=None must not produce an email:outbox write"


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

    def _boom(_session, _status=None):
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


def test_email_flush_rpush_exception_does_not_block_terminal_status(cleanup, monkeypatch):
    """If the Redis rpush inside the email flush branch raises, finalize_session
    STILL writes the terminal status — the outer exception handler swallows it."""
    import popoto.redis_db as rdb

    sid = f"{SID_PREFIX}email-rpush-exception"
    cleanup.append(sid)
    session = _make_session(
        sid,
        text=ORIGINAL_REPLY,
        transport="email",
        chat_id="sender@example.com",
    )

    # Patch rpush on the live Redis handle so the email branch raises.
    original_rpush = rdb.POPOTO_REDIS_DB.rpush

    def _boom_rpush(key, *args, **kwargs):
        if key.startswith("email:outbox:"):
            raise RuntimeError("simulated email rpush failure")
        return original_rpush(key, *args, **kwargs)

    monkeypatch.setattr(rdb.POPOTO_REDIS_DB, "rpush", _boom_rpush)

    # Must NOT raise — exception is swallowed inside flush_deferred_self_draft_sync.
    finalize_session(session, "completed", reason="email rpush exception isolation")

    fresh = list(AgentSession.query.filter(session_id=sid))[0]
    assert fresh.status == "completed", "rpush failure must not prevent terminal status write"


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


def test_no_deferral_writes_zero_outbox(cleanup, caplog):
    """A normal ``completed`` session with no deferred_self_draft_pending produces
    zero flush-originated outbox writes, and the flush's "nothing pending"
    early-return is no longer silent (#3053) — it logs at DEBUG, distinguishing
    "ran, found nothing" from "never ran"."""
    sid = f"{SID_PREFIX}no-deferral"
    cleanup.append(sid)
    session = _make_session(sid, pending=False)

    with caplog.at_level("DEBUG", logger="agent.session_health"):
        finalize_session(session, "completed", reason="normal completion")

    assert _outbox_count(sid) == 0
    assert any(
        "nothing pending" in rec.message
        for rec in caplog.records
        if rec.name == "agent.session_health"
    ), "flush_deferred_self_draft_sync must log a DEBUG line on its nothing-pending early-return"


# ---------------------------------------------------------------------------
# 10. Terminal-path re-enqueue suppression of drafter-fallback steering (#2197)
# ---------------------------------------------------------------------------
#
# On a terminal-turn self-draft deferral, the completed-path flush above
# (``flush_deferred_self_draft_sync`` / ``_deliver_deferred_self_draft_fallback``)
# already delivers the held text exactly once. A SEPARATE handler in
# ``agent/session_executor.py`` — ``_reenqueue_leftover_steering`` — used to pop
# the still-present ``drafter-fallback`` steering message and re-enqueue it as a
# brand-new, context-blind continuation session, which then emitted a misleading
# "no substantive results" reply. The fix partitions leftover steering by sender:
# ``drafter-fallback`` is dropped (the flush already owns it); every other sender
# still re-enqueues exactly as before.
#
# These tests exercise ``_reenqueue_leftover_steering`` directly — it is the
# extracted, testable unit the terminal-path re-enqueue block in
# ``_execute_agent_session`` now delegates to. Steering messages use the real
# Redis-backed queue (``push_steering_message`` / real list contents built
# in-test); ``enqueue_agent_session`` is mocked since it spawns a new session.


def _leftover_msg(sender: str | None, text: str) -> dict:
    return {"sender": sender, "text": text}


class TestReenqueueLeftoverSteeringSuppression:
    @pytest.mark.asyncio
    async def test_fallback_only_leftover_suppressed_not_reenqueued(self, cleanup, caplog):
        """Leftover steering containing ONLY a drafter-fallback message must not
        trigger a re-enqueue, and the suppression must be logged at INFO."""
        sid = f"{SID_PREFIX}reenqueue-fallback-only"
        cleanup.append(sid)
        session = _make_session(sid, pending=False, status="completed")

        leftover = [_leftover_msg(DRAFTER_FALLBACK_SENDER, "rewrite it please")]

        with (
            patch("agent.agent_session_queue.enqueue_agent_session", new=AsyncMock()) as mock_enq,
            caplog.at_level("INFO"),
        ):
            await _reenqueue_leftover_steering(
                session, session, Path("/tmp/does-not-matter"), leftover
            )

        mock_enq.assert_not_called()
        suppression_logs = [
            r
            for r in caplog.records
            if "Suppressing" in r.message and "drafter-fallback" in r.message
        ]
        assert suppression_logs, "expected an INFO-level suppression log line"
        assert all(r.levelname == "INFO" for r in suppression_logs)

    @pytest.mark.asyncio
    async def test_mixed_leftover_reenqueues_only_genuine_message(self, cleanup):
        """Leftover with BOTH a drafter-fallback message and a genuine-sender
        message: enqueue_agent_session IS called, and the payload derives only
        from the genuine (carry) message — the drafter-fallback text must not
        appear in the augmented message_text."""
        sid = f"{SID_PREFIX}reenqueue-mixed"
        cleanup.append(sid)
        session = _make_session(sid, pending=False, status="completed")

        fallback_text = "rewrite it please (fallback text should not carry over)"
        genuine_text = "please also check the staging deploy"
        leftover = [
            _leftover_msg(DRAFTER_FALLBACK_SENDER, fallback_text),
            _leftover_msg("Tom", genuine_text),
        ]

        with patch("agent.agent_session_queue.enqueue_agent_session", new=AsyncMock()) as mock_enq:
            await _reenqueue_leftover_steering(
                session, session, Path("/tmp/does-not-matter"), leftover
            )

        mock_enq.assert_called_once()
        _, kwargs = mock_enq.call_args
        assert genuine_text in kwargs["message_text"]
        assert fallback_text not in kwargs["message_text"]
        assert kwargs["sender_name"] == "Tom"

    @pytest.mark.asyncio
    async def test_senderless_leftover_still_reenqueues(self, cleanup):
        """A leftover message with a missing/None ``sender`` key must be treated
        as NOT drafter-fallback (lands in carry) — matching today's behavior."""
        sid = f"{SID_PREFIX}reenqueue-senderless"
        cleanup.append(sid)
        session = _make_session(sid, pending=False, status="completed")

        leftover = [_leftover_msg(None, "continue please")]

        with patch("agent.agent_session_queue.enqueue_agent_session", new=AsyncMock()) as mock_enq:
            await _reenqueue_leftover_steering(
                session, session, Path("/tmp/does-not-matter"), leftover
            )

        mock_enq.assert_called_once()
        _, kwargs = mock_enq.call_args
        assert "continue please" in kwargs["message_text"]

    @pytest.mark.asyncio
    async def test_email_transport_fallback_only_leftover_also_suppressed(self, cleanup):
        """Suppression is transport-agnostic: an email-path terminal session with
        a drafter-fallback-only leftover is likewise suppressed from re-enqueue.
        The re-enqueue suppression lives in session_executor.py and does not
        branch on transport — only the delivery-flush routing (session_health.py
        for telegram sync, `_deliver_deferred_self_draft_fallback` for email
        async) is transport-specific."""
        sid = f"{SID_PREFIX}reenqueue-email-fallback-only"
        cleanup.append(sid)
        session = _make_session(
            sid, pending=False, status="completed", transport="email", chat_id="sender@example.com"
        )

        leftover = [_leftover_msg(DRAFTER_FALLBACK_SENDER, "rewrite it please")]

        with patch("agent.agent_session_queue.enqueue_agent_session", new=AsyncMock()) as mock_enq:
            await _reenqueue_leftover_steering(
                session, session, Path("/tmp/does-not-matter"), leftover
            )

        mock_enq.assert_not_called()


# ---------------------------------------------------------------------------
# 11. Validator-aware local-path -> attachment conversion (#2211)
# ---------------------------------------------------------------------------
#
# The sync flush runs ``convert_local_paths_to_attachments`` on the deferred
# text BEFORE the narration gate: existing non-secret files are attached
# (``file_paths=`` on both payload builders) and every detected path token —
# dead, secret-excluded, or attached — is scrubbed from the delivered text.
# The async email fallback (``_deliver_deferred_self_draft_fallback``) now has
# attachment PARITY (issue #2303): ``deliver_system_notice`` gained an optional
# ``file_paths=`` channel, so the fallback forwards the converted ``attached``
# list instead of discarding it.

FILE_UNAVAILABLE_NOTICE = "(the referenced file is no longer available)"


@pytest.fixture
def tmp_attachment():
    """A real, existing, non-secret file under /tmp (matches the /tmp/\\S+ pattern)."""
    fd, path = tempfile.mkstemp(dir="/tmp", prefix="dsd-conv-", suffix=".txt")  # noqa: S108
    os.write(fd, b"report body")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


def test_existing_local_path_converted_to_attachment_telegram(cleanup, tmp_attachment):
    """Telegram flush: deferred text carrying an EXISTING /tmp path delivers the
    file via ``file_paths`` with the raw path token scrubbed from the text."""
    sid = f"{SID_PREFIX}conv-tg-attach"
    cleanup.append(sid)
    text = f"The weekly report is done. See {tmp_attachment} for the full numbers."
    session = _make_session(sid, text=text)

    finalize_session(session, "completed", reason="conversion telegram")

    payloads = _outbox_payloads(sid)
    assert len(payloads) == 1
    p = payloads[0]
    assert p.get("file_paths") == [tmp_attachment], (
        f"existing local path must ride file_paths; got {p.get('file_paths')!r}"
    )
    assert tmp_attachment not in p["text"], "raw path token must be scrubbed from delivered text"
    assert "The weekly report is done." in p["text"], "surrounding prose must survive the scrub"


def test_existing_local_path_converted_to_attachment_email(cleanup, monkeypatch, tmp_attachment):
    """Email-completed flush: the same conversion rides the payload's
    ``attachments`` key (the builder maps the ``file_paths=`` param to it)."""
    monkeypatch.delenv("SMTP_USER", raising=False)
    sid = f"{SID_PREFIX}conv-em-attach"
    cleanup.append(sid)
    text = f"The weekly report is done. See {tmp_attachment} for the full numbers."
    session = _make_session(
        sid,
        text=text,
        transport="email",
        chat_id="sender@example.com",
        email_subject="Weekly Report",
    )

    finalize_session(session, "completed", reason="conversion email")

    email_payloads = _email_outbox_payloads(sid)
    assert len(email_payloads) == 1
    p = email_payloads[0]
    assert p["attachments"] == [tmp_attachment], (
        f"email payload must carry the converted path in attachments; got {p['attachments']!r}"
    )
    assert tmp_attachment not in p["body"], "raw path token must be scrubbed from the email body"


def test_dead_path_only_delivers_file_unavailable_notice(cleanup):
    """Deferred text that is ONLY a non-existent /tmp path delivers the canned
    '(no longer available)' notice with NO file_paths — never an empty payload
    the relay's ``if not text and not file_paths`` guard would drop (#1796)."""
    sid = f"{SID_PREFIX}conv-dead-only"
    cleanup.append(sid)
    dead = f"/tmp/dsd-conv-definitely-missing-{os.getpid()}.txt"  # noqa: S108
    assert not os.path.exists(dead)
    session = _make_session(sid, text=dead)

    finalize_session(session, "completed", reason="dead path only")

    payloads = _outbox_payloads(sid)
    assert len(payloads) == 1
    assert payloads[0]["text"] == FILE_UNAVAILABLE_NOTICE
    assert "file_paths" not in payloads[0], "a dead path must never attach"


def test_path_only_text_attaches_with_basename_caption(cleanup, tmp_attachment):
    """Deferred text that is ONLY an existing path: the file attaches and the
    empty-text guard's first arm substitutes the basename caption."""
    sid = f"{SID_PREFIX}conv-path-only"
    cleanup.append(sid)
    session = _make_session(sid, text=tmp_attachment)

    finalize_session(session, "completed", reason="path only")

    payloads = _outbox_payloads(sid)
    assert len(payloads) == 1
    p = payloads[0]
    assert p.get("file_paths") == [tmp_attachment]
    assert p["text"] == os.path.basename(tmp_attachment), (
        f"empty scrub with an attachment must caption with the basename; got {p['text']!r}"
    )


def test_secret_excluded_path_never_attached_and_never_logged(cleanup, caplog):
    """A secret-excluded path (dotfile under /tmp) is scrubbed, never attached;
    sole-content case delivers the canned notice; the secret-skip WARNING logs
    the COUNT only — the path/basename never appears in any log line."""
    fd, secret_path = tempfile.mkstemp(dir="/tmp", prefix=".netrc-dsd-", suffix="")  # noqa: S108
    os.write(fd, b"machine example login secret")
    os.close(fd)
    sid = f"{SID_PREFIX}conv-secret"
    cleanup.append(sid)
    try:
        session = _make_session(sid, text=secret_path)

        with caplog.at_level("INFO", logger="agent.session_health"):
            finalize_session(session, "completed", reason="secret excluded")

        payloads = _outbox_payloads(sid)
        assert len(payloads) == 1
        p = payloads[0]
        assert "file_paths" not in p, "a secret-excluded path must NEVER attach"
        assert p["text"] == FILE_UNAVAILABLE_NOTICE, (
            "sole-content secret path must yield the canned notice (indistinguishable from dead)"
        )
        assert secret_path not in p["text"]

        skip_warnings = [
            r
            for r in caplog.records
            if "excluded as sensitive" in r.getMessage() and r.levelname == "WARNING"
        ]
        assert skip_warnings, "the secret-skip WARNING must fire"
        basename = os.path.basename(secret_path)
        for record in caplog.records:
            assert secret_path not in record.getMessage()
            assert basename not in record.getMessage(), (
                "the secret path/basename must never appear in any log line (count only)"
            )

        # Counter telemetry fired (count-only signal).
        counter = _redis().get("test-dsd:session-health:deferred_flush_secret_paths_skipped")
        assert counter is not None and int(counter) >= 1
    finally:
        try:
            os.unlink(secret_path)
        except OSError:
            pass
        _redis().delete("test-dsd:session-health:deferred_flush_secret_paths_skipped")


def test_narration_plus_existing_path_attaches_telegram(cleanup, tmp_attachment):
    """Narration-before-conversion ordering (the motivating incident shape): a
    narration-only sentence + an existing path still ATTACHES the file, and the
    text is the pathless NARRATION_FALLBACK_MESSAGE — proving conversion ran on
    the original deferred text BEFORE the narration gate."""
    from bridge.message_quality import NARRATION_FALLBACK_MESSAGE

    sid = f"{SID_PREFIX}conv-narr-tg"
    cleanup.append(sid)
    session = _make_session(sid, text=f"Let me check the logs. {tmp_attachment}")

    finalize_session(session, "completed", reason="narration + path telegram")

    payloads = _outbox_payloads(sid)
    assert len(payloads) == 1
    p = payloads[0]
    assert p.get("file_paths") == [tmp_attachment], (
        "the file must survive the narration substitution (convert-before-narration)"
    )
    assert p["text"] == NARRATION_FALLBACK_MESSAGE
    assert tmp_attachment not in p["text"]


def test_narration_plus_existing_path_attaches_email(cleanup, monkeypatch, tmp_attachment):
    """Same convert-before-narration ordering on the email-completed branch."""
    from bridge.message_quality import NARRATION_FALLBACK_MESSAGE

    monkeypatch.delenv("SMTP_USER", raising=False)
    sid = f"{SID_PREFIX}conv-narr-em"
    cleanup.append(sid)
    session = _make_session(
        sid,
        text=f"Let me check the logs. {tmp_attachment}",
        transport="email",
        chat_id="sender@example.com",
    )

    finalize_session(session, "completed", reason="narration + path email")

    email_payloads = _email_outbox_payloads(sid)
    assert len(email_payloads) == 1
    p = email_payloads[0]
    assert p["attachments"] == [tmp_attachment]
    assert p["body"] == NARRATION_FALLBACK_MESSAGE
    assert tmp_attachment not in p["body"]


@pytest.mark.asyncio
async def test_async_fallback_attaches_existing_path(cleanup, tmp_attachment):
    """Async fallback (email failed/abandoned) now ATTACHES (issue #2303): the
    scrubbed message rides deliver_system_notice's new ``file_paths=`` channel
    with the existing file, and the raw path token never reaches the recipient.
    Narration-only prose still substitutes NARRATION_FALLBACK_MESSAGE while the
    attachment survives (convert-before-narration, mirrors the sync flush)."""
    from agent.session_health import _deliver_deferred_self_draft_fallback
    from bridge.message_quality import NARRATION_FALLBACK_MESSAGE

    sid = f"{SID_PREFIX}conv-async-attach"
    cleanup.append(sid)
    session = _make_session(
        sid,
        text=f"Let me check the logs. {tmp_attachment}",
        transport="email",
        chat_id="sender@example.com",
    )

    with patch(
        "agent.output_handler.deliver_system_notice", new_callable=AsyncMock, return_value=True
    ) as mock_notice:
        await _deliver_deferred_self_draft_fallback(session)

    mock_notice.assert_awaited_once()
    args, kwargs = mock_notice.call_args
    delivered_message = args[1]
    assert tmp_attachment not in delivered_message, "no raw local path may reach the recipient"
    assert delivered_message == NARRATION_FALLBACK_MESSAGE
    assert kwargs.get("file_paths") == [tmp_attachment], (
        f"the converted attachment must ride file_paths; got {kwargs.get('file_paths')!r}"
    )


@pytest.mark.asyncio
async def test_async_fallback_dead_path_scrubs_no_attachment(cleanup):
    """Async fallback with a NON-existent /tmp path: scrubbed, nothing attaches,
    and (text emptied by the scrub) the canned notice is delivered with an empty
    file_paths list — never a raw path, never an empty payload (#1796)."""
    from agent.session_health import _deliver_deferred_self_draft_fallback

    dead = "/tmp/dsd-does-not-exist-2303.txt"  # noqa: S108
    sid = f"{SID_PREFIX}conv-async-dead"
    cleanup.append(sid)
    session = _make_session(
        sid,
        text=dead,
        transport="email",
        chat_id="sender@example.com",
    )

    with patch(
        "agent.output_handler.deliver_system_notice", new_callable=AsyncMock, return_value=True
    ) as mock_notice:
        await _deliver_deferred_self_draft_fallback(session)

    mock_notice.assert_awaited_once()
    args, kwargs = mock_notice.call_args
    delivered_message = args[1]
    assert dead not in delivered_message, "a dead path must be scrubbed, never delivered"
    assert delivered_message == FILE_UNAVAILABLE_NOTICE
    assert not kwargs.get("file_paths"), "a dead path must never attach"


@pytest.mark.asyncio
async def test_async_fallback_empty_after_scrub_with_attachment_captions_basename(
    cleanup, tmp_attachment
):
    """Two-armed empty-text guard parity: when the deferred text is ONLY an
    existing path (scrub empties it), the fallback captions with the basename
    and still attaches the file — never an empty payload."""
    from agent.session_health import _deliver_deferred_self_draft_fallback

    sid = f"{SID_PREFIX}conv-async-caption"
    cleanup.append(sid)
    session = _make_session(
        sid,
        text=tmp_attachment,
        transport="email",
        chat_id="sender@example.com",
    )

    with patch(
        "agent.output_handler.deliver_system_notice", new_callable=AsyncMock, return_value=True
    ) as mock_notice:
        await _deliver_deferred_self_draft_fallback(session)

    mock_notice.assert_awaited_once()
    args, kwargs = mock_notice.call_args
    delivered_message = args[1]
    assert delivered_message == os.path.basename(tmp_attachment)
    assert kwargs.get("file_paths") == [tmp_attachment]


def test_non_local_path_deferral_stays_text_only_both_branches(cleanup, monkeypatch):
    """Regression guard (transport-general): a deferral with NO local path still
    produces a text-only payload on both the telegram and email branches.

    (Existing tests §1/§5 assert verbatim text but not the absence of the
    attachment key — this pins that explicitly.)"""
    monkeypatch.delenv("SMTP_USER", raising=False)
    sid_tg = f"{SID_PREFIX}conv-regress-tg"
    cleanup.append(sid_tg)
    finalize_session(_make_session(sid_tg, text=ORIGINAL_REPLY), "completed", reason="regress tg")
    payloads = _outbox_payloads(sid_tg)
    assert len(payloads) == 1
    assert payloads[0]["text"] == ORIGINAL_REPLY
    assert "file_paths" not in payloads[0], "non-local-path deferral must stay text-only"

    sid_em = f"{SID_PREFIX}conv-regress-em"
    cleanup.append(sid_em)
    finalize_session(
        _make_session(sid_em, text=ORIGINAL_REPLY, transport="email", chat_id="a@example.com"),
        "completed",
        reason="regress email",
    )
    email_payloads = _email_outbox_payloads(sid_em)
    assert len(email_payloads) == 1
    assert email_payloads[0]["body"] == ORIGINAL_REPLY
    assert email_payloads[0]["attachments"] == [], "non-local-path email deferral stays text-only"


def test_helper_exception_degrades_to_unconverted_delivery(cleanup, monkeypatch, caplog):
    """If convert_local_paths_to_attachments raises, the flush still delivers
    the UNCONVERTED text (today's behavior) and logs a warning — a conversion
    bug can never suppress delivery."""
    import bridge.message_drafter as message_drafter

    def _boom(_text):
        raise RuntimeError("simulated conversion failure")

    monkeypatch.setattr(message_drafter, "convert_local_paths_to_attachments", _boom)

    sid = f"{SID_PREFIX}conv-helper-raises"
    cleanup.append(sid)
    session = _make_session(sid, text=ORIGINAL_REPLY)

    with caplog.at_level("WARNING", logger="agent.session_health"):
        finalize_session(session, "completed", reason="helper raises")

    payloads = _outbox_payloads(sid)
    assert len(payloads) == 1
    assert payloads[0]["text"] == ORIGINAL_REPLY, (
        "conversion failure must degrade to delivering the unconverted text"
    )
    assert "file_paths" not in payloads[0]
    assert any(
        "convert_local_paths_to_attachments raised" in r.getMessage() for r in caplog.records
    ), "a WARNING must be logged when the helper raises"


# ---------------------------------------------------------------------------
# 9. Re-finalize idempotency
# ---------------------------------------------------------------------------


def test_re_finalize_already_completed_does_not_reflush(cleanup):
    """Finalizing an already-``completed`` session a second time: exactly one
    outbox write total across both calls.

    Under the #3053 hoist, the flush is now the FIRST thing every
    ``finalize_session`` call does — including this second, already-terminal
    call — so the flush IS entered on the re-finalize. What prevents a second
    delivery is that the first flush already popped
    ``deferred_self_draft_pending`` from the authoritative record (the
    post-delivery clear), so the second flush's own "nothing pending"
    early-return no-ops it before the SETNX dedup is even consulted. The
    invariant that actually matters — and the one this test pins — is
    "exactly one outbox write", not "the flush was never entered".
    """
    sid = f"{SID_PREFIX}re-finalize-idempotent"
    cleanup.append(sid)
    session = _make_session(sid, text=ORIGINAL_REPLY)

    finalize_session(session, "completed", reason="first genuine")
    assert _outbox_count(sid) == 1

    # Re-read the now-terminal session and finalize again to 'completed'. The
    # flush runs again (hoisted, unconditional) but no-ops on the cleared flag;
    # the idempotency early-return (current_status == status) still fires below it.
    fresh = list(AgentSession.query.filter(session_id=sid))[0]
    assert fresh.status == "completed"
    finalize_session(fresh, "completed", reason="second re-finalize")

    assert _outbox_count(sid) == 1, "re-finalize must not produce a second outbox write"


# ---------------------------------------------------------------------------
# 8. Terminal-flush promise gate (issue #2423)
# ---------------------------------------------------------------------------

# A forward-deferral reply held as a deferred self-draft: at terminal-flush time
# there is no live agent to self-draft, so the flush must SUBSTITUTE an honest
# fallback rather than deliver the false promise (or swallow the reply, #1796).
PROMISE_REPLY = "On it — I'll follow up with the results once it's ready."


class TestTerminalFlushPromiseGate:
    def test_promise_flagged_deferred_draft_substituted_not_delivered(self, cleanup):
        """A promise-flagged deferred draft reaching the terminal flush is NOT
        delivered verbatim; the honest-fallback substitution ships instead."""
        from agent.session_health import TERMINAL_PROMISE_FALLBACK_MESSAGE

        sid = f"{SID_PREFIX}promise-sub"
        cleanup.append(sid)
        session = _make_session(sid, text=PROMISE_REPLY)

        finalize_session(session, "completed", reason="test completed")

        payloads = _outbox_payloads(sid)
        assert len(payloads) == 1, "substitution must never swallow the reply (#1796)"
        assert payloads[0]["text"] == TERMINAL_PROMISE_FALLBACK_MESSAGE
        assert "follow up with" not in payloads[0]["text"].lower()

    def test_terminal_flush_block_writes_audit(self, cleanup, tmp_path, monkeypatch):
        """A gated terminal flush writes a ``source="terminal_flush"`` audit
        entry to the classification audit log."""
        import bridge.promise_gate as promise_gate

        log_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(promise_gate, "_AUDIT_LOG_PATH", log_path)

        sid = f"{SID_PREFIX}promise-audit"
        cleanup.append(sid)
        session = _make_session(sid, text=PROMISE_REPLY)

        finalize_session(session, "completed", reason="test completed")

        entries = [json.loads(line) for line in log_path.read_text().splitlines()]
        blocks = [e for e in entries if e["source"] == "terminal_flush"]
        assert len(blocks) == 1
        assert blocks[0]["kind"] == "promise_gate"
        assert blocks[0]["action"] == "block"

    def test_kill_switch_delivers_verbatim(self, cleanup, monkeypatch):
        """PROMISE_GATE_ENABLED=false disables the terminal-flush gate: the held
        text is delivered verbatim (kill-switch contract)."""
        monkeypatch.setenv("PROMISE_GATE_ENABLED", "false")

        sid = f"{SID_PREFIX}promise-kill"
        cleanup.append(sid)
        session = _make_session(sid, text=PROMISE_REPLY)

        finalize_session(session, "completed", reason="test completed")

        payloads = _outbox_payloads(sid)
        assert len(payloads) == 1
        assert payloads[0]["text"] == PROMISE_REPLY

    def test_non_promise_text_still_delivered_verbatim(self, cleanup):
        """False-positive guard: a substantive non-promise deferred reply is
        unaffected by the gate."""
        sid = f"{SID_PREFIX}promise-benign"
        cleanup.append(sid)
        session = _make_session(sid, text=ORIGINAL_REPLY)

        finalize_session(session, "completed", reason="test completed")

        payloads = _outbox_payloads(sid)
        assert len(payloads) == 1
        assert payloads[0]["text"] == ORIGINAL_REPLY

    def test_substitute_message_passes_the_heuristic(self):
        """The honest fallback must itself pass the promise heuristic — the
        substitution may never be a new empty promise."""
        from agent.session_health import TERMINAL_PROMISE_FALLBACK_MESSAGE
        from bridge.promise_gate import _evaluate_promise_heuristic

        assert _evaluate_promise_heuristic(TERMINAL_PROMISE_FALLBACK_MESSAGE).action == "allow"

    @pytest.mark.asyncio
    async def test_async_email_fallback_promise_substituted(self, cleanup):
        """The async email fallback (_deliver_deferred_self_draft_fallback) has
        the identical hole and is gated by the same helper."""
        from agent.session_health import (
            TERMINAL_PROMISE_FALLBACK_MESSAGE,
            _deliver_deferred_self_draft_fallback,
        )

        sid = f"{SID_PREFIX}promise-async-email"
        cleanup.append(sid)
        session = _make_session(
            sid,
            text=PROMISE_REPLY,
            transport="email",
            chat_id="sender@example.com",
        )

        with patch(
            "agent.output_handler.deliver_system_notice",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_notice:
            await _deliver_deferred_self_draft_fallback(session)

        mock_notice.assert_awaited_once()
        args, _kwargs = mock_notice.call_args
        assert args[1] == TERMINAL_PROMISE_FALLBACK_MESSAGE


# ---------------------------------------------------------------------------
# 9. Reachability (#3053 AC1): real defer through output_handler.send(),
#    real completion through complete_transcript.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reachability_real_defer_through_output_handler_to_completion(cleanup):
    """AC1: a validator-flagged final reply, deferred through the REAL
    ``TelegramRelayOutputHandler.send()`` path (not a hand-built session
    record), reaches the outbox when the session completes via the real
    ``complete_transcript`` -> ``finalize_session`` chokepoint.
    """
    from bridge.message_drafter import MessageDraft

    sid = f"{SID_PREFIX}reachability-e2e"
    cleanup.append(sid)
    session = _make_session(sid, pending=False, status="running")

    flagged_draft = MessageDraft(text=ORIGINAL_REPLY, needs_self_draft=True)

    with (
        patch(
            "bridge.message_drafter.draft_message",
            new_callable=AsyncMock,
            return_value=flagged_draft,
        ),
        patch(
            "agent.output_handler.TelegramRelayOutputHandler._inject_self_draft_steering",
            return_value=True,
        ),
    ):
        from agent.output_handler import TelegramRelayOutputHandler

        handler = TelegramRelayOutputHandler()
        outcome = await handler.send("12345", ORIGINAL_REPLY, 263, session=session)

    from agent.output_handler import DeliveryOutcome

    assert outcome == DeliveryOutcome.deferred_self_draft
    # No outbox write yet — production code persisted the deferral onto the
    # authoritative record, not this in-memory `session` object.
    assert _outbox_count(sid) == 0
    fresh_before = list(AgentSession.query.filter(session_id=sid))[0]
    assert fresh_before.extra_context.get("deferred_self_draft_pending") is True
    assert fresh_before.extra_context.get("deferred_self_draft_text") == ORIGINAL_REPLY

    # Turn ends with no redraft: real completion entry point.
    from bridge.session_transcript import complete_transcript

    complete_transcript(sid, status="completed")

    payloads = _outbox_payloads(sid)
    assert len(payloads) == 1, "the deferred reply must reach the outbox on completion"
    assert payloads[0]["text"] == ORIGINAL_REPLY
    fresh_after = list(AgentSession.query.filter(session_id=sid))[0]
    assert fresh_after.status == "completed"
    assert not fresh_after.extra_context.get("deferred_self_draft_pending")


# ---------------------------------------------------------------------------
# 10. Parametrized exit-invariant: the flush is entered on every
#     finalize_session exit (#3053 AC2).
# ---------------------------------------------------------------------------


def test_flush_entered_on_fresh_terminal_transition(cleanup, monkeypatch):
    sid = f"{SID_PREFIX}invariant-fresh"
    cleanup.append(sid)
    session = _make_session(sid, text=ORIGINAL_REPLY)

    calls = []
    import agent.session_health as session_health

    original = session_health.flush_deferred_self_draft_sync

    def _spy(sess, status=None):
        calls.append(status)
        return original(sess, status)

    monkeypatch.setattr(session_health, "flush_deferred_self_draft_sync", _spy)

    finalize_session(session, "completed", reason="fresh")

    assert calls == ["completed"], "flush must be entered exactly once on a fresh transition"


def test_flush_entered_on_idempotent_re_finalize(cleanup, monkeypatch):
    sid = f"{SID_PREFIX}invariant-idempotent"
    cleanup.append(sid)
    session = _make_session(sid, text=ORIGINAL_REPLY)

    import agent.session_health as session_health

    original = session_health.flush_deferred_self_draft_sync
    calls = []

    def _spy(sess, status=None):
        calls.append(status)
        return original(sess, status)

    monkeypatch.setattr(session_health, "flush_deferred_self_draft_sync", _spy)

    finalize_session(session, "completed", reason="first")
    fresh = list(AgentSession.query.filter(session_id=sid))[0]
    finalize_session(fresh, "completed", reason="second (idempotent)")

    assert calls == ["completed", "completed"], (
        "flush must be entered on BOTH the fresh transition and the idempotent "
        "re-finalize — it is the flush's own gating/dedup, not finalize_session's "
        "idempotency check, that prevents a second delivery"
    )
    assert _outbox_count(sid) == 1


def test_flush_entered_on_reject_from_terminal_conflict(cleanup, monkeypatch):
    """A rejected terminal->terminal re-transition still flushes before raising
    ``StatusConflictError`` — the accepted cost of the hoist (PM ruling, Open
    Question 1)."""
    sid = f"{SID_PREFIX}invariant-reject"
    cleanup.append(sid)
    session = _make_session(sid, text=ORIGINAL_REPLY, status="running")

    import agent.session_health as session_health

    original = session_health.flush_deferred_self_draft_sync
    calls = []

    def _spy(sess, status=None):
        calls.append(status)
        return original(sess, status)

    monkeypatch.setattr(session_health, "flush_deferred_self_draft_sync", _spy)

    # First finalize to "failed" (a different terminal status than the next call).
    finalize_session(session, "failed", reason="first terminal")
    assert calls == ["failed"]
    assert _outbox_count(sid) == 1

    # Attempting to re-classify failed -> killed hits reject_from_terminal
    # (the session is already terminal), but the flush -- which sits ABOVE
    # that guard -- must have been entered first.
    with pytest.raises(StatusConflictError):
        finalize_session(session, "killed", reason="illegal re-transition")

    assert calls == ["failed", "killed"], "flush must run even on a rejected re-transition"


def test_flush_entered_on_cas_conflict(cleanup, monkeypatch):
    """A CAS conflict (concurrent writer changed the on-disk status) still
    flushes before raising."""
    sid = f"{SID_PREFIX}invariant-cas"
    cleanup.append(sid)
    session = _make_session(sid, text=ORIGINAL_REPLY, status="running")

    import agent.session_health as session_health

    original = session_health.flush_deferred_self_draft_sync
    calls = []

    def _spy(sess, status=None):
        calls.append(status)
        return original(sess, status)

    monkeypatch.setattr(session_health, "flush_deferred_self_draft_sync", _spy)

    # Concurrent writer changes the on-disk status out from under `session`'s
    # in-memory snapshot (still "running").
    fresh = list(AgentSession.query.filter(session_id=sid))[0]
    fresh.status = "abandoned"
    fresh.save(update_fields=["status", "updated_at"])

    with pytest.raises(StatusConflictError):
        finalize_session(session, "completed", reason="stale snapshot")

    assert calls == ["completed"], "flush must run even on a CAS conflict"


# ---------------------------------------------------------------------------
# 11. Post-delivery flag clear (#3053 correction to the "self-clearing" claim)
# ---------------------------------------------------------------------------


def test_flush_clears_pending_flag_after_successful_delivery(cleanup):
    sid = f"{SID_PREFIX}clear-on-success"
    cleanup.append(sid)
    session = _make_session(sid, text=ORIGINAL_REPLY)

    finalize_session(session, "completed", reason="clear test")

    assert _outbox_count(sid) == 1
    fresh = list(AgentSession.query.filter(session_id=sid))[0]
    assert not fresh.extra_context.get("deferred_self_draft_pending"), (
        "the flush must clear deferred_self_draft_pending after a successful delivery"
    )
    assert not fresh.extra_context.get("deferred_self_draft_text"), (
        "the flush must clear deferred_self_draft_text after a successful delivery"
    )


def test_flush_failing_clear_does_not_lose_the_delivery(cleanup, monkeypatch, caplog):
    """If the post-delivery clear itself raises, the delivery already happened
    and must NOT be undone or reported as a failure — WARNING logged, no raise."""
    sid = f"{SID_PREFIX}clear-failure"
    cleanup.append(sid)
    session = _make_session(sid, text=ORIGINAL_REPLY)

    import models.agent_session as agent_session_mod

    original_save = agent_session_mod.AgentSession.save
    call_count = {"n": 0}

    def _boom_on_second_save(self, *args, **kwargs):
        call_count["n"] += 1
        if kwargs.get("update_fields") == ["extra_context"]:
            raise RuntimeError("simulated clear-save failure")
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(agent_session_mod.AgentSession, "save", _boom_on_second_save)

    with caplog.at_level("WARNING", logger="agent.session_health"):
        finalize_session(session, "completed", reason="clear-failure test")

    assert _outbox_count(sid) == 1, "delivery must have happened despite the clear failure"
    assert any(
        "failed to clear" in rec.message.lower()
        for rec in caplog.records
        if rec.name == "agent.session_health"
    )


# ---------------------------------------------------------------------------
# 12. Idempotency log: DEBUG on the ordinary case, INFO when the AUTHORITATIVE
#     record still carries a pending deferral (#3053 AC3)
# ---------------------------------------------------------------------------


def test_double_finalize_stale_object_logs_debug_not_info(cleanup, caplog):
    """A double-``finalize_session("completed")`` on the SAME stale session
    object (the shape of ``test_exactly_once_when_finalize_completed_called_twice``)
    logs the idempotency early-return at DEBUG, not INFO — pinning that the
    INFO promotion reads the AUTHORITATIVE record (where the flag was already
    cleared by the first flush), never the caller's stale parameter."""
    sid = f"{SID_PREFIX}idempotent-debug"
    cleanup.append(sid)
    session = _make_session(sid, text=ORIGINAL_REPLY)

    finalize_session(session, "completed", reason="first")

    with caplog.at_level("DEBUG", logger="models.session_lifecycle"):
        finalize_session(session, "completed", reason="second (stale object)")

    idem_records = [
        rec
        for rec in caplog.records
        if rec.name == "models.session_lifecycle" and "already in terminal state" in rec.message
    ]
    assert len(idem_records) == 1
    assert idem_records[0].levelname == "DEBUG"


def test_idempotency_promotes_to_info_when_pending_still_set(cleanup, caplog, monkeypatch):
    """If the authoritative record still carries a pending deferral at the
    time of a double-finalize (flush failed or was bypassed), the idempotency
    early-return logs at INFO."""
    sid = f"{SID_PREFIX}idempotent-info"
    cleanup.append(sid)
    session = _make_session(sid, text=ORIGINAL_REPLY)

    # Simulate the flush having failed to deliver/clear: patch it to a no-op
    # so the pending flag survives the first finalize.
    import agent.session_health as session_health

    monkeypatch.setattr(session_health, "flush_deferred_self_draft_sync", lambda *a, **k: None)

    finalize_session(session, "completed", reason="first (flush no-op)")
    fresh = list(AgentSession.query.filter(session_id=sid))[0]
    assert fresh.extra_context.get("deferred_self_draft_pending") is True

    with caplog.at_level("INFO", logger="models.session_lifecycle"):
        finalize_session(fresh, "completed", reason="second re-finalize")

    idem_records = [
        rec
        for rec in caplog.records
        if rec.name == "models.session_lifecycle" and "already in terminal state" in rec.message
    ]
    assert len(idem_records) == 1
    assert idem_records[0].levelname == "INFO"
    assert "deferred_self_draft_pending" in idem_records[0].message


# ---------------------------------------------------------------------------
# 13. LIFECYCLE-log swallow promoted to WARNING (#3053 AC3)
# ---------------------------------------------------------------------------


def test_lifecycle_log_failure_logs_warning_and_does_not_block_status_write(
    cleanup, monkeypatch, caplog
):
    sid = f"{SID_PREFIX}lifecycle-log-fail"
    cleanup.append(sid)
    session = _make_session(sid, pending=False)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated lifecycle log failure")

    monkeypatch.setattr(session, "log_lifecycle_transition", _boom)

    with caplog.at_level("WARNING", logger="models.session_lifecycle"):
        finalize_session(session, "completed", reason="lifecycle log failure")

    fresh = list(AgentSession.query.filter(session_id=sid))[0]
    assert fresh.status == "completed", "a raising LIFECYCLE log must not block the status write"
    assert any(
        "lifecycle log failed" in rec.message.lower()
        for rec in caplog.records
        if rec.name == "models.session_lifecycle"
    )
    assert any(
        rec.levelname == "WARNING" for rec in caplog.records if "Lifecycle log" in rec.message
    )


# ---------------------------------------------------------------------------
# 14. waiting_for_children -> _finalize_parent_sync re-enters the hoisted flush
# ---------------------------------------------------------------------------


def test_waiting_for_children_parent_flushed_via_finalize_parent_sync(cleanup):
    from models.session_lifecycle import _finalize_parent_sync

    parent_sid = f"{SID_PREFIX}parent-wfc"
    child_sid = f"{SID_PREFIX}child-wfc"
    cleanup.append(parent_sid)
    cleanup.append(child_sid)

    parent = _make_session(
        parent_sid, pending=True, text=ORIGINAL_REPLY, status="waiting_for_children"
    )
    child = AgentSession(
        session_id=child_sid,
        project_key="test-dsd",
        status="completed",
        parent_agent_session_id=parent.id,
    )
    child.save()

    _finalize_parent_sync(
        parent.id,
        completing_child_id=child.agent_session_id,
        completing_child_status="completed",
    )

    payloads = _outbox_payloads(parent_sid)
    assert len(payloads) == 1, (
        "a waiting_for_children parent must be flushed once all children finalize"
    )
    assert payloads[0]["text"] == ORIGINAL_REPLY

    try:
        child.delete()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 15. Static terminal-writer guard (#3053 AC2): pins the set of code paths
#     that assign a terminal status outside models/session_lifecycle.py.
# ---------------------------------------------------------------------------

_TERMINAL_STATUS_ASSIGNMENT_RE = re.compile(
    r'\.status\s*=\s*["\'](completed|failed|killed|abandoned|cancelled)["\']'
)

# The three known last-resort bypass sites (Technical Approach step 4) --
# each now calls flush_deferred_self_draft_sync() first as belt-and-braces
# redundancy over a path the finalize_session hoist and the backstop sweep
# already cover. A new bypass anywhere else must fail this test.
_KNOWN_BYPASS_FILE = "agent/session_executor.py"
_KNOWN_BYPASS_COUNT = 3


def test_no_new_terminal_writer_bypasses_outside_lifecycle():
    """Any direct ``<obj>.status = "<terminal>"`` assignment in tracked,
    non-test production source must be one of the three known
    ``agent/session_executor.py`` last-resort sites. A new bypass anywhere
    else — including a brand-new file — fails this test, forcing the author
    to either route through ``finalize_session`` or explicitly extend this
    guard (and add the matching flush call) instead of silently reintroducing
    an unguarded terminal-status write.
    """
    repo_root = Path(__file__).resolve().parents[2]
    offenders: dict[str, int] = {}

    for py_file in repo_root.rglob("*.py"):
        rel = py_file.relative_to(repo_root)
        rel_str = str(rel)
        if rel_str.startswith(
            (
                "tests/",
                ".venv/",
                ".worktrees/",
                ".claude/",
                "docs/",
            )
        ):
            continue
        if rel_str == "models/session_lifecycle.py":
            # The lifecycle module IS the authority for terminal writes.
            continue
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        count = len(_TERMINAL_STATUS_ASSIGNMENT_RE.findall(text))
        if count:
            offenders[rel_str] = offenders.get(rel_str, 0) + count

    assert offenders == {_KNOWN_BYPASS_FILE: _KNOWN_BYPASS_COUNT}, (
        f"Unexpected terminal-status bypass writer(s) outside "
        f"models/session_lifecycle.py: {offenders}. Route through "
        f"finalize_session(), or if this is a deliberate new last-resort "
        f"bypass, add a flush_deferred_self_draft_sync() call before it and "
        f"update this guard's expected count."
    )
