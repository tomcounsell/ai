"""Writers for the Pillar A in-flight visibility fields (issue #1172).

Exposes a single public helper, ``record_tool_boundary``, called from both
``pre_tool_use_hook`` (``clear=False``, sets ``current_tool_name``) and
``post_tool_use_hook`` (``clear=True``, sets it back to None). The helper
resolves the in-flight ``AgentSession`` via ``agent.hooks.session_resolver``
(issue #2205), which prefers the true session-id env var and falls back to
a primary-key lookup on the per-run id env var -- covering the bridge-session
shape where the two differ.

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
import time
from datetime import UTC, datetime
from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.agent_session import AgentSession

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


def is_in_cooldown(bucket_key: str, now: float) -> bool:
    """Check-and-arm a cooldown bucket keyed by ``bucket_key``.

    Public so callers outside this module (e.g. the headless session runner's
    stdout-liveness stamp) can share the same lock-protected, per-bucket
    cooldown discipline instead of reimplementing it. Returns True if a prior
    write landed within :data:`COOLDOWN_WINDOW_SEC`; otherwise arms the
    bucket with ``now`` and returns False.
    """
    with _lock:
        prev = _last_write_at.get(bucket_key)
        if prev is not None and (now - prev) < COOLDOWN_WINDOW_SEC:
            return True
        _last_write_at[bucket_key] = now
    return False


def _save_tool_boundary(
    session: AgentSession,
    tool_name: str | None,
    ts: datetime,
    declared_timeout_s: float | None = None,
) -> bool:
    """Apply the field write to the already-resolved ``session``.

    Isolated for monkeypatch-driven test injection of save failures.
    Returns True on a successful save, False otherwise.

    Takes the resolved ``AgentSession`` directly (issue #2205) rather than
    re-querying by session_id -- the caller already resolved it via
    ``agent.hooks.session_resolver.resolve_inflight_session``.

    All three fields ride ONE save so ``current_tool_name`` and
    ``current_tool_timeout_s`` can never split-brain (issue #2145): a
    cooldown-dropped write drops the whole triple together.
    """
    session.current_tool_name = tool_name
    session.last_tool_use_at = ts
    session.current_tool_timeout_s = declared_timeout_s
    session.save(update_fields=["current_tool_name", "last_tool_use_at", "current_tool_timeout_s"])
    return True


def record_tool_boundary(
    *, tool_name: str | None, clear: bool, declared_timeout_s: float | None = None
) -> bool:
    """Record a tool boundary on the in-flight AgentSession.

    Args:
        tool_name: The tool's name (e.g. "Read", "Bash"). Ignored when
            ``clear`` is True; ``current_tool_name`` is unconditionally set
            to None in that case.
        clear: True for PostToolUse (set current_tool_name=None); False for
            PreToolUse (set current_tool_name=tool_name).
        declared_timeout_s: The tool call's own declared timeout in seconds
            (issue #2145) — today only Bash's ``timeout`` param, converted
            from milliseconds by the PreToolUse hook. Ignored (forced None)
            when ``clear`` is True. Read by ``_check_tool_timeout`` to raise
            the wedge budget above the tier default.

    Returns:
        True if a write was applied, False if the call was a no-op
        (cooldown, no in-flight session env var, no matching session, or
        save failure). Never raises.

    Note:
        ``clear=True`` (PostToolUse) bypasses the per-session cooldown so the
        per-tool timeout sub-loop in ``agent/session_health.py`` always sees
        the cleared ``current_tool_name`` immediately after the tool returns.
        See issue #1270.
    """
    from agent.hooks.session_resolver import inflight_cooldown_key, resolve_inflight_session

    bucket_key = inflight_cooldown_key()
    if not bucket_key:
        return False

    now = time.time()
    # PostToolUse writes (clear=True) bypass the cooldown — issue #1270.
    # The per-tool timeout sub-loop in `agent/session_health.py` interprets a
    # non-null `current_tool_name` whose `last_tool_use_at` exceeds the tier
    # budget as a wedge condition. The internal-tier budget is 30s; the 5s
    # cooldown could coalesce a fast PreToolUse→PostToolUse pair within the
    # window and leave `current_tool_name` populated, producing false-positive
    # tool-timeout recoveries. PreToolUse (clear=False) keeps the cooldown so
    # rapid-fire tool calls don't thrash the field.
    if not clear and is_in_cooldown(bucket_key, now):
        return False

    name_to_write: str | None = None if clear else (tool_name or None)
    timeout_to_write: float | None = None if clear else declared_timeout_s
    try:
        session = resolve_inflight_session()
        if session is None:
            return False
        return _save_tool_boundary(
            session=session,
            tool_name=name_to_write,
            ts=datetime.now(tz=UTC),
            declared_timeout_s=timeout_to_write,
        )
    except Exception as e:
        logger.debug("[liveness] record_tool_boundary failed (non-fatal): %s", e)
        return False


def record_turn_boundary(session_id: str | None = None) -> bool:
    """Bump ``last_turn_at`` for the in-flight AgentSession.

    Called from the SDK client's ``result`` event handler. Subject to the
    same cooldown and failure-handling guarantees as ``record_tool_boundary``.

    Args:
        session_id: The true ``AgentSession.session_id`` (NOT the Claude
            UUID, NOT the per-run id env value). When provided, resolves the
            AgentSession directly — this is the worker-process call path
            (``agent/sdk_client.py``'s ``result`` event handler), plumbed
            from the session runner (``agent/session_runner/runner.py``),
            where the in-flight session env vars are unset. This path is
            intentionally NOT routed through
            ``agent.hooks.session_resolver`` — that module reads env vars
            absent in the worker process. When ``None`` (default), falls
            back to ``agent.hooks.session_resolver`` — preserving the
            in-subprocess CLI-hook call sites unchanged.
    """
    if session_id is not None:
        # Worker-process path: explicit session_id, resolve directly.
        now = time.time()
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

    # In-subprocess path: resolve via the shared env-based resolver.
    from agent.hooks.session_resolver import inflight_cooldown_key, resolve_inflight_session

    base_key = inflight_cooldown_key()
    if not base_key:
        return False

    now = time.time()
    # Use a separate cooldown bucket per metric to avoid result-vs-tool
    # interference (a result event during a tight tool loop should still
    # bump last_turn_at promptly).
    bucket_key = f"{base_key}:turn"
    with _lock:
        prev = _last_write_at.get(bucket_key)
        if prev is not None and (now - prev) < COOLDOWN_WINDOW_SEC:
            return False
        _last_write_at[bucket_key] = now

    try:
        entry = resolve_inflight_session()
        if entry is None:
            return False
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
    from agent.hooks.session_resolver import inflight_cooldown_key, resolve_inflight_session

    base_key = inflight_cooldown_key()
    if not base_key or not text:
        return False

    now = time.time()
    bucket_key = f"{base_key}:thinking"
    with _lock:
        prev = _last_write_at.get(bucket_key)
        if prev is not None and (now - prev) < COOLDOWN_WINDOW_SEC:
            return False
        _last_write_at[bucket_key] = now

    excerpt = text[-280:]
    try:
        entry = resolve_inflight_session()
        if entry is None:
            return False
        entry.recent_thinking_excerpt = excerpt
        entry.save(update_fields=["recent_thinking_excerpt"])
        return True
    except Exception as e:
        logger.debug("[liveness] record_thinking_excerpt failed (non-fatal): %s", e)
        return False
