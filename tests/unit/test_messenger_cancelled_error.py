"""Unit tests for the CancelledError handler in `agent/messenger.py::_run_work`.

Covers the plan's failure mode #3 (#1058), inverted by the silent-resume
removal:
- Worker shutdown raises CancelledError inside `_run_work`.
- An interruption the machinery will recover from (absent/non-`no_resume`
  cancel-reason) must be SILENT — no user-visible message, just a re-raise to
  preserve asyncio shutdown semantics.
- Only a terminal `no_resume` cancel-reason earns a best-effort
  `INTERRUPT_NO_RESUME` send via `_send_callback`.
- Flap protection (Risk 6): within 120s, duplicate `no_resume` CancelledErrors
  do NOT produce duplicate user-visible messages (Redis SETNX dedup).
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


def _cancel_reason_patch(reason: str | None):
    """Patch target for agent.cancel_reason.get_cancel_reason's return value."""
    return patch("agent.cancel_reason.get_cancel_reason", return_value=reason)


class TestCancelledErrorSilence:
    """Absent/non-`no_resume` cancel-reason: the handler must send nothing."""

    async def test_absent_reason_sends_nothing(self, task, send_callback):
        with (
            _cancel_reason_patch(None),
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_mock(acquired=True)),
        ):
            await task.run(_cancelling_coro(), send_result=True)
            await asyncio.sleep(0.05)  # let _run_work start
            task._task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task._task

        send_callback.assert_not_awaited()

    async def test_resume_reason_sends_nothing(self, task, send_callback):
        """A stale/legacy `"resume"` reason (no longer written) also stays silent."""
        with (
            _cancel_reason_patch("resume"),
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_mock(acquired=True)),
        ):
            await task.run(_cancelling_coro(), send_result=True)
            await asyncio.sleep(0.05)
            task._task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task._task

        send_callback.assert_not_awaited()

    async def test_cancelled_error_reraises_after_handler(self, task):
        """Re-raise must occur on the silent (no-send) path too."""
        with (
            _cancel_reason_patch(None),
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_mock(acquired=True)),
        ):
            await task.run(_cancelling_coro(), send_result=True)
            await asyncio.sleep(0.05)
            task._task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task._task


class TestCancelledErrorDelivery:
    """`no_resume` cancel-reason: the terminal notice is sent."""

    async def test_no_resume_reason_delivers_interrupt_no_resume(self, task, send_callback):
        with (
            _cancel_reason_patch("no_resume"),
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_mock(acquired=True)),
        ):
            await task.run(_cancelling_coro(), send_result=True)
            await asyncio.sleep(0.05)
            task._task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task._task

        send_callback.assert_awaited()
        [args] = [call.args for call in send_callback.await_args_list]
        assert "won't resume automatically" in args[0]

    async def test_send_callback_timeout_swallowed(self, messenger, task):
        """If send_callback hangs past 2s we must not block shutdown."""

        async def _hang(_msg):
            await asyncio.sleep(10.0)

        messenger._send_callback = _hang
        with (
            _cancel_reason_patch("no_resume"),
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_mock(acquired=True)),
        ):
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
        with (
            _cancel_reason_patch("no_resume"),
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_mock(acquired=True)),
        ):
            await task.run(_cancelling_coro(), send_result=True)
            await asyncio.sleep(0.05)
            task._task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task._task


class TestFlapProtection:
    async def test_duplicate_cancel_within_ttl_does_not_resend(self, messenger):
        """Second `no_resume` CancelledError within 120s must NOT produce another send."""
        # First cancel: SET NX returns True (acquired).
        # Second cancel: SET NX returns False (already held).
        first_task = BackgroundTask(messenger=messenger, acknowledgment_timeout=5.0)
        second_task = BackgroundTask(messenger=messenger, acknowledgment_timeout=5.0)

        redis_db = MagicMock()
        # First call acquires, second call fails to acquire.
        redis_db.set = MagicMock(side_effect=[True, False])

        with (
            _cancel_reason_patch("no_resume"),
            patch("popoto.redis_db.POPOTO_REDIS_DB", redis_db),
        ):
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

        # Exactly one send across both cancellations.
        assert len(messenger._send_callback.await_args_list) == 1

    async def test_redis_unavailable_still_sends(self, messenger, task):
        """If Redis raises on SETNX, handler falls through and sends anyway."""
        redis_db = MagicMock()
        redis_db.set = MagicMock(side_effect=RuntimeError("redis down"))
        with (
            _cancel_reason_patch("no_resume"),
            patch("popoto.redis_db.POPOTO_REDIS_DB", redis_db),
        ):
            await task.run(_cancelling_coro(), send_result=True)
            await asyncio.sleep(0.05)
            task._task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task._task

        # Without a working dedup lock, we prefer duplicate over silence.
        assert any(
            "won't resume automatically" in call.args[0]
            for call in messenger._send_callback.await_args_list
        )
