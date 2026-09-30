"""Unit tests for the agent-judgment ``/catchup`` layer (``bridge/agent_catchup.py``).

These tests exercise the judgment scanner WITHOUT any real Telethon client, real
LLM, or real Redis. The seam: ``sweep_chat`` / ``run_sweep`` accept an injectable
``judge_fn`` and ``enqueue_fn`` and the thread is read off an in-memory fake
Telethon client. We stub the judge so behaviour is deterministic, and capture the
fake enqueue's kwargs so we can prove exactly what would be enqueued.

The system under test is NEVER mocked — only the LLM judge (an external,
nondeterministic dependency) and the Telethon I/O surface are faked. The
conservative-default and double-reply-guard logic is the real code path.

Primary success criteria asserted here:

- **Conservative default**: an already-answered thread produces ZERO enqueues.
- **Garbage/None judge output** → ``ANSWERED`` (no enqueue), proven both at the
  ``judge_message`` boundary (stubbed backend) and at the sweep boundary.
- **Narrow-except-continues**: a judge that raises for one chat logs a greppable
  ``[agent-catchup]`` WARNING and the sweep proceeds to the next chat.
- **No raw-error-leak**: the enqueued ``message_text`` is the ORIGINAL inbound
  text, and ``session_id`` matches ``tg_{project_key}_{chat_id}_{message_id}``.
- **Double-reply guard**: at most ONE enqueue per message id.
- **CLI ``main()`` exits 0 on partial failure** and the errored chat appears in
  the printed summary.
"""

from __future__ import annotations

import io
import os
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bridge.agent_catchup as ac
from bridge.agent_catchup import (
    ANSWERED,
    UNANSWERED_NEEDS_REPLY,
    UNANSWERED_NO_REPLY_NEEDED,
    ChatResult,
    OwnedChat,
    judge_message,
    run_sweep,
    sweep_chat,
)

# ---------------------------------------------------------------------------
# Fakes: in-memory Telethon client + spying enqueue + recording judge
# ---------------------------------------------------------------------------


def _make_msg(msg_id: int, text: str, *, out: bool = False, minutes_ago: int = 5, reactions=None):
    """A minimal Telethon-message-like object for ``read_thread``.

    ``read_thread`` reads ``.id``, ``.text``, ``.out``, ``.date``, ``.reactions``
    and (for inbound messages) ``await m.get_sender()``. ``get_messages`` returns
    newest-first. ``reactions`` defaults to ``None`` (no reactions), matching a
    real Telethon message with no reactions.
    """
    date = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    sender = SimpleNamespace(first_name="TestUser", id=12345)

    async def _get_sender():
        return sender

    return SimpleNamespace(
        id=msg_id,
        text=text,
        out=out,
        date=date,
        get_sender=_get_sender,
        reactions=reactions,
    )


def _valor_reaction():
    """A ``MessageReactions``-shaped fake where the current account chose a reaction.

    Mirrors the real Telethon shape confirmed by a live read (WS2 spike): the
    aggregated ``results`` entry the current account chose has ``chosen_order``
    set (not ``None``); this is the reliable self-reaction field.
    """
    return SimpleNamespace(results=[SimpleNamespace(chosen_order=0)])


def _human_only_reaction():
    """A ``MessageReactions``-shaped fake where only OTHER accounts reacted."""
    return SimpleNamespace(results=[SimpleNamespace(chosen_order=None)])


class FakeClient:
    """In-memory Telethon stand-in. ``get_messages`` returns newest-first."""

    def __init__(self, messages_by_entity: dict):
        # entity (any hashable) -> list[msg] newest-first
        self._messages = messages_by_entity

    async def get_messages(self, entity, limit=None):
        msgs = self._messages.get(entity, [])
        return msgs[:limit] if limit else msgs


class SpyEnqueue:
    """Records every enqueue call's kwargs so tests can assert on them."""

    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)


