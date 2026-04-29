"""Read-the-Room (RTR) pre-send pass for the Telegram drafter.

A lightweight Haiku call inspects the recent chat snapshot + the drafted
message and returns one of ``{send, trim, suppress}`` so the agent does not
post redundant or stale messages into shared chats.

Design notes
------------
* **Snapshot freshness window (Race 2):** The snapshot is a point-in-time read
  from ``TelegramMessage`` that may be stale by ~1s. We accept the race --
  the post-RTR outbox write is async with the relay's send anyway, so even a
  "perfect" snapshot would race the actual send. Locking is intentionally
  avoided.
* **Snapshot includes the agent's own prior turns (Risk 3):** Path A messages
  flow into ``TelegramMessage`` via two recording sites and end up under two
  different ``sender`` values: the bridge handler writes ``sender="Valor"``
  (``direction="out"``) and the relay's PM-direct path writes
  ``sender="system"`` (``direction="in"``). The snapshot is passed through
  *unfiltered* -- the system prompt below tells the model to treat any entry
  with ``sender`` in ``{valor, system}`` as agent-authored context, not as a
  competing input.
* **Fail-open contract:** Any error path -- LLM timeout, malformed tool_use,
  Redis error, snapshot fetch failure -- returns ``RoomVerdict(action="send",
  reason="rtr_error")`` and emits a ``rtr.failed`` ``session_event``. RTR is
  a guard, not a blocker.
* **Hotfix #1055 invariant:** The Anthropic call uses
  ``semaphore_slot()`` + ``async with anthropic.AsyncAnthropic(timeout=3.0)``
  for httpx-level cleanup on cancellation. ``asyncio.wait_for`` is forbidden
  here -- it leaks httpx connections under cancellation.

Public surface
--------------
* ``RoomVerdict`` -- the verdict dataclass returned to the call site.
* ``read_the_room(draft_text, chat_id, session) -> RoomVerdict`` -- the
  async entry point.
* ``READ_THE_ROOM_ENABLED`` -- module-level env-var gate (default ``False``).
* ``RTR_SUPPRESS_EMOJI`` -- the reaction emoji emitted on suppress.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import anthropic

from agent.anthropic_client import semaphore_slot
from bridge.message_drafter import SHORT_OUTPUT_THRESHOLD
from config.models import MODEL_FAST
from utils.api_keys import get_anthropic_api_key

logger = logging.getLogger(__name__)


# === Module-level constants ===

# First-person reactor voice (👀 = "I'm watching the room"). 🫡 ("received and
# standing down") is a documented alternative for personal-moment scenarios;
# operators tune via this single line.
RTR_SUPPRESS_EMOJI = "👀"

# Snapshot defaults. Tuneable via env vars when real-traffic data warrants.
DEFAULT_K = 10
DEFAULT_MAX_AGE_SECONDS = 300  # 5 minutes

# SDK-level timeout passed to anthropic.AsyncAnthropic(timeout=...). The
# httpx layer cancels and cleans up the connection on timeout.
RTR_SDK_TIMEOUT = 3.0

# Below this length the trim verdict is coerced to suppress (a single emoji
# landing in a personal exchange is the exact failure mode this feature
# exists to prevent -- see Implementation Note F4).
TRIM_TOO_SHORT_THRESHOLD = 20

# Trim duration for "draft preview" snippets stored in session_events.
PREVIEW_LENGTH = 200


def _read_enabled() -> bool:
    """Read ``READ_THE_ROOM_ENABLED`` env var fresh on each call.

    Tests set this var via ``monkeypatch.setenv`` per-test; reading at
    call time keeps the toggle live without a process restart.
    """
    return os.environ.get("READ_THE_ROOM_ENABLED", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


# === Verdict dataclass ===


@dataclass
class RoomVerdict:
    """Outcome of a read-the-room pass.

    Attributes:
        action: One of ``"send"``, ``"trim"``, or ``"suppress"``.
        revised_text: Replacement text when ``action == "trim"``.
            ``None`` for ``send`` and ``suppress``.
        reason: Short machine-readable reason string. Used in
            ``session_events`` entries for observability.
    """

    action: str  # "send" | "trim" | "suppress"
    revised_text: str | None = None
    reason: str = ""


# === System prompt ===

READ_THE_ROOM_SYSTEM_PROMPT = """\
You are a pre-send guard for an AI assistant that posts messages into a shared \
Telegram chat. The assistant has just produced a draft. Your job is to decide \
whether the draft should be sent as-is, trimmed to a shorter form, or suppressed.

