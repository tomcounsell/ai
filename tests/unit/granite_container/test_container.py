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


def _mock_driver(
    buffer_text: str = "fake", saw_idle: bool = True, session_id: str = "mock-session-pm"
) -> MagicMock:
    """Build a mock PTYDriver."""
    mock = MagicMock(spec=PTYDriver)
    mock.read_until_idle.return_value = _idle_result(buffer_text, saw_idle)
    mock.last_resume_uuid.return_value = None
    mock.isalive.return_value = True
    # Set _session_id so _transcript_path produces a non-None value,
    # allowing last_assistant_text to be called in the container run path.
    # PM and Dev get different session IDs so stubs can discriminate.
    mock._session_id = session_id
    return mock


def _mock_pm(buffer_text: str = "fake", saw_idle: bool = True) -> MagicMock:
    """Build a mock PM PTYDriver."""
    return _mock_driver(buffer_text, saw_idle, session_id="mock-session-pm")


def _mock_dev(buffer_text: str = "fake", saw_idle: bool = True) -> MagicMock:
    """Build a mock Dev PTYDriver."""
    return _mock_driver(buffer_text, saw_idle, session_id="mock-session-dev")


class TestMakeSandboxCwd(unittest.TestCase):
    """The sandbox tempdir is created under /tmp/granite/."""

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
        self.assertIn("granite/run-", cwd)
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
        pm_mock = _mock_pm(buffer_text)
        dev_mock = _mock_dev(buffer_text)
        return pm_mock, dev_mock

    def test_classify_complete_exits_loop(self) -> None:
        """PM emits [/complete] with a non-empty body -> container exits
        with pm_complete and user_facing_routed=True (issue #1647).

        With the prime-turn relay (issue #1644), PM reads are:
          1. startup
          2. prime-turn relay (returns [/complete] → routes to on_complete,
             sets user_facing_routed=True via the mock callback, breaks)
        The wrap-up guard does NOT fire because user_facing_routed=True.
        """
        delivered: list[str] = []

        def _on_complete(payload: str) -> None:
            delivered.append(payload)

        c = Container(
            user_message="hello",
            max_turns=3,
            on_complete_payload=_on_complete,
        )
        pm_mock, dev_mock = self._build_mock_pair("")

        # PM reads: 1 startup + 1 prime-turn relay (with [/complete]).
        # The [/complete] is now consumed at prime-turn relay, not
        # steady-state, so user_facing_routed is set by on_complete_payload.
        pm_idle_buffers = iter(
            [
                _idle_result("", saw_idle=True),  # startup
                _idle_result("[/complete]\nShipped PR #42.", saw_idle=True),  # prime-turn relay
            ]
        )
        pm_mock.read_until_idle.side_effect = lambda **kw: next(pm_idle_buffers)
        # Dev is idle for all reads.
        dev_mock.read_until_idle.return_value = _idle_result("", saw_idle=True)

        # PM transcript texts: startup (empty → _unknown_classification falls through
        # before classify), then prime-relay "[/complete]\nShipped PR #42.".
        pm_transcript_texts = iter(["[/complete]\nShipped PR #42."])

        def _lat_stub(path, *, baseline_text_count=None):
            if not path or "mock-session-dev" in path:
                return ""
            try:
                return next(pm_transcript_texts)
            except StopIteration:
                return ""

        with (
            patch.object(c, "_spawn_pair") as spawn,
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
            patch(
                "agent.granite_container.container.last_assistant_text",
                side_effect=_lat_stub,
            ),
        ):
            spawn.return_value = None
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        self.assertEqual(result.exit_reason, "pm_complete")
        self.assertEqual(len(result.turns), 1)
        self.assertEqual(result.turns[0].classification, "complete")
        # Non-empty [/complete] sets user_facing_routed=True (issue #1647).
        self.assertTrue(
            result.user_facing_routed, "expected user_facing_routed=True for non-empty [/complete]"
        )
        # The on_complete_payload callback was invoked with the payload.
        self.assertEqual(delivered, ["Shipped PR #42."])

    def test_classify_dev_routes_to_dev(self) -> None:
        """PM emits [/dev] with a payload -> container routes to Dev and summarizes.

        Buffer sequence with prime-turn relay (issue #1644):
          1. startup
          2. prime-turn relay → returns "" (unknown/compliance miss), _prime_relayed=True
          3. stale-buffer guard at turn 0 (sees "[/dev]turn 0" ≠ "" → no action)
          4. turn 0 PM read: "[/dev]\nturn 0"
          5. turn 0 await PM idle for summary write: ""
          6. turn 1: "[/dev]\nturn 1"
          7. turn 1 await: ""
          8. turn 2: "[/dev]\nturn 2"
          9. turn 2 await: ""
        _run_wrapup_guard is patched out (no user_facing callback, would fire).
        """
        c = Container(user_message="hello", max_turns=3)
        pm_mock, dev_mock = self._build_mock_pair("")

        pm_idle_buffers = iter(
            [
                _idle_result("", saw_idle=True),  # 1. startup
                _idle_result("", saw_idle=True),  # 2. prime-turn relay (unknown)
                _idle_result("[/dev]\nturn 0", saw_idle=True),  # 3. stale-buffer guard
                _idle_result("[/dev]\nturn 0", saw_idle=True),  # 4. turn 0 steady-state
                _idle_result("", saw_idle=True),  # 5. turn 0 await PM idle
                _idle_result("[/dev]\nturn 1", saw_idle=True),  # 6. turn 1
                _idle_result("", saw_idle=True),  # 7. turn 1 await
                _idle_result("[/dev]\nturn 2", saw_idle=True),  # 8. turn 2
                _idle_result("", saw_idle=True),  # 9. turn 2 await
            ]
        )
        pm_mock.read_until_idle.side_effect = lambda **kw: next(pm_idle_buffers)
        dev_mock.read_until_idle.return_value = _idle_result(
            "I added foo to bar.py and ran tests.", saw_idle=True
        )

        # last_assistant_text is called for each PM classify and each Dev read.
        # PM path contains "mock-session-pm"; Dev path contains "mock-session-dev".
        pm_transcript_texts = iter(
            [
                "",  # prime-relay (unknown, compliance miss)
                "[/dev]\nturn 0",  # turn 0 steady-state
                "[/dev]\nturn 1",  # turn 1
                "[/dev]\nturn 2",  # turn 2
            ]
        )
        dev_transcript_text = "I added foo to bar.py and ran tests."

        def _last_assistant_text_stub(path, *, baseline_text_count=None):
            if not path:
                return ""
            if "mock-session-dev" in path:
                return dev_transcript_text
            # PM transcript calls get sequential buffer texts.
            try:
                return next(pm_transcript_texts)
            except StopIteration:
                return ""

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
            patch.object(c, "_run_wrapup_guard"),  # no user_facing callback
            patch(
                "agent.granite_container.container.last_assistant_text",
                side_effect=_last_assistant_text_stub,
            ),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        self.assertEqual(
            result.exit_reason, "pm_max_turns", f"got {result.exit_reason}: {result.exit_message}"
        )
        # Prime-relay adds 1 unknown TurnRecord (compliance miss), then 3 dev
        # turns — total 4 turns.
        self.assertEqual(len(result.turns), 4)
        # First turn is the prime-relay compliance miss (unknown).
        self.assertEqual(result.turns[0].classification, "unknown")
        # Remaining three turns were dev-routed.
        for t in result.turns[1:]:
            self.assertEqual(t.classification, "dev")
        # Dev's PTY was written to.
        dev_mock.write.assert_called()
        # PM's PTY was written to (the Dev reports).
        pm_mock.write.assert_called()

    def test_classify_unknown_compliance_miss_continues(self) -> None:
        """PM emits text without a prefix token in steady state -> compliance miss, loop continues.

        Buffer sequence with prime-turn relay (issue #1644):
          1. startup
          2. prime-turn relay → [/complete]\nDone. consumed here → exits pm_complete
        Since the [/complete] is consumed at prime-turn relay, the test now
        verifies the prime-relay path. To test the steady-state compliance miss,
        see test_steady_state_compliance_miss below (uses a mock that skips
        the prime-relay path).
        """
        # To test steady-state compliance miss without coupling to the prime-
        # relay sequence, we provide a [/complete] at prime-relay and verify
        # compliance miss behavior in steady state by using a separate test.
        # This test verifies that the original compliance-miss path is still
        # reachable: prime-relay emits unknown/no-prefix → compliance nudge →
        # then steady-state emits [/complete].
        c = Container(user_message="hello", max_turns=2)
        pm_mock, dev_mock = self._build_mock_pair("")

        # Buffer sequence:
        # [0] startup (empty)
        # [1] prime-relay: "I'm thinking..." → unknown → compliance nudge
        #     _prime_relayed=True, _prime_pm_buf_hash=hash("I'm thinking...")
        # [2] stale-buffer guard at turn 0: reads "[/complete]\nDone." (hash≠prime hash)
        # [3] turn 0 normal read: "[/complete]\nDone." → exits pm_complete
        # wrap-up guard patched (no on_complete_payload callback)
        pm_idle_buffers = iter(
            [
                _idle_result("", saw_idle=True),  # startup
                _idle_result(
                    "I'm thinking out loud about the design.", saw_idle=True
                ),  # prime-relay
                _idle_result("[/complete]\nDone.", saw_idle=True),  # stale-buffer guard
                _idle_result("[/complete]\nDone.", saw_idle=True),  # turn 0 steady-state
            ]
        )
        pm_mock.read_until_idle.side_effect = lambda **kw: next(pm_idle_buffers)
        dev_mock.read_until_idle.return_value = _idle_result("", saw_idle=True)

        # PM transcript texts (one per PM classify call):
        # prime-relay → no prefix (unknown), turn 0 → [/complete]\nDone.
        pm_transcript_texts = iter(
            [
                "I'm thinking out loud about the design.",  # prime-relay: unknown
                "[/complete]\nDone.",  # turn 0: complete
            ]
        )

        def _lat_stub(path, *, baseline_text_count=None):
            if not path or "mock-session-dev" in path:
                return ""
            try:
                return next(pm_transcript_texts)
            except StopIteration:
                return ""

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
            patch.object(c, "_run_wrapup_guard"),  # no on_complete_payload callback
            patch(
                "agent.granite_container.container.last_assistant_text",
                side_effect=_lat_stub,
            ),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        self.assertEqual(
            result.exit_reason, "pm_complete", f"got {result.exit_reason}: {result.exit_message}"
        )
        # 2 turns: 1 from prime-relay (unknown) + 1 from steady-state (complete).
        self.assertEqual(len(result.turns), 2)
        self.assertEqual(result.turns[0].classification, "unknown")
        self.assertTrue(result.turns[0].compliance_miss)
        # The compliance miss from the prime-relay unknown turn was counted.
        self.assertGreaterEqual(result.classification_compliance_misses, 1)
        # The unknown turn re-prompts PM with a corrective nudge.
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
        """PM emits [/user] -> exits pm_user with user_facing_routed=True (issue #1647).

        With the prime-turn relay (issue #1644), the [/user] is consumed at
        prime-relay (turn_index=-1). The on_user_payload callback is called,
        setting user_facing_routed=True so the executor emits REACTION_COMPLETE.
        """
        delivered: list[str] = []

        def _on_user(payload: str) -> None:
            delivered.append(payload)

        c = Container(user_message="hello", max_turns=3, on_user_payload=_on_user)
        pm_mock, dev_mock = _mock_pm(""), _mock_dev("")

        # PM reads:
        # [0] startup
        # [1] prime-turn relay: [/user]\nstatus update 1 → routes to user, exits
        # A second [/user] buffer is provided to prove the loop does NOT consume
        # it — the container must exit after the first [/user] turn.
        buffers = [
            _idle_result("", saw_idle=True),  # startup
            _idle_result("[/user]\nstatus update 1", saw_idle=True),  # prime-turn relay
            _idle_result("[/user]\nstatus update 2", saw_idle=True),  # must NOT be consumed
        ]
        pm_mock.read_until_idle.side_effect = lambda **kw: buffers.pop(0)
        dev_mock.read_until_idle.return_value = _idle_result("", saw_idle=True)

        # PM transcript: only the prime-relay call matters (returns [/user] text).
        pm_transcript_texts = iter(["[/user]\nstatus update 1"])

        def _lat_stub(path, *, baseline_text_count=None):
            if not path or "mock-session-dev" in path:
                return ""
            try:
                return next(pm_transcript_texts)
            except StopIteration:
                return ""

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
            patch(
                "agent.granite_container.container.last_assistant_text",
                side_effect=_lat_stub,
            ),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        self.assertEqual(
            result.exit_reason, "pm_user", f"got {result.exit_reason}: {result.exit_message}"
        )
        self.assertEqual(result.exit_message, "status update 1")
        # Exactly one user-address turn was recorded (prime-relay).
        user_turns = [t for t in result.turns if t.classification == "user"]
        self.assertEqual(len(user_turns), 1)
        # The second [/user] buffer was never consumed.
        self.assertEqual(len(buffers), 1)
        # The on_user_payload callback was invoked → user_facing_routed=True.
        self.assertEqual(delivered, ["status update 1"])
        self.assertTrue(
            result.user_facing_routed, "expected user_facing_routed=True after [/user] delivery"
        )


