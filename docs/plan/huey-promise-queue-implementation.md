# Huey-Based Promise & Message Queue Implementation Guide

## Executive Summary

This document provides a comprehensive implementation guide for our promise and message queue system using **Huey** - a lightweight task queue with native SQLite support. This approach maintains our SQLite-only architecture while providing robust background task execution, parallel processing, and restart recovery.

## Architecture Principles

### Core Design Principles
1. **SQLite-Only**: No Redis/RabbitMQ dependencies for maximum portability
2. **Comprehensive Documentation**: Every function includes implementation notes for future developers
3. **Standard Patterns**: Consistent error handling, logging, and task design
4. **Graceful Degradation**: System continues functioning even if task queue fails
5. **Idempotent Tasks**: All tasks can be safely retried without side effects

### Why Huey Over Alternatives
- **Native SQLite support** via `SqliteHuey` - no additional services required
- **Simpler than Celery** - fewer moving parts, easier debugging
- **Production-ready** - battle-tested in real applications
- **Well-documented** - Claude and other LLMs understand it well
- **Lightweight** - ~2K lines of focused code vs Celery's complexity

## Implementation Architecture

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────────┐
│ Telegram Bot    │────▶│ SQLite DB    │────▶│ Huey Consumer   │
│ (Message Handler)│     │ (Task Queue) │     │ (Task Execution)│
└─────────────────┘     └──────────────┘     └─────────────────┘
         │                       │                      │
         │                       │                      │
         ▼                       ▼                      ▼
┌─────────────────┐     ┌──────────────┐     ┌─────────────────┐
│ Promise Manager │     │ Tasks DB     │     │ Task Results    │
│ (Task Creation) │     │ (huey.db)    │     │ (Completion Msgs)│
└─────────────────┘     └──────────────┘     └─────────────────┘
```

## Database Schema

### Enhanced Promises Table
```sql
-- Promise tracking with Huey integration
-- BEST PRACTICE: Always include comprehensive comments in schema
CREATE TABLE IF NOT EXISTS promises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Telegram context
    chat_id INTEGER NOT NULL,          -- Telegram chat ID for responses
    message_id INTEGER NOT NULL,       -- Original message that created promise
    user_id INTEGER,                   -- Telegram user ID
    username TEXT,                     -- Telegram username for logging
    
    -- Promise details
    task_description TEXT NOT NULL,    -- Human-readable task description
    task_type TEXT NOT NULL,           -- 'code', 'search', 'analysis', etc.
    
    -- Huey integration
    huey_task_id TEXT UNIQUE,         -- Huey task UUID for tracking
    
    -- Dependencies (simple approach)
    -- IMPLEMENTATION NOTE: For v1, we use status checks instead of 
    -- complex dependency graphs. Future versions can enhance this.
    parent_promise_ids TEXT,          -- JSON array of promise IDs to wait for
    
    -- Status tracking
    status TEXT DEFAULT 'pending',     -- pending|waiting|in_progress|completed|failed
    priority INTEGER DEFAULT 5,        -- 1 (highest) to 10 (lowest)
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,              -- When task execution began
    completed_at TIMESTAMP,            -- When task finished (success or failure)
    
    -- Results
    result_summary TEXT,               -- Brief result for user notification
    error_message TEXT,                -- Error details if failed
    retry_count INTEGER DEFAULT 0,     -- Number of retry attempts
    
    -- Flexible metadata
    metadata TEXT                      -- JSON blob for task-specific data
);

-- Performance indexes
CREATE INDEX idx_promises_status ON promises(status);
CREATE INDEX idx_promises_chat_id ON promises(chat_id);
CREATE INDEX idx_promises_huey_task_id ON promises(huey_task_id);
CREATE INDEX idx_promises_created_at ON promises(created_at);

