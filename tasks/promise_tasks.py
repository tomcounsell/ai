"""
Promise execution tasks using Huey.

DESIGN PATTERN: Each task should be:
1. Idempotent - safe to retry
2. Atomic - completes fully or fails cleanly
3. Logged - comprehensive logging for debugging
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from huey import crontab
from .huey_config import huey
from utilities.database import (
    get_promise, update_promise_status, get_database_connection, get_pending_promises
)

logger = logging.getLogger(__name__)


def with_promise_tracking(func):
    """
    Decorator that handles promise status updates and error tracking.
    
    IMPLEMENTATION NOTE: This pattern ensures consistent status
    updates across all promise-executing tasks.
    """
    def wrapper(promise_id: int, *args, **kwargs):
        try:
            # Mark as in_progress
            update_promise_status(promise_id, 'in_progress')
            logger.info(f"Starting promise {promise_id}: {func.__name__}")
            
            # Execute the actual task
            result = func(promise_id, *args, **kwargs)
            
            # Mark as completed
            update_promise_status(promise_id, 'completed', result_summary=result)
            logger.info(f"Completed promise {promise_id}")
            
            return result
            
        except Exception as e:
            logger.error(f"Failed promise {promise_id}: {str(e)}", exc_info=True)
            update_promise_status(promise_id, 'failed', error_message=str(e))
            raise  # Re-raise for Huey retry mechanism
    
    # Preserve the original function's name for Huey
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


@huey.task(retries=3, retry_delay=60)
@with_promise_tracking
def execute_coding_task(promise_id: int) -> str:
    """
    Execute a coding task using Claude Code.
    
    BEST PRACTICE: Keep task functions focused on one responsibility.
    Complex logic should be broken into helper functions.
    
    Args:
        promise_id: Database ID of the promise to execute
        
    Returns:
        Result summary string for user notification
        
    Raises:
        Exception: Any error during execution (triggers retry)
    """
    promise = get_promise(promise_id)
    if not promise:
        raise ValueError(f"Promise {promise_id} not found")
    
    # Parse task metadata
    metadata = json.loads(promise.get('metadata') or '{}')
    
    # IMPLEMENTATION NOTE: Import here to avoid circular imports
    from tools.valor_delegation_tool import spawn_valor_session
    
    # Execute with Claude Code
    result = spawn_valor_session(
        task_description=promise['task_description'],
        target_directory=metadata.get('target_directory', '.'),
        specific_instructions=metadata.get('instructions', ''),
        force_sync=True  # Force synchronous execution since we're already in background
    )
    
    # Send completion notification
    # BEST PRACTICE: Use .schedule() for follow-up tasks
    send_completion_notification.schedule(
        args=(promise_id, result),
        delay=1  # Small delay to ensure DB updates are committed
    )
    
    return result


@huey.task(retries=3, retry_delay=60)
@with_promise_tracking
def execute_search_task(promise_id: int) -> str:
    """Execute a search task."""
    promise = get_promise(promise_id)
    if not promise:
        raise ValueError(f"Promise {promise_id} not found")
    
    # For now, just return a placeholder
    # TODO: Implement actual search logic
    result = f"Search completed for: {promise['task_description']}"
    
    send_completion_notification.schedule(args=(promise_id, result), delay=1)
    
    return result


@huey.task(retries=3, retry_delay=60)
@with_promise_tracking
def execute_analysis_task(promise_id: int) -> str:
    """Execute an analysis task."""
    promise = get_promise(promise_id)
    if not promise:
        raise ValueError(f"Promise {promise_id} not found")
    
    # For now, just return a placeholder
    # TODO: Implement actual analysis logic
    result = f"Analysis completed for: {promise['task_description']}"
    
    send_completion_notification.schedule(args=(promise_id, result), delay=1)
    
    return result


@huey.task(retries=2, retry_delay=30)
def send_completion_notification(promise_id: int, result: str):
    """
    Send completion message to user via Telegram.
    
    IMPLEMENTATION NOTE: This is a separate task so notification
    failures don't affect the main task completion status.
    """
    promise = get_promise(promise_id)
    if not promise:
        logger.error(f"Promise {promise_id} not found for notification")
        return
    
    # Import here to avoid circular imports
    from integrations.telegram.client import get_telegram_client
    
    # Format completion message
    # BEST PRACTICE: Make messages informative but concise
    duration = format_duration(promise.get('created_at'), promise.get('completed_at'))
    message = f"""✅ **Task Complete!**

I finished working on: {promise['task_description'][:100]}{'...' if len(promise['task_description']) > 100 else ''}

**Result:**
{result[:500]}{'...' if len(result) > 500 else ''}