class TestContainerMaxTurns(unittest.TestCase):
    """The max_turns safety cap fires when PM never emits [/complete].

    A genuinely turn-consuming path (repeated [/dev] routing) runs the
    cap down; [/user] and [/complete] are terminal and exercised
    elsewhere.
    """

    def test_max_turns_exits_with_pm_max_turns(self) -> None:
        """The max_turns safety cap fires when PM never emits [/complete].

        Buffer sequence with prime-turn relay (issue #1644):
          1. startup
          2. prime-turn relay: "" → unknown → _prime_relayed=True
          3. stale-buffer guard at turn 0: "[/dev]\nbuild turn 0"
          4. turn 0 normal read: "[/dev]\nbuild turn 0"
          5. turn 0 await PM idle: ""
          6. turn 1: "[/dev]\nbuild turn 1"
          7. turn 1 await PM idle: ""
        _run_wrapup_guard is patched out (no user_facing callback).
        """
        c = Container(user_message="hello", max_turns=2)
        pm_mock, dev_mock = _mock_pm(""), _mock_dev("")

        buffers = [
            _idle_result("", saw_idle=True),  # startup
            _idle_result("", saw_idle=True),  # prime-turn relay (unknown)
            _idle_result("[/dev]\nbuild turn 0", saw_idle=True),  # stale-buffer guard
            _idle_result("[/dev]\nbuild turn 0", saw_idle=True),  # turn 0 steady-state
            _idle_result("", saw_idle=True),  # turn 0 await PM idle
            _idle_result("[/dev]\nbuild turn 1", saw_idle=True),  # turn 1
            _idle_result("", saw_idle=True),  # turn 1 await PM idle
        ]
        pm_mock.read_until_idle.side_effect = lambda **kw: buffers.pop(0)
        dev_mock.read_until_idle.return_value = _idle_result("Dev did the work.", saw_idle=True)

        # PM transcript texts: prime-relay (unknown), turn 0, turn 1.
        # Dev transcript: verbatim dev text.
        pm_transcript_texts = iter(
            [
                "",  # prime-relay: unknown (empty → fallback)
                "[/dev]\nbuild turn 0",  # turn 0
                "[/dev]\nbuild turn 1",  # turn 1
            ]
        )

        def _lat_stub(path, *, baseline_text_count=None):
            if not path:
                return ""
            if "mock-session-dev" in path:
                return "Dev did the work."
            try:
                return next(pm_transcript_texts)
            except StopIteration:
                return ""

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
            patch.object(c, "_run_wrapup_guard"),  # patched out; tested separately
            patch(
                "agent.granite_container.container.last_assistant_text",
                side_effect=_lat_stub,
            ),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        self.assertEqual(
            result.exit_reason, "pm_max_turns", f"got {result.exit_reason}: {result.exit_message}"
        )
        # Two dev-routed turns, both counted.
        dev_turns = [t for t in result.turns if t.classification == "dev"]
        self.assertEqual(len(dev_turns), 2)


