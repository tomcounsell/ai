"""Crash signature extractor — reduce a terminal session's telemetry trace to a
stable, normalized signature key.

Normalization contract:
  - Keep: event type, and for status_transition: to, kill.confirmed_dead,
    kill.signal_sent, presence/absence of idle_gap (bucketed coarsely:
    <5min -> "short", 5-30min -> "medium", >30min -> "long").
  - Drop: pids, timestamps, exact durations, token counts.
  - The human-readable form is a short string like:
    "idle_gap[medium]+status_transition[to=failed,dead=false,sig=SIGTERM]"
  - The hash is sha256[:16] of the canonical human form.
  - The signature_class is "NON_RESUMABLE_DETERMINISTIC" for never-started
    sessions, or a descriptive string for resumable ones.

Usage::

    from agent.crash_signature import extract_signature, CrashSignatureKey

    events = read_session_timeline(session_id)
    key = extract_signature(events, session=session)
    print(key.human_form, key.hash, key.resumable)
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from agent.session_runner.liveness import has_demonstrable_activity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TERMINAL_SUBSEQUENCE_LENGTH: int = 10  # Last N events examined for signature

# Idle gap bucket boundaries (seconds)
_IDLE_SHORT_MAX: float = 300.0  # <5 min
_IDLE_MEDIUM_MAX: float = 1800.0  # 5-30 min
# >30 min -> "long"

# Sentinel for unclassifiable traces
_UNCLASSIFIABLE_FORM = "unclassifiable"
_UNCLASSIFIABLE_CLASS = "unclassifiable"

# Class for never-started deterministic failures
NON_RESUMABLE_DETERMINISTIC = "NON_RESUMABLE_DETERMINISTIC"


# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------


@dataclass
class CrashSignatureKey:
    """Normalized crash signature key derived from a session's telemetry trace.

    Attributes:
        human_form: Short human-readable description of the crash pattern.
        hash: sha256[:16] hex digest of ``human_form`` — stable across runs.
        signature_class: Broad category; ``NON_RESUMABLE_DETERMINISTIC`` for
            sessions that never started, otherwise a descriptive class string.
        resumable: False if ``NON_RESUMABLE_DETERMINISTIC``, True otherwise.
        escalated: Mutable flag set externally by reflection when an alert is sent.
    """

    human_form: str
    hash: str
    signature_class: str
    resumable: bool
    escalated: bool = field(default=False)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _bucket_idle_gap(seconds: float) -> str:
    """Return a coarse bucket label for an idle-gap duration in seconds."""
    if seconds < _IDLE_SHORT_MAX:
        return "short"
    if seconds < _IDLE_MEDIUM_MAX:
        return "medium"
    return "long"


def _normalize_status_transition(event: dict) -> str:
    """Normalize a status_transition event to a stable token."""
    data = event.get("data") or {}
    to_status = data.get("to") or event.get("to") or "unknown"
    kill_info = data.get("kill") or event.get("kill")
    if kill_info and isinstance(kill_info, dict):
        confirmed = str(kill_info.get("confirmed_dead", "")).lower()
        signal = kill_info.get("signal_sent") or "none"
        return f"status_transition[to={to_status},dead={confirmed},sig={signal}]"
    return f"status_transition[to={to_status}]"


def _normalize_idle_gap(event: dict) -> str:
    """Normalize an idle_gap event to a stable bucket token."""
    data = event.get("data") or {}
    seconds_raw = data.get("gap_seconds") or data.get("seconds") or event.get("gap_seconds")
    if seconds_raw is not None:
        try:
            bucket = _bucket_idle_gap(float(seconds_raw))
        except (TypeError, ValueError):
            bucket = "present"
    else:
        # Presence only — no duration data; treat as unknown bucket
        bucket = "present"
    return f"idle_gap[{bucket}]"


def _sha256_hex16(text: str) -> str:
    """Return the first 16 hex characters of the sha256 digest of *text*."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _has_turn_start(events: list[dict]) -> bool:
    """Return True if any event in *events* has type ``turn_start``."""
    return any(e.get("type") == "turn_start" for e in events)


