"""Worker-internal idle-SDK-client sweeper (issue #1128).

Anthropic's Claude Agent SDK holds a persistent `ClaudeSDKClient` connection
across turns on the **SDK path** (`agent.sdk_client.get_response_via_sdk`).
Fleet-ops research (#1104) established that these connections die silently
after roughly 48 hours of idle, so a session that waits 2+ days for a
human reply may be non-functional when resumed. The harness path
(`get_response_via_harness`) is unaffected — it spawns a short-lived
`claude -p` subprocess per turn and has nothing to go stale.

Solution: proactively tear down persistent SDK clients on dormant sessions
before the 48h window. On the next query the SDK rebuilds a fresh client
and resumes conversation state from the stored `claude_session_uuid` via
`--resume`. No reconnect logic is needed.

**Process-locality contract**: the `_active_clients` registry lives in the
worker process. This sweeper runs INSIDE that process (started by
`worker/__main__.py`). The session-watchdog is a separate process and must
not touch `_active_clients`.

Status filter (explicit — issue #1128 C3): target
``{dormant, paused, paused_circuit}``. Exclude `running`, `pending`,
`waiting_for_children`, `superseded`, and all terminal statuses.

Tune via env:
    WATCHDOG_IDLE_TEARDOWN_ENABLED     — default on; set to 0/false/no to disable.
    WATCHDOG_IDLE_SWEEP_INTERVAL       — seconds between sweeps (default 1800 = 30 min).
    WATCHDOG_IDLE_TEARDOWN_THRESHOLD_SECONDS — dormancy age to trigger teardown (default 86400 = 24h).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# === Teardown configuration ===
# Dormancy age (seconds) required before we tear down an SDK client. Default
# 86400 (24h) sits comfortably inside the ~48h silent-death window with
# plenty of safety margin.
IDLE_TEARDOWN_THRESHOLD = int(
    os.environ.get("WATCHDOG_IDLE_TEARDOWN_THRESHOLD_SECONDS", "86400")
)
# Sweep interval (seconds). Default 1800 (30 min) — idle teardown does not
# need to be real-time; the only deadline is the 48h Anthropic kill.
IDLE_SWEEP_INTERVAL = int(os.environ.get("WATCHDOG_IDLE_SWEEP_INTERVAL", "1800"))

# Statuses for which a persistent SDK client is legitimately idle and safe
# to tear down. Must match the 13-state reference in
# `docs/features/session-lifecycle.md`.
TEARDOWN_STATUSES = frozenset({"dormant", "paused", "paused_circuit"})


def _env_flag_enabled(var_name: str, default: bool = True) -> bool:
    """Return True unless the env var is explicitly falsy.

    Accepts the same conventions as the other issue-#1128 gates:
    case-insensitive `"0"`, `"false"`, or `"no"` disable the feature.
    """
    raw = os.environ.get(var_name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no"}


def _to_timestamp(val) -> float | None:
    """Coerce datetime or numeric to a Unix timestamp. None passes through."""
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=UTC)
        return val.timestamp()
    if isinstance(val, (int, float)):
        return float(val)
    return None


async def _sweep_once() -> int:
    """Run one sweep pass over the active-client registry.

    Iterates a **snapshot** of `_active_clients` (never the live dict — a
    concurrent query might be registering or popping an entry). For each
    (session_id, client), load the matching AgentSession, filter on the
    allowed teardown statuses AND dormancy age, then tear down:

      1. `await client.close()` — idempotent; wrapped in try/except.
      2. `_active_clients.pop(session_id, None)` — safe on missing key.
      3. Set `AgentSession.sdk_connection_torn_down_at = now`.

    Returns the count of clients torn down this pass (useful for logging
    and tests).
    """
    if not _env_flag_enabled("WATCHDOG_IDLE_TEARDOWN_ENABLED"):
        logger.debug("[idle-sweeper] disabled via WATCHDOG_IDLE_TEARDOWN_ENABLED")
        return 0

    # Local import to avoid circular dependency (sdk_client.py is imported
    # at session time; keeping the registry hook out of module-init order
    # lets tests isolate the sweeper without spinning up the SDK path).
    try:
        from agent.sdk_client import _active_clients
    except Exception as e:
        logger.warning("[idle-sweeper] cannot import _active_clients registry: %s", e)
        return 0

    if not _active_clients:
        return 0

    # Snapshot — protects against concurrent mutation during iteration.
    snapshot = list(_active_clients.items())
    now_ts = datetime.now(UTC).timestamp()
    torn_down = 0

    try:
        from models.agent_session import AgentSession
    except Exception as e:
        logger.warning("[idle-sweeper] cannot import AgentSession: %s", e)
        return 0

    for session_id, client in snapshot:
        try:
            sessions = list(AgentSession.query.filter(session_id=session_id))
            if not sessions:
                logger.debug(
                    "[idle-sweeper] no AgentSession for session_id=%s; leaving client alone",
                    session_id,
                )
                continue
            sessions.sort(key=lambda s: s.created_at or 0, reverse=True)
            session = sessions[0]
            status_val = getattr(session, "status", None)
            if status_val not in TEARDOWN_STATUSES:
                continue

            # Use updated_at as the dormancy clock. Fall back to
            # started_at, then created_at — always in that order because
            # `updated_at` is the last-activity signal. If all three are
            # unset, skip: we have no way to know how long this client
            # has been idle.
            ref_ts = (
                _to_timestamp(session.updated_at)
                or _to_timestamp(getattr(session, "started_at", None))
                or _to_timestamp(getattr(session, "created_at", None))
            )
            if ref_ts is None:
                continue
            idle_seconds = now_ts - ref_ts
            if idle_seconds < IDLE_TEARDOWN_THRESHOLD:
                continue

            # Teardown. `client.close()` is idempotent; if a concurrent
            # query already closed it, the call is a no-op.
            try:
                if client is not None and hasattr(client, "close"):
                    close_result = client.close()
                    if asyncio.iscoroutine(close_result):
                        await close_result
            except Exception as close_err:
                logger.warning(
                    "[idle-sweeper] client.close() failed for %s: %s",
                    session_id,
                    close_err,
                )

            # Pop from registry (safe on already-removed key).
            _active_clients.pop(session_id, None)

            # Record teardown on the AgentSession record (best-effort).
            try:
                session.sdk_connection_torn_down_at = datetime.now(UTC)
                session.save(update_fields=["sdk_connection_torn_down_at"])
            except Exception as save_err:
                logger.warning(
                    "[idle-sweeper] save() failed for %s: %s",
                    session_id,
                    save_err,
                )

            torn_down += 1
            logger.info(
                "[idle-sweeper] Torn down SDK client for %s (status=%s, idle=%.0fh)",
                session_id,
                status_val,
                idle_seconds / 3600.0,
            )
        except Exception as e:
            logger.warning(
                "[idle-sweeper] Unexpected error for %s: %s",
                session_id,
                e,
            )

    if torn_down:
        logger.info("[idle-sweeper] sweep complete: %d client(s) torn down", torn_down)
    return torn_down


async def run_idle_sweep(interval: int | None = None) -> None:
    """Run the idle-sweeper loop indefinitely.

    Sleeps `interval` seconds between sweeps (default `IDLE_SWEEP_INTERVAL`).
    Any exception from `_sweep_once` is caught and logged so a transient
    Redis or SDK failure cannot kill the loop. Respond to cancellation
    cooperatively so `worker/__main__.py` can shut the task down cleanly.
    """
    sweep_interval = interval or IDLE_SWEEP_INTERVAL
    logger.info(
        "[idle-sweeper] started (interval=%ds, threshold=%ds)",
        sweep_interval,
        IDLE_TEARDOWN_THRESHOLD,
    )
    while True:
        try:
            await _sweep_once()
        except asyncio.CancelledError:
            logger.info("[idle-sweeper] cancelled, exiting loop")
            raise
        except Exception as e:
            logger.error("[idle-sweeper] sweep error: %s", e, exc_info=True)
        try:
            await asyncio.sleep(sweep_interval)
        except asyncio.CancelledError:
            logger.info("[idle-sweeper] cancelled during sleep, exiting loop")
            raise
