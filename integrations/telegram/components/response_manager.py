"""
ResponseManager: Unified response delivery with error handling.

Consolidates response formatting, delivery, and error recovery logic.
"""

import asyncio
import logging
from datetime import datetime

# Using pyrogram instead of python-telegram-bot
from typing import Any


# Mock telegram exceptions for compatibility
class BadRequest(Exception):  # noqa: N818
    pass


class NetworkError(Exception):
    pass


class TimedOut(Exception):  # noqa: N818
    pass


class ParseMode:
    MARKDOWN = "MarkdownV2"  # Pyrogram uses MarkdownV2, not "markdown"


from integrations.telegram.models import (
    AgentResponse,
    DeliveryResult,
    MediaAttachment,
    MessageContext,
)

logger = logging.getLogger(__name__)


class ResponseManager:
    """Unified response delivery with error handling."""

    def __init__(self, telegram_bot: Any | None = None):
        """Initialize with optional Telegram bot instance."""
        self.bot = telegram_bot
        self.max_message_length = 4096  # Telegram limit
        self.retry_attempts = 3
        self.retry_delay = 1  # seconds

    async def deliver_response(
        self, response: AgentResponse, context: MessageContext
    ) -> DeliveryResult:
        """
        Unified response delivery with error handling.

        Handles:
        1. Response formatting
        2. Media attachments
        3. Reactions
        4. Message delivery
        5. History storage
        6. Error recovery

        Returns:
            DeliveryResult with delivery status
        """
        try:
            # Format response for Telegram
            formatted_messages = self._format_for_telegram(response.content)

            # Send main response
            main_result = await self._send_messages(
                context.chat_id, formatted_messages, reply_to_message_id=context.message.id
            )

            if not main_result.success:
                return main_result

            # Handle media attachments
            if response.has_media:
                media_result = await self._handle_media_attachments(
                    context.chat_id,
                    response.media_attachments,
                    reply_to_message_id=main_result.message_id,
                )
                if not media_result.success:
                    logger.warning(f"Media delivery failed: {media_result.error}")

            # Add reactions if specified
            if response.reactions:
                await self._add_reactions(
                    context.chat_id, context.message.id, response.reactions
                )

            # Store conversation history
            await self._store_conversation_history(context, response, main_result)

            return DeliveryResult(
                success=True,
                message_id=main_result.message_id,
                metadata={
                    "processing_time": response.processing_time,
                    "tokens_used": response.tokens_used,
                    "has_media": response.has_media,
                    "reaction_count": len(response.reactions),
                },
            )

        except Exception as e:
            logger.error(f"Response delivery error: {str(e)}", exc_info=True)

            # Try fallback response
            fallback_result = await self._handle_delivery_error(e, context)
            return fallback_result

    def _format_for_telegram(self, content: str) -> list[str]:
        """Format response text for Telegram with message splitting."""
        if not content:
            return ["I processed your message but have no response."]

        # Clean up formatting
        content = content.strip()

        # Split long messages
        if len(content) <= self.max_message_length:
            return [content]

        # Smart splitting by paragraphs/sentences
        messages = []
        current_message = ""

        # Try to split by paragraphs first
        paragraphs = content.split("\n\n")

        for paragraph in paragraphs:
            if len(current_message) + len(paragraph) + 2 <= self.max_message_length:
                if current_message:
                    current_message += "\n\n"
                current_message += paragraph
            else:
                if current_message:
                    messages.append(current_message)
                current_message = paragraph

        if current_message:
            messages.append(current_message)

        # If any message is still too long, force split
        final_messages = []
        for msg in messages:
            if len(msg) <= self.max_message_length:
                final_messages.append(msg)
            else:
                # Force split at max length
                for i in range(0, len(msg), self.max_message_length):
                    final_messages.append(msg[i : i + self.max_message_length])

        return final_messages

    async def _send_messages(
        self, chat_id: int, messages: list[str], reply_to_message_id: int | None = None
    ) -> DeliveryResult:
        """Send messages with retry logic."""
        if not self.bot:
            return DeliveryResult(success=False, error="Telegram bot not initialized")

        last_message_id = None

        for i, message in enumerate(messages):
            # Only reply to first message
            reply_to = reply_to_message_id if i == 0 else None

            for attempt in range(self.retry_attempts):
                try:
                    sent_message = await self.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode=None,  # Disable markdown for now to avoid format errors
                        reply_to_message_id=reply_to,
                    )
                    last_message_id = sent_message.id
                    break

                except BadRequest as e:
                    # Try without markdown if formatting error
                    if "can't parse entities" in str(e).lower():
                        try:
                            sent_message = await self.bot.send_message(
                                chat_id=chat_id, text=message, reply_to_message_id=reply_to
                            )
                            last_message_id = sent_message.id
                            break
                        except Exception:
                            pass

                    if attempt == self.retry_attempts - 1:
                        raise

                except (TimedOut, NetworkError):
                    if attempt < self.retry_attempts - 1:
                        await asyncio.sleep(self.retry_delay * (attempt + 1))
                    else:
                        raise

        return DeliveryResult(success=True, message_id=last_message_id)

    async def _handle_media_attachments(
        self,
        chat_id: int,
        attachments: list[MediaAttachment],
        reply_to_message_id: int | None = None,
    ) -> DeliveryResult:
        """Process and send media attachments."""
        if not self.bot:
            return DeliveryResult(success=False, error="Bot not initialized")

        try:
            for attachment in attachments:
                if attachment.media_type == "image":
                    await self.bot.send_photo(
                        chat_id=chat_id,
                        photo=attachment.file_path,
                        caption=attachment.caption,
                        reply_to_message_id=reply_to_message_id,
                    )
                elif attachment.media_type == "document":
                    await self.bot.send_document(
                        chat_id=chat_id,
                        document=attachment.file_path,
                        caption=attachment.caption,
                        reply_to_message_id=reply_to_message_id,
                    )

            return DeliveryResult(success=True)

        except Exception as e:
            logger.error(f"Media delivery error: {str(e)}")
            return DeliveryResult(success=False, error=f"Media delivery failed: {str(e)}")

    async def _add_reactions(self, chat_id: int, message_id: int, reactions: list[str]):
        """Add reactions to the original message."""
        if not self.bot:
            return

        # Filter to valid Telegram reactions
        valid_reactions = ["👍", "👎", "❤️", "🔥", "🎉", "😁", "😮", "😢", "🤔", "👏"]

        for reaction in reactions:
            if reaction in valid_reactions:
                try:
                    await self.bot.send_reaction(
                        chat_id=chat_id, message_id=message_id, emoji=reaction
                    )
                except Exception as e:
                    logger.debug(f"Failed to add reaction {reaction}: {str(e)}")

    async def _store_conversation_history(
        self, context: MessageContext, response: AgentResponse, delivery_result: DeliveryResult
    ):
        """Store conversation in unified history."""
        try:
            from utilities.database import get_database_connection

            with get_database_connection() as conn:
                # Store user message
                conn.execute(
                    """
                    INSERT INTO chat_messages
                    (chat_id, message_id, username, text, is_bot_message, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        context.chat_id,
                        context.message.id,
                        context.username,
                        context.cleaned_text,
                        False,
                        context.timestamp.isoformat(),
                    ),
                )

                # Store bot response
                if delivery_result.success and delivery_result.message_id:
                    conn.execute(
                        """
                        INSERT INTO chat_messages
                        (chat_id, message_id, username, text, is_bot_message, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """,
                        (
                            context.chat_id,
                            delivery_result.message_id,
                            "valoraibot",
                            response.content,
                            True,
                            datetime.now().isoformat(),
                        ),
                    )

                conn.commit()

        except Exception as e:
            logger.error(f"Failed to store conversation history: {str(e)}")

    async def _handle_delivery_error(
        self, error: Exception, context: MessageContext
    ) -> DeliveryResult:
        """Handle delivery errors with fallback responses."""
        error_message = str(error)

        # Categorize error
        if isinstance(error, BadRequest):
            if "message not found" in error_message.lower():
                # Can't reply to deleted message
                return await self._send_messages(
                    context.chat_id, ["⚠️ Original message was deleted. Here's my response:"]
                )
            elif "chat not found" in error_message.lower():
                return DeliveryResult(success=False, error="Chat no longer exists")

        elif isinstance(error, TimedOut | NetworkError):
            # Network issues
            return DeliveryResult(success=False, error="Network error - will retry", retry_after=60)

        # Generic error response
        try:
            fallback_result = await self._send_messages(
                context.chat_id,
                ["❌ Sorry, I encountered an error sending my response. Please try again."],
            )
            fallback_result.error = error_message
            return fallback_result

        except Exception:
            # Complete failure
            return DeliveryResult(
                success=False, error=f"Complete delivery failure: {error_message}"
            )

    def create_fallback_response(self, error: Exception) -> str:
        """Generate user-friendly error messages."""
        error_type = type(error).__name__

        if error_type == "TimeoutError":
            return "⏱️ Response took too long. Please try again with a simpler request."
        elif error_type == "RateLimitError":
            return "🚦 Rate limit reached. Please wait a moment before trying again."
        elif "API" in error_type:
            return "🔧 Service temporarily unavailable. Please try again in a few minutes."
        else:
            return "❌ An unexpected error occurred. Please try again or contact support."
