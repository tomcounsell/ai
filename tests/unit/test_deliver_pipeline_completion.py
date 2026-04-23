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
