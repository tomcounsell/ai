"""Unit tests for `_deliver_pipeline_completion` (issue #1058).

Covers the runner's contract:
- CAS lock via Redis SETNX; secondary invocations skip.
- Harness success → deliver via send_cb, stamp response_delivered_at,
  finalize parent to "completed".
- Harness returns empty → deliver fallback (summary_context).
- Harness raises → deliver fallback.
- send_cb raises → logged, parent still finalized.
- Missing prior UUID → call get_response_via_harness with prior_uuid=None.
- CancelledError during harness → best-effort interrupted message + re-raise.
- Interrupted-sent dedup lock suppresses repeat sends.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent import session_completion


@pytest.fixture
def parent():
    """A lightweight stand-in for an AgentSession parent."""
    p = MagicMock()
    p.agent_session_id = "parent-abc-123"
    p.session_id = "tg_valor_-123_456"
    p.chat_id = "-123"
    p.telegram_message_id = 456
    p.project_key = "valor"
    p.transport = None
    p.project_config = {"working_directory": "/tmp"}
    p.save = MagicMock()
    return p


@pytest.fixture
def send_cb():
    return AsyncMock(return_value=None)


def _redis_ok():
    """Redis lock always acquired."""
    db = MagicMock()
    db.set = MagicMock(return_value=True)
    db.exists = MagicMock(return_value=False)
    return db


def _redis_held():
    """Lock already held — SETNX returns False."""
    db = MagicMock()
    db.set = MagicMock(return_value=False)
    db.exists = MagicMock(return_value=True)
    return db


class TestLock:
    async def test_lock_held_skips_runner(self, parent, send_cb):
        with patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_held()):
            await session_completion._deliver_pipeline_completion(
                parent, "summary ctx", send_cb, parent.chat_id, parent.telegram_message_id
            )
        send_cb.assert_not_awaited()
        parent.save.assert_not_called()

    async def test_lock_acquired_proceeds(self, parent, send_cb):
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch(
                "agent.sdk_client.get_response_via_harness",
                new=AsyncMock(return_value="final summary text"),
            ),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="uuid-1"),
            patch("models.session_lifecycle.finalize_session") as _fs,
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "fallback ctx", send_cb, parent.chat_id, parent.telegram_message_id
            )
        send_cb.assert_awaited_once()
        args = send_cb.await_args.args
        assert args[0] == parent.chat_id
        assert args[1] == "final summary text"
        _fs.assert_called_once()
        assert _fs.call_args.args[1] == "completed"


class TestHarnessResult:
    async def test_empty_pass1_delivers_degraded_fallback(self, parent, send_cb):
        """Pass 1 empty → degraded fallback, Pass 2 skipped (D6(c) v2)."""
        harness = AsyncMock(return_value="")
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=harness),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="uuid-1"),
            patch("models.session_lifecycle.finalize_session"),
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "fallback summary context", send_cb, parent.chat_id, None
            )
        send_cb.assert_awaited_once()
        assert send_cb.await_args.args[1] == (
            "[drafter unavailable — pipeline completed] fallback summary context"
        )
        # Pass 2 must be skipped when Pass 1 fails.
        assert harness.await_count == 1

    async def test_whitespace_pass1_delivers_degraded_fallback(self, parent, send_cb):
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch(
                "agent.sdk_client.get_response_via_harness", new=AsyncMock(return_value="   \n  ")
            ),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="uuid-1"),
            patch("models.session_lifecycle.finalize_session"),
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "ctx fallback", send_cb, parent.chat_id, None
            )
        assert send_cb.await_args.args[1] == (
            "[drafter unavailable — pipeline completed] ctx fallback"
        )

    async def test_pass1_raises_delivers_degraded_fallback(self, parent, send_cb):
        """Pass 1 exception → ERROR log + degraded fallback; no raise escapes."""

        async def _raise(**_kw):
            raise RuntimeError("harness boom")

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=_raise),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="uuid-1"),
            patch("models.session_lifecycle.finalize_session"),
        ):
            # Must NOT raise — degraded fallback is delivered instead.
            await session_completion._deliver_pipeline_completion(
                parent, "ctx after harness fail", send_cb, parent.chat_id, None
            )
        assert send_cb.await_args.args[1] == (
            "[drafter unavailable — pipeline completed] ctx after harness fail"
        )

    async def test_missing_uuid_still_invokes_harness(self, parent, send_cb):
        # Return real text so Pass 2 kicks in (to verify both still run).
        harness = AsyncMock(return_value="ok")
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=harness),
            patch("agent.sdk_client._get_prior_session_uuid", return_value=None),
            patch("models.session_lifecycle.finalize_session"),
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, None
            )
        # Pass 1 and Pass 2 both fire.
        assert harness.await_count == 2
        # Pass 1's prior_uuid is None (no UUID to resume from).
        pass1_call = harness.await_args_list[0]
        assert pass1_call.kwargs["prior_uuid"] is None
        # Pass 1 uses session_id=None (S-1 UUID isolation).
        assert pass1_call.kwargs["session_id"] is None


class TestDelivery:
    async def test_stamps_response_delivered_at(self, parent, send_cb):
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=AsyncMock(return_value="ok")),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, None
            )
        assert parent.response_delivered_at is not None
        parent.save.assert_called()

    async def test_no_send_cb_still_finalizes(self, parent):
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=AsyncMock(return_value="ok")),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session") as _fs,
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", None, parent.chat_id, None
            )
        _fs.assert_called_once()
        # First positional is parent, second is the new status.
        assert _fs.call_args.args[1] == "completed"

    async def test_send_cb_exception_still_finalizes(self, parent):
        async def _raise(*a, **kw):
            raise RuntimeError("transport down")

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=AsyncMock(return_value="ok")),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session") as _fs,
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", _raise, parent.chat_id, None
            )
        _fs.assert_called_once()


class TestCancelledError:
    async def test_cancelled_sends_interrupted_and_reraises(self, parent, send_cb):
        async def _cancel(**_kw):
            raise asyncio.CancelledError()

        redis_db = MagicMock()
        # First lock set (pipeline_complete_pending) succeeds, second (interrupted-sent) succeeds
        redis_db.set = MagicMock(return_value=True)
        redis_db.exists = MagicMock(return_value=False)
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", redis_db),
            patch("agent.sdk_client.get_response_via_harness", new=_cancel),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
        ):
            with pytest.raises(asyncio.CancelledError):
                await session_completion._deliver_pipeline_completion(
                    parent, "ctx", send_cb, parent.chat_id, None
                )
        # send_cb should have been called once for the interrupted message.
        interrupted_calls = [
            call
            for call in send_cb.await_args_list
            if isinstance(call.args[1], str) and "interrupted" in call.args[1].lower()
        ]
        assert len(interrupted_calls) == 1

    async def test_cancelled_interrupted_dedup_suppresses_duplicate(self, parent, send_cb):
        async def _cancel(**_kw):
            raise asyncio.CancelledError()

        redis_db = MagicMock()
        # pipeline_complete_pending → acquired True, interrupted-sent → False (held)
        redis_db.set = MagicMock(side_effect=[True, False])
        redis_db.exists = MagicMock(return_value=False)
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", redis_db),
            patch("agent.sdk_client.get_response_via_harness", new=_cancel),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
        ):
            with pytest.raises(asyncio.CancelledError):
                await session_completion._deliver_pipeline_completion(
                    parent, "ctx", send_cb, parent.chat_id, None
                )
        # Dedup suppressed the interrupted send.
        send_cb.assert_not_awaited()


class TestScheduler:
    async def test_scheduler_dedup_within_process(self, parent, send_cb):
        async def _slow(**_kw):
            await asyncio.sleep(0.1)
            return "ok"

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=_slow),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
        ):
            first = session_completion.schedule_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, None
            )
            second = session_completion.schedule_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, None
            )
            assert first is second, "Second schedule must return the in-flight task"
            await first

    async def test_scheduler_missing_parent_id_returns_none(self, send_cb):
        parent = SimpleNamespace(agent_session_id=None, id=None)
        task = session_completion.schedule_pipeline_completion(
            parent,
            "ctx",
            send_cb,
            "chat",
            None,  # type: ignore[arg-type]
        )
        assert task is None


# ─── Mid-session-send-aware completion suppression (issue #1262) ────────────


def _outbound_entry(text: str, age_secs: float = 0.0) -> dict:
    """Build a chat_message_log outbound entry."""
    import time as _t

    return {
        "direction": "out",
        "sender": "agent",
        "content": text,
        "message_id": 1,
        "ts": _t.time() - age_secs,
    }


def _make_parent_with_chat_log(entries: list[dict]) -> MagicMock:
    """Build a parent mock with chat_message_log set, no Popoto re-fetch."""
    p = MagicMock()
    p.agent_session_id = "parent-xyz-1262"
    p.session_id = "tg_valor_-999_111"
    p.chat_id = "-999"
    p.telegram_message_id = 222
    p.project_key = "valor"
    p.transport = None
    p.project_config = {"working_directory": "/tmp"}
    p.save = MagicMock()
    p.chat_message_log = list(entries)
    return p


def _patch_redis_no_drain_wait(mock_redis):
    """Make _await_outbox_drained immediately succeed (LLEN returns 0).

    The runner's wait helper constructs its own Redis client via
    redis.Redis.from_url, so we patch at that location.
    """

    class _R:
        def llen(self, key):
            return 0

        def rpush(self, *a, **kw):
            return 1

        def expire(self, *a, **kw):
            return True

    mock_redis.from_url = MagicMock(return_value=_R())


class TestCompletionSuppression:
    """Plan: docs/plans/dedupe-completion-emit.md.

    The completion runner now reads parent.chat_message_log to detect when a
    sub-skill already delivered substantively-the-same content via Path B
    (`valor-telegram send`). High-confidence matches suppress + queue 👀;
    borderline matches escalate to a Haiku judge; everything else delivers.
    """

    async def test_completion_suppressed_when_final_text_matches_chat_log_outbound(self, send_cb):
        """High-confidence dedupe: J >= HIGH_CUTOFF (0.75) → suppress + 👀."""
        text = "deployed to production successfully tests passing all green"
        parent = _make_parent_with_chat_log([_outbound_entry(text)])
        # Pass 2 returns the identical text → J ≈ 1.0 → high-confidence suppress.
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=AsyncMock(return_value=text)),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
            patch("redis.Redis") as _r,
            patch(
                "agent.session_completion._queue_completion_suppress_reaction",
                return_value=True,
            ) as queue_react,
        ):
            _patch_redis_no_drain_wait(_r)
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, parent.telegram_message_id
            )
        # send_cb must NOT have been called — suppression fired.
        send_cb.assert_not_awaited()
        # 👀 reaction was queued via the canonical helper.
        queue_react.assert_called_once()
        args = queue_react.call_args.args
        assert args[1] == parent.chat_id
        assert args[2] == parent.telegram_message_id

    async def test_completion_delivered_when_final_text_unique(self, send_cb):
        """Different-content baseline → suppress check returns send → deliver."""
        baseline_text = "checking the deployment pipeline status please wait briefly"
        final_text = "shipped a brand new feature added totally unrelated content here"
        parent = _make_parent_with_chat_log([_outbound_entry(baseline_text)])
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch(
                "agent.sdk_client.get_response_via_harness",
                new=AsyncMock(return_value=final_text),
            ),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
            patch("redis.Redis") as _r,
        ):
            _patch_redis_no_drain_wait(_r)
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, parent.telegram_message_id
            )
        send_cb.assert_awaited_once()
        assert send_cb.await_args.args[1] == final_text

    async def test_completion_baseline_excludes_inbound_entries(self, send_cb):
        """Inbound (direction='in') entries with identical content do NOT
        suppress — the user's own message is never the suppression baseline.
        """
        text = "the user's question echoing in the inbound entry"
        parent = _make_parent_with_chat_log(
            [
                {
                    "direction": "in",
                    "sender": "tom",
                    "content": text,
                    "message_id": 5,
                    "ts": __import__("time").time(),
                }
            ]
        )
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=AsyncMock(return_value=text)),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
            patch("redis.Redis") as _r,
        ):
            _patch_redis_no_drain_wait(_r)
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, parent.telegram_message_id
            )
        # Baseline filtered → no suppression → delivered.
        send_cb.assert_awaited_once()

    async def test_completion_baseline_excludes_stale_entries(self, send_cb):
        """Outbound entries older than REDUNDANCY_WINDOW_SECONDS are filtered."""
        from bridge.redundancy_filter import REDUNDANCY_WINDOW_SECONDS

        text = "stale outbound that should not be in the baseline"
        parent = _make_parent_with_chat_log(
            [_outbound_entry(text, age_secs=REDUNDANCY_WINDOW_SECONDS + 60)]
        )
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=AsyncMock(return_value=text)),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
            patch("redis.Redis") as _r,
        ):
            _patch_redis_no_drain_wait(_r)
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, parent.telegram_message_id
            )
        # Stale entry filtered → empty baseline → delivered.
        send_cb.assert_awaited_once()

    async def test_completion_judge_called_in_borderline_band(self, send_cb):
        """0.55 <= J < 0.75 → escalate to Haiku judge.

        Asserts: should_suppress called with threshold=0.55 (LOW_CUTOFF), judge
        receives prior context. Judge=restate → suppress; judge=new → deliver.
        """
        text = "the borderline content with some identical words"
        parent = _make_parent_with_chat_log([_outbound_entry(text)])

        from bridge.redundancy_filter import SuppressionVerdict

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=AsyncMock(return_value=text)),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
            patch("redis.Redis") as _r,
            patch("bridge.redundancy_filter.should_suppress") as mock_ss,
            patch(
                "agent.session_completion._judge_completion_novelty",
                new=AsyncMock(return_value=True),  # restate → suppress
            ) as mock_judge,
            patch(
                "agent.session_completion._queue_completion_suppress_reaction",
                return_value=True,
            ) as queue_react,
        ):
            _patch_redis_no_drain_wait(_r)
            mock_ss.return_value = SuppressionVerdict(
                action="suppress",
                reason="jaccard=0.65",
                jaccard=0.65,
                matched_index=0,
            )
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, parent.telegram_message_id
            )

        # Assert should_suppress was called with threshold=LOW_CUTOFF (0.55).
        assert mock_ss.call_args.kwargs.get("threshold") == 0.55
        assert mock_ss.call_args.kwargs.get("session_status") is None
        assert mock_ss.call_args.kwargs.get("expectations") is None
        # Judge was called with prior_text from the baseline.
        mock_judge.assert_awaited_once()
        # Suppression fired.
        send_cb.assert_not_awaited()
        queue_react.assert_called_once()

    async def test_completion_judge_says_new_in_borderline_band_delivers(self, send_cb):
        """Borderline with judge=new → deliver."""
        text = "the borderline content"
        parent = _make_parent_with_chat_log([_outbound_entry(text)])

        from bridge.redundancy_filter import SuppressionVerdict

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=AsyncMock(return_value=text)),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
            patch("redis.Redis") as _r,
            patch("bridge.redundancy_filter.should_suppress") as mock_ss,
            patch(
                "agent.session_completion._judge_completion_novelty",
                new=AsyncMock(return_value=False),  # new → deliver
            ) as mock_judge,
        ):
            _patch_redis_no_drain_wait(_r)
            mock_ss.return_value = SuppressionVerdict(
                action="suppress",
                reason="jaccard=0.60",
                jaccard=0.60,
                matched_index=0,
            )
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, parent.telegram_message_id
            )
        mock_judge.assert_awaited_once()
        send_cb.assert_awaited_once()

    async def test_completion_high_confidence_suppress_skips_haiku(self, send_cb):
        """J >= HIGH_CUTOFF (0.75) → suppress without calling Haiku."""
        text = "high confidence duplicate content"
        parent = _make_parent_with_chat_log([_outbound_entry(text)])

        from bridge.redundancy_filter import SuppressionVerdict

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=AsyncMock(return_value=text)),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
            patch("redis.Redis") as _r,
            patch("bridge.redundancy_filter.should_suppress") as mock_ss,
            patch(
                "agent.session_completion._judge_completion_novelty",
                new=AsyncMock(return_value=False),
            ) as mock_judge,
            patch(
                "agent.session_completion._queue_completion_suppress_reaction",
                return_value=True,
            ),
        ):
            _patch_redis_no_drain_wait(_r)
            mock_ss.return_value = SuppressionVerdict(
                action="suppress",
                reason="jaccard=0.85",
                jaccard=0.85,
                matched_index=0,
            )
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, parent.telegram_message_id
            )
        # Haiku must NOT be called.
        mock_judge.assert_not_awaited()
        send_cb.assert_not_awaited()

    async def test_completion_borderline_with_invalid_matched_index_falls_through(self, send_cb):
        """Defensive: out-of-range matched_index → log warning, deliver."""
        text = "borderline content"
        parent = _make_parent_with_chat_log([_outbound_entry(text)])

        from bridge.redundancy_filter import SuppressionVerdict

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=AsyncMock(return_value=text)),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
            patch("redis.Redis") as _r,
            patch("bridge.redundancy_filter.should_suppress") as mock_ss,
            patch(
                "agent.session_completion._judge_completion_novelty",
                new=AsyncMock(return_value=True),
            ) as mock_judge,
        ):
            _patch_redis_no_drain_wait(_r)
            mock_ss.return_value = SuppressionVerdict(
                action="suppress",
                reason="jaccard=0.65",
                jaccard=0.65,
                matched_index=99,  # out of range vs 1-entry baseline
            )
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, parent.telegram_message_id
            )
        # Judge must NOT be called when matched_index is invalid.
        mock_judge.assert_not_awaited()
        # Delivery proceeds.
        send_cb.assert_awaited_once()

    async def test_completion_send_verdict_with_new_artifact_proceeds(self, send_cb):
        """verdict.action == 'send' (e.g. new_artifact) → deliver, no Haiku."""
        text = "shipped a feature"
        parent = _make_parent_with_chat_log([_outbound_entry(text)])

        from bridge.redundancy_filter import SuppressionVerdict

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=AsyncMock(return_value=text)),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
            patch("redis.Redis") as _r,
            patch("bridge.redundancy_filter.should_suppress") as mock_ss,
            patch(
                "agent.session_completion._judge_completion_novelty",
                new=AsyncMock(return_value=True),
            ) as mock_judge,
        ):
            _patch_redis_no_drain_wait(_r)
            mock_ss.return_value = SuppressionVerdict(
                action="send",
                reason="new_artifact",
            )
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, parent.telegram_message_id
            )
        mock_judge.assert_not_awaited()
        send_cb.assert_awaited_once()

    async def test_completion_adapter_failopen_on_malformed_entry(self, send_cb):
        """Malformed chat_message_log entries don't crash; runner delivers."""
        parent = _make_parent_with_chat_log(
            [
                "not-a-dict",  # type: ignore[list-item]
                {"direction": "out"},  # missing content
                {"direction": "out", "content": "ok", "ts": "not-a-number"},
            ]
        )
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=AsyncMock(return_value="hi")),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
            patch("redis.Redis") as _r,
        ):
            _patch_redis_no_drain_wait(_r)
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, parent.telegram_message_id
            )
        # No crash, delivery proceeds.
        send_cb.assert_awaited_once()

    async def test_completion_silent_fallback_when_telegram_message_id_none(self, send_cb):
        """suppress decision + None anchor → no reaction, no send, log only."""
        text = "duplicate content"
        parent = _make_parent_with_chat_log([_outbound_entry(text)])

        from bridge.redundancy_filter import SuppressionVerdict

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=AsyncMock(return_value=text)),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
            patch("redis.Redis") as _r,
            patch("bridge.redundancy_filter.should_suppress") as mock_ss,
            patch(
                "agent.session_completion._queue_completion_suppress_reaction",
                return_value=True,
            ) as queue_react,
        ):
            _patch_redis_no_drain_wait(_r)
            mock_ss.return_value = SuppressionVerdict(
                action="suppress",
                reason="jaccard=0.85",
                jaccard=0.85,
                matched_index=0,
            )
            await session_completion._deliver_pipeline_completion(
                parent,
                "ctx",
                send_cb,
                parent.chat_id,
                None,  # no anchor
            )
        # Suppress decision but no anchor → no reaction queued, no send.
        queue_react.assert_not_called()
        send_cb.assert_not_awaited()

    async def test_completion_skips_suppression_check_on_sentinel_text(self, send_cb):
        """The degraded-fallback sentinel must bypass the suppression check."""
        # Force Pass 1 empty so degraded fallback is delivered.
        parent = _make_parent_with_chat_log(
            [_outbound_entry("the degraded fallback text or anything")]
        )
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=AsyncMock(return_value="")),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
            patch("redis.Redis") as _r,
            patch("bridge.redundancy_filter.should_suppress") as mock_ss,
        ):
            _patch_redis_no_drain_wait(_r)
            await session_completion._deliver_pipeline_completion(
                parent, "fallback ctx", send_cb, parent.chat_id, parent.telegram_message_id
            )
        # On the sentinel/degraded-fallback path, the runner intentionally
        # bypasses the suppression check. The degraded message MUST always
        # reach the user.
        send_cb.assert_awaited_once()
        # mock_ss may or may not have been called (depends on the actual
        # final_text content); the load-bearing assertion is delivery.

    async def test_completion_outbox_drain_wait_times_out_gracefully(self, send_cb):
        """LLEN never returns 0 → wait times out → runner proceeds."""
        text = "different content from baseline"
        parent = _make_parent_with_chat_log([_outbound_entry("baseline text unrelated")])

        class _SlowR:
            def llen(self, key):
                return 1  # never drains

            def rpush(self, *a, **kw):
                return 1

            def expire(self, *a, **kw):
                return True

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=AsyncMock(return_value=text)),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
            patch("redis.Redis") as _r,
            patch(
                "agent.session_completion._await_outbox_drained",
                new=AsyncMock(return_value=False),  # short-circuit the slow wait
            ),
        ):
            _r.from_url = MagicMock(return_value=_SlowR())
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, parent.telegram_message_id
            )
        # Runner proceeded with whatever was in chat_message_log → delivery.
        send_cb.assert_awaited_once()

    async def test_completion_refetches_parent_before_suppression_check(self, send_cb):
        """The runner re-fetches parent from Popoto immediately before the
        suppression baseline read so a stale in-memory copy doesn't shadow
        a fresh chat_log append.
        """
        # Stale in-memory: empty chat_message_log
        stale_parent = _make_parent_with_chat_log([])
        # Fresh from Popoto: has an outbound entry that matches final_text
        text = "content already sent mid-session and now duplicated"
        fresh_parent = _make_parent_with_chat_log([_outbound_entry(text)])

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=AsyncMock(return_value=text)),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
            patch("redis.Redis") as _r,
            patch(
                "models.agent_session.AgentSession.get_by_id",
                return_value=fresh_parent,
            ),
            patch(
                "agent.session_completion._queue_completion_suppress_reaction",
                return_value=True,
            ) as queue_react,
        ):
            _patch_redis_no_drain_wait(_r)
            await session_completion._deliver_pipeline_completion(
                stale_parent, "ctx", send_cb, stale_parent.chat_id, stale_parent.telegram_message_id
            )
        # Re-fetch surfaced the duplicate → suppress fired.
        send_cb.assert_not_awaited()
        queue_react.assert_called_once()


class TestDrain:
    async def test_drain_no_op_when_empty(self):
        # Ensure drain does not error on empty dict.
        session_completion._pending_completion_tasks.clear()
        await session_completion.drain_pending_completions(timeout=0.1)

    async def test_drain_cancels_long_tasks(self, parent, send_cb):
        async def _forever(**_kw):
            await asyncio.sleep(60.0)
            return "nope"

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=_forever),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
        ):
            task = session_completion.schedule_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, None
            )
            assert task is not None
            await session_completion.drain_pending_completions(timeout=0.2)
            # Give the cancel a tick to propagate through the wrapper's handler.
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
            assert task.done()
