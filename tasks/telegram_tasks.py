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
    with get_database_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        row = cursor.execute("""
            SELECT * FROM message_queue
            WHERE id = ? AND status = 'pending'
        """, (message_id,)).fetchone()
        
        if not row:
            logger.warning(f"Message {message_id} not found or already processed")
            return
        
        message_data = dict(row)
        
    # Mark as processing
    update_message_queue_status(message_id, 'processing')
    
    try:
        # Import Telegram handler components
        from integrations.telegram.client import get_telegram_client
        
        # Get Telegram client
        telegram_client = get_telegram_client()
        if not telegram_client or not telegram_client.client:
            raise Exception("Telegram client not available")
        
        # Process through agent with context
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Run the async processing
        async def process_message():
            # Import agent and context
            from agents.valor.agent import valor_agent
            from agents.valor.context import TelegramChatContext
            
            # Build context
            metadata = json.loads(message_data['metadata'] or '{}')
            context = TelegramChatContext(
                chat_id=message_data['chat_id'],
                username=message_data['sender_username'],
                is_group_chat=metadata.get('is_group_chat', False)
            )
            
            # Add note about missed message
            enhanced_message = f"[Missed message from {message_data['original_timestamp']}]: {message_data['message_text']}"
            
            # Process through agent
            result = await valor_agent.run(enhanced_message, deps=context)
            
            # Send response
            if result.data:
                await telegram_client.client.send_message(
                    message_data['chat_id'],
                    result.data
                )
            
            return result.data
        
        result = loop.run_until_complete(process_message())
        logger.info(f"Processed missed message {message_id} successfully")
        
        # Mark as completed
        update_message_queue_status(message_id, 'completed')
        
    except Exception as e:
        logger.error(f"Failed to process missed message {message_id}: {e}", exc_info=True)
        
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
        process_missed_message.schedule(args=(message['id'],))


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