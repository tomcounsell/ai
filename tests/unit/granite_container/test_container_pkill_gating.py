"""Tests for Container's process-group teardown replacing the deleted pkill fallback.

`_run_pkill_fallback` (which ran `pkill -f "claude --permission-mode
bypassPermissions"` — a machine-wide kill that could hit bystander
processes) has been deleted. Teardown now uses `os.killpg` scoped to
each PTY's own process group, which cannot affect processes outside
that group.
"""

from __future__ import annotations

import signal
import unittest
from unittest.mock import MagicMock, patch

from agent.granite_container.container import Container


def _set_ptys(c: Container, pm: MagicMock, dev: MagicMock) -> None:
    """Side-effect for patching _spawn_pair on a self-spawned Container."""
    c._pm_pty = pm
    c._dev_pty = dev


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


class TestProcessGroupTeardown(unittest.TestCase):
    """Teardown uses os.killpg scoped to each PTY's process group.

    Two real paths:
    - Pool-backed: _uses_pool_pair() → True → killpg never called (pool owns lifecycle)
    - Self-spawned: pids captured before _close_pair() → killpg called after close
    """

    def test_pool_backed_teardown_does_not_kill_pool_pair(self) -> None:
        """Pool-backed container: killpg is NEVER called — the pool owns its PTYs.

        Directly asserts the bystander-survives acceptance criterion:
        the pool's prewarmed pair is not signalled during teardown.
        """
        pm = _mock_pty(session_id="mock-session-pm", pid=1001)
        dev = _mock_pty(session_id="mock-session-dev", pid=2002)
        # Pass as prewarmed pair → _uses_pool_pair() returns True.
        c = Container(user_message="hi", pm_pty=pm, dev_pty=dev)
        # Mark as pool-owned so _close_pair() skips them (as BridgeAdapter does).
        pm._released_to_pool = True
        dev._released_to_pool = True

        def _lat_stub(path, *, baseline_text_count=None):
            if not path or "mock-session-dev" in path:
                return ""
            return "[/complete]\nDone."

        with (
            patch("agent.granite_container.container.last_assistant_text", side_effect=_lat_stub),
            patch("os.getpgid", side_effect=lambda pid: pid + 10000),
            patch("os.killpg") as mock_killpg,
        ):
            result = c.run()

        self.assertEqual(result.exit_reason, "pm_complete")
        # CRITICAL: pool pair must not be killed.
        mock_killpg.assert_not_called()

    def test_self_spawned_teardown_calls_killpg_for_each_pty(self) -> None:
        """Self-spawned container: pids captured before _close_pair → killpg IS called.

        Asserts the orphan-reap acceptance criterion:
        the self-spawned container's process groups are signalled after teardown.
        """
        pm = _mock_pty(session_id="mock-session-pm", pid=3001)
        dev = _mock_pty(session_id="mock-session-dev", pid=4002)
        # No prewarmed pair → _uses_pool_pair() returns False.
        c = Container(user_message="hi")

        def _lat_stub(path, *, baseline_text_count=None):
            if not path or "mock-session-dev" in path:
                return ""
            return "[/complete]\nDone."

        with (
            patch.object(c, "_spawn_pair", side_effect=lambda: _set_ptys(c, pm, dev)),
            patch("agent.granite_container.container.last_assistant_text", side_effect=_lat_stub),
            patch("os.getpgid", side_effect=lambda pid: pid + 10000) as mock_getpgid,
            patch("os.killpg") as mock_killpg,
        ):
            result = c.run()

        self.assertEqual(result.exit_reason, "pm_complete")
        # Pids were pre-captured → killpg fires for each PTY's pgroup.
        mock_getpgid.assert_any_call(3001)
        mock_getpgid.assert_any_call(4002)
        mock_killpg.assert_any_call(13001, signal.SIGTERM)
        mock_killpg.assert_any_call(14002, signal.SIGTERM)

    def test_bystander_process_not_affected(self) -> None:
        """A process in a different pgroup is never killed by self-spawned teardown."""
        pm = _mock_pty(session_id="mock-session-pm", pid=5001)
        dev = _mock_pty(session_id="mock-session-dev", pid=6002)
        c = Container(user_message="hi")
        bystander_pgid = 99999
        killed_pgids: list[int] = []

        def _lat_stub(path, *, baseline_text_count=None):
            if not path or "mock-session-dev" in path:
                return ""
            return "[/complete]\nDone."

        def _fake_killpg(pgid: int, sig: int) -> None:
            killed_pgids.append(pgid)

        with (
            patch.object(c, "_spawn_pair", side_effect=lambda: _set_ptys(c, pm, dev)),
            patch("agent.granite_container.container.last_assistant_text", side_effect=_lat_stub),
            patch("os.getpgid", side_effect=lambda pid: pid + 10000),
            patch("os.killpg", side_effect=_fake_killpg),
        ):
            c.run()

        self.assertNotIn(bystander_pgid, killed_pgids)
        # Only the two PTY pgroups were touched.
        self.assertTrue(all(pgid in (15001, 16002) for pgid in killed_pgids))

    def test_dead_pty_skipped_gracefully(self) -> None:
        """OSError from killpg (process already dead) does not propagate."""
        pm = _mock_pty(session_id="mock-session-pm", pid=7001)
        dev = _mock_pty(session_id="mock-session-dev", pid=8002)
        c = Container(user_message="hi")

        def _lat_stub(path, *, baseline_text_count=None):
            if not path or "mock-session-dev" in path:
                return ""
            return "[/complete]\nDone."

        with (
            patch.object(c, "_spawn_pair", side_effect=lambda: _set_ptys(c, pm, dev)),
            patch("agent.granite_container.container.last_assistant_text", side_effect=_lat_stub),
            patch("os.getpgid", side_effect=ProcessLookupError("already dead")),
            patch("os.killpg") as mock_killpg,
        ):
            result = c.run()

        self.assertEqual(result.exit_reason, "pm_complete")
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
