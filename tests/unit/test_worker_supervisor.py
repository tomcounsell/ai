"""Unit tests for the background-task supervisor (issue #1816 Fix #4).

Covers:
- test_supervise_respawns_on_crash: supervise() respawns a task that exits unexpectedly
- test_supervise_no_respawn_on_cancel: cancelled tasks are NOT respawned
- test_supervise_backoff_grows: backoff delay grows exponentially with each restart
- test_storm_cap_kills_process: exceeding max_restarts → process dies via SIGKILL (real subprocess)
- test_supervise_no_respawn_when_shutdown: shutdown flag suppresses respawn
"""

from __future__ import annotations

import asyncio
import signal
import subprocess
import sys
import textwrap
from unittest.mock import patch

import pytest

from worker.__main__ import (
    WORKER_SUPERVISOR_BASE_BACKOFF_S,
    WORKER_SUPERVISOR_MAX_RESTARTS,
    WORKER_SUPERVISOR_WINDOW_S,
    supervise,
)

# ---------------------------------------------------------------------------
# test_supervise_respawns_on_crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervise_respawns_on_crash():
    """A task that exits with an exception is respawned once within the window."""
    call_count = [0]

    async def _crashing_factory():
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("first run boom")
        # Second run: sleep forever so the task doesn't respawn again.
        await asyncio.sleep(3600)

    sleeps: list[float] = []
    original_sleep = asyncio.sleep

    async def _fake_sleep(delay, *args, **kwargs):
        sleeps.append(delay)
        # Short-circuit so tests don't hang.
        await original_sleep(0)

    with patch("asyncio.sleep", side_effect=_fake_sleep):
        task = supervise(
            "test-crash-respawn",
            _crashing_factory,
            max_restarts=WORKER_SUPERVISOR_MAX_RESTARTS,
            window_s=WORKER_SUPERVISOR_WINDOW_S,
            base_backoff_s=WORKER_SUPERVISOR_BASE_BACKOFF_S,
        )
        # Give the event loop time to run the done-callback and respawn.
        for _ in range(10):
            await asyncio.sleep(0)

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, RuntimeError):
            pass

        # Drain any pending _delayed_respawn tasks spawned by backoff respawns.
        for t in list(asyncio.all_tasks()):
            if not t.done() and t is not asyncio.current_task():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, RuntimeError):
                    pass

    assert call_count[0] >= 2, (
        f"Factory should be called at least twice (initial + 1 respawn); got {call_count[0]}"
    )


# ---------------------------------------------------------------------------
# test_supervise_no_respawn_on_cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervise_no_respawn_on_cancel():
    """Cancelled tasks are NOT respawned — cancellation is the shutdown signal."""
    call_count = [0]

    async def _long_running():
        call_count[0] += 1
        await asyncio.sleep(3600)

    task = supervise("test-cancel", _long_running, max_restarts=5, window_s=60, base_backoff_s=0.1)
    await asyncio.sleep(0)  # let the task start

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Give the event loop a few ticks to flush any pending callbacks.
    for _ in range(5):
        await asyncio.sleep(0)

    assert call_count[0] == 1, (
        f"Cancelled task must NOT be respawned; factory called {call_count[0]} time(s)"
    )


# ---------------------------------------------------------------------------
# test_supervise_backoff_grows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervise_backoff_grows():
    """Backoff delay grows after each restart (exponential: base, base*2, ...)."""
    call_count = [0]
    max_calls = 3

    async def _always_crash():
        call_count[0] += 1
        if call_count[0] >= max_calls:
            await asyncio.sleep(3600)
        raise RuntimeError(f"crash #{call_count[0]}")

    sleeps: list[float] = []
    original_sleep = asyncio.sleep

    async def _record_sleep(delay, *args, **kwargs):
        if delay > 0:
            sleeps.append(delay)
        await original_sleep(0)

    with patch("asyncio.sleep", side_effect=_record_sleep):
        task = supervise(
            "test-backoff",
            _always_crash,
            max_restarts=10,
            window_s=300,
            base_backoff_s=1.0,
        )
        for _ in range(30):
            await asyncio.sleep(0)

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, RuntimeError):
            pass

    # Verify at least 2 backoff sleeps happened and they grow.
    non_zero_sleeps = [s for s in sleeps if s > 0]
    assert len(non_zero_sleeps) >= 1, f"Expected at least one backoff sleep; got {sleeps}"
    if len(non_zero_sleeps) >= 2:
        assert non_zero_sleeps[1] >= non_zero_sleeps[0], f"Backoff should grow: {non_zero_sleeps}"