class TestContainerStartupHardCeiling(unittest.TestCase):
    """Startup phase: hard wall-clock ceiling (PR #1612 review TD2).

    The startup loop keeps polling on short reads until BOTH PTYs
    reach idle — a slow Opus high-effort persona load simply keeps
    the loop cycling cheaply. If the PTYs never settle within
    `STARTUP_HARD_CEILING_S`, the run exits `startup_unresolved`.
    That distinct exit reason is plan Risk 6's detection mode for a
    broken `--permission-mode` flag (the bypass bar never paints, so
    the C5 idle heuristic can never fire).
    """

    def test_never_idle_exits_startup_unresolved_at_ceiling(self) -> None:
        # Neither PTY ever reaches idle AND no startup event is found.
        # With the plateau detector (issue #1710), the container now bails
        # early on a plateau (N consecutive silent cycles) rather than always
        # waiting for the full ceiling. To test the ceiling path specifically
        # we patch STARTUP_PLATEAU_CYCLES to a very large value so the plateau
        # never fires, then verify the ceiling exit captures the frame.
        c = Container(user_message="hello", max_turns=5)
        pm_mock, dev_mock = _mock_pm("", saw_idle=False), _mock_dev("", saw_idle=False)

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
            patch("agent.granite_container.container.STARTUP_HARD_CEILING_S", 0.05),
            # Disable plateau detector so we hit the pure ceiling exit path.
            patch("agent.granite_container.container.STARTUP_PLATEAU_CYCLES", 10_000_000),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        self.assertEqual(
            result.exit_reason,
            "startup_unresolved",
            f"got {result.exit_reason!r}: {result.exit_message!r}",
        )
        self.assertIn("hard ceiling", result.exit_message)
        # Startup failure diagnostic fields (issue #1710).
        self.assertEqual(result.startup_failure_kind, "ceiling")
        self.assertIsNotNone(result.startup_diagnostic_frame)
        self.assertGreater(len(result.startup_diagnostic_frame or ""), 0)
        # The steady-state loop never ran -- no classified turns.
        self.assertEqual(len(result.turns), 0)

    def test_late_settle_proceeds_to_steady_state(self) -> None:
        # A slow cold start: the PTYs are NOT idle on the first
        # startup cycles (persona still loading) but settle later.
        # The loop must keep polling past the early cycles and then
        # proceed to the steady state (prime-turn relay), not exit early.
        c = Container(user_message="hello", max_turns=3)
        pm_mock, dev_mock = _mock_pm(""), _mock_dev("")

        # Buffer sequence with prime-turn relay:
        # [0-1] startup cycles with saw_idle=False (still loading)
        # [2] startup cycle 2: settled (saw_idle=True)
        # [3] prime-turn relay: [/complete]\nDone. → exits pm_complete
        # wrap-up guard patched (no on_complete_payload)
        pm_buffers = iter(
            [
                _idle_result("", saw_idle=False),  # startup cycle 0: still loading
                _idle_result("", saw_idle=False),  # startup cycle 1: still loading
                _idle_result("", saw_idle=True),  # startup cycle 2: settled
                _idle_result("[/complete]\nDone.", saw_idle=True),  # prime-turn relay
            ]
        )
        pm_mock.read_until_idle.side_effect = lambda **kw: next(pm_buffers)
        dev_mock.read_until_idle.return_value = _idle_result("", saw_idle=True)

        # PM transcript: prime-relay returns [/complete]\nDone.
        pm_transcript_texts = iter(["[/complete]\nDone."])

        def _lat_stub(path, *, baseline_text_count=None):
            if not path or "mock-session-dev" in path:
                return ""
            try:
                return next(pm_transcript_texts)
            except StopIteration:
                return ""

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
            patch.object(c, "_run_wrapup_guard"),  # no on_complete_payload
            patch(
                "agent.granite_container.container.last_assistant_text",
                side_effect=_lat_stub,
            ),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        self.assertEqual(
            result.exit_reason,
            "pm_complete",
            f"got {result.exit_reason!r}: {result.exit_message!r}",
        )

    def test_ceiling_is_long_enough_for_cold_persona_load(self) -> None:
        # The ceiling must comfortably exceed the prime post-write
        # budget (360s) — a cold Opus high-effort persona load can
        # run minutes past it (PR #1612 live run).
        from agent.granite_container.container import (
            PRIME_POST_WRITE_TIMEOUT_S,
            STARTUP_HARD_CEILING_S,
        )

        self.assertGreater(STARTUP_HARD_CEILING_S, PRIME_POST_WRITE_TIMEOUT_S)


