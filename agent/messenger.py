"""
Boss Messenger - Communication channel back to the supervisor.

This module provides a way for long-running agent work to send messages
back to the supervisor (currently via Telegram, but abstracted for future
platforms).

Usage:
    messenger = BossMessenger(send_callback)
    await messenger.send("Here's what I found...")
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class MessageRecord:
    """Record of a sent message for tracking."""

    content: str
    timestamp: datetime
    message_type: str = "result"  # "result", "acknowledgment", "error"


@dataclass
class BossMessenger:
    """
    Communication channel for sending messages to the supervisor.

    The agent uses this to send results when work is complete.
    The bridge provides the actual send implementation.
    """

    # Callback to actually send the message (provided by bridge)
    _send_callback: Callable[[str], Awaitable[None]]

    # Chat context (for logging/tracking)
    chat_id: str = ""
    session_id: str = ""

    # Track sent messages
    messages_sent: list[MessageRecord] = field(default_factory=list)

    # Flag to track if we've sent an acknowledgment
    acknowledgment_sent: bool = False

    async def send(self, message: str, message_type: str = "result") -> bool:
        """
        Send a message to the supervisor.

        Args:
            message: The message content to send
            message_type: Type of message ("result", "acknowledgment", "error")

        Returns:
            True if sent successfully, False otherwise
        """
        if not message or not message.strip():
            logger.debug("Skipping empty message")
            return False

        try:
            await self._send_callback(message)

            record = MessageRecord(
                content=message[:200],  # Truncate for record
                timestamp=datetime.now(),
                message_type=message_type,
            )
            self.messages_sent.append(record)

            logger.info(
                f"[{self.session_id}] Sent {message_type} message "
                f"({len(message)} chars) to chat {self.chat_id}"
            )
            return True

        except Exception as e:
            logger.error(f"[{self.session_id}] Failed to send message: {e}")
            return False

    async def send_acknowledgment(self, message: str = "I'm working on this.") -> bool:
        """
        Send a one-time acknowledgment that work is in progress.

        Only sends if no messages have been sent yet.

        Returns:
            True if sent, False if already acknowledged or error
        """
        if self.acknowledgment_sent or self.messages_sent:
            logger.debug(
                f"[{self.session_id}] Skipping acknowledgment - already communicated"
            )
            return False

        self.acknowledgment_sent = True
        return await self.send(message, message_type="acknowledgment")

    def has_communicated(self) -> bool:
        """Check if any message has been sent to the supervisor."""
        return len(self.messages_sent) > 0

    def get_last_message_time(self) -> datetime | None:
        """Get timestamp of the last sent message."""
        if self.messages_sent:
            return self.messages_sent[-1].timestamp
        return None


class BackgroundTask:
    """
    Manages a background agent task with timeout watchdog.

    Handles:
    - Launching agent work without blocking
    - Sending acknowledgment if work takes > timeout
    - Sending final result when complete
    """

    def __init__(
        self,
        messenger: BossMessenger,
        acknowledgment_timeout: float = 180.0,  # 3 minutes
        acknowledgment_message: str = "I'm working on this.",
    ):
        self.messenger = messenger
        self.acknowledgment_timeout = acknowledgment_timeout
        self.acknowledgment_message = acknowledgment_message

        self._task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._started_at: datetime | None = None
        self._completed_at: datetime | None = None
        self._result: str | None = None
        self._error: Exception | None = None

    async def run(
        self,
        coro: Awaitable[str],
        send_result: bool = True,
    ) -> None:
        """
        Run the coroutine as a background task.

        Args:
            coro: The async work to perform (should return a string result)
            send_result: Whether to automatically send the result when done
        """
        self._started_at = datetime.now()

        # Start the main work
        self._task = asyncio.create_task(self._run_work(coro, send_result))

        # Start the watchdog
        self._watchdog_task = asyncio.create_task(self._watchdog())

        logger.info(f"[{self.messenger.session_id}] Background task started")

    async def _run_work(self, coro: Awaitable[str], send_result: bool) -> None:
        """Execute the work and handle completion."""
        try:
            self._result = await coro
            self._completed_at = datetime.now()

            if send_result and self._result:
                await self.messenger.send(self._result, message_type="result")

        except Exception as e:
            self._error = e
            self._completed_at = datetime.now()
            logger.error(f"[{self.messenger.session_id}] Background task failed: {e}")

            # Send error notification
            await self.messenger.send(
                f"I encountered an error: {str(e)[:200]}", message_type="error"
            )
        finally:
            # Cancel watchdog if still running
            if self._watchdog_task and not self._watchdog_task.done():
                self._watchdog_task.cancel()

    async def _watchdog(self) -> None:
        """
        Watch for timeout and send acknowledgment if needed.

        Waits for acknowledgment_timeout seconds, then checks if any
        message has been sent. If not, sends acknowledgment.
        """
        try:
            await asyncio.sleep(self.acknowledgment_timeout)

            # Check if we're still running and haven't communicated
            if (
                self._task
                and not self._task.done()
                and not self.messenger.has_communicated()
            ):
                await self.messenger.send_acknowledgment(self.acknowledgment_message)

        except asyncio.CancelledError:
            # Normal cancellation when task completes
            pass
        except Exception as e:
            logger.error(f"[{self.messenger.session_id}] Watchdog error: {e}")

    @property
    def is_running(self) -> bool:
        """Check if the task is still running."""
        return self._task is not None and not self._task.done()

    @property
    def is_complete(self) -> bool:
        """Check if the task has completed."""
        return self._completed_at is not None

    @property
    def result(self) -> str | None:
        """Get the result if complete."""
        return self._result

    @property
    def error(self) -> Exception | None:
        """Get the error if failed."""
        return self._error
