"""Integration (issue #1938): the runner teardown reap kills a REAL detached
process group, and the recovery-path confirm verifies exit against a REAL live
process — no ENOENT-wedged survivor, no ghost pipeline.

Unlike the unit tests (which inject fake ``killpg``/``getpgid`` seams), these
spawn genuine ``sleep`` children in their own process group
(``start_new_session=True``) and exercise the DEFAULT ``os`` signal path end to
end:

* AC#1/AC#3 (runner): external cancellation of the run task makes
  ``_run_one_turn``'s ``finally`` SIGKILL + confirm the child's process group,
  and the real child is dead afterward (``os.getpgid`` raises
  ``ProcessLookupError``) — no live ``claude -p`` orphans to the worker.
* AC#1 (recovery): ``_confirm_subprocess_dead`` against a real live group
  SIGKILLs it and returns ``confirmed_dead=True`` (the requeue gate); against an
  already-exited pid it returns confirmed-dead without signalling.

Every spawned child is reaped in a ``finally`` even if an assertion fails.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import threading
import time

import pytest

from agent import session_health
from agent.session_runner.adapter import SessionRunnerAdapter
from agent.session_runner.role_driver import HeadlessTurnOutcome
from agent.session_runner.runner import SessionRunner
from tests.unit.session_runner.test_runner_turns import FakeSession


def _spawn_group_child() -> subprocess.Popen:
    """Spawn a long-lived ``sleep`` in its OWN session/process group."""
    return subprocess.Popen(["sleep", "300"], start_new_session=True)


def _start_reaper(proc: subprocess.Popen) -> threading.Thread:
    """Background-reap the child once it dies, mirroring production.

    In production the killed ``claude -p`` is reaped by asyncio's subprocess
    transport (its SIGCHLD watcher); without an equivalent reaper here the
    SIGKILLed child lingers as a zombie and ``killpg(pgid, 0)`` keeps reporting
    the group alive. A daemon thread blocking on ``proc.wait()`` supplies that
    reaping so the liveness probe sees the group disappear.
    """
    t = threading.Thread(target=lambda: proc.wait(), daemon=True)
    t.start()
    return t


def _group_alive(pgid: int) -> bool:
    """True iff the process group still exists (signal 0 probe)."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _reap(proc: subprocess.Popen) -> None:
    """Best-effort cleanup for a test-spawned child (idempotent)."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    with contextlib.suppress(Exception):
        proc.wait(timeout=2)


class _RealChildDriver:
    """Driver that spawns a REAL child, records its pid on the runner handle,
    signals ``spawned``, then hangs until the run task is torn down."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self.runner: SessionRunner | None = None
        self.spawned = asyncio.Event()

    def attach(self, runner: SessionRunner) -> None:
        self.runner = runner

    async def run_turn(self, message: str) -> HeadlessTurnOutcome:
        if self.runner is not None:
            self.runner._on_turn_spawn(self.proc.pid)
        self.spawned.set()
        await asyncio.sleep(3600)
        return HeadlessTurnOutcome(reply_text="[/user]\ndone", turn_ended=True)


def _make_runner(driver):
    session = FakeSession()
    adapter = SessionRunnerAdapter(
        session, "test-proj", "telegram", resolve_callbacks=lambda pk, t: (lambda *a: None, None)
    )
    # No killpg_fn/kill_fn injection: use the DEFAULT os signal seams so this is
    # a genuine end-to-end reap of a real process group.
    runner = SessionRunner(
        agent_session=session,
        adapter=adapter,
        working_dir="/tmp/wd",
        driver=driver,
        steering_pop_fn=lambda: [],
        steer_poll_interval_s=0.05,
    )
    driver.attach(runner)
    return runner, session


@pytest.mark.asyncio
async def test_runner_finally_reaps_real_process_group_on_cancel():
    """External cancel → ``_run_one_turn`` finally SIGKILLs the real child group;
    the child is genuinely dead afterward (no orphan survivor)."""
    proc = _spawn_group_child()
    pgid = os.getpgid(proc.pid)
    try:
        assert _group_alive(pgid) is True
        _start_reaper(proc)  # mirror the asyncio transport reaping the killed child
        driver = _RealChildDriver(proc)
        runner, session = _make_runner(driver)

        task = asyncio.create_task(runner.run("task"))
        await asyncio.wait_for(driver.spawned.wait(), timeout=5)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        # The runner's synchronous SIGKILL+confirm in the finally already reaped
        # the group before the run task resolved.
        deadline = time.monotonic() + 3.0
        while _group_alive(pgid) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert _group_alive(pgid) is False
        # Real child exited (killed by SIGKILL).
        proc.wait(timeout=3)
        assert proc.returncode is not None
        # No reap-failed marker (the real group died within the confirm cap).
        assert not any(e["type"] == "runner_reap_failed" for e in (session.session_events or []))
        # claude_pid was cleared on turn exit → recovery reads None between turns.
        assert getattr(session, "claude_pid", "unset") is None
    finally:
        _reap(proc)


def test_confirm_subprocess_dead_kills_real_live_group():
    """AC#1 recovery gate: the confirm SIGKILLs a real live group and returns
    confirmed_dead=True (so the recovery path may requeue only after real exit)."""
    proc = _spawn_group_child()
    pgid = os.getpgid(proc.pid)
    try:
        assert _group_alive(pgid) is True
        _start_reaper(proc)  # mirror the asyncio transport reaping the killed child
        result = session_health._confirm_subprocess_dead(proc.pid, timeout=3.0)
        assert result.confirmed_dead is True
        assert result.signal_sent is True
        # The real group is gone.
        deadline = time.monotonic() + 3.0
        while _group_alive(pgid) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert _group_alive(pgid) is False
    finally:
        _reap(proc)


def test_confirm_subprocess_dead_already_exited_no_signal():
    """An already-exited pid → confirmed dead without a signal (cancel sufficed)."""
    proc = _spawn_group_child()
    pgid = os.getpgid(proc.pid)
    # Kill it ourselves and reap, so the pid/group is genuinely gone.
    os.killpg(pgid, signal.SIGKILL)
    proc.wait(timeout=3)
    # Give the OS a moment to tear the group down.
    deadline = time.monotonic() + 3.0
    while _group_alive(pgid) and time.monotonic() < deadline:
        time.sleep(0.02)

    result = session_health._confirm_subprocess_dead(proc.pid, timeout=3.0)
    assert result.confirmed_dead is True
    assert result.signal_sent is False
