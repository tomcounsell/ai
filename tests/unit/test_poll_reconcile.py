"""Poll reconcile loop + FloodWaitError propagation on the inbound path (#3095).

Two halves:

1. **FloodWaitError re-raise sites.** A rate-limited Telegram RPC must reach
   ``poll_reconcile_loop``'s backoff branch (or ``process_outbox``'s on the
   send path) instead of being swallowed by a blanket handler that keeps the
   next scan on full cadence. One test per site; each fails if its
   ``except FloodWaitError: raise`` is removed.

2. **The reconcile loop's own behavioral claims** — the adaptive interval, the
   ambiguous-adoption bail, and the warn dedup — which the module docstring
   makes and nothing previously tested. Registry-backed tests use the real
   (test-db-claimed) Redis, mirroring ``test_poll_registry.py``; only the
   Telegram client boundary is faked.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telethon.errors import FloodWaitError

from bridge import poll_registry as reg
from bridge.answer_routing import AnswerTarget, AnswerTargetKind
from bridge.poll_reconcile import (
    _interval,
    adopt_orphaned_polls,
    poll_reconcile_loop,
    warn_if_expired_unanswered,
)
from bridge.poll_registry import (
    POLL_RECONCILE_FAST_INTERVAL_S,
    POLL_RECONCILE_FAST_WINDOW_S,
    POLL_RECONCILE_SLOW_INTERVAL_S,
)
from bridge.poll_vote import translate_poll_vote
from bridge.telegram_relay import _find_already_sent_poll

HINT = "a" * 32
ROW = {
    "chat_id": "-1003449100931",
    "msg_id": 1413,
    "session_id": "sess-1",
    "question": "Which approach?",
    "options": ["Approach A", "Approach B"],
    "created_at": "2026-09-02T08:00:00+00:00",
}


def _flood(seconds: int = 30) -> FloodWaitError:
    return FloodWaitError(request=None, capture=seconds)


def _client_iter_raising(exc: BaseException) -> MagicMock:
    """Client whose ``iter_messages`` fails on first iteration."""

    async def _gen(*_a, **_k):
        raise exc
        yield  # pragma: no cover — makes this an async generator

    client = MagicMock()
    client.iter_messages = _gen
    return client


def _poll_msg(msg_id: int, poll_id: int, hint: str) -> MagicMock:
    """A message carrying a real MessageMediaPoll whose option bytes match ``hint``."""
    from telethon.tl.types import MessageMediaPoll

    from bridge.response import encode_option

    poll = MagicMock()
    poll.id = poll_id
    poll.answers = [MagicMock(option=encode_option(0, hint))]
    msg = MagicMock()
    msg.id = msg_id
    msg.media = MessageMediaPoll(poll=poll, results=None)
    return msg


def _client_yielding(*msgs) -> MagicMock:
    async def _gen(*_a, **_k):
        for m in msgs:
            yield m

    client = MagicMock()
    client.iter_messages = _gen
    return client


def _client_with_vote(option_index: int = 0) -> MagicMock:
    """Client whose GetPollResultsRequest reports one voter on ``option_index``."""
    from bridge.response import encode_option

    results = MagicMock()
    results.results = [MagicMock(option=encode_option(option_index, HINT), voters=1)]
    update = MagicMock()
    update.results = results
    response = MagicMock()
    response.updates = [update]

    async def _call(_request):
        return response

    return MagicMock(side_effect=_call)


def _target(kind=AnswerTargetKind.LIVE) -> AnswerTarget:
    session = MagicMock()
    session.session_id = "sess-1"
    session.project_key = "proj"
    session.initial_telegram_message = {"sender_name": "Tom"}
    return AnswerTarget(kind=kind, session=session, matched_status="running")


class TestFloodWaitPropagation:
    """#3095 — each re-raise site, mutation-checked: remove it and the test fails."""

    @pytest.mark.asyncio
    async def test_already_sent_scan_reraises_floodwait(self):
        """Site 1: ``_find_already_sent_poll``'s scan handler must not swallow it."""
        with pytest.raises(FloodWaitError):
            await _find_already_sent_poll(_client_iter_raising(_flood()), -100, HINT)

    @pytest.mark.asyncio
    async def test_already_sent_scan_still_degrades_on_ordinary_error(self, caplog):
        with caplog.at_level(logging.WARNING):
            found = await _find_already_sent_poll(
                _client_iter_raising(ValueError("boom")), -100, HINT
            )
        assert found is None
        assert "poll lookup scan failed" in caplog.text

    @pytest.mark.asyncio
    async def test_adoption_loop_reraises_floodwait(self):
        """Site 1b: the adoption loop's per-hint handler must pass it through,
        or the relay-side re-raise can never reach the loop's backoff."""
        with (
            patch(
                "bridge.poll_reconcile.iter_pending_polls",
                return_value=[(HINT, dict(ROW))],
            ),
            patch(
                "bridge.telegram_relay._find_already_sent_poll",
                AsyncMock(side_effect=_flood()),
            ),
            pytest.raises(FloodWaitError),
        ):
            await adopt_orphaned_polls(MagicMock())

    @pytest.mark.asyncio
    async def test_adoption_loop_still_continues_on_ordinary_error(self, caplog):
        with (
            patch(
                "bridge.poll_reconcile.iter_pending_polls",
                return_value=[(HINT, dict(ROW))],
            ),
            patch(
                "bridge.telegram_relay._find_already_sent_poll",
                AsyncMock(side_effect=ValueError("boom")),
            ),
            patch("bridge.poll_reconcile.promote_pending_poll") as promote,
            caplog.at_level(logging.WARNING),
        ):
            await adopt_orphaned_polls(MagicMock())
        promote.assert_not_called()
        assert "poll adoption scan failed" in caplog.text

    @pytest.mark.asyncio
    async def test_results_read_reraises_floodwait(self):
        """Site 2: ``translate_poll_vote``'s GetPollResultsRequest handler."""
        with (
            patch("bridge.poll_vote.lookup_poll", return_value=dict(ROW)),
            pytest.raises(FloodWaitError),
        ):
            await translate_poll_vote(MagicMock(side_effect=_flood()), "poll-1")

    @pytest.mark.asyncio
    async def test_results_read_still_degrades_on_ordinary_error(self, caplog):
        with (
            patch("bridge.poll_vote.lookup_poll", return_value=dict(ROW)),
            caplog.at_level(logging.WARNING),
        ):
            await translate_poll_vote(MagicMock(side_effect=ValueError("boom")), "poll-1")
        assert "GetPollResultsRequest failed" in caplog.text

    @pytest.mark.asyncio
    async def test_dispatch_floodwait_releases_claim_and_reraises(self):
        """Site 3: a flood inside ``_dispatch_answer`` (here: ``get_messages``
        before the close) must release the claim like the generic failure path,
        then propagate instead of being logged-and-forgotten."""
        client = _client_with_vote()
        client.get_messages = AsyncMock(side_effect=_flood())
        with (
            patch("bridge.poll_vote.lookup_poll", return_value=dict(ROW)),
            patch("bridge.poll_vote.claim_poll_answer", return_value=True),
            patch("bridge.poll_vote.poll_dispatched", return_value=False),
            patch("bridge.poll_vote.release_poll_claim") as release,
            patch("bridge.poll_vote.mark_poll_steered") as steered,
        ):
            with pytest.raises(FloodWaitError):
                await translate_poll_vote(client, "poll-1")
        release.assert_called_once_with("poll-1")
        steered.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_ordinary_close_error_still_routes_the_answer(self):
        """The pre-existing behavior stays: a non-flood close failure never
        blocks the answer."""
        client = _client_with_vote()
        client.get_messages = AsyncMock(side_effect=ValueError("boom"))
        with (
            patch("bridge.poll_vote.lookup_poll", return_value=dict(ROW)),
            patch("bridge.poll_vote.claim_poll_answer", return_value=True),
            patch("bridge.poll_vote.poll_dispatched", return_value=False),
            patch("bridge.poll_vote.mark_poll_dispatched"),
            patch("bridge.poll_vote.mark_poll_steered") as steered,
            patch("bridge.poll_vote.release_poll_claim") as release,
            patch("bridge.poll_vote.resolve_answer_target", return_value=_target()),
            patch("agent.steering.push_steering_message") as push,
            patch("models.room.room_id_for_session", return_value="proj:room"),
        ):
            await translate_poll_vote(client, "poll-1")
        push.assert_called_once()
        steered.assert_called_once()
        release.assert_not_called()

    @pytest.mark.asyncio
    async def test_loop_backs_off_on_floodwait(self):
        """End of the chain: the loop converts the propagated flood into a
        padded, capped sleep instead of dying or continuing at full cadence."""
        sleeps = []

        async def _sleep(s):
            sleeps.append(s)
            raise asyncio.CancelledError

        with (
            patch(
                "bridge.poll_reconcile.reconcile_once",
                AsyncMock(side_effect=_flood(42)),
            ),
            patch("bridge.poll_reconcile.asyncio.sleep", _sleep),
            pytest.raises(asyncio.CancelledError),
        ):
            await poll_reconcile_loop(MagicMock())
        assert sleeps == [47]  # flood.seconds + 5

    @pytest.mark.asyncio
    async def test_loop_backoff_is_capped(self):
        sleeps = []

        async def _sleep(s):
            sleeps.append(s)
            raise asyncio.CancelledError

        with (
            patch(
                "bridge.poll_reconcile.reconcile_once",
                AsyncMock(side_effect=_flood(9999)),
            ),
            patch("bridge.poll_reconcile.asyncio.sleep", _sleep),
            pytest.raises(asyncio.CancelledError),
        ):
            await poll_reconcile_loop(MagicMock())
        assert sleeps == [300]


