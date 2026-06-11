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
import shutil
import subprocess
import unittest
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
    _build_env,
    _default_substrate_model,
    _strip_ansi,
)


def _model_reachable() -> bool:
    """Check if the live spike-regression tests may run.

    Gated on GRANITE_LIVE_SMOKE=1 (explicit operator opt-in) BEFORE any
    process is spawned: this function runs at module import time (it is
    a ``@skipUnless`` decorator argument), so without the gate merely
    collecting this module spawned a real ``claude --print`` round-trip
    that orphaned ~250MB processes (issue #1632 mode 3). The conftest
    spawn guard cannot intercept import-time spawns, hence the env gate.

    With the opt-in set, mirrors the PoC's prerequisite check:
    `claude --print "ping"` with the driver's default substrate model
    must complete a round-trip; otherwise the tests are skipped.

    The result is cached for the module's lifetime so all four
    env-gated tests see the same value (avoiding per-test races when
    pytest-xdist forks workers and each forks a different `claude`
    subprocess).
    """
    if not hasattr(_model_reachable, "_cache"):
        _model_reachable._cache = _model_reachable_check()
    return _model_reachable._cache


def _model_reachable_check() -> bool:
    if os.environ.get("GRANITE_LIVE_SMOKE") != "1":
        return False
    if not shutil.which("claude"):
        return False
    try:
        # The PTY substrate is the Claude subscription (OAuth), not ollama.
        # Ping with the same default Dev alias the driver spawns so the
        # prerequisite check exercises the real substrate path.
        r = subprocess.run(
            [
                "claude",
                "--permission-mode",
                "bypassPermissions",
                "--model",
                _default_substrate_model("dev"),
                "--print",
                "ping",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
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
        """C4: the `/help` overlay's `esc to cancel` is also idle.

        Matches both the spaced hint and the collapsed-whitespace form
        the TUI actually renders (`Esc to cancel` → `Esctocancel`).
        """
        self.assertIsNotNone(OVERLAY_BAR.search("esc to cancel"))
        self.assertIsNotNone(OVERLAY_BAR.search("esctocancel"))

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
        driver = self._driver_with_mock(
            [
                "bypass permissions on\n",
                "❯ hello world\n",
            ]
        )
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
        driver = self._driver_with_mock(
            [
                "esc to cancel\n",
                "❯ \n",
            ]
        )
        result = driver.read_until_idle(min_content_bytes=0, timeout_s=2.0)
        self.assertTrue(result.saw_idle, f"overlay should count as idle; got {result.buffer!r}")

    def test_min_content_bytes_floor(self) -> None:
        # Bar+glyph present but buffer < min_content_bytes → not idle.
        driver = self._driver_with_mock(
            [
                "bypass permissions on\n",
                "❯ \n",
            ]
        )
        result = driver.read_until_idle(min_content_bytes=10_000, timeout_s=1.0)
        self.assertFalse(result.saw_idle, "floor should prevent premature idle")


class TestDefaultSubstrateModel(unittest.TestCase):
    """The PTY substrate is the Claude subscription, model chosen by role.

    ollama is the granite *classifier's* substrate — never the PTY's. The
    resolver returns unpinned Claude aliases (opus/sonnet) from settings.
    """

    def test_pm_defaults_to_opus(self) -> None:
        self.assertEqual(_default_substrate_model("pm"), "opus")

    def test_dev_defaults_to_sonnet(self) -> None:
        self.assertEqual(_default_substrate_model("dev"), "sonnet")

    def test_reads_settings_override(self) -> None:
        """The resolver reflects GRANITE__PM_MODEL / DEV_MODEL via settings."""
        from config.settings import settings

        with (
            patch.object(settings.granite, "pm_model", "haiku"),
            patch.object(settings.granite, "dev_model", "opus"),
        ):
            self.assertEqual(_default_substrate_model("pm"), "haiku")
            self.assertEqual(_default_substrate_model("dev"), "opus")

    def test_never_returns_ollama_identity(self) -> None:
        """No `:cloud` / `granite` / `gemma` tags — those belong to the classifier."""
        for role in ("pm", "dev"):
            model = _default_substrate_model(role)
            self.assertNotIn(":", model, f"alias must be unpinned/untagged; got {model!r}")
            self.assertFalse(model.startswith(("granite", "gemma", "glm")))

    def test_fallback_map_is_claude_aliases(self) -> None:
        """The except-branch fallback (settings unavailable) yields Claude aliases."""
        from agent.granite_container.pty_driver import _FALLBACK_SUBSTRATE_MODEL

        self.assertEqual(_FALLBACK_SUBSTRATE_MODEL["pm"], "opus")
        self.assertEqual(_FALLBACK_SUBSTRATE_MODEL["dev"], "sonnet")


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
        "RESUME_SKIP model_unreachable: spike-regression gated on GRANITE_LIVE_SMOKE=1",
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
            self.assertTrue(
                result.saw_idle, f"expected idle after hello; got {result.buffer[-200:]!r}"
            )
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
        """Scenario 6: the `/help` overlay is itself idle (C4 invariant).

        The C4 contract is that idle detection treats the overlay as
        idle (bottom bar reads `esc to cancel` instead of the bypass
        bar; the prompt glyph is still there). The test does NOT
        assert that Esc dismisses the overlay; the spike's contract
        (scripts/granite_tui_pty_spike_pexpect.py:686-715) is that
        the overlay either renders or returns to idle within the
        timeout, and either is a pass.
        """
        driver = PTYDriver(role="pm")
        try:
            driver.spawn()
            initial = driver.read_until_idle(min_content_bytes=0, timeout_s=30.0)
            self.assertTrue(initial.saw_idle)
            driver.write("/help")
            result = driver.read_until_idle(min_content_bytes=0, timeout_s=10.0)
            # Spike contract (scripts/granite_tui_pty_spike_pexpect.py:700):
            #   `if saw_idle_after or help_seen: passed = True`
            # Either the TUI returned to idle OR the help overlay
            # rendered — either one is a pass. We mirror that OR exactly.
            #
            # `help_seen` in the spike is the "Esc to cancel" overlay
            # hint. The TUI's overlay rendering collapses whitespace
            # (`Esc to cancel` → `Esctocancel`); the kernel strips
            # whitespace before matching the bar regex, so the marker
            # can be either the spaced or collapsed form.
            buf = result.buffer.lower()
            overlay_visible = "esctocancel" in buf or "esc to cancel" in buf
            self.assertTrue(
                result.saw_idle or overlay_visible,
                f"expected return-to-idle or overlay (esctocancel) after /help; "
                f"saw_idle={result.saw_idle} buffer_tail={result.buffer[-200:]!r}",
            )
        finally:
            driver.close(force=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
