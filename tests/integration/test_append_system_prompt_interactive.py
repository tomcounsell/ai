"""Canary spike: does `claude --append-system-prompt` take effect in the interactive TUI?

This test is the empirical gate for the granite_persona_as_priming refactor (#1692).

The plan's core assumption is that `--append-system-prompt` is honored by the
interactive `claude` TUI (i.e., it injects a real system-prompt segment, not
just user-visible text). If the flag is a no-op in interactive mode, the
deletion of that flag from the granite container is zero-risk. If it is honored,
we need to confirm prime parity before removing it.

Spike procedure:
  1. Spawn `claude --append-system-prompt "If asked for the secret word, reply CANARY-7391"`
  2. Wait for the interactive prompt to paint.
  3. Send "what is the secret word?"
  4. Check whether `CANARY-7391` appears in the response.

Result interpretation:
  - HONORED: The flag injects a real system-prompt; the model returns the canary.
  - NO-OP:   The flag is ignored in interactive mode; the model has no special
             instruction and will not return the canary verbatim.
"""

from __future__ import annotations

import os
import shutil
import sys

import pytest

pexpect = pytest.importorskip("pexpect")

pytestmark = [pytest.mark.integration, pytest.mark.slow]

# The secret word the appended system prompt instructs the model to return.
CANARY_WORD = "CANARY-7391"

# Generous timeout: interactive TUI startup + model inference can take 30-60s.
TUI_STARTUP_TIMEOUT_S = 60
RESPONSE_TIMEOUT_S = 90

# Prompt indicator patterns emitted by the claude interactive TUI.
# The TUI paints a `>` prompt after startup and after each response.
PROMPT_PATTERNS = [
    r">\s",       # standard prompt
    r"╭",         # box-drawing prompt frame
    r"│",         # continuation frame
]


def _find_claude_binary() -> str | None:
    """Return path to the `claude` binary, or None if unavailable."""
    # Prefer the well-known install location used on this machine.
    local_path = os.path.expanduser("~/.local/bin/claude")
    if os.path.isfile(local_path) and os.access(local_path, os.X_OK):
        return local_path
    return shutil.which("claude")


def _strip_ansi(text: bytes) -> str:
    """Naively strip ANSI escape sequences from bytes for clean comparison."""
    import re

    raw = text.decode("utf-8", errors="replace")
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw)