class TestAdaptiveInterval:
    """Fast right after a send, slow thereafter — the docstring's cadence claim."""

    def test_no_rows_means_slow(self):
        assert _interval(None) == POLL_RECONCILE_SLOW_INTERVAL_S

    def test_young_row_means_fast(self):
        assert _interval(POLL_RECONCILE_FAST_WINDOW_S - 1) == POLL_RECONCILE_FAST_INTERVAL_S

    def test_window_boundary_is_slow(self):
        """The window is exclusive: exactly at the boundary the fast phase ends."""
        assert _interval(POLL_RECONCILE_FAST_WINDOW_S) == POLL_RECONCILE_SLOW_INTERVAL_S

    @pytest.mark.asyncio
    async def test_loop_sleeps_fast_while_a_recent_poll_is_open(self):
        young_row = {"created_at": datetime.now(UTC).isoformat()}
        sleeps = []

        async def _sleep(s):
            sleeps.append(s)
            raise asyncio.CancelledError

        with (
            patch("bridge.poll_reconcile.reconcile_once", AsyncMock(return_value=1)),
            patch(
                "bridge.poll_reconcile.iter_unanswered_polls",
                return_value=[("p", young_row)],
            ),
            patch("bridge.poll_reconcile.asyncio.sleep", _sleep),
            pytest.raises(asyncio.CancelledError),
        ):
            await poll_reconcile_loop(MagicMock())
        assert sleeps == [POLL_RECONCILE_FAST_INTERVAL_S]


