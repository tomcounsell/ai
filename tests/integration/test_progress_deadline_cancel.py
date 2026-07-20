"""Integration tests for the progress-deadline cancel scope (issue #1820 Fix #3).

Drives the *real* ``_worker_loop`` (``agent.agent_session_queue._worker_loop``)
against a monkeypatched ``_pop_agent_session`` (returns one real, Redis-backed
``AgentSession`` then ``None``) and a caller-controlled ``_execute_agent_session``
sentinel coroutine — the same pattern ``tests/integration/test_worker_wedge_pending.py``
uses for ``TestWorkerLoopSemaphorePark``. The owned-task pattern, the progress-poll
watcher, and the three-branch ``CancelledError`` disambiguation are all exercised
through the real worker-loop code path — only ``_apply_recovery_transition`` is
patched, to isolate Fix #3's own new code from ``_apply_recovery_transition``'s
internal recovery-attempt bookkeeping (already covered by its own test suite).

Post-cutover (#1924): the PTY substrate is gone, and with it the transport-aware
PTY progress signal, the ``PTYPool.kill_orphans`` delegation, and the granite
PTY-alive Tier-2 reprieve. The surviving deadline machinery is headless-only.

Covers:
- Acceptance criterion #2 (plan): a no-progress session is cancelled, its slot
  reclaimed, and NOT re-queued via the startup-recovery path
  (``deadline_cancelled`` swallow — Branch 1).
- A progressing session is never cancelled.
- A ``waiting_for_children``-shaped session (Tier-2 "compacting" reprieve) is
  never cancelled, even past the deadline.
- Blocker r4 regression guard: an out-of-band cancel of the INNER SDK task
  (``handle.task``, simulating ``_apply_recovery_transition``) does NOT tear
  down the worker loop — the ``Task.cancelling()``-based Branch 2/3
  disambiguation (a builder-verified hardening of the plan's literal
  row-status heuristic — see the code comment at the ``except
  asyncio.CancelledError:`` handler in ``agent_session_queue.py``) survives it.
- Branch 2 backstop: a CancelledError bubbling in after the row is already
  terminal is swallowed too.
- Branch 3 / Blocker 1 round 2: a genuine worker-shutdown cancel tears down
  the owned exec_task (no orphaned subprocess) before re-raising.
- Concern 1: a declined recovery (``_apply_recovery_transition`` returns
  False) still forces the row terminal and skips the outer finally.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import agent.agent_session_queue as _aq
import agent.session_state as _session_state
from agent.session_state import SessionHandle, _active_events, _active_sessions, _active_workers
from agent.slot_lease import SlotLeaseRegistry
from models.agent_session import AgentSession
from models.session_lifecycle import finalize_session, transition_status

PROJECT_KEY = "test-progress-deadline"


def _create_session(session_id: str, **overrides) -> AgentSession:
    """Create a minimal, real (Redis-backed) AgentSession for the watcher to observe."""
    defaults: dict[str, Any] = {
        "project_key": PROJECT_KEY,
        "status": "running",
        "priority": "normal",
        "created_at": time.time(),
        "started_at": time.time(),
        "session_id": session_id,
        "working_dir": "/tmp/progress-deadline-test",
        "message_text": "progress deadline test",
        "sender_name": "ProgressDeadlineTester",
        "chat_id": f"chat-{session_id}",
        "telegram_message_id": 1,
        "session_type": "teammate",
    }
    defaults.update(overrides)
    return AgentSession.create(**defaults)


def _cleanup() -> None:
    stale = [s for s in AgentSession.query.all() if s.project_key == PROJECT_KEY]
    for s in stale:
        try:
            s.delete()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Fast, deterministic deadline/poll constants; clean registry + sessions."""
    prior_registry = _session_state._slot_registry
    monkeypatch.setattr(_aq, "SESSION_PROGRESS_DEADLINE_S", 0.15)
    monkeypatch.setattr(_aq, "PROGRESS_POLL_S", 0.05)
    monkeypatch.setenv("VALOR_WORKER_MODE", "standalone")
    monkeypatch.delenv("DISABLE_PROGRESS_KILL", raising=False)
    yield
    _session_state._slot_registry = prior_registry
    _active_sessions.clear()
    _cleanup()


