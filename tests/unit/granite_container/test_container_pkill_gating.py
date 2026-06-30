"""Tests for Container's process-group teardown replacing the deleted pkill fallback.

`_run_pkill_fallback` (which ran `pkill -f "claude --permission-mode
bypassPermissions"` — a machine-wide kill that could hit bystander
processes) has been deleted. Teardown now uses `os.killpg` scoped to
each PTY's own process group, which cannot affect processes outside
that group.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
import unittest
from unittest.mock import MagicMock, patch

from agent.granite_container.container import Container


def _mock_pty(
    idle_buffer: str = "[/complete] done\nbypass permissions\n❯ ",
    session_id: str = "mock-session-00000000",
    pid: int | None = 12345,
    alive: bool = True,
) -> MagicMock:
    pty = MagicMock()
    result = MagicMock()
    result.saw_idle = True
    result.buffer = idle_buffer
    # Per-turn capture (level-triggered idle, PR #1612): the container
    # classifies `turn_buffer or buffer`, so mirror the buffer here.
    result.turn_buffer = idle_buffer
    result.idle_marker = "bypass permissions"
    result.elapsed_ms = 1
    pty.read_until_idle.return_value = result
    pty.isalive.return_value = alive
    pty.last_resume_uuid.return_value = None
    # Session ID needed so _transcript_path() returns non-None, enabling
    # last_assistant_text to be called via the container's zero-LLM path.
    pty._session_id = session_id
    # Expose pid as an attribute (mirrors PtyDriver.pid property)
    pty.pid = pid
    return pty


class TestUsesPoolPair(unittest.TestCase):
    def test_true_with_prewarmed_pair(self) -> None:
        c = Container(user_message="hi", pm_pty=_mock_pty(), dev_pty=_mock_pty())
        self.assertTrue(c._uses_pool_pair())

    def test_false_without_prewarmed_pair(self) -> None:
        c = Container(user_message="hi")
        self.assertFalse(c._uses_pool_pair())

    def test_false_with_partial_pair(self) -> None:
        c = Container(user_message="hi", pm_pty=_mock_pty())
        self.assertFalse(c._uses_pool_pair())


class TestPkillDeleted(unittest.TestCase):
    """Verify that the machine-wide pkill fallback has been removed."""

    def test_run_pkill_fallback_no_longer_exists(self) -> None:
        c = Container(user_message="hi")
        self.assertFalse(
            hasattr(c, "_run_pkill_fallback"),
            "_run_pkill_fallback must be deleted — it killed bystander processes",
        )


class _ClosablePtyStub:
    """Stub PTY mimicking pexpect's force-close: ``.pid`` returns the real
    child pid until ``close()`` is called, then ``None`` (pexpect nulls
    ``_child`` on force-close). This is the exact behaviour ``_close_pair_and_reap``
    relies on — it MUST capture the pgid BEFORE close, because afterward ``.pid``
    is ``None`` and the leader may already be dead.
    """

    def __init__(self, pid: int, released_to_pool: bool = False) -> None:
        self._pid: int | None = pid
        self._released_to_pool = released_to_pool

    @property
    def pid(self) -> int | None:
        return self._pid

    def close(self, force: bool = False) -> None:
        self._pid = None

    def isalive(self) -> bool:
        return self._pid is not None


class TestProcessGroupTeardown(unittest.TestCase):
    """`_close_pair_and_reap` reaps process groups only on the self-spawned path.

    Two real paths:
    - Pool-backed (`_uses_pool_pair()` True): killpg NEVER called — the pool
      owns its PTYs' lifecycle (close-on-release + PID-targeted orphan kill).
    - Self-spawned (`_uses_pool_pair()` False): pgid captured BEFORE close, then
      the group is SIGTERM/SIGKILLed — reaping orphaned grandchildren that
      pexpect's force-close does not signal.
    """

    def test_pool_backed_teardown_does_not_kill_pool_pair(self) -> None:
        """Pool-backed container: killpg/getpgid are NEVER called by the reap."""
        pm = _mock_pty(session_id="mock-session-pm", pid=1001)
        dev = _mock_pty(session_id="mock-session-dev", pid=2002)
        # Pass as prewarmed pair → _uses_pool_pair() returns True.
        c = Container(user_message="hi", pm_pty=pm, dev_pty=dev)
        # Mark as pool-owned so _close_pair() skips them (as BridgeAdapter does).
        pm._released_to_pool = True
        dev._released_to_pool = True
        self.assertTrue(c._uses_pool_pair())

        with (
            patch("os.getpgid", side_effect=AssertionError("getpgid must not run")),
            patch("os.killpg", side_effect=AssertionError("killpg must not run")),
        ):
            c._close_pair_and_reap()  # must not signal the pool's process groups

    def test_self_spawned_reap_kills_real_child_process_group(self) -> None:
        """Self-spawned reap kills the REAL child's process group.

        Spawns a throwaway ``sleep`` in its own session/process group, wires a
        stub PTY whose ``.pid`` is the child's pid BEFORE close and ``None``
        AFTER close (mimicking pexpect), then asserts ``_close_pair_and_reap``
        actually kills the group. If the helper captured the pgid AFTER close
        (the bug), the group would never be signalled and this would hang/fail.
        """
        child = subprocess.Popen(["sleep", "30"], preexec_fn=os.setsid)
        child_pid = child.pid
        try:
            # setsid → the child is its own process-group leader.
            self.assertEqual(os.getpgid(child_pid), child_pid)

            c = Container(user_message="hi")  # no prewarmed pair → self-spawned
            self.assertFalse(c._uses_pool_pair())
            c._pm_pty = _ClosablePtyStub(child_pid)  # type: ignore[assignment]
            c._dev_pty = None

            c._close_pair_and_reap()

            # close() nulled the stub's pid (pexpect contract).
            self.assertIsNone(c._pm_pty.pid)

            # The child's process group must have been killed.
            deadline = time.time() + 5.0
            while time.time() < deadline and child.poll() is None:
                time.sleep(0.05)
            self.assertIsNotNone(child.poll(), "self-spawned child's process group must be reaped")
        finally:
            try:
                os.killpg(child_pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                child.kill()
            except OSError:
                pass
            try:
                child.wait(timeout=5)
            except Exception:
                pass

    def test_dead_pty_getpgid_failure_is_graceful(self) -> None:
        """getpgid raising (process already dead) does not propagate; no killpg."""
        c = Container(user_message="hi")  # self-spawned
        c._pm_pty = _ClosablePtyStub(7001)  # type: ignore[assignment]
        c._dev_pty = None

        with (
            patch("os.getpgid", side_effect=ProcessLookupError("already dead")),
            patch("os.killpg") as mock_killpg,
        ):
            c._close_pair_and_reap()  # must not raise

        mock_killpg.assert_not_called()


class TestSpawnFailureTeardown(unittest.TestCase):
    """Spawn-failure path reaps partially-spawned PTYs individually."""

    def test_pm_pty_killed_when_dev_pty_none(self) -> None:
        """If only _pm_pty was created before spawn failed, it is still killed."""
        c = Container(user_message="hi")
        pm = _mock_pty(pid=5001)
        c._pm_pty = pm
        c._dev_pty = None  # dev never got created

        with (
            patch("os.getpgid", return_value=15001) as mock_getpgid,
            patch("os.killpg") as mock_killpg,
            patch.object(c, "_spawn_pair", side_effect=RuntimeError("boom")),
        ):
            result = c.run()

        self.assertEqual(result.exit_reason, "exception")
        mock_getpgid.assert_called_once_with(5001)
        mock_killpg.assert_any_call(15001, signal.SIGTERM)

    def test_dev_pty_killed_when_pm_pty_none(self) -> None:
        """If only _dev_pty was created before spawn failed, it is still killed."""
        c = Container(user_message="hi")
        c._pm_pty = None
        dev = _mock_pty(pid=6002)
        c._dev_pty = dev

        with (
            patch("os.getpgid", return_value=16002) as mock_getpgid,
            patch("os.killpg") as mock_killpg,
            patch.object(c, "_spawn_pair", side_effect=RuntimeError("boom")),
        ):
            result = c.run()

        self.assertEqual(result.exit_reason, "exception")
        mock_getpgid.assert_called_once_with(6002)
        mock_killpg.assert_any_call(16002, signal.SIGTERM)

    def test_both_ptys_killed_on_spawn_failure(self) -> None:
        """When both PTYs exist at spawn failure, both are independently killed."""
        c = Container(user_message="hi")
        pm = _mock_pty(pid=7001)
        dev = _mock_pty(pid=8002)
        c._pm_pty = pm
        c._dev_pty = dev

        def _fake_getpgid(pid: int) -> int:
            return pid + 10000

        with (
            patch("os.getpgid", side_effect=_fake_getpgid),
            patch("os.killpg") as mock_killpg,
            patch.object(c, "_spawn_pair", side_effect=RuntimeError("boom")),
        ):
            result = c.run()

        self.assertEqual(result.exit_reason, "exception")
        mock_killpg.assert_any_call(17001, signal.SIGTERM)
        mock_killpg.assert_any_call(18002, signal.SIGTERM)


if __name__ == "__main__":
    unittest.main(verbosity=2)