def _has_telemetry_truncated(events: list[dict]) -> bool:
    """Return True if the trace contains a telemetry_truncated marker."""
    return any(e.get("type") == "telemetry_truncated" for e in events)


def _has_demonstrable_progress(session: object | None) -> bool:
    """Return True if the session's own fields prove it started and did work.

    Delegates to the consolidated leaf
    :func:`agent.session_runner.liveness.has_demonstrable_activity` (#2004
    Task 2), which reads ONLY ``{turn_count, last_tool_use_at}`` and never
    raises. ``liveness`` is stdlib-only, so this extractor stays
    dependency-light — it still does not import the stall classifier, kill
    machinery, or ``IDLE_SUSPECT_SECS``.

    Deliberate divergence from the stall classifier (critique C2):
    ``freshness_window=None`` → presence-only. The stall classifier runs live
    and gates ``last_tool_use_at`` on ``IDLE_SUSPECT_SECS`` to catch
    *currently* stalled sessions. This extractor instead runs over
    already-terminal sessions inside a 2h-lookback reflection, so "now" is
    minutes-to-hours after death; a freshness window would read stale/False
    for exactly the sessions we want to rescue. Any recorded tool use is
    therefore ground-truth progress via presence only.
    """
    return has_demonstrable_activity(session, freshness_window=None)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_signature(
    events: list[dict],
    *,
    session: object | None = None,
    subsequence_length: int = TERMINAL_SUBSEQUENCE_LENGTH,
) -> CrashSignatureKey:
    """Reduce a terminal session's telemetry trace to a normalized signature.

    The function never raises — any exception in the classification logic
    yields the ``"unclassifiable"`` sentinel signature.

    Determinism guardrail (in priority order):

    1. No ``turn_start`` event in the full trace
       -> ``NON_RESUMABLE_DETERMINISTIC``, ``resumable=False`` — **unless** the
       session's own progress fields prove progress. A missing ``turn_start`` is
       overridden to the normal resumable path when
       ``_has_demonstrable_progress(session)`` is True (``turn_count > 0`` or a
       recorded ``last_tool_use_at``), because telemetry writes can lag or be
       lost after a crash. Only a session with no ``turn_start`` AND no
       demonstrable progress is stamped the deterministic never-started key.
    2. ``session.startup_failure_kind == "ceiling"`` + has ``turn_start``
       -> resumable, classification proceeds normally with a "ceiling" prefix
       (historical records only — nothing produces ``startup_failure_kind``
       after the PTY teardown, plan #1924; the value stays valid in old rows)

    Args:
        events: Ordered list of telemetry event dicts (earliest first).
            May be empty or contain unknown event types.
        session: Optional AgentSession (or any object with a
            ``startup_failure_kind`` attribute). Pass ``None`` if unavailable.
        subsequence_length: Number of trailing events to include in signature
            (default: ``TERMINAL_SUBSEQUENCE_LENGTH`` = 10).

    Returns:
        A ``CrashSignatureKey`` with stable ``human_form`` and ``hash``.
    """
    try:
        return _extract_signature_inner(
            events, session=session, subsequence_length=subsequence_length
        )
    except Exception as exc:
        logger.warning("extract_signature failed unexpectedly: %r — returning unclassifiable", exc)
        return _unclassifiable_key()


def _unclassifiable_key() -> CrashSignatureKey:
    return CrashSignatureKey(
        human_form=_UNCLASSIFIABLE_FORM,
        hash=_sha256_hex16(_UNCLASSIFIABLE_FORM),
        signature_class=_UNCLASSIFIABLE_CLASS,
        resumable=True,
    )


