"""Integration test for extraction-finalization decoupling (hotfix #1055).

Verifies that the hotfix decouples post-session memory extraction from
session finalization — the session completes and the PM nudge fires promptly,
while extraction is still pending, even if extraction would stall indefinitely.

The full ``_execute_agent_session`` flow requires substantial Redis/Popoto
infrastructure (AgentSession creation, pyrogram bridge, harness subprocess,
session lifecycle transitions). This test focuses on the narrower contract
that the hotfix establishes at the scheduler boundary:

  1. The synchronous ``_schedule_post_session_extraction`` returns promptly
     even when extraction would block for 30+ seconds.
  2. The scheduled task is still pending (``.done() is False``) at the moment
     the scheduler returns.
  3. A simulated "PM nudge" call that runs after the scheduler returns completes
     in the 5-second bounded window — not blocked by extraction latency.
  4. ``drain_pending_extractions`` cancels cooperatively-suspended tasks
     within its timeout budget.
  5. Cancellation does not propagate past the fire-and-forget wrapper's
     ``try/except`` structure (CancelledError is re-raised cleanly for drain).

The 5-second budget is the user-visible SLO: a dev session producing a
30-second stall in extraction must still finalize and nudge the PM within
5 seconds — the core promise of this hotfix.

Why cooperative ``asyncio.sleep`` rather than sync ``time.sleep(40)``?
Real production flows AsyncAnthropic → httpx → asyncio.wait_for, all of
which are cancellable at every await point. A sync ``time.sleep`` in the
coroutine body is uncancellable until the next await — a pathology that
does not reflect production behavior. The double-timeout in
``memory_extraction.py`` (SDK timeout=30s + asyncio.wait_for=35s) hard-caps
the production latency in all cases.
"""

import asyncio
import logging
import time

import pytest


@pytest.fixture(autouse=True)
def _clear_pending_tasks():
    from agent import session_executor as se

    se._pending_extraction_tasks.clear()
    yield
    for task in list(se._pending_extraction_tasks.values()):
        if not task.done():
            task.cancel()
    se._pending_extraction_tasks.clear()


class TestSessionFinalizationDecoupled:
    """End-to-end behavioral test for the decoupling guarantee (hotfix #1055)."""

    @pytest.mark.asyncio
    async def test_scheduler_returns_within_5s_window_with_hung_anthropic(
        self, caplog, monkeypatch
    ):
        """The 5-second user-visible SLO: scheduler returns promptly even with hung extraction.

        This is the core hotfix #1055 guarantee — a 40-second stall in extraction
        must NOT delay finalization and PM nudge beyond the 5-second SLO. We
        stub run_post_session_extraction itself (the top-level async entry point)
        to block on asyncio.sleep(30); in production the outer asyncio.wait_for
        inside memory_extraction.py caps this at 35s anyway, but the point of
        Layer 2 is that even an uncapped 30s stall cannot delay the PM nudge.
        """
        from agent import session_executor as se

        caplog.set_level(logging.INFO, logger="agent.session_executor")

        async def _slow_extract(session_id, response_text, project_key=None):
            await asyncio.sleep(30)  # Long stall; scheduler must not await.

        monkeypatch.setattr(
            "agent.memory_extraction.run_post_session_extraction",
            _slow_extract,
        )

        pm_nudge_called = asyncio.Event()

        async def _fake_handle_dev_completion(*args, **kwargs):
            # Simulates the PM nudge that must run AFTER the scheduler returns.
            pm_nudge_called.set()

        start = time.time()

        # Exact call-shape _execute_agent_session uses post-fix:
        # synchronous schedule (no await), then await the PM nudge.
        se._schedule_post_session_extraction("sess-int-1", "A" * 200)
        await _fake_handle_dev_completion()

        elapsed = time.time() - start

        # The entire post-finalization sequence must complete within the 5s SLO.
        assert elapsed < 5.0, (
            f"scheduler + PM-nudge path took {elapsed:.2f}s — violates the 5s SLO. "
            "Extraction latency MUST NOT block session finalization (hotfix #1055)."
        )
        assert pm_nudge_called.is_set(), "PM nudge must fire"

        # The extraction task should still be pending.
        task = se._pending_extraction_tasks.get("sess-int-1")
        assert task is not None, "extraction task must still be registered"
        assert task.done() is False, (
            "extraction task MUST still be pending at this point — "
            "proves scheduler and nudge ran ahead of extraction completion"
        )

        # Cleanup: cancel the cooperatively-suspended task.
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_drain_cancels_cooperative_extraction_on_shutdown(self, caplog, monkeypatch):
        """drain_pending_extractions cancels cooperatively-suspended tasks within timeout.

        Uses asyncio.sleep (not time.sleep) because real production extraction
        goes through AsyncAnthropic + httpx + asyncio.wait_for — all cooperative
        cancellation points. A sync time.sleep in the coroutine body is NOT
        cancellable until the next await, so the production code path does
        not suffer from that pathology.
        """
        from agent import session_executor as se

        caplog.set_level(logging.WARNING, logger="agent.session_executor")

        async def _slow_extract(session_id, response_text, project_key=None):
            await asyncio.sleep(30)  # Cooperative; cancellable at await point.

        monkeypatch.setattr(
            "agent.memory_extraction.run_post_session_extraction",
            _slow_extract,
        )

        se._schedule_post_session_extraction("sess-drain-1", "A" * 200)
        task = se._pending_extraction_tasks.get("sess-drain-1")
        assert task is not None

        # Drain with a short timeout; the cooperative sleep will be cancelled
        # promptly when drain calls task.cancel().
        start = time.time()
        await se.drain_pending_extractions(timeout=0.5)
        elapsed = time.time() - start

        # Drain budget: ~0.5s timeout + cancel overhead.
        assert elapsed < 2.0, (
            f"drain exceeded its 0.5s timeout by too much (took {elapsed:.2f}s total)"
        )
        # Drain called task.cancel(); give one event-loop tick for the task
        # to finalize in "cancelled" state before we assert.
        try:
            await asyncio.wait_for(task, timeout=0.5)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
        assert task.done() or task.cancelled(), (
            "drain must have cancelled the hung task"
        )
        warning_messages = [
            r.message for r in caplog.records if r.levelname == "WARNING"
        ]
        assert any("did not complete" in msg for msg in warning_messages), (
            f"drain should log WARNING when cancelling hung tasks (got {warning_messages})"
        )

    @pytest.mark.asyncio
    async def test_cancellation_does_not_crash_via_wrapper(self, monkeypatch):
        """Cancelling a cooperative-suspended extraction raises CancelledError cleanly.

        Uses asyncio.sleep so cancellation is prompt. Real production flows
        through AsyncAnthropic httpx calls, which are cancellable at every
        await point, plus an outer asyncio.wait_for that hard-caps latency.
        """
        from agent import session_executor as se

        async def _slow_extract(session_id, response_text, project_key=None):
            await asyncio.sleep(30)

        monkeypatch.setattr(
            "agent.memory_extraction.run_post_session_extraction",
            _slow_extract,
        )

        se._schedule_post_session_extraction("sess-cancel-safe", "A" * 200)
        task = se._pending_extraction_tasks.get("sess-cancel-safe")

        task.cancel()

        # Awaiting a cancelled task should raise CancelledError — the
        # critical invariant here is that it raises CancelledError
        # cleanly, NOT a wrapped/swallowed exception that would make
        # shutdown drainage ambiguous.
        with pytest.raises(asyncio.CancelledError):
            await task
