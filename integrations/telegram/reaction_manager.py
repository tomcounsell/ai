"""
Telegram reaction management system for intent-based message preprocessing.

This module provides a centralized system for managing Telegram message reactions
based on message intent classification and processing status.
"""

import asyncio
import logging
from enum import Enum

from ..ollama_intent import IntentResult, MessageIntent
from .emoji_mapping import VALID_TELEGRAM_REACTIONS

logger = logging.getLogger(__name__)


class ReactionStatus(Enum):
    """Status of message processing for reaction management."""

    RECEIVED = "received"  # Message received, initial reaction
    PROCESSING = "processing"  # Intent classified, processing started
    COMPLETED = "completed"  # Processing completed successfully
    ERROR = "error"  # Error occurred during processing
    IGNORED = "ignored"  # Message ignored (not in whitelist, etc.)


class TelegramReactionManager:
    """Manages Telegram message reactions based on intent and processing status."""

    def __init__(self):
        """Initialize the reaction manager."""

        # Base reaction emojis for different statuses
        # Using only valid Telegram reactions based on API investigation
        self.status_reactions = {
            ReactionStatus.RECEIVED: "👀",  # Eyes - message seen
            ReactionStatus.PROCESSING: None,  # Will use intent-specific emoji
            ReactionStatus.COMPLETED: "👍",  # Thumbs up - completed (✅ not available)
            ReactionStatus.ERROR: "👎",  # Thumbs down - error (🚫 not available)
            ReactionStatus.IGNORED: None,  # No reaction for ignored messages
        }

        # Use the valid reactions from the emoji mapping module
        self.valid_telegram_emojis = VALID_TELEGRAM_REACTIONS

        # Intent-specific reaction emojis (from intent classification)
        # Using only valid Telegram reaction emojis based on API investigation
        self.intent_reactions = {
            MessageIntent.CASUAL_CHAT: "😁",
            MessageIntent.QUESTION_ANSWER: "🤔",
            MessageIntent.PROJECT_QUERY: "🙏",
            MessageIntent.DEVELOPMENT_TASK: "👨‍💻",
            MessageIntent.IMAGE_GENERATION: "🎉",  # Party - 🎨 not available
            MessageIntent.IMAGE_ANALYSIS: "👀",
            MessageIntent.WEB_SEARCH: "🗿",
            MessageIntent.LINK_ANALYSIS: "🍾",
            MessageIntent.SYSTEM_HEALTH: "❤",  # Plain heart - ❤️ with variant selector not available
            MessageIntent.UNCLEAR: "🤨",
        }


        # Track reactions added to messages to avoid duplicates
        self.message_reactions: dict[tuple, list[str]] = {}  # (chat_id, message_id) -> [emojis]

    async def add_received_reaction(self, client, chat_id: int, message_id: int) -> bool:
        """
        Add initial "received" reaction to indicate message was seen.

        Args:
            client: Telegram client instance
            chat_id: Chat ID
            message_id: Message ID

        Returns:
            bool: True if reaction was added successfully
        """
        return await self._add_reaction(
            client,
            chat_id,
            message_id,
            self.status_reactions[ReactionStatus.RECEIVED],
            ReactionStatus.RECEIVED,
        )

    async def add_intent_reaction(
        self, client, chat_id: int, message_id: int, intent_result: IntentResult
    ) -> bool:
        """
        Add intent-specific reaction based on classification.

        Args:
            client: Telegram client instance
            chat_id: Chat ID
            message_id: Message ID
            intent_result: Result from intent classification

        Returns:
            bool: True if reaction was added successfully
        """
        # Use suggested emoji from classification if available and valid, otherwise use default
        emoji = intent_result.suggested_emoji
        if not emoji or len(emoji) != 1 or emoji not in self.valid_telegram_emojis:
            emoji = self.intent_reactions.get(intent_result.intent, "🤔")
            logger.debug(
                f"Invalid suggested emoji '{intent_result.suggested_emoji}', using default: {emoji}"
            )

        success = await self._add_reaction(
            client, chat_id, message_id, emoji, ReactionStatus.PROCESSING
        )

        if success:
            logger.info(
                f"Added intent reaction {emoji} for {intent_result.intent.value} "
                f"(confidence: {intent_result.confidence:.2f})"
            )

        return success

    async def add_completion_reaction(self, client, chat_id: int, message_id: int) -> bool:
        """
        Add completion reaction to indicate processing finished.

        Args:
            client: Telegram client instance
            chat_id: Chat ID
            message_id: Message ID

        Returns:
            bool: True if reaction was added successfully
        """
        return await self._add_reaction(
            client,
            chat_id,
            message_id,
            self.status_reactions[ReactionStatus.COMPLETED],
            ReactionStatus.COMPLETED,
        )

    async def add_error_reaction(self, client, chat_id: int, message_id: int) -> bool:
        """
        Add error reaction to indicate processing failed.

        Args:
            client: Telegram client instance
            chat_id: Chat ID
            message_id: Message ID

        Returns:
            bool: True if reaction was added successfully
        """
        return await self._add_reaction(
            client,
            chat_id,
            message_id,
            self.status_reactions[ReactionStatus.ERROR],
            ReactionStatus.ERROR,
        )

    async def _add_reaction(
        self, client, chat_id: int, message_id: int, emoji: str | None, status: ReactionStatus
    ) -> bool:
        """
        Internal method to add a reaction to a message.

        Uses a 3-reaction strategy:
        1. Acknowledge (👀) - always present
        2. Intent/Tool (varies) - replaced as processing evolves
        3. Final status (👍/👎) - added at completion

        Args:
            client: Telegram client instance
            chat_id: Chat ID
            message_id: Message ID
            emoji: Emoji to add as reaction
            status: Status this reaction represents

        Returns:
            bool: True if reaction was added successfully
        """
        if not emoji:
            return False

        # Validate emoji is in valid Telegram reactions list
        if emoji not in self.valid_telegram_emojis:
            logger.warning(f"Invalid emoji '{emoji}', using fallback")
            emoji = "🤔"

        message_key = (chat_id, message_id)

        try:
            # Get existing reactions
            existing_reactions = self.message_reactions.get(message_key, [])

            # Determine which reactions to keep based on status
            if status == ReactionStatus.RECEIVED:
                # First reaction - just add it
                new_reactions = [emoji]
            elif status == ReactionStatus.PROCESSING:
                # Second reaction - keep first (👀), replace/add second
                if len(existing_reactions) >= 1:
                    new_reactions = [existing_reactions[0], emoji]
                else:
                    new_reactions = [emoji]
            elif status in [ReactionStatus.COMPLETED, ReactionStatus.ERROR]:
                # Third reaction - keep first two, add final
                if len(existing_reactions) >= 2:
                    new_reactions = existing_reactions[:2] + [emoji]
                elif len(existing_reactions) == 1:
                    new_reactions = existing_reactions + [emoji]
                else:
                    new_reactions = [emoji]
            else:
                # Default: just add to existing
                new_reactions = existing_reactions + [emoji]

            # Use raw API to set all reactions at once
            from pyrogram.raw import functions, types

            # Create reaction objects for all emojis
            reactions = [
                types.ReactionEmoji(emoticon=reaction_emoji) for reaction_emoji in new_reactions
            ]

            # Send all reactions (replaces existing)
            await client.invoke(
                functions.messages.SendReaction(
                    peer=await client.resolve_peer(chat_id),
                    msg_id=message_id,
                    reaction=reactions,
                    big=False,
                )
            )

            # Track the reactions
            self.message_reactions[message_key] = new_reactions

            logger.debug(
                f"Set reactions for message {message_key}: {' '.join(new_reactions)} (status: {status.value})"
            )
            return True

        except Exception as e:
            logger.warning(f"Failed to add reaction {emoji} to message {message_key}: {e}")
            # Fallback to simple send_reaction if raw API fails
            try:
                await client.send_reaction(chat_id, message_id, emoji)

                # Track the reaction even with fallback
                if message_key not in self.message_reactions:
                    self.message_reactions[message_key] = []

                # Simple append for fallback (can't control replacement)
                if emoji not in self.message_reactions[message_key]:
                    self.message_reactions[message_key].append(emoji)

                logger.debug(f"Added reaction {emoji} via fallback method")
                return True
            except Exception as fallback_e:
                logger.warning(f"Fallback also failed: {fallback_e}")
                return False

    async def update_reaction_sequence(
        self,
        client,
        chat_id: int,
        message_id: int,
        intent_result: IntentResult,
        success: bool = True,
    ) -> bool:
        """
        Update the complete reaction sequence for a message.

        This method manages the full lifecycle of reactions:
        1. Received (👀) - already added
        2. Intent-specific emoji
        3. Completion (✅) or Error (❌)

        Args:
            client: Telegram client instance
            chat_id: Chat ID
            message_id: Message ID
            intent_result: Result from intent classification
            success: Whether processing completed successfully

        Returns:
            bool: True if all reactions were updated successfully
        """
        # Only add completion/error reaction since intent reaction was already added
        # during the intent classification phase
        
        # Small delay to ensure reactions appear in sequence
        await asyncio.sleep(0.2)

        # Add completion/error reaction
        if success:
            return await self.add_completion_reaction(client, chat_id, message_id)
        else:
            return await self.add_error_reaction(client, chat_id, message_id)

    def get_message_reactions(self, chat_id: int, message_id: int) -> list[str]:
        """
        Get all reactions added to a specific message.

        Args:
            chat_id: Chat ID
            message_id: Message ID

        Returns:
            List[str]: List of emoji reactions added to this message
        """
        message_key = (chat_id, message_id)
        return self.message_reactions.get(message_key, []).copy()

    def clear_message_reactions(self, chat_id: int, message_id: int) -> None:
        """
        Clear tracked reactions for a message (for cleanup).

        Args:
            chat_id: Chat ID
            message_id: Message ID
        """
        message_key = (chat_id, message_id)
        if message_key in self.message_reactions:
            del self.message_reactions[message_key]

    def get_intent_emoji(self, intent: MessageIntent) -> str:
        """
        Get the default emoji for a specific intent.

        Args:
            intent: Message intent

        Returns:
            str: Emoji character for this intent
        """
        return self.intent_reactions.get(intent, "🤔")

    async def update_tool_reaction(
        self, client, chat_id: int, message_id: int, tool_emoji: str
    ) -> bool:
        """
        Update the second reaction slot with a tool-specific emoji.

        This replaces the intent emoji with a tool-specific one as processing evolves:
        - 🔍 when searching
        - 📊 when analyzing data
        - 🎨 when generating images
        - 🌐 when fetching web data
        - 🔨 when executing tasks
        - etc.

        Args:
            client: Telegram client instance
            chat_id: Chat ID
            message_id: Message ID
            tool_emoji: Emoji representing the tool being used

        Returns:
            bool: True if reaction was updated successfully
        """
        if tool_emoji not in self.valid_telegram_emojis:
            logger.warning(f"Invalid tool emoji '{tool_emoji}', skipping")
            return False

        return await self._add_reaction(
            client, chat_id, message_id, tool_emoji, ReactionStatus.PROCESSING
        )

    async def cleanup_old_reactions(self, max_tracked_messages: int = 1000) -> None:
        """
        Clean up old reaction tracking data to prevent memory buildup.

        Args:
            max_tracked_messages: Maximum number of messages to keep tracked
        """
        if len(self.message_reactions) > max_tracked_messages:
            # Keep only the most recent entries (this is a simple implementation)
            # In a production system, you might want to use timestamps
            items = list(self.message_reactions.items())
            to_keep = items[-max_tracked_messages:]
            self.message_reactions = dict(to_keep)

            logger.info(f"Cleaned up reaction tracking, kept {len(to_keep)} most recent messages")


