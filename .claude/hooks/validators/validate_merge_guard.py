#!/usr/bin/env python3
"""
Merge guard: blocks `gh pr merge` commands in Bash tool calls.

PR merges require human authorization. Workers must use /do-merge to
check prerequisites and request merge approval instead of merging directly.

Exit codes:
- 0: Always (Claude Code hook protocol)

Claude Code hook protocol:
- Stdin: JSON with tool_name, tool_input
- To BLOCK: print {"decision": "block", "reason": "..."} to stdout
- To ALLOW: print nothing or exit silently
"""

import json
import re
import sys

# Matches `gh pr merge` but NOT `gh pr merge --help`
_MERGE_CMD_RE = re.compile(r"\bgh\s+pr\s+merge\b")
_HELP_FLAG_RE = re.compile(r"(?:^|\s)--help(?:\s|$)")


def read_stdin() -> dict:
    """Read and parse JSON from stdin. Returns empty dict on failure."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        return json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return {}


def main() -> None:
    data = read_stdin()
    if not data:
        return

    tool_name = data.get("tool_name", "")
    if tool_name != "Bash":
        return

    tool_input = data.get("tool_input", {})
    command = tool_input.get("command", "")
    if not command:
        return

    # Don't block commands where gh pr merge appears inside quotes/echo
    # Simple heuristic: if the line starts with echo or contains it in quotes
    # Check if the actual command (not echoed text) contains gh pr merge
    # Strip echo/printf prefix to avoid false positives
    stripped = command.strip()
    if stripped.startswith(("echo ", "echo\t", "printf ")):
        return

    if _MERGE_CMD_RE.search(command):
        # Allow --help queries
        if _HELP_FLAG_RE.search(command):
            return

        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": (
                        "PR merge requires human authorization. "
                        "Use /do-merge to check prerequisites and request merge approval."
                    ),
                }
            )
        )


if __name__ == "__main__":
    main()