_Completed in {duration}_
"""
    
    try:
        # Get Telegram client and send message
        client = get_telegram_client()
        if client and client.client:
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            loop.run_until_complete(
                client.client.send_message(
                    chat_id=promise['chat_id'],
                    text=message
                )
            )
            
            logger.info(f"Sent completion notification for promise {promise_id}")
    except Exception as e:
        # BEST PRACTICE: Log notification failures but don't retry forever
        logger.error(f"Failed to send completion notification: {e}")


@huey.task()
def check_promise_dependencies(promise_id: int):
    """
    Check if promise dependencies are satisfied and execute if ready.
    
    DESIGN PATTERN: Simple dependency checking via polling.
    For v1, we poll every 30 seconds. Future versions could use
    signals or callbacks for immediate execution.
    """
    promise = get_promise(promise_id)
    if not promise or promise['status'] != 'waiting':
        return
    
    # Check parent promises
    parent_ids = json.loads(promise.get('parent_promise_ids') or '[]')
    if not parent_ids:
        # No dependencies, execute immediately
        update_promise_status(promise_id, 'pending')
        execute_promise_by_type.schedule(args=(promise_id,))
        return
    
    # Check if all parents are completed
    all_completed = True
    for parent_id in parent_ids:
        parent = get_promise(parent_id)
        if not parent or parent['status'] != 'completed':
            all_completed = False
            break
    
    if all_completed:
        # Dependencies satisfied, execute
        logger.info(f"Dependencies satisfied for promise {promise_id}")
        update_promise_status(promise_id, 'pending')
        execute_promise_by_type.schedule(args=(promise_id,))
    else:
        # Check again in 30 seconds
        # IMPLEMENTATION NOTE: Exponential backoff could be added here
        check_promise_dependencies.schedule(
            args=(promise_id,),
            delay=30
        )


@huey.task()
def execute_promise_by_type(promise_id: int):
    """
    Route promise to appropriate execution task based on type.
    
    BEST PRACTICE: Use a routing function to keep task selection
    logic centralized and easy to extend.
    """
    logger.info(f"Execute promise by type called for promise {promise_id}")
    promise = get_promise(promise_id)
    if not promise:
        logger.error(f"Promise {promise_id} not found")
        return
    
    logger.info(f"Promise {promise_id} details: type={promise.get('task_type')}, status={promise.get('status')}, description={promise.get('task_description')[:50]}...")
    
    # Route based on task type
    # IMPLEMENTATION NOTE: Add new task types here as needed
    task_map = {
        'code': execute_coding_task,
        'search': execute_search_task,
        'analysis': execute_analysis_task,
    }
    
    task_func = task_map.get(promise['task_type'])
    if task_func:
        logger.info(f"Routing promise {promise_id} to {task_func.__name__}")
        # Schedule the task instead of calling directly with delay=0 for immediate execution
        result = task_func.schedule(args=(promise_id,), delay=0)
        logger.info(f"Scheduled task {task_func.__name__} for promise {promise_id}, Huey task ID: {getattr(result, 'id', 'unknown')}")
    else:
        logger.error(f"Unknown task type: {promise['task_type']}")
        update_promise_status(promise_id, 'failed', error_message=f"Unknown task type: {promise['task_type']}")


# BEST PRACTICE: Periodic cleanup tasks
@huey.periodic_task(crontab(minute='*/30'))
def cleanup_old_promises():
    """
    Clean up old completed/failed promises.
    
    IMPLEMENTATION NOTE: Keeps last 7 days of history for debugging.
    Adjust retention period based on your needs.
    """
    cutoff_date = datetime.utcnow() - timedelta(days=7)
    
    with get_database_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM promises 
            WHERE status IN ('completed', 'failed') 
            AND completed_at < ?
        """, (cutoff_date,))
        
        deleted_count = cursor.rowcount
        conn.commit()
        
    if deleted_count > 0:
        logger.info(f"Cleaned up {deleted_count} old promises")


@huey.periodic_task(crontab(minute='*/5'))
def resume_stalled_promises():
    """
    Resume promises that got stuck (e.g., due to restart).
    
    BEST PRACTICE: Always have a recovery mechanism for
    tasks that might get orphaned during restarts.
    """
    # Find promises marked as in_progress for too long
    stalled_cutoff = datetime.utcnow() - timedelta(minutes=30)
    
    with get_database_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM promises 
            WHERE status = 'in_progress' 
            AND created_at < ?
        """, (stalled_cutoff,))
        
        stalled_promises = cursor.fetchall()
    
    for (promise_id,) in stalled_promises:
        logger.warning(f"Resuming stalled promise {promise_id}")
        # Reset to pending
        update_promise_status(promise_id, 'pending')
        execute_promise_by_type.schedule(args=(promise_id,))


def format_duration(start_time: str, end_time: str) -> str:
    """Format duration between two timestamps."""
    if not start_time or not end_time:
        return "unknown duration"
    
    try:
        start = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        end = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
        duration = end - start
        
        if duration.total_seconds() < 60:
            return f"{int(duration.total_seconds())} seconds"
        elif duration.total_seconds() < 3600:
            return f"{int(duration.total_seconds() / 60)} minutes"
        else:
            return f"{int(duration.total_seconds() / 3600)} hours"
    except:
        return "unknown duration"