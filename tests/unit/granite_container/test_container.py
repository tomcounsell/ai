"""Tests for the container (PoC #1546).

The container's two-PTY coordination is the early risk (per the
plan's *Technical Approach*). These tests cover the unit-level
logic with mocked PTYs, plus an env-gated two-PTY ping-pong
integration test.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agent.granite_container.container import (
    Container,
    ContainerResult,
    TurnRecord,
    _make_sandbox_cwd,
    result_to_json,
)
from agent.granite_container.pty_driver import IdleResult, PTYDriver


def _idle_result(buffer_text: str = "fake buffer", saw_idle: bool = True) -> IdleResult:
    return IdleResult(
        saw_idle=saw_idle,
        buffer=buffer_text,
        idle_marker="bypass permissions on",
        elapsed_ms=100,
    )


def _mock_driver(buffer_text: str = "fake", saw_idle: bool = True) -> MagicMock:
    """Build a mock PTYDriver."""
    mock = MagicMock(spec=PTYDriver)
    mock.read_until_idle.return_value = _idle_result(buffer_text, saw_idle)
    mock.last_resume_uuid.return_value = None
    mock.isalive.return_value = True
    return mock


class TestMakeSandboxCwd(unittest.TestCase):
    """The sandbox tempdir is created under /tmp/granite-poc/."""

    def test_sandbox_under_tmp(self) -> None:
        # Use the platform's actual tempdir, not a hardcoded /tmp prefix.
        # macOS resolves tempfile.gettempdir() to /var/folders/.../T (per-user),
        # so a hardcoded /tmp assertion fails there even though the kernel
        # itself is correct.
        cwd, label = _make_sandbox_cwd()
        self.assertTrue(
            cwd.startswith(tempfile.gettempdir()),
            f"expected cwd under {tempfile.gettempdir()!r}, got {cwd!r}",
        )
        self.assertIn("granite-poc", cwd)
        self.assertTrue(label.startswith("run-"))


class TestContainerRejectsEmptyMessage(unittest.TestCase):
    """The container rejects empty user messages."""

    def test_empty(self) -> None:
        with self.assertRaises(ValueError):
            Container(user_message="")

    def test_whitespace(self) -> None:
        with self.assertRaises(ValueError):
            Container(user_message="   \n   ")


class TestContainerRunWithMockedPtys(unittest.TestCase):
    """End-to-end run with mocked PTYs and mocked classifier.

    Exercises the steady-state loop's classification + routing
    path without spawning a real TUI. The mock PTY always returns
    the same buffer on `read_until_idle`; the test patches the
    classifier to return a deterministic routing decision.
    """

    def _build_mock_pair(self, buffer_text: str) -> tuple[MagicMock, MagicMock]:
        pm_mock = _mock_driver(buffer_text)
        dev_mock = _mock_driver(buffer_text)
        return pm_mock, dev_mock

    def test_classify_complete_exits_loop(self) -> None:
        """PM emits [/complete] -> container exits with pm_complete."""
        c = Container(user_message="hello", max_turns=3)
        pm_mock, dev_mock = self._build_mock_pair("")

        # The tests patch _prime_session so the prime's
        # read_until_idle is a no-op. The startup phase calls
        # _cycle_idle(pm, min=0) once (both PTYs idle, no startup
        # event -> break). Then steady-state calls _cycle_idle(pm)
        # for the first turn. So PM has 2 read_until_idle calls.
        pm_idle_buffers = iter(
            [
                _idle_result("", saw_idle=True),  # startup
                _idle_result("[/complete]\nShipped PR #42.", saw_idle=True),  # steady-state
            ]
        )
        pm_mock.read_until_idle.side_effect = lambda **kw: next(pm_idle_buffers)
        # Dev is idle for all reads.
        dev_mock.read_until_idle.return_value = _idle_result("", saw_idle=True)

        with (
            patch.object(c, "_spawn_pair") as spawn,
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
        ):
            spawn.return_value = None
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        self.assertEqual(result.exit_reason, "pm_complete")
        self.assertEqual(len(result.turns), 1)
        self.assertEqual(result.turns[0].classification, "complete")

    def test_classify_dev_routes_to_dev(self) -> None:
        """PM emits [/dev] with a payload -> container routes to Dev and summarizes."""
        c = Container(user_message="hello", max_turns=3)
        pm_mock, dev_mock = self._build_mock_pair("")

        # PM reads: 1 startup, then 2 per dev-turn (steady-state
        # PM read + await PM idle for summary write). For 3
        # max_turns (all dev), 1 + 2*3 = 7 PM reads total.
        pm_idle_buffers = iter(
            [
                _idle_result("", saw_idle=True),  # startup
                _idle_result("[/dev]\nturn 0", saw_idle=True),  # turn 0 steady-state
                _idle_result("", saw_idle=True),  # turn 0 await PM idle
                _idle_result("[/dev]\nturn 1", saw_idle=True),  # turn 1
                _idle_result("", saw_idle=True),  # turn 1 await
                _idle_result("[/dev]\nturn 2", saw_idle=True),  # turn 2
                _idle_result("", saw_idle=True),  # turn 2 await
            ]
        )
        pm_mock.read_until_idle.side_effect = lambda **kw: next(pm_idle_buffers)
        dev_mock.read_until_idle.return_value = _idle_result(
            "I added foo to bar.py and ran tests.", saw_idle=True
        )

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
            patch("agent.granite_container.container.extract_dev_prompt") as extract,
            patch("agent.granite_container.container.summarize_for_pm") as summarize,
        ):
            extract.return_value = "add foo to bar.py"
            summarize.return_value = "Dev added foo to bar.py and ran tests."
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        self.assertEqual(
            result.exit_reason, "pm_max_turns", f"got {result.exit_reason}: {result.exit_message}"
        )
        self.assertEqual(len(result.turns), 3)
        # All three turns were dev-routed.
        for t in result.turns:
            self.assertEqual(t.classification, "dev")
        # Dev's PTY was written to.
        dev_mock.write.assert_called()
        # PM's PTY was written to (the summaries).
        pm_mock.write.assert_called()

    def test_classify_unknown_compliance_miss_continues(self) -> None:
        """PM emits text without a prefix token -> compliance miss, loop continues."""
        c = Container(user_message="hello", max_turns=2)
        pm_mock, dev_mock = self._build_mock_pair("")

        pm_idle_buffers = iter(
            [
                _idle_result("", saw_idle=True),  # startup
                _idle_result("I'm thinking out loud about the design.", saw_idle=True),  # no prefix
                _idle_result("[/complete]\nDone.", saw_idle=True),  # exit
            ]
        )
        pm_mock.read_until_idle.side_effect = lambda **kw: next(pm_idle_buffers)
        dev_mock.read_until_idle.return_value = _idle_result("", saw_idle=True)

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        self.assertEqual(
            result.exit_reason, "pm_complete", f"got {result.exit_reason}: {result.exit_message}"
        )
        self.assertEqual(len(result.turns), 2)
        self.assertEqual(result.turns[0].classification, "unknown")
        self.assertTrue(result.turns[0].compliance_miss)
        # The compliance miss was counted.
        self.assertEqual(result.classification_compliance_misses, 1)
        # The unknown turn re-prompts PM with a corrective nudge so
        # the loop sees fresh output instead of spinning on the same
        # non-compliant buffer until max_turns.
        from agent.granite_container.container import PM_COMPLIANCE_NUDGE

        pm_mock.write.assert_any_call(PM_COMPLIANCE_NUDGE)


class TestContainerUserAddress(unittest.TestCase):
    """A [/user] turn is terminal for a PoC invocation (no bridge).

    With no user to relay to and no user reply to re-prompt PM with,
    the container exits on pm_user after the first [/user] turn rather
    than looping back to re-read an idle PM and reclassify the same
    buffer until max_turns (the spin bug the review flagged).
    """

    def test_user_address_exits_with_pm_user(self) -> None:
        c = Container(user_message="hello", max_turns=3)
        pm_mock, dev_mock = _mock_driver(""), _mock_driver("")

        # PM reads: startup, then a single [/user] steady-state turn.
        buffers = [
            _idle_result("", saw_idle=True),  # startup
            _idle_result("[/user]\nstatus update 1", saw_idle=True),  # turn 0
            # A second [/user] buffer is provided to prove the loop
            # does NOT consume it — the container must exit after the
            # first [/user] turn.
            _idle_result("[/user]\nstatus update 2", saw_idle=True),
        ]
        pm_mock.read_until_idle.side_effect = lambda **kw: buffers.pop(0)
        dev_mock.read_until_idle.return_value = _idle_result("", saw_idle=True)

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        self.assertEqual(
            result.exit_reason, "pm_user", f"got {result.exit_reason}: {result.exit_message}"
        )
        self.assertEqual(result.exit_message, "status update 1")
        # Exactly one user-address turn was recorded — the loop did
        # not burn additional turns re-reading the idle PM.
        user_turns = [t for t in result.turns if t.classification == "user"]
        self.assertEqual(len(user_turns), 1)
        # The second [/user] buffer was never consumed.
        self.assertEqual(len(buffers), 1)


class TestContainerMaxTurns(unittest.TestCase):
    """The max_turns safety cap fires when PM never emits [/complete].

    A genuinely turn-consuming path (repeated [/dev] routing) runs the
    cap down; [/user] and [/complete] are terminal and exercised
    elsewhere.
    """

    def test_max_turns_exits_with_pm_max_turns(self) -> None:
        c = Container(user_message="hello", max_turns=2)
        pm_mock, dev_mock = _mock_driver(""), _mock_driver("")

        # PM reads: 1 startup, then 2 per dev-turn (steady-state read
        # + await PM idle for the summary write). For 2 max_turns all
        # dev-routed, 1 + 2*2 = 5 PM reads total.
        buffers = [
            _idle_result("", saw_idle=True),  # startup
            _idle_result("[/dev]\nbuild turn 0", saw_idle=True),  # turn 0
            _idle_result("", saw_idle=True),  # turn 0 await PM idle
            _idle_result("[/dev]\nbuild turn 1", saw_idle=True),  # turn 1
            _idle_result("", saw_idle=True),  # turn 1 await PM idle
        ]
        pm_mock.read_until_idle.side_effect = lambda **kw: buffers.pop(0)
        dev_mock.read_until_idle.return_value = _idle_result("Dev did the work.", saw_idle=True)

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
            patch("agent.granite_container.container.extract_dev_prompt") as extract,
            patch("agent.granite_container.container.summarize_for_pm") as summarize,
        ):
            extract.return_value = "do the work"
            summarize.return_value = "Dev did the work."
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        self.assertEqual(
            result.exit_reason, "pm_max_turns", f"got {result.exit_reason}: {result.exit_message}"
        )
        # Two dev-routed turns, both counted.
        dev_turns = [t for t in result.turns if t.classification == "dev"]
        self.assertEqual(len(dev_turns), 2)


class TestContainerStartupUnresolved(unittest.TestCase):
    """Startup phase exhausts all cycles without settling -> startup_unresolved."""

    def test_startup_unresolved_exits_early(self) -> None:
        # Neither PTY ever reaches idle during the startup window, so the
        # for-loop's else-branch fires and the container exits before
        # entering the steady-state loop.
        from agent.granite_container.container import STARTUP_WINDOW_CYCLES

        c = Container(user_message="hello", max_turns=5)
        pm_mock, dev_mock = _mock_driver("", saw_idle=False), _mock_driver("", saw_idle=False)

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        self.assertEqual(
            result.exit_reason,
            "startup_unresolved",
            f"got {result.exit_reason!r}: {result.exit_message!r}",
        )
        self.assertIn(str(STARTUP_WINDOW_CYCLES), result.exit_message)
        # No steady-state turns should have been recorded.
        self.assertEqual(len(result.turns), 0)


class TestContainerHang(unittest.TestCase):
    """PTY hang is treated as pm_hang / dev_hang exit reason."""

    def test_pm_hang_exits(self) -> None:
        c = Container(user_message="hello", max_turns=3)
        pm_mock, dev_mock = _mock_driver(""), _mock_driver("")

        # The startup phase must see both PTYs idle (saw_idle=True) to
        # break out and enter the steady-state loop. Return idle=True
        # for the startup read (min_content_bytes=0 path), then hang
        # (saw_idle=False) for the first steady-state PM read.
        startup_idle = _idle_result("", saw_idle=True)
        hang_result = _idle_result("", saw_idle=False)
        pm_buffers = iter([startup_idle, hang_result])
        pm_mock.read_until_idle.side_effect = lambda **kw: next(pm_buffers)
        dev_mock.read_until_idle.return_value = _idle_result("", saw_idle=True)

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        self.assertEqual(result.exit_reason, "pm_hang")


class TestContainerResultSerialization(unittest.TestCase):
    """The result JSON shape is stable for the results doc."""

    def test_to_json(self) -> None:
        result = ContainerResult(
            session_id="abc123",
            user_message="hello",
            turns=[
                TurnRecord(
                    turn_index=0,
                    pm_idle_ms=100,
                    dev_idle_ms=200,
                    classification="dev",
                    compliance_miss=False,
                    pm_first_line="[/dev]",
                    routed_payload_chars=42,
                    granite_extract_ms=50,
                    granite_summarize_ms=30,
                    pm_idle_marker="bypass permissions",
                    dev_idle_marker="bypass permissions",
                ),
            ],
            exit_reason="pm_max_turns",
            exit_message="reached max_turns=1",
        )
        s = result_to_json(result)
        d = json.loads(s)
        self.assertEqual(d["session_id"], "abc123")
        self.assertEqual(d["exit_reason"], "pm_max_turns")
        self.assertEqual(len(d["turns"]), 1)
        self.assertEqual(d["turns"][0]["classification"], "dev")


if __name__ == "__main__":
    unittest.main(verbosity=2)
