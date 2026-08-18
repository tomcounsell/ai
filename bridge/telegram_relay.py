"""Telegram message relay: processes the PM outbox queue.

Async task that runs in the bridge's event loop alongside the session queue
consumer. Polls Redis for agent-authored messages queued by
tools/send_message.py and sends them via Telethon.

Redis queue contract:
    Key pattern: telegram:outbox:{session_id}
    Message format: JSON with {chat_id, reply_to, text, file_paths?, session_id, timestamp}
    TTL: 1 hour (set by the tool, safety net for crashed sessions)

    Backward compatibility: legacy payloads with ``file_path`` (string) are
    normalized to ``file_paths`` (list) at relay time during rolling deployments.

Retry and dead-letter behavior:
    Failed messages are re-queued with a ``_relay_attempts`` counter embedded in
    the JSON payload. After ``MAX_RELAY_RETRIES`` (default 3) failed attempts,
    text messages are routed to the dead letter queue via ``bridge/dead_letters.py``
    for later replay. Reactions and custom emoji messages are ephemeral and are
    discarded after exhausting retries. Unknown message types are rejected
    immediately without entering the retry loop.

After successful send, records the Telegram message ID on the AgentSession's
pm_sent_message_ids field. This list is checked by the PM self-message bypass
in bridge/telegram_bridge.py's send callback.
"""

import asyncio
import json
import logging
import os

import redis
from telethon.errors import FloodWaitError

from utils.peer import numeric_peer

logger = logging.getLogger(__name__)

# Poll interval for checking outbox queues (100ms for low latency)
RELAY_POLL_INTERVAL = 0.1

# Maximum messages to process per poll cycle (prevents starvation)
RELAY_BATCH_SIZE = 10

# Redis key pattern for scanning outbox queues
OUTBOX_KEY_PATTERN = "telegram:outbox:*"

# Maximum relay attempts before routing to dead letter
MAX_RELAY_RETRIES = 3

# Flood-wait handling constants — provisional, tune from production flood-wait telemetry
RELAY_FLOOD_WAIT_BUFFER_SECS = int(os.environ.get("RELAY_FLOOD_WAIT_BUFFER_SECS", "5"))
RELAY_FLOOD_WAIT_MAX_SLEEP_SECS = int(os.environ.get("RELAY_FLOOD_WAIT_MAX_SLEEP_SECS", "300"))
RELAY_FLOOD_WAIT_MAX = int(os.environ.get("RELAY_FLOOD_WAIT_MAX", "10"))

# Known message types accepted by the relay dispatcher
KNOWN_MESSAGE_TYPES = {None, "reaction", "custom_emoji_message"}


class _DeliveredNoId:
    """Sentinel: a send reached Telegram but no ``message_id`` was captured.

    ``_send_queued_message`` returns ``int | None`` where ``None`` historically
    meant *both* "send failed/dropped" and "delivered but Telethon returned no
    id". That conflation let a delivered-but-idless ``pm_direct`` reply be
    treated as a failure, which skipped the ``#1205-style`` dedup registration
    (so the executor's ``response`` copy shipped too) and re-queued the message
    (#2179). This sentinel disambiguates the delivered-without-id case so the
    relay records the dedup draft and does not retry.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return "DELIVERED_NO_ID"


DELIVERED_NO_ID = _DeliveredNoId()


def _delivered(msg_id: int | None):
    """Wrap a delivered send's result: the id, or the delivered-no-id sentinel."""
    return msg_id if msg_id is not None else DELIVERED_NO_ID


# Maximum characters per chat_message_log entry (prevents Path A's multi-paragraph
# drafter output from inflating Redis storage to ~200KB per session).
MAX_CHAT_LOG_ENTRY_CHARS = 500


def _safe_unlink(path: str) -> None:
    """Unlink a temp file the relay was asked to clean up.

    Wrapped in try/except so cleanup never raises into the send path: a
    missing file just means another process beat us to it (or the file
    was never written), which is harmless. Used by the voice-note send
    path and by the DLQ placement path when the payload carries
    ``cleanup_file: True``.
    """
    import os as _os

    try:
        _os.unlink(path)
        logger.debug("Relay: cleaned up temp file %s", path)
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001
        logger.warning("Relay: cleanup_file unlink failed for %s: %s", path, e)


def _get_redis_connection() -> redis.Redis:
    """Get a synchronous Redis connection for queue operations."""

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(redis_url, decode_responses=True)


def _deliverable_peer(chat_id, kind: str, message: dict) -> bool:
    """True when ``chat_id`` is a peer Telethon can send to; log the drop otherwise.

    Two distinct drops, kept at different levels because they mean different
    things: a local session id (``local-<uuid>``) is routine — its output was
    already written by ``FileOutputHandler`` — while ``chat_id=0`` is the
    chatless-session placeholder leaking into an outbound payload, which would
    raise ``PeerIdInvalidError`` at send time (#2644).

    ``kind`` names the payload for the log line ("message", "reaction",
    "custom emoji message"). Every send path routes through here so the three
    cannot drift apart again.

    The parse itself belongs to ``utils.peer``, which is the single home for
    "is this a Telegram peer" across the source side too. Re-deriving it with a
    bare ``int()`` here — as the old message-path check did — recreated the
    drift this helper exists to prevent: ``int()`` accepts ``"+5"``, ``5.9``,
    and ``True``, all of which ``utils.peer`` rejects, so the relay and the
    source side disagreed about the same payload.
    """
    chat_id_int = numeric_peer(chat_id)
    if chat_id_int is None:
        logger.debug(f"Relay: dropping {kind} for non-Telegram chat_id '{chat_id}' (local session)")
        return False

    if chat_id_int == 0:
        logger.warning(
            f"Relay: dropping {kind} with zero chat_id (invalid Telegram peer): {message}"
        )
        return False

    return True


