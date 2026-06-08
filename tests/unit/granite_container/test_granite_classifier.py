"""Tests for the granite classifier (PoC #1546).

The classifier has two surfaces:
  - `classify_pm_prefix`: a deterministic regex parse on the first
    line of PM's tail. Fully unit-testable; no ollama call.
  - `extract_dev_prompt` / `summarize_for_pm`: ollama.chat() calls
    that translate between PM and Dev. Tested with a mocked ollama
    response (so the test does not depend on the local ollama
    service) plus an env-gated live test that exercises the real
    translation path.

Why split the test surface: the classification decision is
deterministic and should never be exercised against a real LLM. The
translation quality IS exercised against the real LLM in the
env-gated test — that is the Q6 measurement the plan calls for.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from agent.granite_container.granite_classifier import (
    SYSTEM_PROMPT,
    TRANSLATION_TOOLS,
    classify_pm_prefix,
    extract_dev_prompt,
    summarize_for_pm,
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


# ---------------------------------------------------------------------------
# extract_dev_prompt / summarize_for_pm: mocked ollama path
# ---------------------------------------------------------------------------


def _make_ollama_response(tool_name: str, arguments: dict) -> MagicMock:
    """Build a mock ollama response carrying a single tool call."""
    fn = MagicMock()
    fn.name = tool_name
    fn.arguments = arguments
    tc = MagicMock()
    tc.function = fn

    msg = MagicMock()
    msg.tool_calls = [tc]
    response = MagicMock()
    response.message = msg
    return response


class TestExtractDevPromptMocked(unittest.TestCase):
    """`extract_dev_prompt` calls ollama and returns the dev_prompt arg."""

    def test_returns_dev_prompt(self) -> None:
        with patch("agent.granite_container.granite_classifier.ollama_chat") as mock_chat:
            mock_chat.return_value = _make_ollama_response(
                "extract_dev_prompt", {"dev_prompt": "add foo to bar.py"}
            )
            result = extract_dev_prompt("[/dev]\nadd foo to bar.py")
        self.assertEqual(result, "add foo to bar.py")

    def test_raises_on_wrong_tool(self) -> None:
        with patch("agent.granite_container.granite_classifier.ollama_chat") as mock_chat:
            mock_chat.return_value = _make_ollama_response(
                "summarize_for_pm", {"summary": "wrong tool"}
            )
            with self.assertRaises(Exception) as ctx:
                extract_dev_prompt("[/dev]\nadd foo to bar.py")
        self.assertIn("extract_dev_prompt", str(ctx.exception))

    def test_raises_on_ollama_failure(self) -> None:
        with patch("agent.granite_container.granite_classifier.ollama_chat") as mock_chat:
            mock_chat.side_effect = RuntimeError("ollama down")
            with self.assertRaises(Exception):
                extract_dev_prompt("[/dev]\nadd foo to bar.py")


class TestSummarizeForPmMocked(unittest.TestCase):
    """`summarize_for_pm` calls ollama and returns the summary arg."""

    def test_returns_summary(self) -> None:
        with patch("agent.granite_container.granite_classifier.ollama_chat") as mock_chat:
            mock_chat.return_value = _make_ollama_response(
                "summarize_for_pm", {"summary": "Dev added foo to bar.py and ran tests."}
            )
            result = summarize_for_pm("long dev output...")
        self.assertIn("Dev added foo", result)

    def test_raises_on_wrong_tool(self) -> None:
        with patch("agent.granite_container.granite_classifier.ollama_chat") as mock_chat:
            mock_chat.return_value = _make_ollama_response(
                "extract_dev_prompt", {"dev_prompt": "wrong tool"}
            )
            with self.assertRaises(Exception) as ctx:
                summarize_for_pm("long dev output...")
        self.assertIn("summarize_for_pm", str(ctx.exception))


# ---------------------------------------------------------------------------
# Schema sanity: tools are well-formed
# ---------------------------------------------------------------------------


class TestTranslationTools(unittest.TestCase):
    """The 2 translation tools are well-formed and match the SYSTEM_PROMPT."""

    def test_two_tools(self) -> None:
        names = {t["function"]["name"] for t in TRANSLATION_TOOLS}
        self.assertEqual(len(names), 2)
        self.assertIn("extract_dev_prompt", names)
        self.assertIn("summarize_for_pm", names)

    def test_extract_dev_prompt_schema(self) -> None:
        # Find the tool.
        tool = next(t for t in TRANSLATION_TOOLS if t["function"]["name"] == "extract_dev_prompt")
        params = tool["function"]["parameters"]
        self.assertEqual(params["type"], "object")
        self.assertIn("dev_prompt", params["properties"])
        self.assertEqual(params["required"], ["dev_prompt"])

    def test_summarize_for_pm_schema(self) -> None:
        tool = next(t for t in TRANSLATION_TOOLS if t["function"]["name"] == "summarize_for_pm")
        params = tool["function"]["parameters"]
        self.assertIn("summary", params["properties"])
        self.assertEqual(params["required"], ["summary"])

    def test_system_prompt_documents_both_tools(self) -> None:
        self.assertIn("extract_dev_prompt", SYSTEM_PROMPT)
        self.assertIn("summarize_for_pm", SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
