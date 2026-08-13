#!/usr/bin/env python3
"""PreToolUse hook: Block agent-issued Redis flush commands, unconditionally.

Issue #2645. On 2026-08-07 06:24Z an ad-hoc debug script called
`POPOTO_REDIS_DB.flushdb()`. It meant to hit a test db, but the shell already
had a production `REDIS_URL` exported, so the client's `db` was 0 and 25,825
keys of live agent state (AgentSession, Room, Job, subconscious memory,
steering lists, DM coverage epochs) were destroyed. Recovery took an AOF
point-in-time restore, ~30s of Redis downtime, and ~2 minutes of discarded
writes. That was the *second* incident of this exact class -- the first, on
2026-06-03, was unrecoverable because AOF was not yet enabled.

`validate_no_raw_redis_delete.py` already blocks the less-destructive raw
Redis writes/reads, but only when the command also carries a Popoto
vocabulary token (`_POPOTO_CONTEXT`). A bare `redis.Redis().flushdb()` or
`redis-cli -n 0 flushdb` carries no such token, so that gate is wrong for
flush: this validator carries **no** Popoto-context gate. `flushdb`/`flushall`
are unconditionally dangerous regardless of what vocabulary surrounds them.

This is Layer 3 of a four-layer defense (see
docs/plans/redis-flush-hardening.md). Layer 1 is a process-wide monkeypatch
guard (`tools/redis_flush_guard.py`) that stops the call even when it is
buried inside an already-written file, which this Bash-string validator
cannot see. Both layers share one escape hatch,
`REDIS_PRODUCTION_FLUSH_OK=1`, so an agent that needs to override learns one
name instead of two.

What is blocked: `.flushdb(...)`/`.flushall(...)` call shapes and
`redis-cli ... FLUSHDB`/`FLUSHALL` invocations. What is not: `grep`/`rg` over
the words, prose, and anything already carrying the escape prefix.
"""

import json
import re
import sys

# The same override name Layer 1 (tools/redis_flush_guard.py) reads. Checked
# FIRST, before any block pattern, so the escape this hook's own block
# message quotes is the escape that actually works (D5a).
_ESCAPE = re.compile(r"\bREDIS_PRODUCTION_FLUSH_OK=1\b")

# Call shapes only -- never the bare word, so `grep -rn flushdb tests/`,
# `rg flushall`, and prose mentioning either word pass through untouched.
_BLOCK_PATTERNS = [
    re.compile(r"\.flushdb\s*\("),
    re.compile(r"\.flushall\s*\("),
    re.compile(r"redis-cli\s+.*\bFLUSHDB\b", re.IGNORECASE),
    re.compile(r"redis-cli\s+.*\bFLUSHALL\b", re.IGNORECASE),
]

_REASON = """Blocked: unconditional Redis flush call (issue #2645).

`.flushdb(...)` / `.flushall(...)` and `redis-cli ... FLUSHDB` / `FLUSHALL`
are blocked here unconditionally -- not gated on Popoto vocabulary -- because
a flush wiped live production Redis twice: 2026-06-03 (unrecoverable, AOF was
not yet on) and 2026-08-07 (25,825 keys, ~30s downtime, an AOF point-in-time
restore). Both times the script believed it was targeting a test db and was
not.

If you genuinely mean to flush a per-process claimed test db (see
tests/db_claim.py for the claim idiom), prefix the command with the escape
this hook and the process-wide guard in tools/redis_flush_guard.py both
honor:

  REDIS_PRODUCTION_FLUSH_OK=1 python -c "..."

Never point that prefix at a command that could reach production. If you are
unsure which db a client targets, do not flush -- inspect first with
`redis-cli -n <db> DBSIZE`."""


def find_violation(command: str | None) -> str | None:
    """Pure predicate: return a block-reason string if `command` performs an
    unconditional Redis flush, else None. Never raises for well-formed input;
    returns None for empty/None input.

    Args:
        command: The Bash command string from the hook payload.

    Returns:
        The reason string to block with, or None to allow.
    """
    if not command:
        return None
    if _ESCAPE.search(command):
        return None
    for pattern in _BLOCK_PATTERNS:
        if pattern.search(command):
            return _REASON
    return None


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        hook_input = json.loads(raw)
    except (json.JSONDecodeError, ValueError, OSError):
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
