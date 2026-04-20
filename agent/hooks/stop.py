"""Stop hook: logs session completion, enforces SDLC branch rules, and runs
the medium-aware delivery review gate.

Review gate flow (plan #1035 Part E + F):
1. First stop -> draft raw output via bridge.message_drafter.draft_message,
   present a prepopulated ``send_message`` tool-call presentation, block.
2. Agent invokes ``python tools/send_message.py 'text'`` (as-is or edited)
   OR ``python tools/react_with_emoji.py 'feeling'`` OR stops silent OR
   continues working.
3. Second stop -> inspect the transcript tail for tool_use blocks; classify
   the outcome (send / edit+send / react / silent / continue) and allow
   completion. No string-menu parsing, no delivery_text plumbing.

Medium is resolved from ``session.extra_context.transport`` (default
``"telegram"``), so email sessions run the same gate with email format
rules applied by the drafter.

Child sessions (``session.parent_agent_session_id`` set) skip the gate:
their output routes through the parent, which owns delivery.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

from claude_agent_sdk import HookContext, StopHookInput

logger = logging.getLogger(__name__)

# Module-level state tracking which sessions have seen the review gate.
# Keyed by session_id -> {"timestamp": float, "draft": str, "medium": str}.
# Cleared implicitly when the process restarts. Stale entries evicted
# on access if older than _REVIEW_STATE_TTL seconds.
_review_state: dict[str, dict[str, Any]] = {}
_REVIEW_STATE_TTL = 3600  # 1 hour — sessions should complete well within this

# Patterns suggesting the agent stopped prematurely (promise without substance)
_FALSE_STOP_PATTERNS = re.compile(
    r"(?:^|\n)\s*(?:I (?:started|initiated|began|kicked off|triggered)|"
    r"Let me (?:check|look|investigate|find|search)|"
    r"I(?:'m| am) (?:going to|about to|checking|looking|searching|working on))"
    r".*$",
    re.IGNORECASE | re.MULTILINE,
)

# Tool-invocation patterns used to classify the agent's delivery choice.
# Matches both forms the agent might invoke (bash-style):
#   python tools/send_message.py "text..."
#   python tools/react_with_emoji.py "feeling"
# We scan transcript text (JSONL tool_use blocks are embedded as strings)
# and accept either bare argv or quoted forms.
_SEND_MESSAGE_PATTERN = re.compile(r"tools/send_message\.py\b")
_REACT_PATTERN = re.compile(r"tools/react_with_emoji\.py\b")
# Legacy support: the old send_telegram.py tool remains a valid send path
# for PM self-messaging (pm_bypass) and for the transition window.
_LEGACY_SEND_TELEGRAM_PATTERN = re.compile(r"tools/send_telegram\.py\b")


def _is_user_triggered() -> bool:
    """Return True if this session has a user-visible transport configured.

    A session is user-triggered when it has either Telegram chat context
    OR an email reply-to address. For sessions without any transport
    (local/background work), the review gate is skipped entirely.
    """
    return bool(
        os.environ.get("TELEGRAM_CHAT_ID")
        or os.environ.get("EMAIL_REPLY_TO")
        or os.environ.get("VALOR_TRANSPORT")
    )


def _resolve_medium(session_id: str) -> str:
    """Resolve the delivery medium from session.extra_context.transport.

    Default = "telegram" (historical default). Returns the transport string
    which is threaded into ``draft_message(medium=...)``.
    """
    # Env override wins
    env_override = os.environ.get("VALOR_TRANSPORT")
    if env_override:
        return env_override.strip().lower()

    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if sessions:
            extra = getattr(sessions[0], "extra_context", None) or {}
            transport = extra.get("transport")
            if transport:
                return str(transport).strip().lower()
    except Exception as e:
        logger.debug(f"[stop_hook] medium resolution failed: {e}")

    # Heuristic fallback based on env vars
    if os.environ.get("EMAIL_REPLY_TO"):
        return "email"
    return "telegram"


def _is_child_session(session_id: str) -> bool:
    """Return True if the session has a parent (child sessions skip the gate)."""
    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            return False
        return bool(getattr(sessions[0], "parent_agent_session_id", None))
    except Exception as e:
        logger.debug(f"[stop_hook] parent check failed: {e}")
        return False


def _read_transcript_tail(input_data: dict, max_chars: int = 2000) -> str:
    """Read the last N chars from the session transcript."""
    transcript_path = input_data.get("transcript_path", "")
    if not transcript_path:
        return ""
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            read_size = min(file_size, max_chars * 2)
            f.seek(max(0, file_size - read_size))
            raw = f.read()
            return raw.decode("utf-8", errors="replace")[-max_chars:]
    except OSError as e:
        logger.debug(f"[stop_hook] Could not read transcript tail: {e}")
        return ""


def _detect_false_stop(output_tail: str) -> bool:
    """Detect if the agent's output looks like a promise without substance."""
    if len(output_tail.strip()) > 500:
        return False
    return bool(_FALSE_STOP_PATTERNS.search(output_tail))