class TestContainerPrimeHandlesTrustFolder(unittest.TestCase):
    """PRIMING-1: a fresh PTY parked on the trust-folder screen
    is dismissed with '1' before the prime slash command is sent.

    This unsticks the 200s timeout observed in the live driver
    (issue #1572, regression gate). The C5 idle heuristic requires
    the bypass-permissions bar, which the trust-folder dialog does
    NOT paint. The pre-C5 loop in `_prime_session` looks for the
    trust pattern and dismisses with "1" (the documented response
    from `scripts/probe_slash_arguments.py:241-247`), turning a
    60s silent stall into a <2s dismiss + normal C5 wait.
    """

    def test_trust_folder_dismissed_before_prime(self) -> None:
        c = Container(user_message="hello", max_turns=2)
        pm_mock = _mock_pm("")
        dev_mock = _mock_dev("")

        # PM's read sequence with the new prime logic:
        #   1. pre-C5 trust-dismissal loop: sees trust dialog
        #   2. pre-C5 trust-dismissal loop: post-dismissal idle
        #   3. pre-write C5 wait: welcome frame idle
        #   4. post-write C5 wait: prime response idle ("Worked for Ns")
        trust_buffer = "Do you trust this folder?\n1. Yes, I trust this folder\n2. No"
        idle_buffer = "welcome frame ...bypass permissions on >"
        primed_buffer = "prime response ... Worked for 35s ...bypass permissions on >"
        pm_buffers = iter(
            [
                _idle_result(trust_buffer, saw_idle=False),  # 1: trust dialog
                _idle_result(idle_buffer, saw_idle=True),  # 2: post-dismissal
                _idle_result(idle_buffer, saw_idle=True),  # 3: pre-write C5
                _idle_result(primed_buffer, saw_idle=True),  # 4: post-write C5
            ]
        )
        pm_mock.read_until_idle.side_effect = lambda **kw: next(pm_buffers)

        with patch.object(c, "_spawn_pair"), patch.object(c, "_close_pair"):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            c._prime_session(pm_mock, "/granite:prime-pm-role")

        # Both writes happened on the same PTY, in order:
        # trust dismissal first, then the prime slash command.
        # We assert by index in the call_args_list (not by string
        # lex-order — "1" sorts after "/granite..." in ASCII).
        self.assertEqual(pm_mock.write.call_count, 2)
        first_call, second_call = pm_mock.write.call_args_list
        self.assertEqual(first_call.args[0], "1")
        self.assertTrue(
            second_call.args[0].startswith("/granite:prime-pm-role "),
            f"expected prime slash command, got {second_call.args[0]!r}",
        )

    def test_prime_without_trust_folder_skips_dismiss(self) -> None:
        """When no trust pattern is present and the PTY is idle
        on first read, no dismissal write is made — the prime
        goes through immediately."""
        c = Container(user_message="hello", max_turns=2)
        pm_mock = _mock_pm("")
        pm_mock.read_until_idle.return_value = _idle_result(
            "welcome ...bypass permissions on >", saw_idle=True
        )

        with patch.object(c, "_spawn_pair"), patch.object(c, "_close_pair"):
            c._pm_pty = pm_mock
            c._dev_pty = _mock_dev("")
            c._prime_session(pm_mock, "/granite:prime-pm-role")

        # Only the prime slash command was written — no "1".
        self.assertEqual(pm_mock.write.call_count, 1)
        self.assertTrue(pm_mock.write.call_args.args[0].startswith("/granite:prime-pm-role "))

    def test_prime_uses_post_dismissal_c5_budget(self) -> None:
        """Post-write C5 budget is PRIME_POST_WRITE_TIMEOUT_S, raised
        above the prior 60s/120s default. Persona loading on Opus
        4.8 with high effort can take 90-180s for the first slash
        command (the prime command plus the post-write wait for
        the model's actual response). PR #1612 live run on June
        2026 hit 120s saw_idle=False on PM; the post-write budget
        absorbs that latency."""
        from agent.granite_container.container import (
            PRIME_POST_WRITE_MIN_CONTENT_BYTES,
            PRIME_POST_WRITE_TIMEOUT_S,
            PRIME_PRE_WRITE_TIMEOUT_S,
        )

        self.assertGreaterEqual(PRIME_PRE_WRITE_TIMEOUT_S, 30.0)
        # The post-write budget is the long one (persona load).
        self.assertGreaterEqual(PRIME_POST_WRITE_TIMEOUT_S, 300.0)
        # Pre-write is short (welcome frame); post-write is the
        # long one. The split must be enforced.
        self.assertLess(PRIME_PRE_WRITE_TIMEOUT_S, PRIME_POST_WRITE_TIMEOUT_S)
        # The post-write read needs a content floor; without it,
        # the bypass-permissions bar (a persistent footer) matches
        # the C5 idle heuristic on the stale pre-write buffer.
        self.assertGreaterEqual(PRIME_POST_WRITE_MIN_CONTENT_BYTES, 1000)


class TestContainerSpawnPairReusesPrewarmed(unittest.TestCase):
    """PTYPool pre-warmed pair is reused by Container, not duplicated.

    Regression test for the pool double-spawn that regressed
    issue #1572's orphan-leak acceptance criterion.
    """

    def test_prewarmed_pair_skips_spawn(self) -> None:
        from agent.granite_container.pty_driver import PTYDriver

        prewarmed_pm = MagicMock(spec=PTYDriver)
        prewarmed_dev = MagicMock(spec=PTYDriver)

        # Pass the prewarmed pair via ctor.
        c2 = Container(
            user_message="hello",
            max_turns=2,
            pm_pty=prewarmed_pm,
            dev_pty=prewarmed_dev,
        )

        # Track any new spawn attempts.
        with patch.object(PTYDriver, "spawn") as spawn_method:
            c2._spawn_pair()
            spawn_method.assert_not_called()

        # The prewarmed pair was assigned, not a fresh one.
        self.assertIs(c2._pm_pty, prewarmed_pm)
        self.assertIs(c2._dev_pty, prewarmed_dev)

    def test_no_prewarmed_pair_spawns_fresh(self) -> None:
        """Backward compat: ctor with no prewarmed PTYs still
        spawns a fresh pair (used by tests + run_ping_pong_test)."""
        c = Container(user_message="hello", max_turns=2)
        self.assertIsNone(c._prewarmed_pm_pty)
        self.assertIsNone(c._prewarmed_dev_pty)
        # _spawn_pair is normally covered by the existing test
        # suite; we just confirm the ctor doesn't pre-populate.
        self.assertIsNone(c._pm_pty)
        self.assertIsNone(c._dev_pty)

    def test_close_pair_skips_pool_owned_ptys(self) -> None:
        """PTYs marked _released_to_pool=True are not closed by
        Container._close_pair (the pool's __aexit__ owns them)."""
        from agent.granite_container.pty_driver import PTYDriver

        c = Container(user_message="hello", max_turns=2)
        pool_pm = MagicMock(spec=PTYDriver)
        pool_dev = MagicMock(spec=PTYDriver)
        pool_pm._released_to_pool = True
        pool_dev._released_to_pool = True
        c._pm_pty = pool_pm
        c._dev_pty = pool_dev

        c._close_pair()

        pool_pm.close.assert_not_called()
        pool_dev.close.assert_not_called()


