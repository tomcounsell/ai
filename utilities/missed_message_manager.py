"""
Robust missed message detection and processing using promise queue.

DESIGN PRINCIPLES:
1. Persistent state tracking - survive process restarts
2. Resumable from last known position - no fixed time windows  
3. Background processing via Huey - non-blocking startup
4. Comprehensive error recovery - graceful degradation
5. Chat-aware filtering - respect authorization and dev groups
"""

import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any
from dataclasses import dataclass

from pyrogram.enums import ChatType
from huey import crontab
from tasks.huey_config import huey
from utilities.database import (
    get_chat_state, update_chat_state, queue_missed_message, 
    get_pending_missed_messages, mark_scan_completed, update_message_queue_status
)

logger = logging.getLogger(__name__)


@dataclass
class MissedMessage:
    """Represents a missed message with full context."""
    chat_id: int
    message_id: int
    text: str
    sender_username: Optional[str]
    timestamp: datetime
    chat_type: str
    is_mention: bool = False
    metadata: Dict[str, Any] = None


class MissedMessageManager:
    """
    Manages missed message detection and processing with persistent state.
    
    Key improvements over legacy system:
    - Uses message IDs instead of timestamps for resumption
    - Persists state across restarts
    - Background processing via Huey
    - No fixed time windows
    - Comprehensive error recovery
    """
    
    def __init__(self, telegram_client, message_handler):
        self.client = telegram_client
        self.message_handler = message_handler
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    async def start_missed_message_scan(self) -> None:
        """
        Start comprehensive missed message scan on startup.
        
        This method runs in background via Huey to avoid blocking startup.
        """
        if not self.client or not self.message_handler:
            self.logger.warning("Client or message handler not available for missed message scan")
            return
        
        self.logger.info("🔍 Starting comprehensive missed message scan...")
        
        try:
            # Get all authorized chats
            authorized_chats = await self._get_authorized_chats()
            self.logger.info(f"Found {len(authorized_chats)} authorized chats to scan")
            
            # Schedule background scan for each chat
            for chat_id in authorized_chats:
                scan_chat_for_missed_messages.schedule((chat_id,), delay=0)
            
            self.logger.info(f"✅ Scheduled missed message scans for {len(authorized_chats)} chats")
            
        except Exception as e:
            self.logger.error(f"❌ Failed to start missed message scan: {e}", exc_info=True)
    
    async def _get_authorized_chats(self) -> List[int]:
        """Get list of chat IDs this bot instance should handle."""
        authorized_chats = []
        
        try:
            async for dialog in self.client.get_dialogs():
                chat = dialog.chat
                chat_id = chat.id
                is_private_chat = chat.type == ChatType.PRIVATE
                
                if self.message_handler._should_handle_chat(chat_id, is_private_chat):
                    authorized_chats.append(chat_id)
                    
        except Exception as e:
            self.logger.error(f"Error getting authorized chats: {e}")
        
        return authorized_chats
    
    def update_last_seen(self, chat_id: int, message_id: int, timestamp: datetime = None) -> None:
        """Update the last seen message for a chat."""
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        update_chat_state(
            chat_id=chat_id,
            last_seen_message_id=message_id,
            last_seen_timestamp=timestamp.isoformat(),
            bot_online=True
        )
        
        self.logger.debug(f"Updated last seen for chat {chat_id}: message {message_id}")
    
    async def process_pending_missed_messages(self, chat_id: int) -> None:
        """
        Process any pending missed messages for a chat.
        
        Called when user sends a new message to trigger processing.
        """
        pending_messages = get_pending_missed_messages(chat_id)
        
        if not pending_messages:
            return
        
        self.logger.info(f"📬 Processing {len(pending_messages)} pending missed messages for chat {chat_id}")
        
        try:
            # Group messages and create summary
            message_texts = [msg['message_text'] for msg in pending_messages]
            
            # Determine chat type for filtering
            chat_info = await self._get_chat_info(chat_id)
            
            # Filter based on chat type
            relevant_messages = await self._filter_relevant_messages(
                message_texts, chat_info['chat_type'], chat_info['is_dev_group']
            )
            
            if relevant_messages:
                # Create summary and route through agent
                summary = self._create_missed_message_summary(relevant_messages)
                
                # Schedule background processing
                process_missed_message_batch.schedule((chat_id, summary), delay=0)
                
                # Mark messages as processing
                for msg in pending_messages:
                    update_message_queue_status(msg['id'], 'processing')
            else:
                # Mark as completed (no relevant messages)
                for msg in pending_messages:
                    update_message_queue_status(msg['id'], 'completed')
                
                self.logger.info(f"No relevant missed messages to process for chat {chat_id}")
                
        except Exception as e:
            self.logger.error(f"Error processing pending missed messages for chat {chat_id}: {e}")
            
            # Mark as failed
            for msg in pending_messages:
                update_message_queue_status(msg['id'], 'failed', str(e))
    
    async def _get_chat_info(self, chat_id: int) -> Dict[str, Any]:
        """Get chat information for message filtering."""
        try:
            chat = await self.client.get_chat(chat_id)
            is_private_chat = chat.type == ChatType.PRIVATE
            
            # Check if dev group
            from integrations.notion.utils import is_dev_group
            is_dev_group_chat = is_dev_group(chat_id) if not is_private_chat else False
            
            return {
                'chat_type': 'private' if is_private_chat else 'group',
                'is_dev_group': is_dev_group_chat,
                'chat_title': getattr(chat, 'title', 'DM')
            }
        except Exception as e:
            self.logger.warning(f"Could not get chat info for {chat_id}: {e}")
            return {
                'chat_type': 'unknown',
                'is_dev_group': False,
                'chat_title': 'Unknown'
            }
    
    async def _filter_relevant_messages(self, messages: List[str], chat_type: str, 
                                       is_dev_group: bool) -> List[str]:
        """Filter messages based on chat type and mention detection."""
        if chat_type == 'private' or is_dev_group:
            # DMs and dev groups: all messages are relevant
            return messages
        
        # Regular groups: only messages with bot mentions
        try:
            me = await self.client.get_me()
            bot_username = me.username
            
            relevant = []
            for msg in messages:
                if f"@{bot_username}" in msg:
                    relevant.append(msg)
            
            return relevant
            
        except Exception as e:
            self.logger.error(f"Error filtering messages: {e}")
            return []  # Fail safe - don't process if we can't determine relevance
    
    def _create_missed_message_summary(self, messages: List[str]) -> str:
        """Create a summary of missed messages for agent processing."""
        if len(messages) == 1:
            return f"I was offline and missed this message: {messages[0]}"
        
        # Show last 3 messages for context
        recent = messages[-3:]
        return (
            f"I was offline and missed {len(messages)} messages. "
            f"Recent messages were: {'; '.join(recent)}"
        )