class CountingJudge:
    """A judge stub that returns a fixed verdict and counts its invocations."""

    def __init__(self, verdict: str):
        self.verdict = verdict
        self.calls: list[tuple] = []

    async def __call__(self, transcript: str, inbound_text: str, inbound_id: int) -> str:
        self.calls.append((transcript, inbound_text, inbound_id))
        return self.verdict

    @property
    def call_count(self) -> int:
        return len(self.calls)


def _owned_chat(chat_id=100, title="Dev: Popoto", project_key="popoto"):
    """Build an OwnedChat whose ``entity`` is just an opaque sentinel key."""
    entity = object()
    project = {"_key": project_key, "working_directory": "/tmp/proj"}
    return OwnedChat(chat_id=chat_id, chat_title=title, project=project, entity=entity)


async def _noop_record(*args, **kwargs):  # record_processed / record_last stubs
    return None


# ---------------------------------------------------------------------------
# 1. Conservative default: an already-answered thread → ZERO enqueues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_answered_thread_produces_zero_enqueues():
    """PRIMARY CRITERION: a thread the judge calls ANSWERED enqueues NOTHING.

    A human message followed by a Valor reply; the judge returns ANSWERED. This
    is the conservative-default acceptance bar — an already-answered thread MUST
    produce no recovery enqueue.
    """
    chat = _owned_chat()
    # newest-first: Valor reply (newest), then the human message.
    messages = [
        _make_msg(2, "Sure, here's the fix.", out=True, minutes_ago=4),
        _make_msg(1, "Please fix the bug", out=False, minutes_ago=5),
    ]
    client = FakeClient({chat.entity: messages})
    enqueue = SpyEnqueue()
    judge = CountingJudge(ANSWERED)

    result = await sweep_chat(
        client,
        chat,
        enqueue_fn=enqueue,
        judge_fn=judge,
        record_processed_fn=_noop_record,
        record_last_fn=_noop_record,
    )

    assert enqueue.calls == [], "answered thread must enqueue nothing"
    assert result.enqueued == 0
    assert result.messages_scanned == 2
    # The single inbound human message was judged exactly once.
    assert judge.call_count == 1


# ---------------------------------------------------------------------------
# 2. Empty thread → judge NOT called, zero enqueues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_thread_skips_judge_and_enqueues_nothing():
    """No messages → judge never invoked, zero enqueues."""
    chat = _owned_chat()
    client = FakeClient({chat.entity: []})
    enqueue = SpyEnqueue()
    judge = CountingJudge(UNANSWERED_NEEDS_REPLY)  # would enqueue if ever called

    result = await sweep_chat(
        client,
        chat,
        enqueue_fn=enqueue,
        judge_fn=judge,
        record_processed_fn=_noop_record,
        record_last_fn=_noop_record,
    )

    assert judge.call_count == 0, "judge must not be called for an empty thread"
    assert enqueue.calls == []
    assert result.messages_scanned == 0
    assert result.enqueued == 0


# ---------------------------------------------------------------------------
# 3. Whitespace-only / empty inbound text → skipped BEFORE the judge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_whitespace_only_inbound_skipped_before_judge():
    """A whitespace-only inbound message is filtered before the judge runs.

    The real inbound message (with text) IS judged; the whitespace one is not —
    so the judge sees exactly one call, for the real message only.
    """
    chat = _owned_chat()
    messages = [
        _make_msg(2, "   \n\t  ", out=False, minutes_ago=3),  # whitespace-only
        _make_msg(1, "real question?", out=False, minutes_ago=5),
    ]
    client = FakeClient({chat.entity: messages})
    enqueue = SpyEnqueue()
    judge = CountingJudge(ANSWERED)

    await sweep_chat(
        client,
        chat,
        enqueue_fn=enqueue,
        judge_fn=judge,
        record_processed_fn=_noop_record,
        record_last_fn=_noop_record,
    )

    judged_ids = [call[2] for call in judge.calls]
    assert judged_ids == [1], "only the non-whitespace inbound message is judged"
    assert enqueue.calls == []


# ---------------------------------------------------------------------------
# 4. LLM wrapper failure / junk verdict → conservative ANSWERED (no enqueue)
# ---------------------------------------------------------------------------