You will receive two inputs:

1. A snapshot of the last few messages in the chat (newest last). Each entry has \
a `sender` and a `content` field.
2. The draft the assistant is about to send.

Important attribution rules:
- Snapshot entries with `sender` in {Valor, valor, system} ARE this assistant's \
prior turns. Treat them as context for "what has already been said", not as \
competing input from another participant.
- Snapshot entries from any other sender are other participants (humans or other \
bots).

Decide one of three actions:

- "send" — the draft lands cleanly: it adds new information, answers an open \
question, or is a substantive reply that has not already been delivered. Default \
to send when uncertain.
- "trim" — the draft is partially redundant or partially stale; provide a \
shorter `revised_text` (e.g. a brief acknowledgement or a one-sentence pointer). \
Use trim sparingly — short messages must still carry signal.
- "suppress" — the draft is fully redundant (another participant just answered \
the same question), or the conversation has clearly moved on, or the draft is \
substantially identical to the assistant's own prior turn within the last 60 \
seconds (self-duplicate). Use `reason="self_duplicate"` for the latter case.

Conservative bias: when in doubt, return `send`. False suppression is harder to \
detect than false trim because the human gets no signal back.

You MUST call the `room_verdict` tool with a flat structured result \
(`action`, `revised_text`, `reason`)."""


# === Snapshot fetching ===


def _format_snapshot_for_prompt(messages: list[dict[str, Any]]) -> str:
    """Render the snapshot as a compact text block for the user message."""
    if not messages:
        return "(no recent messages)"
    lines: list[str] = []
    for m in messages:
        sender = m.get("sender") or "?"
        content = (m.get("content") or "").strip().replace("\n", " ")
        # Cap each line so a single noisy message can't blow the context.
        if len(content) > 400:
            content = content[:400] + "..."
        lines.append(f"- {sender}: {content}")
    return "\n".join(lines)


def _fetch_snapshot_sync(
    chat_id: str | int,
    *,
    k: int,
    max_age_seconds: int,
) -> list[dict[str, Any]]:
    """Synchronously query ``TelegramMessage`` for the last K messages within
    the time window. Caller wraps in ``asyncio.to_thread`` so the event loop
    stays unblocked.

    Returns a list of dicts with ``sender``, ``content``, ``timestamp`` (and
    ``direction``, ``message_id`` for diagnostic use). Sorted newest-LAST so
    the formatter can render chronologically.

    Returns ``[]`` on any error -- callers treat empty snapshot as "clean
    room" and short-circuit to ``send``.
    """
    try:
        from models.telegram import TelegramMessage

        messages = list(TelegramMessage.query.filter(chat_id=str(chat_id)))
        # Sort by raw float timestamp DESC so we can slice the newest K.
        messages.sort(key=lambda m: float(m.timestamp or 0), reverse=True)
        cutoff = time.time() - max_age_seconds
        windowed: list[dict[str, Any]] = []
        for m in messages[: k * 4]:  # pre-slice so post-filter has room to drop stale
            ts = float(m.timestamp or 0)
            if ts < cutoff:
                continue
            windowed.append(
                {
                    "sender": getattr(m, "sender", None) or "?",
                    "content": getattr(m, "content", None) or "",
                    "direction": getattr(m, "direction", None),
                    "timestamp": ts,
                    "message_id": getattr(m, "message_id", None),
                }
            )
            if len(windowed) >= k:
                break
        # Render chronologically (oldest first, newest last) for the prompt.
        windowed.reverse()
        return windowed
    except Exception as e:
        logger.warning(
            "RTR snapshot fetch failed for chat_id=%s (%s); treating as empty",
            chat_id,
            e,
        )
        return []


async def _fetch_snapshot(
    chat_id: str | int,
    *,
    k: int,
    max_age_seconds: int,
) -> list[dict[str, Any]]:
    """Async wrapper around the synchronous Popoto snapshot query."""
    return await asyncio.to_thread(
        _fetch_snapshot_sync, chat_id, k=k, max_age_seconds=max_age_seconds
    )


# === session_events helpers ===


def _make_event(
    event_type: str,
    *,
    chat_id: str | int | None,
    draft_text: str | None,
    revised_text: str | None = None,
    reason: str = "",
    error: str | None = None,
) -> dict[str, Any]:
    """Construct a session_events dict with the documented schema."""
    event: dict[str, Any] = {
        "type": event_type,
        "ts": time.time(),
        "chat_id": str(chat_id) if chat_id is not None else None,
        "reason": reason,
    }
    if draft_text is not None:
        event["draft_preview"] = draft_text[:PREVIEW_LENGTH]
    if revised_text is not None:
        event["revised_preview"] = revised_text[:PREVIEW_LENGTH]
    if error is not None:
        event["error"] = error
    return event


def _append_event(session: Any, event: dict[str, Any]) -> None:
    """Best-effort append to ``session.session_events``.

    Matches the codebase's existing append posture: read-modify-write with no
    lock. Race 3 (concurrent appends from two RTR calls in the same session)
    is documented and accepted -- the surrounding event log is best-effort.
    """
    if session is None:
        return
    try:
        events = list(getattr(session, "session_events", None) or [])
        events.append(event)
        session.session_events = events
        if hasattr(session, "save"):
            session.save()
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("RTR session_events append failed (non-fatal): %s", e)


# === Tool schema ===

_ROOM_VERDICT_TOOL = {
    "name": "room_verdict",
    "description": (
        "Return the read-the-room verdict for the candidate draft. "
        "Action must be one of send|trim|suppress."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["send", "trim", "suppress"],
                "description": "Verdict action.",
            },
            "revised_text": {
                "type": ["string", "null"],
                "description": ("Replacement text when action='trim'. Null for send/suppress."),
            },
            "reason": {
                "type": "string",
                "description": "Short machine-readable reason string.",
            },
        },
        "required": ["action", "reason"],
    },
}


def _parse_verdict_block(message: Any) -> RoomVerdict:
    """Extract the verdict from a Haiku message response.

    Raises ``ValueError`` if the response does not contain a usable
    ``room_verdict`` tool_use block. Caller wraps in try/except for fail-open.
    """
    content = getattr(message, "content", None) or []
    for block in content:
        block_type = getattr(block, "type", None)
        if block_type == "tool_use" and getattr(block, "name", None) == "room_verdict":
            payload = getattr(block, "input", None) or {}
            action = payload.get("action")
            if action not in ("send", "trim", "suppress"):
                raise ValueError(f"unexpected RTR action: {action!r}")
            revised = payload.get("revised_text")
            reason = payload.get("reason") or ""
            return RoomVerdict(
                action=action,
                revised_text=revised if isinstance(revised, str) and revised else None,
                reason=str(reason),
            )
    raise ValueError("no room_verdict tool_use block in response")


# === Public entry point ===


async def read_the_room(
    draft_text: str,
    chat_id: str | int | None,
    session: Any = None,
    *,
    k: int = DEFAULT_K,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> RoomVerdict:
    """Pre-send guard pass returning a ``RoomVerdict``.

    Fail-open contract: every error path returns
    ``RoomVerdict(action="send", reason="rtr_error")`` and emits a
    ``rtr.failed`` ``session_event``. The caller continues with the original
    draft text on any non-send / non-trim verdict it cannot handle.

    Short-circuits (return ``send`` without calling Haiku, no event emitted):
        * ``READ_THE_ROOM_ENABLED`` env var is falsey.
        * ``draft_text`` is empty / whitespace-only.
        * ``chat_id`` is ``None`` (no room to read).
        * ``len(draft_text) < SHORT_OUTPUT_THRESHOLD`` -- aligns with the
          drafter's own bypass band so we don't pay RTR latency for short
          messages the drafter already skipped.
        * ``session.sdlc_slug`` is set -- emits a ``rtr.bypassed`` event.
        * Snapshot is empty -- nothing to compare against.

    Args:
        draft_text: The string that will hit the outbox after upstream
            transformations. Pass ``delivery_text`` from
            ``TelegramRelayOutputHandler.send``, NOT the raw input or
            ``draft.text``.
        chat_id: Target Telegram chat identifier.
        session: Optional ``AgentSession`` for observability. RTR appends
            ``rtr.*`` entries to ``session.session_events`` for trim,
            suppress, suppress_fallthrough, bypass, and failure cases.
        k: Snapshot K cap (default 10).
        max_age_seconds: Snapshot time window in seconds (default 300).

    Returns:
        ``RoomVerdict``. ``send`` and ``trim`` are the verdicts the call site
        acts on; ``suppress`` indicates "do not write the original text" and
        the call site decides between emitting a 👀 reaction (when
        ``reply_to_msg_id`` is set) or falling through to send the original
        text (when no anchor is available).
    """
    # ── Short-circuits ──
    if not _read_enabled():
        return RoomVerdict(action="send", reason="rtr_disabled")

    if not draft_text or not draft_text.strip():
        return RoomVerdict(action="send", reason="empty_draft")

    if chat_id is None:
        return RoomVerdict(action="send", reason="no_chat_id")

    if len(draft_text) < SHORT_OUTPUT_THRESHOLD:
        return RoomVerdict(action="send", reason="short_output")

    if bool(session and getattr(session, "sdlc_slug", None)):
        _append_event(
            session,
            _make_event(
                "rtr.bypassed",
                chat_id=chat_id,
                draft_text=draft_text,
                reason="sdlc_session",
            ),
        )
        return RoomVerdict(action="send", reason="sdlc_session")

    # ── Snapshot ──
    snapshot = await _fetch_snapshot(chat_id, k=k, max_age_seconds=max_age_seconds)
    if not snapshot:
        return RoomVerdict(action="send", reason="empty_snapshot")

    # ── Haiku call (post-#1055 pattern: semaphore_slot + inner async with
    #    AsyncAnthropic(timeout=3.0); NO asyncio.wait_for). ──
    user_payload = (
        "## Recent chat snapshot (oldest first, newest last)\n"
        f"{_format_snapshot_for_prompt(snapshot)}\n\n"
        "## Draft about to be sent\n"
        f"{draft_text}\n\n"
        "Decide via the room_verdict tool."
    )

    try:
        async with semaphore_slot():
            async with anthropic.AsyncAnthropic(
                api_key=get_anthropic_api_key(),
                timeout=RTR_SDK_TIMEOUT,
            ) as client:
                message = await client.messages.create(
                    model=MODEL_FAST,
                    max_tokens=400,
                    system=READ_THE_ROOM_SYSTEM_PROMPT,
                    tools=[_ROOM_VERDICT_TOOL],
                    tool_choice={"type": "tool", "name": "room_verdict"},
                    messages=[{"role": "user", "content": user_payload}],
                )

        verdict = _parse_verdict_block(message)
        # If the model returns trim with no revised_text, treat as send (no
        # rewrite available -> nothing to substitute).
        if verdict.action == "trim" and not verdict.revised_text:
            return RoomVerdict(
                action="send",
                reason="trim_missing_revised_text",
            )
        return verdict

    except anthropic.APITimeoutError as e:
        logger.warning("RTR Haiku call timed out: %s", e)
        _append_event(
            session,
            _make_event(
                "rtr.failed",
                chat_id=chat_id,
                draft_text=draft_text,
                reason="rtr_error",
                error="APITimeoutError",
            ),
        )
        return RoomVerdict(action="send", reason="rtr_error")
    except anthropic.APIConnectionError as e:
        logger.warning("RTR Haiku call connection error: %s", e)
        _append_event(
            session,
            _make_event(
                "rtr.failed",
                chat_id=chat_id,
                draft_text=draft_text,
                reason="rtr_error",
                error="APIConnectionError",
            ),
        )
        return RoomVerdict(action="send", reason="rtr_error")
    except anthropic.APIError as e:
        logger.warning("RTR Haiku API error: %s", e)
        _append_event(
            session,
            _make_event(
                "rtr.failed",
                chat_id=chat_id,
                draft_text=draft_text,
                reason="rtr_error",
                error="APIError",
            ),
        )
        return RoomVerdict(action="send", reason="rtr_error")
    except ValueError as e:
        # Bad tool_use shape -- _parse_verdict_block raised.
        logger.warning("RTR parse error: %s", e)
        _append_event(
            session,
            _make_event(
                "rtr.failed",
                chat_id=chat_id,
                draft_text=draft_text,
                reason="rtr_error",
                error="ValueError",
            ),
        )
        return RoomVerdict(action="send", reason="rtr_error")
    except Exception as e:
        # Last-resort catch -- never let RTR crash the send path.
        logger.warning("RTR unexpected error (%s): %s", type(e).__name__, e)
        _append_event(
            session,
            _make_event(
                "rtr.failed",
                chat_id=chat_id,
                draft_text=draft_text,
                reason="rtr_error",
                error=type(e).__name__,
            ),
        )
        return RoomVerdict(action="send", reason="rtr_error")


# Public re-exports so call sites can `from bridge.read_the_room import ...`
# without poking module internals for tests.
__all__ = [
    "RoomVerdict",
    "RTR_SUPPRESS_EMOJI",
    "TRIM_TOO_SHORT_THRESHOLD",
    "READ_THE_ROOM_SYSTEM_PROMPT",
    "DEFAULT_K",
    "DEFAULT_MAX_AGE_SECONDS",
    "RTR_SDK_TIMEOUT",
    "read_the_room",
]
