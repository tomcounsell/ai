"""Every answer route closes the poll, not only a tap (#2701 review blocker 2).

``mark_poll_steered`` is written by the vote path. Nothing else called it, so a
human who typed their answer instead of tapping left the registry row alive for
its full ``POLL_REGISTRY_TTL_S`` — ``session_has_open_poll`` kept reporting an
open question, the nudge loop took ``pause_open_question`` every turn, and the
session lost auto-continue for a day while advancing one turn per human message.

These tests assert the **wiring**, at the two seams every prose answer passes
through. ``tests/unit/test_poll_registry.py`` covers the registry function's own
semantics against real Redis; the defect was that nobody called it.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_event_message(chat_id: int = 12345, msg_id: int = 67890):
    event = MagicMock()
    event.chat_id = chat_id
    message = MagicMock()
    message.id = msg_id
    message.media = None
    return event, message


class TestSteeringLadderClosesThePoll:
    """The live / pending / live-guard routes all land in ``_ack_steering_routed``.

    Wiring it here rather than at each branch also covers the intake-classifier
    interjection call sites, which reach the same helper.
    """

    @pytest.mark.asyncio
    async def test_typed_reply_closes_the_open_poll(self):
        from bridge.telegram_bridge import _ack_steering_routed

        event, message = _make_event_message()
        with (
            patch("bridge.telegram_bridge.push_steering_message"),
            patch("bridge.telegram_bridge.set_reaction", new_callable=AsyncMock),
            patch(
                "bridge.telegram_bridge.record_telegram_message_handled",
                new_callable=AsyncMock,
            ),
            patch("bridge.poll_registry.mark_session_polls_steered") as close_out,
        ):
            await _ack_steering_routed(
                MagicMock(),
                event,
                message,
                session_id="sess-1",
                sender_name="Alice",
                text="option B, and skip the migration",
                log_context="[test] steer log",
                room_id="test|system",
            )

        close_out.assert_called_once_with("sess-1")

    @pytest.mark.asyncio
    async def test_abort_also_closes_the_poll(self):
        """An abort kills the session, so the question is moot either way.

        Leaving the row open would keep pausing a session that is already dead.
        """
        from bridge.telegram_bridge import _ack_steering_routed

        event, message = _make_event_message()
        with (
            patch("bridge.telegram_bridge.push_steering_message"),
            patch("bridge.telegram_bridge.set_reaction", new_callable=AsyncMock),
            patch(
                "bridge.telegram_bridge.record_telegram_message_handled",
                new_callable=AsyncMock,
            ),
            patch("bridge.poll_registry.mark_session_polls_steered") as close_out,
        ):
            await _ack_steering_routed(
                MagicMock(),
                event,
                message,
                session_id="sess-1",
                sender_name="Alice",
                text="stop",
                log_context="[test] abort log",
                room_id="test|system",
            )

        close_out.assert_called_once_with("sess-1")

    @pytest.mark.asyncio
    async def test_close_out_runs_after_the_steering_push(self):
        """The human's message reaches the steering inbox first.

        Bookkeeping hangs off the answer route and must never sit between the
        human's message and the inbox it is aimed at.
        """
        from bridge.telegram_bridge import _ack_steering_routed

        event, message = _make_event_message()
        calls = []
        with (
            patch(
                "bridge.telegram_bridge.push_steering_message",
                side_effect=lambda *a, **kw: calls.append("push"),
            ),
            patch("bridge.telegram_bridge.set_reaction", new_callable=AsyncMock),
            patch(
                "bridge.telegram_bridge.record_telegram_message_handled",
                new_callable=AsyncMock,
            ),
            patch(
                "bridge.poll_registry.mark_session_polls_steered",
                side_effect=lambda sid: calls.append("close_out"),
            ),
        ):
            await _ack_steering_routed(
                MagicMock(),
                event,
                message,
                session_id="sess-1",
                sender_name="Alice",
                text="option B",
                log_context="[test] steer log",
                room_id="test|system",
            )

        assert calls == ["push", "close_out"]


class TestResumeCompletedSessionClosesThePoll:
    """The completed branch is the mainline for a poll answer, not an edge case."""

    @staticmethod
    def _completed():
        completed = MagicMock()
        completed.session_id = "sess-1"
        completed.project_key = "valor"
        completed.working_dir = "/tmp/wd"
        completed.project_config = None
        completed.session_type = None
        return completed

    @pytest.mark.asyncio
    async def test_close_out_follows_the_re_enqueue(self):
        """The marker certifies a dispatch that already happened.

        `translate_poll_vote` reaches this on the COMPLETED mainline, and its own
        `mark_poll_steered` is written last for exactly this reason: the marker
        de-indexes the poll from POLL_OPEN_INDEX, and both `iter_unanswered_polls`
        and `poll_expired_unanswered` key on its absence. Closing first would make
        the release-and-retry handler inert against a raising dispatch.

        Closing after costs nothing: the dispatch only enqueues, so the resumed
        turn's registry read is orders of magnitude later.
        """
        from bridge.answer_routing import resume_completed_session

        calls = []

        with (
            patch(
                "bridge.poll_registry.mark_session_polls_steered",
                side_effect=lambda sid: calls.append(("close_out", sid)),
            ),
            patch(
                "bridge.telegram_bridge._build_completed_resume_text",
                return_value="augmented",
            ),
            patch(
                "bridge.dispatch.dispatch_telegram_session",
                new_callable=AsyncMock,
                side_effect=lambda **kw: calls.append(("dispatch", kw["session_id"])),
            ),
        ):
            await resume_completed_session(
                completed=self._completed(),
                text="option B",
                sender_name="Alice",
                telegram_chat_id="12345",
                telegram_message_id=67890,
            )

        assert calls == [("dispatch", "sess-1"), ("close_out", "sess-1")]

    @pytest.mark.asyncio
    async def test_a_failed_dispatch_leaves_the_poll_open(self):
        """The tap survives a dispatch failure and the retry can still find it.

        `translate_poll_vote` releases its claim on an exception so the next
        reconcile tick retries. That retry reads POLL_OPEN_INDEX and skips steered
        rows, so a close-out written before the dispatch would permanently swallow
        the human's answer.
        """
        from bridge.answer_routing import resume_completed_session

        calls = []

        with (
            patch(
                "bridge.poll_registry.mark_session_polls_steered",
                side_effect=lambda sid: calls.append(("close_out", sid)),
            ),
            patch(
                "bridge.telegram_bridge._build_completed_resume_text",
                return_value="augmented",
            ),
            patch(
                "bridge.dispatch.dispatch_telegram_session",
                new_callable=AsyncMock,
                side_effect=RuntimeError("queue down"),
            ),
            pytest.raises(RuntimeError, match="queue down"),
        ):
            await resume_completed_session(
                completed=self._completed(),
                text="option B",
                sender_name="Alice",
                telegram_chat_id="12345",
                telegram_message_id=67890,
            )

        assert calls == []


class TestAdoptionStillScansAndPromotesASteeredHint:
    """A steered hint is deliberately still adopted, not skipped.

    Adoption is the only thing that makes a Race-6 orphan's later tap routable
    at all: ``translate_poll_vote`` bails immediately at
    ``lookup_poll(poll_id) is None``, so an un-adopted row can never be reached
    by a subsequent vote regardless of what closed it out. Because ANY inbound
    chatter marks the hint (``mark_session_polls_steered``), skipping steered
    hints here would let an unrelated message permanently swallow a subsequent
    tap — exactly the failure class #2701 exists to remove. The scan-cost
    objection does not hold either: a successful adoption promotes and DELETES
    the pending row, so the normal case costs one scan, not one per tick.
    ``promote_pending_poll`` carries the steered marker onto the real row, so
    the promoted row is born steered at no extra cost.
    """

    @pytest.mark.asyncio
    async def test_steered_hint_is_still_scanned_and_the_promoted_row_is_born_steered(
        self, redis_test_db
    ):
        """Runs against real Redis, and the hint is genuinely marked steered.

        Patching `iter_pending_polls` and asserting the scan saw the hint would
        pass identically with the skip re-added, since nothing in that setup makes
        the hint steered. The marker has to be a real registry write, and the
        promoted row has to be read back, or the test asserts nothing.
        """
        import uuid

        from bridge import poll_registry as reg
        from bridge.poll_reconcile import adopt_orphaned_polls

        hint = uuid.uuid4().hex
        poll_id = f"test-{uuid.uuid4().hex[:12]}"
        try:
            reg.register_pending_poll(
                hint, chat_id=-1001, session_id="sess-1", question="Q?", options=["A", "B"]
            )
            # Unrelated chatter, not an answer — this is the case the skip broke.
            reg.mark_session_polls_steered("sess-1")
            assert reg.poll_steered(hint) is True

            # Scoped to this test's hint: the claimed db is shared with sibling
            # tests, and a blanket return would try to promote their rows onto
            # this poll id too.
            async def _find(_client, _peer, scanned_hint):
                return (67890, poll_id) if scanned_hint == hint else None

            with patch(
                "bridge.telegram_relay._find_already_sent_poll",
                new=_find,
            ):
                await adopt_orphaned_polls(MagicMock())

            # Adopted, so a later tap resolves a row instead of dying at
            # `lookup_poll(poll_id) is None` — and the row is born steered, so it
            # costs the reconcile loop nothing.
            assert reg.lookup_poll(poll_id) is not None
            assert reg.poll_steered(poll_id) is True
            assert reg.lookup_pending_poll(hint) is None
        finally:
            r = reg._redis()
            reg.delete_pending_poll(hint)
            r.delete(f"{reg.POLL_STEERED_PREFIX}{hint}")
            for prefix in (reg.POLL_ROW_PREFIX, reg.POLL_STEERED_PREFIX):
                r.delete(f"{prefix}{poll_id}")
            r.srem(reg.POLL_OPEN_INDEX, poll_id)
