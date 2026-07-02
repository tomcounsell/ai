"""Substrate A deterministic fault injectors (plan Task 3).

One injector per failure class in the plan's Substrate A table. Each targets
a real granite seam, feeds it a recorded / synthetic frame stream (or a
scripted classifier), and asserts the recovery / detection path fires
deterministically — sub-second, in the default unit suite, with no ollama,
no model, no network, and no real ``claude`` spawn.

Every injector was demonstrated **red-first** before being asserted green:
the detection / recovery path was temporarily broken (idle bar matcher
neutered, login patterns emptied, read deadline bypassed, wrap-up guard
disabled, crash suppressed) and the test was observed to FAIL, proving it
detects the fault rather than passing vacuously. The captured failing output
is recorded out-of-band (see the PR description); this module carries only
the green assertions.

Class 6 (silent no-progress tail) asserts silence is OBSERVABLE via the
existing ``IdleResult`` seam only. Wiring an actual no-progress detector is
explicitly OUT OF SCOPE (#1688 / plan No-Gos).
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from agent.granite_container.pty_driver import IDLE_BAR
from agent.granite_container.startup_parser import StartupEvent, parse_startup_frame
from tests.granite_faults import scenarios

# QUIESCENCE_S defaults to 2.0s (the byte-silence gate). Patch it small so the
# driver-seam injectors run sub-second; the gate itself has dedicated coverage
# in test_pty_driver.py::TestQuiescenceGate — here we exercise the bar/glyph/
# floor + deadline logic, not the real-time wait.
_FAST_QUIESCENCE = patch("agent.granite_container.pty_driver.QUIESCENCE_S", 0.02)


# ===========================================================================
# Class 1 — Turn-detection wedge
# ===========================================================================
class TestClass1TurnDetectionWedge(unittest.TestCase):
    """Recorded idle frame with IDLE_BAR mutated/removed → no idle, bounded."""

    @_FAST_QUIESCENCE
    def test_baseline_frame_reaches_idle(self) -> None:
        """Control: the unmutated recorded frame DOES reach idle.

        Proves the fixture is a valid idle capture, so a False in the
        mutated case is caused by the mutation, not a broken fixture.
        """
        frames = scenarios.load_fixture("idle_settled.frames")
        driver = scenarios.driver_with_child(scenarios.fake_child_frames([frames]))
        result = driver.read_until_idle(min_content_bytes=400, timeout_s=1.0)
        self.assertTrue(
            result.saw_idle, f"baseline fixture should idle; tail={result.buffer[-120:]!r}"
        )

    @_FAST_QUIESCENCE
    def test_removed_idle_bar_wedges_deterministically(self) -> None:
        """IDLE_BAR text removed → detector reports no-idle, no infinite wait."""
        frames = scenarios.remove_idle_bar(scenarios.load_fixture("idle_settled.frames"))
        # Sanity: the mutation actually removed the signal the detector keys on.
        self.assertIsNone(IDLE_BAR.search(frames), "mutation must remove the bypass bar")

        driver = scenarios.driver_with_child(scenarios.fake_child_frames([frames]))
        result = driver.read_until_idle(min_content_bytes=400, timeout_s=0.5)

        self.assertFalse(result.saw_idle, "removed idle bar must wedge (no idle)")
        # No infinite wait: the read returned bounded by its own timeout.
        self.assertLessEqual(result.elapsed_ms, 1500, "wedge must stay bounded, not hang")

    @_FAST_QUIESCENCE
    def test_renamed_idle_bar_also_wedges(self) -> None:
        """A reflowed bar wording (Anthropic release drift) also wedges."""
        frames = scenarios.rename_idle_bar(scenarios.load_fixture("idle_settled.frames"))
        self.assertIsNone(IDLE_BAR.search(frames))
        driver = scenarios.driver_with_child(scenarios.fake_child_frames([frames]))
        result = driver.read_until_idle(min_content_bytes=400, timeout_s=0.5)
        self.assertFalse(result.saw_idle)

    def test_hook_edge_resolves_the_wedge(self) -> None:
        """#1688 green-swap: with the Stop hook edge present, turn-end is
        detected even though the idle bar is stripped (read_until_idle wedges).

        The idle heuristic remains wedged (asserted above); the hook edge
        supplies the turn-end signal independent of the bar, so the container's
        hook-driven authority no longer depends on the fragile bar glyph.
        """
        import json
        import tempfile
        from pathlib import Path

        from agent.granite_container.hook_edge import TURN_END, HookEdgeConsumer

        with tempfile.TemporaryDirectory() as d:
            edge = Path(d) / "edges.ndjson"
            envelope = {
                "ts": 1.0,
                "event": "Stop",
                "payload": {
                    "hook_event_name": "Stop",
                    "session_id": "sid",
                    "transcript_path": "/t.jsonl",
                },
            }
            edge.write_text(json.dumps(envelope) + "\n")
            consumer = HookEdgeConsumer(edge, session_id="sid")
            edges = consumer.poll()
            self.assertEqual([e.kind for e in edges], [TURN_END])
            self.assertEqual(edges[0].transcript_path, "/t.jsonl")


# ===========================================================================
# Class 2 — Startup-dialog / /login wedge
# ===========================================================================
class TestClass2StartupLoginWedge(unittest.TestCase):
    """Synthetic startup frames → parse_startup_frame classification."""

    def test_baseline_login_frame_classifies(self) -> None:
        """Control: the recorded login frame classifies as LOGIN_PROMPT."""
        frame = scenarios.load_fixture("startup_login_prompt.frame")
        match = parse_startup_frame(frame)
        self.assertEqual(match.event, StartupEvent.LOGIN_PROMPT)

    def test_login_phrases_dropped_falls_to_unknown(self) -> None:
        """Login wording drifts past every pattern → UNKNOWN (startup_unresolved)."""
        frame = scenarios.drop_login_phrases(scenarios.load_fixture("startup_login_prompt.frame"))
        match = parse_startup_frame(frame)
        self.assertEqual(
            match.event,
            StartupEvent.UNKNOWN,
            "a login frame the parser cannot recognize must fall to UNKNOWN "
            "(the startup_unresolved path), not a wrong classification",
        )
        # The response is None on UNKNOWN — the container asks granite / bails,
        # it does not auto-dismiss a frame it failed to recognize.
        self.assertIsNone(match.response)


# ===========================================================================
# Class 3 — Process hang / U-state
# ===========================================================================
class TestClass3ProcessHang(unittest.TestCase):
    """Stub PTY child that never paints → bounded read, no unbounded block."""

    @_FAST_QUIESCENCE
    def test_silent_hung_child_read_is_bounded(self) -> None:
        """A silent hung child → read returns at its deadline, saw_idle=False."""
        child = scenarios.fake_child_hung(honor_timeout=True)
        driver = scenarios.driver_with_child(child, timeout_s=5.0)

        budget_s = 0.3
        result = driver.read_until_idle(min_content_bytes=0, timeout_s=budget_s)

        self.assertFalse(result.saw_idle, "a hung child never reaches idle")
        # The read honored its deadline: elapsed is bounded near the budget,
        # never the driver's 5s default and never unbounded.
        self.assertLess(
            result.elapsed_ms,
            int(budget_s * 1000) + 400,
            f"bounded-read must honor the timeout; elapsed={result.elapsed_ms}ms",
        )


# ===========================================================================
# Class 4 — Loop / non-convergence
# ===========================================================================
class TestClass4LoopNonConvergence(unittest.TestCase):
    """Scripted PM always emits [/dev] → max-turns + wrap-up guard terminate."""

    def test_always_dev_terminates_with_user_facing_message(self) -> None:
        from agent.granite_container.container import OPERATOR_TERMINAL_MESSAGE

        run = scenarios.run_scripted_container(pm_text="[/dev]\nkeep building forever", max_turns=2)

        # The loop did not spin forever: it hit the max-turns safety cap and
        # the wrap-up guard delivered a user-facing terminal message.
        self.assertEqual(run.result.exit_reason, "pm_no_user_message")
        self.assertIn(OPERATOR_TERMINAL_MESSAGE, run.user_deliveries)
        self.assertTrue(run.result.user_facing_routed)
        # Every consumed steady-state turn was dev-routed (non-convergence).
        dev_turns = [t for t in run.result.turns if t.classification == "dev"]
        self.assertGreaterEqual(len(dev_turns), 1)


# ===========================================================================
# Class 5 — Crash
# ===========================================================================
class TestClass5CrashFailLoud(unittest.TestCase):
    """Corrupt transcript read → fail-loud exception exit, not silent success."""

    def test_transcript_crash_surfaces_as_exception(self) -> None:
        def _boom(path: str | None, *, baseline_text_count: int | None = None) -> str:
            raise ValueError("corrupt JSONL: unexpected end of transcript")

        run = scenarios.run_scripted_container(
            pm_text="[/dev]\nirrelevant",
            last_assistant_side_effect=_boom,
        )

        # Fail-loud: the crash surfaces as the `exception` exit reason with a
        # populated message — NOT a silent clean exit.
        self.assertEqual(run.result.exit_reason, "exception")
        self.assertTrue(run.result.exit_message, "a crash must carry a message")
        self.assertIn("ValueError", run.result.exit_message)
        # Not silently marked as successfully delivered.
        self.assertFalse(run.result.user_facing_routed)


# ===========================================================================
# Class 6 — Silent no-progress tail
# ===========================================================================
class TestClass6SilentNoProgressTail(unittest.TestCase):
    """Stub emits N frames then goes quiet → silence observable via the seam."""

    @_FAST_QUIESCENCE
    def test_silence_is_observable_via_idle_result(self) -> None:
        """No idle bar ever paints → saw_idle=False, elapsed surfaces the wait.

        This asserts ONLY the existing observable seam. No no-progress
        detector is implemented or wired — that is out of scope (#1688).
        """
        frames = scenarios.load_fixture("working_no_idle.frames")
        driver = scenarios.driver_with_child(scenarios.fake_child_frames([frames]))

        budget_s = 0.3
        result = driver.read_until_idle(min_content_bytes=0, timeout_s=budget_s)

        # The tail is silent and never reaches idle.
        self.assertFalse(result.saw_idle, "progress-then-quiet must not false-idle")
        # The elapsed-since-last-frame signal is surfaced: the read waited out
        # its budget looking for an idle that never came. This is the seam a
        # future detector would consume — here we only assert it is observable.
        self.assertGreaterEqual(
            result.elapsed_ms,
            int(budget_s * 1000 * 0.6),
            f"elapsed must surface the silent wait; got {result.elapsed_ms}ms",
        )
        # The progress frames WERE captured (progress happened, then stopped).
        self.assertIn("esc to interrupt", result.turn_buffer)


# ===========================================================================
# Golden-recorder output is consumable by the replay-and-mutate path
# ===========================================================================
class TestRecordedFixtureIsConsumable(unittest.TestCase):
    """The golden-recorder's real ollama capture feeds the Substrate A path.

    Task 4 commits ``recorded_session.frames`` (captured from a real
    ollama-backed ``claude`` session — trust dialog, welcome box, model reply,
    stop-hook paint). This proves that recorded fixture is consumable by the
    same replay-and-mutate machinery the hand-authored seeds use: it carries
    the load-bearing idle bar, and the mutation removes it.
    """

    def test_recorded_frames_carry_the_idle_bar(self) -> None:
        frames = scenarios.load_fixture("recorded_session.frames")
        self.assertIsNotNone(
            IDLE_BAR.search(frames),
            "the recorded golden fixture must carry the bypass-permissions bar "
            "(the load-bearing idle signal Substrate A mutates)",
        )

    def test_remove_idle_bar_mutation_neutralizes_recorded_frames(self) -> None:
        frames = scenarios.load_fixture("recorded_session.frames")
        mutated = scenarios.remove_idle_bar(frames)
        self.assertIsNone(
            IDLE_BAR.search(mutated),
            "the mutation must strip the bar from the real recording too — "
            "recorded fixtures are consumable by the same mutate path as seeds",
        )


# ===========================================================================
# Harness invariants — coverage + no orphan processes
# ===========================================================================
class TestHarnessInvariants(unittest.TestCase):
    """One injector per class, and no real child process is ever spawned."""

    def test_one_scenario_per_failure_class(self) -> None:
        classes = sorted(s.failure_class for s in scenarios.SCENARIOS)
        self.assertEqual(classes, [1, 2, 3, 4, 5, 6], "exactly one injector per plan class")

    def test_fake_children_are_not_real_processes(self) -> None:
        """The injectors' fake children are MagicMocks, never real spawns."""
        for child in (
            scenarios.fake_child_frames(["x"]),
            scenarios.fake_child_hung(honor_timeout=True),
        ):
            self.assertIsInstance(child, MagicMock)

    def test_no_orphan_claude_children_after_injectors(self) -> None:
        """After running the driver-seam injectors, this process has no
        ``claude`` / ``pexpect`` child processes — nothing real was spawned.

        The autouse ``_block_real_claude_spawn`` conftest guard makes a real
        spawn raise; this test confirms the complementary invariant that the
        injectors leave zero orphan PTY children behind.
        """
        import psutil

        with _FAST_QUIESCENCE:
            # Exercise every driver-seam injector once.
            scenarios.driver_with_child(
                scenarios.fake_child_frames([scenarios.load_fixture("idle_settled.frames")])
            ).read_until_idle(min_content_bytes=0, timeout_s=0.2)
            scenarios.driver_with_child(
                scenarios.fake_child_hung(honor_timeout=True), timeout_s=1.0
            ).read_until_idle(min_content_bytes=0, timeout_s=0.2)

        me = psutil.Process(os.getpid())
        leaked = [
            c.name()
            for c in me.children(recursive=True)
            if "claude" in c.name().lower() or "pexpect" in c.name().lower()
        ]
        self.assertEqual(leaked, [], f"no orphan PTY children expected; found {leaked}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
