"""
Output handler protocol and implementations for agent session output routing.

Defines the OutputHandler protocol that all output destinations must implement,
plus built-in implementations for file logging and stderr logging. The bridge
registers its Telegram-specific handler; standalone workers use
TelegramRelayOutputHandler (with FileOutputHandler dual-write).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Default directory for worker output logs
WORKER_LOGS_DIR = Path(__file__).parent.parent / "logs" / "worker"


@runtime_checkable
class OutputHandler(Protocol):
    """Protocol for routing agent session output to a destination.

    Implementations must provide send() for text output and react() for
    emoji reactions. Both methods are async to support I/O-bound destinations
    (Telegram, email, etc.).
    """

    async def send(
        self,
        chat_id: str,
        text: str,
        reply_to_msg_id: int,
        session: Any = None,
    ) -> None:
        """Send text output to the destination.

        Args:
            chat_id: Target chat/channel identifier.
            text: Message text to send.
            reply_to_msg_id: Original message ID to reply to.
            session: Optional session context object.
        """
        ...

    async def react(
        self,
        chat_id: str,
        msg_id: int,
        emoji: str | None = None,
    ) -> None:
        """Set a reaction emoji on a message.

        Args:
            chat_id: Target chat/channel identifier.
            msg_id: Message ID to react to.
            emoji: Emoji string to set, or None to clear.
        """
        ...


class FileOutputHandler:
    """Write agent output to log files in logs/worker/.

    Each session gets its own log file at logs/worker/{session_id}.log.
    Output includes timestamps for human readability.
    """

    def __init__(self, log_dir: Path | None = None):
        self.log_dir = log_dir or WORKER_LOGS_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)

    async def send(
        self,
        chat_id: str,
        text: str,
        reply_to_msg_id: int,
        session: Any = None,
    ) -> None:
        """Append text output to the session's log file."""
        if not text:
            return

        session_id = getattr(session, "session_id", None) or chat_id
        log_path = self.log_dir / f"{session_id}.log"

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        entry = f"[{timestamp}] chat={chat_id} reply_to={reply_to_msg_id}\n{text}\n---\n"

        try:
            with open(log_path, "a") as f:
                f.write(entry)
            logger.info(f"Worker output written to {log_path.name} ({len(text)} chars)")
        except Exception as e:
            logger.error(f"Failed to write worker output to {log_path}: {e}")

    async def react(
        self,
        chat_id: str,
        msg_id: int,
        emoji: str | None = None,
    ) -> None:
        """Log the reaction (no-op for file output)."""
        session_id = chat_id  # Best effort
        log_path = self.log_dir / f"{session_id}.log"

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        entry = f"[{timestamp}] REACTION chat={chat_id} msg={msg_id} emoji={emoji}\n"

        try:
            with open(log_path, "a") as f:
                f.write(entry)
        except Exception:
            pass  # Reactions are best-effort for file output


class LoggingOutputHandler:
    """Simple stderr/logging fallback for output routing.

    Writes all output to the Python logger at INFO level. Useful for
    development and debugging when file output is not needed.
    """

    async def send(
        self,
        chat_id: str,
        text: str,
        reply_to_msg_id: int,
        session: Any = None,
    ) -> None:
        """Log text output via Python logging."""
        if not text:
            return

        session_id = getattr(session, "session_id", None) or "unknown"
        logger.info(
            f"[worker:{session_id}] Output ({len(text)} chars): "
            f"{text[:200]}{'...' if len(text) > 200 else ''}"
        )

    async def react(
        self,
        chat_id: str,
        msg_id: int,
        emoji: str | None = None,
    ) -> None:
        """Log the reaction via Python logging."""
        logger.info(f"[worker] Reaction: chat={chat_id} msg={msg_id} emoji={emoji}")


