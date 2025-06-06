"""
Telegram-specific tasks for message processing.

DESIGN PRINCIPLE: Keep Telegram logic separate from promise logic
for better modularity and testing.
"""
import json
import logging
from datetime import datetime

from huey import crontab
from .huey_config import huey
from utilities.database import get_database_connection

logger = logging.getLogger(__name__)


@huey.task(retries=2, retry_delay=60)
def process_missed_message(message_id: int):
    """
    Process a message that was received while bot was offline.
    
    IMPLEMENTATION NOTE: We store the full message in the database
    rather than passing large objects through the task queue.
    """
    with get_database_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT chat_id, message_text, sender_username, metadata
            FROM message_queue
            WHERE id = ? AND status = 'pending'
        """, (message_id,))
        
        row = cursor.fetchone()
        if not row:
            logger.warning(f"Message {message_id} not found or already processed")
            return
        
        chat_id, text, username, metadata_json = row
        metadata = json.loads(metadata_json or '{}')
        
        # Mark as processing
        cursor.execute("""
            UPDATE message_queue 
            SET status = 'processing' 
            WHERE id = ?
        """, (message_id,))
        conn.commit()
    
    try:
        # Process through normal agent flow
        # BEST PRACTICE: Reuse existing message processing logic
        from integrations.telegram.handlers import get_message_handler
        
        handler = get_message_handler()
        if handler:
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Create mock message for processing
            from unittest.mock import Mock
            mock_message = Mock()
            mock_message.text = text
            mock_message.from_user = Mock()
            mock_message.from_user.username = username
            mock_message.chat = Mock()
            mock_message.chat.id = chat_id
            
            # Process the message
            loop.run_until_complete(
                handler._process_message_text(mock_message, chat_id, text)
            )
        
        # Mark as completed
        with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE message_queue 
                SET status = 'completed', processed_at = ? 
                WHERE id = ?
            """, (datetime.utcnow(), message_id))
            conn.commit()
            
    except Exception as e:
        logger.error(f"Failed to process missed message {message_id}: {e}")
        
        # Mark as failed
        with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE message_queue 
                SET status = 'failed', error_message = ? 
                WHERE id = ?
            """, (str(e), message_id))
            conn.commit()
            
        raise  # Re-raise for retry


@huey.periodic_task(crontab(minute='*/10'))
def process_pending_messages():
    """
    Process any pending messages in the queue.
    
    BEST PRACTICE: Use periodic tasks as a safety net for
    messages that might not have been queued properly.
    """
    with get_database_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM message_queue 
            WHERE status = 'pending' 
            AND created_at < datetime('now', '-1 minute')
            ORDER BY created_at ASC
            LIMIT 10
        """)
        
        pending_messages = cursor.fetchall()
    
    for (message_id,) in pending_messages:
        process_missed_message.schedule(args=(message_id,))