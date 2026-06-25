"""Unit tests for BackgroundTask cwd-vanished watchdog (issue #1357)."""

import asyncio
import logging
from unittest.mock import patch

import pytest

from agent.messenger import BackgroundTask, BossMessenger


def _make_messenger() -> BossMessenger:
    async def _noop_send(_text: str) -> None:
        return None

    return BossMessenger(
        _send_callback=_noop_send,
        chat_id="test-chat",
        session_id="test-session",
    )


@pytest.mark.asyncio
async def test_working_dir_none_does_not_cancel(tmp_path):
    """working_dir=None: watchdog never trips cwd-vanished even when dirs are deleted."""
    messenger = _make_messenger()
    task = BackgroundTask(messenger=messenger, working_dir=None)
    # HEARTBEAT short for the test
    with patch.object(BackgroundTask, "HEARTBEAT_INTERVAL", 0.05):

        async def long_work() -> str:
            await asyncio.sleep(0.4)
            return "done"

        await task.run(long_work(), send_result=False)
        # Wait for the work task to finish naturally
        if task._task is not None:
            try:
                await task._task
            except asyncio.CancelledError:
                pytest.fail("work task was cancelled despite working_dir=None")
        # Watchdog cancels itself when work completes
        if task._watchdog_task is not None:
            try:
                await task._watchdog_task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_working_dir_present_no_false_positive(tmp_path):
    """working_dir exists throughout the run: no cwd-vanished cancel."""
    messenger = _make_messenger()
    task = BackgroundTask(messenger=messenger, working_dir=str(tmp_path))
    with patch.object(BackgroundTask, "HEARTBEAT_INTERVAL", 0.05):

        async def short_work() -> str:
            await asyncio.sleep(0.3)
            return "done"

        await task.run(short_work(), send_result=False)
        if task._task is not None:
            try:
                await task._task
            except asyncio.CancelledError:
                pytest.fail("work task cancelled despite working_dir existing")
        # Watchdog cancels itself naturally when work completes
        if task._watchdog_task is not None:
            try:
                await task._watchdog_task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_cwd_vanished_cancels_work(tmp_path, caplog):
    """When working_dir disappears mid-run, watchdog cancels the work task."""
    workdir = tmp_path / "wt"
    workdir.mkdir()

    messenger = _make_messenger()
    task = BackgroundTask(messenger=messenger, working_dir=str(workdir))

    cancelled_event = asyncio.Event()

    async def long_work() -> str:
        try:
            await asyncio.sleep(60)  # would never finish on its own
            return "should not reach"
        except asyncio.CancelledError:
            cancelled_event.set()
            raise

    with (
        patch.object(BackgroundTask, "HEARTBEAT_INTERVAL", 0.05),
        caplog.at_level(logging.WARNING, logger="agent.messenger"),
    ):
        await task.run(long_work(), send_result=False)
        # Let one watchdog tick happen with the dir intact
        await asyncio.sleep(0.1)
        # Pull the rug
        import shutil

        shutil.rmtree(workdir)
        # Wait for cancellation to propagate (watchdog tick + propagation)
        try:
            await asyncio.wait_for(cancelled_event.wait(), timeout=5.0)
        except TimeoutError:
            pytest.fail("watchdog did not cancel work task within 5s of cwd vanish")

        # Drain the cancelled task so pytest doesn't warn about pending tasks
        if task._task is not None:
            try:
                await task._task
            except asyncio.CancelledError:
                pass
        if task._watchdog_task is not None and not task._watchdog_task.done():
            task._watchdog_task.cancel()
            try:
                await task._watchdog_task
            except asyncio.CancelledError:
                pass

    # Confirm the cwd_vanished warning was logged
    assert any(
        "cwd_vanished" in rec.message and str(workdir) in rec.message for rec in caplog.records
    ), f"cwd_vanished WARNING not logged. Records: {[r.message for r in caplog.records]}"


@pytest.mark.asyncio
async def test_empty_string_working_dir_treated_as_none(tmp_path):
    """working_dir='' must NOT activate the cwd-vanished branch."""
    messenger = _make_messenger()
    task = BackgroundTask(messenger=messenger, working_dir="")

    # Internal check: empty string was normalized to None
    assert task._working_dir is None

    with patch.object(BackgroundTask, "HEARTBEAT_INTERVAL", 0.05):

        async def short_work() -> str:
            await asyncio.sleep(0.2)
            return "done"

        await task.run(short_work(), send_result=False)
        if task._task is not None:
            try:
                await task._task
            except asyncio.CancelledError:
                pytest.fail("empty-string working_dir should not cancel")
        if task._watchdog_task is not None:
            try:
                await task._watchdog_task
            except asyncio.CancelledError:
                pass


def test_increment_cwd_vanished_counter_swallows_redis_errors():
    """Counter increment must never raise — it's observability, not correctness."""
    messenger = _make_messenger()
    task = BackgroundTask(messenger=messenger, working_dir="/tmp")

    # Patch the import inside _increment_cwd_vanished_counter to raise.
    with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
        mock_redis.incr.side_effect = RuntimeError("redis down")
        # Should NOT raise
        task._increment_cwd_vanished_counter()
