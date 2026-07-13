"""Stalled-session advisory classifier — pure read-only stall signal detector.

Analyses a session's telemetry event window and optional project-level health
counters to produce a three-level verdict: healthy / suspect / stalled.

Design constraints:
  - Zero writes: no Redis mutations, no file writes, no side effects.
  - Fail-soft: any exception inside classify_session_stall returns "healthy".
  - No import from agent.session_health — this classifier must never pull in
    the kill/recovery machinery (enforced by the test suite).
  - Uses bridge.utc.to_unix_ts for all datetime → float conversions.

Usage::

    from agent.session_stall_classifier import classify_session_stall, StallVerdict
    from agent.session_telemetry import read_session_timeline

    events = read_session_timeline(session_id)
    verdict = classify_session_stall(events, session=session_obj)
    if verdict.level == "stalled":
        ...
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

from agent.session_runner.liveness import has_demonstrable_activity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Probe-status gate
# ---------------------------------------------------------------------------

# Statuses in which a session has *begun execution* (has a trace file).
# "pending" is deliberately excluded — stall detection for pending sessions
# belongs to monitoring/session_watchdog.py (issue #1313).
# "dormant" / "waiting_for_children" / "superseded" are also excluded because
# they have distinct recovery owners (see models/session_lifecycle.py).
_RUNNING_PROBE_STATUSES: frozenset[str] = frozenset(
    {"running", "active", "paused", "paused_circuit"}
)

# ---------------------------------------------------------------------------
# Threshold constants — pinned, documented, tunable
# ---------------------------------------------------------------------------

# Live never-started grace: a session in _RUNNING_PROBE_STATUSES with zero
# turn_start events is not flagged until it has been running for at least this
# many seconds.
#
# Sizing (2026-07-13): set to 4x the normal expected cold-start-to-first-signal.
# Empirically, healthy SDLC turns complete end-to-end in 7-10 min, and Opus
# cold-start-to-first-token runs 15-20 min in the heavy-context case (issue
# #1227). The pre-`system/init` window is dominated by MCP-fleet startup (init
# is gated on every configured MCP server connecting), which the output-based
# progress signal cannot observe in time. Anchoring "normal expected" at ~5 min
# and taking 4x gives a 20-min window that clears the documented worst case
# without waiting on a signal we can't expect to be timely. Genuine hangs inside
# this widened window are caught out-of-band by the CPU-delta liveness probe
# (short-term hang detector), not by output silence. Env-tunable.
NEVER_STARTED_GRACE_SECS: int = int(os.environ.get("NEVER_STARTED_GRACE_SECS", "1200"))

# Confirmation margin added on top of NEVER_STARTED_GRACE_SECS before the
# _never_started_past_grace predicate fires. Sized to cover worst-case
# cold-start-to-first-turn latency (runner subprocess spawn + persona prime).
# Provisional safety-chosen starting value — tune via env / adjust freely;
# no structural change needed.
NEVER_STARTED_CONFIRM_MARGIN_SECS: int = int(
    os.environ.get("NEVER_STARTED_CONFIRM_MARGIN_SECS", "30")
)

# Sustained idle window: an idle_gap event whose duration exceeds this
# threshold raises the advisory to at least "suspect".
IDLE_SUSPECT_SECS: int = 300  # 5 min

# Sustained stall threshold: idle past this window with no offsetting
# evidence → "stalled".
IDLE_STALL_SECS: int = 600  # 10 min

# Tool-timeout count that triggers "suspect" corroboration.
TOOL_TIMEOUT_SUSPECT_COUNT: int = 3

# Recovery attempt count that corroborates "suspect".
RECOVERY_SUSPECT_COUNT: int = 2

# ---------------------------------------------------------------------------
# Terminal statuses (mirror models/session_lifecycle.py — no circular import)
# ---------------------------------------------------------------------------

_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "killed", "abandoned", "cancelled"}
)

# Kill-bearing to_status values that flag a stalled session.
_KILL_STATUSES: frozenset[str] = frozenset({"killed", "failed", "cancelled"})

# ---------------------------------------------------------------------------
# StallVerdict
# ---------------------------------------------------------------------------


@dataclass
class StallVerdict:
    """Advisory classification for a single session's stall state.

    Attributes:
        level:   "healthy" | "suspect" | "stalled"
        reason:  Short machine-readable slug describing the primary signal.
        signals: Dict of debuggability data (thresholds, raw values, counters).
    """

    level: str
    reason: str
    signals: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_session_stall(
    events: list[dict],
    *,
    session=None,
    project_counters: dict | None = None,
) -> StallVerdict:
    """Classify a session's stall level from its telemetry event window.

    Args:
        events:           Ordered list of telemetry event dicts (from
                          read_session_timeline).  May be empty.
        session:          Optional AgentSession instance.  Used only for
                          the never-started probe and must have
                          ``status``, ``started_at``, and ``created_at``
                          attributes.
        project_counters: Optional dict of project-level health counters
                          (e.g. from read_project_health_counters).  Weak
                          corroborating signal only.

    Returns:
        StallVerdict with level in {"healthy", "suspect", "stalled"}.
        On any unexpected exception returns StallVerdict("healthy",
        "unclassifiable", {}) so the caller is never disrupted.
    """
    try:
        return _classify(events, session=session, project_counters=project_counters)
    except Exception as exc:  # noqa: BLE001
        logger.debug("classify_session_stall swallowed exception: %r", exc)
        return StallVerdict("healthy", "unclassifiable", {})


def read_project_health_counters(project_key: str) -> dict[str, int]:
    """Read project-level health counters from Redis (read-only).

    Reads keys of the form ``{project_key}:session-health:{metric}``.
    Returns an empty dict on any Redis error (fail-soft).

    Never mutates Redis — no incr, delete, sadd, srem, or any write
    operation is performed.

    Args:
        project_key: The project partition key (e.g. "valor").

    Returns:
        Dict mapping metric names to integer counts.
        Keys include e.g. "tool_timeouts:tier1", "recoveries:kill".
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB  # type: ignore[import]

        r = POPOTO_REDIS_DB
        prefix = f"{project_key}:session-health:"
        result: dict[str, int] = {}

        # Scan for matching keys to avoid hardcoding metric names.
        for raw_key in r.scan_iter(f"{prefix}*"):
            key_str = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
            metric_name = key_str[len(prefix) :]
            raw_val = r.get(raw_key)
            if raw_val is not None:
                try:
                    result[metric_name] = int(raw_val)
                except (ValueError, TypeError):
                    pass
        return result
    except Exception as exc:  # noqa: BLE001
        logger.debug("read_project_health_counters failed for %r: %r", project_key, exc)
        return {}


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------