async def _run_worker_loop(monkeypatch, worker_key: str, session: AgentSession, execute_fn):
    """Spawn the real ``_worker_loop`` for exactly one popped session.

    ``_pop_agent_session`` is monkeypatched to return ``session`` once, then
    ``None`` forever after — mirrors ``TestWorkerLoopSemaphorePark``'s
    established pattern. Returns the spawned ``asyncio.Task`` — the caller is
    responsible for driving/asserting and for cancelling it in a ``finally``.
    """
    pop_count = [0]

    async def _mock_pop(wk, is_pk=False):
        pop_count[0] += 1
        if pop_count[0] == 1:
            return session
        return None

    monkeypatch.setattr(_aq, "_pop_agent_session", _mock_pop)
    monkeypatch.setattr(_aq, "_execute_agent_session", execute_fn)

    event = asyncio.Event()
    _active_events[worker_key] = event
    loop_task = asyncio.create_task(
        _aq._worker_loop(worker_key, event, False),
        name=f"progress-deadline-test-loop-{worker_key}",
    )
    _active_workers[worker_key] = loop_task
    return loop_task


async def _teardown_loop(loop_task: asyncio.Task, worker_key: str) -> None:
    if loop_task is not None and not loop_task.done():
        loop_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(loop_task), timeout=1.0)
        except (TimeoutError, asyncio.CancelledError):
            pass
    _active_workers.pop(worker_key, None)
    _active_events.pop(worker_key, None)


def _fresh(session: AgentSession) -> AgentSession | None:
    return AgentSession.query.get(redis_key=session.db_key.redis_key)


# ---------------------------------------------------------------------------
# Sentinels
# ---------------------------------------------------------------------------


async def _hang_forever(session: AgentSession) -> None:
    """Never completes on its own — only exits via an external cancel."""
    await asyncio.sleep(3600)


def _tracking_hang_forever(tracking: dict):
    async def _inner(session: AgentSession) -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            tracking["cancelled"] = True
            raise

    return _inner


def _quick_progress(tracking: dict):
    """Bumps last_tool_use_at a few times, then completes normally — well
    within the (generous, per-test-overridden) SESSION_PROGRESS_DEADLINE_S.
    Sets `tracking["done"]` on completion so the test can poll a direct
    signal instead of racing `loop_task.done()` (which never fires in
    standalone mode — the loop parks indefinitely after the queue drains).
    """

    async def _inner(session: AgentSession) -> None:
        for _ in range(3):
            fresh = _fresh(session)
            if fresh is not None:
                fresh.last_tool_use_at = datetime.now(UTC)
                fresh.save(update_fields=["last_tool_use_at"])
            await asyncio.sleep(0.03)
        tracking["done"] = True

    return _inner


def _absorbing_sentinel(tracking: dict):
    """Reproduces the #1039 BackgroundTask/`_execute_agent_session` shape:
    registers an INNER task on the `_active_sessions` handle and awaits it
    under `except Exception` — which does NOT catch `asyncio.CancelledError`
    (BaseException subclass since Python 3.8). Cancelling `handle.task`
    (simulating `_apply_recovery_transition`) therefore propagates
    CancelledError through this sentinel uncaught, exactly like the real
    `session_executor.py:1942` `await task._task` under `except Exception`.
    """

    async def _inner(session: AgentSession) -> None:
        inner_task = asyncio.create_task(asyncio.sleep(3600))
        _active_sessions[session.agent_session_id] = SessionHandle(task=inner_task, pid=424242)
        tracking["inner_task"] = inner_task
        try:
            await inner_task
        except Exception:
            # Matches session_executor.py:1942's `except Exception` — does
            # NOT catch CancelledError, so a cancelled inner_task's
            # CancelledError propagates straight through.
            pass
        finally:
            _active_sessions.pop(session.agent_session_id, None)

    return _inner


