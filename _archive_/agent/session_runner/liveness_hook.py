"""Stdlib-only ``PreToolUse`` liveness stamp for headless runner sessions.

Registered by :func:`agent.session_runner.hook_edge.generate_hook_settings` on
a ``matcher: ""`` ``PreToolUse`` entry, so it fires for EVERY tool call the
spawned ``claude -p`` makes — including tool calls made inside an in-process
subagent, which carry the parent session's hook settings (verified empirically
2026-07-30: a ``Bash`` call issued by an ``Agent`` subagent fires the parent's
``PreToolUse`` hook with the parent ``session_id``).

Why this exists (the #1930 regression, root-caused 2026-07-30)
--------------------------------------------------------------
``agent_session_queue._session_progress_ts`` — the progress-deadline
watchdog's freshness clock — reads ``last_tool_use_at`` / ``last_turn_at`` /
``acquired_at``. ``last_tool_use_at`` is stamped ONLY by this repo's own
``.claude/hooks/pre_tool_use.py``, which exists in *this* repo's project
settings and nowhere else. A PM session running against any other repo
(cyndra-consulting, client repos) therefore has NO signal that ticks
mid-turn: the clock degenerates to ``acquired_at`` and every turn longer than
``SESSION_PROGRESS_DEADLINE_S`` (1800s) is killed regardless of how much work
is happening. Observed three times in 24h on one Cyndra thread, each at
1799.99–1800.4s, twice mid-``/deploy``.

Why stdlib-only (and not a call into ``liveness_writers``)
----------------------------------------------------------
A ``PreToolUse`` hook runs as a fresh subprocess on EVERY tool call. Importing
the ORM to write the field directly costs ~2.0s per tool call measured on this
machine (``agent.hooks.liveness_writers`` → popoto → redis), against ~0.07s
for a stdlib-only interpreter start — a ~30x tax on every tool call in every
session. So this hook does the cheapest possible thing: write the current Unix
timestamp to a per-session marker file. The worker reads it through
:func:`agent.session_runner.liveness.tool_activity_ts`, which already runs on
the watchdog's own 30s poll — no extra process, no Redis write on the hot path.

Deliberately does NOT stamp ``current_tool_name``. That field arms the
per-tool timeout sub-loop (``session_health._check_tool_timeout``, 300s default
tier), and arming a killer for sessions that have never had one is a behavior
change this fix does not need: the wedge clock only needs *freshness*.

Contract: ALWAYS exits 0 and never writes to stderr. A ``PreToolUse`` hook that
exits 2 BLOCKS the tool call — a liveness stamp must never be able to stop a
turn.
"""

from __future__ import annotations

import sys
import time


def stamp(path: str, now: float | None = None) -> bool:
    """Write ``now`` (Unix seconds) to ``path``. Returns False on any failure.

    Whole-file overwrite of a ~18-byte payload — the last writer wins, which is
    exactly the semantics wanted (freshest tool boundary). Never raises.
    """
    try:
        with open(path, "w") as f:
            f.write(f"{now if now is not None else time.time():.6f}")
        return True
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    """Always returns 0 — see the module docstring's blocking-exit contract."""
    args = sys.argv[1:] if argv is None else argv
    try:
        # Drain stdin so Claude Code's hook writer never sees EPIPE. The
        # payload is unused: this hook stamps time, it does not classify.
        if not sys.stdin.isatty():
            sys.stdin.read()
    except Exception:  # noqa: S110 — nothing to log to; stderr must stay clean
        pass
    if args:
        stamp(args[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
