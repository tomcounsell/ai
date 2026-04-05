"""Telegram message relay: processes the PM outbox queue.

Async task that runs in the bridge's event loop alongside the session queue
consumer. Polls Redis for PM-authored messages queued by
tools/send_telegram.py and sends them via Telethon.

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
pm_sent_message_ids field. This list is checked by the summarizer bypass
in bridge/response.py.
"""

import asyncio
import json
import logging

import redis

logger = logging.getLogger(__name__)

# Poll interval for checking outbox queues (100ms for low latency)
RELAY_POLL_INTERVAL = 0.1

# Maximum messages to process per poll cycle (prevents starvation)
RELAY_BATCH_SIZE = 10

# Redis key pattern for scanning outbox queues
OUTBOX_KEY_PATTERN = "telegram:outbox:*"

# Maximum relay attempts before routing to dead letter
MAX_RELAY_RETRIES = 3

# Known message types accepted by the relay dispatcher
KNOWN_MESSAGE_TYPES = {None, "reaction", "custom_emoji_message"}


def _get_redis_connection() -> redis.Redis:
    """Get a synchronous Redis connection for queue operations."""
    import os

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(redis_url, decode_responses=True)


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
    except Exception as e:
        logger.warning(f"Relay: reaction send failed: {e}")
        return False


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
    except Exception as e:
        logger.error(f"Relay: emoji message send failed entirely: {e}")
        return None


async def _send_queued_message(
    telegram_client,
    message: dict,
) -> int | None:
    """Send a single queued message via Telethon.

    Supports single files, multi-file albums (via ``file_paths`` list),
    and backward-compatible ``file_path`` (string) payloads.

    Args:
        telegram_client: The Telethon TelegramClient instance.
        message: Parsed message dict with chat_id, reply_to, text,
            optional file_paths (list) or file_path (string), and session_id.

    Returns:
        The Telegram message ID on success, None on failure.
        For albums, returns the ID of the first message in the album.
    """
    import os

    chat_id = message.get("chat_id")
    reply_to = message.get("reply_to")
    text = message.get("text", "")

    # Normalize file_path (string, legacy) and file_paths (list, current)
    file_paths = message.get("file_paths")
    legacy_file_path = message.get("file_path")
    if file_paths is None and legacy_file_path:
        file_paths = [legacy_file_path]

    if not chat_id:
        logger.warning(f"Relay: skipping malformed message (no chat_id): {message}")
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
                # Single file or album
                file_arg = available[0] if len(available) == 1 else available
                sent = await telegram_client.send_file(
                    int(chat_id),
                    file_arg,
                    caption=text or None,
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
                    f"(files={file_names}, "
                    f"caption={len(text)} chars, msg_id={msg_id})"
                )
                return msg_id
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
        return msg_id
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

    if msg_type in ("reaction", "custom_emoji_message"):
        logger.warning(
            f"Relay: discarding {msg_type} after {reason} (chat_id={chat_id}): {message}"
        )
        return

    # Text/file messages -- persist to dead letter queue
    text = message.get("text", "")
    reply_to = message.get("reply_to")
    if chat_id and text:
        try:
            from bridge.dead_letters import persist_failed_delivery

            await persist_failed_delivery(
                chat_id=int(chat_id),
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
                try:
                    if msg_type == "reaction":
                        success = await _send_queued_reaction(telegram_client, message)
                    elif msg_type == "custom_emoji_message":
                        msg_id = await _send_custom_emoji_message(telegram_client, message)
                        success = msg_id is not None
                    else:
                        msg_id = await _send_queued_message(telegram_client, message)
                        success = msg_id is not None
                except Exception as handler_err:
                    logger.warning(
                        f"Relay: handler exception for {msg_type or 'default'} "
                        f"in {key}: {handler_err}"
                    )
                    success = False

                if success:
                    sent_count += 1
                    # Record sent message ID on AgentSession
                    if msg_id is not None:
                        session_id = message.get("session_id")
                        if session_id:
                            await asyncio.to_thread(_record_sent_message, session_id, msg_id)

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
                            )
                        except Exception:
                            pass  # Non-fatal: history storage is best-effort
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

    Used by the summarizer bypass to wait for the relay to drain
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
