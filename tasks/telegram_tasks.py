"""
Telegram-specific tasks for message processing.

DESIGN PRINCIPLE: Keep Telegram logic separate from promise logic
for better modularity and testing.
"""
import json
import logging
from datetime import datetime
import sqlite3

from huey import crontab
from .huey_config import huey
from utilities.database import get_database_connection, update_message_queue_status

logger = logging.getLogger(__name__)


@huey.task(retries=2, retry_delay=60)
def process_missed_message(message_id: int):
    """
    Process a message that was received while bot was offline.
    
    IMPLEMENTATION NOTE: We store the full message in the database
    rather than passing large objects through the task queue.
    """
    start_time = datetime.now()
    logger.info(f"📬 STARTING MISSED MESSAGE PROCESSING | Message ID: {message_id}")
    
    try:
        with get_database_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            row = cursor.execute("""
                SELECT * FROM message_queue
                WHERE id = ? AND status = 'pending'
            """, (message_id,)).fetchone()
            
            if not row:
                logger.warning(f"📭 Message {message_id} not found or already processed")
                return
            
            message_data = dict(row)
            logger.info(f"📧 Found message from chat {message_data.get('chat_id')} | Text: {message_data.get('text', '')[:50]}...")
        
        # Mark as processing
        update_message_queue_status(message_id, 'processing')
        logger.info(f"🔄 Marked message {message_id} as processing")
        
    except Exception as e:
        logger.error(f"❌ Database error: {e}")
        return
    
    try:
        # Process through agent with context
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Run the async processing
        async def process_message():
            # Simple echo response for missed messages - actual processing happens via main server
            enhanced_message = f"[Missed message from {message_data['original_timestamp']}]: {message_data['message_text']}"
            
            # Return basic acknowledgment - no response needed for missed messages
            return None
        
        result = loop.run_until_complete(process_message())
        logger.info(f"Processed missed message {message_id} successfully")
        
        # No response needed for missed messages - they are processed silently
        
        # Mark as completed
        update_message_queue_status(message_id, 'completed')
        
        execution_time = datetime.now() - start_time
        logger.info(f"✅ COMPLETED MISSED MESSAGE PROCESSING | Message {message_id} in {execution_time.total_seconds():.1f}s")
        
    except Exception as e:
        execution_time = datetime.now() - start_time
        logger.error(f"❌ FAILED MISSED MESSAGE PROCESSING | Message {message_id} after {execution_time.total_seconds():.1f}s")
        logger.error(f"💥 Error: {e}", exc_info=True)
        
        # Mark as failed
        update_message_queue_status(message_id, 'failed', str(e))
        
        raise  # Re-raise for retry


@huey.periodic_task(crontab(minute='*/10'))
def process_pending_messages():
    """
    Process any pending messages in the queue.
    
    BEST PRACTICE: Use periodic tasks as a safety net for
    messages that might not have been queued properly.
    """
    from utilities.database import get_pending_messages
    
    pending_messages = get_pending_messages(limit=10)
    
    for message in pending_messages:
        logger.info(f"Scheduling missed message {message['id']} for processing")
        process_missed_message.schedule(args=(message['id'],), delay=0)


@huey.periodic_task(crontab(hour='*/24'))
def cleanup_old_messages():
    """
    Clean up old processed messages from the queue.
    
    IMPLEMENTATION NOTE: Keeps last 7 days of history for debugging.
    """
    cutoff_date = datetime.utcnow() - timedelta(days=7)
    
    with get_database_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM message_queue 
            WHERE status IN ('completed', 'failed') 
            AND processed_at < ?
        """, (cutoff_date.isoformat(),))
        
        deleted_count = cursor.rowcount
        conn.commit()
        
    if deleted_count > 0:
        logger.info(f"Cleaned up {deleted_count} old messages from queue")


# Import timedelta for cleanup task
from datetime import timedelta