async def _send_queued_reaction(
    telegram_client,
    message: dict,
) -> bool:
    """Send a queued reaction via Telethon.

    Supports both standard emoji (``emoji`` field) and custom emoji
    (``custom_emoji_document_id`` field). When a custom emoji document_id
    is present, constructs an ``EmojiResult`` so ``set_reaction()`` can
    dispatch to ``ReactionCustomEmoji`` with automatic fallback.

    Args:
        telegram_client: The Telethon TelegramClient instance.
        message: Parsed reaction dict with chat_id, reply_to, emoji,
            and optional custom_emoji_document_id.

    Returns:
        True on success, False on failure. Failed reactions are not re-queued.
    """
    chat_id = message.get("chat_id")
    reply_to = message.get("reply_to")
    emoji = message.get("emoji")

    if not chat_id or not reply_to or not emoji:
        logger.warning(f"Relay: skipping malformed reaction payload: {message}")
        return False

    if not _deliverable_peer(chat_id, "reaction", message):
        return False

    try:
        from bridge.response import set_reaction

        # If custom emoji document_id is present, wrap in EmojiResult
        custom_doc_id = message.get("custom_emoji_document_id")
        if custom_doc_id is not None:
            from tools.emoji_embedding import EmojiResult

            emoji_result = EmojiResult(
                emoji=emoji,
                document_id=int(custom_doc_id),
                is_custom=True,
            )
            ok = await set_reaction(telegram_client, int(chat_id), int(reply_to), emoji_result)
        else:
            ok = await set_reaction(telegram_client, int(chat_id), int(reply_to), emoji)

        if ok:
            logger.info(f"Relay: set reaction {emoji} on msg {reply_to} in chat {chat_id}")
        else:
            logger.warning(
                f"Relay: failed to set reaction {emoji} on msg {reply_to} in chat {chat_id}"
            )
        return ok
    except FloodWaitError:
        raise
    except Exception as e:
        logger.warning(f"Relay: reaction send failed: {e}")
        return False


def _session_reached_terminal_status(session_id: str) -> bool:
    """True when the session owning this payload has finished.

    Only consulted for liveness-tick payloads, so the Popoto query never runs
    on the relay's 100 ms poll loop for ordinary reaction traffic. Fail-open
    (returns False) on any error: dropping a tick is cosmetic, but wrongly
    dropping one on a transient Redis blip would freeze the counter.
    """
    try:
        from models.agent_session import AgentSession
        from models.session_lifecycle import TERMINAL_STATUSES

        for session in AgentSession.query.filter(session_id=session_id):
            return str(getattr(session, "status", "") or "").lower() in TERMINAL_STATUSES
        return False
    except Exception as e:  # noqa: BLE001
        logger.debug("Relay: terminal-status lookup failed for %s: %s", session_id, e)
        return False


def _reaction_yields_slot(message: dict) -> bool:
    """Decide whether this reaction must be dropped rather than delivered (#2716).

    Telegram permits one reaction per sender per message, and seven writers
    across two processes target a session's originating message. Ordering is
    undefined: `output_handler.react()` writes ``telegram:outbox:{chat_id}``
    while ticks, budget, and completion reactions write
    ``telegram:outbox:{session_id}``, and `process_outbox` iterates those keys
    in unspecified order. There is no single queue whose FIFO order could be
    relied on, so precedence is enforced here, at the one point all outbox
    traffic converges.

    Two rules, both anchored on ``heartbeat:slot_owner:{chat_id}:{message_id}``:

    1. **Terminal is final.** Once a rank-1 reaction lands, nothing lower may
       overwrite it. This also closes a latent bug that predates the counter:
       nothing stopped `tool_budget`'s 🤯 landing after a session's terminal
       reaction.
    2. **The tick yields to everything.** A rank-5 liveness tick is dropped
       whenever any higher-ranked writer owns the slot, and additionally
       whenever its session has already reached a terminal status.

    Everything else is left alone. The plan's broader phrasing ("drop a
    lower-priority reaction whenever a higher one owns the slot") is
    deliberately NOT implemented: several legitimate progressions are a later,
    lower-ranked reaction overwriting a higher one — the rank-3 RTR suppress
    after a rank-2 🤯, and the rank-4 child-completion suppress after a rank-3
    ✍. Enforcing rank monotonically would silently drop both. (The ⚠ → ✍
    pickup progression is often cited here but is not actually an example: ⚠ is
    set in-process by ``react_if_worker_down`` and never reaches this drain, so
    it records no slot owner at all.)

    Returns:
        True when the caller must drop this payload.
    """
    try:
        from agent.reaction_priority import DEFAULT_PRIORITY, PRIORITY_HEARTBEAT, PRIORITY_TERMINAL
        from bridge.liveness_ticks import read_slot_owner

        priority = int(message.get("priority") or DEFAULT_PRIORITY)
        is_tick = message.get("heartbeat_tick") is not None
        chat_id = message.get("chat_id")
        reply_to = message.get("reply_to")
        if chat_id is None or reply_to is None:
            return False

        if is_tick:
            session_id = message.get("session_id")
            if session_id and _session_reached_terminal_status(session_id):
                logger.debug("Relay: dropping liveness tick for terminal session %s", session_id)
                return True

        owner = read_slot_owner(chat_id, reply_to)
        if owner is None or priority <= owner:
            return False

        if owner == PRIORITY_TERMINAL or priority == PRIORITY_HEARTBEAT:
            logger.debug(
                "Relay: dropping reaction (priority=%d) — slot %s/%s owned by rank %d",
                priority,
                chat_id,
                reply_to,
                owner,
            )
            return True
        return False
    except Exception as e:  # noqa: BLE001 — never crash the drain on precedence
        logger.debug("Relay: reaction precedence check failed (delivering): %s", e)
        return False