# ---------------------------------------------------------------------------
# Acceptance criterion #2 — no-progress session cancelled and reclaimed
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_no_progress_session_cancelled_reclaimed_not_requeued(
    monkeypatch, redis_test_db, caplog
):
    """A session with no progress past the deadline is cancelled (Branch 1),
    its slot is reclaimed, and it is NOT re-queued via the startup-recovery
    ("will be re-queued by startup recovery") path.
    """
    registry = SlotLeaseRegistry(max_concurrent=2)
    _session_state._slot_registry = registry
    await registry.acquire()  # pre-occupy one slot so we can prove reclaim

    worker_key = "test-pd-01"
    session = _create_session(
        "pd-no-progress-1",
        last_tool_use_at=None,
        last_turn_at=None,
    )

    recovery_calls: list[dict] = []

    async def _fake_apply_recovery_transition(entry, **kwargs):
        recovery_calls.append(kwargs)
        finalize_session(entry, "killed", reason="test: progress deadline")
        return True

    loop_task = None
    try:
        with patch.object(
            _aq,
            "_apply_recovery_transition",
            AsyncMock(side_effect=_fake_apply_recovery_transition),
        ):
            loop_task = await _run_worker_loop(monkeypatch, worker_key, session, _hang_forever)

            # Poll until the recovery fires (bounded).
            for _ in range(200):
                await asyncio.sleep(0.02)
                if recovery_calls:
                    break

        assert recovery_calls, "Expected _apply_recovery_transition to fire on the deadline path"
        # This session NEVER produced SDK output (last_tool_use_at/last_turn_at
        # both None, _hang_forever emits nothing) — the issue #2181 init-hang
        # shape. The deadline kill on a never-communicated session now routes
        # through the terminal-finalizing `init_hang` reason_kind (circuit
        # breaker), not the requeue-eligible `progress_deadline`.
        assert recovery_calls[0]["reason_kind"] == "init_hang"
        assert recovery_calls[0]["handle"] is None

        fresh = _fresh(session)
        assert fresh is not None and fresh.status == "killed"

        # Slot reclaimed: the session's own bound lease is freed (the
        # manually pre-occupied permit — acquired but never bound to
        # anything — deliberately stays held; it exists only to prove the
        # registry wasn't already fully drained before the reclaim).
        assert registry.permits_free() == 1
        assert registry.leases() == []

        # NOT re-queued via the startup-recovery path.
        assert "will be re-queued by startup recovery" not in caplog.text
    finally:
        await _teardown_loop(loop_task, worker_key)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_progressing_session_not_cancelled(monkeypatch, redis_test_db):
    """A session making steady progress (fresh last_tool_use_at) completes
    normally and is never touched by the deadline watcher.

    Uses a generous deadline (well above the sentinel's own runtime) so this
    is not a timing race against SESSION_PROGRESS_DEADLINE_S under parallel
    (xdist) load — the assertion is "never touched", not "just barely wins".
    """
    monkeypatch.setattr(_aq, "SESSION_PROGRESS_DEADLINE_S", 5.0)

    worker_key = "test-pd-02"
    session = _create_session("pd-progressing-1")

    apply_recovery_mock = AsyncMock()
    tracking: dict = {}

    loop_task = None
    try:
        with patch.object(_aq, "_apply_recovery_transition", apply_recovery_mock):
            loop_task = await _run_worker_loop(
                monkeypatch, worker_key, session, _quick_progress(tracking)
            )

            for _ in range(100):
                await asyncio.sleep(0.02)
                if tracking.get("done"):
                    break

        assert tracking.get("done") is True, "Sentinel did not complete in time"
        apply_recovery_mock.assert_not_called()
    finally:
        await _teardown_loop(loop_task, worker_key)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_waiting_for_children_reprieve_preserved(monkeypatch, redis_test_db):
    """A session with a live Tier-2 "compacting" reprieve signal is NOT
    cancelled even after repeatedly crossing the progress deadline.
    """
    worker_key = "test-pd-03"
    session = _create_session(
        "pd-reprieve-1",
        last_tool_use_at=None,
        last_turn_at=None,
        last_compaction_ts=time.time(),  # fresh — Tier-2 "compacting" reprieve
    )

    apply_recovery_mock = AsyncMock()
    tracking: dict = {}

    loop_task = None
    try:
        with patch.object(_aq, "_apply_recovery_transition", apply_recovery_mock):
            loop_task = await _run_worker_loop(
                monkeypatch, worker_key, session, _tracking_hang_forever(tracking)
            )
            # Let several deadline-exceeded polls elapse — each must reprieve.
            await asyncio.sleep(0.6)

        apply_recovery_mock.assert_not_called()
        fresh = _fresh(session)
        assert fresh is not None and fresh.status == "running"
        assert (fresh.reprieve_count or 0) >= 1, (
            "Expected the real Tier-2 reprieve gate to have fired at least once "
            "(reprieve_count should be incremented by _should_kill_no_progress)"
        )
    finally:
        await _teardown_loop(loop_task, worker_key)


