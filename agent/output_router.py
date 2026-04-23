"""Output Router - Externalized session steering decision logic.

Extracted from agent_session_queue.py so that persona-specific routing
decisions live outside the generic executor. The executor's send_to_chat()
callback calls route_session_output() and executes the returned action —
the call site stays inside send_to_chat() to preserve temporal coupling
with chat_state flag-setting and post-execution cleanup.

PM final-delivery protocol (issue #1058):
    The router no longer inspects message content for any marker. The
    previous `[PIPELINE_COMPLETE]` protocol was removed because content-
    marker routing failed under context overflow, stale UUIDs, and persona
    drift. Final delivery is driven by `_handle_dev_session_completion`
    detecting pipeline completion and invoking `_deliver_pipeline_completion`
    — see `docs/features/pm-final-delivery.md`. PM+SDLC paths here resolve
    to `nudge_continue` (except for the `waiting_for_children` → `deliver`
    and terminal-status guards, which are preserved).

Public API:
    determine_delivery_action()  — pure function, returns action string
    route_session_output()       — wraps determine_delivery_action with persona context
    MAX_NUDGE_COUNT              — safety cap constant
    NUDGE_MESSAGE                — default nudge text
    SendToChatResult             — state dataclass for send_to_chat
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Safety cap — deliver to Telegram after this many nudges
MAX_NUDGE_COUNT = 50

# Default nudge message sent to the agent for all session types.
# The PM session owns SDLC intelligence; the bridge just keeps the agent working.
NUDGE_MESSAGE = "Keep working — only stop when you need human input or you're done."

# Post-compaction nudge guard window (seconds). When the executor has just
# observed a compaction on this session (via the PreCompact hook or the SDK-
# tick backstop), the router defers any nudge for this many seconds to let
# the SDK finish writing the compacted transcript and return cleanly to idle.
# Issue #1127 / plan: docs/plans/compaction-hardening.md.
POST_COMPACT_NUDGE_GUARD_SECONDS = 30


# ---------------------------------------------------------------------------
# State dataclass
# ---------------------------------------------------------------------------


@dataclass
class SendToChatResult:
    """Explicit state returned from send_to_chat instead of fragile nonlocal closures.

    Replaces the _defer_reaction and _completion_sent nonlocal variables that were
    set in send_to_chat() and read in the outer _execute_agent_session() scope. Multiple code
    paths previously set these via closure mutation; this dataclass makes the state
    explicit and eliminates inconsistency if an exception occurs between set and read.
    """

    completion_sent: bool = False
    defer_reaction: bool = False
    auto_continue_count: int = 0


# ---------------------------------------------------------------------------
# Core routing logic
# ---------------------------------------------------------------------------


def determine_delivery_action(
    msg: str,
    stop_reason: str | None,
    auto_continue_count: int,
    max_nudge_count: int,
    session_status: str | None = None,
    completion_sent: bool = False,
    watchdog_unhealthy: str | None = None,
    session_type: str | None = None,
    classification_type: str | None = None,
    last_compaction_ts: float | None = None,
) -> str:
    """Pure function: decide what send_to_chat should do with agent output.

    Returns one of:
        "deliver"                   — send to Telegram
        "deliver_fallback"          — send fallback message (empty output, cap reached)
        "nudge_rate_limited"        — backoff then nudge (rate limited)
        "nudge_empty"               — nudge (empty output)
        "nudge_continue"            — nudge (PM/SDLC session, continue pipeline)
        "defer_post_compact"        — skip this tick entirely; a compaction just
                                      completed and we want the SDK to finish
                                      before we nudge. The executor MUST NOT
                                      call `_enqueue_nudge` on this action and
                                      MUST NOT set `completion_sent=True` —
                                      the next SDK idle tick re-enters this
                                      function and either defers again or
                                      nudges normally.
        "drop"                      — drop output (completion already sent)
        "deliver_already_completed" — deliver without nudge (session already done)

    Args:
        last_compaction_ts: Unix timestamp of the most recent compaction for
            this session, read from ``AgentSession.last_compaction_ts``. When
            set and within ``POST_COMPACT_NUDGE_GUARD_SECONDS`` of ``now``,
            short-circuit to ``"defer_post_compact"``. Default ``None``
            preserves pre-1127 behavior (no guard).

    Note (issue #1058): no content-string inspection happens here. Pipeline
    completion is detected separately in `_handle_dev_session_completion` via
    the `is_pipeline_complete` predicate, which invokes a dedicated
    completion-turn runner that delivers the final message directly.
    """
    from models.session_lifecycle import TERMINAL_STATUSES as _TERMINAL_STATUSES

    if session_status in _TERMINAL_STATUSES:
        return "deliver_already_completed"
    if completion_sent:
        return "drop"
    # Post-compaction nudge guard (issue #1127). Placed AFTER terminal and
    # completion-sent guards so a just-terminated session still exits cleanly
    # — but BEFORE all other classification, because deferring is strictly
    # less disruptive than any other action and the other branches can be
    # re-evaluated on the next idle tick.
    if last_compaction_ts is not None:
        try:
            age = time.time() - float(last_compaction_ts)
        except (TypeError, ValueError):
            age = None
        if age is not None and age < POST_COMPACT_NUDGE_GUARD_SECONDS:
            return "defer_post_compact"
    # Watchdog flagged this session as stuck — deliver instead of nudging
    if watchdog_unhealthy:
        return "deliver" if msg and msg.strip() else "deliver_fallback"
    if stop_reason == "rate_limited":
        return "nudge_rate_limited"
    if not msg or not msg.strip():
        if auto_continue_count + 1 <= max_nudge_count:
            return "nudge_empty"
        return "deliver_fallback"
    if auto_continue_count >= max_nudge_count:
        return "deliver"
    # PM in waiting_for_children must deliver (not nudge) so the session exits
    # cleanly and releases its global semaphore slot.  The child dev session
    # can then acquire the slot.  Issue #1004.
    if session_status == "waiting_for_children":
        return "deliver"
    # PM sessions running SDLC work continue through pipeline stages via
    # nudge. Final delivery is handled out-of-band by the completion-turn
    # runner — see `_deliver_pipeline_completion` in `agent/session_completion.py`.
    if session_type == "pm" and classification_type == "sdlc":
        return "nudge_continue"
    if stop_reason in ("end_turn", None) and len(msg.strip()) > 0:
        return "deliver"
    return "deliver"


def route_session_output(
    msg: str,
    stop_reason: str | None,
    auto_continue_count: int,
    session_status: str | None = None,
    completion_sent: bool = False,
    watchdog_unhealthy: str | None = None,
    session_type: str | None = None,
    classification_type: str | None = None,
    is_teammate: bool = False,
    last_compaction_ts: float | None = None,
) -> tuple[str, int]:
    """Determine delivery action with persona-aware nudge cap.

    Wraps determine_delivery_action() to select the correct nudge cap based
    on session persona (Teammate uses a reduced cap).

    Args:
        last_compaction_ts: Forwarded to ``determine_delivery_action``. The
            caller is responsible for reading ``AgentSession.last_compaction_ts``
            from Redis and passing the value through; this wrapper does not
            read the AgentSession itself. See issue #1127.

    Returns:
        (action, effective_nudge_cap) — the action string and the nudge cap used
    """
    if is_teammate:
        from agent.teammate_handler import TEAMMATE_MAX_NUDGE_COUNT

        effective_cap = TEAMMATE_MAX_NUDGE_COUNT
    else:
        effective_cap = MAX_NUDGE_COUNT

    action = determine_delivery_action(
        msg=msg,
        stop_reason=stop_reason,
        auto_continue_count=auto_continue_count,
        max_nudge_count=effective_cap,
        session_status=session_status,
        completion_sent=completion_sent,
        watchdog_unhealthy=watchdog_unhealthy,
        session_type=session_type,
        classification_type=classification_type,
        last_compaction_ts=last_compaction_ts,
    )
    return action, effective_cap
