#!/usr/bin/env python3
"""PreToolUse hook: Block machine-wide, pattern-matched kills of test runs.

Issue #2562. A sibling agent that wants a clean process table before its own
run reaches for the obvious command:

    kill -9 $(pgrep -f "bin/pytest")
    pkill -9 -f pytest-clean.sh

That has no scoping whatsoever. On a machine where several agents test
concurrently it kills every pytest on the box, including the runs the author
knows nothing about. It did exactly that four times in one day: two lanes lost
full-suite runs mid-flight, one of them at 99%, and the resulting evidence
(a controller SIGKILLed with no summary, orphaned xdist workers, an empty
`lastfailed`) was misread for hours as a memory ceiling and then as a poisonous
tail test.

`scripts/pytest-clean.sh` already scopes its own reaping to workers it owns or
true orphans, and `scripts/reap-xdist.sh` is the deliberate machine-wide sweep
with a parent-liveness filter that spares any run whose controller is alive.
Both protections are bypassed the moment the pattern above is typed by hand,
and nothing warned about it. This validator is that warning.

What is blocked: a kill verb whose targets come from a *pattern* naming a test
runner. What is not: killing a specific PID, the sanctioned reaper, and any
read-only `pgrep`/`ps` inspection.
"""

import json
import re
import sys

# Process patterns that name a test run. Deliberately narrow: this is about
# the concurrent-test-run collision in #2562, not about pattern kills in
# general, and a broad rule here would block legitimate service management.
_TEST_RUNNER_PATTERN = r"(?:py\.?test|xdist|pytest-clean)"

# The sanctioned machine-wide sweep. It checks parent liveness before killing,
# so it spares a run whose controller is still alive -- the exact property the
# hand-rolled commands lack.
_SANCTIONED = re.compile(r"reap-xdist\.sh")

_BLOCK_PATTERNS = [
    # kill/killall taking its targets from a pgrep substitution:
    #   kill -9 $(pgrep -f "bin/pytest")   |   kill `pgrep -f pytest`
    re.compile(
        r"\bkill(?:all)?\b[^|;&\n]*?[$`]\(?\s*pgrep\b[^)`\n]*" + _TEST_RUNNER_PATTERN,
        re.IGNORECASE,
    ),
    # pkill matching a pattern directly:  pkill -f pytest
    re.compile(r"\bpkill\b[^|;&\n]*" + _TEST_RUNNER_PATTERN, re.IGNORECASE),
    # killall by process name:  killall pytest
    re.compile(r"\bkillall\b[^|;&\n]*" + _TEST_RUNNER_PATTERN, re.IGNORECASE),
    # pgrep piped into a killer:  pgrep -f pytest | xargs kill -9
    re.compile(
        r"\bpgrep\b[^|\n]*" + _TEST_RUNNER_PATTERN + r"[^|\n]*\|[^|\n]*\b(?:kill|xargs)\b",
        re.IGNORECASE,
    ),
]

_REASON = """Blocked: machine-wide pattern kill of pytest processes (issue #2562).

This command matches every pytest on the machine, not just yours. Several
agents test concurrently here, and this exact command destroyed four
full-suite runs belonging to other lanes -- one of them at 99% -- leaving a
SIGKILLed controller with no summary that cost hours to diagnose.

Instead:

  scripts/reap-xdist.sh            # dry-run: show what would be killed
  scripts/reap-xdist.sh --apply    # kill only orphans whose parent is gone

That is the sanctioned sweep. It checks parent liveness, so a run that is
still being driven by a live shell is never touched.

To stop your own run, kill it by PID (`kill <pid>`); `scripts/pytest-clean.sh`
reaps its own workers on the way out.

If you are clearing the decks out of impatience: a full `tests/unit/` run
legitimately takes about 20 minutes on this machine. Nothing is stuck."""


def find_violation(command: str) -> str | None:
    """Return the block reason if `command` is a machine-wide test-run kill.

    Args:
        command: The Bash command string from the hook payload.

    Returns:
        The reason string to block with, or None to allow.
    """
    if not command:
        return None
    if _SANCTIONED.search(command):
        return None
    for pattern in _BLOCK_PATTERNS:
        if pattern.search(command):
            return _REASON
    return None


def main() -> None:
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    if hook_input.get("tool_name") != "Bash":
        return
    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    reason = find_violation(command)
    if reason:
        print(json.dumps({"decision": "block", "reason": reason}))


if __name__ == "__main__":
    main()