class TestContainerOnTurnHook(unittest.TestCase):
    """PR #1612 review TD1: the optional `on_turn` hook fires once per
    classified PM turn (every destination), and a raising hook never
    crashes the loop."""

    def _run_with_hook(self, on_turn) -> ContainerResult:
        c = Container(user_message="hello", max_turns=3, on_turn=on_turn)
        pm_mock, dev_mock = _mock_pm(""), _mock_dev("")
        # Buffer sequence with prime-turn relay:
        # [0] startup
        # [1] prime-turn relay: "no prefix here" → unknown → on_turn called
        #     _prime_relayed=True
        # [2] stale-buffer guard at turn 0: "[/complete]\nDone." (hash≠prime hash)
        # [3] turn 0 normal read: "[/complete]\nDone." → on_turn called, exits pm_complete
        # wrap-up guard patched (no on_complete_payload)
        pm_buffers = iter(
            [
                _idle_result("", saw_idle=True),  # startup
                _idle_result("no prefix here", saw_idle=True),  # prime-relay: unknown
                _idle_result("[/complete]\nDone.", saw_idle=True),  # stale-buffer guard
                _idle_result("[/complete]\nDone.", saw_idle=True),  # turn 0: complete
            ]
        )
        pm_mock.read_until_idle.side_effect = lambda **kw: next(pm_buffers)
        dev_mock.read_until_idle.return_value = _idle_result("", saw_idle=True)

        # PM transcript texts: prime-relay (unknown), turn 0 (complete).
        pm_transcript_texts_hook = iter(
            [
                "no prefix here",  # prime-relay: unknown
                "[/complete]\nDone.",  # turn 0: complete
            ]
        )

        def _lat_stub(path, *, baseline_text_count=None):
            if path is None:
                return ""
            try:
                return next(pm_transcript_texts_hook)
            except StopIteration:
                return ""

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
            patch.object(c, "_run_wrapup_guard"),  # no on_complete_payload
            patch(
                "agent.granite_container.container.last_assistant_text",
                side_effect=_lat_stub,
            ),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            return c.run()

    def test_on_turn_called_once_per_classified_turn(self) -> None:
        calls: list[int] = []
        result = self._run_with_hook(lambda: calls.append(1))
        self.assertEqual(result.exit_reason, "pm_complete")
        # One unknown turn + one complete turn = two classifications.
        self.assertEqual(len(calls), 2)

    def test_raising_on_turn_does_not_crash_loop(self) -> None:
        def _boom() -> None:
            raise RuntimeError("liveness write failed")

        result = self._run_with_hook(_boom)
        self.assertEqual(result.exit_reason, "pm_complete")


class TestContainerHang(unittest.TestCase):
    """PTY hang is treated as pm_hang / dev_hang exit reason."""

    def test_pm_hang_exits(self) -> None:
        c = Container(user_message="hello", max_turns=3)
        pm_mock, dev_mock = _mock_pm(""), _mock_dev("")

        # Buffer sequence with prime-turn relay (issue #1644):
        # [0] startup: saw_idle=True (settles)
        # [1] prime-turn relay: saw_idle=False (hang) → pm_hang exit before steady-state
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
                    pm_idle_marker="bypass permissions",
                    dev_idle_marker="bypass permissions",
                ),
            ],
            exit_reason="pm_max_turns",
            exit_message="reached max_turns=1",
            transcript_fallback_count=0,
        )
        s = result_to_json(result)
        d = json.loads(s)
        self.assertEqual(d["session_id"], "abc123")
        self.assertEqual(d["exit_reason"], "pm_max_turns")
        self.assertEqual(len(d["turns"]), 1)
        self.assertEqual(d["turns"][0]["classification"], "dev")


class TestPrimeTurnRelay(unittest.TestCase):
    """Issue #1644: PM's prime-turn output is relayed to Dev via operator.

    The prime-turn relay (_route_pm_classification called on PM's prime buffer
    after both primes complete) ensures the PM's first instruction is not lost.
    """

    def test_both_primes_carry_user_message(self) -> None:
        """Both PM and Dev primes are sent with self.user_message (issue #1692).

        PM receives the message for immediate routing. Dev receives it as labeled
        background context so it has task context when the [/dev] relay arrives.
        Dev must NOT act before the relay — this is enforced by the prime text.
        """
        c = Container(user_message="build a feature for me", max_turns=1)
        pm_mock, dev_mock = _mock_pm(""), _mock_dev("")

        pm_mock.read_until_idle.return_value = _idle_result("startup", saw_idle=True)
        dev_mock.read_until_idle.return_value = _idle_result("startup", saw_idle=True)

        with patch.object(c, "_spawn_pair"), patch.object(c, "_close_pair"):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            # Both primes now carry the user message.
            from agent.granite_container.container import DEV_PRIME_SLASH_CMD, PM_PRIME_SLASH_CMD

            c._prime_session(pm_mock, PM_PRIME_SLASH_CMD, include_user_message=True)
            c._prime_session(dev_mock, DEV_PRIME_SLASH_CMD, include_user_message=True)

        # PM's write contains the user message.
        pm_write_arg = pm_mock.write.call_args.args[0]
        self.assertIn("build a feature for me", pm_write_arg)
        # Dev's write also contains the user message (background context, issue #1692).
        dev_write_arg = dev_mock.write.call_args.args[0]
        self.assertIn("build a feature for me", dev_write_arg)

    def test_prime_turn_dev_instruction_relayed_once(self) -> None:
        """PM emits [/dev] during prime → exactly ONE Dev dispatch (S2 + race guard, #1644).

        The prime-turn relay must dispatch the Dev instruction exactly once.
        The stale-buffer race guard (self._prime_relayed + PM summary write)
        prevents the steady-state loop from re-reading the same [/dev] and
        dispatching a second time.
        """
        dispatched_to_dev: list[str] = []

        c = Container(user_message="do the task", max_turns=5)
        pm_mock, dev_mock = _mock_pm(""), _mock_dev("")

        # Buffer sequence:
        # [0] startup
        # [1] prime-turn relay: [/dev]\nBuild X → routes to Dev
        #     Dev cycle runs, last_assistant_text read, verbatim text written to PM.
        #     _prime_relayed=True (else branch fires for all non-break outcomes
        #     including dev routes).
        # [2] await PM idle for summary write (inside dev routing)
        # [3] stale-buffer guard at turn 0 (hash of "[/dev]\nBuild X" != guard buf)
        #     → no nudge; proceeds to normal steady-state read
        # [4] steady-state turn 0 PM read: [/complete]\nDone. → exits pm_complete
        pm_buffers = [
            _idle_result("", saw_idle=True),  # [0] startup
            _idle_result("[/dev]\nBuild X", saw_idle=True),  # [1] prime-turn relay
            _idle_result("", saw_idle=True),  # [2] await PM idle for summary write
            _idle_result("[/complete]\nDone.", saw_idle=True),  # [3] stale-buffer guard
            _idle_result("[/complete]\nDone.", saw_idle=True),  # [4] steady-state turn 0
        ]
        pm_mock.read_until_idle.side_effect = lambda **kw: pm_buffers.pop(0)
        dev_mock.read_until_idle.return_value = _idle_result(
            "I built X and it works.", saw_idle=True
        )

        original_dev_write = dev_mock.write.side_effect

        def _track_dev_write(payload: str) -> None:
            dispatched_to_dev.append(payload)
            if original_dev_write:
                original_dev_write(payload)

        dev_mock.write.side_effect = _track_dev_write

        # PM transcript texts: prime-relay (dev), then turn 0 steady-state (complete).
        # Dev transcript: verbatim dev text.
        pm_transcript_texts = iter(
            [
                "[/dev]\nBuild X",  # prime-relay
                "[/complete]\nDone.",  # turn 0 steady-state
            ]
        )

        def _lat_stub(path, *, baseline_text_count=None):
            if not path:
                return ""
            if "mock-session-dev" in path:
                return "I built X and it works."
            try:
                return next(pm_transcript_texts)
            except StopIteration:
                return ""

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
            patch.object(c, "_run_wrapup_guard"),  # no on_complete_payload
            patch(
                "agent.granite_container.container.last_assistant_text",
                side_effect=_lat_stub,
            ),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        # The Dev PTY was written to EXACTLY once (the relayed instruction payload).
        # The zero-LLM path sends the classification payload verbatim (no extract_dev_prompt).
        # The stale-PM-buffer race guard ensures the steady-state loop does
        # not re-dispatch the same [/dev] a second time.
        self.assertEqual(
            len(dispatched_to_dev),
            1,
            f"expected exactly 1 Dev dispatch, got {len(dispatched_to_dev)}: {dispatched_to_dev!r}",
        )
        self.assertEqual(dispatched_to_dev[0], "Build X")
        self.assertIn(result.exit_reason, ("pm_complete", "pm_max_turns"))

    def test_prime_turn_user_payload_routed(self) -> None:
        """PM emits [/user] (not [/dev]) during prime → routes to user, user_facing_routed=True.

        This verifies the prime-relay handles non-dev prime cases correctly
        (concern C6, issues #1644/#1647).
        """
        delivered: list[str] = []

        def _on_user(payload: str) -> None:
            delivered.append(payload)

        c = Container(user_message="what is the status?", max_turns=3, on_user_payload=_on_user)
        pm_mock, dev_mock = _mock_pm(""), _mock_dev("")

        # Buffer: startup, then [/user] at prime-relay → exits pm_user
        pm_buffers = [
            _idle_result("", saw_idle=True),  # startup
            _idle_result("[/user]\nStatus: all good.", saw_idle=True),  # prime-relay
        ]
        pm_mock.read_until_idle.side_effect = lambda **kw: pm_buffers.pop(0)
        dev_mock.read_until_idle.return_value = _idle_result("", saw_idle=True)

        # PM transcript: prime-relay returns [/user] text.
        pm_transcript_texts = iter(["[/user]\nStatus: all good."])

        def _lat_stub(path, *, baseline_text_count=None):
            if not path or "mock-session-dev" in path:
                return ""
            try:
                return next(pm_transcript_texts)
            except StopIteration:
                return ""

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
            patch(
                "agent.granite_container.container.last_assistant_text",
                side_effect=_lat_stub,
            ),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        self.assertEqual(result.exit_reason, "pm_user")
        self.assertEqual(delivered, ["Status: all good."])
        self.assertTrue(
            result.user_facing_routed,
            "expected user_facing_routed=True after [/user] delivery at prime-relay",
        )


