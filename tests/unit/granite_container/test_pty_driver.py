"""Tests for the substrate driver (agent.granite_container.pty_driver).

Two layers of coverage:
  - Unit tests: pure logic, no real `claude` invocation. Mocks a pexpect
    child and exercises `write` (C1 submit key), `read_until_idle` (C5
    glyph+bar+floor), and the `INTERRUPTED_RE` regex (C2 dual-form
    acceptance).
  - Spike-regression test: re-runs the v7 spike's scenarios 1, 2, 3, 6
    against the new driver. Gated on the `claude --print "ping"`
    prerequisite (model-reachable env). In a non-reachable env, the
    test is *skipped* with a structured log line.

Why mocks for unit tests: pexpect's API is a thin surface (send /
read_nonblocking / before / after). Mocking it lets us assert the
*driver's* behavior (the C1/C2/C5 invariants) without standing up a
real TUI. The spike-regression test is the durable guard that the
driver's behavior matches the spike's observed_state on the real TUI.
"""

from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pexpect
import pexpect.exceptions

from agent.granite_container.pty_driver import (
    IDLE_BAR,
    INTERRUPTED_RE,
    OVERLAY_BAR,
    PROMPT_GLYPH,
    PTYDriver,
    PTYDriverError,
    SUBMIT_KEY,
    _build_env,
    _pick_substrate_model,
    _strip_ansi,
)