-- Message queue for missed/scheduled messages
CREATE TABLE IF NOT EXISTS message_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Message context
    chat_id INTEGER NOT NULL,
    message_id INTEGER,
    message_text TEXT NOT NULL,
    message_type TEXT NOT NULL,        -- 'missed'|'scheduled'|'followup'
    sender_username TEXT,
    original_timestamp TIMESTAMP,      -- When message was originally sent
    
    -- Processing status
    status TEXT DEFAULT 'pending',     -- pending|processing|completed|failed
    processed_at TIMESTAMP,
    error_message TEXT,
    
    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata TEXT                      -- JSON blob with full message data
);

CREATE INDEX idx_message_queue_status ON message_queue(status);
CREATE INDEX idx_message_queue_chat_id ON message_queue(chat_id);
```

## Huey Configuration

### `tasks/huey_config.py`
```python
"""
Huey task queue configuration.

BEST PRACTICE: Centralize all Huey configuration in one place.
This makes it easy to adjust settings without hunting through code.
"""
import os
from huey import SqliteHuey

# IMPLEMENTATION NOTE: Use environment variables for production flexibility
HUEY_DB_PATH = os.environ.get('HUEY_DB_PATH', 'data/huey.db')
HUEY_IMMEDIATE = os.environ.get('HUEY_IMMEDIATE', 'false').lower() == 'true'

# Create Huey instance with SQLite backend
# BEST PRACTICE: Name your app clearly - it appears in logs
huey = SqliteHuey(
    'valor-bot',
    filename=HUEY_DB_PATH,
    
    # CRITICAL: Set immediate=False in production
    # immediate=True makes tasks run synchronously (good for testing)
    immediate=HUEY_IMMEDIATE,
    
    # BEST PRACTICE: Configure these for production stability
    # These settings prevent runaway tasks and ensure cleanup
    results=True,           # Store task results
    store_none=False,       # Don't store None results (saves space)
    utc=True,              # Always use UTC for timestamps
    
    # Connection settings for SQLite
    timeout=10.0,          # Connection timeout in seconds
    
    # Task expiration (results cleaned up after 1 week)
    result_expire=604800,  # 7 days in seconds
)

# IMPLEMENTATION NOTE: Import tasks here to register them with Huey
# This ensures all tasks are discovered when the consumer starts
from . import telegram_tasks
from . import promise_tasks
```

## Task Implementations

### `tasks/promise_tasks.py`
```python
"""
Promise execution tasks using Huey.

DESIGN PATTERN: Each task should be:
1. Idempotent - safe to retry
2. Atomic - completes fully or fails cleanly
3. Logged - comprehensive logging for debugging
"""
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from huey import crontab
from .huey_config import huey
from utilities.database import (
    get_promise, update_promise_status, complete_promise,
    fail_promise, get_pending_promises
)
from tools.valor_delegation_tool import delegate_to_claude_code
from integrations.telegram.client import send_telegram_message

logger = logging.getLogger(__name__)

# BEST PRACTICE: Use decorators for common patterns
def with_promise_tracking(func):
    """
    Decorator that handles promise status updates and error tracking.
    
    IMPLEMENTATION NOTE: This pattern ensures consistent status
    updates across all promise-executing tasks.
    """
    async def wrapper(promise_id: int, *args, **kwargs):
        try:
            # Mark as in_progress
            update_promise_status(promise_id, 'in_progress')
            logger.info(f"Starting promise {promise_id}: {func.__name__}")
            
            # Execute the actual task
            result = await func(promise_id, *args, **kwargs)
            
            # Mark as completed
            complete_promise(promise_id, result)
            logger.info(f"Completed promise {promise_id}")
            
            return result
            
        except Exception as e:
            logger.error(f"Failed promise {promise_id}: {str(e)}", exc_info=True)
            fail_promise(promise_id, str(e))
            raise  # Re-raise for Huey retry mechanism
            
    return wrapper


@huey.task(retries=3, retry_delay=60)
@with_promise_tracking
async def execute_coding_task(promise_id: int) -> str:
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
    metadata = json.loads(promise.metadata or '{}')
    
    # IMPLEMENTATION NOTE: Delegate to Claude Code with full context
    # The delegation tool handles the actual code execution
    result = await delegate_to_claude_code(
        task_description=promise.task_description,
        target_directory=metadata.get('target_directory', ''),
        specific_instructions=metadata.get('instructions', ''),
        context={
            'chat_id': promise.chat_id,
            'username': promise.username,
            'original_request': metadata.get('original_text', '')
        }
    )
    
    # Send completion notification
    # BEST PRACTICE: Use .schedule() for follow-up tasks
    send_completion_notification.schedule(
        args=(promise_id, result),
        delay=1  # Small delay to ensure DB updates are committed
    )
    
    return result


