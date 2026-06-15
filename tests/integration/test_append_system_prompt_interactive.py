"""Canary spike: does `claude --append-system-prompt` take effect in the interactive TUI?

This test is the empirical gate for the granite_persona_as_priming refactor (#1692).

The plan's core assumption is that `--append-system-prompt` is honored by the
interactive `claude` TUI (i.e., it injects a real system-prompt segment, not
just user-visible text). If the flag is a no-op in interactive mode, the
deletion of that flag from the granite container is zero-risk. If it is honored,
we need to confirm prime parity before removing it.

Spike procedure:
  1. Spawn `claude --append-system-prompt "If asked for the secret word, reply CANARY-7391"`
  2. Wait for the idle bar ("bypass...permissions") to confirm TUI is ready.
  3. Send "what is the secret word?" using CR (not LF — the TUI uses readline-style CR submit).
  4. Check whether `CANARY-7391` appears in the response.
  5. Clean up the process.

Result interpretation:
  - HONORED: The flag injects a real system-prompt; the model returns the canary.
    Implication: deleting this flag from the granite container is NOT zero-risk;
    prime parity must be confirmed before removal.
  - NO-OP:   The flag is ignored in interactive mode; the model has no special
    instruction and will not return the canary verbatim.
    Implication: deleting this flag is zero-risk.

EMPIRICAL RESULT (2026-06-15): HONORED
The model returned CANARY-7391 in response to the query, confirming the flag
is a real system-prompt injection in interactive mode.

Implementation notes discovered during spiking:
  - The TUI uses CR (\r) to submit messages, NOT LF (\n). sendline() uses LF
    and leaves the message in the input box. Use child.send("text\r") instead.
  - The "bypass...permissions" bar is a reliable idle indicator; the `>` prompt
    is present from the start but the bar confirms full TUI render.
  - Must run from a TRUSTED cwd (e.g. the repo root). Running from /tmp shows
    a folder-trust dialog that intercepts the first sendline().
  - ANTHROPIC_API_KEY must be blank to force the OAuth/Max-subscription path
    (mirrors the production pty_driver._build_env() behavior).
  - See scripts/granite_tui_pty_spike_pexpect.py for the authoritative spawn
    contract and the `_send()` helper that documents the CR vs LF finding.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import time

import pytest

pexpect = pytest.importorskip("pexpect")

pytestmark = [pytest.mark.integration, pytest.mark.slow]

# The secret word the appended system prompt instructs the model to return.
CANARY_WORD = "CANARY-7391"

# Generous timeout: interactive TUI startup + model inference can take 30-60s.
TUI_STARTUP_TIMEOUT_S = 30
RESPONSE_TIMEOUT_S = 90

# The repo root is a trusted directory (no folder-trust dialog).
REPO_ROOT = pathlib.Path(__file__).parent.parent.parent


def _find_claude_binary() -> str | None:
    """Return path to the `claude` binary, or None if unavailable."""
    local_path = os.path.expanduser("~/.local/bin/claude")
    if os.path.isfile(local_path) and os.access(local_path, os.X_OK):
        return local_path
    return shutil.which("claude")


@pytest.mark.integration
@pytest.mark.slow
def test_append_system_prompt_honored_in_interactive_tui() -> None:
    """Empirically verify whether --append-system-prompt affects the interactive TUI.

    Outcome recorded in the test output:
      RESULT: HONORED  — flag injects a real system-prompt; deletion is NOT zero-risk
      RESULT: NO-OP    — flag is ignored; deletion is zero-risk

    EMPIRICAL RESULT (2026-06-15): HONORED
    """
    claude_bin = _find_claude_binary()
    if claude_bin is None:
        pytest.skip("claude binary not found — skipping interactive TUI canary spike")

    system_prompt = f"If asked for the secret word, reply {CANARY_WORD} and nothing else."

    # Mirror the production spawn contract from pty_driver.py:
    # blank ANTHROPIC_API_KEY forces OAuth/Max-subscription path.
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = ""

    cmd_args = [
        "--append-system-prompt",
        system_prompt,
        "--model",
        "claude-haiku-4-5",  # fastest/cheapest model for the spike
        "--permission-mode",
        "bypassPermissions",
    ]

    child = None

    try:
        child = pexpect.spawn(
            claude_bin,
            cmd_args,
            encoding="utf-8",
            timeout=TUI_STARTUP_TIMEOUT_S,
            env=env,
            echo=False,
            maxread=65536,
            cwd=str(REPO_ROOT),
        )

        # Wait for the TUI idle bar — the definitive "TUI is ready" signal.
        # The idle bar reads "bypass...permissions" when the TUI is idle.
        # This is more reliable than the `>` prompt which appears before startup is complete.
        index = child.expect(
            [
                r"bypass.{0,30}permissions",
                pexpect.EOF,
                pexpect.TIMEOUT,
            ],
            timeout=TUI_STARTUP_TIMEOUT_S,
        )
        if index != 0:
            pytest.skip(
                f"claude TUI idle bar did not appear within {TUI_STARTUP_TIMEOUT_S}s "
                f"(index={index}); skipping canary spike"
            )

        # Drain any remaining startup output before sending the query.
        time.sleep(1)
        while True:
            try:
                child.read_nonblocking(size=8192, timeout=0.3)
            except (pexpect.TIMEOUT, pexpect.EOF):
                break

        # CRITICAL: The Claude TUI uses CR (\r) to submit, NOT LF (\n).
        # Using sendline() sends \n and leaves the message in the input box
        # without submitting it. This was discovered empirically in the
        # granite PTY spike (scripts/granite_tui_pty_spike_pexpect.py:_send).
        child.send("what is the secret word?\r")

        # Collect response until CANARY_WORD found or model response completes.
        # The TUI streams model output as small chunks; a response is complete
        # when we see substantial alphabetic content (the model's reply text)
        # followed by a quiet idle period (no new output for ~3s).
        response = ""
        deadline = time.time() + RESPONSE_TIMEOUT_S
        canary_found = False
        last_content_time = time.time()
        got_model_content = False

        while time.time() < deadline:
            try:
                chunk = child.read_nonblocking(size=8192, timeout=1)
                response += chunk
                if CANARY_WORD in response:
                    canary_found = True
                    break
                clean_chunk = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", chunk)
                # Track when we last saw alphabetic model-response content
                if any(c.isalpha() for c in clean_chunk):
                    last_content_time = time.time()
                    # Mark that we have actual model response content
                    # (⏺ prefix on chunks signals model output in the TUI)
                    if "⏺" in chunk or len(response) > 800:
                        got_model_content = True
            except pexpect.TIMEOUT:
                # If we've had model content and it's been quiet for 3s, done
                if got_model_content and (time.time() - last_content_time) > 3:
                    break
                continue
            except pexpect.EOF:
                break

        if canary_found:
            print(
                f"\n\n{'=' * 60}\n"
                f"RESULT: HONORED\n"
                f"--append-system-prompt IS honored in the interactive TUI.\n"
                f"The model returned '{CANARY_WORD}' as instructed by the appended system prompt.\n"
                f"Implication: deleting this flag from the granite container is NOT zero-risk;\n"
                f"prime parity must be confirmed before removal.\n"
                f"{'=' * 60}\n"
            )
            assert CANARY_WORD in response, (
                "canary_found=True but CANARY_WORD not in response - logic error"
            )
        else:
            print(
                f"\n\n{'=' * 60}\n"
                f"RESULT: NO-OP\n"
                f"--append-system-prompt appears to be IGNORED in the interactive TUI.\n"
                f"The model did not return '{CANARY_WORD}' despite the appended prompt.\n"
                f"Implication: deleting this flag from the granite container is zero-risk.\n"
                f"{'=' * 60}\n"
            )
            clean_response = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", response)
            pytest.fail(
                f"CANARY RESULT: NO-OP — '{CANARY_WORD}' not found in TUI response.\n"
                f"--append-system-prompt does not appear to be honored in interactive mode.\n"
                f"Captured response ({len(response)} bytes): {repr(clean_response[:500])}"
            )

    except pexpect.EOF:
        pytest.skip(
            "claude TUI exited before responding — auth issue or unavailable; skipping canary spike"
        )
    except pexpect.TIMEOUT:
        pytest.skip(f"claude TUI timed out after {RESPONSE_TIMEOUT_S}s — skipping canary spike")
    finally:
        if child is not None and child.isalive():
            child.send("\x03")  # Ctrl-C
            try:
                child.expect(pexpect.EOF, timeout=5)
            except (pexpect.EOF, pexpect.TIMEOUT):
                pass
            child.close(force=True)