class TestWrapupGuard(unittest.TestCase):
    """Issue #1647: a granite session cannot reach completed with zero user-facing messages.

    The wrap-up guard fires when exit_reason is in the successful set and
    user_facing_routed is False. It drives PM to produce a [/user]/[/complete]
    summary; on continued PM silence it delivers OPERATOR_TERMINAL_MESSAGE.
    """

    def _build_container_no_callback(self) -> tuple[Container, MagicMock, MagicMock]:
        """Container with no user/complete callbacks (simulates PM silence)."""
        c = Container(user_message="do the work", max_turns=1)
        pm_mock = _mock_pm("")
        dev_mock = _mock_dev("")
        return c, pm_mock, dev_mock

    def test_no_user_facing_message_triggers_wrapup(self) -> None:
        """When PM never emits [/user]/[/complete], the wrap-up guard fires
        and sends OPERATOR_TERMINAL_MESSAGE via on_user_payload (issue #1647).
        """
        terminal_deliveries: list[str] = []

        def _on_user(payload: str) -> None:
            terminal_deliveries.append(payload)

        c = Container(user_message="do the work", max_turns=1, on_user_payload=_on_user)
        pm_mock, dev_mock = _mock_pm(""), _mock_dev("")

        # Steady-state exits pm_max_turns (no [/complete]) → wrap-up guard fires.
        # Wrap-up guard writes PM_WRAPUP_PROMPT; PM responds with another [/dev]
        # (still no user-facing) so guard exhausts MAX_WRAPUP_ATTEMPTS=1;
        # then OPERATOR_TERMINAL_MESSAGE is sent via on_user_payload.
        pm_buffers = [
            _idle_result("", saw_idle=True),  # [0] startup
            _idle_result("", saw_idle=True),  # [1] prime-relay (unknown → _prime_relayed=True)
            _idle_result("[/dev]\ntask", saw_idle=True),  # [2] stale-buffer guard at turn 0
            _idle_result("[/dev]\ntask", saw_idle=True),  # [3] turn 0 steady-state PM read
            _idle_result("", saw_idle=True),  # [4] turn 0 await PM idle (inside dev route)
            # wrap-up guard:
            _idle_result("", saw_idle=True),  # [5] await PM idle before wrapup prompt
            _idle_result(
                "[/dev]\nstill more work", saw_idle=True
            ),  # [6] PM wrapup response (still no user-facing → dev route)
            _idle_result("", saw_idle=True),  # [7] await PM idle for wrapup dev-route summary
        ]
        pm_mock.read_until_idle.side_effect = lambda **kw: pm_buffers.pop(0)
        dev_mock.read_until_idle.return_value = _idle_result("Dev finished.", saw_idle=True)

        # PM transcript texts: prime-relay (unknown), turn 0 ([/dev]), wrapup ([/dev] again).
        # Dev transcript: verbatim dev text.
        pm_transcript_texts = iter(
            [
                "",  # prime-relay: unknown (empty → fallback)
                "[/dev]\ntask",  # turn 0: dev route
                "[/dev]\nstill more work",  # wrapup response: still dev (no user-facing)
            ]
        )

        def _lat_stub(path, *, baseline_text_count=None):
            if not path:
                return ""
            if "mock-session-dev" in path:
                return "Dev finished."
            try:
                return next(pm_transcript_texts)
            except StopIteration:
                return ""

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
            patch(
                "agent.granite_container.container.last_assistant_text",
                side_effect=_lat_stub,
            ),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        # The wrap-up guard must have delivered OPERATOR_TERMINAL_MESSAGE.
        from agent.granite_container.container import OPERATOR_TERMINAL_MESSAGE

        self.assertIn(OPERATOR_TERMINAL_MESSAGE, terminal_deliveries)
        self.assertTrue(result.user_facing_routed)
        self.assertEqual(result.exit_reason, "pm_no_user_message")

    def test_wrapup_attempts_bounded(self) -> None:
        """The wrap-up guard is capped at MAX_WRAPUP_ATTEMPTS=1 (issue #1647)."""
        from agent.granite_container.container import MAX_WRAPUP_ATTEMPTS

        self.assertEqual(MAX_WRAPUP_ATTEMPTS, 1)

    def test_wrapup_seed_falls_back_to_canned_string(self) -> None:
        """When _last_dev_report is None and Dev PTY returns blank,
        the wrap-up prompt is seeded with DEV_REPORT_UNAVAILABLE (BLOCKER 2).
        No NameError, no empty string interpolation.
        """
        from agent.granite_container.container import (
            DEV_REPORT_UNAVAILABLE,
            OPERATOR_TERMINAL_MESSAGE,
        )

        terminal_deliveries: list[str] = []

        def _on_user(payload: str) -> None:
            terminal_deliveries.append(payload)

        c = Container(user_message="do the work", max_turns=0, on_user_payload=_on_user)
        pm_mock, dev_mock = _mock_pm(""), _mock_dev("")

        # max_turns=0 means steady-state never runs → pm_max_turns immediately.
        # No _last_dev_report captured (dev branch never ran).
        # Dev PTY returns empty buffer → DEV_REPORT_UNAVAILABLE used as seed.
        pm_buffers = [
            _idle_result("", saw_idle=True),  # startup
            _idle_result("", saw_idle=True),  # prime-relay (unknown)
            # wrap-up guard:
            _idle_result("", saw_idle=True),  # await PM idle
            _idle_result("", saw_idle=True),  # PM wrapup response (still unknown → exhausted)
        ]
        pm_mock.read_until_idle.side_effect = lambda **kw: pm_buffers.pop(0)
        dev_mock.read_until_idle.return_value = _idle_result("", saw_idle=True)  # blank Dev

        wrapup_prompts_written: list[str] = []
        original_write = pm_mock.write.side_effect

        def _track_pm_write(payload: str) -> None:
            wrapup_prompts_written.append(payload)
            if original_write:
                original_write(payload)

        pm_mock.write.side_effect = _track_pm_write

        # PM transcript: prime-relay (unknown), then wrapup response (still unknown).
        # Dev transcript: always returns "" so DEV_REPORT_UNAVAILABLE is used.
        pm_transcript_texts = iter(
            [
                "",  # prime-relay: unknown (empty)
                "",  # wrapup response: still unknown (empty)
            ]
        )

        def _lat_stub(path, *, baseline_text_count=None):
            if not path or "mock-session-dev" in path:
                return ""
            try:
                return next(pm_transcript_texts)
            except StopIteration:
                return ""

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
            patch(
                "agent.granite_container.container.last_assistant_text",
                side_effect=_lat_stub,
            ),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            # Should not raise NameError or crash.
            result = c.run()

        # OPERATOR_TERMINAL_MESSAGE delivered (max attempts exhausted).
        self.assertIn(OPERATOR_TERMINAL_MESSAGE, terminal_deliveries)
        # At least one PM write contained DEV_REPORT_UNAVAILABLE (the seed).
        wrapup_with_seed = [p for p in wrapup_prompts_written if DEV_REPORT_UNAVAILABLE in p]
        self.assertTrue(
            wrapup_with_seed,
            f"expected PM_WRAPUP_PROMPT to contain DEV_REPORT_UNAVAILABLE; "
            f"PM writes were: {wrapup_prompts_written!r}",
        )
        # Wrap-up seed-build fallback must increment transcript_fallback_count (SDLC tech-debt fix).
        # The prime-turn and wrapup-response also fall back (both transcript reads return ""),
        # so the counter is >= 1 from the seed-build site alone.
        self.assertGreaterEqual(
            result.transcript_fallback_count,
            1,
            "wrap-up seed-build DEV_REPORT_UNAVAILABLE branch must increment "
            "transcript_fallback_count",
        )

    def test_terminal_message_sent_when_pm_silent(self) -> None:
        """After the bounded wrap-up yields nothing, OPERATOR_TERMINAL_MESSAGE
        is delivered via on_user_payload so the human always gets a real message
        (concern C2, issue #1647).

        Also verifies user_facing_routed=True and exit_reason=pm_no_user_message.
        """
        from agent.granite_container.container import OPERATOR_TERMINAL_MESSAGE

        terminal_deliveries: list[str] = []

        def _on_user(payload: str) -> None:
            terminal_deliveries.append(payload)

        c = Container(user_message="do the work", max_turns=0, on_user_payload=_on_user)
        pm_mock, dev_mock = _mock_pm(""), _mock_dev("")

        pm_buffers = [
            _idle_result("", saw_idle=True),  # startup
            _idle_result("", saw_idle=True),  # prime-relay
            # wrap-up guard: PM never produces user-facing output
            _idle_result("", saw_idle=True),  # await PM idle
            _idle_result("no prefix — still silent", saw_idle=True),  # PM wrapup response
        ]
        pm_mock.read_until_idle.side_effect = lambda **kw: pm_buffers.pop(0)
        dev_mock.read_until_idle.return_value = _idle_result("", saw_idle=True)

        # PM transcript: prime-relay (unknown), wrapup response (still no prefix).
        pm_transcript_texts = iter(
            [
                "",  # prime-relay: unknown (empty → fallback)
                "no prefix — still silent",  # wrapup response: unknown (no prefix)
            ]
        )

        def _lat_stub(path, *, baseline_text_count=None):
            if not path or "mock-session-dev" in path:
                return ""
            try:
                return next(pm_transcript_texts)
            except StopIteration:
                return ""

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
            patch(
                "agent.granite_container.container.last_assistant_text",
                side_effect=_lat_stub,
            ),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        self.assertIn(OPERATOR_TERMINAL_MESSAGE, terminal_deliveries)
        self.assertTrue(result.user_facing_routed)
        self.assertEqual(result.exit_reason, "pm_no_user_message")

    def test_empty_complete_body_not_user_facing(self) -> None:
        """[/complete] with empty body does not set user_facing_routed (S5/C7, #1647).

        An empty [/complete] is not user-facing — it triggers the wrap-up guard
        instead of silently terminating the session.
        """
        terminal_deliveries: list[str] = []

        def _on_user(payload: str) -> None:
            terminal_deliveries.append(payload)

        c = Container(user_message="do the work", max_turns=2, on_user_payload=_on_user)
        pm_mock, dev_mock = _mock_pm(""), _mock_dev("")

        pm_buffers = [
            _idle_result("", saw_idle=True),  # startup
            _idle_result("[/complete]", saw_idle=True),  # prime-relay: empty [/complete]
            # wrap-up guard:
            _idle_result("", saw_idle=True),  # await PM idle
            _idle_result("[/user]\nReal summary.", saw_idle=True),  # PM wrapup → delivers
        ]
        pm_mock.read_until_idle.side_effect = lambda **kw: pm_buffers.pop(0)
        dev_mock.read_until_idle.return_value = _idle_result("", saw_idle=True)

        # PM transcript: prime-relay (empty [/complete]), wrapup ([/user]\nReal summary.).
        pm_transcript_texts = iter(
            [
                "[/complete]",  # prime-relay: empty complete body (not user-facing)
                "[/user]\nReal summary.",  # wrapup response: delivers to user
            ]
        )

        def _lat_stub(path, *, baseline_text_count=None):
            if not path or "mock-session-dev" in path:
                return ""
            try:
                return next(pm_transcript_texts)
            except StopIteration:
                return ""

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            patch.object(c, "_run_pkill_fallback"),
            patch(
                "agent.granite_container.container.last_assistant_text",
                side_effect=_lat_stub,
            ),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        # The empty [/complete] must NOT have triggered a delivery.
        # The wrap-up guard ran and the PM's [/user] was delivered.
        self.assertIn("Real summary.", terminal_deliveries)
        # Empty [/complete] body — user_facing_routed set by the wrap-up [/user].
        self.assertTrue(result.user_facing_routed)


