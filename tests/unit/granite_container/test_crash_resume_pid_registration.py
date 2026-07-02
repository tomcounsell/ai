"""Crash-resumed PTY PID registration (plan #1851, issue tracking #1851).

`Container._resume_crashed_pty` spawns a fresh `PTYDriver` OUTSIDE the
`PTYPool`'s own spawn paths, so its new OS PID was invisible to the
worker-startup orphan sweep (`_kill_orphaned_pty_pids`), letting
crash-resumed `claude` processes leak across worker crash/restart
cycles.

These tests drive `_resume_crashed_pty` against a REAL `PTYPool`
instance (via its `register_pid`/`unregister_pid` bound methods, wired
exactly as `BridgeAdapter` wires them), using a tmp registry path so
the real `data/granite_pty_pids.json` is never touched. No real
`claude` process spawns — `PTYDriver` is patched with fake drivers.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import agent.granite_container.pty_pool as pty_pool_module
from agent.granite_container.container import Container
from agent.granite_container.pty_pool import PTYPool

DEAD_PID = 40001
NEW_PID = 40002


class _FakeDeadPTY:
    """Stand-in for a crashed `PTYDriver`: exposes `_child.pid` and a
    controllable `close()` (success or raise)."""

    def __init__(self, close_raises: bool = False, pid: int = DEAD_PID) -> None:
        self.cwd = "/repo"
        self._explicit_model = "opus"
        self._extra_env = {"AGENT_SESSION_ID": "as-1"}
        self._settings_path = "/tmp/granite/hooks/dev-uuid.settings.json"
        self._resume_uuid = "99999999-8888-7777-6666-555555555555"
        self._child = SimpleNamespace(pid=pid)
        self._close_raises = close_raises

    def last_resume_uuid(self) -> str:
        return self._resume_uuid

    def close(self, force: bool = True) -> None:
        if self._close_raises:
            raise RuntimeError("close failed")


def _make_fake_new_driver(pid: int = NEW_PID, write_raises: bool = False):
    """Build a fake replacement `PTYDriver` class (patched in for
    `container.PTYDriver`). `spawn()` always sets `_child.pid`
    (mirroring a real live process); `write()` optionally raises to
    exercise the spawn-ok/write-fail path."""

    created: dict = {}

    class _FakeNewDriver:
        def __init__(self, **kwargs) -> None:
            created.update(kwargs)
            self._child = None

        def spawn(self) -> None:
            self._child = SimpleNamespace(pid=pid)
            created["spawned"] = True

        def write(self, text: str) -> None:
            if write_raises:
                raise RuntimeError("write failed")
            created["wrote"] = text

    return _FakeNewDriver, created


class TestCrashResumePidRegistration(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.registry_path = str(Path(self._tmpdir.name) / "granite_pty_pids.json")
        self.pool = PTYPool(pool_size=1, pid_registry_path=self.registry_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _container(self, on_pty_spawn=None, on_pty_despawn=None) -> Container:
        return Container(
            user_message="do the work",
            max_turns=1,
            on_pty_spawn=on_pty_spawn if on_pty_spawn is not None else self.pool.register_pid,
            on_pty_despawn=(
                on_pty_despawn if on_pty_despawn is not None else self.pool.unregister_pid
            ),
        )

    def test_resumed_pid_registered_and_persisted(self) -> None:
        """(a) The resumed PID is sweep-visible: it lands in
        `pool.get_spawned_pids()` AND the persisted JSON registry."""
        container = self._container()
        dead = _FakeDeadPTY()
        fake_driver, _created = _make_fake_new_driver()

        with patch("agent.granite_container.container.PTYDriver", fake_driver):
            new_pty = container._resume_crashed_pty(dead, "dev")

        self.assertIsNotNone(new_pty)
        self.assertIn(NEW_PID, self.pool.get_spawned_pids())
        data = json.loads(Path(self.registry_path).read_text())
        self.assertIn(NEW_PID, data["pids"])

    def test_closing_the_loop_sweep_reaps_registered_pid(self) -> None:
        """(b) Registration is not just registry membership — the
        worker-startup sweep actually consumes it. Register a synthetic
        resumed PID (an integer, not a real process), then run the real
        `_kill_orphaned_pty_pids()` sweep with `os.kill` monkeypatched
        to a collector, and assert the PID was passed to SIGKILL."""
        self.pool.register_pid(NEW_PID)
        killed_pids: list[int] = []

        def _fake_kill(pid: int, _sig: int) -> None:
            killed_pids.append(pid)

        with (
            patch.object(pty_pool_module, "DEFAULT_PID_REGISTRY_PATH", self.registry_path),
            patch("os.kill", side_effect=_fake_kill),
        ):
            killed_count = pty_pool_module._kill_orphaned_pty_pids()

        self.assertIn(NEW_PID, killed_pids)
        self.assertEqual(killed_count, 1)

    def test_despawn_kept_registered_when_close_fails(self) -> None:
        """(c) `dead_pty.close(force=True)` raising means the old
        process may still be alive: `on_pty_despawn` must NOT be
        called, so the dead PID stays registered for the sweep."""
        self.pool.register_pid(DEAD_PID)
        container = self._container()
        dead = _FakeDeadPTY(close_raises=True)
        fake_driver, _created = _make_fake_new_driver()

        with patch("agent.granite_container.container.PTYDriver", fake_driver):
            container._resume_crashed_pty(dead, "dev")

        self.assertIn(DEAD_PID, self.pool.get_spawned_pids())

    def test_despawn_drops_pid_when_close_succeeds(self) -> None:
        """(c) The close-success path drops the confirmed-dead PID."""
        self.pool.register_pid(DEAD_PID)
        container = self._container()
        dead = _FakeDeadPTY(close_raises=False)
        fake_driver, _created = _make_fake_new_driver()

        with patch("agent.granite_container.container.PTYDriver", fake_driver):
            container._resume_crashed_pty(dead, "dev")

        self.assertNotIn(DEAD_PID, self.pool.get_spawned_pids())

    def test_close_ok_write_fail_still_drops_dead_pid(self) -> None:
        """(e) Round-2 Concern-2 fix: close succeeds, then `write()`
        raises (the method returns None). The confirmed-dead PID must
        STILL be dropped — the despawn call lives in the outer
        `finally`, which fires on every exit path, not just the
        successful swap."""
        self.pool.register_pid(DEAD_PID)
        container = self._container()
        dead = _FakeDeadPTY(close_raises=False)
        fake_driver, _created = _make_fake_new_driver(write_raises=True)

        with patch("agent.granite_container.container.PTYDriver", fake_driver):
            result = container._resume_crashed_pty(dead, "dev")

        self.assertIsNone(result)
        self.assertNotIn(DEAD_PID, self.pool.get_spawned_pids())

    def test_spawn_ok_write_fail_still_registers_new_pid(self) -> None:
        """(d) Round-1 BLOCKER fix: `write()` raising after a
        successful `spawn()` must not strand a live, unregistered
        `claude` process. Registration happens before `write()`."""
        container = self._container()
        dead = _FakeDeadPTY()
        fake_driver, _created = _make_fake_new_driver(write_raises=True)

        with patch("agent.granite_container.container.PTYDriver", fake_driver):
            result = container._resume_crashed_pty(dead, "dev")

        self.assertIsNone(result)
        self.assertIn(NEW_PID, self.pool.get_spawned_pids())

    def test_raising_on_pty_spawn_is_fail_silent(self) -> None:
        """(f) A raising `on_pty_spawn` must never crash the resume —
        it still returns the new PTY and clears the resume-owner
        window."""

        def _raising_on_pty_spawn(_pid: int) -> None:
            raise RuntimeError("boom")

        container = self._container(
            on_pty_spawn=_raising_on_pty_spawn, on_pty_despawn=self.pool.unregister_pid
        )
        dead = _FakeDeadPTY()
        fake_driver, _created = _make_fake_new_driver()

        with patch("agent.granite_container.container.PTYDriver", fake_driver):
            new_pty = container._resume_crashed_pty(dead, "dev")

        self.assertIsNotNone(new_pty)
        self.assertFalse(container.crash_resume_in_flight())


if __name__ == "__main__":
    unittest.main()
