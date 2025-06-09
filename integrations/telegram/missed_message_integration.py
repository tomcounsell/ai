"""
Integration layer for new missed message system.

Replaces the legacy startup scan with robust promise-based processing.
"""

import logging
from datetime import datetime, timezone
from utilities.missed_message_manager import MissedMessageManager

logger = logging.getLogger(__name__)


class MissedMessageIntegration:
    """
    Integration adapter for the new missed message system.
    
    Provides clean interface between telegram client/handlers and the 
    robust missed message manager.
    """
    
    def __init__(self, telegram_client, message_handler):
        self.missed_message_manager = MissedMessageManager(telegram_client, message_handler)
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    async def startup_scan(self) -> None:
        """
        Replacement for legacy _check_startup_missed_messages.
        
        TEMPORARILY DISABLED: To prevent database lock conflicts with main server.
        Background scans are handled by Huey when system is stable.
        """
        self.logger.info("🚀 Starting new promise-based missed message system...")
        
        try:
            # TEMPORARILY DISABLED: Prevents database lock conflicts
            # await self.missed_message_manager.start_missed_message_scan()
            
            self.logger.info("✅ Missed message system startup disabled (prevents session locks)")
            
        except Exception as e:
            self.logger.error(f"❌ Failed to start missed message system: {e}", exc_info=True)
            # Don't fail startup - continue without missed message detection
    
    def update_last_seen(self, chat_id: int, message_id: int) -> None:
        """
        Update last seen message for a chat.
        
        Call this when processing any message to maintain accurate state.
        """
        try:
            self.missed_message_manager.update_last_seen(chat_id, message_id)
        except Exception as e:
            self.logger.warning(f"Failed to update last seen for chat {chat_id}: {e}")
    
    async def process_missed_for_chat(self, chat_id: int) -> None:
        """
        Process any pending missed messages for a chat.
        
        Call this when user sends a new message to trigger processing.
        """
        try:
            await self.missed_message_manager.process_pending_missed_messages(chat_id)
        except Exception as e:
            self.logger.error(f"Failed to process missed messages for chat {chat_id}: {e}")
    
    def is_enabled(self) -> bool:
        """Check if missed message system is enabled and functional."""
        return (
            self.missed_message_manager is not None and
            self.missed_message_manager.client is not None and
            self.missed_message_manager.message_handler is not None
        )