class TestContainerResultPtySlot(unittest.TestCase):
    """ContainerResult.pty_slot field (issue #1663).

    pty_slot is stamped by BridgeAdapter from acquire_pair's slot.idx
    AFTER the container run completes. The ContainerResult field must
    default to None (it is not populated by the container itself) and
    must accept an integer value so BridgeAdapter can assign it.
    """

    def test_pty_slot_defaults_none(self) -> None:
        """ContainerResult.pty_slot is None on a freshly-built result."""
        result = ContainerResult(
            session_id="abc",
            user_message="hello",
            turns=[],
            exit_reason="pm_complete",
            exit_message="",
            transcript_fallback_count=0,
        )
        self.assertIsNone(result.pty_slot)

    def test_pty_slot_roundtrips(self) -> None:
        """Setting ContainerResult.pty_slot to a value returns that value."""
        result = ContainerResult(
            session_id="abc",
            user_message="hello",
            turns=[],
            exit_reason="pm_complete",
            exit_message="",
            transcript_fallback_count=0,
        )
        result.pty_slot = 2
        self.assertEqual(result.pty_slot, 2)


class TestTranscriptPathRealpath(unittest.TestCase):
    """`_transcript_path` realpath-resolves the cwd slug and guards on session_id.

    Finding 1, latent bug 2: Claude Code names transcript dirs from the
    realpath-resolved cwd. A symlink-crossing cwd must slug to the
    resolved path, and the None-guard must precede the realpath so a
    falsy session_id still yields None (never a wrong path).
    """

    def test_none_session_id_returns_none_even_with_symlink_cwd(self) -> None:
        """None-guard precedes realpath: falsy session_id always returns None."""
        import os
        import tempfile

        from agent.granite_container.container import _transcript_path

        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.join(tmp, "real")
            link = os.path.join(tmp, "link")
            os.mkdir(real)
            os.symlink(real, link)
            self.assertIsNone(_transcript_path(link, None))
            self.assertIsNone(_transcript_path(link, ""))

    def test_symlink_cwd_slug_is_realpath_resolved(self) -> None:
        """A symlink-crossing cwd produces the realpath slug, not the link slug."""
        import os
        import tempfile

        from agent.granite_container.container import _transcript_path

        with tempfile.TemporaryDirectory() as tmp:
            # Resolve tmp itself (macOS /var -> /private/var) so the
            # expected slug is computed from the same realpath base.
            real = os.path.realpath(os.path.join(tmp, "real"))
            link = os.path.join(tmp, "link")
            os.mkdir(real)
            os.symlink(real, link)

            path = _transcript_path(link, "sess-uuid")
            self.assertIsNotNone(path)
            expected_slug = real.replace("/", "-").replace(".", "-")
            link_slug = os.path.realpath(link)  # same as real, sanity
            self.assertEqual(link_slug, real)
            self.assertIn(expected_slug, path)
            self.assertTrue(path.endswith("sess-uuid.jsonl"))

    def test_dotted_worktree_cwd_replaces_dot_with_dash(self) -> None:
        """Regression: a `.worktrees` cwd must slug the dot to '-'.

        Every bridge session runs in a synthetic `.worktrees/dev-{id}`
        worktree. Claude Code replaces BOTH '/' and '.' with '-'. Replacing
        only '/' pointed the transcript read at a directory Claude Code never
        writes to -> file-missing every turn -> OPERATOR_TERMINAL_MESSAGE
        shipped instead of the PM's real reply. Must stay in sync with
        bridge_adapter._transcript_path_from_spec.
        """
        from agent.granite_container.container import _transcript_path

        # Non-existent path: realpath is an identity transform, so the slug is
        # deterministic without touching the filesystem.
        path = _transcript_path("/Users/x/src/ai/.worktrees/dev-5732c769", "u")
        self.assertIsNotNone(path)
        self.assertIn("-Users-x-src-ai--worktrees-dev-5732c769", path)
        self.assertNotIn(".worktrees", path)
        self.assertTrue(path.endswith("u.jsonl"))

    def test_empty_cwd_does_not_crash_and_skips_realpath(self) -> None:
        """Empty cwd is not realpath'd (would return process CWD); slug stays empty-rooted."""
        from agent.granite_container.container import _transcript_path

        path = _transcript_path("", "sess-uuid")
        # cwd == "" -> realpath skipped -> slug "" -> path ends with the file.
        self.assertIsNotNone(path)
        self.assertTrue(path.endswith("sess-uuid.jsonl"))


