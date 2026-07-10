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

import time
from datetime import UTC, datetime
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


def _read_field(entry: Any, name: str) -> Any:
    """Read ``name`` from a dict-style or attribute-style entry.

    Both ``_has_demonstrable_progress`` forks historically read AgentSession
    objects via ``getattr(..., None)``; dict entries are accepted so the leaf
    is pure over either shape. Missing fields default to ``None``.
    """
    if isinstance(entry, dict):
        return entry.get(name)
    return getattr(entry, name, None)


def _as_unix_ts(val: Any) -> float | None:
    """Coerce a datetime / int / float / ISO-string to a Unix timestamp.

    Mirrors ``bridge.utc.to_unix_ts`` semantics (naive datetimes are treated
    as UTC — Popoto strips tzinfo on save) without importing it: this module
    stays stdlib-only so ``agent/crash_signature.py`` can import it where it
    deliberately cannot import ``agent/session_health.py``. Returns ``None``
    when the value cannot be coerced.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=UTC)
        return val.timestamp()
    if isinstance(val, int | float):
        return float(val)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    return None


def has_demonstrable_activity(entry: Any, *, freshness_window: float | None = None) -> bool:
    """Return True iff the entry's own fields prove it has taken a turn or used a tool.

    The consolidated leaf for the two ``_has_demonstrable_progress`` forks
    (#2004 Task 2): ``agent/session_stall_classifier.py`` (freshness-windowed,
    passes ``IDLE_SUSPECT_SECS``) and ``agent/crash_signature.py``
    (presence-only, passes ``None``). Reads ONLY ``{turn_count,
    last_tool_use_at}`` — the exact subset both forks already used.

    **B1 guard:** ``log_path`` / ``claude_session_uuid`` / ``last_stdout_at``
    / ``last_turn_at`` are deliberately NOT presence signals here — an
    init-only/log-only session must read no-progress for the stall/crash
    paths. (``session_health`` has its own wider leaf,
    :func:`derive_sdk_ever_output`, for the started-vs-never-started axis.)

    Semantics:

    - ``turn_count > 0`` (int) → progress. A numeric-string ``turn_count``
      is coerced defensively (``int(turn_count) > 0``) — parity ported from
      the crash_signature fork for a real persisted shape.
    - ``last_tool_use_at`` with ``freshness_window=None`` → presence-only:
      any recorded tool use is progress. The crash extractor runs over
      already-terminal sessions inside a lookback reflection, so a wall-clock
      window would read stale/False for exactly the sessions it rescues.
    - ``last_tool_use_at`` with a numeric ``freshness_window`` (seconds) →
      progress iff ``now - ts < freshness_window``. The stall classifier runs
      live and gates on ``IDLE_SUSPECT_SECS`` to catch *currently* stalled
      sessions. The window is arithmetic on the caller-supplied value; this
      module holds no freshness policy of its own.

    Never raises: ``None`` / missing / malformed fields read as no-progress.
    """
    if entry is None:
        return False
    try:
        turn_count = _read_field(entry, "turn_count")
        if isinstance(turn_count, int) and turn_count > 0:
            return True
        if isinstance(turn_count, str):
            try:
                if int(turn_count) > 0:
                    return True
            except (TypeError, ValueError):
                pass

        last_tool_use_at = _read_field(entry, "last_tool_use_at")
        if freshness_window is None:
            return last_tool_use_at is not None
        ts = _as_unix_ts(last_tool_use_at)
        if ts is not None and (time.time() - ts) < freshness_window:
            return True
    except Exception:  # noqa: BLE001 — never-raises contract (fail-soft to no-progress)
        return False
    return False