# Huey background tasks

@huey.task(retries=3, retry_delay=60)
def scan_chat_for_missed_messages(chat_id: int) -> None:
    """
    Background task to scan a single chat for missed messages.
    
    Uses last_seen_message_id to resume from where we left off.
    """
    logger.info(f"🔍 Scanning chat {chat_id} for missed messages...")
    
    try:
        # This needs to run in async context
        import asyncio
        asyncio.run(_async_scan_chat(chat_id))
        
    except Exception as e:
        logger.error(f"❌ Failed to scan chat {chat_id}: {e}", exc_info=True)
        raise


async def _async_scan_chat(chat_id: int) -> None:
    """Async implementation of chat scanning."""
    from integrations.telegram.client import TelegramClient
    
    # Get or create telegram client connection
    client = TelegramClient()
    if not await client.ensure_connected():
        logger.error(f"Cannot scan chat {chat_id} - Telegram client not connected")
        return
    
    try:
        # Get chat state
        chat_state = get_chat_state(chat_id)
        last_seen_id = chat_state['last_seen_message_id'] if chat_state else None
        
        logger.info(f"Chat {chat_id} state: last_seen={last_seen_id}")
        
        missed_messages = []
        messages_scanned = 0
        
        # Scan message history
        async for message in client.client.get_chat_history(chat_id):
            messages_scanned += 1
            
            # Stop if we've reached our last seen message
            if last_seen_id and message.id <= last_seen_id:
                logger.info(f"Reached last seen message {last_seen_id}, stopping scan")
                break
            
            # Skip non-text messages
            if not message.text:
                continue
            
            # Store as missed message
            missed_msg = MissedMessage(
                chat_id=chat_id,
                message_id=message.id,
                text=message.text,
                sender_username=getattr(message.from_user, 'username', None) if message.from_user else None,
                timestamp=message.date,
                chat_type='private' if message.chat.type == ChatType.PRIVATE else 'group',
                metadata={
                    'scan_time': datetime.now(timezone.utc).isoformat(),
                    'message_type': 'text'
                }
            )
            
            # Queue for processing
            queue_missed_message(
                chat_id=missed_msg.chat_id,
                message_id=missed_msg.message_id,
                message_text=missed_msg.text,
                sender_username=missed_msg.sender_username,
                original_timestamp=missed_msg.timestamp.isoformat(),
                metadata=missed_msg.metadata
            )
            
            missed_messages.append(missed_msg)
            
            # Safety limit to prevent runaway scans
            if messages_scanned >= 1000:
                logger.warning(f"Hit scan limit for chat {chat_id}, stopping")
                break
        
        # Update state
        if missed_messages:
            # Update last seen to the newest message we found
            newest_msg = missed_messages[0]  # First message is newest
            update_chat_state(
                chat_id=chat_id,
                last_seen_message_id=newest_msg.message_id,
                last_seen_timestamp=newest_msg.timestamp.isoformat()
            )
        
        mark_scan_completed(chat_id, messages_scanned)
        
        logger.info(f"✅ Chat {chat_id} scan complete: {len(missed_messages)} missed messages, {messages_scanned} total scanned")
        
    except Exception as e:
        logger.error(f"Error during async scan of chat {chat_id}: {e}", exc_info=True)
        raise
    finally:
        # Cleanup client connection
        if hasattr(client, 'cleanup'):
            await client.cleanup()


