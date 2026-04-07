"""Output Router - Externalized session steering decision logic.

Extracted from agent_session_queue.py so that persona-specific routing
decisions live outside the generic executor. The executor's send_to_chat()
callback calls route_session_output() and executes the returned action —
the call site stays inside send_to_chat() to preserve temporal coupling
with chat_state flag-setting and post-execution cleanup.

Public API:
    determine_delivery_action()  — pure function, returns action string
    route_session_output()       — wraps determine_delivery_action with persona context
    MAX_NUDGE_COUNT              — safety cap constant
    NUDGE_MESSAGE                — default nudge text
    SendToChatResult             — state dataclass for send_to_chat
"""

from __future__ import annotations

import logging
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
) -> str:
    """Pure function: decide what send_to_chat should do with agent output.

    Returns one of:
        "deliver"                  — send to Telegram
        "deliver_fallback"         — send fallback message (empty output, cap reached)
        "nudge_rate_limited"       — backoff then nudge (rate limited)
        "nudge_empty"              — nudge (empty output)
        "nudge_continue"           — nudge (PM/SDLC session, continue pipeline)
        "drop"                     — drop output (completion already sent)
        "deliver_already_completed" — deliver without nudge (session already done)
    """
    from models.session_lifecycle import TERMINAL_STATUSES as _TERMINAL_STATUSES

    if session_status in _TERMINAL_STATUSES:
        return "deliver_already_completed"
    if completion_sent:
        return "drop"
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
    # PM sessions running SDLC work should continue through pipeline stages
    # rather than delivering after the first skill completes.
    # The PM decides when to stop; the bridge just keeps it working.
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
) -> tuple[str, int]:
    """Determine delivery action with persona-aware nudge cap.

    Wraps determine_delivery_action() to select the correct nudge cap based
    on session persona (Teammate uses a reduced cap).

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
    )
    return action, effective_cap
