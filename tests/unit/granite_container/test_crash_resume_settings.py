"""Crash-resume must reapply ``--settings`` (issue #1688, critique concern #1).

A resumed session must NOT run hookless: if the post-crash child were spawned
without ``--settings``, its turns would fire no Stop edges and the container
would silently revert to idle-guess completion — re-wedging the exact path the
hook-driven design fixes. These tests pin the contract at both layers:

- PTYDriver: ``spawn()`` with a ``resume_uuid`` still appends ``--settings``.
- Container: ``_resume_crashed_pty`` threads the dead PTY's ``_settings_path``
  into the replacement driver.

No real ``claude`` is spawned (pexpect.spawn is patched).
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from agent.granite_container.container import Container
from agent.granite_container.pty_driver import PTYDriver


def _capture_spawn_args() -> tuple[MagicMock, dict]:
    """A fake pexpect.spawn that records the argv it was called with."""
    captured: dict = {}

    def _fake_spawn(cmd, args, **kwargs):
        captured["cmd"] = cmd
        captured["args"] = list(args)
        child = MagicMock()
        child.isalive.return_value = True
        return child

    return _fake_spawn, captured


class TestResumeSpawnArgsIncludeSettings(unittest.TestCase):
    """Driver layer: --resume spawns still carry --settings."""

    def test_resume_spawn_args_include_settings(self) -> None:
        fake_spawn, captured = _capture_spawn_args()
        driver = PTYDriver(
            role="pm",
            settings_path="/tmp/granite/hooks/pm-uuid.settings.json",
            resume_uuid="11111111-2222-3333-4444-555555555555",
        )
        with patch("agent.granite_container.pty_driver.pexpect.spawn", side_effect=fake_spawn):
            driver.spawn()
        args = captured["args"]
        self.assertIn("--resume", args)
        self.assertEqual(args[args.index("--resume") + 1], "11111111-2222-3333-4444-555555555555")
        # The load-bearing assertion: the resumed child is NOT hookless.
        self.assertIn("--settings", args)
        self.assertEqual(
            args[args.index("--settings") + 1],
            "/tmp/granite/hooks/pm-uuid.settings.json",
        )

    def test_resume_spawn_omits_session_id(self) -> None:
        """--resume and --session-id are mutually exclusive; resume wins."""
        fake_spawn, captured = _capture_spawn_args()
        driver = PTYDriver(
            role="pm",
            session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            settings_path="/tmp/granite/hooks/pm-uuid.settings.json",
            resume_uuid="11111111-2222-3333-4444-555555555555",
        )
        with patch("agent.granite_container.pty_driver.pexpect.spawn", side_effect=fake_spawn):
            driver.spawn()
        args = captured["args"]
        self.assertIn("--resume", args)
        self.assertNotIn("--session-id", args)
        self.assertIn("--settings", args)


class TestContainerResumeThreadsSettingsPath(unittest.TestCase):
    """Container layer: _resume_crashed_pty carries the settings path over."""

    def _dead_pty(self) -> MagicMock:
        dead = MagicMock(spec=PTYDriver)
        dead.cwd = "/repo"
        dead._explicit_model = "opus"
        dead._extra_env = {"AGENT_SESSION_ID": "as-1"}
        dead._settings_path = "/tmp/granite/hooks/dev-uuid.settings.json"
        dead.last_resume_uuid.return_value = "99999999-8888-7777-6666-555555555555"
        return dead

    def test_resume_crashed_pty_reapplies_settings_path(self) -> None:
        container = Container(user_message="do the work", max_turns=1)
        dead = self._dead_pty()
        created: dict = {}

        class _FakeDriver:
            def __init__(self, **kwargs):
                created.update(kwargs)
                self._settings_path = kwargs.get("settings_path")

            def spawn(self):
                created["spawned"] = True

            def write(self, text):
                created["wrote"] = text

        with patch("agent.granite_container.container.PTYDriver", _FakeDriver):
            new_pty = container._resume_crashed_pty(dead, "dev")

        self.assertIsNotNone(new_pty)
        # Concern #1: the replacement child inherits the hook settings file.
        self.assertEqual(created.get("settings_path"), "/tmp/granite/hooks/dev-uuid.settings.json")
        self.assertEqual(created.get("resume_uuid"), "99999999-8888-7777-6666-555555555555")
        self.assertTrue(created.get("spawned"))
        # Practice 6 minimum: the continue nudge was submitted.
        self.assertTrue(created.get("wrote"))
        # Concern #6: the resume-owner window is closed after the re-spawn.
        self.assertFalse(container.crash_resume_in_flight())

    def test_no_resume_handle_returns_none(self) -> None:
        container = Container(user_message="do the work", max_turns=1)
        dead = self._dead_pty()
        dead.last_resume_uuid.return_value = None
        self.assertIsNone(container._resume_crashed_pty(dead, "dev"))
        self.assertFalse(container.crash_resume_in_flight())


if __name__ == "__main__":
    unittest.main()
