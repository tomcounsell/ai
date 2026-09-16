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
runner or a long-lived service. What is not: killing a specific PID, the
sanctioned reaper or stop script, and any read-only `pgrep`/`ps` inspection.
"""

import json
import re
import sys

# Process patterns that name a test run. Deliberately narrow: this is about
# the concurrent-test-run collision in #2562, not about pattern kills in
# general, and a broad rule here would block legitimate service management.
_TEST_RUNNER_PATTERN = r"(?:py\.?test|xdist|pytest-clean)"

# Long-lived shared services on this machine. Each row is (display name, plain
# substring alternatives matching the production command line, sanctioned stop
# path). Adding the next service is one row here, not a new regex. The
# alternatives stay literal substrings on purpose (per #3208, clever regex over
# process names has platform edge cases), and bare `worker` is deliberately
# NOT a row: it is a substring of too many innocent strings, so only
# service-shaped forms (`-m worker`, the watchdog path/labels) match.
_SERVICES = [
    # Dashboard (port 8500): `pkill -f "python -m ui.app"` killed production
    # while aiming at a throwaway instance (issue #3316).
    ("python -m ui.app", r"ui\.app", "scripts/valor-service.sh stop"),
    # Session execution engine (com.valor.worker.plist runs `python -m worker`).
    ("python -m worker", r"-m\s+worker\b", "worker-stop (worker-disable to keep it down)"),
    # Telegram bridge: the sole session intake; killing it drops messages.
    ("bridge/telegram_bridge.py", r"telegram_bridge", "scripts/valor-service.sh stop"),
    # Email bridge (com.valor.email-bridge.plist runs `-m bridge.email_bridge`).
    (
        "bridge/email_bridge.py",
        r"bridge\.email_bridge|email_bridge",
        "email-stop (email-disable to keep it down)",
    ),
    # Watchdog (runs as monitoring/worker_watchdog.py; hyphen form catches the
    # com.valor.worker-watchdog launchd label instead of the command line).
    (
        "monitoring/worker_watchdog.py",
        r"monitoring/worker_watchdog\.py|worker_watchdog|worker-watchdog",
        "scripts/valor-service.sh stop",
    ),
    # Reflection worker (runs as `python -m reflections`; aliases catch the
    # com.valor.reflection-worker launchd label and the module attribute form).
    (
        "python -m reflections",
        r"-m\s+reflections\b|reflection_worker|reflection-worker",
        "launchctl bootout gui/$(id -u)/com.valor.reflection-worker",
    ),
]

_SERVICE_PATTERN = "(?:" + "|".join(f"(?:{pattern})" for _, pattern, _ in _SERVICES) + ")"

# The sanctioned machine-wide sweep. It checks parent liveness before killing,
# so it spares a run whose controller is still alive -- the exact property the
# hand-rolled commands lack.
_SANCTIONED = re.compile(r"reap-xdist\.sh")


def _kill_verb_patterns(target_pattern: str) -> list:
    """The four kill shapes parameterized over a target pattern."""
    return [
        # kill/killall taking its targets from a pgrep substitution:
        #   kill -9 $(pgrep -f "bin/pytest")   |   kill `pgrep -f pytest`
        re.compile(
            r"\bkill(?:all)?\b[^|;&\n]*?[$`]\(?\s*pgrep\b[^)`\n]*" + target_pattern,
            re.IGNORECASE,
        ),
        # pkill matching a pattern directly:  pkill -f pytest
        re.compile(r"\bpkill\b[^|;&\n]*" + target_pattern, re.IGNORECASE),
        # killall by process name:  killall pytest
        re.compile(r"\bkillall\b[^|;&\n]*" + target_pattern, re.IGNORECASE),
        # pgrep piped into a killer:  pgrep -f pytest | xargs kill -9
        re.compile(
            r"\bpgrep\b[^|\n]*" + target_pattern + r"[^|\n]*\|[^|\n]*\b(?:kill|xargs)\b",
            re.IGNORECASE,
        ),
    ]


_BLOCK_PATTERNS = _kill_verb_patterns(_TEST_RUNNER_PATTERN)
_SERVICE_BLOCK_PATTERNS = _kill_verb_patterns(_SERVICE_PATTERN) + [
    # killall takes a process NAME, not a command-line substring, so a
    # standalone `worker` token here names the service itself -- the
    # process-name position the kill shapes anchor on. This stays scoped to
    # killall: pkill -f matches arbitrary command-line substrings, where a
    # bare worker over-matches (homework, coworker, my-worker) and is
    # deliberately never matched. The exact-token match keeps
    # `killall my-worker` and `killall CoWorker` allowed.
    re.compile(r"\bkillall\b\s+(?:-\w+\s+)?worker(?:\s|$|[;&\n])", re.IGNORECASE),
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

_SERVICE_REASON_TEMPLATE = """Blocked: machine-wide pattern kill of {service} (issue #3316).

This command matches every {service} process on the machine, not just yours.
A reviewer stopping a throwaway dashboard with `pkill -f "python -m ui.app"`
killed the production dashboard on port 8500 the same way: the pattern carries
no port, no PID, no scoping -- it matches the shared service too.

Instead:

  {stop}

That is the sanctioned stop path for this service.

To stop only your own throwaway instance, find its PID first
(`pgrep -af "{service}"` is read-only and stays allowed) and `kill <pid>`.
Never clear processes by pattern."""


def _service_reason(command: str) -> str:
    """Return the service-specific block reason for `command`.

    Names the matched service and its sanctioned stop path so the agent knows
    what to run instead of the pattern kill.
    """
    for service, pattern, stop in _SERVICES:
        if re.search(pattern, command, re.IGNORECASE):
            return _SERVICE_REASON_TEMPLATE.format(service=service, stop=stop)
    return _SERVICE_REASON_TEMPLATE.format(
        service="shared service", stop="scripts/valor-service.sh stop"
    )


def find_violation(command: str) -> str | None:
    """Return the block reason if `command` is a machine-wide pattern kill.

    Test-runner matches keep the original pytest reason; service matches get
    the service-specific reason naming the sanctioned stop path.

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
    for pattern in _SERVICE_BLOCK_PATTERNS:
        if pattern.search(command):
            return _service_reason(command)
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