class TestAmbiguousAdoptionBail:
    """More than one candidate: adopt nothing, warn — never guess."""

    @pytest.mark.asyncio
    async def test_two_matches_adopt_nothing_and_warn(self, caplog):
        client = _client_yielding(_poll_msg(1, 11, HINT), _poll_msg(2, 22, HINT))
        with caplog.at_level(logging.WARNING):
            found = await _find_already_sent_poll(client, -100, HINT)
        assert found is None
        assert "poll_adoption_ambiguous" in caplog.text

    @pytest.mark.asyncio
    async def test_unique_match_returns_ids(self):
        client = _client_yielding(_poll_msg(7, 77, HINT))
        assert await _find_already_sent_poll(client, -100, HINT) == (7, 77)

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self):
        other_hint = "b" * 32
        client = _client_yielding(_poll_msg(7, 77, other_hint))
        assert await _find_already_sent_poll(client, -100, HINT) is None

    @pytest.mark.asyncio
    async def test_adoption_promotes_only_a_unique_find(self):
        with (
            patch(
                "bridge.poll_reconcile.iter_pending_polls",
                return_value=[(HINT, dict(ROW))],
            ),
            patch(
                "bridge.telegram_relay._find_already_sent_poll",
                AsyncMock(return_value=(7, 77)),
            ),
            patch("bridge.poll_reconcile.promote_pending_poll", return_value=True) as promote,
        ):
            await adopt_orphaned_polls(MagicMock())
        promote.assert_called_once_with(HINT, 77, msg_id=7)

    @pytest.mark.asyncio
    async def test_adoption_skips_when_scan_finds_nothing(self):
        with (
            patch(
                "bridge.poll_reconcile.iter_pending_polls",
                return_value=[(HINT, dict(ROW))],
            ),
            patch(
                "bridge.telegram_relay._find_already_sent_poll",
                AsyncMock(return_value=None),
            ),
            patch("bridge.poll_reconcile.promote_pending_poll") as promote,
        ):
            await adopt_orphaned_polls(MagicMock())
        promote.assert_not_called()