def _has_demonstrable_progress(session) -> bool:
    """Return True if the session's own fields prove it has started and is working.

    The ``never_started`` probe keys off telemetry ``turn_start`` events, but
    a session's telemetry write can lag or be lost. Delegates to the
    consolidated leaf
    :func:`agent.session_runner.liveness.has_demonstrable_activity` (#2004
    Task 2), which consults the AgentSession's own progress fields as ground
    truth — reading ONLY ``{turn_count, last_tool_use_at}``:

      * ``turn_count > 0``           → the session has taken at least one turn
        (numeric-string ``turn_count`` coerced defensively — parity with the
        crash-signature caller).
      * ``last_tool_use_at`` fresh   → a tool fired within the suspect window.

    "Fresh" means within :data:`IDLE_SUSPECT_SECS`, passed as the leaf's
    ``freshness_window`` — this classifier runs live and must catch
    *currently* stalled sessions. Missing/malformed attributes count as
    no-progress and the leaf never raises, falling through to the
    elapsed-grace check rather than masking a genuine never-started session.
    """
    return has_demonstrable_activity(session, freshness_window=IDLE_SUSPECT_SECS)


def _classify(
    events: list[dict],
    *,
    session=None,
    project_counters: dict | None = None,
) -> StallVerdict:
    """Core classification logic.  Raises on unexpected errors (caller wraps)."""
    from bridge.utc import to_unix_ts  # local import to avoid top-level coupling

    events = events or []
    counters = project_counters or {}

    # ------------------------------------------------------------------
    # 1. Never-started probe (only for _RUNNING_PROBE_STATUSES)
    # ------------------------------------------------------------------
    session_status = getattr(session, "status", None)
    has_turn_start = any(e.get("type") == "turn_start" for e in events)

    if session is not None and session_status in _RUNNING_PROBE_STATUSES:
        if not has_turn_start:
            # ----------------------------------------------------------
            # 1a. demonstrable-progress probe (never_started false-positive guard)
            # ----------------------------------------------------------
            # "No turn_start event" does not always imply "never started"
            # (telemetry writes can lag or be lost). Trust the session's own
            # progress fields to avoid a false positive on a session that is
            # demonstrably working.
            if _has_demonstrable_progress(session):
                return StallVerdict(
                    "healthy",
                    "progress_fields_fresh",
                    {
                        "turn_count": getattr(session, "turn_count", None),
                        "session_status": session_status,
                    },
                )
            # Session is in a probe status but has never emitted a turn_start.
            started_ref = getattr(session, "started_at", None) or getattr(
                session, "created_at", None
            )
            ts = to_unix_ts(started_ref)
            if ts is not None:
                elapsed = time.time() - ts
                # Grace + confirmation margin: the margin covers worst-case
                # cold-start latency (runner subprocess spawn + persona prime)
                # before we are willing to call a session stalled.
                confirm_threshold = NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS
                if elapsed > confirm_threshold:
                    return StallVerdict(
                        "stalled",
                        "never_started",
                        {
                            "elapsed_secs": elapsed,
                            "grace_secs": NEVER_STARTED_GRACE_SECS,
                            "confirm_margin_secs": NEVER_STARTED_CONFIRM_MARGIN_SECS,
                            "session_status": session_status,
                        },
                    )
            # Could not resolve timestamp — fall through to healthy.
            # (fail-soft: don't flag when we can't measure)
    elif not events:
        # No session object or non-probe status with no events — nothing to analyse.
        return StallVerdict("healthy", "not_started_probe", {})

    if not events:
        return StallVerdict("healthy", "no_events", {})

    # ------------------------------------------------------------------
    # 2. Analyse the event window
    # ------------------------------------------------------------------

    # Track idle gap signals.
    max_idle_secs: float = 0.0
    recent_idle_secs: float = 0.0

    # Track kill-bearing transitions.
    kill_transition_seen = False

    # Track turn activity (any turn_end or turn_start after first event).
    recent_turn_ts: float | None = None

    for event in events:
        etype = event.get("type") or ""

        # --- idle_gap events ---
        if etype == "idle_gap":
            data = event.get("data") or {}
            raw_dur = (
                event.get("gap_seconds")
                or data.get("gap_seconds")
                or data.get("duration_secs")
                or data.get("duration")
                or event.get("duration_secs")
                or event.get("duration")
            )
            if raw_dur is not None:
                try:
                    dur = float(raw_dur)
                    if dur > max_idle_secs:
                        max_idle_secs = dur
                    recent_idle_secs = dur  # last observed idle gap
                except (TypeError, ValueError):
                    pass

        # --- status_transition events: look for kill-bearing transitions ---
        elif etype == "status_transition":
            data = event.get("data") or {}
            to_status = data.get("to") or event.get("to") or ""
            if to_status in _KILL_STATUSES:
                kill_transition_seen = True

        # --- turn events: track last activity timestamp ---
        elif etype in {"turn_start", "turn_end"}:
            ts_raw = event.get("ts") or event.get("timestamp")
            if ts_raw is not None:
                ts_val = to_unix_ts(ts_raw)
                if ts_val is not None:
                    if recent_turn_ts is None or ts_val > recent_turn_ts:
                        recent_turn_ts = ts_val

    # ------------------------------------------------------------------
    # 3. Corroborate with project_counters (weak signal)
    # ------------------------------------------------------------------
    # tool_use timeout events: not tracked per-event; project_counters aggregates
    # tool_timeouts more reliably, so we rely on that below instead.
    tool_timeout_total = sum(v for k, v in counters.items() if k.startswith("tool_timeouts"))
    recovery_total = sum(v for k, v in counters.items() if k.startswith("recoveries"))
    counter_suspect = (
        tool_timeout_total >= TOOL_TIMEOUT_SUSPECT_COUNT or recovery_total >= RECOVERY_SUSPECT_COUNT
    )

    # ------------------------------------------------------------------
    # 4. Determine verdict
    # ------------------------------------------------------------------

    # Recent turn activity: if the last turn was very recent (< IDLE_SUSPECT_SECS ago)
    # the session is healthy regardless of max idle gap in the history.
    if recent_turn_ts is not None:
        age_since_turn = time.time() - recent_turn_ts
        if age_since_turn < IDLE_SUSPECT_SECS:
            return StallVerdict(
                "healthy",
                "recent_turn_activity",
                {
                    "age_since_turn_secs": age_since_turn,
                    "idle_suspect_threshold": IDLE_SUSPECT_SECS,
                },
            )

    # Kill-bearing transition in the event window.
    if kill_transition_seen:
        return StallVerdict(
            "stalled",
            "kill_transition",
            {
                "max_idle_secs": max_idle_secs,
                "counter_suspect": counter_suspect,
            },
        )

    # Idle gap analysis.
    idle_to_check = max(max_idle_secs, recent_idle_secs)

    if idle_to_check >= IDLE_STALL_SECS:
        return StallVerdict(
            "stalled",
            "idle_gap_exceeded_stall",
            {
                "idle_secs": idle_to_check,
                "stall_threshold": IDLE_STALL_SECS,
                "counter_suspect": counter_suspect,
            },
        )

    if idle_to_check >= IDLE_SUSPECT_SECS:
        return StallVerdict(
            "suspect",
            "idle_gap_exceeded_suspect",
            {
                "idle_secs": idle_to_check,
                "suspect_threshold": IDLE_SUSPECT_SECS,
                "stall_threshold": IDLE_STALL_SECS,
                "counter_suspect": counter_suspect,
            },
        )

    # Counter-only suspect (weak signal — not enough on its own to elevate
    # further, but worth surfacing).
    if counter_suspect:
        return StallVerdict(
            "suspect",
            "project_counter_suspect",
            {
                "tool_timeout_total": tool_timeout_total,
                "recovery_total": recovery_total,
                "tool_timeout_threshold": TOOL_TIMEOUT_SUSPECT_COUNT,
                "recovery_threshold": RECOVERY_SUSPECT_COUNT,
            },
        )

    return StallVerdict("healthy", "no_concerning_signals", {})
