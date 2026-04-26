"""Writers for the Pillar A in-flight visibility fields (issue #1172).

Exposes a single public helper, ``record_tool_boundary``, called from both
``pre_tool_use_hook`` (``clear=False``, sets ``current_tool_name``) and
``post_tool_use_hook`` (``clear=True``, sets it back to None). The helper
resolves the in-flight ``AgentSession`` via the ``AGENT_SESSION_ID`` env var
(set by the worker when spawning the session).

Design choices:

- **Per-session 5-second in-memory cooldown.** Tight tool loops (100+ calls
  in 60s) would otherwise produce a Redis write storm. The cooldown is a
  best-effort coalesce: writes within the window are dropped silently. The
  cooldown is process-local — fine because each session runs in one harness
  subprocess and the operator-facing dashboard tolerates 5s eventual
  consistency.
- **Fail closed (silently).** Every Popoto/Redis interaction is wrapped in
  try/except. A failure logs at DEBUG and returns False. The hook return
  value is unaffected — the agent never crashes because liveness writes
  failed.
- **No backfill.** Sessions started before this commit lands keep ``None``
  on the new fields until their next tool boundary fires. The dashboard
  renders them gracefully.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from threading import Lock

logger = logging.getLogger(__name__)

# Per-session cooldown window (seconds). Bounds Redis write rate under tight
# tool loops; chosen to match the dashboard refresh cadence.
COOLDOWN_WINDOW_SEC = 5.0

# Last-write timestamps keyed by AgentSession.session_id. Process-local;
# protected by a mutex so concurrent hook invocations don't race.
_last_write_at: dict[str, float] = {}
_lock = Lock()


def _reset_cooldown_for_tests() -> None:
    """Test-only helper: clear the cooldown map so each test starts fresh."""
    with _lock:
        _last_write_at.clear()


def _is_in_cooldown(session_id: str, now: float) -> bool:
    with _lock:
        prev = _last_write_at.get(session_id)
        if prev is not None and (now - prev) < COOLDOWN_WINDOW_SEC:
            return True
        _last_write_at[session_id] = now
    return False


def _save_tool_boundary(session_id: str, tool_name: str | None, ts: datetime) -> bool:
    """Apply the field write to the AgentSession matching ``session_id``.

    Isolated for monkeypatch-driven test injection of save failures.
    Returns True on a successful save, False otherwise.
    """
    from models.agent_session import AgentSession

    matches = list(AgentSession.query.filter(session_id=session_id))
    if not matches:
        logger.debug("[liveness] no AgentSession for session_id=%s — skip", session_id)
        return False

    entry = matches[0]
    entry.current_tool_name = tool_name
    entry.last_tool_use_at = ts
    entry.save(update_fields=["current_tool_name", "last_tool_use_at"])
    return True


def record_tool_boundary(*, tool_name: str | None, clear: bool) -> bool:
    """Record a tool boundary on the in-flight AgentSession.

    Args:
        tool_name: The tool's name (e.g. "Read", "Bash"). Ignored when
            ``clear`` is True; ``current_tool_name`` is unconditionally set
            to None in that case.
        clear: True for PostToolUse (set current_tool_name=None); False for
            PreToolUse (set current_tool_name=tool_name).

    Returns:
        True if a write was applied, False if the call was a no-op
        (cooldown, missing AGENT_SESSION_ID, no matching session, or save
        failure). Never raises.
    """
    session_id = os.environ.get("AGENT_SESSION_ID")
    if not session_id:
        return False

    now = time.time()
    if _is_in_cooldown(session_id, now):
        return False

    name_to_write: str | None = None if clear else (tool_name or None)
    try:
        return _save_tool_boundary(
            session_id=session_id,
            tool_name=name_to_write,
            ts=datetime.now(tz=UTC),
        )
    except Exception as e:
        logger.debug("[liveness] record_tool_boundary failed (non-fatal): %s", e)
        return False


def record_turn_boundary() -> bool:
    """Bump ``last_turn_at`` for the in-flight AgentSession.

    Called from the SDK client's ``result`` event handler. Subject to the
    same cooldown and failure-handling guarantees as ``record_tool_boundary``.
    """
    session_id = os.environ.get("AGENT_SESSION_ID")
    if not session_id:
        return False

    now = time.time()
    # Use a separate cooldown bucket per metric to avoid result-vs-tool
    # interference (a result event during a tight tool loop should still
    # bump last_turn_at promptly).
    bucket_key = f"{session_id}:turn"
    with _lock:
        prev = _last_write_at.get(bucket_key)
        if prev is not None and (now - prev) < COOLDOWN_WINDOW_SEC:
            return False
        _last_write_at[bucket_key] = now

    try:
        from models.agent_session import AgentSession

        matches = list(AgentSession.query.filter(session_id=session_id))
        if not matches:
            return False
        entry = matches[0]
        entry.last_turn_at = datetime.now(tz=UTC)
        entry.save(update_fields=["last_turn_at"])
        return True
    except Exception as e:
        logger.debug("[liveness] record_turn_boundary failed (non-fatal): %s", e)
        return False


def record_thinking_excerpt(text: str) -> bool:
    """Persist the last 280 chars of extended-thinking content.

    Called by the SDK client's stream-event handler when accumulating thinking
    deltas. Cap at 280 chars (tweet length) — small enough to render, large
    enough to be informative. Subject to the same cooldown and failure
    semantics as the other writers.
    """
    session_id = os.environ.get("AGENT_SESSION_ID")
    if not session_id or not text:
        return False

    now = time.time()
    bucket_key = f"{session_id}:thinking"
    with _lock:
        prev = _last_write_at.get(bucket_key)
        if prev is not None and (now - prev) < COOLDOWN_WINDOW_SEC:
            return False
        _last_write_at[bucket_key] = now

    excerpt = text[-280:]
    try:
        from models.agent_session import AgentSession

        matches = list(AgentSession.query.filter(session_id=session_id))
        if not matches:
            return False
        entry = matches[0]
        entry.recent_thinking_excerpt = excerpt
        entry.save(update_fields=["recent_thinking_excerpt"])
        return True
    except Exception as e:
        logger.debug("[liveness] record_thinking_excerpt failed (non-fatal): %s", e)
        return False