def _extract_signature_inner(
    events: list[dict],
    *,
    session: object | None,
    subsequence_length: int,
) -> CrashSignatureKey:
    """Core implementation (called from extract_signature's try block)."""

    # ------------------------------------------------------------------
    # Guard: empty event list -> unclassifiable
    # ------------------------------------------------------------------
    if not events:
        return _unclassifiable_key()

    # ------------------------------------------------------------------
    # Determinism guardrail — check startup_failure_kind first
    # ------------------------------------------------------------------
    startup_failure_kind: str | None = None
    if session is not None:
        startup_failure_kind = getattr(session, "startup_failure_kind", None)

    # ------------------------------------------------------------------
    # turn_start check (guardrail 1)
    # ------------------------------------------------------------------
    has_turn = _has_turn_start(events)

    if not has_turn and not _has_demonstrable_progress(session):
        # Genuine never-started: no telemetry turn_start AND the session's own
        # progress fields prove nothing happened.
        form = f"{NON_RESUMABLE_DETERMINISTIC}[no_turn_start]"
        return CrashSignatureKey(
            human_form=form,
            hash=_sha256_hex16(form),
            signature_class=NON_RESUMABLE_DETERMINISTIC,
            resumable=False,
        )

    # If turn_start is missing but the session's own fields prove progress
    # (turn_count > 0 or a recorded tool use), the telemetry write merely
    # lagged/was lost — fall through to the normal resumable-signature path
    # below rather than stamping the deterministic non-resumable key.

    # ------------------------------------------------------------------
    # Session started at least one turn — could be resumable.
    # Extract terminal subsequence for signature tokens.
    # ------------------------------------------------------------------
    tail = events[-subsequence_length:]

    tokens: list[str] = []
    has_truncated = _has_telemetry_truncated(events)

    for evt in tail:
        etype = evt.get("type")
        if etype == "idle_gap":
            tokens.append(_normalize_idle_gap(evt))
        elif etype == "status_transition":
            tokens.append(_normalize_status_transition(evt))
        elif etype in (
            "turn_start",
            "turn_end",
            "tool_use",
            "token_usage",
            "telemetry_truncated",
            "unknown",
        ):
            tokens.append(etype)
        else:
            # Unknown or future event type — include raw type name
            if etype:
                tokens.append(f"unknown[{etype}]")

    if not tokens:
        return _unclassifiable_key()

    # Derive signature class from terminal events
    sig_class = _derive_signature_class(tail, startup_failure_kind=startup_failure_kind)

    prefix_parts: list[str] = []
    if has_truncated:
        prefix_parts.append("truncated")
    if startup_failure_kind == "ceiling":
        prefix_parts.append("ceiling")

    human_form = "+".join(prefix_parts + tokens)

    return CrashSignatureKey(
        human_form=human_form,
        hash=_sha256_hex16(human_form),
        signature_class=sig_class,
        resumable=True,
    )


def _derive_signature_class(tail: list[dict], *, startup_failure_kind: str | None) -> str:
    """Derive a human-readable signature class from the terminal event subsequence."""
    terminal_to: str | None = None
    has_kill = False
    kill_signal: str | None = None

    for evt in reversed(tail):
        if evt.get("type") == "status_transition":
            data = evt.get("data") or {}
            terminal_to = data.get("to") or evt.get("to")
            kill_info = data.get("kill") or evt.get("kill")
            if kill_info and isinstance(kill_info, dict):
                has_kill = True
                kill_signal = kill_info.get("signal_sent")
            break

    has_idle_gap = any(e.get("type") == "idle_gap" for e in tail)

    parts: list[str] = []

    if startup_failure_kind == "ceiling":
        parts.append("ceiling_timeout")
    if has_idle_gap:
        parts.append("idle_gap")
    if has_kill and kill_signal:
        parts.append(f"kill_{kill_signal.lower()}")
    elif has_kill:
        parts.append("kill_unknown")
    if terminal_to:
        parts.append(f"terminal_{terminal_to}")

    return "|".join(parts) if parts else "unclassified_mid_stream"
