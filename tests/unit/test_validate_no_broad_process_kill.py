"""Tests for the machine-wide pattern-kill validator (#2562, widened in #3316).

The defect it exists to prevent: an agent clearing the process table before
its own run kills every concurrent pytest on the machine, including other
lanes' full-suite runs. The command that did it four times in one day is the
first case below, verbatim from the transcript.

The widened defect (#3316): a reviewer stopping a throwaway dashboard with
`pkill -f "python -m ui.app"` killed the production dashboard too. The
BLOCKED_SERVICES cases cover the same kill shapes aimed at long-lived
services.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_VALIDATOR = (
    Path(__file__).resolve().parents[2]
    / ".claude"
    / "hooks"
    / "validators"
    / "validate_no_broad_process_kill.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("validate_no_broad_process_kill", _VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_no_broad_process_kill"] = module
    spec.loader.exec_module(module)
    return module


validator = _load()


BLOCKED = [
    # The exact command from the #2562 transcript.
    'kill -9 $(pgrep -f "bin/pytest") 2>/dev/null',
    'kill -9 $(pgrep -f "pytest-clean.sh") 2>/dev/null',
    "kill $(pgrep -f pytest)",
    "kill -9 `pgrep -f pytest`",
    "pkill -f pytest",
    "pkill -9 -f 'bin/pytest'",
    "pkill -f py.test",
    "killall pytest",
    "pgrep -f pytest | xargs kill -9",
    # Casing must not be an escape hatch.
    "PKILL -F PYTEST",
]

# Service kills (issue #3316): one row per long-lived service per kill-verb
# shape, using the production command lines. The killall rows use the
# service's name token (killall takes a process name, not a full command).
BLOCKED_SERVICES = [
    # python -m ui.app (dashboard) -- the incident command shape is first.
    'pkill -f "python -m ui.app"',
    'kill $(pgrep -f "python -m ui.app")',
    "killall ui.app",
    'pgrep -f "python -m ui.app" | xargs kill -9',
    # python -m worker (session execution engine).
    'pkill -f "python -m worker"',
    'kill -9 $(pgrep -f "python -m worker")',
    "killall worker",
    'pgrep -f "python -m worker" | xargs kill',
    # bridge/telegram_bridge.py.
    "pkill -f telegram_bridge",
    "kill $(pgrep -f telegram_bridge)",
    "killall telegram_bridge",
    "pgrep -f telegram_bridge | xargs kill -9",
    # bridge/email_bridge.py.
    "pkill -f bridge.email_bridge",
    "kill $(pgrep -f email_bridge)",
    "killall email_bridge",
    "pgrep -f email_bridge | xargs kill -9",
    # monitoring/worker_watchdog.py, plus the launchd-label hyphen alias.
    "pkill -f monitoring/worker_watchdog.py",
    "kill $(pgrep -f worker_watchdog)",
    "killall worker_watchdog",
    "pgrep -f worker_watchdog | xargs kill -9",
    "pkill -f worker-watchdog",
    "killall worker-watchdog",
    # python -m reflections, plus the module-attribute alias.
    'pkill -f "python -m reflections"',
    "kill -9 `pgrep -f reflection_worker`",
    "killall reflection_worker",
    "pgrep -f reflection_worker | xargs kill -9",
    'kill $(pgrep -f "python -m reflections")',
    # The reap-xdist.sh exemption is scoped to the test-runner branch only:
    # mentioning it (even in a chained command or a trailing comment) must
    # not also disable the service guard (#3316 review round 1 tech debt).
    'scripts/reap-xdist.sh --apply && pkill -f "python -m ui.app"',
    'pkill -f "python -m ui.app"  # after reap-xdist.sh',
]

# (service kill command, stop-path hint the block reason must name). Each hint
# must be a command that actually targets that service -- `scripts/
# valor-service.sh stop` only stops the Telegram bridge (`stop_bridge`), so it
# is wrong for the dashboard and the watchdog (#3316 review round 1 blocker).
BLOCKED_SERVICE_REASONS = [
    ('pkill -f "python -m ui.app"', "scripts/valor-service.sh restart"),
    ('pkill -f "python -m worker"', "worker-stop"),
    ("killall worker", "worker-stop"),
    ("pkill -f telegram_bridge", "scripts/valor-service.sh stop"),
    ("pkill -f email_bridge", "email-stop"),
    ("pkill -f monitoring/worker_watchdog.py", "com.valor.worker-watchdog"),
    ("pkill -f worker-watchdog", "com.valor.worker-watchdog"),
    ('pkill -f "python -m reflections"', "com.valor.reflection-worker"),
    ("pkill -f reflection_worker", "com.valor.reflection-worker"),
]

ALLOWED = [
    # The sanctioned sweep, which checks parent liveness first.
    "scripts/reap-xdist.sh",
    "scripts/reap-xdist.sh --apply",
    "./scripts/reap-xdist.sh --apply",
    # Killing one known PID is properly scoped.
    "kill -9 12345",
    "kill -TERM 9004",
    "kill -9 88620",
    # Read-only inspection is how you find out what is running.
    "pgrep -f pytest",
    'ps aux | grep "bin/pytest"',
    "pgrep -f pytest | wc -l",
    "pgrep -f worker | wc -l",
    'ps aux | grep "ui.app"',
    'pgrep -af "python -m worker"',
    # Unrelated process management must not be caught.
    "pkill -f 'node dev-server'",
    "killall Dock",
    # Bare `worker` is a substring of too many innocent strings: only
    # service-shaped forms (`-m worker`, the watchdog path/labels) block.
    "pkill -f worker",
    "pkill -f homework",
    "killall my-worker",
    "killall CoWorker",
    # The sanctioned service stop paths are plain invocations, not kills.
    "scripts/valor-service.sh stop",
    "worker-stop",
    "worker-disable",
    "email-stop",
    "email-disable",
    # Actually running tests.
    "scripts/pytest-clean.sh tests/unit/ -q",
]


@pytest.mark.parametrize("command", BLOCKED)
def test_blocks_machine_wide_test_kills(command):
    reason = validator.find_violation(command)
    assert reason is not None, f"should have blocked: {command}"
    assert "reap-xdist.sh" in reason, "the block must name the sanctioned alternative"


@pytest.mark.parametrize("command", BLOCKED_SERVICES)
def test_blocks_machine_wide_service_kills(command):
    reason = validator.find_violation(command)
    assert reason is not None, f"should have blocked: {command}"
    assert "by pattern" in reason, "the block must teach the kill-by-PID rule"


@pytest.mark.parametrize(("command", "stop_hint"), BLOCKED_SERVICE_REASONS)
def test_service_block_names_sanctioned_stop(command, stop_hint):
    reason = validator.find_violation(command)
    assert reason is not None, f"should have blocked: {command}"
    assert stop_hint in reason, "the block must name the sanctioned stop path"


@pytest.mark.parametrize("command", ALLOWED)
def test_allows_scoped_and_read_only_commands(command):
    assert validator.find_violation(command) is None, f"should have allowed: {command}"


def test_empty_command_is_allowed():
    assert validator.find_violation("") is None


def test_registered_in_the_bash_dispatcher():
    """A validator that is written but not dispatched blocks nothing (#2435)."""
    dispatcher = (
        Path(__file__).resolve().parents[2]
        / ".claude"
        / "hooks"
        / "dispatch"
        / "pre_tool_use_bash.py"
    )
    source = dispatcher.read_text()
    assert "validate_no_broad_process_kill" in source


def test_dispatcher_blocks_the_transcript_command():
    """End-to-end through the dispatcher, not just the predicate."""
    spec = importlib.util.spec_from_file_location(
        "pre_tool_use_bash",
        Path(__file__).resolve().parents[2]
        / ".claude"
        / "hooks"
        / "dispatch"
        / "pre_tool_use_bash.py",
    )
    dispatcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dispatcher)

    reason = dispatcher.dispatch(
        {
            "tool_name": "Bash",
            "cwd": str(Path(__file__).resolve().parents[2]),
            "tool_input": {"command": 'kill -9 $(pgrep -f "bin/pytest")'},
        }
    )
    assert reason is not None
    assert "reap-xdist.sh" in reason