@huey.task(retries=2, retry_delay=30)
async def send_completion_notification(promise_id: int, result: str):
    """
    Send completion message to user via Telegram.
    
    IMPLEMENTATION NOTE: This is a separate task so notification
    failures don't affect the main task completion status.
    """
    promise = get_promise(promise_id)
    if not promise:
        logger.error(f"Promise {promise_id} not found for notification")
        return
    
    # Format completion message
    # BEST PRACTICE: Make messages informative but concise
    message = f"""✅ **Task Complete!**

I finished working on: {promise.task_description[:100]}{'...' if len(promise.task_description) > 100 else ''}

**Result:**
{result[:500]}{'...' if len(result) > 500 else ''}

_Completed in {format_duration(promise.created_at, promise.completed_at)}_
"""
    
    try:
        await send_telegram_message(
            chat_id=promise.chat_id,
            text=message,
            reply_to_message_id=promise.message_id
        )
    except Exception as e:
        # BEST PRACTICE: Log notification failures but don't retry forever
        logger.error(f"Failed to send completion notification: {e}")


@huey.task()
async def check_promise_dependencies(promise_id: int):
    """
    Check if promise dependencies are satisfied and execute if ready.
    
    DESIGN PATTERN: Simple dependency checking via polling.
    For v1, we poll every 30 seconds. Future versions could use
    signals or callbacks for immediate execution.
    """
    promise = get_promise(promise_id)
    if not promise or promise.status != 'waiting':
        return
    
    # Check parent promises
    parent_ids = json.loads(promise.parent_promise_ids or '[]')
    if not parent_ids:
        # No dependencies, execute immediately
        update_promise_status(promise_id, 'pending')
        execute_promise_by_type.schedule(args=(promise_id,))
        return
    
    # Check if all parents are completed
    all_completed = True
    for parent_id in parent_ids:
        parent = get_promise(parent_id)
        if not parent or parent.status != 'completed':
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
    promise = get_promise(promise_id)
    if not promise:
        logger.error(f"Promise {promise_id} not found")
        return
    
    # Route based on task type
    # IMPLEMENTATION NOTE: Add new task types here as needed
    task_map = {
        'code': execute_coding_task,
        'search': execute_search_task,
        'analysis': execute_analysis_task,
    }
    
    task_func = task_map.get(promise.task_type)
    if task_func:
        task_func(promise_id)
    else:
        logger.error(f"Unknown task type: {promise.task_type}")
        fail_promise(promise_id, f"Unknown task type: {promise.task_type}")


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
            AND started_at < ?
            AND retry_count < 3
        """, (stalled_cutoff,))
        
        stalled_promises = cursor.fetchall()
    
    for (promise_id,) in stalled_promises:
        logger.warning(f"Resuming stalled promise {promise_id}")
        # Reset to pending and increment retry count
        update_promise_status(promise_id, 'pending')
        execute_promise_by_type.schedule(args=(promise_id,))
```

### `tasks/telegram_tasks.py`
```python
"""
Telegram-specific tasks for message processing.

DESIGN PRINCIPLE: Keep Telegram logic separate from promise logic
for better modularity and testing.
"""
import json
import logging
from datetime import datetime

from .huey_config import huey
from utilities.database import get_database_connection
from integrations.telegram.handlers import process_message_with_agent

logger = logging.getLogger(__name__)


@huey.task(retries=2, retry_delay=60)
async def process_missed_message(message_id: int):
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
        await process_message_with_agent(
            chat_id=chat_id,
            text=text,
            username=username,
            is_missed_message=True,  # Flag for special handling
            original_timestamp=metadata.get('timestamp')
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
```

## Integration with Main Application

### `utilities/promise_manager_huey.py`
```python
"""
Promise management with Huey integration.