async def test_judge_message_llm_failure_returns_answered(monkeypatch):
    """``judge_message`` maps any ``run_typed`` failure to ANSWERED (#1925).

    PydanticAI's ``Literal`` schema forces one of the three valid verdicts
    directly (with a single auto-retry on mismatch) -- there is no more
    "garbage but non-raising" backend output to simulate; the only failure
    mode left is the wrapper raising after exhausting its retry, which must
    still map to the conservative default. ``judge_message`` NEVER raises.
    """
    monkeypatch.setattr(ac, "run_typed", AsyncMock(side_effect=RuntimeError("llm down")))

    verdict = await judge_message("Valor: hi\nUser: hello", "hello", 1)
    assert verdict == ANSWERED


@pytest.mark.asyncio
async def test_sweep_with_junk_judge_fn_enqueues_nothing():
    """A judge_fn returning junk strings never triggers an enqueue.

    Only the exact ``UNANSWERED_NEEDS_REPLY`` token enqueues; anything else
    (including garbage) is treated conservatively as no-reply-needed. This
    exercises ``sweep_chat``'s own verdict handling, independent of
    ``judge_message``'s internals -- ``judge_fn`` is any injectable async
    callable.
    """
    chat = _owned_chat()
    messages = [_make_msg(1, "anybody there?", out=False, minutes_ago=5)]
    client = FakeClient({chat.entity: messages})
    enqueue = SpyEnqueue()

    async def junk_judge(transcript, text, mid):
        return "GARBAGE_NOT_A_VERDICT"

    result = await sweep_chat(
        client,
        chat,
        enqueue_fn=enqueue,
        judge_fn=junk_judge,
        record_processed_fn=_noop_record,
        record_last_fn=_noop_record,
    )

    assert enqueue.calls == []
    assert result.enqueued == 0


@pytest.mark.asyncio
async def test_no_reply_needed_verdict_enqueues_nothing():
    """UNANSWERED_NO_REPLY_NEEDED is a no-op for enqueue (only NEEDS_REPLY acts)."""
    chat = _owned_chat()
    messages = [_make_msg(1, "thanks!", out=False, minutes_ago=5)]
    client = FakeClient({chat.entity: messages})
    enqueue = SpyEnqueue()
    judge = CountingJudge(UNANSWERED_NO_REPLY_NEEDED)

    result = await sweep_chat(
        client,
        chat,
        enqueue_fn=enqueue,
        judge_fn=judge,
        record_processed_fn=_noop_record,
        record_last_fn=_noop_record,
    )

    assert enqueue.calls == []
    assert result.enqueued == 0


# ---------------------------------------------------------------------------
# 5. Narrow-except-continues: one chat's judge raises → WARNING + continue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_raises_logs_warning_and_defaults_answered(caplog):
    """A judge that RAISES for a message logs a greppable WARNING, defaults ANSWERED.

    ``sweep_chat`` wraps each ``judge_fn`` call in a narrow try/except: on raise it
    logs ``[agent-catchup]`` at WARNING and treats the verdict as ANSWERED (no
    enqueue). The sweep does NOT abort.
    """
    chat = _owned_chat()
    messages = [_make_msg(1, "will this crash?", out=False, minutes_ago=5)]
    client = FakeClient({chat.entity: messages})
    enqueue = SpyEnqueue()

    async def raising_judge(transcript, text, mid):
        raise RuntimeError("judge exploded")

    with caplog.at_level("WARNING"):
        result = await sweep_chat(
            client,
            chat,
            enqueue_fn=enqueue,
            judge_fn=raising_judge,
            record_processed_fn=_noop_record,
            record_last_fn=_noop_record,
        )

    assert enqueue.calls == [], "a raising judge must not enqueue (conservative)"
    assert result.enqueued == 0
    assert any(ac.LOG_PREFIX in rec.message for rec in caplog.records), (
        "expected a greppable [agent-catchup] WARNING"
    )