def _reanchor_liveness_counter(message: dict, msg_id) -> None:
    """Re-anchor a session's liveness counter to a message it just published (#2716).

    This is the named signal the ceiling depends on: the forced-progress
    marker is released only when the new anchor message actually exists, and
    the relay's own record of sent message ids is the truth source for that.
    Re-anchoring on ANY message the session publishes, not just a
    ceiling-forced one, is intended — the human has been answered either way.

    ``DELIVERED_NO_ID`` (a send that reached Telegram but returned no message
    id) is the hole this closes: there is no message to anchor to, so the
    counter stops instead. Left unhandled, the ceiling marker would stay
    latched and the counter would freeze at the ceiling digit despite the
    human having been answered.
    """
    try:
        from bridge.liveness_ticks import reanchor, stop_counter

        session_id = message.get("session_id")
        if not session_id:
            return
        if msg_id is None:
            stop_counter(session_id)
        else:
            reanchor(session_id, int(msg_id))
    except Exception as e:  # noqa: BLE001
        logger.debug("Relay: liveness re-anchor failed (non-fatal): %s", e)


def _record_reaction_slot_owner(message: dict) -> None:
    """Record the rank that now owns this message's reaction slot."""
    try:
        from agent.reaction_priority import DEFAULT_PRIORITY
        from bridge.liveness_ticks import record_slot_owner

        chat_id = message.get("chat_id")
        reply_to = message.get("reply_to")
        if chat_id is None or reply_to is None:
            return
        # Only a writer whose rank we actually know may claim the slot. An
        # unranked glyph (an arbitrary agent-issued `react_with_emoji` call)
        # delivers, but recording it would pin the slot at the fallback's
        # terminal rank and suppress the budget, pickup, and tick writers behind
        # it for the key's full TTL -- the opposite of the documented intent
        # that such a reaction simply wins until the next tick overwrites it.
        if not message.get("priority_ranked", True):
            return
        record_slot_owner(chat_id, reply_to, int(message.get("priority") or DEFAULT_PRIORITY))
    except Exception as e:  # noqa: BLE001
        logger.debug("Relay: slot-owner record failed (non-fatal): %s", e)


def _record_sent_reaction(message: dict) -> None:
    """Durably record a sent reaction in the existing message log (#2494).

    Spike-2 found sent reactions had no durable record anywhere
    (``_send_queued_reaction`` returned a bool and wrote nothing). Owner
    ruling: no new subsystem — the reaction is a reply-to message in the
    existing log with escaped, parseable content
    (``<reaction>👀</reaction>``), written at the send-success site like
    every other outbound record. Best-effort: never raises into the relay.
    """
    try:
        from bridge.utc import utc_now
        from tools.telegram_history import store_message

        chat_id = message.get("chat_id")
        reply_to = message.get("reply_to")
        emoji = message.get("emoji")
        if not chat_id or not reply_to or not emoji:
            return
        store_message(
            chat_id=str(chat_id),
            content=f"<reaction>{emoji}</reaction>",
            sender="system",
            timestamp=utc_now(),
            message_type="reaction",
            reply_to_msg_id=int(reply_to),
            direction="out",
        )
    except Exception as e:  # noqa: BLE001 — record is best-effort bookkeeping
        logger.debug("Relay reaction record failed (non-fatal): %s", e)


def _bind_outbound_message_to_job(message: dict, msg_id) -> None:
    """Bind a sent message's id to the session's Job in the reply index (#2494).

    The permanent ``message_id → job_id`` index is written for OUTBOUND
    messages too, so a user reply to Valor's own message routes straight to
    the same Job with no model call. Resolution is session → bound Job via
    the trigger message's binding (``job_for_session``). Best-effort:
    a miss (no session, no Job bound yet) is a silent no-op.
    """
    try:
        if msg_id is None:
            return
        session_id = message.get("session_id")
        chat_id = message.get("chat_id")
        if not session_id or not chat_id:
            return
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            return
        from bridge.job_router import (
            bind_message_to_job,
            job_for_session,
            telegram_message_key,
        )

        job = job_for_session(sessions[0])
        if job is None:
            return
        bind_message_to_job(telegram_message_key(chat_id, msg_id), job.job_id, room_id=job.room_id)
    except Exception as e:  # noqa: BLE001 — binding is best-effort bookkeeping
        logger.debug("Relay outbound job binding failed (non-fatal): %s", e)


