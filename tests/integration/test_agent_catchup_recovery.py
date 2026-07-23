"""Integration test: agent-judgment ``/catchup`` recovers a sessioned-but-unanswered message.

This is the headline acceptance scenario for issue #1709:

A message whose session HUNG or was KILLED **without replying** is dedup-marked
``processed`` (it has a real ``DedupRecord`` entry) yet has NO Valor reply in the
thread. The mechanical catchup/reconciler key on "did a session get enqueued" and
so skip it FOREVER — recovery today requires manual ``DedupRecord`` surgery.

``/catchup`` keys on the THREAD instead ("did Valor actually reply?"). This test
proves, against REAL Redis (via the autouse ``redis_test_db`` isolation fixture)
and the REAL ``bridge.dedup`` write path:

1. A dedup-marked, reply-less message is DETECTED and enqueued EXACTLY ONCE — with
   NO manual ``DedupRecord`` surgery (the pre-existing dedup entry is left intact;
   ``/catchup`` reads the thread, not the dedup set, to decide).
2. A SECOND run does NOT double-enqueue. Idempotency comes from the landed-reply
   guard — the snapshot check plus the fresh pre-enqueue re-read — once the
   recovery reply lands in the thread, NOT from a dedup read (this module never
   reads the dedup set). The dedup write after enqueue only keeps the mechanical
   scanners' bookkeeping consistent; it does not gate this module's enqueue.

The LLM judge is STUBBED for determinism (it is the one nondeterministic external
dependency); everything else — the dedup ORM, the sweep/guard logic, the enqueue
contract — is the real code path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from bridge.agent_catchup import (
    UNANSWERED_NEEDS_REPLY,
    OwnedChat,
    sweep_chat,
)

# A recognizable test prefix so any leaked state is trivially identifiable.
TEST_PROJECT_KEY = "test-catchup-recovery"
TEST_CHAT_ID = 999000123


# ---------------------------------------------------------------------------
# Redis gate + dedup model import (skip cleanly if Redis is unavailable)
# ---------------------------------------------------------------------------


def _require_redis_and_dedup():
    """Import the dedup model + writers; skip the whole test if Redis is down.

    The autouse ``redis_test_db`` fixture points Popoto at an isolated test db, so
    importing and using ``DedupRecord`` here writes only to that db. If Redis is
    not reachable at all, skip with a clear reason rather than erroring.
    """
    try:
        import redis

        redis.Redis(db=1).ping()
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip(f"Redis unavailable — skipping agent-catchup recovery integration test: {e}")

    from bridge.dedup import (
        is_duplicate_message,
        record_last_processed,
        record_message_processed,
    )
    from models.dedup import DedupRecord

    return SimpleNamespace(
        DedupRecord=DedupRecord,
        is_duplicate_message=is_duplicate_message,
        record_message_processed=record_message_processed,
        record_last_processed=record_last_processed,
    )


def _make_msg(msg_id: int, text: str, *, out: bool = False, minutes_ago: int = 5, reactions=None):
    """Minimal Telethon-message-like object for ``read_thread``."""
    date = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    sender = SimpleNamespace(first_name="HungSessionUser", id=424242)

    async def _get_sender():
        return sender

    return SimpleNamespace(
        id=msg_id, text=text, out=out, date=date, get_sender=_get_sender, reactions=reactions
    )


def _valor_reaction():
    """A ``MessageReactions``-shaped fake where the current account chose a reaction.

    Mirrors the real Telethon shape confirmed by a live read (WS2 spike):
    ``results[i].chosen_order`` set (not ``None``) is the reliable self-reaction
    field — ``recent_reactions``/``my`` was observed unreliable.
    """
    return SimpleNamespace(results=[SimpleNamespace(chosen_order=0)])


class FakeClient:
    """In-memory Telethon stand-in; ``get_messages`` returns newest-first."""

    def __init__(self, entity, messages):
        self._entity = entity
        self._messages = messages

    async def get_messages(self, entity, limit=None):
        msgs = self._messages
        return msgs[:limit] if limit else msgs


class SpyEnqueue:
    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_recovers_hung_session_exactly_once_no_surgery():
    """A dedup-marked, reply-less message is recovered EXACTLY ONCE, no surgery.

    Setup mirrors a hung/killed session:
    - The inbound message already has a ``DedupRecord`` entry (a session WAS
      enqueued for it — that's why the mechanical scanners skip it forever).
    - The thread has NO Valor reply after it (the session died without replying).

    ``/catchup`` reads the thread, the stubbed judge returns
    ``UNANSWERED_NEEDS_REPLY``, and the sweep enqueues exactly one recovery
    session through the REAL dedup write path — WITHOUT anyone clearing the
    pre-existing ``DedupRecord`` entry first (no manual surgery).

    Then a SECOND sweep — now with the recovery reply landed in the thread — does
    NOT enqueue again (double-reply guard).
    """
    dep = _require_redis_and_dedup()

    inbound_id = 50001
    inbound_text = "Did the deploy finish? I haven't heard back."

    try:
        # --- Simulate the hung session: the message IS dedup-marked already. ---
        await dep.record_message_processed(TEST_CHAT_ID, inbound_id)
        assert await dep.is_duplicate_message(TEST_CHAT_ID, inbound_id), (
            "precondition: the message must already be dedup-marked (a session "
            "was enqueued for it) — this is exactly what the mechanical scanners "
            "skip forever"
        )

        # The thread: ONLY the inbound human message, NO Valor reply after it.
        entity = object()
        messages = [_make_msg(inbound_id, inbound_text, out=False, minutes_ago=10)]
        client = FakeClient(entity, messages)

        chat = OwnedChat(
            chat_id=TEST_CHAT_ID,
            chat_title="Dev: Cyndra",
            project={"_key": TEST_PROJECT_KEY, "working_directory": "/tmp/catchup-test"},
            entity=entity,
        )
        enqueue = SpyEnqueue()

        async def judge_unanswered(transcript, text, mid):
            # The thread shows no Valor reply → genuinely unanswered.
            return UNANSWERED_NEEDS_REPLY

        # --- FIRST sweep: real dedup write path, NO manual surgery performed. ---
        result1 = await sweep_chat(
            client,
            chat,
            enqueue_fn=enqueue,
            judge_fn=judge_unanswered,
            record_processed_fn=dep.record_message_processed,
            record_last_fn=dep.record_last_processed,
        )

        assert result1.enqueued == 1, "the hung-session message must be recovered"
        assert len(enqueue.calls) == 1, "recovered EXACTLY once"
        call = enqueue.calls[0]
        assert call["session_id"] == f"tg_{TEST_PROJECT_KEY}_{TEST_CHAT_ID}_{inbound_id}"
        assert call["message_text"] == inbound_text, "enqueues the ORIGINAL inbound text"
        # Dedup still marked (it was before, and the recovery re-wrote it).
        assert await dep.is_duplicate_message(TEST_CHAT_ID, inbound_id)

        # --- SECOND sweep: the recovery session's reply has now landed. ---
        # get_messages is newest-first, so prepend the Valor reply.
        messages.insert(
            0, _make_msg(inbound_id + 1, "Yes, deploy finished.", out=True, minutes_ago=1)
        )
        result2 = await sweep_chat(
            client,
            chat,
            enqueue_fn=enqueue,
            judge_fn=judge_unanswered,
            record_processed_fn=dep.record_message_processed,
            record_last_fn=dep.record_last_processed,
        )

        assert result2.enqueued == 0, "second sweep must not double-enqueue"
        assert len(enqueue.calls) == 1, "still exactly one enqueue across both sweeps"

    finally:
        # Clean up the dedup record we created (Popoto ORM only — never raw Redis).
        for rec in dep.DedupRecord.query.filter(chat_id=str(TEST_CHAT_ID)):
            rec.delete()


@pytest.mark.asyncio
async def test_answered_thread_no_enqueue_even_when_dedup_marked():
    """Conservative default holds against real Redis: answered thread → no reply.

    A message that IS dedup-marked AND already has a Valor reply must produce
    ZERO enqueues. This is the conservative-default acceptance bar proven on the
    real dedup path: ``/catchup`` does not re-reply to an already-answered
    message just because it appears in the lookback window.
    """
    dep = _require_redis_and_dedup()

    inbound_id = 60001
    try:
        await dep.record_message_processed(TEST_CHAT_ID, inbound_id)

        entity = object()
        # newest-first: Valor reply (newest) then the human message → answered.
        messages = [
            _make_msg(inbound_id + 1, "Handled — all green.", out=True, minutes_ago=2),
            _make_msg(inbound_id, "Is the build passing?", out=False, minutes_ago=5),
        ]
        client = FakeClient(entity, messages)
        chat = OwnedChat(
            chat_id=TEST_CHAT_ID,
            chat_title="Dev: Cyndra",
            project={"_key": TEST_PROJECT_KEY, "working_directory": "/tmp/catchup-test"},
            entity=entity,
        )
        enqueue = SpyEnqueue()

        # Judge returns ANSWERED — the conservative, correct verdict here.
        async def judge_answered(transcript, text, mid):
            from bridge.agent_catchup import ANSWERED

            return ANSWERED

        result = await sweep_chat(
            client,
            chat,
            enqueue_fn=enqueue,
            judge_fn=judge_answered,
            record_processed_fn=dep.record_message_processed,
            record_last_fn=dep.record_last_processed,
        )

        assert enqueue.calls == [], "answered thread must produce no reply"
        assert result.enqueued == 0

    finally:
        for rec in dep.DedupRecord.query.filter(chat_id=str(TEST_CHAT_ID)):
            rec.delete()


@pytest.mark.asyncio
async def test_reaction_only_ack_no_enqueue_against_real_dedup(caplog):
    """WS2 headline scenario: a Valor emoji reaction (no reply message) → no re-enqueue.

    This is the bug this workstream fixes (issue #2204 root cause 2): a message
    Valor acknowledged via reaction only (the repo's preferred "I heard you"
    signal, no ``out`` reply message in the thread) used to be judged
    ``UNANSWERED_NEEDS_REPLY`` and re-enqueued. Proven here against REAL Redis: the
    message is dedup-marked (a session WAS enqueued originally) AND has no Valor
    reply message, ONLY a Valor reaction — and a judge stub that would say
    NEEDS_REPLY if ever called never gets the chance, because the reaction is
    recognized as ANSWERED at the thread-read layer before the judge runs.
    """
    dep = _require_redis_and_dedup()

    inbound_id = 70001
    inbound_text = "Got the report over to finance, thanks!"
    try:
        await dep.record_message_processed(TEST_CHAT_ID, inbound_id)

        entity = object()
        messages = [
            _make_msg(
                inbound_id, inbound_text, out=False, minutes_ago=5, reactions=_valor_reaction()
            )
        ]
        client = FakeClient(entity, messages)
        chat = OwnedChat(
            chat_id=TEST_CHAT_ID,
            chat_title="Dev: Cyndra",
            project={"_key": TEST_PROJECT_KEY, "working_directory": "/tmp/catchup-test"},
            entity=entity,
        )
        enqueue = SpyEnqueue()

        judge_calls = []

        async def judge_would_reply(transcript, text, mid):
            judge_calls.append(mid)
            return UNANSWERED_NEEDS_REPLY  # would enqueue if ever reached

        result = await sweep_chat(
            client,
            chat,
            enqueue_fn=enqueue,
            judge_fn=judge_would_reply,
            record_processed_fn=dep.record_message_processed,
            record_last_fn=dep.record_last_processed,
        )

        assert judge_calls == [], "a Valor-reacted message must never reach the judge"
        assert enqueue.calls == [], "reaction-only ack must not be re-enqueued"
        assert result.enqueued == 0

    finally:
        for rec in dep.DedupRecord.query.filter(chat_id=str(TEST_CHAT_ID)):
            rec.delete()