@pytest.mark.asyncio
async def test_run_sweep_continues_after_chat_failure(caplog):
    """One chat erroring does NOT stop the sweep: the next chat is still judged.

    ``run_sweep`` wraps each ``sweep_chat`` in a narrow try/except. We make the
    FIRST chat blow up inside ``read_thread`` (the fake client raises for it), and
    assert the SECOND chat is still judged and enqueued, plus the errored chat is
    recorded with ``errored=True`` and a greppable WARNING is logged.
    """
    good_chat = _owned_chat(chat_id=200, title="Dev: Good", project_key="good")
    bad_chat = _owned_chat(chat_id=300, title="Dev: Bad", project_key="bad")

    class HalfBrokenClient:
        def __init__(self, good_entity, good_messages, bad_entity):
            self._good_entity = good_entity
            self._good_messages = good_messages
            self._bad_entity = bad_entity

        async def get_messages(self, entity, limit=None):
            if entity is self._bad_entity:
                raise RuntimeError("telethon read failed for this chat")
            return self._good_messages

    good_messages = [_make_msg(1, "genuinely unanswered question?", out=False, minutes_ago=5)]
    client = HalfBrokenClient(good_chat.entity, good_messages, bad_chat.entity)
    enqueue = SpyEnqueue()
    judge = CountingJudge(UNANSWERED_NEEDS_REPLY)

    with caplog.at_level("WARNING"):
        results = await run_sweep(
            client,
            [bad_chat, good_chat],  # bad first — prove the sweep continues
            enqueue_fn=enqueue,
            judge_fn=judge,
            record_processed_fn=_noop_record,
            record_last_fn=_noop_record,
        )

    by_id = {r.chat_id: r for r in results}
    assert by_id[300].errored is True, "the bad chat must be recorded as errored"
    assert by_id[300].error
    assert by_id[200].errored is False
    # The good chat was still judged + enqueued AFTER the bad one failed.
    assert len(enqueue.calls) == 1
    assert enqueue.calls[0]["chat_id"] == "200"
    assert any(ac.LOG_PREFIX in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# 6. Enqueued message_text == inbound text (never an error string); session_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_uses_original_inbound_text_and_session_id():
    """On UNANSWERED_NEEDS_REPLY, enqueue carries the ORIGINAL inbound text.

    Asserts:
    - ``message_text`` equals the exact inbound text (never a composed reply or
      an error/system string).
    - ``session_id`` == ``tg_{project_key}_{chat_id}_{message_id}``.
    - the original sender metadata is propagated.
    """
    chat = _owned_chat(chat_id=555, title="Dev: Popoto", project_key="popoto")
    inbound_text = "Can you redeploy the worker? It's been silent for an hour."
    messages = [_make_msg(42, inbound_text, out=False, minutes_ago=5)]
    client = FakeClient({chat.entity: messages})
    enqueue = SpyEnqueue()
    judge = CountingJudge(UNANSWERED_NEEDS_REPLY)

    result = await sweep_chat(
        client,
        chat,
        enqueue_fn=enqueue,
        judge_fn=judge,
        record_processed_fn=_noop_record,
        record_last_fn=_noop_record,
    )

    assert result.enqueued == 1
    assert len(enqueue.calls) == 1
    call = enqueue.calls[0]
    assert call["message_text"] == inbound_text, "must enqueue the ORIGINAL inbound text"
    assert call["session_id"] == "tg_popoto_555_42"
    assert call["project_key"] == "popoto"
    assert call["chat_id"] == "555"
    assert call["telegram_message_id"] == 42
    assert call["sender_name"] == "TestUser"
    # And it is NOT some error/system string.
    assert "error" not in call["message_text"].lower()
    assert "[agent-catchup]" not in call["message_text"]


@pytest.mark.asyncio
async def test_dedup_written_immediately_after_enqueue():
    """Dedup writers fire exactly once, right after a successful enqueue."""
    chat = _owned_chat(chat_id=777, project_key="popoto")
    messages = [_make_msg(9, "unanswered and needs a reply?", out=False, minutes_ago=5)]
    client = FakeClient({chat.entity: messages})
    enqueue = SpyEnqueue()
    judge = CountingJudge(UNANSWERED_NEEDS_REPLY)

    processed_calls: list[tuple] = []
    last_calls: list[tuple] = []

    async def rec_processed(chat_id, message_id):
        processed_calls.append((chat_id, message_id))

    async def rec_last(chat_id, message_id, date):
        last_calls.append((chat_id, message_id))

    await sweep_chat(
        client,
        chat,
        enqueue_fn=enqueue,
        judge_fn=judge,
        record_processed_fn=rec_processed,
        record_last_fn=rec_last,
    )

    assert processed_calls == [(777, 9)]
    assert last_calls == [(777, 9)]


# ---------------------------------------------------------------------------
# 7. Double-reply guard: a fresh Valor reply after the message → no enqueue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_double_reply_guard_blocks_enqueue_when_valor_replied_after():
    """If a Valor reply already follows the message, do NOT enqueue.

    Even when the judge (mistakenly) returns UNANSWERED_NEEDS_REPLY, the
    position-based double-reply guard sees a Valor ``out`` message AFTER the
    inbound message in the freshly-read thread and skips the enqueue. This guards
    the worst-case (double reply to a customer).
    """
    chat = _owned_chat()
    # newest-first: Valor reply (newest) AFTER the human message.
    messages = [
        _make_msg(2, "Already handled this.", out=True, minutes_ago=2),
        _make_msg(1, "the question", out=False, minutes_ago=5),
    ]
    client = FakeClient({chat.entity: messages})
    enqueue = SpyEnqueue()
    judge = CountingJudge(UNANSWERED_NEEDS_REPLY)  # judge says reply — guard overrides

    result = await sweep_chat(
        client,
        chat,
        enqueue_fn=enqueue,
        judge_fn=judge,
        record_processed_fn=_noop_record,
        record_last_fn=_noop_record,
    )

    assert enqueue.calls == [], "double-reply guard must block the enqueue"
    assert result.enqueued == 0


@pytest.mark.asyncio
async def test_fresh_pre_enqueue_reread_blocks_when_reply_lands_during_judgment(caplog):
    """Race-1: a reply that lands AFTER the snapshot but BEFORE enqueue blocks it.

    The snapshot read at the top of the sweep shows NO Valor reply, so the
    position-based snapshot guard does not fire and the judge returns
    UNANSWERED_NEEDS_REPLY. But by the time we reach the pre-enqueue re-read, a
    fresh Valor ``out`` reply has landed in the thread. ``_valor_replied_since``
    re-reads and sees it → NO enqueue. This is the exact double-reply this feature
    exists to prevent, and the snapshot guard alone cannot catch it.
    """
    chat = _owned_chat()
    inbound = _make_msg(1, "the question", out=False, minutes_ago=5)

    class RaceClient:
        """First read: snapshot (no reply). Subsequent reads: reply has landed."""

        def __init__(self):
            self._reads = 0

        async def get_messages(self, entity, limit=None):
            self._reads += 1
            if self._reads == 1:
                # Snapshot at top of sweep: ONLY the inbound message, no reply.
                return [inbound]
            # Pre-enqueue re-read: a fresh Valor reply has now landed (newest-first).
            return [_make_msg(2, "Just replied.", out=True, minutes_ago=0), inbound]

    client = RaceClient()
    enqueue = SpyEnqueue()
    judge = CountingJudge(UNANSWERED_NEEDS_REPLY)

    with caplog.at_level("WARNING"):
        result = await sweep_chat(
            client,
            chat,
            enqueue_fn=enqueue,
            judge_fn=judge,
            record_processed_fn=_noop_record,
            record_last_fn=_noop_record,
        )

    assert judge.call_count == 1, "the inbound message was judged (snapshot showed no reply)"
    assert enqueue.calls == [], "fresh pre-enqueue re-read must block the enqueue"
    assert result.enqueued == 0
    assert any(ac.LOG_PREFIX in rec.message for rec in caplog.records), (
        "expected a greppable [agent-catchup] WARNING when a reply lands during judgment"
    )


@pytest.mark.asyncio
async def test_pre_enqueue_reread_failure_falls_back_to_snapshot_and_enqueues(caplog):
    """A failing pre-enqueue re-read does not crash and preserves current behavior.

    The snapshot read succeeds (no reply → judge says NEEDS_REPLY, snapshot guard
    passes). The pre-enqueue re-read then RAISES. ``_valor_replied_since`` swallows
    the error, logs a greppable WARNING, and returns False — so the enqueue
    proceeds (we do not make behavior worse than the snapshot-only guard) and the
    sweep never crashes.
    """
    chat = _owned_chat()
    inbound = _make_msg(1, "still need an answer", out=False, minutes_ago=5)

    class RereadFailsClient:
        def __init__(self):
            self._reads = 0

        async def get_messages(self, entity, limit=None):
            self._reads += 1
            if self._reads == 1:
                return [inbound]  # snapshot OK
            raise RuntimeError("telethon flaked on the re-read")

    client = RereadFailsClient()
    enqueue = SpyEnqueue()
    judge = CountingJudge(UNANSWERED_NEEDS_REPLY)

    with caplog.at_level("WARNING"):
        result = await sweep_chat(
            client,
            chat,
            enqueue_fn=enqueue,
            judge_fn=judge,
            record_processed_fn=_noop_record,
            record_last_fn=_noop_record,
        )

    assert result.enqueued == 1, "re-read failure must not block the enqueue (no regression)"
    assert len(enqueue.calls) == 1
    assert any(ac.LOG_PREFIX in rec.message for rec in caplog.records), (
        "a greppable [agent-catchup] WARNING must be logged on re-read failure"
    )


@pytest.mark.asyncio
async def test_at_most_one_enqueue_per_message_across_two_sweeps():
    """A message is enqueued AT MOST once even if two sweeps both judge it.

    First sweep (no Valor reply yet) enqueues once. We then append a Valor reply
    to the thread (simulating the recovery session's reply landing) and run a
    second sweep — the double-reply guard prevents a second enqueue.
    """
    chat = _owned_chat(chat_id=888, project_key="popoto")
    inbound = _make_msg(11, "still waiting on this", out=False, minutes_ago=6)
    messages = [inbound]
    client = FakeClient({chat.entity: messages})
    enqueue = SpyEnqueue()
    judge = CountingJudge(UNANSWERED_NEEDS_REPLY)

    # First sweep: no Valor reply present → enqueues exactly once.
    await sweep_chat(
        client,
        chat,
        enqueue_fn=enqueue,
        judge_fn=judge,
        record_processed_fn=_noop_record,
        record_last_fn=_noop_record,
    )
    assert len(enqueue.calls) == 1

    # The recovery session replies — a fresh Valor out message lands AFTER it.
    # get_messages is newest-first, so prepend the reply.
    messages.insert(0, _make_msg(12, "On it.", out=True, minutes_ago=1))

    # Second sweep: guard sees the Valor reply → no second enqueue.
    await sweep_chat(
        client,
        chat,
        enqueue_fn=enqueue,
        judge_fn=judge,
        record_processed_fn=_noop_record,
        record_last_fn=_noop_record,
    )
    assert len(enqueue.calls) == 1, "at most one enqueue per message id"


# ---------------------------------------------------------------------------
# 8. Reaction-aware valor-catchup (WS2): a Valor reaction is thread-native ANSWERED
# ---------------------------------------------------------------------------


def test_valor_reacted_true_for_self_chosen_reaction():
    """``_valor_reacted`` reads ``results[i].chosen_order`` (the spike-confirmed field).

    A live Telethon read (WS2 spike) showed ``recent_reactions``/``my`` to be
    unreliable for the self-reaction signal; ``chosen_order`` on the aggregated
    ``results`` entry is what this module relies on instead.
    """
    msg = _make_msg(1, "hello", reactions=_valor_reaction())
    assert ac._valor_reacted(msg) is True


def test_valor_reacted_false_when_only_other_accounts_reacted():
    """A reaction from someone other than Valor must NOT count as 'handled' (Risk 3)."""
    msg = _make_msg(1, "hello", reactions=_human_only_reaction())
    assert ac._valor_reacted(msg) is False


@pytest.mark.parametrize(
    "reactions",
    [
        None,  # no reactions on the message at all
        SimpleNamespace(results=None),  # results explicitly None
        SimpleNamespace(results=[]),  # results present but empty
    ],
    ids=["reactions_none", "results_none", "results_empty"],
)
def test_valor_reacted_defaults_false_on_missing_or_empty_reactions(reactions):
    """Missing/None/empty reactions map to 'not reacted' — never an exception."""
    msg = _make_msg(1, "hello", reactions=reactions)
    assert ac._valor_reacted(msg) is False


def test_valor_reacted_defensive_against_shape_surprise():
    """A Telethon-shape surprise (unexpected object) must not raise — defaults False."""

    class Weird:
        @property
        def results(self):
            raise RuntimeError("unexpected shape")

    msg = _make_msg(1, "hello", reactions=Weird())
    assert ac._valor_reacted(msg) is False


@pytest.mark.asyncio
async def test_reaction_only_ack_judged_answered_without_calling_judge():
    """PRIMARY WS2 CRITERION: a Valor reaction on an inbound message → ANSWERED,
    no judge call, no enqueue — even if a stubbed judge would say NEEDS_REPLY.

    This is the reaction-only-ack case from the plan's Test Impact: Valor reacted
    to an inbound message (no reply message exists in the thread) → recognized as
    handled at the thread-read layer, closing the blind spot that used to cause a
    re-enqueue.
    """
    chat = _owned_chat()
    messages = [_make_msg(1, "thanks for the update!", out=False, reactions=_valor_reaction())]
    client = FakeClient({chat.entity: messages})
    enqueue = SpyEnqueue()
    judge = CountingJudge(UNANSWERED_NEEDS_REPLY)  # would enqueue if ever called

    result = await sweep_chat(
        client,
        chat,
        enqueue_fn=enqueue,
        judge_fn=judge,
        record_processed_fn=_noop_record,
        record_last_fn=_noop_record,
    )

    assert judge.call_count == 0, "a Valor-reacted message must not reach the judge"
    assert enqueue.calls == []
    assert result.enqueued == 0
    assert result.verdicts == [(1, ANSWERED)]


@pytest.mark.asyncio
async def test_human_reaction_on_inbound_message_still_reaches_judge():
    """A reaction from someone OTHER than Valor must not suppress judgment (Risk 3).

    If a human reacted (not Valor), the message is not thread-natively "handled" —
    it still goes through the normal judge path, so a genuinely unanswered message
    directed at Valor is not silently skipped just because a bystander reacted.
    """
    chat = _owned_chat()
    messages = [
        _make_msg(1, "@valorengels can you look?", out=False, reactions=_human_only_reaction())
    ]
    client = FakeClient({chat.entity: messages})
    enqueue = SpyEnqueue()
    judge = CountingJudge(UNANSWERED_NEEDS_REPLY)

    result = await sweep_chat(
        client,
        chat,
        enqueue_fn=enqueue,
        judge_fn=judge,
        record_processed_fn=_noop_record,
        record_last_fn=_noop_record,
    )

    assert judge.call_count == 1, "a non-Valor reaction must not bypass the judge"
    assert result.enqueued == 1
    assert len(enqueue.calls) == 1


# ---------------------------------------------------------------------------
# 9. CLI main() exits 0 on partial failure; errored chat appears in summary
# ---------------------------------------------------------------------------


def test_format_summary_includes_errored_chats():
    """``format_summary`` renders errored chats — never silently drops them."""
    results = [
        ChatResult(chat_title="Dev: Good", chat_id=200, messages_scanned=3, enqueued=1),
        ChatResult(chat_title="Dev: Bad", chat_id=300, errored=True, error="telethon boom"),
    ]
    summary = ac.format_summary(results)
    assert "Dev: Good" in summary
    assert "Dev: Bad" in summary
    assert "ERROR" in summary
    assert "telethon boom" in summary


def test_main_exits_zero_on_partial_failure_and_prints_errored_chat(monkeypatch):
    """``main()`` returns 0 even when a chat errored, and prints it in the summary.

    We stub ``_run_async`` to return a result list containing one errored chat
    (the realistic partial-failure shape) and capture stdout. ``main()`` must
    print the summary including the errored chat and return 0 (best-effort).
    """
    errored_results = [
        ChatResult(chat_title="Dev: Good", chat_id=200, messages_scanned=2, enqueued=0),
        ChatResult(chat_title="Dev: Bad", chat_id=300, errored=True, error="boom"),
    ]

    async def fake_run_async(lookback=None):
        return errored_results

    monkeypatch.setattr(ac, "_run_async", fake_run_async)
    monkeypatch.setattr("sys.argv", ["valor-catchup"])

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ac.main()

    out = buf.getvalue()
    assert rc == 0, "main() must always exit 0 (best-effort contract)"
    assert "Dev: Bad" in out, "the errored chat must appear in the printed summary"
    assert "ERROR" in out


def test_main_exits_zero_when_run_async_raises(monkeypatch):
    """A top-level crash inside the sweep still yields exit 0 (never crashes /update)."""

    async def boom(lookback=None):
        raise RuntimeError("everything is on fire")

    monkeypatch.setattr(ac, "_run_async", boom)
    monkeypatch.setattr("sys.argv", ["valor-catchup"])

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ac.main()

    assert rc == 0
    assert "aborted" in buf.getvalue().lower()


# ---------------------------------------------------------------------------
# Fixture parity: ollama -> Haiku swap (#1925 patch — plan Step 4 note)
# ---------------------------------------------------------------------------
#
# These call the REAL Haiku backend (mirrors
# tests/unit/test_work_request_classifier.py::TestLlmClassification) against a
# small, hand-labeled fixture set covering the judge's full verdict space.
# Everywhere else in this file, judge_message's backend is stubbed for
# determinism -- this class is the one place the actual model decision is
# pinned, guarding against a future model-swap regression in judgment quality.


@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="requires ANTHROPIC_API_KEY",
)
class TestJudgeMessageLlmClassification:
    """Real-Haiku fixture parity for judge_message (#1925)."""

    @pytest.mark.parametrize(
        "transcript, inbound_text",
        [
            (
                "User: What's the deploy status?\n"
                "Valor: The deploy finished successfully at 3:02pm, all green.",
                "What's the deploy status?",
            ),
            (
                "User: Can you merge PR 42?\nValor: Merged PR 42, tests are green.",
                "Can you merge PR 42?",
            ),
        ],
    )
    async def test_answered(self, transcript, inbound_text):
        verdict = await judge_message(transcript, inbound_text, 1)
        assert verdict == ANSWERED, f"Expected ANSWERED for: {inbound_text!r}, got: {verdict}"

    @pytest.mark.parametrize(
        "transcript, inbound_text",
        [
            (
                "User: @valorengels can you check why the build is failing?",
                "@valorengels can you check why the build is failing?",
            ),
            (
                "Alice: hey team\n"
                "Bob: morning\n"
                "User: @valorengels what's the status of the migration script?",
                "@valorengels what's the status of the migration script?",
            ),
        ],
    )
    async def test_unanswered_needs_reply(self, transcript, inbound_text):
        verdict = await judge_message(transcript, inbound_text, 2)
        assert verdict == UNANSWERED_NEEDS_REPLY, (
            f"Expected UNANSWERED_NEEDS_REPLY for: {inbound_text!r}, got: {verdict}"
        )

    @pytest.mark.parametrize(
        "transcript, inbound_text",
        [
            (
                "Alice: nice work everyone\nBob: agreed, great job",
                "nice work everyone",
            ),
            (
                "Alice: hey Bob, did you see the game last night?\nBob: yeah wild finish",
                "hey Bob, did you see the game last night?",
            ),
        ],
    )
    async def test_unanswered_no_reply_needed(self, transcript, inbound_text):
        verdict = await judge_message(transcript, inbound_text, 3)
        assert verdict == UNANSWERED_NO_REPLY_NEEDED, (
            f"Expected UNANSWERED_NO_REPLY_NEEDED for: {inbound_text!r}, got: {verdict}"
        )