async def _send_custom_emoji_message(
    telegram_client,
    message: dict,
) -> int | None:
    """Send a standalone custom emoji message via Telethon.

    Uses ``MessageEntityCustomEmoji`` to render the emoji as a custom
    sticker in the message. Falls back to sending the emoji character
    as plain text if the custom emoji send fails.

    Args:
        telegram_client: The Telethon TelegramClient instance.
        message: Parsed message dict with chat_id, reply_to, emoji,
            and optional custom_emoji_document_id.

    Returns:
        The Telegram message ID on success, None on failure.
    """
    chat_id = message.get("chat_id")
    reply_to = message.get("reply_to")
    emoji_char = message.get("emoji", "")
    custom_doc_id = message.get("custom_emoji_document_id")

    if not chat_id or not emoji_char:
        logger.warning(f"Relay: skipping malformed custom emoji message: {message}")
        return None

    if not _deliverable_peer(chat_id, "custom emoji message", message):
        return None

    reply_to_id = int(reply_to) if reply_to else None

    # Try sending with custom emoji entity
    if custom_doc_id is not None:
        try:
            from telethon.tl.types import MessageEntityCustomEmoji

            # Custom emoji entity replaces the placeholder text
            placeholder = emoji_char
            entity = MessageEntityCustomEmoji(
                offset=0,
                length=len(placeholder),
                document_id=int(custom_doc_id),
            )
            sent = await telegram_client.send_message(
                int(chat_id),
                placeholder,
                reply_to=reply_to_id,
                formatting_entities=[entity],
            )
            msg_id = getattr(sent, "id", None)
            logger.info(
                f"Relay: sent custom emoji message (doc_id={custom_doc_id}) "
                f"to chat {chat_id} (msg_id={msg_id})"
            )
            return msg_id
        except Exception as e:
            logger.warning(
                f"Relay: custom emoji message failed (doc_id={custom_doc_id}), "
                f"falling back to plain text: {e}"
            )

    # Fallback: send emoji character as plain text
    try:
        sent = await telegram_client.send_message(
            int(chat_id),
            emoji_char,
            reply_to=reply_to_id,
        )
        msg_id = getattr(sent, "id", None)
        logger.info(
            f"Relay: sent emoji message (plain text fallback) to chat {chat_id} (msg_id={msg_id})"
        )
        return msg_id
    except FloodWaitError:
        raise
    except Exception as e:
        logger.error(f"Relay: emoji message send failed entirely: {e}")
        return None


async def _maybe_send_oversized_text_as_file(
    telegram_client,
    chat_id,
    text: str,
    reply_to,
    session_id: str,
) -> "int | None":
    """Convert oversized text (>4096 chars) to a .txt attachment and send it.

    Serves both the file+text path and the text-only path in _send_queued_message,
    providing a single oversized-text detection + conversion point for both branches.
    Deliberately does NOT perform the terminal send for normal-length text — callers
    must handle the fall-through case themselves.

    Returns the Telegram message ID of the uploaded .txt file, or None if the text
    is within the 4096-char limit (no action taken) or if the conversion/send fails
    (fall-through to caller's normal send path).

    Analogue of the text-only inline block at _send_queued_message lines 388-438
    (#1749 defect 2).
    """
    if not text or len(text) <= 4096:
        return None

    preview = text[:200].replace("\n", " ")
    logger.error(
        "Relay: oversized text reached relay (len=%d > 4096) — "
        "converting to .txt attachment. session_id=%s chat_id=%s preview=%r",
        len(text),
        session_id,
        chat_id,
        preview,
    )
    try:
        import tempfile
        import time as _time

        ts = int(_time.time())
        safe_sid = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(session_id))
        fd, overflow_path = tempfile.mkstemp(
            suffix=".txt",
            prefix=f"relay_overlong_{safe_sid}_{ts}_",
        )
        with os.fdopen(fd, "w") as fh:
            fh.write(text)

        caption = "[auto-attached: response exceeded 4096 chars]"
        sent = await telegram_client.send_file(
            int(chat_id),
            overflow_path,
            caption=caption,
            reply_to=reply_to,
        )
        if isinstance(sent, list):
            msg_id = getattr(sent[0], "id", None) if sent else None
        else:
            msg_id = getattr(sent, "id", None)
        logger.info(
            "Relay: sent oversized text as .txt attachment to chat %s "
            "(file=%s, orig_len=%d, msg_id=%s)",
            chat_id,
            overflow_path,
            len(text),
            msg_id,
        )
        return msg_id
    except Exception as overflow_err:
        # Fall through to caller's normal send path; Telethon will raise
        # MessageTooLongError which is handled by the existing retry + dead-letter path.
        logger.error(
            "Relay: length-guard .txt conversion failed (%s); falling back to normal send",
            overflow_err,
        )
        return None