DESIGN PRINCIPLE: This manager provides a clean interface between
the main application and the Huey task queue.
"""
import json
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from tasks.huey_config import huey
from tasks.promise_tasks import (
    execute_promise_by_type, 
    check_promise_dependencies
)
from .database import get_database_connection

logger = logging.getLogger(__name__)


class HueyPromiseManager:
    """
    Manages promise lifecycle with Huey task queue.
    
    BEST PRACTICE: Encapsulate task queue details behind a
    clean interface. The rest of the app shouldn't need to
    know about Huey specifics.
    """
    
    def __init__(self):
        """Initialize the promise manager."""
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    def create_promise(
        self,
        chat_id: int,
        message_id: int,
        task_description: str,
        task_type: str,
        username: Optional[str] = None,
        user_id: Optional[int] = None,
        priority: int = 5,
        metadata: Optional[Dict[str, Any]] = None,
        dependencies: Optional[List[int]] = None
    ) -> int:
        """
        Create a new promise and queue it for execution.
        
        IMPLEMENTATION NOTE: This method handles both simple promises
        and promises with dependencies. The task queue determines
        when to actually execute based on dependency status.
        
        Args:
            chat_id: Telegram chat ID for responses
            message_id: Original message that triggered this promise
            task_description: Human-readable description
            task_type: Type of task ('code', 'search', etc.)
            username: Telegram username for logging
            user_id: Telegram user ID
            priority: 1 (highest) to 10 (lowest)
            metadata: Additional task-specific data
            dependencies: List of promise IDs that must complete first
            
        Returns:
            Promise ID from database
        """
        # Validate inputs
        if task_type not in ['code', 'search', 'analysis']:
            raise ValueError(f"Invalid task type: {task_type}")
        
        # Determine initial status
        status = 'waiting' if dependencies else 'pending'
        
        # Create promise in database
        with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO promises (
                    chat_id, message_id, user_id, username,
                    task_description, task_type, status, priority,
                    parent_promise_ids, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                chat_id, message_id, user_id, username,
                task_description, task_type, status, priority,
                json.dumps(dependencies) if dependencies else None,
                json.dumps(metadata) if metadata else None
            ))
            
            promise_id = cursor.lastrowid
            conn.commit()
        
        self.logger.info(
            f"Created promise {promise_id}: {task_type} - "
            f"{task_description[:50]}... (status: {status})"
        )
        
        # Queue for execution
        if dependencies:
            # Schedule dependency check
            # BEST PRACTICE: Use delay to avoid race conditions
            check_promise_dependencies.schedule(
                args=(promise_id,),
                delay=2
            )
        else:
            # Execute immediately
            result = execute_promise_by_type(promise_id)
            
            # Store Huey task ID
            if hasattr(result, 'id'):
                self._update_huey_task_id(promise_id, result.id)
        
        return promise_id
    
    def create_parallel_promises(
        self,
        chat_id: int,
        message_id: int,
        tasks: List[Dict[str, Any]],
        username: Optional[str] = None,
        user_id: Optional[int] = None
    ) -> List[int]:
        """
        Create multiple promises that execute in parallel.
        
        BEST PRACTICE: Batch database operations when creating
        multiple related items.
        
        Example:
            tasks = [
                {'description': 'Review auth.py', 'type': 'code'},
                {'description': 'Review api.py', 'type': 'code'},
                {'description': 'Review db.py', 'type': 'code'}
            ]
        """
        promise_ids = []
        
        for task in tasks:
            promise_id = self.create_promise(
                chat_id=chat_id,
                message_id=message_id,
                task_description=task['description'],
                task_type=task['type'],
                username=username,
                user_id=user_id,
                priority=task.get('priority', 5),
                metadata=task.get('metadata')
            )
            promise_ids.append(promise_id)
        
        self.logger.info(f"Created {len(promise_ids)} parallel promises")
        return promise_ids
    
    def create_dependent_promises(
        self,
        chat_id: int,
        message_id: int,
        tasks: List[Dict[str, Any]],
        dependency_map: Dict[str, List[str]],
        username: Optional[str] = None,
        user_id: Optional[int] = None
    ) -> Dict[str, int]:
        """
        Create promises with dependencies between them.
        
        IMPLEMENTATION NOTE: This uses a simple dependency model where
        promises wait for specific other promises to complete.
        
        Example:
            tasks = [
                {'name': 'setup', 'description': 'Set up environment', 'type': 'code'},
                {'name': 'test', 'description': 'Write tests', 'type': 'code'},
                {'name': 'run', 'description': 'Run tests', 'type': 'code'}
            ]
            dependency_map = {
                'test': ['setup'],  # test depends on setup
                'run': ['test']     # run depends on test
            }
        """
        # First pass: Create all promises
        name_to_id = {}
        
        for task in tasks:
            task_name = task['name']
            dependencies = None
            
            # Check if this task has dependencies
            if task_name in dependency_map:
                dep_names = dependency_map[task_name]
                # Convert dependency names to IDs (if already created)
                dependencies = [
                    name_to_id[dep_name] 
                    for dep_name in dep_names 
                    if dep_name in name_to_id
                ]
            
            promise_id = self.create_promise(
                chat_id=chat_id,
                message_id=message_id,
                task_description=task['description'],
                task_type=task['type'],
                username=username,
                user_id=user_id,
                priority=task.get('priority', 5),
                metadata=task.get('metadata'),
                dependencies=dependencies if dependencies else None
            )
            
            name_to_id[task_name] = promise_id
        
        self.logger.info(
            f"Created {len(name_to_id)} promises with dependencies: "
            f"{list(name_to_id.keys())}"
        )
        
        return name_to_id
    
    def resume_pending_promises(self):
        """
        Resume all pending promises after restart.
        
        BEST PRACTICE: Call this on application startup to
        recover from crashes or restarts.
        """
        with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, task_type, status 
                FROM promises 
                WHERE status IN ('pending', 'waiting', 'in_progress')
                ORDER BY priority ASC, created_at ASC
            """)
            
            pending_promises = cursor.fetchall()
        
        resumed_count = 0
        
        for promise_id, task_type, status in pending_promises:
            if status == 'waiting':
                # Check dependencies
                check_promise_dependencies.schedule(args=(promise_id,))
            else:
                # Execute directly
                execute_promise_by_type.schedule(args=(promise_id,))
            
            resumed_count += 1
        
        if resumed_count > 0:
            self.logger.info(f"Resumed {resumed_count} pending promises")
        
        return resumed_count
    
    def _update_huey_task_id(self, promise_id: int, task_id: str):
        """Store Huey task ID for tracking."""
        with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE promises 
                SET huey_task_id = ? 
                WHERE id = ?
            """, (task_id, promise_id))
            conn.commit()
```

### `integrations/telegram/handlers.py` (Modified)
```python
# Add to the message handler

async def handle_message(self, client, message):
    """
    Handle incoming Telegram message.
    
    IMPLEMENTATION NOTE: Check for promise triggers and create
    background tasks when appropriate.
    """
    # ... existing message handling code ...
    
    # Check if response indicates a promise
    if self._should_create_promise(answer, intent_result):
        # Create promise for background execution
        promise_id = await self._create_promise_from_response(
            client, message, chat_id, answer, processed_text
        )
        
        # Send immediate acknowledgment
        # BEST PRACTICE: Always acknowledge promise creation
        if promise_id:
            ack_message = "I'll work on that task and follow up when complete."
            await client.send_message(chat_id, ack_message)

def _should_create_promise(self, response: str, intent: dict) -> bool:
    """
    Determine if response indicates a background task.
    
    IMPLEMENTATION NOTE: Start simple with explicit markers,
    then enhance with AI-based detection.
    """
    # Check for explicit promise marker
    if "ASYNC_PROMISE|" in response:
        return True
    
    # Check task duration estimate
    if intent.get('estimated_duration', 0) > 30:  # seconds
        return True
    
    # Check for promise-indicating phrases
    promise_phrases = [
        "i'll work on",
        "let me handle",
        "i'll implement",
        "i'll analyze",
        "working on this in the background"
    ]
    
    response_lower = response.lower()
    return any(phrase in response_lower for phrase in promise_phrases)

async def _create_promise_from_response(
    self,
    client,
    message,
    chat_id: int,
    response: str,
    original_text: str
) -> Optional[int]:
    """
    Extract promise details and create background task.
    
    BEST PRACTICE: Parse response carefully to extract
    task details for proper promise creation.
    """
    # Parse task details from response
    task_type = 'code'  # Default, could be enhanced with classification
    
    if "ASYNC_PROMISE|" in response:
        # Explicit promise with details
        parts = response.split("ASYNC_PROMISE|", 1)
        if len(parts) == 2:
            task_description = parts[1].split("|")[0]
    else:
        # Infer from response
        task_description = original_text[:200]
    
    # Create promise
    promise_manager = HueyPromiseManager()
    promise_id = promise_manager.create_promise(
        chat_id=chat_id,
        message_id=message.id,
        task_description=task_description,
        task_type=task_type,
        username=message.from_user.username,
        user_id=message.from_user.id,
        metadata={
            'original_text': original_text,
            'response': response,
            'timestamp': message.date.timestamp()
        }
    )
    
    return promise_id
```

## Consumer Setup and Management

### `huey_consumer.py`
```python
#!/usr/bin/env python
"""
Huey consumer entry point.

