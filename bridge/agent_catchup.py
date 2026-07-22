"""Agent-judgment ``/catchup``: recover sessioned-but-unanswered messages.

The mechanical catchup (``bridge/catchup.py::scan_for_missed_messages``) and the
periodic reconciler (``bridge/reconciler.py::reconcile_once``) both key recovery
on **"did a session get enqueued"** — gated by ``is_duplicate_message`` (a
~50-ID-per-chat ``DedupRecord``) plus the ``LastProcessedRecord`` cursor. Neither
keys on **"did a reply actually reach the chat."** So a message whose session
hung or was killed *without replying* is dedup-marked **processed** and skipped
**forever** by both scanners — bookkeeping-indistinguishable from a message that
was answered correctly.

This module answers a different question: *did Valor actually reply in the
thread?* The **thread is the source of truth** — Valor's own ``out`` messages are
the ground truth for what's been said. We read the recent thread (including
Valor's replies), ask an LLM judge which inbound human messages are genuinely
unanswered, and enqueue recovery sessions only for those. We do **not** add a new
"answered-ness" watermark or store (forbidden by #948); we read the thread, and
on actual recovery we ALSO write through the existing
``record_message_processed`` / ``record_last_processed`` dedup path so the
*mechanical* scanners stay consistent with what we recovered.

**Idempotency is provided by the landed-reply guard, NOT by a dedup read.** This
module never reads the dedup set (``is_duplicate_message``) to decide whether to
enqueue — the thread is the source of truth. What keeps recovery to AT MOST one
reply per message is the two-layer landed-reply guard: the snapshot check
(``_has_valor_reply_after`` on the thread read at the top of the sweep) plus a
FRESH, targeted re-read IMMEDIATELY before enqueue (``_valor_replied_since``).
Once a recovery session's reply lands in the thread, every subsequent sweep sees
it and skips. The dedup write after enqueue is for the *mechanical* scanners'
bookkeeping; it is not what makes this module idempotent.

**Conservative by construction.** Any error, ambiguity, empty/garbage/None judge
output → ``ANSWERED`` (no reply). The acceptance bar is: a thread whose recent
messages were already answered produces NO reply. A missed reply is recoverable
on the next sweep; a spurious double-reply to a customer is not.

This is an additive layer. It does NOT modify ``scan_for_missed_messages`` or
``reconcile_once``, does NOT make ``_check_if_handled`` thread-aware, and does NOT
wire a scheduler — it is a standalone CLI (``valor-catchup``) invoked out-of-band
(and, separately, as ``/update``'s best-effort final step).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from agent.llm import run_typed
from bridge.routing import persona_to_session_type, resolve_persona
from config.enums import SessionType
from config.models import MODEL_FAST

logger = logging.getLogger(__name__)

# Greppable marker for every WARNING this module emits, so a single
# `grep '\[agent-catchup\]' logs/*.log` surfaces the whole sweep.
LOG_PREFIX = "[agent-catchup]"

# Judge verdict classes.
ANSWERED = "ANSWERED"
UNANSWERED_NEEDS_REPLY = "UNANSWERED_NEEDS_REPLY"
UNANSWERED_NO_REPLY_NEEDED = "UNANSWERED_NO_REPLY_NEEDED"

# Lookback bound per chat: min(last N messages, last H hours), capped.
# Mirrors the reconciler's bounded get_messages call (#1408).
MAX_MESSAGES_PER_CHAT = 20
LOOKBACK_HOURS = 2

# Per-message transcript truncation for the judge prompt (keep tokens bounded).
_MAX_MSG_CHARS = 400
_SENDER_VALOR = "Valor"


# =============================================================================
# Data shapes
# =============================================================================


@dataclass
class ThreadMessage:
    """A single message in a chat thread, normalized for the judge.

    ``is_valor`` is True for Valor's own ``out`` replies (the ground truth for
    what has been said). ``message_id``, ``sender_name`` and ``date`` carry the
    inbound-message metadata needed to enqueue a recovery session.
    """

    message_id: int
    text: str
    is_valor: bool
    sender_name: str
    sender_id: int | None
    date: datetime
    reply_to_msg_id: int | None = None  # threaded reply target, None if top-level
    reply_to_sender: str | None = None  # sender name of the replied-to message, if known


@dataclass
class OwnedChat:
    """An owned chat to sweep: the Telethon entity plus its project config."""

    chat_id: int
    chat_title: str
    project: dict
    entity: object  # Telethon entity (opaque here)


@dataclass
class ChatResult:
    """Per-chat sweep outcome for the CLI summary.

    A chat that errored has ``errored=True`` and ``error`` set; it MUST still
    appear in the summary (never silently dropped).
    """

    chat_title: str
    chat_id: int
    messages_scanned: int = 0
    enqueued: int = 0
    errored: bool = False
    error: str | None = None
    verdicts: list[tuple[int, str]] = field(default_factory=list)


# =============================================================================
# LLM judge (PydanticAI wrapper, Haiku default)
# =============================================================================


class CatchupJudgeVerdict(BaseModel):
    """Typed structured output for the agent-catchup LLM judge (#1925).

    Replaces the previous hand-rolled Ollama-chat/Haiku-fallback pair
    and its free-text ``_parse_verdict`` scan -- PydanticAI's ``Literal``
    schema forces one of the three valid verdicts directly, with a single
    auto-retry on mismatch.
    """

    verdict: Literal[ANSWERED, UNANSWERED_NEEDS_REPLY, UNANSWERED_NO_REPLY_NEEDED]


def _build_judge_prompt(transcript: str, inbound_text: str, inbound_id: int) -> str:
    """Render the judge prompt for one inbound message against the thread."""
    return (
        "You are judging whether a specific inbound human message in a Telegram "
        "GROUP CHAT has been ANSWERED by Valor (an AI agent).\n\n"
        "Valor's own messages are tagged 'Valor:'. They are the ground truth for "
        "what has actually been said in the chat.\n\n"
        "Messages tagged '[replying to X]' are threaded replies TO that person, "
        "NOT to Valor.\n\n"
        "Recent thread (oldest first):\n"
        f"{transcript}\n\n"
        f"The message in question (id={inbound_id}):\n"
        f"{inbound_text[:_MAX_MSG_CHARS]}\n\n"
        "Classify with EXACTLY one of these labels:\n"
        "- ANSWERED = Valor already replied to this message in the thread, OR the "
        "message needs no reply.\n"
        "- UNANSWERED_NEEDS_REPLY = this is a direct question or request addressed "
        "TO Valor (e.g. @valorengels, a reply to Valor's own message, or a general "
        "group question Valor should clearly answer) that Valor has NOT yet replied to.\n"
        "- UNANSWERED_NO_REPLY_NEEDED = no Valor reply yet, but none is warranted. "
        "Use this for: reactions/acknowledgments ('nice!', 'interesting'), messages "
        "between other participants, messages replying to someone other than Valor, "
        "or side conversations Valor was not part of.\n\n"
        "IMPORTANT: This is a group chat. Most messages are between human participants "
        "and do NOT require Valor to respond. Only choose UNANSWERED_NEEDS_REPLY when "
        "the message is clearly directed at Valor specifically.\n"
        "Be CONSERVATIVE: when in doubt, choose ANSWERED.\n\n"
        "Reply with ONLY one of: ANSWERED, UNANSWERED_NEEDS_REPLY, "
        "UNANSWERED_NO_REPLY_NEEDED."
    )


async def judge_message(transcript: str, inbound_text: str, inbound_id: int) -> str:
    """Classify whether one inbound message is answered, by reading the thread.

    Returns one of three classes:

    - ``ANSWERED`` — Valor already replied in the thread, OR no reply is warranted.
    - ``UNANSWERED_NEEDS_REPLY`` — a genuine question/request with no Valor reply
      yet that clearly should be answered. This is the ONLY class that triggers a
      recovery enqueue.
    - ``UNANSWERED_NO_REPLY_NEEDED`` — no Valor reply yet, but none is warranted
      (acknowledgment, social chatter, directed elsewhere).

    Backend: PydanticAI wrapper, Haiku default (#1925) — replaces the previous
    Ollama-first/Haiku-fallback pair (mirrors the migrated
    ``bridge.routing.classify_conversation_terminus``). The ``CatchupJudgeVerdict``
    schema forces one of the three valid verdicts directly, so the old
    free-text ``_parse_verdict`` scan is no longer needed.

    CONSERVATIVE CONTRACT: any error maps to ``ANSWERED`` (no reply). A missed
    reply is recoverable on the next sweep; a spurious double-reply is not.
    This function NEVER raises — every failure path returns ``ANSWERED``.
    """
    prompt = _build_judge_prompt(transcript, inbound_text, inbound_id)

    try:
        decision = await run_typed(prompt, CatchupJudgeVerdict, model=MODEL_FAST)
        return decision.verdict
    except Exception as e:
        logger.debug("%s judge failed: %s", LOG_PREFIX, e)
        # Conservative default on any error.
        return ANSWERED


# =============================================================================
# Owner scoping
# =============================================================================


async def resolve_owned_chats(
    client,
    monitored_groups: list[str],
    find_project_fn,
) -> list[OwnedChat]:
    """Resolve this machine's owned chats from the live Telethon dialogs.

    Reuses the existing owner-scoping (``ALL_MONITORED_GROUPS``, already filtered
    to this machine's projects via ``ACTIVE_PROJECTS``) and the case-insensitive
    title match + duplicate-dialog guard that ``scan_for_missed_messages`` uses.
    Composes the existing helpers rather than reinventing owner scoping.

    Returns a list of ``OwnedChat`` (chat_id, chat_title, project, entity).
    """
    owned: list[OwnedChat] = []
    seen_chat_ids: set[int] = set()

    dialogs = await client.get_dialogs()
    for dialog in dialogs:
        chat_title = getattr(dialog.entity, "title", None)
        if not chat_title:
            continue
        # monitored_groups holds lowercase names; titles may have capitals.
        if chat_title.lower() not in monitored_groups:
            continue
        # Telethon can return the same supergroup twice (channel + linked group).
        if dialog.id in seen_chat_ids:
            logger.warning(
                "%s skipping duplicate dialog for %s (id=%s)",
                LOG_PREFIX,
                chat_title,
                dialog.id,
            )
            continue
        seen_chat_ids.add(dialog.id)

        project = find_project_fn(chat_title)
        if not project:
            logger.warning("%s no project config for %s", LOG_PREFIX, chat_title)
            continue

        owned.append(
            OwnedChat(
                chat_id=dialog.id,
                chat_title=chat_title,
                project=project,
                entity=dialog.entity,
            )
        )
    return owned


# =============================================================================
# Thread read
# =============================================================================


async def read_thread(client, entity, lookback: timedelta | None = None) -> list[ThreadMessage]:
    """Read the recent thread for a chat INCLUDING Valor's own ``out`` replies.

    Bounded read: min(last ``MAX_MESSAGES_PER_CHAT`` messages, last
    ``LOOKBACK_HOURS`` hours). Returns messages oldest-first so the judge sees a
    natural transcript. ``m.out`` maps to Valor (same mapping that backs
    ``valor-telegram read``).
    """
    effective = lookback if lookback is not None else timedelta(hours=LOOKBACK_HOURS)
    cutoff = datetime.now(UTC) - effective

    raw = await client.get_messages(entity, limit=MAX_MESSAGES_PER_CHAT)

    out: list[ThreadMessage] = []
    # Build an id→sender index as we go so reply_to_sender can be filled cheaply.
    id_to_sender: dict[int, str] = {}
    for m in raw:
        if m.date < cutoff:
            break  # get_messages returns newest-first; older than cutoff → stop.
        text = m.text or ""
        is_valor = bool(m.out)
        sender_name = _SENDER_VALOR
        sender_id = None
        if not is_valor:
            try:
                sender = await m.get_sender()
            except Exception:
                sender = None
            sender_name = getattr(sender, "first_name", None) or "Unknown"
            sender_id = getattr(sender, "id", None)

        # Threaded reply context: Telethon stores reply_to as a MessageReplyHeader.
        reply_to_msg_id: int | None = None
        reply_to_sender: str | None = None
        try:
            reply_header = getattr(m, "reply_to", None)
            if reply_header is not None:
                reply_to_msg_id = getattr(reply_header, "reply_to_msg_id", None)
                if reply_to_msg_id is not None:
                    reply_to_sender = id_to_sender.get(reply_to_msg_id)
        except Exception:  # noqa: S110 -- best-effort reply-context parse
            pass

        id_to_sender[m.id] = sender_name
        out.append(
            ThreadMessage(
                message_id=m.id,
                text=text,
                is_valor=is_valor,
                sender_name=sender_name,
                sender_id=sender_id,
                date=m.date,
                reply_to_msg_id=reply_to_msg_id,
                reply_to_sender=reply_to_sender,
            )
        )

    out.reverse()  # oldest-first
    # Second pass: fill reply_to_sender for any reply_to_msg_id not yet resolved
    # (the raw list is newest-first so earlier messages appear later in iteration).
    full_index = {m.message_id: m.sender_name for m in out}
    for m in out:
        if m.reply_to_msg_id is not None and m.reply_to_sender is None:
            m.reply_to_sender = full_index.get(m.reply_to_msg_id)
    return out


def _render_transcript(thread: list[ThreadMessage]) -> str:
    """Render a thread as a 'Sender: text' transcript for the judge prompt.

    When a message is a threaded reply to another participant, the prefix is
    annotated with ``[replying to X]`` so the judge can see cross-participant
    side conversations clearly.
    """
    lines = []
    for m in thread:
        who = _SENDER_VALOR if m.is_valor else m.sender_name
        if m.reply_to_msg_id is not None and not m.is_valor:
            reply_target = m.reply_to_sender or f"msg#{m.reply_to_msg_id}"
            who = f"{who} [replying to {reply_target}]"
        lines.append(f"{who}: {m.text[:_MAX_MSG_CHARS]}")
    return "\n".join(lines)


def _has_valor_reply_after(thread: list[ThreadMessage], inbound_id: int) -> bool:
    """True if any Valor ``out`` message appears after the given inbound message.

    The thread is oldest-first. A Valor reply that lands later in the transcript
    than the inbound message is treated as a fresh reply for the double-reply
    guard. This is intentionally position-based (not threaded-reply-based),
    because most replies are not threaded.
    """
    seen_inbound = False
    for m in thread:
        if m.message_id == inbound_id:
            seen_inbound = True
            continue
        if seen_inbound and m.is_valor:
            return True
    return False


async def _valor_replied_since(client, entity, inbound_id: int) -> bool:
    """Fresh, targeted re-read: did a Valor ``out`` reply land after ``inbound_id``?

    This is the Race-1 mitigation (plan: "Race Conditions" → Race 1, Risk 1). The
    judge loop runs one LLM call per message (up to ``MAX_MESSAGES_PER_CHAT``), so
    many seconds can elapse between the single snapshot read at the top of
    ``sweep_chat`` and an actual enqueue. A customer reply may land in that window.
    We therefore re-check IMMEDIATELY before enqueue, with a cheap targeted read
    (newest ``MAX_MESSAGES_PER_CHAT`` messages, filtered to ids strictly greater
    than ``inbound_id``). Mirrors ``bridge.catchup._check_if_handled``'s
    ``get_messages``-since pattern, but position/id-based rather than
    threaded-reply-based (most replies are not threaded).

    Returns True if a fresh Valor ``out`` message with ``id > inbound_id`` now
    exists. On ANY re-read error this returns False (greppable WARNING logged):
    the conservative pre-existing snapshot guard already ran, so returning False
    preserves current behavior rather than making it worse — it never crashes the
    sweep. ``min_id`` is intentionally NOT passed to ``get_messages`` so in-memory
    fake clients (which accept only ``limit``) can serve the re-read; filtering is
    done in Python on ``m.id``.
    """
    try:
        raw = await client.get_messages(entity, limit=MAX_MESSAGES_PER_CHAT)
    except Exception as e:
        logger.warning(
            "%s pre-enqueue re-read failed for msg=%s; falling back to snapshot guard: %s",
            LOG_PREFIX,
            inbound_id,
            e,
        )
        return False

    for m in raw:
        if getattr(m, "out", False) and getattr(m, "id", 0) > inbound_id:
            return True
    return False


# =============================================================================
# Per-chat sweep
# =============================================================================


async def sweep_chat(
    client,
    chat: OwnedChat,
    *,
    enqueue_fn,
    judge_fn=judge_message,
    record_processed_fn=None,
    record_last_fn=None,
    lookback: timedelta | None = None,
) -> ChatResult:
    """Judge one owned chat's recent thread and enqueue genuine misses.

    For each inbound human message (skipping Valor's own and whitespace-only
    text), run ``judge_fn`` against the rendered transcript. On
    ``UNANSWERED_NEEDS_REPLY``: apply the landed-reply guard in TWO layers — the
    snapshot check (``_has_valor_reply_after`` on the thread read at the top of
    the sweep) and a FRESH targeted re-read immediately before enqueue
    (``_valor_replied_since``, the Race-1 mitigation: a reply may have landed
    during the multi-second judge loop). If either sees a Valor reply after the
    message, skip. Otherwise resolve persona, enqueue exactly one recovery session
    with the ORIGINAL inbound text (never composed reply text), then write dedup
    immediately (for the mechanical scanners' bookkeeping — the landed-reply guard,
    not the dedup write, is what makes this idempotent).

    ``judge_fn`` and ``enqueue_fn`` are injectable so unit tests can stub the
    judge and assert enqueues. ``record_processed_fn`` / ``record_last_fn``
    default to the real ``bridge.dedup`` writers.

    NARROW try/except: a per-message failure logs a greppable WARNING and
    continues; the per-chat caller (``run_sweep``) wraps this whole call so a
    chat-level failure is recorded and the sweep proceeds to the next chat.
    """
    if record_processed_fn is None or record_last_fn is None:
        from bridge.dedup import record_last_processed, record_message_processed

        record_processed_fn = record_processed_fn or record_message_processed
        record_last_fn = record_last_fn or record_last_processed

    result = ChatResult(chat_title=chat.chat_title, chat_id=chat.chat_id)

    thread = await read_thread(client, chat.entity, lookback=lookback)
    result.messages_scanned = len(thread)

    # Empty thread → judge not called, zero enqueues.
    if not thread:
        return result

    transcript = _render_transcript(thread)

    for m in thread:
        # Skip Valor's own messages — only inbound human messages are judged.
        if m.is_valor:
            continue
        # Skip whitespace-only / empty text BEFORE the judge (mirror the scanners).
        if not m.text.strip():
            continue

        try:
            verdict = await judge_fn(transcript, m.text, m.message_id)
        except Exception as e:
            # Defensive: judge_fn already swallows errors and returns ANSWERED,
            # but a stubbed/injected judge might raise. Conservative default.
            logger.warning(
                "%s judge raised for chat=%s msg=%s; defaulting ANSWERED: %s",
                LOG_PREFIX,
                chat.chat_id,
                m.message_id,
                e,
            )
            verdict = ANSWERED

        result.verdicts.append((m.message_id, verdict))

        if verdict != UNANSWERED_NEEDS_REPLY:
            continue

        # Double-reply guard (snapshot): if a Valor reply already appears after
        # this message in the thread read at the top of the sweep, do not enqueue.
        if _has_valor_reply_after(thread, m.message_id):
            logger.info(
                "%s chat=%s msg=%s judged UNANSWERED but a Valor reply exists "
                "after it — skipping (double-reply guard)",
                LOG_PREFIX,
                chat.chat_id,
                m.message_id,
            )
            continue

        # Race-1 mitigation (defense in depth): the judge loop may have run for
        # many seconds since the snapshot above. Do a FRESH, targeted re-read
        # IMMEDIATELY before enqueue and skip if a Valor reply has landed in the
        # meantime — this is the guard that prevents a customer double-reply.
        if await _valor_replied_since(client, chat.entity, m.message_id):
            logger.warning(
                "%s chat=%s msg=%s judged UNANSWERED but a Valor reply landed "
                "during judgment — skipping enqueue (Race-1 pre-enqueue re-read)",
                LOG_PREFIX,
                chat.chat_id,
                m.message_id,
            )
            continue

        try:
            await _enqueue_recovery(
                chat=chat,
                inbound=m,
                enqueue_fn=enqueue_fn,
                record_processed_fn=record_processed_fn,
                record_last_fn=record_last_fn,
            )
            result.enqueued += 1
        except Exception as e:
            logger.warning(
                "%s enqueue failed for chat=%s msg=%s; continuing: %s",
                LOG_PREFIX,
                chat.chat_id,
                m.message_id,
                e,
            )
            continue

    return result


async def _enqueue_recovery(
    *,
    chat: OwnedChat,
    inbound: ThreadMessage,
    enqueue_fn,
    record_processed_fn,
    record_last_fn,
) -> None:
    """Enqueue one recovery session, then write dedup immediately.

    Mirrors ``scan_for_missed_messages`` / ``reconcile_once`` exactly: same
    ``session_id`` shape (``tg_{project_key}_{chat_id}_{message_id}``), same
    ``enqueue_agent_session`` signature, same dedup write right after enqueue.
    Persona is resolved via ``resolve_persona`` → ``persona_to_session_type``
    (the #1708 helpers) with the same narrow per-message fallback to eng.

    NEVER composes reply text — only the ORIGINAL inbound message text is
    enqueued; the worker session produces the persona-correct reply.
    """
    project = chat.project
    project_key = project.get("_key", "unknown")
    working_dir = project.get("working_directory", "")
    session_id = f"tg_{project_key}_{chat.chat_id}_{inbound.message_id}"

    try:
        persona = resolve_persona(project, chat.chat_title, is_dm=False)
        session_type = persona_to_session_type(persona)
    except Exception as e:
        logger.warning(
            "%s persona resolution failed for chat %s (%s); defaulting to eng: %s",
            LOG_PREFIX,
            chat.chat_id,
            chat.chat_title,
            e,
        )
        session_type = SessionType.ENG

    logger.warning(
        "%s recovering unanswered message in %s: msg %d from %s: '%s'",
        LOG_PREFIX,
        chat.chat_title,
        inbound.message_id,
        inbound.sender_name,
        inbound.text[:80],
    )

    await enqueue_fn(
        project_key=project_key,
        session_id=session_id,
        working_dir=working_dir,
        message_text=inbound.text,  # ORIGINAL inbound text only — never composed.
        sender_name=inbound.sender_name,
        chat_id=str(chat.chat_id),
        telegram_message_id=inbound.message_id,
        chat_title=chat.chat_title,
        priority="low",
        sender_id=inbound.sender_id,
        session_type=session_type,
        project_config=project,
    )

    # Dedup write immediately after enqueue — same as the mechanical scanners, to
    # keep THEIR bookkeeping consistent with what we recovered. This is NOT what
    # makes this module idempotent: the landed-reply guard (snapshot +
    # pre-enqueue re-read) is. This module never reads the dedup set.
    await record_processed_fn(chat.chat_id, inbound.message_id)
    await record_last_fn(chat.chat_id, inbound.message_id, inbound.date)


# =============================================================================
# Full sweep
# =============================================================================


async def run_sweep(
    client,
    owned_chats: list[OwnedChat],
    *,
    enqueue_fn,
    judge_fn=judge_message,
    record_processed_fn=None,
    record_last_fn=None,
    lookback: timedelta | None = None,
) -> list[ChatResult]:
    """Sweep every owned chat, returning a per-chat result list.

    Each chat's sweep is wrapped in a NARROW try/except: on failure, a greppable
    WARNING is logged and a ``ChatResult`` with ``errored=True`` is appended — the
    chat appears in the summary, the sweep never aborts. Best-effort contract.
    """
    results: list[ChatResult] = []
    for chat in owned_chats:
        try:
            result = await sweep_chat(
                client,
                chat,
                enqueue_fn=enqueue_fn,
                judge_fn=judge_fn,
                record_processed_fn=record_processed_fn,
                record_last_fn=record_last_fn,
                lookback=lookback,
            )
        except Exception as e:
            logger.warning(
                "%s sweep failed for chat=%s (%s); continuing: %s",
                LOG_PREFIX,
                chat.chat_id,
                chat.chat_title,
                e,
            )
            result = ChatResult(
                chat_title=chat.chat_title,
                chat_id=chat.chat_id,
                errored=True,
                error=str(e),
            )
        results.append(result)
    return results


def format_summary(results: list[ChatResult]) -> str:
    """Render a per-chat summary, INCLUDING chats that errored.

    Errored chats are never silently dropped — they appear with an ERROR tag.
    """
    lines = ["[agent-catchup] sweep summary:"]
    total_enqueued = 0
    for r in results:
        if r.errored:
            lines.append(f"  {r.chat_title} (id={r.chat_id}): ERROR — {r.error}")
            continue
        total_enqueued += r.enqueued
        lines.append(
            f"  {r.chat_title} (id={r.chat_id}): "
            f"scanned={r.messages_scanned}, recovered={r.enqueued}"
        )
    errored = sum(1 for r in results if r.errored)
    lines.append(f"  total: {len(results)} chat(s), {total_enqueued} recovered, {errored} errored")
    return "\n".join(lines)


# =============================================================================
# CLI entry point
# =============================================================================


async def _run_async(lookback: timedelta | None = None) -> list[ChatResult]:
    """Resolve owned chats against a live Telethon client and run the sweep.

    Composes the production wiring: the ``valor-telegram`` Telethon client, the
    bridge's owner-scoping globals (``ALL_MONITORED_GROUPS`` /
    ``find_project_for_chat``), and the real ``enqueue_agent_session``.
    """
    # Import the bridge module for its side effect of populating owner-scoping
    # globals (ACTIVE_PROJECTS → GROUP_TO_PROJECT → ALL_MONITORED_GROUPS) and
    # propagating them into bridge.routing.
    import bridge.telegram_bridge as tb
    from agent.agent_session_queue import enqueue_agent_session
    from bridge.routing import find_project_for_chat
    from tools.valor_telegram import _telethon_client

    monitored_groups = list(tb.ALL_MONITORED_GROUPS)
    if not monitored_groups:
        logger.warning("%s no monitored groups for this machine — nothing to sweep", LOG_PREFIX)
        return []

    client = _telethon_client()
    await client.start()
    try:
        owned = await resolve_owned_chats(client, monitored_groups, find_project_for_chat)
        return await run_sweep(
            client,
            owned,
            enqueue_fn=enqueue_agent_session,
            lookback=lookback,
        )
    finally:
        await client.disconnect()


def main() -> int:
    """``valor-catchup`` entry point.

    Resolves this machine's owned chats, runs the agent-judgment sweep, prints a
    per-chat summary (including errored chats), and ALWAYS exits 0 — even on
    partial failure (best-effort contract; ``/update`` ignores the exit code).
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="valor-catchup",
        description=(
            "Agent-judgment catchup: read each owned chat's thread, judge which "
            "inbound messages are genuinely unanswered, and enqueue recovery "
            "sessions. Strongly biased toward NOT replying. Always exits 0."
        ),
    )
    parser.add_argument(
        "--lookback-hours",
        type=float,
        default=None,
        help=f"Lookback window in hours (default: {LOOKBACK_HOURS}).",
    )
    args = parser.parse_args()

    from bridge.catchup import CATCHUP_DISABLED_FLAG, catchup_disabled

    if catchup_disabled():
        msg = f"skipped — {CATCHUP_DISABLED_FLAG} exists (operator kill switch)"
        logger.warning("%s %s", LOG_PREFIX, msg)
        print(f"[agent-catchup] {msg}")
        return 0

    lookback = timedelta(hours=args.lookback_hours) if args.lookback_hours is not None else None

    try:
        results = asyncio.run(_run_async(lookback=lookback))
    except Exception as e:
        # Best-effort contract: never crash, never non-zero exit.
        logger.warning("%s sweep aborted at top level: %s", LOG_PREFIX, e)
        print(f"[agent-catchup] sweep aborted: {e}")
        return 0

    print(format_summary(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