async def _generate_draft(output_tail: str, session_id: str, medium: str) -> str:
    """Run the drafter on agent output to create a draft message."""
    try:
        from bridge.message_drafter import draft_message, format_violations

        result = await draft_message(output_tail, medium=medium)
        text = result.text if hasattr(result, "text") else str(result)
        # Surface validator violations in the draft presentation (plan §Part B).
        if getattr(result, "violations", None):
            warning = format_violations(result.violations, medium)
            if warning:
                text = f"{text}\n\n{warning}"
        return text
    except Exception as e:
        logger.warning(f"[stop_hook] Drafter failed for {session_id}: {e}")
        return output_tail[:500] if len(output_tail) > 500 else output_tail


def _build_review_prompt(draft: str, medium: str, is_false_stop: bool) -> str:
    """Build the review gate prompt using tool-call delivery contract.

    Replaces the SEND/EDIT/REACT/SILENT/CONTINUE string menu (plan §Part E).
    The agent invokes a real tool to deliver; we detect the invocation on
    the next stop via transcript inspection (plan §D4 implicit clearing).
    """
    lines = [
        "── DELIVERY REVIEW ──",
        "",
        f"Draft message (medium={medium}):",
        f'"""{draft}"""',
        "",
        "To deliver, invoke ONE of these tools before stopping:",
        "",
        "  • Send the draft as-is:",
        f"    python tools/send_message.py {_shell_quote(draft)}",
        "",
        "  • Edit then send (replace the text arg with your revision):",
        "    python tools/send_message.py 'your revised text here'",
        "",
        "  • React-only (Telegram):",
        "    python tools/react_with_emoji.py 'excited'",
        "",
        "  • Silent: stop without invoking either tool.",
        "",
        "  • Continue: keep working and we will re-enter the gate at the next stop.",
    ]
    if is_false_stop:
        lines.extend(
            [
                "",
                "⚠️  Your output looks like intent without substance. Consider continuing "
                "instead of sending.",
            ]
        )
    return "\n".join(lines)


def _shell_quote(text: str) -> str:
    """Safely quote text for the shell example in the prompt.

    This is presentation-only (copy-paste ergonomics); the agent is expected
    to construct its own argv, not run the exact string verbatim.
    """
    if not text:
        return "''"
    safe = text.replace("'", "'\\''")
    # Truncate long drafts in the example — the agent has the full draft above.
    if len(safe) > 240:
        safe = safe[:240] + "…"
    return f"'{safe}'"


def classify_delivery_outcome(transcript_tail: str) -> str:
    """Inspect the transcript tail for tool invocations and classify outcome.

    Returns one of: "send", "react", "continue", "silent".

    - "send": transcript shows a send_message or (legacy) send_telegram tool
      invocation after the review gate prompt.
    - "react": transcript shows a react_with_emoji tool invocation.
    - "continue": agent did not invoke any delivery tool but produced other
      tool_use activity (keeps working).
    - "silent": agent stopped without any delivery tool and without other
      work signals.

    The caller maps these outcomes to stop-hook actions (clear gate, block
    with continue prompt, etc.). See plan §D4 for the classification table.
    """
    if not transcript_tail:
        return "silent"
    if _SEND_MESSAGE_PATTERN.search(transcript_tail) or _LEGACY_SEND_TELEGRAM_PATTERN.search(
        transcript_tail
    ):
        return "send"
    if _REACT_PATTERN.search(transcript_tail):
        return "react"
    # Look for other tool_use blocks as a weak signal the agent is still working.
    # "tool_use" is the schema key used by the Claude Agent SDK for tool invocations.
    if "tool_use" in transcript_tail or '"type": "tool_use"' in transcript_tail:
        return "continue"
    return "silent"