USAGE:
    python huey_consumer.py tasks.huey_config.huey -w 4 -k thread
    
OPTIONS:
    -w: Number of workers (default: 1)
    -k: Worker type: thread, process, greenlet (default: thread)
    
BEST PRACTICE: Use threads for I/O-bound tasks (like ours),
processes for CPU-bound tasks.
"""
import logging
import sys
from huey.consumer import Consumer
from tasks.huey_config import huey

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

if __name__ == '__main__':
    # IMPLEMENTATION NOTE: The consumer handles all the complex
    # bits of task execution, retries, and scheduling.
    consumer = Consumer(huey)
    consumer.run()
```

### `scripts/start_huey.sh`
```bash
#!/bin/bash
# Start Huey consumer with production settings

# BEST PRACTICE: Use environment variables for configuration
export HUEY_DB_PATH="data/huey.db"
export HUEY_IMMEDIATE="false"

# Ensure data directory exists
mkdir -p data

# Start consumer with 4 threads
# IMPLEMENTATION NOTE: Adjust worker count based on load
python huey_consumer.py tasks.huey_config.huey \
    -w 4 \
    -k thread \
    -l logs/huey.log \
    -v
```

## Testing Strategy

### `tests/test_huey_promises.py`
```python
"""
Test promise execution with Huey.

