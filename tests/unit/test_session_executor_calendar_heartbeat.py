"""Unit tests for the owned calendar-heartbeat task lifecycle (issue #2590).

``asyncio.create_task(_calendar_heartbeat(...))`` used to be fired with no
reference kept, no done-callback, and no shutdown drain — the confirmed
mechanism behind the #2574 tail wedge. These tests verify the replacement
scheduler mirrors the extraction pattern (#1055):

- ``_schedule_calendar_heartbeat`` registers the task in
  ``_pending_calendar_tasks`` and the done-callback removes it.
- Heartbeat failures are logged by the done-callback, never propagated.
- ``drain_pending_calendar_heartbeats`` is a no-op when nothing is pending,
  lets fast tasks finish, and cancels stuck ones.
- The whole heartbeat body — including subprocess spawn — is bounded by
  ``CALENDAR_HEARTBEAT_TIMEOUT`` (the old code only bounded ``communicate()``).
"""

import asyncio

import pytest

import agent.session_executor as _se_module

# Captured at collection time, before the autouse ``no_calendar_subprocess_in_tests``
# fixture swaps in a no-op. Lets the timeout test exercise the real coroutine.
_REAL_CALENDAR_HEARTBEAT = _se_module._calendar_heartbeat


@pytest.fixture(autouse=True)
def _clear_pending_calendar_tasks():
    """Reset the module-level _pending_calendar_tasks between tests."""
    from agent import session_executor as se

    se._pending_calendar_tasks.clear()
    yield
    for task in list(se._pending_calendar_tasks):
        if not task.done():
            task.cancel()
    se._pending_calendar_tasks.clear()


class TestScheduleCalendarHeartbeat:
    """Verify the scheduler owns the task it creates."""

    @pytest.mark.asyncio
    async def test_task_registered_and_removed_on_completion(self, monkeypatch):
        from agent import session_executor as se

        started = asyncio.Event()
        release = asyncio.Event()

        async def _slow_heartbeat(slug, project=None):
            started.set()
            await release.wait()

        monkeypatch.setattr(se, "_calendar_heartbeat", _slow_heartbeat)

        se._schedule_calendar_heartbeat("test-slug", project="test-proj")
        await asyncio.wait_for(started.wait(), timeout=2.0)
        assert len(se._pending_calendar_tasks) == 1, "task must be registered while running"

        release.set()
        await asyncio.gather(*se._pending_calendar_tasks)
        await asyncio.sleep(0)  # let done-callbacks run
        assert not se._pending_calendar_tasks, "done-callback must deregister the task"

    @pytest.mark.asyncio
    async def test_heartbeat_exception_logged_not_propagated(self, monkeypatch, caplog):
        from agent import session_executor as se

        async def _boom(slug, project=None):
            raise RuntimeError("simulated heartbeat crash")

        monkeypatch.setattr(se, "_calendar_heartbeat", _boom)

        with caplog.at_level("WARNING", logger=se.logger.name):
            se._schedule_calendar_heartbeat("crash-slug")
            (task,) = se._pending_calendar_tasks
            with pytest.raises(RuntimeError):
                await task
            await asyncio.sleep(0)  # let done-callback run

        assert not se._pending_calendar_tasks
        assert any("simulated heartbeat crash" in r.message for r in caplog.records), (
            "done-callback must log the task exception"
        )


class TestDrainPendingCalendarHeartbeats:
    """Verify shutdown drain semantics mirror drain_pending_extractions."""

    @pytest.mark.asyncio
    async def test_noop_when_empty(self):
        from agent import session_executor as se

        assert not se._pending_calendar_tasks
        await se.drain_pending_calendar_heartbeats(timeout=0.1)  # must return immediately

    @pytest.mark.asyncio
    async def test_fast_task_completes_within_drain(self, monkeypatch):
        from agent import session_executor as se

        async def _fast_heartbeat(slug, project=None):
            await asyncio.sleep(0)

        monkeypatch.setattr(se, "_calendar_heartbeat", _fast_heartbeat)
        se._schedule_calendar_heartbeat("fast-slug")

        await se.drain_pending_calendar_heartbeats(timeout=2.0)
        await asyncio.sleep(0)
        assert not se._pending_calendar_tasks

    @pytest.mark.asyncio
    async def test_stuck_task_cancelled_by_drain(self, monkeypatch):
        from agent import session_executor as se

        async def _stuck_heartbeat(slug, project=None):
            await asyncio.sleep(600)

        monkeypatch.setattr(se, "_calendar_heartbeat", _stuck_heartbeat)
        se._schedule_calendar_heartbeat("stuck-slug")
        (task,) = se._pending_calendar_tasks

        await se.drain_pending_calendar_heartbeats(timeout=0.05)
        # Cancellation lands on the next loop pass.
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert task.cancelled()
        assert not se._pending_calendar_tasks


class TestHeartbeatTimeoutBoundsSpawn:
    """The wait_for must cover subprocess creation, not just communicate()."""

    @pytest.mark.asyncio
    async def test_hang_during_spawn_is_bounded(self, monkeypatch):
        from agent import session_executor as se

        async def _hanging_spawn(*cmd, **kwargs):
            await asyncio.sleep(600)  # simulate a wedge inside _connect_pipes

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _hanging_spawn)
        monkeypatch.setattr(se, "CALENDAR_HEARTBEAT_TIMEOUT", 0.05)

        # Must return (logging a warning), not hang or raise.
        await asyncio.wait_for(_REAL_CALENDAR_HEARTBEAT("timeout-slug", project="p"), timeout=2.0)
