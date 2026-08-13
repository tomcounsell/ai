"""Tests for the unconditional Redis flush validator (issue #2645).

The defect it exists to prevent: an agent-issued Bash command that calls
`.flushdb()`/`.flushall()` or `redis-cli ... FLUSHDB`/`FLUSHALL` reaches a
live interpreter before Layer 1 (`tools/redis_flush_guard.py`) ever gets a
chance to intercept it as a file on disk. Unlike
`validate_no_raw_redis_delete.py`, this validator carries no Popoto-context
gate -- a flush is unconditionally destructive and the most dangerous shapes
(`redis.Redis().flushdb()`, `redis-cli -n 0 flushdb`) carry no Popoto
vocabulary at all.
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
    / "validate_no_redis_flush.py"
)

_DISPATCHER = (
    Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "dispatch" / "pre_tool_use_bash.py"
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


validator = _load(_VALIDATOR, "validate_no_redis_flush")


BLOCKED = [
    "redis.Redis().flushdb()",
    "POPOTO_REDIS_DB.flushdb()",
    "r.flushall()",
    "client.flushdb ()",
    "redis-cli -n 0 flushdb",
    "redis-cli FLUSHALL",
    "redis-cli -h 127.0.0.1 -p 6379 flushdb",
    # Casing must not be an escape hatch for the CLI forms.
    "redis-cli -n 0 FlushDB",
    "REDIS-CLI flushall",
]

ALLOWED = [
    # Read-only inspection / search over the words, not a call shape.
    "grep -rn flushdb tests/",
    "rg flushall",
    'grep -rn "flushdb" .',
    "rg -i flushdb docs/",
    # Prose / docstrings mentioning the words.
    'echo "this incident was caused by a call to flushdb on db 0"',
    "cat docs/plans/redis-flush-hardening.md",
    # Unrelated redis-cli usage.
    "redis-cli -n 3 DBSIZE",
    "redis-cli PING",
    # Attribute access without a call.
    'python -c "print(redis.Redis.flushdb)"',
]


@pytest.mark.parametrize("command", BLOCKED)
def test_blocks_flush_call_shapes(command):
    reason = validator.find_violation(command)
    assert reason is not None, f"should have blocked: {command}"
    assert "REDIS_PRODUCTION_FLUSH_OK=1" in reason
    assert "2026-06-03" in reason
    assert "2026-08-07" in reason


@pytest.mark.parametrize("command", ALLOWED)
def test_no_false_positives(command):
    assert validator.find_violation(command) is None, f"should have allowed: {command}"


def test_empty_command_is_allowed():
    assert validator.find_violation("") is None


def test_none_command_is_allowed():
    assert validator.find_violation(None) is None


def test_escape_prefix_disarms_the_block():
    """D5a regression: the escape named in the block message must be the
    escape find_violation actually honors."""
    assert validator.find_violation('REDIS_PRODUCTION_FLUSH_OK=1 python -c "r.flushdb()"') is None
    assert validator.find_violation("REDIS_PRODUCTION_FLUSH_OK=1 redis-cli -n 0 flushdb") is None


def test_block_message_names_the_working_escape_form():
    reason = validator.find_violation("r.flushdb()")
    assert 'REDIS_PRODUCTION_FLUSH_OK=1 python -c "' in reason


def test_block_message_points_at_per_process_test_db_idiom():
    reason = validator.find_violation("r.flushdb()")
    assert "tests/db_claim.py" in reason


def test_registered_in_the_bash_dispatcher():
    """A validator that is written but not dispatched blocks nothing (#2435)."""
    source = _DISPATCHER.read_text()
    assert "validate_no_redis_flush" in source


def test_dispatcher_blocks_a_real_flush_command():
    """End-to-end through the dispatcher's actual tool path, not just the
    predicate in isolation -- the agent's Bash tool call is what must be
    gated."""
    dispatcher = _load(_DISPATCHER, "pre_tool_use_bash")

    reason = dispatcher.dispatch(
        {
            "tool_name": "Bash",
            "cwd": str(Path(__file__).resolve().parents[2]),
            "tool_input": {"command": "redis.Redis().flushdb()"},
        }
    )
    assert reason is not None
    assert "REDIS_PRODUCTION_FLUSH_OK=1" in reason


def test_dispatcher_allows_a_grep_over_the_word():
    dispatcher = _load(_DISPATCHER, "pre_tool_use_bash")

    reason = dispatcher.dispatch(
        {
            "tool_name": "Bash",
            "cwd": str(Path(__file__).resolve().parents[2]),
            "tool_input": {"command": "grep -rn flushdb tests/"},
        }
    )
    assert reason is None
