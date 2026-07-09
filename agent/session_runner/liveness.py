"""Single authoritative ``sdk_ever_output`` derivation (owner directive, 2026-07-07).

Owned by the runner package — which already owns subprocess spawn/kill
(``runner.py``) — per Tom's design directive: *"One authoritative liveness
signal makes the most sense. As much as we can strengthen a single module,
let's do that instead of manipulating the worker."*

``agent/session_health.py`` (the worker) imports :func:`derive_sdk_ever_output`
and calls it at all four of its recovery-path derivation sites rather than
inlining the OR expression itself. See
``docs/plans/headless-runner-zombie-liveness.md`` for the full root-cause
analysis and the four call sites this feeds.
"""

from __future__ import annotations

from typing import Any


def derive_sdk_ever_output(entry: Any) -> bool:
    """Return True iff the SDK has EVER produced recognized output.

    True when any of three per-turn/per-stream fields on the ``AgentSession``
    (or session-health entry proxy) ``entry`` is set:

    - ``last_tool_use_at`` — a tool boundary fired (PreToolUse/PostToolUse
      CLI hooks).
    - ``last_turn_at`` — a turn boundary completed (harness ``result`` event,
      via ``agent.hooks.liveness_writers.record_turn_boundary``).
    - ``last_stdout_at`` — the headless stream produced any output at all
      (``init`` event or subsequent stdout line, via
      ``SessionRunner._stamp_stdout_liveness``). This is the headless
      replacement for the PTY-era ``last_pty_read_loop_at`` liveness signal
      that the granite teardown dropped (#1843 Gap B).

    This is a presence check, not a freshness check — it answers "has the
    SDK EVER produced output," which is what the never-started gate and the
    reprieve-cap guard both need. Freshness-based mid-turn cadence checks
    (e.g. ``_has_progress`` sub-check A) are a separate, untouched concern.

    Never raises: missing attributes default to ``None`` via ``getattr``.
    """
    return bool(
        getattr(entry, "last_tool_use_at", None)
        or getattr(entry, "last_turn_at", None)
        or getattr(entry, "last_stdout_at", None)
    )