BEST PRACTICE: Test both immediate mode (synchronous) and
normal mode (asynchronous) to ensure tasks work correctly.
"""
import pytest
from unittest.mock import patch, MagicMock

from tasks.huey_config import huey
from utilities.promise_manager_huey import HueyPromiseManager


class TestHueyPromises:
    """Test promise creation and execution."""
    
    @pytest.fixture
    def immediate_mode(self):
        """Enable immediate mode for synchronous testing."""
        # IMPLEMENTATION NOTE: Immediate mode makes tasks run
        # synchronously, perfect for unit tests
        original = huey.immediate
        huey.immediate = True
        yield
        huey.immediate = original
    
    def test_create_simple_promise(self, immediate_mode):
        """Test creating and executing a simple promise."""
        manager = HueyPromiseManager()
        
        with patch('tasks.promise_tasks.delegate_to_claude_code') as mock_delegate:
            mock_delegate.return_value = "Task completed successfully"
            
            promise_id = manager.create_promise(
                chat_id=12345,
                message_id=67890,
                task_description="Fix authentication bug",
                task_type="code",
                username="testuser"
            )
            
            # In immediate mode, task executes synchronously
            assert promise_id is not None
            mock_delegate.assert_called_once()
    
    def test_parallel_promises(self, immediate_mode):
        """Test creating multiple parallel promises."""
        manager = HueyPromiseManager()
        
        tasks = [
            {'description': 'Review file1.py', 'type': 'code'},
            {'description': 'Review file2.py', 'type': 'code'},
            {'description': 'Review file3.py', 'type': 'code'}
        ]
        
        promise_ids = manager.create_parallel_promises(
            chat_id=12345,
            message_id=67890,
            tasks=tasks
        )
        
        assert len(promise_ids) == 3
    
    def test_dependent_promises(self):
        """Test promises with dependencies."""
        manager = HueyPromiseManager()
        
        tasks = [
            {'name': 'setup', 'description': 'Set up env', 'type': 'code'},
            {'name': 'test', 'description': 'Write tests', 'type': 'code'}
        ]
        
        dependency_map = {
            'test': ['setup']  # test depends on setup
        }
        
        name_to_id = manager.create_dependent_promises(
            chat_id=12345,
            message_id=67890,
            tasks=tasks,
            dependency_map=dependency_map
        )
        
        assert 'setup' in name_to_id
        assert 'test' in name_to_id
        
        # Verify test promise is in 'waiting' status
        test_promise = get_promise(name_to_id['test'])
        assert test_promise.status == 'waiting'
```

## Deployment Checklist

### Initial Setup
- [ ] Install Huey: `pip install huey`
- [ ] Create data directory: `mkdir -p data`
- [ ] Run database migrations to add promise tables
- [ ] Configure logging directory: `mkdir -p logs`

### Start Services
- [ ] Start main application: `scripts/start.sh`
- [ ] Start Huey consumer: `scripts/start_huey.sh`
- [ ] Verify consumer is processing: `tail -f logs/huey.log`

### Monitor Health
- [ ] Check promise creation: `SELECT COUNT(*) FROM promises;`
- [ ] Monitor task execution: `SELECT status, COUNT(*) FROM promises GROUP BY status;`
- [ ] Watch for errors: `grep ERROR logs/huey.log`

### Production Considerations
- [ ] Set up process supervisor (systemd/supervisor) for consumer
- [ ] Configure log rotation for Huey logs
- [ ] Monitor disk space for SQLite databases
- [ ] Set up alerts for failed promises
- [ ] Plan for consumer scaling if needed

## Troubleshooting Guide

### Common Issues

1. **Tasks not executing**
   - Check consumer is running: `ps aux | grep huey`
   - Verify immediate mode is False in production
   - Check database permissions

2. **Duplicate task execution**
   - Ensure only one consumer instance per database
   - Check task idempotency implementation

3. **Memory growth**
   - Monitor result expiration settings
   - Check for task result cleanup
   - Verify old promises are being cleaned up

4. **SQLite locking**
   - Use WAL mode for better concurrency
   - Keep transactions short
   - Consider connection pooling settings

### Debug Commands

```bash
# Check pending promises
sqlite3 system.db "SELECT id, task_description, status FROM promises WHERE status='pending';"

# View recent errors
sqlite3 system.db "SELECT task_description, error_message FROM promises WHERE status='failed' ORDER BY completed_at DESC LIMIT 10;"

# Monitor task throughput
watch -n 5 'sqlite3 huey.db "SELECT COUNT(*) FROM huey_task;"'
```

## Future Enhancements

### Version 2.0 Ideas
1. **Advanced Dependencies**: Graph-based dependency resolution
2. **Priority Queues**: Multiple queues for different priority levels
3. **Progress Tracking**: Real-time progress updates for long tasks
4. **Task Cancellation**: Allow users to cancel pending promises
5. **Distributed Execution**: Multiple consumer instances with coordination

### Scaling Considerations
- **PostgreSQL Migration**: When SQLite limits are reached
- **Redis Integration**: For high-throughput scenarios
- **Kubernetes Deployment**: For cloud-native scaling
- **Monitoring Dashboard**: Grafana/Prometheus integration

## Conclusion

This Huey-based implementation provides a robust, portable solution for promise and message queue management while maintaining our SQLite-only architecture. The comprehensive documentation and best practices ensure future developers can understand and extend the system effectively.

Remember: **Always document your implementation decisions** - your future self and teammates will thank you!