async def _send_queued_message(
    telegram_client,
    message: dict,
) -> "int | None | _DeliveredNoId":
    """Send a single queued message via Telethon.

    Supports single files, multi-file albums (via ``file_paths`` list),
    and backward-compatible ``file_path`` (string) payloads.

    Args:
        telegram_client: The Telethon TelegramClient instance.
        message: Parsed message dict with chat_id, reply_to, text,
            optional file_paths (list) or file_path (string), and session_id.

    Returns:
        The Telegram message ID when the send delivered and an id was captured;
        ``DELIVERED_NO_ID`` when the send delivered but Telethon returned no id
        (so callers can dedup/record without re-queuing, #2179); ``None`` when
        the message was dropped (malformed) or the send failed with an
        exception. For albums, returns the ID of the first message in the album.

    Note: this function may mutate ``message`` in-place with ``_file_sent`` and
    ``_file_msg_id`` idempotency keys after a successful file send, and with
    ``_flood_waits`` after a FloodWait event. These keys are preserved when the
    message is re-queued so subsequent attempts can skip the already-sent file
    and avoid re-counting flood events (#1749 defects 1 and 4).
    """
    chat_id = message.get("chat_id")
    reply_to = message.get("reply_to")
    text = message.get("text", "")
    session_id = message.get("session_id") or "unknown"

    # Normalize file_path (string, legacy) and file_paths (list, current)
    file_paths = message.get("file_paths")
    legacy_file_path = message.get("file_path")
    if file_paths is None and legacy_file_path:
        file_paths = [legacy_file_path]

    if not chat_id:
        logger.warning(f"Relay: skipping malformed message (no chat_id): {message}")
        return None

    if not _deliverable_peer(chat_id, "message", message):
        return None

    # Must have either text or files
    if not text and not file_paths:
        logger.warning(f"Relay: skipping malformed message (no text or files): {message}")
        return None

    try:
        reply_to_id = int(reply_to) if reply_to else None

        # File send path
        if file_paths:
            # Filter to files that exist at send time
            available = [fp for fp in file_paths if os.path.isfile(fp)]
            missing = [fp for fp in file_paths if not os.path.isfile(fp)]

            if missing:
                for fp in missing:
                    logger.warning(f"Relay: file not found at send time: {fp}")

            if available:
                # Voice-note branch: deliver as a native Telegram voice bubble.
                # Requires payload `voice_note: True` and (optionally) `duration`.
                # Falls back to the standard document-send path on attribute
                # construction failure -- never crashes the relay.
                if message.get("voice_note") and len(available) == 1:
                    voice_path = available[0]
                    duration = message.get("duration")
                    try:
                        from telethon.tl.types import DocumentAttributeAudio

                        attr_duration = int(duration) if duration else 0
                        attrs = [
                            DocumentAttributeAudio(
                                duration=attr_duration,
                                voice=True,
                                waveform=b"",
                            )
                        ]
                        sent = await telegram_client.send_file(
                            int(chat_id),
                            voice_path,
                            caption=text or None,
                            reply_to=reply_to_id,
                            voice_note=True,
                            attributes=attrs,
                        )
                        msg_id = (
                            getattr(sent[0], "id", None)
                            if isinstance(sent, list) and sent
                            else getattr(sent, "id", None)
                        )
                        logger.info(
                            "Relay: sent voice note to chat %s (file=%s, duration=%ds, msg_id=%s)",
                            chat_id,
                            os.path.basename(voice_path),
                            attr_duration,
                            msg_id,
                        )
                        if message.get("cleanup_file"):
                            _safe_unlink(voice_path)
                        return _delivered(msg_id)
                    except FloodWaitError:
                        raise
                    except Exception as voice_err:
                        logger.warning(
                            "Relay: voice-note send failed (%s); "
                            "falling back to document send for chat %s",
                            voice_err,
                            chat_id,
                        )
                        # Fall through to standard send path below.

                # Single file or album (default path).
                # Send file without caption, then text as a separate message so
                # Telegram's narrow caption column doesn't constrain the text layout.
                # Idempotency guard: if a prior attempt already sent the file but crashed
                # before returning, skip the send and reuse the recorded msg_id (#1749
                # defect 1; analogue of the #1205 text-dedup guard).
                if message.get("_file_sent"):
                    msg_id = message.get("_file_msg_id")
                    logger.info(
                        "Relay: skipping already-sent file(s) for chat %s (idempotency guard, "
                        "msg_id=%s) (#1749 defect 1)",
                        chat_id,
                        msg_id,
                    )
                else:
                    file_arg = available[0] if len(available) == 1 else available
                    sent = await telegram_client.send_file(
                        int(chat_id),
                        file_arg,
                        reply_to=reply_to_id,
                    )
                    # Telethon returns a list for albums, single Message for one file
                    if isinstance(sent, list):
                        msg_id = getattr(sent[0], "id", None) if sent else None
                    else:
                        msg_id = getattr(sent, "id", None)
                    file_names = [os.path.basename(fp) for fp in available]
                    logger.info(
                        f"Relay: sent PM file(s) to chat {chat_id} "
                        f"(files={file_names}, msg_id={msg_id})"
                    )
                    # Record successful file send for idempotency on retry (#1749 defect 1)
                    message["_file_sent"] = True
                    message["_file_msg_id"] = msg_id

                if message.get("cleanup_file"):
                    for fp in available:
                        _safe_unlink(fp)

                if text:
                    # Oversized follow-up text guard: reuses the same helper as the
                    # text-only path so both branches share one conversion code path
                    # (#1749 defect 2).
                    attach_id = await _maybe_send_oversized_text_as_file(
                        telegram_client, chat_id, text, reply_to_id, session_id
                    )
                    if attach_id is not None:
                        # Oversized text shipped as .txt attachment; skip raw send_message.
                        return attach_id
                    await telegram_client.send_message(
                        int(chat_id),
                        text,
                        reply_to=reply_to_id,
                    )
                    logger.info(f"Relay: sent follow-up text to chat {chat_id} ({len(text)} chars)")
                return _delivered(msg_id)
            else:
                # All files missing -- fall back to text-only
                logger.warning(
                    "Relay: all files missing at send time. Falling back to text-only send."
                )
                if not text:
                    logger.warning(
                        f"Relay: all files missing and no text "
                        f"-- skipping message to chat {chat_id}"
                    )
                    return None

        # Belt-and-suspenders length guard: Telegram rejects text messages >4096 chars
        # with MessageTooLongError. Primary fix lives in the drafter (bridge/message_drafter.py),
        # but if any caller bypasses the drafter and writes >4096 chars to the outbox, we
        # convert to a .txt file attachment rather than split or drop. NEVER split messages
        # (see docs/plans/message-drafter.md No-Gos: "No message splitting. Ever.").
        attach_id = await _maybe_send_oversized_text_as_file(
            telegram_client, chat_id, text, reply_to_id, session_id
        )
        if attach_id is not None:
            return attach_id

        # Text-only send path
        from bridge.markdown import send_markdown

        sent = await send_markdown(
            telegram_client,
            int(chat_id),
            text,
            reply_to=reply_to_id,
        )
        msg_id = getattr(sent, "id", None)
        logger.info(
            f"Relay: sent PM message to chat {chat_id} "
            f"(reply_to={reply_to}, {len(text)} chars, msg_id={msg_id})"
        )
        return _delivered(msg_id)
    except FloodWaitError:
        raise
    except Exception as e:
        logger.error(f"Relay: failed to send message to chat {chat_id}: {e}", exc_info=True)
        return None


