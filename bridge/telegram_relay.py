"""Telegram message relay: processes the PM outbox queue.

Async task that runs in the bridge's event loop alongside the job queue
consumer. Polls Redis for PM-authored messages queued by
tools/send_telegram.py and sends them via Telethon.

Redis queue contract:
    Key pattern: telegram:outbox:{session_id}
    Message format: JSON with {chat_id, reply_to, text, session_id, timestamp}
    TTL: 1 hour (set by the tool, safety net for crashed sessions)

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


def _get_redis_connection() -> redis.Redis:
    """Get a synchronous Redis connection for queue operations."""
    import os

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(redis_url, decode_responses=True)


async def _send_queued_message(
    telegram_client,
    message: dict,
) -> int | None:
    """Send a single queued message via Telethon.

    Args:
        telegram_client: The Telethon TelegramClient instance.
        message: Parsed message dict with chat_id, reply_to, text, session_id.

    Returns:
        The Telegram message ID on success, None on failure.
    """
    chat_id = message.get("chat_id")
    reply_to = message.get("reply_to")
    text = message.get("text", "")

    if not chat_id or not text:
        logger.warning(f"Relay: skipping malformed message (no chat_id or text): {message}")
        return None

    try:
        from bridge.markdown import send_markdown

        sent = await send_markdown(
            telegram_client,
            int(chat_id),
            text,
            reply_to=int(reply_to) if reply_to else None,
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

                msg_id = await _send_queued_message(telegram_client, message)

                if msg_id is not None:
                    sent_count += 1
                    session_id = message.get("session_id")
                    if session_id:
                        await asyncio.to_thread(_record_sent_message, session_id, msg_id)

                    # Store sent message for Redis history
                    try:
                        from datetime import datetime

                        from bridge.telegram_bridge import store_message

                        await asyncio.to_thread(
                            store_message,
                            chat_id=message.get("chat_id"),
                            content=message.get("text", ""),
                            sender="system",
                            timestamp=datetime.now(),
                            message_type="pm_direct",
                        )
                    except Exception:
                        pass  # Non-fatal: history storage is best-effort
                else:
                    # Send failed -- re-push to queue tail for retry
                    try:
                        await asyncio.to_thread(r.rpush, key, raw)
                        logger.info(f"Relay: re-queued failed message in {key} for retry")
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