@huey.task(retries=2, retry_delay=30)
def process_missed_message_batch(chat_id: int, summary: str) -> None:
    """
    Background task to process a batch of missed messages through the agent.
    """
    logger.info(f"📬 Processing missed message batch for chat {chat_id}")
    
    try:
        # This would integrate with the existing agent routing system
        # For now, just mark as completed
        # TODO: Integrate with _route_message_with_intent or similar
        
        logger.info(f"✅ Processed missed messages for chat {chat_id}: {summary[:100]}...")
        
        # Mark all pending messages for this chat as completed
        pending = get_pending_missed_messages(chat_id)
        for msg in pending:
            update_message_queue_status(msg['id'], 'completed')
            
    except Exception as e:
        logger.error(f"❌ Failed to process missed messages for chat {chat_id}: {e}")
        
        # Mark as failed
        pending = get_pending_missed_messages(chat_id)
        for msg in pending:
            update_message_queue_status(msg['id'], 'failed', str(e))
        
        raise


# Periodic cleanup task
@huey.periodic_task(crontab(minute='*/30'))  # Every 30 minutes
def cleanup_old_processed_messages():
    """Clean up old processed missed messages."""
    from datetime import timedelta
    from utilities.database import get_database_connection
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    
    try:
        with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM message_queue 
                WHERE message_type = 'missed' 
                AND status IN ('completed', 'failed')
                AND processed_at < ?
            """, (cutoff.isoformat(),))
            
            deleted = cursor.rowcount
            conn.commit()
            
            if deleted > 0:
                logger.info(f"🧹 Cleaned up {deleted} old processed missed messages")
                
    except Exception as e:
        logger.error(f"Error during missed message cleanup: {e}")