class TestTranscriptReadDiagnostic(unittest.TestCase):
    """The three-way transcript-read diagnostic (Finding 1 lead change).

    A single 'PM transcript read empty' message hid three distinct
    failure modes. The split must emit stable, greppable substrings:
    path-None / file-missing / no-new-entry.
    """

    def test_branch_classifier_path_none(self) -> None:
        from agent.granite_container.container import _transcript_read_branch

        self.assertEqual(_transcript_read_branch(None), "transcript read: path-None")

    def test_branch_classifier_file_missing(self) -> None:
        from agent.granite_container.container import _transcript_read_branch

        self.assertEqual(
            _transcript_read_branch("/no/such/transcript.jsonl"),
            "transcript read: file-missing",
        )

    def test_branch_classifier_no_new_entry(self) -> None:
        import tempfile

        from agent.granite_container.container import _transcript_read_branch

        with tempfile.NamedTemporaryFile(suffix=".jsonl") as fh:
            self.assertEqual(
                _transcript_read_branch(fh.name),
                "transcript read: no-new-entry",
            )

    def test_log_diagnostic_path_none_substring(self) -> None:
        from agent.granite_container.container import _log_transcript_read_diagnostic

        pm = MagicMock()
        pm._session_id = "pm-uuid"
        dev = MagicMock()
        dev._session_id = "dev-uuid"
        with self.assertLogs("agent.granite_container.container", level="WARNING") as cm:
            _log_transcript_read_diagnostic("prime-turn", None, pm, dev)
        joined = "\n".join(cm.output)
        self.assertIn("transcript read: path-None", joined)
        self.assertIn("prime-turn", joined)
        self.assertIn("pm-uuid", joined)

    def test_log_diagnostic_file_missing_substring(self) -> None:
        from agent.granite_container.container import _log_transcript_read_diagnostic

        pm = MagicMock()
        pm._session_id = "pm-uuid"
        dev = MagicMock()
        dev._session_id = "dev-uuid"
        with self.assertLogs("agent.granite_container.container", level="WARNING") as cm:
            _log_transcript_read_diagnostic(
                "steady-state turn 3", "/no/such/transcript.jsonl", pm, dev
            )
        joined = "\n".join(cm.output)
        self.assertIn("transcript read: file-missing", joined)
        self.assertIn("steady-state turn 3", joined)

    def test_log_diagnostic_no_new_entry_substring(self) -> None:
        import tempfile

        from agent.granite_container.container import _log_transcript_read_diagnostic

        pm = MagicMock()
        pm._session_id = "pm-uuid"
        dev = MagicMock()
        dev._session_id = "dev-uuid"
        with tempfile.NamedTemporaryFile(suffix=".jsonl") as fh:
            with self.assertLogs("agent.granite_container.container", level="WARNING") as cm:
                _log_transcript_read_diagnostic("wrap-up guard", fh.name, pm, dev)
        joined = "\n".join(cm.output)
        self.assertIn("transcript read: no-new-entry", joined)
        self.assertIn("wrap-up guard", joined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
