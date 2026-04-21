"""Unit tests for the CancelledError handler in `agent/messenger.py::_run_work`.

Covers the plan's failure mode #3 (#1058):
- Worker shutdown raises CancelledError inside `_run_work`.
- Handler must best-effort deliver an "I was interrupted" message via
  `_send_callback` and then re-raise to preserve asyncio shutdown semantics.
- Flap protection (Risk 6): within 120s, duplicate CancelledErrors do NOT
  produce duplicate user-visible messages (Redis SETNX dedup).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.messenger import BackgroundTask, BossMessenger


@pytest.fixture
def send_callback():
    """AsyncMock send callback; defaults to returning None (success)."""
    return AsyncMock(return_value=None)


@pytest.fixture
def messenger(send_callback):
    return BossMessenger(
        _send_callback=send_callback,
        chat_id="test_chat",
        session_id="test_session_1058",
    )


@pytest.fixture
def task(messenger):
    return BackgroundTask(messenger=messenger, acknowledgment_timeout=5.0)


async def _cancelling_coro():
    """Coroutine that awaits forever until cancelled."""
    await asyncio.sleep(60.0)
    return "unreachable"


def _redis_mock(acquired: bool = True):
    """Patch target for POPOTO_REDIS_DB.set returning a chosen NX result."""
    db = MagicMock()
    db.set = MagicMock(return_value=acquired)
    return db


class TestCancelledErrorDelivery:
    async def test_cancelled_error_delivers_interrupted_message(self, task, send_callback):
        with patch(
            "popoto.redis_db.POPOTO_REDIS_DB",
            _redis_mock(acquired=True),
        ):
            await task.run(_cancelling_coro(), send_result=True)
            await asyncio.sleep(0.05)  # let _run_work start
            task._task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task._task

        send_callback.assert_awaited()
        [args] = [call.args for call in send_callback.await_args_list]
        assert "interrupted" in args[0].lower()
        assert "resume automatically" in args[0]

    async def test_cancelled_error_reraises_after_send(self, task):
        with patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_mock(acquired=True)):
            await task.run(_cancelling_coro(), send_result=True)
            await asyncio.sleep(0.05)
            task._task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task._task

    async def test_send_callback_timeout_swallowed(self, messenger, task):
        """If send_callback hangs past 2s we must not block shutdown."""

        async def _hang(_msg):
            await asyncio.sleep(10.0)

        messenger._send_callback = _hang
        with patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_mock(acquired=True)):
            await task.run(_cancelling_coro(), send_result=True)
            await asyncio.sleep(0.05)
            task._task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task._task, timeout=4.0)

    async def test_send_callback_exception_swallowed(self, messenger, task):
        """If send_callback raises, handler still re-raises CancelledError."""

        async def _boom(_msg):
            raise RuntimeError("send failed")

        messenger._send_callback = _boom
        with patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_mock(acquired=True)):
            await task.run(_cancelling_coro(), send_result=True)
            await asyncio.sleep(0.05)
            task._task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task._task


class TestFlapProtection:
    async def test_duplicate_cancel_within_ttl_does_not_resend(self, messenger):
        """Second CancelledError within 120s must NOT produce another send."""
        # First cancel: SET NX returns True (acquired).
        # Second cancel: SET NX returns False (already held).
        first_task = BackgroundTask(messenger=messenger, acknowledgment_timeout=5.0)
        second_task = BackgroundTask(messenger=messenger, acknowledgment_timeout=5.0)

        redis_db = MagicMock()
        # First call acquires, second call fails to acquire.
        redis_db.set = MagicMock(side_effect=[True, False])

        with patch("popoto.redis_db.POPOTO_REDIS_DB", redis_db):
            await first_task.run(_cancelling_coro(), send_result=True)
            await asyncio.sleep(0.05)
            first_task._task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first_task._task

            await second_task.run(_cancelling_coro(), send_result=True)
            await asyncio.sleep(0.05)
            second_task._task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second_task._task

        # Exactly one "interrupted" send across both cancellations.
        interrupted_sends = [
            call.args
            for call in messenger._send_callback.await_args_list
            if isinstance(call.args[0], str) and "interrupted" in call.args[0].lower()
        ]
        assert len(interrupted_sends) == 1

    async def test_redis_unavailable_still_sends(self, messenger, task):
        """If Redis raises on SETNX, handler falls through and sends anyway."""
        redis_db = MagicMock()
        redis_db.set = MagicMock(side_effect=RuntimeError("redis down"))
        with patch("popoto.redis_db.POPOTO_REDIS_DB", redis_db):
            await task.run(_cancelling_coro(), send_result=True)
            await asyncio.sleep(0.05)
            task._task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task._task

        # Without a working dedup lock, we prefer duplicate over silence.
        assert any(
            "interrupted" in call.args[0].lower()
            for call in messenger._send_callback.await_args_list
        )
