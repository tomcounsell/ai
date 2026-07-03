"""Unit tests for the running->failed user notification (#1877 defect #2).

Covers `_maybe_send_failure_notice`, the best-effort helper the executor's
failure-finalize block calls when `task.error` is set. Requirements:
  * a failure notice is sent with the shared `FAILURE_NOTICE` copy;
  * a raising send-callback does not propagate (finalization is never blocked);
  * the `failed-sent` dedup suppresses a second send for the same session;
  * a present `cancel-reason` (a killer already owns the exit narrative)
    suppresses the failure send entirely (cross-class dedup collision guard).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.notification_copy import FAILURE_NOTICE
from agent.session_executor import _maybe_send_failure_notice


def _messenger():
    m = MagicMock()
    m._send_callback = AsyncMock(return_value=None)
    return m


def _redis_setnx(acquired: bool):
    db = MagicMock()
    db.set = MagicMock(return_value=acquired)
    return db


@pytest.mark.asyncio
async def test_sends_failure_notice_with_shared_copy():
    """No cancel-reason, dedup free → the FAILURE_NOTICE copy is sent once."""
    messenger = _messenger()
    with (
        patch("agent.cancel_reason.get_cancel_reason", return_value=None),
        patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_setnx(True)),
    ):
        await _maybe_send_failure_notice(messenger, "sess-fail-1")

    messenger._send_callback.assert_awaited_once_with(FAILURE_NOTICE)


@pytest.mark.asyncio
async def test_raising_send_callback_does_not_propagate():
    """A send-callback that raises must be swallowed (finalization not blocked)."""
    messenger = _messenger()
    messenger._send_callback = AsyncMock(side_effect=RuntimeError("send boom"))
    with (
        patch("agent.cancel_reason.get_cancel_reason", return_value=None),
        patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_setnx(True)),
    ):
        # Must NOT raise.
        await _maybe_send_failure_notice(messenger, "sess-fail-2")

    messenger._send_callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_dedup_suppresses_second_send():
    """failed-sent SET NX already held → no send (single-notice guarantee)."""
    messenger = _messenger()
    with (
        patch("agent.cancel_reason.get_cancel_reason", return_value=None),
        patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_setnx(False)),
    ):
        await _maybe_send_failure_notice(messenger, "sess-fail-3")

    messenger._send_callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_reason_present_suppresses_failure_send():
    """A killer already owns the exit narrative → failure send is skipped."""
    messenger = _messenger()
    redis = _redis_setnx(True)
    with (
        patch("agent.cancel_reason.get_cancel_reason", return_value="no_resume"),
        patch("popoto.redis_db.POPOTO_REDIS_DB", redis),
    ):
        await _maybe_send_failure_notice(messenger, "sess-fail-4")

    messenger._send_callback.assert_not_awaited()
    # The dedup key must not even be acquired when we defer to the killer.
    redis.set.assert_not_called()


@pytest.mark.asyncio
async def test_redis_unavailable_still_sends():
    """If the dedup lock is unavailable, prefer a possible duplicate over silence."""
    messenger = _messenger()
    redis = MagicMock()
    redis.set = MagicMock(side_effect=RuntimeError("redis down"))
    with (
        patch("agent.cancel_reason.get_cancel_reason", return_value=None),
        patch("popoto.redis_db.POPOTO_REDIS_DB", redis),
    ):
        await _maybe_send_failure_notice(messenger, "sess-fail-5")

    messenger._send_callback.assert_awaited_once_with(FAILURE_NOTICE)