# ---------------------------------------------------------------------------
# Blocker r4 — out-of-band inner-task cancel must NOT tear down the worker
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_out_of_band_inner_cancel_does_not_tear_down_worker(monkeypatch, redis_test_db):
    """Cancelling the INNER SDK task (`handle.task`, simulating an
    out-of-band `_apply_recovery_transition` tool_timeout/worker_dead kill)
    tears down the "subprocess" but must NOT propagate a CancelledError that
    tears down the worker loop itself (Blocker r4). This is the scenario the
    plan's literal row-status heuristic mis-handles when the row is requeued
    to "pending" (not a TERMINAL_STATUSES member) — the
    `Task.cancelling()`-based Branch 2/3 disambiguation in
    `agent_session_queue.py` is what actually keeps this test green.
    """
    # Keep the deadline watcher from ever firing during this test — this
    # test is about the OUT-OF-BAND inner-task cancel, not Fix #3's own
    # deadline branch.
    monkeypatch.setattr(_aq, "SESSION_PROGRESS_DEADLINE_S", 3600)

    worker_key = "test-pd-04"
    session = _create_session("pd-outofband-1")
    tracking: dict = {}

    loop_task = None
    try:
        loop_task = await _run_worker_loop(
            monkeypatch, worker_key, session, _absorbing_sentinel(tracking)
        )

        # Wait for the sentinel to register its inner task on the handle.
        for _ in range(100):
            await asyncio.sleep(0.02)
            if "inner_task" in tracking:
                break
        assert "inner_task" in tracking, "Sentinel did not register its inner task in time"

        # Row transitions to "pending" — the common non-terminal requeue
        # outcome of a real out-of-band tool_timeout/worker_dead recovery —
        # BEFORE the inner-task cancel resolves (mirrors
        # _apply_recovery_transition's actual ordering: cancel+await the
        # handle task, THEN transition the row).
        transition_status(session, "pending", reason="test: simulated out-of-band recovery")

        # Simulate the out-of-band killer cancelling the INNER SDK task —
        # NOT exec_task, NOT the worker-loop task itself.
        tracking["inner_task"].cancel()

        # Give the event loop time to unwind the cancellation and propagate
        # (or, if the bug were present, tear down the worker loop).
        await asyncio.sleep(0.3)

        assert not loop_task.done(), (
            "The worker loop task must survive an out-of-band cancel of the "
            "inner SDK task — it must NOT be torn down for cleaning up one "
            "already-recovered session (Blocker r4)."
        )
        assert _active_workers.get(worker_key) is loop_task, (
            "The worker must still be registered in _active_workers after "
            "surviving the out-of-band inner-task cancel."
        )
    finally:
        await _teardown_loop(loop_task, worker_key)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_branch2_backstop_already_terminal_row_swallowed(monkeypatch, redis_test_db):
    """Branch 2 backstop: a CancelledError that bubbles in after the row is
    ALREADY terminal (an out-of-band killer fully finished, including the
    terminal transition, before its cancel's CancelledError resolves here)
    is swallowed — the worker survives and the row is not disturbed.
    """
    monkeypatch.setattr(_aq, "SESSION_PROGRESS_DEADLINE_S", 3600)

    worker_key = "test-pd-05"
    session = _create_session("pd-branch2-1")
    tracking: dict = {}

    loop_task = None
    try:
        loop_task = await _run_worker_loop(
            monkeypatch, worker_key, session, _absorbing_sentinel(tracking)
        )
        for _ in range(100):
            await asyncio.sleep(0.02)
            if "inner_task" in tracking:
                break
        assert "inner_task" in tracking

        # Row is ALREADY terminal by the time the cancel resolves.
        finalize_session(session, "abandoned", reason="test: already finalized")
        tracking["inner_task"].cancel()
        await asyncio.sleep(0.3)

        assert not loop_task.done(), (
            "Worker must survive a bubbled cancel on an already-terminal row"
        )
        fresh = _fresh(session)
        assert fresh is not None and fresh.status == "abandoned", (
            "The pre-set terminal status must not be overwritten by the swallow path"
        )
    finally:
        await _teardown_loop(loop_task, worker_key)


