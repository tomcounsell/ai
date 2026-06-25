"""Tests for the granite classifier (PoC #1546).

The classifier has one primary surface:
  - `classify_pm_prefix`: a deterministic regex parse on the first
    line of PM's tail. Fully unit-testable; no ollama call.

The LLM-based translation functions (`extract_dev_prompt`, `summarize_for_pm`,
`TRANSLATION_TOOLS`, `SYSTEM_PROMPT`, `GraniteTranslationError`) were deleted
in the zero-LLM shuttle (PR #1686) — the container now reads PM/Dev output
verbatim from JSONL transcripts via `last_assistant_text`.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from agent.granite_container.granite_classifier import (
    classify_pm_prefix,
    ensure_granite_model,
)

# ---------------------------------------------------------------------------
# classify_pm_prefix: deterministic regex parse
# ---------------------------------------------------------------------------


class TestClassifyPmPrefix(unittest.TestCase):
    """The strict regex parses the first line; the fallback handles light drift."""

    def test_dev_token_strict(self) -> None:
        result = classify_pm_prefix("[/dev]\nadd a function `foo` to bar.py")
        self.assertEqual(result.destination, "dev")
        self.assertFalse(result.compliance_miss)
        self.assertIn("add a function", result.payload)

    def test_user_token_strict(self) -> None:
        result = classify_pm_prefix("[/user]\nThe plan is on track; will report back at EOD.")
        self.assertEqual(result.destination, "user")
        self.assertFalse(result.compliance_miss)

    def test_complete_token_strict(self) -> None:
        result = classify_pm_prefix("[/complete]\nShipped PR #42; tests pass.")
        self.assertEqual(result.destination, "complete")
        self.assertFalse(result.compliance_miss)

    def test_first_line_whitespace_strict(self) -> None:
        """A leading-space prefix is a compliance miss (strict regex requires start-of-line)."""
        result = classify_pm_prefix(" [/dev]\nadd a function `foo`")
        self.assertTrue(result.compliance_miss)
        # The fallback should still classify the token.
        self.assertEqual(result.destination, "dev")

    def test_token_with_garbage_after_strict(self) -> None:
        """A line like `[/dev] extra text` is a strict miss; the fallback recovers."""
        result = classify_pm_prefix("[/dev] extra text\nthe rest")
        self.assertTrue(result.compliance_miss)
        self.assertEqual(result.destination, "dev")

    def test_unknown_token(self) -> None:
        result = classify_pm_prefix("[/unknown]\nsome text")
        self.assertEqual(result.destination, "unknown")
        self.assertTrue(result.compliance_miss)

    def test_no_token(self) -> None:
        result = classify_pm_prefix("I think the user wants X.")
        self.assertEqual(result.destination, "unknown")
        self.assertTrue(result.compliance_miss)

    def test_empty_input(self) -> None:
        result = classify_pm_prefix("")
        self.assertEqual(result.destination, "unknown")
        self.assertTrue(result.compliance_miss)
        self.assertEqual(result.raw_first_line, "")

    def test_whitespace_only_input(self) -> None:
        result = classify_pm_prefix("   \n   \n")
        self.assertEqual(result.destination, "unknown")
        self.assertTrue(result.compliance_miss)

    def test_multiline_with_prefix(self) -> None:
        """The first non-empty line is the prefix; subsequent lines are the payload."""
        pm_tail = "   \n[/dev]\nadd a function `foo`\nto bar.py\n"
        result = classify_pm_prefix(pm_tail)
        self.assertEqual(result.destination, "dev")
        self.assertFalse(result.compliance_miss)
        self.assertIn("add a function", result.payload)
        self.assertIn("to bar.py", result.payload)

    def test_fallback_within_first_200_chars(self) -> None:
        """The fallback regex looks at the first 200 chars only (latency vs. accuracy)."""
        # Pad 250 chars of unrelated text, then the token.
        padding = "x" * 250
        result = classify_pm_prefix(f"{padding}[/user]\nthe user-facing message")
        # The token is past the 200-char window; the fallback does NOT
        # catch it. This is intentional — the classifier is a fast
        # first-line check, not a deep parse.
        self.assertEqual(result.destination, "unknown")
        self.assertTrue(result.compliance_miss)

    def test_clean_synthetic_input_unaffected(self) -> None:
        """Inputs without a bullet marker keep the strict first-line path."""
        result = classify_pm_prefix("[/dev]\nadd a function `foo` to bar.py")
        self.assertEqual(result.destination, "dev")
        self.assertFalse(result.compliance_miss)

    def test_ordering_attack_first_line_dev_body_has_complete_still_routes_dev(self) -> None:
        """PM first line is [/dev], body has literal ⏺ [/complete] — must still route dev.

        With the anchored-frame path deleted, classification is strict first-line only.
        A mid-body echoed token cannot hijack routing.
        """
        text = "[/dev] Do the work\n\n⏺ [/complete] (echoed from Dev output)\n\nMore content."
        result = classify_pm_prefix(text)
        self.assertEqual(result.destination, "dev")

    # -- Backward compatibility: bare tokens must still work with harness=None --

    def test_dev_bare_token_harness_none(self) -> None:
        """[/dev] with no harness suffix produces harness=None (backward compat)."""
        result = classify_pm_prefix("[/dev]\nadd a function `foo` to bar.py")
        self.assertEqual(result.destination, "dev")
        self.assertFalse(result.compliance_miss)
        self.assertIsNone(result.harness)

    def test_user_bare_token_harness_none(self) -> None:
        """[/user] token always yields harness=None."""
        result = classify_pm_prefix("[/user]\nThe plan is on track.")
        self.assertEqual(result.destination, "user")
        self.assertIsNone(result.harness)

    def test_complete_bare_token_harness_none(self) -> None:
        """[/complete] token always yields harness=None."""
        result = classify_pm_prefix("[/complete]\nShipped PR #42.")
        self.assertEqual(result.destination, "complete")
        self.assertIsNone(result.harness)

    # -- Harness suffix strict match --

    def test_dev_pi_harness_strict(self) -> None:
        """[/dev:pi] strict match → destination=dev, harness='pi', compliance_miss=False."""
        result = classify_pm_prefix("[/dev:pi]\nbuild it")
        self.assertEqual(result.destination, "dev")
        self.assertEqual(result.harness, "pi")
        self.assertFalse(result.compliance_miss)
        self.assertIn("build it", result.payload)

    def test_dev_claude_harness_strict(self) -> None:
        """[/dev:claude] explicit suffix → harness='claude', compliance_miss=False."""
        result = classify_pm_prefix("[/dev:claude]\ndo the refactor")
        self.assertEqual(result.destination, "dev")
        self.assertEqual(result.harness, "claude")
        self.assertFalse(result.compliance_miss)

    def test_dev_unknown_harness_strict(self) -> None:
        """[/dev:unknown] strict → harness='unknown', destination='dev'."""
        result = classify_pm_prefix("[/dev:unknown]\nsome instruction")
        self.assertEqual(result.destination, "dev")
        self.assertEqual(result.harness, "unknown")
        self.assertFalse(result.compliance_miss)

    # -- Harness suffix fallback match (compliance_miss=True) --

    def test_dev_pi_harness_fallback_mid_line(self) -> None:
        """Mid-line token 'output: [/dev:pi] please build' → dev, harness='pi', miss=True."""
        result = classify_pm_prefix("output: [/dev:pi] please build")
        self.assertEqual(result.destination, "dev")
        self.assertEqual(result.harness, "pi")
        self.assertTrue(result.compliance_miss)

    def test_dev_pi_harness_fallback_leading_whitespace(self) -> None:
        """Leading whitespace before [/dev:pi] is a strict miss; fallback recovers harness."""
        result = classify_pm_prefix("  [/dev:pi] some instruction")
        self.assertEqual(result.destination, "dev")
        self.assertEqual(result.harness, "pi")
        self.assertTrue(result.compliance_miss)

    def test_dev_pi_harness_fallback_mid_text(self) -> None:
        """Newline before [/dev:pi] text — fallback recovers harness and compliance_miss=True."""
        result = classify_pm_prefix("\n[/dev:pi] text")
        self.assertEqual(result.destination, "dev")
        self.assertEqual(result.harness, "pi")
        self.assertTrue(result.compliance_miss)


# ---------------------------------------------------------------------------
# ANSI stripping: defense-in-depth
# ---------------------------------------------------------------------------
#
# Synthetic coverage. Time-shifted regressions (TUI version drift, Ink/React
# upgrades) may surface only on real TUI runs; the live smoke test in the
# cutover plan is the second-line defense. Schedule a second live smoke
# test ~24 hours after the first to catch time-shifted regressions.
#
# The classifier delegates to pty_driver._strip_ansi so the two layers
# cannot drift. These tests pin the behavior at the classifier boundary.
# ---------------------------------------------------------------------------


class TestAnsiStripping(unittest.TestCase):
    """ANSI escape sequences must not corrupt the prefix-token classification.

    The PTY layer's read_until_idle already strips CSI+OSC, but defense
    in depth at the classifier catches time-shifted TUI upgrades.
    """

    def test_strip_csi_does_not_corrupt_classification(self) -> None:
        """Leading CSI SGR sequences (color codes) survive the PTY strip
        in some TUI versions and would corrupt the first-line check."""
        # \x1b[31m = red, \x1b[0m = reset
        result = classify_pm_prefix("\x1b[31m[/dev]\x1b[0m\nadd a function `foo` to bar.py")
        self.assertEqual(result.destination, "dev")
        self.assertFalse(result.compliance_miss)
        self.assertIn("add a function", result.payload)

    def test_strip_osc_does_not_corrupt_classification(self) -> None:
        """Leading OSC sequence (`ESC]0;titleBEL`) sets the window title.
        The PTY strip removes it; the classifier must remain correct."""
        # \x1b]0;title\x07 = OSC set window title
        result = classify_pm_prefix("\x1b]0;title\x07[/dev]\nadd a function `foo` to bar.py")
        self.assertEqual(result.destination, "dev")
        self.assertFalse(result.compliance_miss)
        self.assertIn("add a function", result.payload)

    def test_strip_keypad_does_not_corrupt_classification(self) -> None:
        """Leading keypad-mode ESC (`ESC=`) is a single-char ESC control.
        The PTY strip removes it; the classifier must remain correct."""
        # \x1b= = application keypad mode
        result = classify_pm_prefix("\x1b=[/dev]\nadd a function `foo` to bar.py")
        self.assertEqual(result.destination, "dev")
        self.assertFalse(result.compliance_miss)
        self.assertIn("add a function", result.payload)


class TestCursorPositionedSpacingSurvives(unittest.TestCase):
    """Issue #1634: inter-word spacing painted as cursor-advance CSI
    sequences must survive the screen-capture -> payload reconstruction.

    The Ink/React TUI renderer paints `word1 word2` as
    `word1<cursor-forward>word2` — the gap lives entirely in the escape
    sequence, never as a literal space. The original `_strip_ansi` deleted
    those CSI sequences outright, collapsing the words (`word1word2`) and
    delivering space-stripped text to CEO-facing chats. The fix translates
    cursor-advance sequences (CUF `\\x1b[NC`, CHA `\\x1b[NG`) to spaces
    before the blanket CSI strip. These tests pin that invariant at the
    classifier boundary — the same boundary that feeds the outbox.
    """

    def test_cursor_forward_preserves_interior_spaces(self) -> None:
        """CUF (`\\x1b[1C`) between words must reconstruct as a space, not vanish."""
        from agent.granite_container.pty_driver import _strip_ansi

        painted = "Online\x1b[1Cand\x1b[1Cready."
        self.assertEqual(_strip_ansi(painted), "Online and ready.")

    def test_cursor_forward_count_maps_to_n_spaces(self) -> None:
        """A CUF with an explicit count (`\\x1b[3C`) advances N columns -> N spaces."""
        from agent.granite_container.pty_driver import _strip_ansi

        self.assertEqual(_strip_ansi("a\x1b[3Cb"), "a   b")

    def test_bare_cursor_forward_maps_to_one_space(self) -> None:
        """A countless CUF (`\\x1b[C`) advances one column -> one space."""
        from agent.granite_container.pty_driver import _strip_ansi

        self.assertEqual(_strip_ansi("a\x1b[Cb"), "a b")

    def test_cursor_horizontal_absolute_preserves_word_boundary(self) -> None:
        """CHA (`\\x1b[NG`) jumps to an absolute column; reconstruction keeps
        the word boundary as a space rather than collapsing the words."""
        from agent.granite_container.pty_driver import _strip_ansi

        self.assertEqual(_strip_ansi("Online\x1b[7Gand\x1b[11Gready."), "Online and ready.")

    def test_strip_is_idempotent_after_cursor_expansion(self) -> None:
        """Re-stripping already-reconstructed text is a no-op (the helper is
        called at several points in the read path)."""
        from agent.granite_container.pty_driver import _strip_ansi

        once = _strip_ansi("Online\x1b[1Cand\x1b[1Cready.")
        self.assertEqual(_strip_ansi(once), once)

    def test_realistic_strip_ansi_on_pm_text_keeps_spaces(self) -> None:
        """ANSI cursor-forward sequences in PM transcript text must reconstruct
        as spaces, not collapse words (issue #1634).

        With the zero-LLM shuttle, the container classifies PM's JSONL transcript
        text via `last_assistant_text` (not the painted PTY buffer). This test
        verifies that _strip_ansi correctly expands cursor-advance CSI sequences
        to spaces — the same strip is applied to PM text before classification.
        """
        from agent.granite_container.pty_driver import _strip_ansi

        # Simulate a PM transcript text with cursor-advance paint sequences.
        pm_text_with_csi = (
            "[/user]\x1b[2COnline\x1b[1Cand\x1b[1Crouting\x1b[1C—"
            "\x1b[1CPM\x1b[1Csession\x1b[1Cfor\x1b[1CPR\x1b[1C#1612\x1b[1Cis\x1b[1Clive."
        )
        stripped = _strip_ansi(pm_text_with_csi)
        # After strip, [/user] token is intact and words are separated by spaces.
        self.assertTrue(stripped.startswith("[/user]"))
        self.assertIn("Online and routing", stripped)
        self.assertIn("PM session", stripped)
        self.assertNotIn("Onlineand", stripped)
        self.assertNotIn("PMsession", stripped)
        # The stripped text classifies correctly.
        # Note: the \x1b[2C expands to 2 spaces, making the first line
        # "[/user]  Online..." which triggers a fallback compliance miss
        # (token not alone on its line). The destination is still "user".
        result = classify_pm_prefix(stripped)
        self.assertEqual(result.destination, "user")
        self.assertIn("Online and routing", result.payload)


# ---------------------------------------------------------------------------
# ensure_granite_model: startup precondition (mocked subprocess / ollama)
# ---------------------------------------------------------------------------

_CLS = "agent.granite_container.granite_classifier"


def _ok_probe() -> MagicMock:
    """A successful `ollama run` result (returncode 0, non-empty stdout)."""
    return MagicMock(returncode=0, stdout="ready", stderr="")


def _bad_probe() -> MagicMock:
    """A failed `ollama run` result (model not found / empty output)."""
    return MagicMock(returncode=1, stdout="", stderr="model not found")


class TestEnsureGraniteModel(unittest.TestCase):
    """The hard startup precondition: granite present + responsive.

    `ensure_granite_model` checks that `import ollama` works (the
    granite TUI runner needs the ollama client), the CLI is on PATH,
    and the model answers a probe. The function no longer uses an
    `ollama_chat` module-level alias — patch `builtins.__import__` for
    the importability check and patch `shutil.which` + `subprocess.run`
    for the CLI/probe checks.
    """

    def test_returns_false_when_client_unimportable(self) -> None:
        import builtins

        real_import = builtins.__import__

        def _no_ollama(name, *args, **kwargs):
            if name == "ollama":
                raise ImportError("no module named ollama")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_no_ollama):
            ok, detail = ensure_granite_model()
        self.assertFalse(ok)
        self.assertIn("python client", detail)

    def test_returns_false_when_cli_absent(self) -> None:
        with patch(f"{_CLS}.shutil.which", return_value=None):
            ok, detail = ensure_granite_model()
        self.assertFalse(ok)
        self.assertIn("CLI not found", detail)

    def test_ok_when_first_probe_responsive(self) -> None:
        with (
            patch(f"{_CLS}.shutil.which", return_value="/usr/local/bin/ollama"),
            patch(f"{_CLS}.subprocess.run", return_value=_ok_probe()) as run,
        ):
            ok, detail = ensure_granite_model()
        self.assertTrue(ok)
        self.assertIn("responsive", detail)
        # Only the probe ran — no pull when the model already answers.
        self.assertEqual(run.call_count, 1)

    def test_pulls_then_succeeds_when_model_missing(self) -> None:
        # First probe fails, pull succeeds, second probe succeeds.
        side_effects = [_bad_probe(), MagicMock(returncode=0, stdout="", stderr=""), _ok_probe()]
        with (
            patch(f"{_CLS}.shutil.which", return_value="/usr/local/bin/ollama"),
            patch(f"{_CLS}.subprocess.run", side_effect=side_effects) as run,
        ):
            ok, detail = ensure_granite_model()
        self.assertTrue(ok)
        self.assertIn("pulled", detail)
        # probe → pull → probe
        self.assertEqual(run.call_count, 3)

    def test_fails_when_pull_fails(self) -> None:
        import subprocess

        def _runner(cmd, **kwargs):
            if cmd[1] == "pull":
                raise subprocess.CalledProcessError(1, cmd, stderr="pull boom")
            return _bad_probe()

        with (
            patch(f"{_CLS}.shutil.which", return_value="/usr/local/bin/ollama"),
            patch(f"{_CLS}.subprocess.run", side_effect=_runner),
        ):
            ok, detail = ensure_granite_model()
        self.assertFalse(ok)
        self.assertIn("pull", detail)

    def test_no_pull_when_disabled(self) -> None:
        with (
            patch(f"{_CLS}.shutil.which", return_value="/usr/local/bin/ollama"),
            patch(f"{_CLS}.subprocess.run", return_value=_bad_probe()) as run,
        ):
            ok, detail = ensure_granite_model(pull_if_missing=False)
        self.assertFalse(ok)
        self.assertEqual(run.call_count, 1)  # probe only, no pull attempt

    def test_probe_timeout_treated_as_not_responsive(self) -> None:
        import subprocess

        with (
            patch(f"{_CLS}.shutil.which", return_value="/usr/local/bin/ollama"),
            patch(
                f"{_CLS}.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="ollama", timeout=60),
            ),
        ):
            ok, _ = ensure_granite_model(pull_if_missing=False)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