def _record_sent_message(session_id: str, msg_id: int) -> None:
    """Record a sent message ID on the AgentSession.

    Non-fatal: logs a warning if the session is not found or save fails.

    Args:
        session_id: The bridge/Telegram session ID.
        msg_id: The Telegram message ID returned by Telethon.
    """
    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if sessions:
            # Use the newest session record
            sessions.sort(key=lambda s: s.created_at or 0, reverse=True)
            sessions[0].record_pm_message(msg_id)
            logger.debug(f"Relay: recorded msg_id={msg_id} on session {session_id}")
        else:
            logger.warning(f"Relay: session {session_id} not found for recording msg_id={msg_id}")
    except Exception as e:
        logger.warning(f"Relay: failed to record msg_id on session {session_id}: {e}")


def _record_relay_sent_draft(session_id: str, text: str) -> None:
    """Append a PM self-send to ``AgentSession.recent_sent_drafts``.

    The redundancy filter (``bridge/redundancy_filter.py``) compares the next
    drafter-bound send against this list to suppress near-duplicates. Without
    this hook, CLI self-send outputs (sender="system") were invisible to
    the filter, so the executor's follow-up send_cb (sender="Valor") shipped
    the same content unchanged.

    Empty/whitespace text is skipped. Non-fatal on every error path.
    """
    if not text or not text.strip():
        return
    try:
        from bridge.message_drafter import extract_artifacts
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            return
        sessions.sort(key=lambda s: s.created_at or 0, reverse=True)
        artifacts = extract_artifacts(text) or {}
        sessions[0].record_recent_sent_draft(text, artifacts)
        logger.debug(f"Relay: registered PM self-send in recent_sent_drafts for {session_id}")
    except Exception as e:
        logger.warning(f"Relay: failed to record recent_sent_draft for {session_id}: {e}")


def _append_outbound_chat_log(message: dict, msg_id: int | None) -> None:
    """Append an outbound entry to the owning AgentSession's chat_message_log.

    Three-tier owning-session resolution (issue #1192):
      1. payload["owner_agent_session_id"] — set by Path B (valor-telegram send)
         when the agent invokes it via Bash inside an agent session.
      2. payload["session_id"] without a "cli-" or "local-" prefix — real queue
         session_id created by TelegramRelayOutputHandler (Path A).
      3. Fallback: query AgentSession by chat_id+status="running" (covers manual CLI sends).
         Replicates the core logic of get_active_session_for_chat() synchronously,
         since this function runs in a thread (asyncio.to_thread) and cannot await.
    If no session is resolved, the append is skipped silently (debug log).

    NOTE: We append AFTER the successful send, so the drafter never sees the
    current turn's outbound text (it produces that text). The drafter sees prior
    turns and prior Path B sends. That is the desired behavior.

    Wrapped in try/except so relay failures never break the send path.
    """
    try:
        from models.agent_session import AgentSession

        text = (message.get("text") or "").strip()
        if not text:
            # File-only send: produce a "[file: filename]" placeholder so the
            # drafter sees mid-session file sends (plan §Technical Approach).
            file_paths = message.get("file_paths") or (
                [message["file_path"]] if message.get("file_path") else []
            )
            if not file_paths:
                return  # No content at all (e.g. voice note with no file_paths)
            import os

            text = "[file: " + ", ".join(os.path.basename(fp) for fp in file_paths) + "]"

        # Truncate to prevent verbose drafter output inflating Redis storage
        # (~200KB per session without this bound).
        if len(text) > MAX_CHAT_LOG_ENTRY_CHARS:
            text = text[:MAX_CHAT_LOG_ENTRY_CHARS] + "…"

        chat_id = message.get("chat_id")

        # Tier 1: owner_agent_session_id injected by Path B (valor-telegram send).
        # Use get_by_id() — it accepts the raw agent_session_id AutoKey string
        # directly and is far more efficient than query.filter(session_id=...) or
        # a full table scan via query.all(). query.filter(session_id=owner_id)
        # would always fail silently because session_id is the bridge session_id
        # field, not the Popoto AutoKey (agent_session_id). The full table scan
        # fallback is equally wrong — both were dead code paths.
        owner_id = message.get("owner_agent_session_id")
        session = None
        if owner_id:
            session = AgentSession.get_by_id(owner_id)
            if session is None:
                logger.warning(
                    "Relay: AGENT_SESSION_ID=%s set but not found in Redis; "
                    "chat_log entry skipped for Path B send",
                    owner_id,
                )

        # Tier 2: real queue session_id (no cli-/local- prefix) — Path A
        if session is None:
            queue_session_id = message.get("session_id") or ""
            if queue_session_id and not queue_session_id.startswith(("cli-", "local-")):
                rows = list(AgentSession.query.filter(session_id=queue_session_id))
                if rows:
                    session = rows[0]

        # Tier 3: fallback by chat_id for manual CLI sends.
        # NOTE: get_active_session_for_chat is async; we replicate its core query
        # synchronously here since _append_outbound_chat_log runs in a thread
        # (via asyncio.to_thread in the relay loop) and cannot await.
        if session is None and chat_id:
            try:
                candidates = list(AgentSession.query.filter(chat_id=chat_id, status="running"))
                if candidates:
                    candidates.sort(key=lambda s: s.created_at or 0, reverse=True)
                    session = candidates[0]
            except Exception:  # noqa: S110 -- miss handled by session-None branch
                pass

        if session is None:
            logger.debug(
                "Relay: no owning session resolved for chat_log append "
                "(chat_id=%s, owner_agent_session_id=%s, session_id=%s) — skipping",
                chat_id,
                message.get("owner_agent_session_id"),
                message.get("session_id"),
            )
            return

        session.append_chat_log("out", "valor", text, msg_id)
    except Exception as exc:
        logger.warning("Relay: _append_outbound_chat_log failed (non-fatal): %s", exc)


