#!/usr/bin/env python3
"""User-level Stop hook: Validate SDLC quality gates at session end.

This is a STANDALONE script deployed to ~/.claude/hooks/sdlc/ by the update
system. It imports shared utilities from sdlc_context.py in the same directory.

Behavior:
- If we are in SDLC context AND code was modified (detected via git diff),
  check whether quality commands (pytest, ruff, black) appear in the session's
  recent command history (from hook stdin transcript).
- If quality gates were not run, emit a warning (exit 2 to block stop).
- If not in SDLC context, silently allows.

Exit codes:
  0 — pass (non-SDLC context, or all gates satisfied)
  2 — block (SDLC context with code modified but quality gates not run)

Escape hatch:
  Set SKIP_SDLC=1 to bypass enforcement. A warning is logged to stderr.

Claude Code hook protocol:
  Stdin: JSON with session_id and optional transcript
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Standalone script — sys.path mutation is safe (never imported as library)
# Import shared utilities from sibling module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sdlc_context import is_sdlc_context, read_stdin


def has_code_changes() -> bool:
    """Check if there are code file changes in the working tree or staged.

    Looks for .py, .js, .ts files that have been modified.
    """
    code_extensions = {".py", ".js", ".ts"}
    try:
        # Check both staged and unstaged changes
        diff_output = subprocess.check_output(
            ["git", "diff", "--name-only", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        staged_output = subprocess.check_output(
            ["git", "diff", "--name-only", "--cached"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()

        all_files = set()
        if diff_output:
            all_files.update(diff_output.split("\n"))
        if staged_output:
            all_files.update(staged_output.split("\n"))

        for f in all_files:
            if Path(f).suffix.lower() in code_extensions:
                return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # NOTE: We intentionally do NOT check main...HEAD here.
    # That would flag already-committed changes from prior sessions,
    # causing false positives when resuming a session/ branch.

    return False


# Quality gate detection
QUALITY_RUN_HINTS = {
    "pytest": "pytest tests/",
    "ruff": "ruff check .",
    "black": "black --check .",
}

ERROR_TEMPLATE = """\
SDLC Quality Gate: Code was modified this session but not all quality checks were run.

Missing:
{missing_lines}
Please run the missing checks before completing this session.
Set SKIP_SDLC=1 to bypass in genuine emergencies (logs warning).
"""


def check_quality_gates_from_transcript(hook_input: dict) -> list[str]:
    """Check which quality commands are missing from the session transcript.

    The Stop hook receives the session transcript which includes all commands
    run during the session. We scan for evidence of pytest, ruff, and black.

    Returns list of missing command names.
    """
    # Try to get transcript from hook input
    transcript = hook_input.get("transcript", [])
    transcript_text = json.dumps(transcript) if transcript else ""

    # Also check the stop_hook_conversation if available
    conversation = hook_input.get("stop_hook_conversation", "")
    if conversation:
        transcript_text += str(conversation)

    # Scan for quality command evidence
    missing = []
    for cmd_name in ("pytest", "ruff", "black"):
        if not re.search(r"\b" + cmd_name + r"\b", transcript_text):
            missing.append(cmd_name)

    return missing


def main():
    try:
        hook_input = read_stdin()

        # Fast path: not in SDLC context
        if not is_sdlc_context():
            sys.exit(0)

        # No code changes — no enforcement needed
        if not has_code_changes():
            sys.exit(0)

        # Check which quality gates are missing
        missing = check_quality_gates_from_transcript(hook_input)

        if not missing:
            sys.exit(0)  # All gates passed

        # Handle SKIP_SDLC escape hatch
        if os.environ.get("SKIP_SDLC") == "1":
            session_id = hook_input.get("session_id", "unknown")
            print(
                f"WARNING: SKIP_SDLC=1 set — bypassing SDLC quality gate for session "
                f"'{session_id}'. Missing: {', '.join(missing)}",
                file=sys.stderr,
            )
            sys.exit(0)

        # Build error message
        missing_lines = "\n".join(f"  - {cmd} (run: {QUALITY_RUN_HINTS[cmd]})" for cmd in missing)
        print(ERROR_TEMPLATE.format(missing_lines=missing_lines), file=sys.stderr)
        sys.exit(2)

    except Exception:
        # Fail open: never block the user due to hook errors
        sys.exit(0)


if __name__ == "__main__":
    main()