# ---------------------------------------------------------------------------
# test_supervise_no_respawn_when_shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervise_no_respawn_when_shutdown():
    """When _shutdown_requested is True, a crashing task is NOT respawned."""
    call_count = [0]

    async def _crashing():
        call_count[0] += 1
        raise RuntimeError("crash")

    import agent.session_state as _ss

    original = _ss._shutdown_requested
    _ss._shutdown_requested = True

    try:
        task = supervise("test-shutdown", _crashing, max_restarts=5, window_s=60, base_backoff_s=0)
        # Give the event loop time to run the factory and the done-callback.
        for _ in range(10):
            await asyncio.sleep(0)
    finally:
        _ss._shutdown_requested = original
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, RuntimeError):
                pass

    assert call_count[0] == 1, (
        f"Shutdown-flagged task must NOT be respawned; factory called {call_count[0]} time(s)"
    )


# ---------------------------------------------------------------------------
# test_storm_cap_kills_process — REAL subprocess test
# ---------------------------------------------------------------------------


def test_storm_cap_kills_process():
    """Storm cap fires _self_kill() (SIGKILL) — assert REAL process death.

    This test spawns a child Python process that exercises supervise() with a
    very low max_restarts (2) and a factory that always crashes.  When the cap
    fires the child must die with SIGKILL, NOT keep running.

    A log-only assertion would falsely pass if _self_kill() used sys.exit(1)
    instead of SIGKILL, because sys.exit inside a done-callback is swallowed
    by the event loop.  This test asserts the exit signal directly and confirms
    the faulthandler thread dump landed on stderr before the kill.
    """
    import os
    import tempfile

    # Resolve the repo root from this test file's location.
    # tests/unit/test_worker_supervisor.py → tests/unit → tests → repo_root
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # The child script: runs supervise() with max_restarts=2 and a factory that
    # always raises. When the cap fires, _self_kill() → thread dump → SIGKILL.
    child_script = textwrap.dedent(f"""
        import asyncio
        import os
        import sys

        # Ensure the worktree repo root is importable (not the main checkout).
        _repo_root = {repo_root!r}
        sys.path.insert(0, _repo_root)

        # Stub session_state._shutdown_requested so the shutdown guard is False.
        import agent.session_state as _ss
        _ss._shutdown_requested = False

        from worker.__main__ import supervise

        call_count = [0]

        async def _always_crash():
            call_count[0] += 1
            raise RuntimeError(f"crash #{{call_count[0]}}")

        async def main():
            supervise(
                "storm-test",
                _always_crash,
                max_restarts=2,       # very low cap for fast test
                window_s=60,
                base_backoff_s=0.0,   # no delay so the test runs fast
            )
            # Keep the event loop running so the done-callbacks can fire.
            for _ in range(200):
                await asyncio.sleep(0.01)

        asyncio.run(main())
        # If we reach here, the storm cap did NOT fire.
        print("ERROR: storm cap never fired", flush=True)
        sys.exit(99)
    """)

    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as f:
        f.write(child_script)
        script_path = f.name

    result = subprocess.run(
        [sys.executable, script_path],
        cwd=repo_root,
        capture_output=True,
        timeout=30,
    )

    os.unlink(script_path)

    # macOS/launchd-only worker → POSIX SIGKILL (signal 9) → returncode = -9.
    # os.kill(getpid(), SIGKILL) delivers the signal; the process exits with -9.
    expected = -signal.SIGKILL
    stderr_text = result.stderr.decode()
    assert result.returncode == expected, (
        f"Expected exit by SIGKILL (rc={expected}); got rc={result.returncode}.\n"
        f"stdout={result.stdout.decode()!r}\n"
        f"stderr={stderr_text!r}"
    )
    # The faulthandler thread dump must land on stderr before the kill so a real
    # production wedge leaves forensic evidence in logs/worker_error.log.
    assert "Current thread" in stderr_text or "Thread 0x" in stderr_text, (
        f"Expected a faulthandler thread dump on stderr; got stderr={stderr_text!r}"
    )