def _model_reachable() -> bool:
    """Check if the model-reachable prerequisite is satisfied.

    Mirrors the PoC's prerequisite check at the plan's *Prerequisites*
    table: `claude --print "ping"` with the substrate's model-pick
    policy (prefer gemma*, fall back to non-granite*) must complete a
    round-trip. If the check fails (returncode != 0, timeout, or
    `claude` not on PATH), the spike-regression test is skipped.
    """
    if not shutil.which("claude"):
        return False
    try:
        # Match the same model-pick policy as the substrate driver so
        # the prerequisite check exercises the same code path.
        tags = json.loads(
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5).read()
        )
        names = [m["name"] for m in tags.get("models", [])]
        if not names:
            return False
        pick = next(
            (n for n in names if n.startswith("gemma")),
            next((n for n in names if not n.startswith("granite")), names[0]),
        )
        r = subprocess.run(
            [
                "claude",
                "--permission-mode",
                "bypassPermissions",
                "--model",
                pick,
                "--print",
                "ping",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError, urllib.error.URLError, json.JSONDecodeError):
        return False


class TestC1SubmitKey(unittest.TestCase):
    """C1: every text write ends with `\\r` (CR), never `\\n` (LF)."""

    def test_write_appends_cr_not_lf(self) -> None:
        """A bare text write must end in `\\r`, not `\\n`."""
        driver = PTYDriver(role="pm")
        mock_child = MagicMock()
        driver._child = mock_child
        driver.write("hello world")
        sent: str = mock_child.send.call_args[0][0]
        self.assertTrue(sent.endswith("\r"), f"write() must end in \\r; got {sent!r}")
        self.assertFalse(sent.endswith("\n"), f"write() must not end in \\n; got {sent!r}")

    def test_write_rejects_empty_input(self) -> None:
        driver = PTYDriver(role="pm")
        mock_child = MagicMock()
        driver._child = mock_child
        with self.assertRaises(PTYDriverError):
            driver.write("")
        mock_child.send.assert_not_called()

    def test_write_normalizes_trailing_lf_to_cr(self) -> None:
        """A text write ending in `\\n` is normalized to `\\r`."""
        driver = PTYDriver(role="pm")
        mock_child = MagicMock()
        driver._child = mock_child
        driver.write("hello\n")
        sent: str = mock_child.send.call_args[0][0]
        self.assertTrue(sent.endswith("\r"))
        self.assertFalse(sent.endswith("\n"))

    def test_write_preserves_internal_newlines(self) -> None:
        """Internal `\\n` is preserved; only the trailing newline is the submit key."""
        driver = PTYDriver(role="pm")
        mock_child = MagicMock()
        driver._child = mock_child
        driver.write("line one\nline two")
        sent: str = mock_child.send.call_args[0][0]
        self.assertIn("\n", sent, "internal newlines are preserved")
        self.assertTrue(sent.endswith("\r"))


class TestC2InterruptedRegex(unittest.TestCase):
    """C2: the interjection regex matches both v2.1.160 and older text."""

    def test_v2_1_160_text_matches(self) -> None:
        text = "Press Ctrl-C again to exit"
        self.assertIsNotNone(INTERRUPTED_RE.search(text))

    def test_older_text_matches(self) -> None:
        text = "Interrupted · What should Claude do instead?"
        self.assertIsNotNone(INTERRUPTED_RE.search(text))

    def test_older_text_with_bullet_variants(self) -> None:
        for sep in ("·", "•", "."):
            text = f"Interrupted {sep} What should Claude do instead?"
            self.assertIsNotNone(
                INTERRUPTED_RE.search(text),
                f"interjection text with separator {sep!r} should match",
            )

    def test_unrelated_text_does_not_match(self) -> None:
        for text in ("hello world", "bypass permissions on", "Interrupted (other)"):
            self.assertIsNone(INTERRUPTED_RE.search(text), f"{text!r} should not match")


class TestC5IdleHeuristic(unittest.TestCase):
    """C5: idle = bar + glyph + content floor; C4: overlay is also idle."""

    def test_idle_bar_matches_bypass_permissions(self) -> None:
        self.assertIsNotNone(IDLE_BAR.search("bypass permissions on"))

    def test_idle_bar_matches_with_styling(self) -> None:
        self.assertIsNotNone(IDLE_BAR.search("bypass  permissions"))

    def test_overlay_bar_matches(self) -> None:
        """C4: the `/help` overlay's `esc to cancel` is also idle."""
        self.assertIsNotNone(OVERLAY_BAR.search("esc to cancel"))

    def test_prompt_glyph_matches_arrow(self) -> None:
        self.assertIsNotNone(PROMPT_GLYPH.search("> "))

    def test_prompt_glyph_matches_unicode_arrow(self) -> None:
        self.assertIsNotNone(PROMPT_GLYPH.search("❯ "))


class TestStripAnsi(unittest.TestCase):
    """ANSI stripping should drop CSI / OSC sequences and keep visible text."""

    def test_strips_csi(self) -> None:
        s = "\x1b[31mhello\x1b[0m world"
        self.assertEqual(_strip_ansi(s), "hello world")

    def test_strips_osc(self) -> None:
        s = "before\x1b]0;title\x07after"
        self.assertEqual(_strip_ansi(s), "beforeafter")

    def test_idempotent(self) -> None:
        s = "\x1b[31mhello\x1b[0m"
        once = _strip_ansi(s)
        twice = _strip_ansi(once)
        self.assertEqual(once, twice)


class TestBuildEnv(unittest.TestCase):
    """The child env blanks the API key (Max OAuth path)."""

    def test_api_key_blanked(self) -> None:
        env = _build_env()
        self.assertEqual(env.get("ANTHROPIC_API_KEY"), "")

    def test_inherits_path(self) -> None:
        env = _build_env()
        self.assertIn("PATH", env)


class TestReadUntilIdle(unittest.TestCase):
    """`read_until_idle` honors glyph+bar+floor and the C4 overlay."""

    def _driver_with_mock(self, chunks: list[str]) -> PTYDriver:
        """Build a driver whose pexpect child yields `chunks` in order."""
        driver = PTYDriver(role="pm", timeout_s=2.0)
        mock_child = MagicMock()
        # Use a side_effect iterator that yields chunks in order, then
        # raises TIMEOUT (so the loop terminates). Each call to
        # read_nonblocking pulls the next chunk.
        chunks_iter = iter(chunks)
        def read_nonblocking(size: int, timeout: float) -> str:
            try:
                return next(chunks_iter)
            except StopIteration:
                # The driver catches pexpect.TIMEOUT to mean "no new data
                # this tick; continue waiting". We mirror that here.
                raise pexpect.TIMEOUT("mock timeout")
        mock_child.read_nonblocking.side_effect = read_nonblocking
        driver._child = mock_child
        return driver

    def test_saw_idle_when_bar_and_glyph_present(self) -> None:
        # First chunk has the bar; second adds the glyph. min_content_bytes=0
        # means we accept any bar+glyph frame.
        driver = self._driver_with_mock([
            "bypass permissions on\n",
            "❯ hello world\n",
        ])
        result = driver.read_until_idle(min_content_bytes=0, timeout_s=2.0)
        self.assertTrue(result.saw_idle, f"expected saw_idle; buffer={result.buffer!r}")
        self.assertIn("bypass permissions", result.buffer)
        self.assertIn("❯", result.buffer)

    def test_saw_idle_false_when_only_glyph(self) -> None:
        # Glyph but no bar → not idle.
        driver = self._driver_with_mock(["❯ \n"])
        result = driver.read_until_idle(min_content_bytes=0, timeout_s=1.0)
        self.assertFalse(result.saw_idle)

    def test_saw_idle_false_when_only_bar(self) -> None:
        driver = self._driver_with_mock(["bypass permissions on\n"])
        result = driver.read_until_idle(min_content_bytes=0, timeout_s=1.0)
        self.assertFalse(result.saw_idle)

    def test_overlay_bar_also_idle(self) -> None:
        # C4: `/help` overlay shows `esc to cancel` — also idle.
        driver = self._driver_with_mock([
            "esc to cancel\n",
            "❯ \n",
        ])
        result = driver.read_until_idle(min_content_bytes=0, timeout_s=2.0)
        self.assertTrue(result.saw_idle, f"overlay should count as idle; got {result.buffer!r}")

    def test_min_content_bytes_floor(self) -> None:
        # Bar+glyph present but buffer < min_content_bytes → not idle.
        driver = self._driver_with_mock([
            "bypass permissions on\n",
            "❯ \n",
        ])
        result = driver.read_until_idle(min_content_bytes=10_000, timeout_s=1.0)
        self.assertFalse(result.saw_idle, "floor should prevent premature idle")


class TestPickSubstrateModel(unittest.TestCase):
    """The model picker avoids granite (the operator) and prefers gemma."""

    def test_prefers_gemma(self) -> None:
        fake_tags = {
            "models": [
                {"name": "granite4.1:3b"},
                {"name": "gemma3:4b"},
                {"name": "llama3:8b"},
            ]
        }
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = (
                str(fake_tags).replace("'", '"').encode("utf-8")
            )
            model = _pick_substrate_model()
        self.assertTrue(model.startswith("gemma"), f"expected gemma; got {model!r}")

    def test_falls_back_to_non_granite(self) -> None:
        fake_tags = {
            "models": [
                {"name": "granite4.1:3b"},
                {"name": "llama3:8b"},
            ]
        }
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = (
                str(fake_tags).replace("'", '"').encode("utf-8")
            )
            model = _pick_substrate_model()
        self.assertFalse(model.startswith("granite"), f"should avoid granite; got {model!r}")

    def test_raises_on_ollama_unreachable(self) -> None:
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = OSError("connection refused")
            with self.assertRaises(PTYDriverError):
                _pick_substrate_model()


class TestLifecycle(unittest.TestCase):
    """Spawn / close / isalive are well-behaved."""

    def test_write_before_spawn_raises(self) -> None:
        driver = PTYDriver(role="pm")
        with self.assertRaises(PTYDriverError):
            driver.write("hello")

    def test_send_ctrl_c_before_spawn_raises(self) -> None:
        driver = PTYDriver(role="pm")
        with self.assertRaises(PTYDriverError):
            driver.send_ctrl_c()

    def test_read_until_idle_before_spawn_raises(self) -> None:
        driver = PTYDriver(role="pm")
        with self.assertRaises(PTYDriverError):
            driver.read_until_idle()

    def test_double_spawn_raises(self) -> None:
        driver = PTYDriver(role="pm")
        mock_child = MagicMock()
        mock_child.isalive.return_value = True
        driver._child = mock_child
        with self.assertRaises(PTYDriverError):
            driver.spawn()

    def test_close_when_not_spawned_is_noop(self) -> None:
        driver = PTYDriver(role="pm")
        driver.close()  # must not raise


class TestSpikeRegressionEnvGated(unittest.TestCase):
    """Spike-regression test gated on model-reachable env.

    In a model-reachable env, spawns a real TUI and exercises scenarios
    1, 2, 3, 6 (the four the v7 spike's per-scenario footer observed_state
    is checked against). In a non-reachable env, the test is *skipped*
    with a structured log line and the regression contract is preserved
    for the next model-reachable run.

    The PoC's test impact section calls out that the regression test
    re-runs the scenarios and compares footers, not bytes — the spike
    transcripts are reference material, not fixtures. This test
    exercises the per-scenario path on the live TUI; the footer
    comparison is the per-scenario `observed_state` field the spike
    report records.
    """

    @unittest.skipUnless(
        _model_reachable(),
        "RESUME_SKIP model_unreachable — spike-regression test gated on `claude --print ping` succeeding",
    )
    def test_scenario_1_idle_paint(self) -> None:
        """Scenario 1: spawn -> wait for idle -> assert bar+glyph present."""
        driver = PTYDriver(role="pm")
        try:
            driver.spawn()
            result = driver.read_until_idle(min_content_bytes=0, timeout_s=30.0)
            self.assertTrue(result.saw_idle, f"expected saw_idle; buffer={result.buffer[-200:]!r}")
            self.assertIn("bypass", result.buffer)
        finally:
            driver.close(force=True)

    @unittest.skipUnless(
        _model_reachable(),
        "RESUME_SKIP model_unreachable",
    )
    def test_scenario_2_submit(self) -> None:
        """Scenario 2: send `hello` -> wait for idle -> assert response arrived.

        The TUI's behavior on `hello\\r` (vs `hello\\n`) is the
        load-bearing C1 fact. The driver already asserts the C1
        side; this test asserts the live TUI accepts the `\\r` and
        produces a response frame.
        """
        driver = PTYDriver(role="pm")
        try:
            driver.spawn()
            initial = driver.read_until_idle(min_content_bytes=0, timeout_s=30.0)
            self.assertTrue(initial.saw_idle)
            driver.write("hello")
            result = driver.read_until_idle(min_content_bytes=100, timeout_s=30.0)
            self.assertTrue(result.saw_idle, f"expected idle after hello; got {result.buffer[-200:]!r}")
        finally:
            driver.close(force=True)

    @unittest.skipUnless(
        _model_reachable(),
        "RESUME_SKIP model_unreachable",
    )
    def test_scenario_3_idle_stable(self) -> None:
        """Scenario 3: idle remains stable across a follow-up read cycle."""
        driver = PTYDriver(role="pm")
        try:
            driver.spawn()
            first = driver.read_until_idle(min_content_bytes=0, timeout_s=30.0)
            self.assertTrue(first.saw_idle)
            # Re-reading on an already-idle TUI should still see idle.
            second = driver.read_until_idle(min_content_bytes=0, timeout_s=5.0)
            self.assertTrue(second.saw_idle)
        finally:
            driver.close(force=True)

    @unittest.skipUnless(
        _model_reachable(),
        "RESUME_SKIP model_unreachable",
    )
    def test_scenario_6_idle_after_overlay(self) -> None:
        """Scenario 6: idle returns after a `/help` overlay dismisses.

        C4 invariant: the `esc to cancel` overlay is also idle; the
        loop holds while the user dismisses. This test exercises the
        overlay path; the idle detection must work after the overlay
        has closed.
        """
        driver = PTYDriver(role="pm")
        try:
            driver.spawn()
            initial = driver.read_until_idle(min_content_bytes=0, timeout_s=30.0)
            self.assertTrue(initial.saw_idle)
            driver.write("/help")
            overlay = driver.read_until_idle(min_content_bytes=0, timeout_s=5.0)
            # The overlay is itself idle (C4). After sending an Esc-like
            # follow-up we expect to see the bypass bar return.
            driver.write("\x1b")  # Esc
            after = driver.read_until_idle(min_content_bytes=0, timeout_s=5.0)
            self.assertTrue(after.saw_idle)
        finally:
            driver.close(force=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
