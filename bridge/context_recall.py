"""Context-recall advisory: tell the PM when it is missing conversation context.

Issue #2694. Two edges raise the same advisory, and both hand the PM a
fully-formed, copy-pasteable history-read command with the *real* chat id
already interpolated:

* **Inbound** — the intake classifier (``tools/classifier.py``) judges
  ``context_recall_advised`` as two extra fields on its existing local-granite
  pass, so there is no second LLM call on the inbound hot path. Ships dark
  behind ``CONTEXT_RECALL_INBOUND_ENABLED`` (default ``false``, plan decision
  D1) because the 3B model's judgment has not cleared an accuracy bar.
* **Outbound** — ``check_outbound_context_recall`` inspects PM output that
  looks like a referent-clarifying question ("which PR do you mean?") and, on
  a positive verdict, ``agent/output_handler.py`` suppresses the message and
  bounces it back through the existing self-draft steering loop (D2).

This module owns the **single** definition of the advisory text so the two
edges cannot drift, plus the structural prefilter that keeps the paid Haiku
call off the outbound hot path.

Everything here fails open: any exception, timeout, unusable chat id, or
disabled kill switch degrades to exactly the pre-#2694 behavior. Nothing in
this module can drop or delay a message on its own.
"""

from __future__ import annotations

import logging
import os

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Number of recent messages the emitted command asks for. GRAIN OF SALT:
# provisional/tunable via env. Matches `valor-telegram read`'s own
# `--limit` default (tools/valor_telegram.py) so the advisory does not invent a
# depth the CLI disagrees with. Both media read the same constant so the two
# edges cannot drift (plan decision D3).
CONTEXT_RECALL_HISTORY_DEPTH = int(os.environ.get("CONTEXT_RECALL_HISTORY_DEPTH", "10"))

# Longest PM output (characters) still eligible for the outbound Haiku verdict.
# GRAIN OF SALT: provisional/tunable via env. This is the cheapness gate: it
# alone decides how much outbound traffic reaches a paid call, so it is a named
# constant rather than an inline literal. A genuine referent-clarifying question
# ("which PR do you mean?") is short; anything longer is carrying its own
# context and does not need history.
CONTEXT_RECALL_PREFILTER_MAX_CHARS = int(
    os.environ.get("CONTEXT_RECALL_PREFILTER_MAX_CHARS", "200")
)

_TRUTHY = ("1", "true", "yes", "on")

# Model prompt for the outbound verdict. The test is a question about
# *referents*, never a keyword allowlist (Development Principle 3).
OUTBOUND_CONTEXT_RECALL_PROMPT = """You are judging one outgoing message written by an
AI assistant to its human colleague.

Message:
{text}

Question: is this message asking the human to supply context that the assistant
could instead recover by re-reading the recent conversation history?

Answer true when the message asks the human to identify, disambiguate, or
re-state something that was already named earlier in the conversation -- "which
PR do you mean?", "which one?", "what were we doing?", "can you remind me what
you wanted?".

Answer false when the message is a substantive answer, a status update, or a
question whose answer the human alone holds -- a decision, a preference, an
approval, or a fact that lives outside the conversation.

Return advised (bool) and a concise reason."""


class ContextRecallVerdict(BaseModel):
    """Outbound judgment: should this message be held pending a history read?"""

    advised: bool = False
    reason: str = Field(default="")


def inbound_enabled() -> bool:
    """Read ``CONTEXT_RECALL_INBOUND_ENABLED`` fresh on each call.

    Default ``false`` (plan decision D1 — inbound ships dark). Read fresh
    rather than via a cached ``pydantic-settings`` field so the kill switch can
    be flipped without a process restart, mirroring
    ``bridge/read_the_room.py::_read_enabled``.
    """
    return os.environ.get("CONTEXT_RECALL_INBOUND_ENABLED", "false").strip().lower() in _TRUTHY


def outbound_enabled() -> bool:
    """Read ``CONTEXT_RECALL_OUTBOUND_ENABLED`` fresh on each call (default ``true``)."""
    return os.environ.get("CONTEXT_RECALL_OUTBOUND_ENABLED", "true").strip().lower() in _TRUTHY


def _chat_id_usable(chat_id, medium: str) -> bool:
    """Medium-aware chat-id validation.

    The guard MUST branch on ``medium`` before it validates.
    ``utils.peer.deliverable_telegram_peer`` accepts only a nonzero *integer*
    peer, but email sessions carry ``chat_id = from_addr``
    (``bridge/email_bridge.py``), so running the Telegram guard on an email
    session would reject every single one and the email leg would be dead on
    arrival.

    Telegram session ids are rejected here only *incidentally*: session ids are
    non-numeric, so the numeric peer parse fails. Nothing compares ``chat_id``
    to ``session_id``.
    """
    if medium == "telegram":
        from utils.peer import deliverable_telegram_peer

        return deliverable_telegram_peer(chat_id)
    return bool(chat_id) and "@" in str(chat_id)


