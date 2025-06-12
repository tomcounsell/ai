"""
Simplified MessageHandler using UnifiedMessageProcessor.

This replaces the 1,994-line monolithic handler with a clean delegation pattern.
"""

import logging

# Using pyrogram in this project
from typing import Any

# Mock telegram types for compatibility
Bot = Any
Update = Any
ContextTypes = type("ContextTypes", (), {"DEFAULT_TYPE": Any})
TelegramMessageHandler = Any
filters = type("filters", (), {"ALL": True})

from integrations.telegram.models import ProcessingResult
from integrations.telegram.unified_processor import (
    UnifiedMessageProcessor,
    create_unified_processor,
)

logger = logging.getLogger(__name__)


class MessageHandler:
    """
    Simplified handler using unified processor.

    Replaces 1,994 lines with ~100 lines by delegating to UnifiedMessageProcessor.
    """

    def __init__(self, telegram_bot: Bot, valor_agent=None):
        """Initialize with bot and optional agent."""
        self.bot = telegram_bot
        self.processor: UnifiedMessageProcessor | None = None
        self.valor_agent = valor_agent

        # Feature flags for gradual migration
        self.use_unified_processor = True
        self.legacy_fallback_enabled = False

        # Metrics
        self.total_messages = 0
        self.unified_messages = 0
        self.legacy_messages = 0

    async def initialize(self):
        """Initialize the processor and components."""
        if self.use_unified_processor:
            self.processor = await create_unified_processor(
                bot=self.bot, valor_agent=self.valor_agent
            )
            logger.info("Unified message processor initialized")

    async def handle_message(self, client, message) -> ProcessingResult:
        """
        Simplified entry point - delegates to unified processor.

        This method replaces the previous 432-line handle_message method.
        """
        self.total_messages += 1

        try:
            # Check feature flag
            if self.use_unified_processor and self.processor:
                # Use new unified processor
                self.unified_messages += 1
                # Create update-like object for unified processor
                update_obj = type('Update', (), {'message': message})()
                result = await self.processor.process_message(update_obj, client)

                # Log result
                if result.success:
                    logger.info(f"✅ Message processed successfully: {result.summary}")
                else:
                    logger.warning(f"⚠️ Message processing failed: {result.error}")

                return result

            elif self.legacy_fallback_enabled:
                # Fallback to legacy handler if enabled
                self.legacy_messages += 1
                logger.warning("Using legacy message handler (fallback)")
                # Would call legacy handler here
                return ProcessingResult.failed("Legacy handler not available")

            else:
                # No processor available
                logger.error("No message processor available")
                return ProcessingResult.failed("Message processor not initialized")

        except Exception as e:
            logger.error(f"❌ Unexpected error in message processing: {str(e)}", exc_info=True)

            # Try to send error response
            if message and message.chat and message.chat.id:
                try:
                    await client.send_message(
                        chat_id=message.chat.id,
                        text="❌ Sorry, an unexpected error occurred. Please try again.",
                        reply_to_message_id=message.id,
                    )
                except Exception as send_error:
                    logger.error(f"Failed to send error message: {send_error}")

            return ProcessingResult.failed(f"Unexpected error: {str(e)}")

    def get_handlers(self):
        """Get Telegram handlers for registration."""
        return [
            # Main message handler for all types
            TelegramMessageHandler(filters.ALL, self.handle_message)
        ]

    def get_metrics(self) -> dict:
        """Get handler metrics."""
        metrics = {
            "total_messages": self.total_messages,
            "unified_messages": self.unified_messages,
            "legacy_messages": self.legacy_messages,
            "unified_percentage": (
                (self.unified_messages / self.total_messages * 100)
                if self.total_messages > 0
                else 0
            ),
        }

        # Add processor metrics if available
        if self.processor:
            metrics["processor_metrics"] = self.processor.get_metrics()

        return metrics

    def set_feature_flags(self, use_unified: bool = True, legacy_fallback: bool = False):
        """Configure feature flags for gradual migration."""
        self.use_unified_processor = use_unified
        self.legacy_fallback_enabled = legacy_fallback
        logger.info(
            f"Feature flags updated: unified={use_unified}, legacy_fallback={legacy_fallback}"
        )

    async def shutdown(self):
        """Clean shutdown of handler and processor."""
        logger.info("Shutting down message handler")

        # Get final metrics
        metrics = self.get_metrics()
        logger.info(f"Final metrics: {metrics}")

        # Cleanup processor if needed
        if self.processor:
            # Any cleanup needed
            pass


def create_message_handler(bot: Bot, valor_agent=None) -> MessageHandler:
    """Factory function to create message handler."""
    return MessageHandler(telegram_bot=bot, valor_agent=valor_agent)
