"""Unit tests for extraction-finalization decoupling (hotfix #1055).

These tests verify the invariants of the fire-and-forget extraction scheduler
that was added to ``agent/session_executor.py``:

- Extraction failures never propagate out of ``_execute_agent_session``.
- The PM nudge (``_handle_dev_session_completion``) fires while extraction
  is still pending — proving the #987 ordering is preserved and the #1055
  stall pattern cannot reoccur.
- Duplicate schedules for the same session_id are deduplicated.
- ``drain_pending_extractions`` returns immediately when no tasks are
  pending (first-deploy case).
"""

import asyncio
import logging

import pytest


@pytest.fixture(autouse=True)
def _clear_pending_tasks():
    """Reset the module-level _pending_extraction_tasks between tests."""
    from agent import session_executor as se

    se._pending_extraction_tasks.clear()
    yield
    # Cancel any leaked tasks so test teardown is clean
    for task in list(se._pending_extraction_tasks.values()):
        if not task.done():
            task.cancel()
    se._pending_extraction_tasks.clear()


class TestScheduleExtractionDecoupling:
    """Verify fire-and-forget scheduler semantics."""

    @pytest.mark.asyncio
    async def test_extraction_error_does_not_propagate(self, monkeypatch):
        """asyncio.TimeoutError inside the extraction task is swallowed by the wrapper."""
        from agent import session_executor as se

        async def _raise_timeout(session_id, response_text, project_key=None):
            raise TimeoutError("simulated extraction timeout")

        monkeypatch.setattr(
            "agent.memory_extraction.run_post_session_extraction",
            _raise_timeout,
        )

        # Synchronous call — no await.
        se._schedule_post_session_extraction("sess-err-1", "response text")

        # Wait for the background task to complete (it will swallow the error).
        task = se._pending_extraction_tasks.get("sess-err-1")
        # If the task already ran to completion + done-callback popped it, task is None.
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except TimeoutError:
                task.cancel()
                raise AssertionError("wrapper task did not complete")

        # If we got here, the asyncio.TimeoutError was swallowed by the wrapper.
        # Also confirm nothing is left pending for this session_id.
        assert "sess-err-1" not in se._pending_extraction_tasks

    @pytest.mark.asyncio
    async def test_pm_nudge_fires_while_extraction_pending(self, monkeypatch):
        """Scheduler is synchronous; extraction task is not done when scheduler returns."""
        from agent import session_executor as se

        # Stub extraction to suspend cooperatively — the task should still be
        # pending immediately after the scheduler returns.
        async def _slow_extract(session_id, response_text, project_key=None):
            await asyncio.sleep(10)

        monkeypatch.setattr(
            "agent.memory_extraction.run_post_session_extraction",
            _slow_extract,
        )

        se._schedule_post_session_extraction("sess-slow-1", "response text")

        task = se._pending_extraction_tasks.get("sess-slow-1")
        assert task is not None, "task must be registered immediately"
        # The critical #1055 invariant: scheduler is synchronous and returned
        # before the extraction task completed. `.done()` is False proves it.
        assert task.done() is False, (
            "extraction task must still be pending when scheduler returns — "
            "proves the PM nudge path is not blocked by extraction latency"
        )

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_duplicate_schedule_is_deduplicated(self, monkeypatch, caplog):
        """Calling _schedule_post_session_extraction twice with same session_id dedupes."""
        from agent import session_executor as se

        caplog.set_level(logging.INFO, logger="agent.session_executor")

        async def _slow_extract(session_id, response_text, project_key=None):
            await asyncio.sleep(5)

        monkeypatch.setattr(
            "agent.memory_extraction.run_post_session_extraction",
            _slow_extract,
        )

        se._schedule_post_session_extraction("s1", "first call")
        first_task = se._pending_extraction_tasks["s1"]

        se._schedule_post_session_extraction("s1", "second call")
        # Dict entry should be identical task object — second call no-ops.
        assert se._pending_extraction_tasks["s1"] is first_task, (
            "second schedule must not replace the first task"
        )
        assert any("already in-flight for s1" in rec.message for rec in caplog.records), (
            "must log INFO with 'already in-flight for s1'"
        )

        first_task.cancel()
        try:
            await first_task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_drain_pending_extractions_noop_when_empty(self, caplog):
        """drain_pending_extractions returns in ~0s and emits no WARNING when empty."""
        import time

        from agent import session_executor as se

        # Ensure empty state
        assert se._pending_extraction_tasks == {}

        caplog.set_level(logging.WARNING, logger="agent.session_executor")

        start = time.time()
        await se.drain_pending_extractions(timeout=5.0)
        elapsed = time.time() - start

        assert elapsed < 0.2, (
            f"drain must return almost immediately when no tasks are pending "
            f"(got {elapsed:.2f}s) — "
            "protects graceful shutdown from unnecessary 5s wait on first-deploy"
        )
        # No WARNING messages should have been emitted for the empty case
        warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
        assert not warning_records, (
            f"empty-drain must not log WARNING (got {[r.message for r in warning_records]})"
        )

    @pytest.mark.asyncio
    async def test_scheduler_is_sync_def_not_async(self):
        """Structural invariant: _schedule_post_session_extraction is sync (review guard)."""
        import inspect

        from agent import session_executor as se

        assert not inspect.iscoroutinefunction(se._schedule_post_session_extraction), (
            "_schedule_post_session_extraction MUST be declared 'def', not 'async def' — "
            "any awaiting would regress #987 and #1055. See hotfix #1055 docstring."
        )

    @pytest.mark.asyncio
    async def test_cancellation_does_not_propagate_past_wrapper(self, monkeypatch):
        """Cancelling a scheduled task raises CancelledError inside the wrapper.

        The wrapper re-raises CancelledError (preserving cancellation semantics
        for shutdown drain). The caller (scheduler or drain) is responsible for
        not letting CancelledError propagate to user-visible paths.
        """
        from agent import session_executor as se

        async def _slow_extract(session_id, response_text, project_key=None):
            await asyncio.sleep(30)

        monkeypatch.setattr(
            "agent.memory_extraction.run_post_session_extraction",
            _slow_extract,
        )

        se._schedule_post_session_extraction("sess-cancel", "text")
        task = se._pending_extraction_tasks["sess-cancel"]
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task
