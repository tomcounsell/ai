"""Stop hook: logs session completion, enforces SDLC branch rules,
and implements the delivery review gate for Telegram-triggered sessions.

The review gate gives the agent final say over its output:
1. First stop → summarize raw output into a draft, present choices to agent
2. Agent picks SEND / EDIT / REACT / SILENT / CONTINUE
3. Second stop → parse choice, write delivery instruction to AgentSession
4. Bridge reads delivery instruction and executes accordingly
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

from claude_agent_sdk import HookContext, StopHookInput

logger = logging.getLogger(__name__)

# Module-level state tracking which sessions have already seen the review gate.
# Keyed by session_id → timestamp of first stop.  Cleared implicitly when the
# process restarts (bridge restart = fresh state).
_review_state: dict[str, float] = {}

# Patterns that suggest the agent stopped prematurely (promise without substance)
_FALSE_STOP_PATTERNS = re.compile(
    r"(?:^|\n)\s*(?:I (?:started|initiated|began|kicked off|triggered)|"
    r"Let me (?:check|look|investigate|find|search)|"
    r"I(?:'m| am) (?:going to|about to|checking|looking|searching|working on))"
    r".*$",
    re.IGNORECASE | re.MULTILINE,
)


def _is_telegram_triggered() -> bool:
    """Check if this session was triggered by a Telegram message."""
    return bool(os.environ.get("TELEGRAM_CHAT_ID") and os.environ.get("TELEGRAM_REPLY_TO"))


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


def _has_pm_messages(session_id: str) -> bool:
    """Check if the session (or its parent) already sent PM messages."""
    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            return False
        session = sessions[0]
        if hasattr(session, "has_pm_messages") and session.has_pm_messages():
            return True
        if hasattr(session, "get_parent_chat_session"):
            parent = session.get_parent_chat_session()
            if parent and hasattr(parent, "has_pm_messages") and parent.has_pm_messages():
                return True
    except Exception as e:
        logger.debug(f"[stop_hook] PM message check failed: {e}")
    return False


def _detect_false_stop(output_tail: str) -> bool:
    """Detect if the agent's output looks like a promise without substance.

    Returns True if the output contains promise-like patterns and is short
    (suggesting the agent stopped after announcing intent, not after doing work).
    """
    if len(output_tail.strip()) > 500:
        return False
    return bool(_FALSE_STOP_PATTERNS.search(output_tail))


async def _generate_draft(output_tail: str, session_id: str) -> str:
    """Run the summarizer on agent output to create a draft message."""
    try:
        from bridge.summarizer import summarize_response

        result = await summarize_response(output_tail)
        return result.text if hasattr(result, "text") else str(result)
    except Exception as e:
        logger.warning(f"[stop_hook] Summarizer failed for {session_id}: {e}")
        return output_tail[:500] if len(output_tail) > 500 else output_tail


def _build_review_prompt(draft: str, is_false_stop: bool) -> str:
    """Build the review gate prompt showing draft + delivery choices."""
    prompt = (
        "── DELIVERY REVIEW ──\n\n"
        f"Here is the draft message that would be sent to the user:\n\n"
        f'"{draft}"\n\n'
        "Choose how to deliver your response:\n"
        "  SEND — deliver the draft as-is\n"
        "  EDIT: <your revised message> — replace the draft with your text\n"
        "  REACT: <emoji> — send only an emoji reaction (e.g. REACT: 😁)\n"
        "  SILENT — send nothing (no text, no emoji)\n"
        "  CONTINUE — resume working (you stopped too early)\n"
    )
    if is_false_stop:
        prompt += (
            "\n⚠️  Your output looks like you announced intent but didn't finish "
            "the actual work. Consider choosing CONTINUE to keep going.\n"
        )
    prompt += "\nReply with your choice:"
    return prompt


def _parse_delivery_choice(output_tail: str) -> dict[str, str | None]:
    """Parse the agent's delivery choice from its response to the review gate.

    Returns a dict with delivery_action and optionally delivery_text/delivery_emoji.
    Falls back to SEND (deliver the draft) if unparseable.
    """
    text = output_tail.strip()

    # Try to find the choice in the last portion of output
    # The agent's response to the review gate is typically the last line(s)
    lines = text.split("\n")
    # Search from the end for a delivery choice
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue

        upper = line.upper()

        if upper == "SEND":
            return {"delivery_action": "send"}

        if upper.startswith("EDIT:"):
            revised = line[5:].strip()
            if revised:
                return {"delivery_action": "send", "delivery_text": revised}
            # EDIT with no text → treat as SEND
            return {"delivery_action": "send"}

        if upper.startswith("REACT:"):
            emoji = line[6:].strip()
            if emoji:
                return {"delivery_action": "react", "delivery_emoji": emoji}
            return {"delivery_action": "react", "delivery_emoji": "👍"}

        if upper == "SILENT":
            return {"delivery_action": "silent"}

        if upper == "CONTINUE":
            return {"delivery_action": "continue"}

    # Unparseable → conservative: deliver the draft
    logger.info("[stop_hook] Could not parse delivery choice, defaulting to SEND")
    return {"delivery_action": "send"}


def _write_delivery_to_session(session_id: str, choice: dict, draft: str) -> None:
    """Write delivery instruction fields to the AgentSession."""
    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            logger.warning(f"[stop_hook] Session {session_id} not found for delivery write")
            return

        session = sessions[0]
        action = choice.get("delivery_action", "send")

        if action == "send":
            session.delivery_action = "send"
            session.delivery_text = choice.get("delivery_text") or draft
        elif action == "react":
            session.delivery_action = "react"
            session.delivery_emoji = choice.get("delivery_emoji", "👍")
        elif action == "silent":
            session.delivery_action = "silent"
        # "continue" is handled by blocking again — no fields written

        session.save()
        logger.info(
            f"[stop_hook] Delivery instruction written: session={session_id}, action={action}"
        )
    except Exception as e:
        logger.warning(f"[stop_hook] Failed to write delivery instruction: {e}")


async def stop_hook(
    input_data: StopHookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> dict[str, Any]:
    """Log when a session completes. Hard-blocks code-on-main violations.
    Implements the delivery review gate for Telegram-triggered sessions.

    Review gate flow:
    1. First stop: generate draft from summarizer, present choices, block
    2. Agent responds with SEND/EDIT/REACT/SILENT/CONTINUE
    3. Second stop: parse choice, write to AgentSession, allow completion
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
    # Only fires for Telegram-triggered sessions where the agent hasn't
    # already self-messaged (PM bypass).
    if not _is_telegram_triggered():
        return {}

    if _has_pm_messages(session_id):
        logger.info(f"[stop_hook] Skipping review gate: PM already self-messaged ({session_id})")
        return {}

    # Check if this is the first or second stop for this session
    if session_id not in _review_state:
        # ── First stop: generate draft, present choices ──
        _review_state[session_id] = time.time()

        output_tail = _read_transcript_tail(input_data)
        if not output_tail.strip():
            logger.info(f"[stop_hook] Empty output, skipping review gate ({session_id})")
            _review_state.pop(session_id, None)
            return {}

        is_false_stop = _detect_false_stop(output_tail)
        draft = await _generate_draft(output_tail, session_id)
        review_prompt = _build_review_prompt(draft, is_false_stop)

        elapsed = time.time() - _review_state[session_id]
        logger.info(
            f"[stop_hook] Review gate activated: session={session_id}, "
            f"draft_len={len(draft)}, false_stop={is_false_stop}, "
            f"elapsed={elapsed:.1f}s"
        )

        return {
            "decision": "block",
            "reason": review_prompt,
        }
    else:
        # ── Second stop: parse delivery choice ──
        output_tail = _read_transcript_tail(input_data, max_chars=500)
        choice = _parse_delivery_choice(output_tail)

        if choice.get("delivery_action") == "continue":
            # Agent wants to keep working — block again but don't re-show the gate
            logger.info(f"[stop_hook] Agent chose CONTINUE ({session_id})")
            _review_state.pop(session_id, None)  # Reset so next stop triggers gate again
            return {
                "decision": "block",
                "reason": "Resuming work. Continue where you left off.",
            }

        # Write delivery instruction and allow completion
        draft = await _generate_draft(_read_transcript_tail(input_data, max_chars=2000), session_id)
        _write_delivery_to_session(session_id, choice, draft)

        elapsed = time.time() - _review_state.get(session_id, time.time())
        logger.info(
            f"[stop_hook] Review gate complete: session={session_id}, "
            f"choice={choice.get('delivery_action')}, elapsed={elapsed:.1f}s"
        )
        _review_state.pop(session_id, None)
        return {}
