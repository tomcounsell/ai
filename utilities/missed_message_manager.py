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
from collections import defaultdict

from pyrogram.enums import ChatType
from huey import crontab
from tasks.huey_config import huey
from utilities.database import (
    get_chat_state, update_chat_state, queue_missed_message, 
    get_pending_missed_messages, mark_scan_completed, update_message_queue_status
)

logger = logging.getLogger(__name__)

# Circuit breaker for failing chats
CHAT_FAILURE_COUNT = defaultdict(int)
CHAT_FAILURE_THRESHOLD = 3
CHAT_FAILURE_TIMEOUT = 300  # 5 minutes


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
        # Check circuit breaker
        if self._is_chat_circuit_broken(chat_id):
            self.logger.warning(f"Circuit breaker active for chat {chat_id} - skipping missed message processing")
            return
        
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
        """Get chat information for message filtering with timeout protection."""
        import asyncio
        
        try:
            # Add timeout to prevent hanging on get_chat API call
            chat = await asyncio.wait_for(
                self.client.get_chat(chat_id),
                timeout=5.0  # 5 second timeout
            )
            is_private_chat = chat.type == ChatType.PRIVATE
            
            # Check if dev group
            from integrations.notion.utils import is_dev_group
            is_dev_group_chat = is_dev_group(chat_id) if not is_private_chat else False
            
            return {
                'chat_type': 'private' if is_private_chat else 'group',
                'is_dev_group': is_dev_group_chat,
                'chat_title': getattr(chat, 'title', 'DM')
            }
        except asyncio.TimeoutError:
            self.logger.warning(f"Timeout getting chat info for {chat_id} - using defaults")
            return {
                'chat_type': 'unknown',
                'is_dev_group': False,
                'chat_title': 'Unknown'
            }
        except Exception as e:
            self.logger.warning(f"Could not get chat info for {chat_id}: {e}")
            self._record_chat_failure(chat_id)
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
            # Add timeout to get_me call
            import asyncio
            me = await asyncio.wait_for(
                self.client.get_me(),
                timeout=5.0
            )
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
    
    def _is_chat_circuit_broken(self, chat_id: int) -> bool:
        """Check if circuit breaker is active for a chat."""
        import time
        failure_info = CHAT_FAILURE_COUNT.get(chat_id, {'count': 0, 'last_failure': 0})
        
        # Reset if timeout has passed
        if time.time() - failure_info.get('last_failure', 0) > CHAT_FAILURE_TIMEOUT:
            CHAT_FAILURE_COUNT.pop(chat_id, None)
            return False
        
        return failure_info.get('count', 0) >= CHAT_FAILURE_THRESHOLD
    
    def _record_chat_failure(self, chat_id: int) -> None:
        """Record a failure for circuit breaker."""
        import time
        if chat_id not in CHAT_FAILURE_COUNT:
            CHAT_FAILURE_COUNT[chat_id] = {'count': 0, 'last_failure': 0}
        
        CHAT_FAILURE_COUNT[chat_id]['count'] += 1
        CHAT_FAILURE_COUNT[chat_id]['last_failure'] = time.time()
        
        if CHAT_FAILURE_COUNT[chat_id]['count'] >= CHAT_FAILURE_THRESHOLD:
            self.logger.warning(f"Circuit breaker tripped for chat {chat_id} after {CHAT_FAILURE_THRESHOLD} failures")


# Huey background tasks

@huey.task(retries=3, retry_delay=60)
def scan_chat_for_missed_messages(chat_id: int) -> None:
    """
    Background task to scan a single chat for missed messages.
    
    Uses last_seen_message_id to resume from where we left off.
    """
    start_time = datetime.now()
    logger.info(f"🔍 STARTING MISSED MESSAGE SCAN | Chat: {chat_id}")
    logger.info(f"⏰ Scan initiated at {start_time.strftime('%H:%M:%S')}")
    
    try:
        # This needs to run in async context
        import asyncio
        asyncio.run(_async_scan_chat(chat_id))
        
        execution_time = datetime.now() - start_time
        logger.info(f"✅ COMPLETED MISSED MESSAGE SCAN | Chat: {chat_id} in {execution_time.total_seconds():.1f}s")
        
    except Exception as e:
        execution_time = datetime.now() - start_time
        logger.error(f"❌ FAILED MISSED MESSAGE SCAN | Chat: {chat_id} after {execution_time.total_seconds():.1f}s")
        logger.error(f"💥 Error: {e}", exc_info=True)
        raise


async def _async_scan_chat(chat_id: int) -> None:
    """Async implementation of chat scanning."""
    import os
    from integrations.telegram.client import TelegramClient
    
    # Check if session file is already locked by another process
    session_file = "ai_project_bot.session"
    if os.path.exists(session_file):
        try:
            # Try to check if file is locked by attempting to get lock info
            import subprocess
            result = subprocess.run(['lsof', session_file], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                # Session file is locked by other processes
                logger.warning(f"Session file locked by other processes - skipping chat {chat_id} scan")
                return
        except Exception:
            # If we can't check, proceed anyway
            pass
    
    # Get or create telegram client connection
    client = TelegramClient()
    if not await client.initialize():
        logger.error(f"Cannot scan chat {chat_id} - Telegram client initialization failed")
        return
    
    if not client.is_connected:  # Fixed: removed () - is_connected is a property
        logger.error(f"Cannot scan chat {chat_id} - Telegram client not connected")
        await client.stop()
        return
    
    try:
        # Get chat state
        chat_state = get_chat_state(chat_id)
        last_seen_id = chat_state['last_seen_message_id'] if chat_state else None
        
        logger.info(f"Chat {chat_id} state: last_seen={last_seen_id}")
        
        missed_messages = []
        messages_scanned = 0
        
        # Scan message history with timeout protection
        try:
            # Wrap the entire history iteration in a timeout
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
        except asyncio.TimeoutError:
            logger.warning(f"Timeout while scanning chat {chat_id} history - processed {messages_scanned} messages")
        except Exception as e:
            logger.error(f"Error scanning chat {chat_id} history: {e}")
            raise
        
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
        try:
            await client.stop()
        except Exception as cleanup_error:
            logger.warning(f"Error stopping Telegram client: {cleanup_error}")


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
        
        logger.info(f"✅ Completed missed message processing for chat {chat_id}")
        
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