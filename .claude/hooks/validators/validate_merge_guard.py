#!/usr/bin/env python3
"""
Merge guard: blocks `gh pr merge` unless authorized via /do-merge.

Autonomous workers cannot merge PRs directly. The /do-merge skill checks
prerequisites (TEST, REVIEW, DOCS) and creates a short-lived authorization
file that this hook checks before allowing the merge.

Authorization flow:
1. Human says "merge it" (via Telegram or local session)
2. /do-merge checks prerequisites, creates data/merge_authorized_{pr_number}
3. Worker runs `gh pr merge {pr_number}`
4. This hook finds the authorization file → allows the merge
5. /do-merge cleans up the authorization file after merge

Without authorization:
- Worker tries `gh pr merge` → blocked with message to use /do-merge

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
from pathlib import Path

# Matches `gh pr merge` but NOT `gh pr merge --help`
_MERGE_CMD_RE = re.compile(r"\bgh\s+pr\s+merge\b")
_HELP_FLAG_RE = re.compile(r"(?:^|\s)--help(?:\s|$)")
# Extract PR number from `gh pr merge 123` or `gh pr merge 123 --squash`
_PR_NUMBER_RE = re.compile(r"\bgh\s+pr\s+merge\s+(\d+)")

# Authorization files live in data/ relative to project root
_DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def _is_authorized(command: str) -> bool:
    """Check if a merge authorization file exists for the PR number in the command."""
    match = _PR_NUMBER_RE.search(command)
    if not match:
        return False
    pr_number = match.group(1)
    auth_file = _DATA_DIR / f"merge_authorized_{pr_number}"
    return auth_file.exists()


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

    # Don't block commands where gh pr merge appears inside echo/printf
    stripped = command.strip()
    if stripped.startswith(("echo ", "echo\t", "printf ")):
        return

    if _MERGE_CMD_RE.search(command):
        # Allow --help queries
        if _HELP_FLAG_RE.search(command):
            return

        # Allow if authorized via /do-merge
        if _is_authorized(command):
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