async def _dead_letter_message(message: dict, reason: str) -> None:
    """Route a failed message to the dead letter queue or discard it.

    Text messages are persisted via bridge/dead_letters.py for later replay.
    Reactions and custom emoji messages are ephemeral and not worth replaying,
    so they are logged at WARNING level and discarded.

    Args:
        message: The message payload that exhausted retries.
        reason: Human-readable reason for dead-lettering.
    """
    msg_type = message.get("type")
    chat_id = message.get("chat_id")

    # Honor cleanup_file flag at terminal failure: the producer trusted the
    # relay to manage temp-file lifecycle, so retry exhaustion is one of the
    # two completion points (the other is successful send).
    if message.get("cleanup_file"):
        for fp in message.get("file_paths") or []:
            _safe_unlink(fp)
        legacy = message.get("file_path")
        if legacy:
            _safe_unlink(legacy)

    if msg_type in ("reaction", "custom_emoji_message"):
        logger.warning(
            f"Relay: discarding {msg_type} after {reason} (chat_id={chat_id}): {message}"
        )
        return

    # Text/file messages -- persist to dead letter queue
    text = message.get("text", "")
    reply_to = message.get("reply_to")
    if chat_id and text:
        # Same parse as the send paths (one predicate, `utils.peer`), but not
        # `_deliverable_peer`: this is about discarding a stored record rather
        # than dropping a send, so the log text differs, and the zero case here
        # deliberately logs only the chat_id rather than the whole payload.
        chat_id_int = numeric_peer(chat_id)
        if chat_id_int is None:
            logger.debug(f"Relay: discarding dead letter for non-Telegram chat_id '{chat_id}'")
            return

        # chat_id=0 is not a valid Telegram peer — don't persist, it loops forever.
        # Narrowed from <= 0: group/supergroup IDs are legitimately negative;
        # narrowing only this guard without dead_letters.py:57 is a no-op (#1749 defect 3).
        if chat_id_int == 0:
            logger.warning(
                f"Relay: discarding dead letter for chat_id=0 (not a valid Telegram peer): "
                f"{chat_id!r}"
            )
            return

        try:
            from bridge.dead_letters import persist_failed_delivery

            await persist_failed_delivery(
                chat_id=chat_id_int,
                reply_to=int(reply_to) if reply_to else None,
                text=text,
            )
            logger.warning(
                f"Relay: dead-lettered message for chat {chat_id} ({reason}, {len(text)} chars)"
            )
        except Exception as e:
            logger.error(f"Relay: failed to persist dead letter for chat {chat_id}: {e}")
    else:
        logger.warning(
            f"Relay: discarding non-text message after {reason} (chat_id={chat_id}): {message}"
        )