@pytest.mark.integration
@pytest.mark.slow
def test_append_system_prompt_honored_in_interactive_tui() -> None:
    """Empirically verify whether --append-system-prompt affects the interactive TUI.

    Outcome recorded in the test output:
      RESULT: HONORED  — flag injects a real system-prompt; deletion is NOT zero-risk
      RESULT: NO-OP    — flag is ignored; deletion is zero-risk
    """
    claude_bin = _find_claude_binary()
    if claude_bin is None:
        pytest.skip("claude binary not found — skipping interactive TUI canary spike")

    system_prompt = f"If asked for the secret word, reply {CANARY_WORD} and nothing else."

    cmd_args = [
        "--append-system-prompt",
        system_prompt,
        "--model",
        "claude-haiku-4-5",  # fastest/cheapest model for the spike
        "--permission-mode",
        "bypassPermissions",
    ]

    child = None
    captured_output: list[str] = []

    try:
        child = pexpect.spawn(
            claude_bin,
            cmd_args,
            encoding="utf-8",
            timeout=TUI_STARTUP_TIMEOUT_S,
            env={**os.environ},
        )
        child.logfile_read = sys.stdout  # stream TUI output to test output

        # Wait for the TUI prompt to appear.
        # The claude TUI prints a welcome frame and then a `>` prompt.
        index = child.expect(
            [
                r">",         # bare prompt
                pexpect.EOF,
                pexpect.TIMEOUT,
            ],
            timeout=TUI_STARTUP_TIMEOUT_S,
        )
        if index != 0:
            captured_output.append(f"TUI startup failed: index={index}, before={child.before!r}")
            pytest.skip(
                f"claude TUI did not paint a prompt within {TUI_STARTUP_TIMEOUT_S}s "
                f"(index={index}); skipping canary spike"
            )

        captured_output.append("TUI prompt detected — sending query")

        # Send the canary query.
        child.sendline("what is the secret word?")

        # Wait for CANARY_WORD or another prompt, whichever comes first.
        # We use a generous timeout because model inference can be slow.
        result_index = child.expect(
            [
                CANARY_WORD,  # 0: canary word found — flag is HONORED
                r">",         # 1: another prompt without canary — flag is NO-OP
                pexpect.EOF,  # 2: process exited
                pexpect.TIMEOUT,  # 3: timed out
            ],
            timeout=RESPONSE_TIMEOUT_S,
        )

        # Capture everything received so far for diagnostic output.
        before_text = child.before or ""
        after_text = child.after or ""
        captured_output.append(f"expect result index: {result_index}")
        captured_output.append(f"before: {before_text!r:.500}")
        captured_output.append(f"after: {after_text!r:.200}")

        if result_index == 0:
            # CANARY_WORD appeared in the response.
            print(
                f"\n\n{'='*60}\n"
                f"RESULT: HONORED\n"
                f"--append-system-prompt IS honored in the interactive TUI.\n"
                f"The model returned '{CANARY_WORD}' as instructed by the appended system prompt.\n"
                f"Implication: deleting this flag from the granite container is NOT zero-risk;\n"
                f"prime parity must be confirmed before removal.\n"
                f"{'='*60}\n"
            )
            # The test PASSES when the flag is honored (behavior confirmed).
            assert CANARY_WORD in (before_text + after_text), (
                f"Expected '{CANARY_WORD}' in response but pexpect.expect matched index 0 "
                f"without it in captured text — this should not happen"
            )
        elif result_index == 1:
            # Got another prompt without seeing CANARY_WORD — flag appears to be NO-OP.
            # Drain more output in case the canary word comes after the prompt.
            try:
                child.expect(CANARY_WORD, timeout=5)
                # Found it after all — flag IS honored.
                print(
                    f"\n\n{'='*60}\n"
                    f"RESULT: HONORED (delayed)\n"
                    f"--append-system-prompt IS honored (canary found after secondary prompt).\n"
                    f"{'='*60}\n"
                )
                # Let the test pass.
            except (pexpect.EOF, pexpect.TIMEOUT):
                print(
                    f"\n\n{'='*60}\n"
                    f"RESULT: NO-OP\n"
                    f"--append-system-prompt appears to be IGNORED in the interactive TUI.\n"
                    f"The model did not return '{CANARY_WORD}' despite the appended prompt.\n"
                    f"Implication: deleting this flag from the granite container is zero-risk.\n"
                    f"{'='*60}\n"
                )
                # Record as an explicit NO-OP by failing with a descriptive message
                # so the result is unambiguous in CI output.
                pytest.fail(
                    f"CANARY RESULT: NO-OP — '{CANARY_WORD}' not found in TUI response.\n"
                    f"--append-system-prompt does not appear to be honored in interactive mode.\n"
                    f"Captured output: {captured_output}"
                )
        else:
            pytest.skip(
                f"claude TUI exited unexpectedly or timed out (index={result_index}); "
                f"canary spike inconclusive. Captured: {captured_output}"
            )

    except pexpect.EOF:
        pytest.skip(
            "claude TUI exited before responding — auth issue or unavailable; skipping canary spike"
        )
    except pexpect.TIMEOUT:
        pytest.skip(
            f"claude TUI timed out after {RESPONSE_TIMEOUT_S}s — skipping canary spike. "
            f"Captured: {captured_output}"
        )
    finally:
        if child is not None and child.isalive():
            child.sendcontrol("c")
            try:
                child.expect(pexpect.EOF, timeout=5)
            except (pexpect.EOF, pexpect.TIMEOUT):
                pass
            child.close(force=True)
