"""Session ID registry for hook-side resolution of bridge session IDs.

Hooks fired by the Claude Agent SDK execute in the parent bridge process,
not inside the Claude Code subprocess. The VALOR_SESSION_ID env var is only
set on the subprocess, so hooks cannot read it. This module provides a
module-level registry that maps Claude Code UUIDs to bridge session IDs,
allowing hooks to resolve the correct session ID.

Registration flow:
1. SDKAgentClient.query() calls register_pending(bridge_session_id) before
   starting the SDK query.
2. The first hook callback calls complete_registration(claude_uuid) to
   promote the pending entry to a full mapping.
3. All subsequent hook calls use resolve(claude_uuid) to look up the
   bridge session ID.
4. SDKAgentClient.query() calls unregister(claude_uuid) in its finally
   block to clean up.

Thread safety: The bridge is single-threaded asyncio, so dict operations
on distinct keys are safe without locking.

See issue #597 and docs/plans/fix-hook-session-id-registry.md.
"""

from __future__ import annotations

import logging
import time
from collections import deque

logger = logging.getLogger(__name__)

# Sentinel key for the pending (pre-registration) entry.
_PENDING_KEY = "__pending__"

# TTL for stale entry cleanup (30 minutes).
_STALE_TTL_SECONDS = 30 * 60

# Maps Claude Code UUID -> bridge session ID
_registry: dict[str, str] = {}

# Maps Claude Code UUID -> registration timestamp (for TTL sweep)
_timestamps: dict[str, float] = {}

# Maps Claude Code UUID -> tool activity tracking
_activity: dict[str, dict] = {}


def register_pending(bridge_session_id: str) -> None:
    """Pre-register a bridge session ID before the Claude Code UUID is known.

    Called by SDKAgentClient.query() before client.query() starts.
    The first hook callback will promote this to a full mapping via
    complete_registration().

    Args:
        bridge_session_id: The bridge/Telegram session ID (e.g.,
            "tg_valor_-1003449100931_247").
    """
    if not bridge_session_id:
        logger.debug("[session_registry] register_pending called with empty session_id, ignoring")
        return

    _registry[_PENDING_KEY] = bridge_session_id
    _timestamps[_PENDING_KEY] = time.time()
    logger.debug("[session_registry] Pre-registered pending session: %s", bridge_session_id)


def complete_registration(claude_uuid: str) -> str | None:
    """Promote a pending registration to a full UUID-keyed mapping.

    Called by the first hook callback with the Claude Code UUID from
    input_data["session_id"]. If no pending entry exists (e.g., the
    UUID was already registered), this is a no-op.

    Args:
        claude_uuid: The Claude Code session UUID.

    Returns:
        The bridge session ID if promotion succeeded, None otherwise.
    """
    if not claude_uuid:
        logger.debug("[session_registry] complete_registration called with empty uuid, ignoring")
        return None

    # Already registered -- return the existing mapping
    if claude_uuid in _registry and claude_uuid != _PENDING_KEY:
        return _registry[claude_uuid]

    # Promote pending entry
    bridge_session_id = _registry.pop(_PENDING_KEY, None)
    if bridge_session_id is None:
        logger.debug("[session_registry] No pending entry to promote for uuid=%s", claude_uuid)
        return None

    _timestamps.pop(_PENDING_KEY, None)
    _registry[claude_uuid] = bridge_session_id
    _timestamps[claude_uuid] = time.time()
    _activity[claude_uuid] = {"tool_count": 0, "last_tools": deque(maxlen=3)}
    logger.debug(
        "[session_registry] Promoted pending -> uuid=%s, bridge_sid=%s",
        claude_uuid,
        bridge_session_id,
    )
    return bridge_session_id


def resolve(claude_uuid: str | None) -> str | None:
    """Look up the bridge session ID for a Claude Code UUID.

    If the UUID has a direct mapping, returns it. If not, checks for a
    pending entry and auto-promotes it. Returns None if no mapping exists.

    This is the primary lookup function used by all hook call sites.

    Args:
        claude_uuid: The Claude Code session UUID from input_data["session_id"].

    Returns:
        The bridge session ID, or None if not found.
    """
    if not claude_uuid:
        return None

    # Direct lookup
    if claude_uuid in _registry and claude_uuid != _PENDING_KEY:
        return _registry[claude_uuid]

    # Auto-promote pending entry on first resolve
    bridge_sid = complete_registration(claude_uuid)
    if bridge_sid:
        return bridge_sid

    return None


def record_tool_use(claude_uuid: str | None, tool_name: str) -> None:
    """Record a tool use for a session.

    Tracks the total tool count and the last 3 tool names for heartbeat
    enrichment and stuck-detection.

    Args:
        claude_uuid: The Claude Code session UUID.
        tool_name: Name of the tool that was used (e.g., "Bash", "Read").
    """
    if not claude_uuid:
        return

    if claude_uuid not in _activity:
        _activity[claude_uuid] = {"tool_count": 0, "last_tools": deque(maxlen=3)}

    _activity[claude_uuid]["tool_count"] += 1
    _activity[claude_uuid]["last_tools"].append(tool_name)


def get_activity(bridge_session_id: str | None) -> dict:
    """Get tool activity for a bridge session ID.

    Looks up the Claude Code UUID(s) associated with this bridge session
    and returns aggregated activity data.

    Args:
        bridge_session_id: The bridge/Telegram session ID.

    Returns:
        Dict with "tool_count" (int) and "last_tools" (list[str]),
        or empty dict if not found.
    """
    if not bridge_session_id:
        return {}

    # Reverse lookup: find the UUID for this bridge session ID
    for uuid, sid in _registry.items():
        if sid == bridge_session_id and uuid != _PENDING_KEY:
            activity = _activity.get(uuid)
            if activity:
                return {
                    "tool_count": activity["tool_count"],
                    "last_tools": list(activity["last_tools"]),
                }
    return {}


def unregister(claude_uuid: str | None) -> None:
    """Remove a session from the registry.

    Called by SDKAgentClient.query() in its finally block after the
    query completes or fails.

    Args:
        claude_uuid: The Claude Code session UUID to remove.
    """
    if not claude_uuid:
        return

    removed = _registry.pop(claude_uuid, None)
    _timestamps.pop(claude_uuid, None)
    _activity.pop(claude_uuid, None)
    if removed:
        logger.debug(
            "[session_registry] Unregistered uuid=%s (bridge_sid=%s)", claude_uuid, removed
        )


def cleanup_stale() -> int:
    """Remove registry entries older than _STALE_TTL_SECONDS.

    Safety net for entries that were not cleaned up due to uncaught
    exceptions. Each entry is ~200 bytes, so even leaked entries are
    negligible, but this prevents unbounded growth over long uptimes.

    Returns:
        Number of entries removed.
    """
    now = time.time()
    stale_keys = [key for key, ts in _timestamps.items() if (now - ts) > _STALE_TTL_SECONDS]
    for key in stale_keys:
        _registry.pop(key, None)
        _timestamps.pop(key, None)
        _activity.pop(key, None)

    if stale_keys:
        logger.info("[session_registry] Cleaned up %d stale entries", len(stale_keys))
    return len(stale_keys)


def _reset_for_testing() -> None:
    """Clear all registry state. Only for use in tests."""
    _registry.clear()
    _timestamps.clear()
    _activity.clear()