async def stop_hook(
    input_data: StopHookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> dict[str, Any]:
    """Log when a session completes. Hard-blocks code-on-main violations.

    Runs the medium-aware delivery review gate for user-triggered sessions:
    1. First stop: generate a draft via the drafter (with per-medium rules),
       present as a prepopulated tool-call presentation, block.
    2. Second stop: inspect transcript for send_message/react_with_emoji
       tool invocations; classify outcome; allow completion.
    """
    session_id = input_data.get("session_id", "unknown")
    transcript_path = input_data.get("transcript_path", "")

    logger.info(f"[stop_hook] Session stop: session_id={session_id}, transcript={transcript_path}")

    # SDLC enforcement: hard-block code pushed directly to main
    try:
        from agent.sdk_client import _check_no_direct_main_push

        violation = _check_no_direct_main_push(session_id)
        if violation:
            logger.error(
                f"[stop_hook] SDLC violation detected for session {session_id}: "
                "code modified on main branch"
            )
            return {
                "decision": "block",
                "reason": violation,
            }
    except Exception as e:
        logger.warning(
            f"[stop_hook] SDLC branch check failed for {session_id}: {e} — "
            "failing open, session allowed to complete"
        )

    # ── Delivery review gate ──
    if not _is_user_triggered():
        return {}

    # Child sessions skip the gate — their output routes via the parent.
    if _is_child_session(session_id):
        logger.info(
            f"[stop_hook] Skipping review gate for child session {session_id} "
            "(parent owns delivery)"
        )
        return {}

    # Evict stale entries to prevent unbounded growth in long-running processes
    now = time.time()
    stale = [k for k, v in _review_state.items() if now - v.get("timestamp", 0) > _REVIEW_STATE_TTL]
    for k in stale:
        _review_state.pop(k, None)

    if session_id not in _review_state:
        # ── First stop: generate draft, present prepopulated tool call ──
        start_time = time.time()

        output_tail = _read_transcript_tail(input_data)
        if not output_tail.strip():
            logger.info(f"[stop_hook] Empty output, skipping review gate ({session_id})")
            return {}

        medium = _resolve_medium(session_id)
        is_false_stop = _detect_false_stop(output_tail)
        draft = await _generate_draft(output_tail, session_id, medium)
        review_prompt = _build_review_prompt(draft, medium, is_false_stop)

        _review_state[session_id] = {
            "timestamp": start_time,
            "draft": draft,
            "medium": medium,
        }

        elapsed = time.time() - start_time
        logger.info(
            f"[stop_hook] Review gate activated: session={session_id}, medium={medium}, "
            f"draft_len={len(draft)}, false_stop={is_false_stop}, elapsed={elapsed:.1f}s"
        )

        return {
            "decision": "block",
            "reason": review_prompt,
        }

    # ── Second stop: classify outcome from tool-call history ──
    output_tail = _read_transcript_tail(input_data, max_chars=4000)
    outcome = classify_delivery_outcome(output_tail)

    cached = _review_state.get(session_id, {})
    start_time = cached.get("timestamp", time.time())
    elapsed = time.time() - start_time

    if outcome == "continue":
        logger.info(f"[stop_hook] Agent continued working ({session_id})")
        _review_state.pop(session_id, None)  # Reset so next stop re-enters gate
        return {
            "decision": "block",
            "reason": "Resuming work. Continue where you left off.",
        }

    logger.info(
        f"[stop_hook] Review gate complete: session={session_id}, "
        f"outcome={outcome}, elapsed={elapsed:.1f}s"
    )
    _review_state.pop(session_id, None)
    return {}
