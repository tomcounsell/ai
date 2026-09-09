"""Red-first regression tests for the ``response_delivered_at`` stamp (#3270).

Background: ``response_delivered_at`` had exactly two non-test writers
(``agent/session_executor.py`` under ``action == "deliver"``, and
``agent/session_completion.py`` gated on ``delivery_attempted``), and neither
fires for PM/eng sessions whose reply is deferred for self-draft. Those replies
are delivered by ``agent.session_health.flush_deferred_self_draft_sync``, which
RPUSHes straight to ``telegram:outbox:`` without stamping the row. The #918
duplicate-delivery guard ``_delivery_belongs_to_current_run`` therefore returned
``False`` for every such row, and the #944 orphan net kept requeuing rows that
had already answered the human.

Three layers are covered here:

* **A1** -- the flush stamps ``response_delivered_at`` on the authoritative row.
* **A1c** -- the stamp SURVIVES ``finalize_session``'s own trailing full
  ``session.save()`` on the caller's (possibly stale) object. Testing the flush
  in isolation is not sufficient: the flush is fresh-reading by design, so an
  isolated test is green while the production bug survives intact.
* **A1b** -- with stamp and caller-object mirror in place, the health check
  finalizes a delivered-but-unfinalized row ``completed`` instead of requeuing
  it to ``pending``. This is the orphan-bounce reproduction.

Real Redis (per-worker test db via the autouse ``redis_test_db`` fixture), real
``AgentSession`` rows through the ORM. Session ids use the ``test-rda-`` prefix
and are deleted in teardown.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from agent.session_health import flush_deferred_self_draft_sync
from models.agent_session import AgentSession
from models.session_lifecycle import finalize_session, get_authoritative_session

SID_PREFIX = "test-rda-"

REPLY = (
    "I committed the card and opened the pull request as requested. "
    "The change is ready for your review whenever you have a moment."
)


def _redis():
    """The live test-db Redis handle the flush writes its outbox payload through."""
    import popoto.redis_db as rdb

    return rdb.POPOTO_REDIS_DB


def _outbox_len(session_id: str) -> int:
    return _redis().llen(f"telegram:outbox:{session_id}")


def _make_session(
    session_id: str,
    *,
    status: str = "running",
    chat_id: str = "test-rda-chat",
    started_minutes_ago: int = 120,
    pending_self_draft: bool = True,
) -> AgentSession:
    """Create and SAVE a real row carrying a pending deferred self-draft."""
    extra_context: dict = {"transport": "telegram"}
    if pending_self_draft:
        extra_context["deferred_self_draft_pending"] = True
        extra_context["deferred_self_draft_text"] = REPLY
    started = datetime.now(tz=UTC) - timedelta(minutes=started_minutes_ago)
    return AgentSession.create(
        session_id=session_id,
        session_type="teammate",
        project_key="test-rda",
        status=status,
        chat_id=chat_id,
        telegram_message_id=4242,
        sender_name="TestUser",
        message_text="commit the card and open a PR",
        extra_context=extra_context,
        created_at=started,
        started_at=started,
        updated_at=started,
        turn_count=1,
        tool_call_count=0,
    )


@pytest.fixture
def cleanup(redis_test_db):
    """Track created session_ids; remove rows, outboxes and dedup keys in teardown."""
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
                r.delete(f"self_draft_fallback_sent:{sid}:{rid}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# A1 -- the flush stamps the field
# ---------------------------------------------------------------------------


def test_flush_stamps_response_delivered_at(cleanup):
    """``flush_deferred_self_draft_sync`` stamps ``response_delivered_at``.

    Red before the fix: the flush writes the outbox payload and returns without
    ever touching the field, leaving it ``None`` and starving the #918 guard.
    """
    sid = f"{SID_PREFIX}flush-stamp"
    cleanup.append(sid)
    session = _make_session(sid)

    assert flush_deferred_self_draft_sync(session, "completed") is True
    assert _outbox_len(sid) == 1, "precondition: the reply was delivered"

    fresh = get_authoritative_session(sid)
    assert fresh is not None
    assert fresh.response_delivered_at is not None, (
        "the flush delivered the reply to the human but never stamped "
        "response_delivered_at, so the #918 delivery guard can never fire"
    )


# ---------------------------------------------------------------------------
# A1c -- the stamp survives finalize_session's trailing full save
# ---------------------------------------------------------------------------


def test_stamp_survives_finalize_session_with_stale_caller_object(cleanup):
    """End-to-end ``finalize_session`` with a STALE caller object.

    ``finalize_session`` invokes the flush (fresh-reading, writes the
    authoritative record) and then runs a bare ``session.save()`` on the
    caller's own object -- a full popoto HSET that writes back whatever the
    caller's in-memory copy holds. A caller whose ``response_delivered_at`` is
    still ``None`` therefore erases the stamp microseconds after it lands,
    unless the flush also mirrors the value onto the caller's object.

    RED both against unfixed code (nothing stamps at all) and against a
    fresh-object-only fix (the trailing save clobbers it). The isolated flush
    test above is green in the second case -- that gap is the point.
    """
    sid = f"{SID_PREFIX}stale-caller"
    cleanup.append(sid)
    stale_caller = _make_session(sid)
    assert stale_caller.response_delivered_at is None, "precondition: caller snapshot is unstamped"

    finalize_session(stale_caller, "completed", reason="test: turn end")

    fresh = get_authoritative_session(sid)
    assert fresh is not None
    assert fresh.status == "completed"
    assert fresh.response_delivered_at is not None, (
        "finalize_session's trailing full save on the caller's stale object "
        "resurrected response_delivered_at=None over the flush's stamp"
    )


# ---------------------------------------------------------------------------
# A1b -- orphan-bounce reproduction through the real health check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_finalizes_delivered_row_instead_of_requeuing(cleanup):
    """The orphan-bounce reproduction (#944 net vs. #918 guard).

    A row that answered the human through the deferred-self-draft flush and
    then had its ``status`` corrupted back to ``running`` by a stale full save
    must be finalized ``completed`` by the health check, not requeued to
    ``pending`` (which posts a second, unprompted reply).

    Red before the fix: ``response_delivered_at`` is ``None``, so
    ``_delivery_belongs_to_current_run`` returns ``False`` and the row is
    recovered to ``pending``.
    """
    sid = f"{SID_PREFIX}orphan-bounce"
    cleanup.append(sid)
    session = _make_session(sid)

    # The reply reaches the human through the real terminal-path flush.
    finalize_session(session, "completed", reason="test: turn end")
    assert _outbox_len(sid) == 1, "precondition: the reply was delivered"

    # A concurrent stale full save rewrites the status back to running -- the
    # corruption this issue documents. Written narrowly so nothing else moves.
    corrupted = get_authoritative_session(sid)
    assert corrupted is not None
    corrupted.status = "running"
    corrupted.save(update_fields=["status"])

    entry = get_authoritative_session(sid)
    assert entry is not None and entry.status == "running", "precondition: row reads running"

    mock_cls = MagicMock()
    mock_cls.query.filter.return_value = [entry]
    with (
        patch("agent.session_health.AgentSession", mock_cls),
        patch("agent.session_health._active_workers", {}),
        patch("agent.session_health._active_sessions", {}),
    ):
        from agent.session_health import _agent_session_health_check

        await _agent_session_health_check()

    after = get_authoritative_session(sid)
    assert after is not None
    assert after.status == "completed", (
        "the health check requeued a row that had already answered the human -- "
        f"got status={after.status!r}; each requeue posts another unprompted reply"
    )


# ---------------------------------------------------------------------------
# The stamp arms a guard that must not fire on a LIVE row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_leaves_a_live_fenced_delivered_row_alone(cleanup):
    """A running row with a LIVE execution fence is never finalized here.

    The delivery guard in ``_agent_session_health_check`` deliberately skips
    the ``worker_alive``/``_has_progress`` evaluation, so before #3270 the only
    thing keeping it off a mid-turn row was that ``response_delivered_at`` was
    effectively never set. A1 changed that: the redraft path in
    ``agent/output_handler.py::send`` now stamps mid-run, while the row is
    still ``running``. A long eng/PM run that answers the human and then keeps
    working past the 300s health-check cadence therefore presents exactly the
    shape this guard finalizes -- and finalizing it writes a terminal status
    onto a row its runner is still driving, which is the mid-turn corruption
    #3270 exists to stop.

    The fence here is this test process' own pid, so ``fence_is_live`` answers
    with a real, unmocked liveness verdict.
    """
    import os

    from agent.pid_fence import proc_create_time

    sid = f"{SID_PREFIX}live-fence"
    cleanup.append(sid)
    session = _make_session(sid, pending_self_draft=False)
    session.stamp_execution_spawn(
        pid=os.getpid(),
        create_time=proc_create_time(os.getpid()),
        cwd="/tmp",
        harness="claude",
    )
    session.response_delivered_at = datetime.now(tz=UTC)
    session.save()

    entry = get_authoritative_session(sid)
    assert entry is not None and entry.status == "running"
    assert (entry.live_fence or {}).get("pid") == os.getpid(), "precondition: fence is bound"

    # The worker bookkeeping also says this row is being executed, so the ONLY
    # thing that can finalize it in this pass is the delivery guard.
    mock_cls = MagicMock()
    mock_cls.query.filter.return_value = [entry]
    live_worker = MagicMock()
    live_worker.done.return_value = False
    with (
        patch("agent.session_health.AgentSession", mock_cls),
        patch("agent.session_health._active_workers", {entry.worker_key: live_worker}),
        patch("agent.session_health._active_sessions", {entry.agent_session_id: MagicMock()}),
    ):
        from agent.session_health import _agent_session_health_check

        await _agent_session_health_check()

    after = get_authoritative_session(sid)
    assert after is not None
    assert after.status == "running", (
        "the delivery guard finalized a row whose runner is still executing -- "
        f"got status={after.status!r}, the mid-turn terminal-status corruption of #3270"
    )


@pytest.mark.asyncio
async def test_health_check_still_finalizes_a_delivered_row_with_a_dead_fence(cleanup):
    """The twin of the test above: no live fence, so #918's guard still fires.

    Same row shape, same fresh ``response_delivered_at`` -- only the fence
    differs (a pid that is not ours). The liveness gate must narrow the guard
    to genuinely stranded rows, not disable it.
    """
    from agent.pid_fence import proc_create_time

    sid = f"{SID_PREFIX}dead-fence"
    cleanup.append(sid)
    session = _make_session(sid, pending_self_draft=False)
    # A recorded create_time that cannot match whatever holds this pid now:
    # `fence_is_live` answers False for both "dead" and "recycled".
    session.stamp_execution_spawn(
        pid=999_999,
        create_time=(proc_create_time(1) or 1.0) - 10_000.0,
        cwd="/tmp",
        harness="claude",
    )
    session.response_delivered_at = datetime.now(tz=UTC)
    session.save()

    entry = get_authoritative_session(sid)
    assert entry is not None and entry.status == "running"

    # Identical worker bookkeeping to the live-fence twin above, so the fence
    # is the ONLY variable between the two tests.
    mock_cls = MagicMock()
    mock_cls.query.filter.return_value = [entry]
    live_worker = MagicMock()
    live_worker.done.return_value = False
    with (
        patch("agent.session_health.AgentSession", mock_cls),
        patch("agent.session_health._active_workers", {entry.worker_key: live_worker}),
        patch("agent.session_health._active_sessions", {entry.agent_session_id: MagicMock()}),
    ):
        from agent.session_health import _agent_session_health_check

        await _agent_session_health_check()

    after = get_authoritative_session(sid)
    assert after is not None
    assert after.status == "completed", (
        "the #918 duplicate-delivery guard stopped firing on a stranded "
        f"delivered row -- got status={after.status!r}"
    )


# ---------------------------------------------------------------------------
# A1c sibling -- the output_handler.send stamp, through a stale-passing caller
# ---------------------------------------------------------------------------


def test_send_stamp_survives_finalize_session_by_the_same_caller_object(cleanup):
    """The second stamp site, exercised through the caller shape that clobbers.

    The `finalize_session` caller enumeration classified two production paths
    as BOTH stale-passing and holding the very object handed to
    `TelegramRelayOutputHandler.send` as ``session=``:
    `agent/session_completion.py:1144` (`parent`, the same object passed to
    `send_cb` a few lines earlier) and the notice-delivery finalizes in
    `agent/session_health.py` (`entry`, routed into `send` via
    `deliver_system_notice`). Both then hand that object to `finalize_session`,
    whose trailing bare `session.save()` writes the object's whole in-memory
    snapshot back over the row.

    So this test reproduces that exact ordering on one object: redraft-success
    `send`, then `finalize_session` on the same object. Without the
    caller-object mirror the stamp is erased and the cleared
    `deferred_self_draft_pending` is re-armed with the originally rejected
    draft text -- which `_deferred_self_draft_backstop_sweep` selects on and
    re-delivers on top of the successful rewrite.
    """
    import asyncio

    from agent.output_handler import TelegramRelayOutputHandler
    from bridge.message_drafter import MessageDraft

    sid = f"{SID_PREFIX}send-stamp-stale"
    cleanup.append(sid)
    session = _make_session(sid)
    assert session.response_delivered_at is None, "precondition: unstamped"

    handler = TelegramRelayOutputHandler()
    handler._redis = MagicMock()

    async def _bypass_drafter(text, *, session=None, medium="telegram"):
        return MessageDraft(text=text, artifacts={})

    with (
        patch("bridge.message_drafter.draft_message", _bypass_drafter),
        patch("agent.steering.reset_self_draft_attempts"),
    ):
        asyncio.run(handler.send(sid, "the successful rewrite", 0, session=session))

    # The caller now hands its OWN object to finalize_session, as the two
    # enumerated production sites do.
    finalize_session(session, "completed", reason="test: caller-object finalize")

    fresh = get_authoritative_session(sid)
    assert fresh is not None
    assert fresh.status == "completed"
    assert fresh.response_delivered_at is not None, (
        "finalize_session's trailing full save erased the send-path stamp"
    )
    assert not (fresh.extra_context or {}).get("deferred_self_draft_pending"), (
        "finalize_session's trailing full save re-armed deferred_self_draft_pending "
        "with the originally rejected draft text; the backstop sweep will re-deliver it"
    )
