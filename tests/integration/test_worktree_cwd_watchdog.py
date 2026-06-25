"""Integration test for cwd-vanished watchdog (issue #1357).

Spawns a long-running async task with `working_dir=tempdir`, removes the
directory mid-run, and asserts the BackgroundTask watchdog cancels the
work task. The heartbeat interval is monkeypatched to 0.1s for the test
so we don't wait two minutes in CI.

Note: this is an integration test (not pure unit) because it exercises
the full BackgroundTask.run / _watchdog / _run_work CancelledError path
together.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.messenger import BackgroundTask, BossMessenger


@pytest.mark.asyncio
async def test_cwd_vanished_cancels_within_seconds(caplog):
    """End-to-end: delete the worktree dir, watchdog cancels work in <10s."""
    sent_messages: list[str] = []

    async def _capture_send(text: str) -> None:
        sent_messages.append(text)

    messenger = BossMessenger(
        _send_callback=_capture_send,
        chat_id="cwd-vanished-test",
        session_id="cwd-vanished-test-session",
    )

    tmp_root = Path(tempfile.mkdtemp(prefix="cwd-vanished-test-"))
    workdir = tmp_root / "wt"
    workdir.mkdir()

    task = BackgroundTask(
        messenger=messenger,
        working_dir=str(workdir),
    )

    cancelled_event = asyncio.Event()
    completed_event = asyncio.Event()

    async def long_work() -> str:
        try:
            await asyncio.sleep(60)
            return "should not reach"
        except asyncio.CancelledError:
            cancelled_event.set()
            raise
        finally:
            completed_event.set()

    try:
        with (
            patch.object(BackgroundTask, "HEARTBEAT_INTERVAL", 0.1),
            caplog.at_level(logging.WARNING, logger="agent.messenger"),
        ):
            await task.run(long_work(), send_result=False)
            # Let watchdog tick once with the dir intact (sanity)
            await asyncio.sleep(0.2)
            assert not cancelled_event.is_set(), "premature cancel before delete"

            # Pull the rug out from under the SDK
            shutil.rmtree(workdir)

            # Watchdog should detect the missing dir within ~1 tick (0.1s)
            # and cancel the work task. Allow up to 10s for slow CI.
            try:
                await asyncio.wait_for(cancelled_event.wait(), timeout=10.0)
            except TimeoutError:
                pytest.fail(
                    "watchdog failed to cancel work task within 10s of "
                    f"deletion (records: {[r.message for r in caplog.records]})"
                )

            # Wait for the work task's finally block to run
            try:
                await asyncio.wait_for(completed_event.wait(), timeout=5.0)
            except TimeoutError:
                pytest.fail("work task did not propagate cancellation in time")

            # Drain the cancelled task
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

        # Verify the cwd_vanished WARNING was logged with session_id and path
        cwd_records = [r for r in caplog.records if "cwd_vanished" in r.message]
        assert cwd_records, (
            f"cwd_vanished WARNING was not logged. Records: {[r.message for r in caplog.records]}"
        )
        assert any("cwd-vanished-test-session" in r.message for r in cwd_records)
    finally:
        # Best-effort cleanup
        if tmp_root.exists():
            shutil.rmtree(tmp_root, ignore_errors=True)


@pytest.mark.asyncio
async def test_no_working_dir_runs_normally(caplog):
    """Sanity: when working_dir is None, the watchdog never trips."""
    sent_messages: list[str] = []

    async def _capture_send(text: str) -> None:
        sent_messages.append(text)

    messenger = BossMessenger(
        _send_callback=_capture_send,
        chat_id="no-working-dir-test",
        session_id="no-working-dir-test-session",
    )

    task = BackgroundTask(messenger=messenger, working_dir=None)

    async def quick_work() -> str:
        await asyncio.sleep(0.4)
        return "done"

    with (
        patch.object(BackgroundTask, "HEARTBEAT_INTERVAL", 0.05),
        caplog.at_level(logging.WARNING, logger="agent.messenger"),
    ):
        await task.run(quick_work(), send_result=False)
        if task._task is not None:
            try:
                await asyncio.wait_for(task._task, timeout=5.0)
            except asyncio.CancelledError:
                pytest.fail("work was cancelled despite working_dir=None")
        if task._watchdog_task is not None:
            try:
                await task._watchdog_task
            except asyncio.CancelledError:
                pass

    # No cwd_vanished WARNINGs should have been emitted
    cwd_records = [r for r in caplog.records if "cwd_vanished" in r.message]
    assert not cwd_records, (
        f"cwd_vanished WARNING falsely fired with working_dir=None: "
        f"{[r.message for r in cwd_records]}"
    )