def _read_drafter_in_handler_flag() -> bool:
    """Read MESSAGE_DRAFTER_IN_HANDLER env var once, defaulting to true.

    Per plan docs/plans/message-drafter.md Race 3: the flag is a startup-config,
    not a runtime-config. Reading once at handler __init__ time keeps the flag
    sticky across the lifetime of the handler even if an operator flips it
    mid-session. Accepts "0", "false", "no" (case-insensitive) as disable.
    """
    raw = os.environ.get("MESSAGE_DRAFTER_IN_HANDLER", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


class TelegramRelayOutputHandler:
    """Route agent output to the Redis outbox for Telegram delivery.

    Writes JSON payloads to ``telegram:outbox:{session_id}`` using the same
    format as ``tools/send_telegram.py``.  The bridge relay
    (``bridge/telegram_relay.py``) polls these keys and delivers via Telethon.

    When ``MESSAGE_DRAFTER_IN_HANDLER`` is true (default), every ``send()``
    invocation routes through ``bridge.message_drafter.draft_message`` before
    the payload is written to Redis. This closes the worker-bypass gap where
    worker-executed PM sessions previously wrote raw text straight to the
    outbox, producing `MessageTooLongError` on content >4096 chars
    (see docs/plans/message-drafter.md §Problem, evidence §4–§7).

    An optional *file_handler* enables dual-write so output is also persisted
    to the local file log for debugging and audit. Redis errors are caught and
    logged -- they never propagate to the caller.
    """

    # TTL applied to each outbox key (seconds). Matches tools/send_telegram.py.
    OUTBOX_TTL = 3600

    def __init__(
        self,
        redis_url: str | None = None,
        file_handler: FileOutputHandler | None = None,
    ):
        self._redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self._file_handler = file_handler
        self._redis = None  # Lazy connection
        # Read the drafter flag ONCE at init (Race 3 in the plan). Flipping the
        # env var mid-session does not affect an already-constructed handler.
        self._drafter_enabled = _read_drafter_in_handler_flag()

    def _get_redis(self):
        """Return a Redis connection, creating one lazily on first use."""
        if self._redis is None:
            import redis

            self._redis = redis.Redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    async def send(
        self,
        chat_id: str,
        text: str,
        reply_to_msg_id: int,
        session: Any = None,
    ) -> None:
        """Write a message payload to the Redis outbox for Telegram delivery.

        Payload format matches ``tools/send_telegram.py:145-151``::

            {"chat_id", "reply_to", "text", "session_id", "timestamp"}

        Args:
            chat_id: Target Telegram chat identifier.
            text: Message text to send.
            reply_to_msg_id: Original message ID to reply to (may be None).
            session: Optional AgentSession providing ``session_id``.
        """
        if not text:
            return

        session_id = getattr(session, "session_id", None) or chat_id
        reply_to = int(reply_to_msg_id) if reply_to_msg_id else None

        # Run the drafter in-process BEFORE writing to the outbox. This is the
        # critical fix from docs/plans/message-drafter.md §Part C — it closes
        # the worker-bypass gap where the worker's send_cb path wrote raw text
        # straight to Redis and bypassed all length/format compliance.
        delivery_text = text
        file_paths: list[str] | None = None
        if self._drafter_enabled:
            try:
                from bridge.message_drafter import draft_message

                draft = await draft_message(
                    text,
                    session=session,
                    medium="telegram",
                )
                # If the drafter produced a drafted result, use its text. When
                # was_drafted is False (short output or empty), keep the original
                # raw text (drafter returns it verbatim in that case).
                if draft.text:
                    delivery_text = draft.text
                if draft.full_output_file is not None:
                    file_paths = [str(draft.full_output_file)]
            except Exception as e:
                # Drafter failure MUST NOT block delivery. Fall back to raw text;
                # the relay length guard (bridge/telegram_relay.py) catches any
                # oversize payloads as a last line of defense.
                logger.warning(
                    "Drafter failed in TelegramRelayOutputHandler.send (%s); "
                    "falling back to raw text",
                    e,
                )

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "reply_to": reply_to,
            "text": delivery_text,
            "session_id": session_id,
            "timestamp": time.time(),
        }
        if file_paths:
            payload["file_paths"] = file_paths

        queue_key = f"telegram:outbox:{session_id}"
        try:
            r = self._get_redis()
            r.rpush(queue_key, json.dumps(payload))
            r.expire(queue_key, self.OUTBOX_TTL)
            logger.info(
                "Queued output to %s (%d chars, files=%d)",
                queue_key,
                len(delivery_text),
                len(file_paths or []),
            )
        except Exception as e:
            logger.error(f"Failed to write to Redis outbox {queue_key}: {e}")

        # Dual-write to file handler for audit/debugging
        if self._file_handler is not None:
            await self._file_handler.send(chat_id, text, reply_to_msg_id, session)

    async def react(
        self,
        chat_id: str,
        msg_id: int,
        emoji: str | None = None,
    ) -> None:
        """Write a reaction payload to the Redis outbox.

        Args:
            chat_id: Target Telegram chat identifier.
            msg_id: Message ID to react to.
            emoji: Emoji string to set, or None to clear.
        """
        # Derive a session_id -- best effort, use chat_id as fallback
        session_id = chat_id
        reply_to = int(msg_id) if msg_id else None

        # Normalize EmojiResult to str for JSON serialization
        emoji_str = str(emoji) if emoji is not None else None

        payload = {
            "type": "reaction",
            "chat_id": chat_id,
            "reply_to": reply_to,
            "emoji": emoji_str,
            "session_id": session_id,
            "timestamp": time.time(),
        }

        queue_key = f"telegram:outbox:{session_id}"
        try:
            r = self._get_redis()
            r.rpush(queue_key, json.dumps(payload))
            r.expire(queue_key, self.OUTBOX_TTL)
            logger.info(f"Queued reaction to {queue_key} (emoji={emoji})")
        except Exception as e:
            logger.error(f"Failed to write reaction to Redis outbox {queue_key}: {e}")

        # Dual-write to file handler
        if self._file_handler is not None:
            await self._file_handler.react(chat_id, msg_id, emoji)
