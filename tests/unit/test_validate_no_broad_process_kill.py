"""Tests for the machine-wide test-run kill validator (issue #2562).

The defect it exists to prevent: an agent clearing the process table before
its own run kills every concurrent pytest on the machine, including other
lanes' full-suite runs. The command that did it four times in one day is the
first case below, verbatim from the transcript.
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

ALLOWED = [
    # The sanctioned sweep, which checks parent liveness first.
    "scripts/reap-xdist.sh",
    "scripts/reap-xdist.sh --apply",
    "./scripts/reap-xdist.sh --apply",
    # Killing one known PID is properly scoped.
    "kill -9 12345",
    "kill -TERM 9004",
    # Read-only inspection is how you find out what is running.
    "pgrep -f pytest",
    'ps aux | grep "bin/pytest"',
    "pgrep -f pytest | wc -l",
    # Unrelated process management must not be caught.
    "pkill -f 'node dev-server'",
    "killall Dock",
    # Actually running tests.
    "scripts/pytest-clean.sh tests/unit/ -q",
]


@pytest.mark.parametrize("command", BLOCKED)
def test_blocks_machine_wide_test_kills(command):
    reason = validator.find_violation(command)
    assert reason is not None, f"should have blocked: {command}"
    assert "reap-xdist.sh" in reason, "the block must name the sanctioned alternative"


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