@pytest.fixture
def warned_poll_id(redis_test_db):
    """Unique poll id whose warn/steer keys are cleaned up, per test_poll_registry."""
    pid = f"test-{uuid.uuid4().hex[:12]}"
    yield pid
    r = reg._redis()
    r.delete(f"{reg.POLL_WARNED_PREFIX}{pid}")
    r.delete(f"{reg.POLL_STEERED_PREFIX}{pid}")


def _aged_row(age_s: float) -> dict:
    return dict(ROW, created_at=(datetime.now(UTC) - timedelta(seconds=age_s)).isoformat())


class TestWarnDedup:
    """``poll_expired_unanswered`` fires exactly once per poll, on the real registry."""

    def test_expired_unanswered_warns_once(self, warned_poll_id, caplog):
        row = _aged_row(reg.POLL_EXPIRY_WARN_AGE_S + 60)
        with caplog.at_level(logging.WARNING):
            warn_if_expired_unanswered(warned_poll_id, row)
        assert "poll_expired_unanswered" in caplog.text

        caplog.clear()
        with caplog.at_level(logging.WARNING):
            warn_if_expired_unanswered(warned_poll_id, row)
        assert "poll_expired_unanswered" not in caplog.text

    def test_young_poll_never_warns(self, warned_poll_id, caplog):
        with caplog.at_level(logging.WARNING):
            warn_if_expired_unanswered(warned_poll_id, _aged_row(1))
        assert "poll_expired_unanswered" not in caplog.text

    def test_steered_poll_never_warns(self, warned_poll_id, caplog):
        reg.mark_poll_steered(warned_poll_id)
        row = _aged_row(reg.POLL_EXPIRY_WARN_AGE_S + 60)
        with caplog.at_level(logging.WARNING):
            warn_if_expired_unanswered(warned_poll_id, row)
        assert "poll_expired_unanswered" not in caplog.text

    def test_unparseable_created_at_never_warns(self, warned_poll_id, caplog):
        """_row_age_s treats a bad timestamp as age 0 — below the warn age."""
        with caplog.at_level(logging.WARNING):
            warn_if_expired_unanswered(warned_poll_id, dict(ROW, created_at="not-a-date"))
        assert "poll_expired_unanswered" not in caplog.text
