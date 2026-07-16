"""Acceptance test for the hot-path Redis off-loop cut-over (issue #1826).

Exercises, end-to-end and with a real (test) Redis, the composition between:

  * the drain-loop idle-check's off-loop offload (`agent/redis_offload.py`
    via `agent.agent_session_queue._worker_loop`'s clear-then-check cut-over
    at the `session is None` branch), and
  * the #1815 on-loop liveness beacon / dead-man's-switch
    (`agent.session_state.get_loop_tick`, `worker.__main__._heartbeat_cycle`).

Redis is artificially slowed by wrapping the `offload_redis` seam itself
(monkeypatching the name as imported into `agent.agent_session_queue`) so
that ONLY the one call this plan's cut-over routes through it is slowed --
everything else the drain loop does (popping via `_pop_agent_session`,
`transition_status`'s CAS re-read, etc.) runs at normal speed, exactly as
in production. The wrapper still delegates to the REAL `offload_redis`, so
the actual `_redis_io_pool` bulkhead thread-pool dispatch is exercised, not
faked -- the "slow Redis" sleeps inside that executor thread, never on the
event loop and never against the real Redis server's own timing.

Four acceptance properties (mirrors the plan's Test Impact row for this
file, docs/plans/hot-path-redis-off-loop.md):

  (a) `get_loop_tick()` (the #1815 beacon) keeps advancing in lockstep with
      wall-clock time while the offloaded idle-check is in flight -- the
      loop is not frozen by the slow call.
  (b) An unrelated coroutine (a plain `asyncio.sleep`-based counter) makes
      concurrent progress during the same window.
  (c) A dead-man's-switch check (`worker.__main__._heartbeat_cycle`) run
      while the slow call is in flight does NOT trigger `_self_kill` --
      patched so nothing actually aborts the test process.
  (d) `enqueue_agent_session()` fired while the offloaded idle-check is in
      flight is picked up promptly -- because the cut-over clears the
      wakeup event BEFORE running the query (clear-then-check), the drain
      loop does not stall behind the full bounded wait.

Unit-level coverage of the clear-then-check ordering itself (with a fully
mocked AgentSession) already lives in
tests/unit/test_agent_session_queue_async.py::TestClearThenCheckDrainLoop.
This file is the integration-level composition proof: real asyncio event
loop, real (test-db) Redis, real `enqueue_agent_session()`.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest

import worker.__main__ as wm
from agent.agent_session_queue import (
    _active_events,
    _active_workers,
    _worker_loop,
    enqueue_agent_session,
)
from agent.redis_offload import offload_redis as _real_offload_redis
from agent.session_state import bump_loop_tick, get_loop_tick
from config.enums import SessionType
from models.agent_session import AgentSession

# Modest but clearly-observable slowdown -- long enough to prove the loop
# doesn't block for its duration, short enough to keep the suite fast.
SLOW_SECONDS = 1.2
# Fire the concurrent enqueue partway through the in-flight offload so it
# genuinely races the slow query rather than landing before/after it.
ENQUEUE_DELAY_S = 0.4
TICKER_INTERVAL_S = 0.1


@pytest.mark.asyncio
async def test_slow_redis_does_not_freeze_loop_or_park_drain(monkeypatch):
    chat_id = f"slow-redis-1826-{uuid.uuid4().hex[:8]}"

    # --- Artificially slow "Redis", scoped to the ONE offloaded call ----
    # Wrap the `offload_redis` name as imported into agent_session_queue.py
    # (the drain-loop idle-check's cut-over seam) so ONLY that call is
    # slowed. Delegates to the real offload_redis so the actual
    # _redis_io_pool thread-pool dispatch still runs -- this proves the
    # mechanism, not just its effect.
    filter_threads: list[str] = []

    async def slow_offload_redis(fn, *args, **kwargs):
        def wrapped():
            filter_threads.append(threading.current_thread().name)
            time.sleep(SLOW_SECONDS)
            return fn()

        return await _real_offload_redis(wrapped)

    monkeypatch.setattr("agent.agent_session_queue.offload_redis", slow_offload_redis)
    # Large relative to SLOW_SECONDS so that IF the wakeup were somehow
    # lost, the resulting stall would be obviously distinguishable from the
    # correct (prompt) pickup asserted below.
    monkeypatch.setattr("agent.agent_session_queue.DRAIN_TIMEOUT", SLOW_SECONDS * 5)

    # --- (a) on-loop #1815 beacon ticker --------------------------------
    # Mirrors worker/__main__.py's _loop_tick_task, at a faster cadence so
    # the test doesn't need to wait multiple seconds per sample.
    ticker_stop = asyncio.Event()

    async def ticker() -> None:
        while not ticker_stop.is_set():
            bump_loop_tick()
            await asyncio.sleep(TICKER_INTERVAL_S)

    ticker_task = asyncio.create_task(ticker())

    # --- (b) unrelated coroutine progress counter -----------------------
    unrelated_progress = {"count": 0}

    async def unrelated_coroutine() -> None:
        while not ticker_stop.is_set():
            unrelated_progress["count"] += 1
            await asyncio.sleep(TICKER_INTERVAL_S)

    unrelated_task = asyncio.create_task(unrelated_coroutine())

    # --- (c) dead-man's-switch self-kill spy ----------------------------
    self_kill_calls: list[str] = []
    monkeypatch.setattr(wm, "_self_kill", lambda: self_kill_calls.append("killed"))
    monkeypatch.setattr(wm, "WORKER_DEADMAN_ENABLED", True)

    # --- Drain loop under test -------------------------------------------
    executed: list[AgentSession] = []
    execution_ts: dict[str, float] = {}

    import agent.agent_session_queue as asq

    async def fake_execute(session: AgentSession) -> None:
        executed.append(session)
        execution_ts["t"] = time.monotonic()
        # Only one session is ever enqueued in this test -- end the
        # standalone loop cleanly instead of letting it run another
        # (also-slowed) idle-check cycle.
        asq._session_state._shutdown_requested = True

    event = asyncio.Event()
    _active_events[chat_id] = event
    asq._session_state._shutdown_requested = False

    # Initialized before the try so the finally can always cancel-await it,
    # even if the body raises before the task is created (#2120: an orphaned
    # worker_task leaks `coroutine '_worker_loop' was never awaited`).
    worker_task = None

    try:
        with (
            patch("agent.agent_session_queue._execute_agent_session", side_effect=fake_execute),
            patch("agent.agent_session_queue._complete_agent_session", new_callable=AsyncMock),
            patch("agent.agent_session_queue._check_restart_flag", return_value=False),
            patch("agent.agent_session_queue.save_session_snapshot"),
            patch.dict("os.environ", {"VALOR_WORKER_MODE": "standalone"}),
        ):
            worker_task = asyncio.create_task(_worker_loop(chat_id, event, is_project_keyed=False))
            _active_workers[chat_id] = worker_task

            # Give the loop one tick to pop (finds nothing pending yet),
            # clear the event, and dispatch the slow offloaded idle-check --
            # i.e., let it genuinely enter the in-flight window before we
            # sample/enqueue against it.
            await asyncio.sleep(0.05)

            mid_time = time.monotonic()
            mid_tick = get_loop_tick()
            assert mid_tick is not None, (
                "beacon must have ticked at least once before the offload window sample point"
            )
            mid_progress = unrelated_progress["count"]

            # (c) A dead-man's-switch check evaluated WHILE the slow offload
            # is in flight must see a fresh beacon and must NOT self-kill.
            wm._heartbeat_cycle(
                armed=True,
                thread_start=time.monotonic() - 30.0,
                beacon_log_next=0.0,
            )
            assert not self_kill_calls, (
                "a dead-man's-switch check performed while the offloaded Redis "
                "call is in flight must not trigger _self_kill -- the on-loop "
                "beacon must still be fresh"
            )

            # (d) Fire a real enqueue while the offload is still sleeping.
            await asyncio.sleep(ENQUEUE_DELAY_S)
            enqueue_time = time.monotonic()
            await enqueue_agent_session(
                project_key="test",
                session_id=f"s-{uuid.uuid4().hex[:8]}",
                working_dir="/tmp/test",
                message_text="enqueued during in-flight offload",
                sender_name="Test",
                chat_id=chat_id,
                telegram_message_id=1,
                session_type=SessionType.TEAMMATE,
            )

            # Second dead-man's-switch check, later in the still-in-flight
            # window, must also stay quiet.
            await asyncio.sleep(SLOW_SECONDS * 0.3)
            wm._heartbeat_cycle(
                armed=True,
                thread_start=time.monotonic() - 30.0,
                beacon_log_next=0.0,
            )
            assert not self_kill_calls, (
                "dead-man's-switch must still not fire this late in the in-flight offload window"
            )

            await asyncio.wait_for(worker_task, timeout=SLOW_SECONDS + 5.0)

            final_time = time.monotonic()
            final_tick = get_loop_tick()
    finally:
        ticker_stop.set()
        await asyncio.gather(ticker_task, unrelated_task, return_exceptions=True)
        # Cancel AND await the worker loop task to completion on every exit
        # path (#2120). On the happy path it already completed via the wait_for
        # above; on any early failure/timeout it is still running, and dropping
        # it here would leak `coroutine '_worker_loop' was never awaited` at GC,
        # wedging full-suite teardown. Mirrors the _teardown_loop pattern.
        if worker_task is not None and not worker_task.done():
            worker_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(worker_task), timeout=1.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
        asq._session_state._shutdown_requested = False
        _active_events.pop(chat_id, None)
        _active_workers.pop(chat_id, None)

    # --- Assertions --------------------------------------------------------

    # The slow callable really did run off the event loop (proves the
    # composition mechanism, not just its effect).
    assert filter_threads, "the offloaded idle-check callable was never invoked"
    assert all(name.startswith("redis-io-") for name in filter_threads), (
        f"offloaded idle-check must run on the redis-io- bulkhead thread, not the "
        f"event loop thread; observed thread names: {filter_threads}"
    )

    # (a) On-loop beacon kept pace with wall-clock across the whole
    # remainder of the slow-call window -- a frozen loop could not have
    # advanced last_loop_tick this closely.
    wall_elapsed = final_time - mid_time
    tick_elapsed = final_tick - mid_tick
    assert tick_elapsed > wall_elapsed - 0.4, (
        f"on-loop beacon fell behind wall-clock by {wall_elapsed - tick_elapsed:.2f}s "
        "during the slow-Redis window -- the event loop must not stall while the "
        "offloaded query is in flight"
    )

    # (b) The unrelated coroutine made real, roughly wall-clock-paced
    # progress over the same window.
    progress_made = unrelated_progress["count"] - mid_progress
    assert progress_made >= max(2, int(wall_elapsed / (TICKER_INTERVAL_S * 3))), (
        f"unrelated coroutine only progressed {progress_made} times over "
        f"{wall_elapsed:.2f}s -- it should make concurrent progress while the "
        "offloaded Redis call is in flight"
    )

    # (c) No self-kill fired anywhere in the window (already asserted twice
    # above; re-assert post-hoc for a single clear final signal).
    assert not self_kill_calls, "the #1815 dead-man's-switch must never fire on a slow Redis"

    # (d) The enqueued session was picked up -- and picked up promptly, not
    # stalled behind an extra full DRAIN_TIMEOUT on top of the outstanding
    # offload. If the wakeup had been lost (old check-before-clear
    # ordering), the loop would have needed to wait out the (patched, much
    # larger) DRAIN_TIMEOUT after the in-flight offload's empty result came
    # back, ballooning this well past SLOW_SECONDS + 1.0.
    assert len(executed) == 1
    assert executed[0].message_text == "enqueued during in-flight offload"
    pickup_latency = execution_ts["t"] - enqueue_time
    assert pickup_latency < SLOW_SECONDS + 1.0, (
        f"drain loop took {pickup_latency:.2f}s to pick up a session enqueued "
        "during the in-flight offload -- looks like the clear-then-check wakeup "
        "was lost and the loop fell back to waiting out the full DRAIN_TIMEOUT"
    )