# ---------------------------------------------------------------------------
# Branch 3 / Blocker 1 round 2 — genuine worker shutdown tears down exec_task
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_shutdown_cancel_tears_down_exec_task(monkeypatch, redis_test_db):
    """A genuine worker-shutdown cancel (the worker-loop task itself is
    cancelled) must tear down the owned exec_task (no orphaned
    subprocess/PTY) BEFORE re-raising (Blocker 1, round 2).
    """
    monkeypatch.setattr(_aq, "SESSION_PROGRESS_DEADLINE_S", 3600)

    worker_key = "test-pd-06"
    session = _create_session("pd-shutdown-1")
    tracking: dict = {}

    loop_task = await _run_worker_loop(
        monkeypatch, worker_key, session, _tracking_hang_forever(tracking)
    )
    try:
        # Let the loop actually start executing the session.
        await asyncio.sleep(0.1)

        loop_task.cancel()
        # `_worker_loop`'s own OUTERMOST `except asyncio.CancelledError:`
        # (pre-existing, outside Fix #3's scope) logs and does NOT re-raise —
        # so the task completes normally rather than propagating
        # CancelledError to its awaiter. What Fix #3 (Branch 3) guarantees is
        # that the OWNED exec_task's underlying work is torn down (no
        # orphaned subprocess) before that re-raise reaches the outer handler.
        await asyncio.wait_for(loop_task, timeout=2.0)

        assert tracking.get("cancelled") is True, (
            "The owned exec_task's underlying work must be cancelled (no "
            "orphaned subprocess) before the worker-shutdown re-raise."
        )
    finally:
        _active_workers.pop(worker_key, None)
        _active_events.pop(worker_key, None)


# ---------------------------------------------------------------------------
# Concern 1 — declined recovery still forces the row terminal
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_declined_recovery_forces_terminal_status(monkeypatch, redis_test_db):
    """When `_apply_recovery_transition` declines (returns False, e.g.
    MAX_RECOVERY_ATTEMPTS / OOM-defer), Fix #3 must still force the row
    terminal via `transition_status(..., "cancelled")` and skip the outer
    finally (`finalized_by_execute=True`).
    """
    worker_key = "test-pd-07"
    session = _create_session(
        "pd-declined-1",
        last_tool_use_at=None,
        last_turn_at=None,
    )

    declined_mock = AsyncMock(return_value=False)

    loop_task = None
    try:
        with patch.object(_aq, "_apply_recovery_transition", declined_mock):
            loop_task = await _run_worker_loop(monkeypatch, worker_key, session, _hang_forever)

            for _ in range(200):
                await asyncio.sleep(0.02)
                if declined_mock.call_count:
                    break

        assert declined_mock.call_count >= 1
        # Give the forced transition_status call a moment to land.
        for _ in range(50):
            fresh = _fresh(session)
            if fresh is not None and fresh.status == "cancelled":
                break
            await asyncio.sleep(0.02)

        fresh = _fresh(session)
        _status = getattr(fresh, "status", None)
        assert fresh is not None and fresh.status == "cancelled", (
            f"Expected the row forced to 'cancelled' on decline; got status={_status!r}"
        )
    finally:
        await _teardown_loop(loop_task, worker_key)


# ---------------------------------------------------------------------------
# Post-cutover guard (#1924) — the PTY progress/kill seams stay deleted
# ---------------------------------------------------------------------------


def test_pty_deadline_seams_stay_deleted():
    """The transport-aware PTY progress signal and the pool-targeted kill seam
    must not resurface in the worker queue module (names checked as strings
    intentionally — #1924 one-way cutover)."""
    import agent.agent_session_queue as aq

    src_names = dir(aq)
    for gone in ("PTYPool", "kill_orphans", "_pty_quiescent_long_enough"):
        assert gone not in src_names, f"agent_session_queue.{gone} resurfaced post-cutover"

    for gone_field in ("last_pty_activity_at", "last_pty_read_loop_at", "dev_pid"):
        assert not hasattr(AgentSession, gone_field), (
            f"AgentSession.{gone_field} resurfaced post-cutover"
        )
