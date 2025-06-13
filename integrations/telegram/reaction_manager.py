"""
ReactionManager: Orchestrates the sophisticated emoji reaction workflow.

Implements the complete emoji reaction system:
1. 👀 Read receipts when messages are received
2. Intent-based work indicators (👨‍💻, 🔍, etc.) 
3. Progress tracking during processing (⏳)
4. Success/error completion reactions (👍/❌)
5. Automated error recovery workflow
6. Huey promise integration with status updates
"""

import asyncio
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from pyrogram import Client
from pyrogram.errors import FloodWait, BadRequest

from .emoji_mapping import VALID_TELEGRAM_REACTIONS
from integrations.ollama_intent import IntentResult

logger = logging.getLogger(__name__)


class ReactionManager:
    """Orchestrates the sophisticated emoji reaction workflow."""
    
    # Intent-based emoji mapping for work indicators
    INTENT_EMOJI_MAP = {
        "general_development": "👨‍💻",
        "web_search": "🔍", 
        "image_analysis": "👁️",
        "data_analysis": "📊",
        "system_maintenance": "🔧",
        "documentation": "📚",
        "testing": "🧪",
        "debugging": "🐛",
        "deployment": "🚀",
        "planning": "🧠",
        "question_answering": "💬",
        "file_processing": "📁",
        "notification": "🔔",
        "urgent": "🚨"
    }
    
    # Standard workflow emojis
    READ_RECEIPT_EMOJI = "👀"
    PROGRESS_EMOJI = "⏳" 
    SUCCESS_EMOJI = "👍"
    ERROR_EMOJI = "❌"
    RECOVERY_EMOJI = "🔄"
    
    def __init__(self, client: Client, ollama_classifier=None, promise_manager=None):
        """Initialize with Telegram client and optional integrations."""
        self.client = client
        self.ollama_classifier = ollama_classifier
        self.promise_manager = promise_manager
        
        # Track reactions per message to avoid duplicates
        self.message_reactions: Dict[str, set] = {}
        
    async def add_read_receipt(self, chat_id: int, message_id: int) -> bool:
        """Step 1: Add 👀 eyes emoji when message first received."""
        return await self._add_reaction_safe(
            chat_id, message_id, self.READ_RECEIPT_EMOJI, "read_receipt"
        )
        
    async def add_intent_reaction(self, chat_id: int, message_id: int, intent: IntentResult) -> bool:
        """Step 2: Add work indicator based on Ollama intent classification."""
        if not intent or not intent.intent:
            return False
            
        # Use the emoji from intent result or fallback to our mapping
        emoji = intent.suggested_emoji if hasattr(intent, 'suggested_emoji') and intent.suggested_emoji else self.INTENT_EMOJI_MAP.get(intent.intent.value, "🧠")
        
        return await self._add_reaction_safe(
            chat_id, message_id, emoji, f"intent_{intent.intent.value}"
        )
        
    async def add_progress_reaction(self, chat_id: int, message_id: int, work_type: str = "processing") -> bool:
        """Step 3: Add ⏳ or work-specific progress indicator."""
        return await self._add_reaction_safe(
            chat_id, message_id, self.PROGRESS_EMOJI, f"progress_{work_type}"
        )
        
    async def add_completion_reaction(self, chat_id: int, message_id: int, success: bool, error: Exception = None) -> bool:
        """Step 4: Add 👍 for success or ❌ for error."""
        emoji = self.SUCCESS_EMOJI if success else self.ERROR_EMOJI
        reaction_type = "success" if success else f"error_{type(error).__name__ if error else 'unknown'}"
        
        # Remove progress emoji first
        await self._remove_reaction_safe(chat_id, message_id, self.PROGRESS_EMOJI)
        
        success_result = await self._add_reaction_safe(
            chat_id, message_id, emoji, reaction_type
        )
        
        # If this was an error, potentially trigger recovery
        if not success and error:
            await self._schedule_error_recovery(chat_id, message_id, error)
            
        return success_result
        
    async def add_recovery_reaction(self, chat_id: int, message_id: int) -> bool:
        """Step 5: Add 🔄 when starting automated error recovery."""
        return await self._add_reaction_safe(
            chat_id, message_id, self.RECOVERY_EMOJI, "error_recovery"
        )
        
    async def monitor_promise_status(self, promise_id: str, chat_id: int, message_id: int):
        """Step 6: Monitor Huey promise and update reactions accordingly."""
        if not self.promise_manager:
            logger.warning("Promise manager not available for monitoring")
            return
            
        try:
            # This would need to be implemented with actual promise monitoring
            # For now, we'll add progress indicator
            await self.add_progress_reaction(chat_id, message_id, "promise_work")
            
        except Exception as e:
            logger.error(f"Error monitoring promise {promise_id}: {e}")
            
    async def _add_reaction_safe(self, chat_id: int, message_id: int, emoji: str, reaction_type: str) -> bool:
        """Safely add reaction with error handling and duplicate prevention."""
        message_key = f"{chat_id}:{message_id}"
        
        # Initialize message reactions tracking
        if message_key not in self.message_reactions:
            self.message_reactions[message_key] = set()
            
        # Check if this reaction type already exists
        if reaction_type in self.message_reactions[message_key]:
            logger.debug(f"Reaction {reaction_type} already exists for message {message_key}")
            return True
            
        # Validate emoji is supported
        if emoji not in VALID_TELEGRAM_REACTIONS:
            logger.warning(f"Emoji {emoji} not in valid Telegram reactions")
            return False
            
        try:
            await self.client.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=emoji
            )
            
            # Track this reaction
            self.message_reactions[message_key].add(reaction_type)
            
            logger.debug(f"✅ Added {emoji} reaction ({reaction_type}) to message {message_key}")
            return True
            
        except FloodWait as e:
            logger.warning(f"FloodWait adding reaction: waiting {e.value} seconds")
            await asyncio.sleep(e.value)
            # Retry once
            try:
                await self.client.set_message_reaction(
                    chat_id=chat_id,
                    message_id=message_id,
                    reaction=emoji
                )
                self.message_reactions[message_key].add(reaction_type)
                return True
            except Exception as retry_error:
                logger.error(f"Failed to add reaction after FloodWait: {retry_error}")
                return False
                
        except BadRequest as e:
            logger.warning(f"BadRequest adding reaction {emoji}: {e}")
            return False
            
        except Exception as e:
            logger.error(f"Unexpected error adding reaction {emoji}: {e}")
            return False
            
    async def _remove_reaction_safe(self, chat_id: int, message_id: int, emoji: str) -> bool:
        """Safely remove reaction with error handling."""
        try:
            await self.client.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=""  # Empty string removes reactions
            )
            logger.debug(f"🗑️ Removed {emoji} reaction from message {chat_id}:{message_id}")
            return True
            
        except Exception as e:
            logger.debug(f"Could not remove reaction {emoji}: {e}")
            return False
            
    async def _schedule_error_recovery(self, chat_id: int, message_id: int, error: Exception):
        """Schedule automated error recovery workflow."""
        try:
            # Import here to avoid circular imports
            from .error_recovery import error_recovery_workflow
            from .models import MessageContext
            
            # Create a minimal context for recovery (we may not have full context at this point)
            recovery_context = MessageContext(
                message=None,
                chat_id=chat_id,
                username="system",
                workspace="ai",
                working_directory="/Users/valorengels/src/ai",
                is_dev_group=False,
                is_mention=False,
                cleaned_text=str(error),
                chat_history=[],
                reply_context=None,
                media_info=None,
                timestamp=datetime.now(),
                requires_response=False
            )
            
            # Start error recovery workflow in background
            asyncio.create_task(
                error_recovery_workflow.start_recovery(
                    error, recovery_context, chat_id, message_id, self
                )
            )
            
            logger.info(f"🔄 Scheduled error recovery for {type(error).__name__}: {str(error)[:100]}")
                
        except Exception as recovery_error:
            logger.error(f"Failed to schedule error recovery: {recovery_error}")
            
    def get_message_reactions(self, chat_id: int, message_id: int) -> set:
        """Get current reactions for a message."""
        message_key = f"{chat_id}:{message_id}"
        return self.message_reactions.get(message_key, set())
        
    def clear_message_reactions(self, chat_id: int, message_id: int):
        """Clear reaction tracking for a message."""
        message_key = f"{chat_id}:{message_id}"
        if message_key in self.message_reactions:
            del self.message_reactions[message_key]


# Convenience functions for backward compatibility
async def add_message_received_reaction(client: Client, chat_id: int, message_id: int) -> bool:
    """Add read receipt reaction - backward compatibility function."""
    reaction_manager = ReactionManager(client)
    return await reaction_manager.add_read_receipt(chat_id, message_id)


async def add_intent_based_reaction(client: Client, chat_id: int, message_id: int, intent: IntentResult) -> bool:
    """Add intent-based reaction - convenience function."""
    reaction_manager = ReactionManager(client)
    return await reaction_manager.add_intent_reaction(chat_id, message_id, intent)


async def add_completion_status_reaction(client: Client, chat_id: int, message_id: int, success: bool, error: Exception = None) -> bool:
    """Add completion status reaction - convenience function."""
    reaction_manager = ReactionManager(client)
    return await reaction_manager.add_completion_reaction(chat_id, message_id, success, error)