# Singleton instance for use throughout the application
reaction_manager = TelegramReactionManager()


async def add_message_received_reaction(client, chat_id: int, message_id: int) -> bool:
    """
    Convenience function to add initial "received" reaction.

    Args:
        client: Telegram client instance
        chat_id: Chat ID
        message_id: Message ID

    Returns:
        bool: True if reaction was added successfully
    """
    return await reaction_manager.add_received_reaction(client, chat_id, message_id)


async def add_intent_based_reaction(
    client, chat_id: int, message_id: int, intent_result: IntentResult
) -> bool:
    """
    Convenience function to add intent-specific reaction.

    Args:
        client: Telegram client instance
        chat_id: Chat ID
        message_id: Message ID
        intent_result: Result from intent classification

    Returns:
        bool: True if reaction was added successfully
    """
    return await reaction_manager.add_intent_reaction(client, chat_id, message_id, intent_result)


async def complete_reaction_sequence(
    client, chat_id: int, message_id: int, intent_result: IntentResult, success: bool = True
) -> bool:
    """
    Convenience function to complete the full reaction sequence.

    Args:
        client: Telegram client instance
        chat_id: Chat ID
        message_id: Message ID
        intent_result: Result from intent classification
        success: Whether processing completed successfully

    Returns:
        bool: True if all reactions were updated successfully
    """
    return await reaction_manager.update_reaction_sequence(
        client, chat_id, message_id, intent_result, success
    )


async def update_tool_reaction(client, chat_id: int, message_id: int, tool_emoji: str) -> bool:
    """
    Convenience function to update the tool reaction (second slot).

    Use this to replace the intent emoji with a tool-specific one:
    - 🔍 when searching
    - 📊 when analyzing
    - 🎨 when generating images
    - 🌐 when fetching web data
    - 🔨 when building
    - etc.

    Args:
        client: Telegram client instance
        chat_id: Chat ID
        message_id: Message ID
        tool_emoji: Emoji for the tool being used

    Returns:
        bool: True if reaction was updated successfully
    """
    return await reaction_manager.update_tool_reaction(client, chat_id, message_id, tool_emoji)