def build_context_recall_advisory(
    *,
    chat_id,
    medium: str = "telegram",
    reason: str | None = None,
) -> str | None:
    """Compose the advisory handed to the PM, or ``None`` to stay silent.

    **No-placeholder contract:** the returned string always carries a
    fully-formed, runnable command with the real chat id already interpolated.
    It never contains a stand-in token for the caller to substitute. When the
    chat id is unusable for ``medium`` this returns ``None`` — an advisory
    pointing at a command that cannot run is worse than no advisory.

    Args:
        chat_id: the session's ``chat_id``. A numeric Telegram peer, or an
            email address when ``medium == "email"``.
        medium: ``"telegram"`` or ``"email"``.
        reason: optional judgment reason, appended verbatim for the PM's
            benefit.

    Returns:
        The advisory text, or ``None`` on any guard miss or failure.
    """
    try:
        if not _chat_id_usable(chat_id, medium):
            return None

        depth = CONTEXT_RECALL_HISTORY_DEPTH
        if medium == "telegram":
            command = f"valor-telegram read --chat-id {chat_id} -n {depth}"
        else:
            command = f'valor-email read --search "{chat_id}" -n {depth}'

        advisory = (
            "[context-recall] This message may depend on earlier conversation "
            "you cannot see. Read the recent history before answering:\n"
            f"    {command}\n"
            "Decide for yourself whether you need it — nothing reads it for you."
        )
        if reason:
            advisory += f"\nWhy this was flagged: {reason}"

        logger.info(
            "[context-recall] advisory built (medium=%s, command=%s)",
            medium,
            "valor-telegram read" if medium == "telegram" else "valor-email read",
        )
        return advisory
    except Exception as e:
        # Fail open: no advisory is always safe.
        logger.warning("[context-recall] advisory build failed (non-fatal): %s", e)
        return None


def build_email_fallback_advisory() -> str:
    """Advisory for an email session whose peer address is unresolvable.

    ``valor-email read`` has no per-peer flag — only ``--search`` — so with no
    address to search there is nothing to interpolate. Point at
    ``valor-email threads`` instead, which is still a fully-formed command.
    """
    return (
        "[context-recall] This message may depend on earlier conversation "
        "you cannot see. Read the recent threads before answering:\n"
        "    valor-email threads\n"
        "Decide for yourself whether you need it — nothing reads it for you."
    )


def _prefilter(text: str | None) -> bool:
    """Cheap structural gate: is this output even shaped like the target case?

    Gates on message *shape*, not on intent keywords: short, and asking
    something. Anything else never reaches the paid model call, which is what
    keeps the outbound edge off the latency budget.
    """
    if not text or not text.strip():
        return False
    stripped = text.strip()
    if len(stripped) > CONTEXT_RECALL_PREFILTER_MAX_CHARS:
        return False
    return "?" in stripped


async def check_outbound_context_recall(text: str | None) -> ContextRecallVerdict:
    """Judge whether outbound PM text is a recoverable-context question.

    Haiku rather than granite on this edge: a false positive here suppresses a
    real message and costs a human round trip, so precision is worth the paid
    call. The structural prefilter keeps it off the hot path.

    Never raises. Every failure mode — kill switch off, prefilter reject,
    timeout, provider error, schema exhaustion — returns ``advised=False``, so
    the message is sent exactly as it would be today.
    """
    try:
        if not outbound_enabled():
            return ContextRecallVerdict(advised=False, reason="kill switch off")
        if not _prefilter(text):
            return ContextRecallVerdict(advised=False, reason="prefilter reject")

        from agent.llm import run_typed

        # sdk_timeout mirrors the promise gate's budget. No coroutine-level
        # asyncio.wait_for is added here: run_typed already owns the
        # double-timeout pattern, and wrapping the Anthropic client in an outer
        # cancel leaks httpx connections (documented in bridge/promise_gate.py).
        verdict = await run_typed(
            OUTBOUND_CONTEXT_RECALL_PROMPT.format(text=text),
            ContextRecallVerdict,
            sdk_timeout=3.0,
        )
        return verdict
    except Exception as e:
        logger.warning("[context-recall] outbound check failed, sending as-is: %s", e)
        return ContextRecallVerdict(advised=False, reason=f"check failed: {e}")