async def process_outbox(telegram_client) -> int:
    """Process all pending outbox queues, sending messages via Telethon.

    Scans for telegram:outbox:* keys in Redis, processes up to RELAY_BATCH_SIZE
    messages per call, and records sent message IDs on AgentSession.

    Args:
        telegram_client: The Telethon TelegramClient instance.

    Returns:
        Number of messages successfully sent in this cycle.
    """
    sent_count = 0

    try:
        r = await asyncio.to_thread(_get_redis_connection)
        keys = await asyncio.to_thread(r.keys, OUTBOX_KEY_PATTERN)

        for key in keys:
            processed = 0
            while processed < RELAY_BATCH_SIZE:
                # LPOP is atomic -- safe even with hypothetical concurrent consumers
                raw = await asyncio.to_thread(r.lpop, key)
                if not raw:
                    break

                processed += 1

                try:
                    message = json.loads(raw)
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"Relay: skipping malformed queue entry in {key}: {e}")
                    continue

                # Validate message type before dispatch
                msg_type = message.get("type")
                if msg_type not in KNOWN_MESSAGE_TYPES:
                    logger.warning(
                        f"Relay: unknown message type '{msg_type}', discarding: {message}"
                    )
                    continue

                # Dispatch to handler with unified error handling
                success = False
                msg_id = None
                session_id = message.get("session_id")
                try:
                    if msg_type == "reaction":
                        if await asyncio.to_thread(_reaction_yields_slot, message):
                            continue
                        success = await _send_queued_reaction(telegram_client, message)
                        if success:
                            # Durable reaction record (#2494): reply-to entry
                            # in the existing message log, at send success.
                            await asyncio.to_thread(_record_sent_reaction, message)
                            await asyncio.to_thread(_record_reaction_slot_owner, message)
                    elif msg_type == "custom_emoji_message":
                        msg_id = await _send_custom_emoji_message(telegram_client, message)
                        success = msg_id is not None
                    else:
                        send_result = await _send_queued_message(telegram_client, message)
                        # A send that reached Telegram but returned no message id
                        # (DELIVERED_NO_ID) is still a success: it must record the
                        # dedup draft and must NOT be re-queued. Only a plain None
                        # (drop/failure) is treated as failure (#2179).
                        if send_result is DELIVERED_NO_ID:
                            success = True
                            msg_id = None
                        else:
                            msg_id = send_result
                            success = msg_id is not None
                except FloodWaitError as flood_err:
                    # Borrows only blocking-sleep shape from telegram_bridge.py connect-loop
                    # handler; NOT a connect-path handler; intentionally omits
                    # _write_flood_backoff side-effect (#1749 defect 4).
                    wait_secs = min(
                        flood_err.seconds + RELAY_FLOOD_WAIT_BUFFER_SECS,
                        RELAY_FLOOD_WAIT_MAX_SLEEP_SECS,
                    )
                    logger.warning(
                        f"FloodWaitError: Telegram requests {flood_err.seconds}s backoff, "
                        f"sleeping {wait_secs}s (send-path handler, #1749 defect 4)"
                    )
                    await asyncio.sleep(wait_secs)
                    flood_waits = message.get("_flood_waits", 0) + 1
                    message["_flood_waits"] = flood_waits
                    if flood_waits >= RELAY_FLOOD_WAIT_MAX:
                        logger.error(
                            f"FloodWait backstop reached ({flood_waits} floods), "
                            f"dead-lettering message"
                        )
                        await _dead_letter_message(message, reason="flood_backstop")
                        # Do not fall through to generic retry; backstopped msgs are dead-lettered
                        continue
                    else:
                        # Re-queue without burning _relay_attempts; carries _file_sent if set
                        await asyncio.to_thread(r.rpush, key, json.dumps(message))
                        continue
                    success = False
                except Exception as handler_err:
                    logger.warning(
                        f"Relay: handler exception for {msg_type or 'default'} "
                        f"in {key}: {handler_err}"
                    )
                    success = False

                if success:
                    sent_count += 1
                    if msg_type != "reaction":
                        await asyncio.to_thread(_reanchor_liveness_counter, message, msg_id)
                    # Record sent message ID on AgentSession
                    if msg_id is not None:
                        session_id = message.get("session_id")
                        if session_id:
                            await asyncio.to_thread(_record_sent_message, session_id, msg_id)
                            # Opt-in producer-readable ack (issue #2717). Gated on
                            # the payload flag: an unconditional write would add
                            # two Redis ops to every outbound message system-wide
                            # for one low-traffic consumer. Non-fatal — the relay
                            # must never crash on ack bookkeeping.
                            if message.get("ack_sent_id"):
                                try:
                                    from bridge.outbox_ack import publish_sent_message_id

                                    await asyncio.to_thread(
                                        publish_sent_message_id, session_id, msg_id
                                    )
                                except Exception as ack_err:  # noqa: BLE001
                                    logger.warning(
                                        f"Relay: failed to publish sent-id ack for "
                                        f"session {session_id}: {ack_err}"
                                    )
                    # Append outbound entry to owning session's chat_message_log (issue #1192).
                    # Three-tier resolution: owner_agent_session_id → real session_id → chat lookup.
                    # Non-fatal — relay must never crash on chat-log bookkeeping.
                    await asyncio.to_thread(_append_outbound_chat_log, message, msg_id)

                    # Permanent reply index for OUTBOUND messages (#2494): a
                    # user reply to this sent message routes to the same Job
                    # with no model call. Best-effort, non-fatal.
                    if msg_id is not None:
                        await asyncio.to_thread(_bind_outbound_message_to_job, message, msg_id)

                    # Register PM self-send in recent_sent_drafts so the executor's
                    # follow-up send_cb is dedup'd by bridge.redundancy_filter. Without
                    # this, the relay's "system" send and the executor's "Valor" send
                    # both ship the same content (#1205-style duplicate).
                    if msg_type is None:
                        session_id_for_dedup = message.get("session_id")
                        if session_id_for_dedup:
                            await asyncio.to_thread(
                                _record_relay_sent_draft,
                                session_id_for_dedup,
                                message.get("text") or "",
                            )

                    # Store sent message for Redis history (text messages only)
                    if msg_type is None and msg_id is not None:
                        try:
                            from bridge.telegram_bridge import store_message
                            from bridge.utc import utc_now

                            await asyncio.to_thread(
                                store_message,
                                chat_id=message.get("chat_id"),
                                content=message.get("text", ""),
                                sender="system",
                                timestamp=utc_now(),
                                message_type="pm_direct",
                                direction="out",
                            )
                        except Exception as e:
                            # Non-fatal: history storage is best-effort.
                            logger.debug("Relay history store_message failed: %s", e)
                else:
                    # Bounded retry: increment attempt counter, dead-letter if exhausted
                    attempts = message.get("_relay_attempts", 0) + 1
                    message["_relay_attempts"] = attempts
                    if attempts >= MAX_RELAY_RETRIES:
                        await _dead_letter_message(
                            message, reason=f"max retries ({MAX_RELAY_RETRIES}) exceeded"
                        )
                    else:
                        try:
                            requeue_raw = json.dumps(message)
                            await asyncio.to_thread(r.rpush, key, requeue_raw)
                            logger.info(
                                f"Relay: re-queued failed message in {key} "
                                f"(attempt {attempts}/{MAX_RELAY_RETRIES})"
                            )
                        except Exception as re_err:
                            logger.error(f"Relay: failed to re-queue message: {re_err}")

    except Exception as e:
        logger.error(f"Relay: outbox processing error: {e}", exc_info=True)

    return sent_count


async def relay_loop(telegram_client) -> None:
    """Main relay loop: continuously process PM outbox queues.

    Runs as an asyncio task in the bridge's event loop. Polls Redis
    for outbox messages and sends them via Telethon.

    Args:
        telegram_client: The Telethon TelegramClient instance.
    """
    logger.info("Telegram relay started -- processing PM outbox queues")

    while True:
        try:
            sent = await process_outbox(telegram_client)
            if sent > 0:
                logger.info(f"Relay: processed {sent} message(s)")
        except Exception as e:
            logger.error(f"Relay loop error: {e}", exc_info=True)

        await asyncio.sleep(RELAY_POLL_INTERVAL)


def get_outbox_length(session_id: str) -> int:
    """Check the number of pending messages in a session's outbox queue.

    Used by the PM self-message bypass to wait for the relay to drain
    before checking pm_sent_message_ids.

    Args:
        session_id: The session ID to check.

    Returns:
        Number of pending messages, or 0 on error.
    """
    try:
        r = _get_redis_connection()
        key = f"telegram:outbox:{session_id}"
        return r.llen(key)
    except Exception:
        return 0
