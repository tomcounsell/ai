# Unified Promise & Message Queue Architecture

## Executive Summary

This document proposes a unified architecture that solves two critical issues:

1. **Missed Messages Bug**: The current logic incorrectly filters messages, making it impossible to catch messages sent while the bot was offline
2. **Promise System Enhancement**: Expanding our promise architecture to support parallel execution, dependencies, and restart recovery

Instead of building a custom solution, we'll leverage **Celery** - a battle-tested distributed task queue that provides all the features we need.

## Current Issues

### 1. Missed Messages Logic Bug

**Current Broken Logic:**
```python
# integrations/telegram/client.py, line 212-217
# Skip messages from before bot start time
if message.date.timestamp() < self.bot_start_time:
    continue
    
# Check if message is too old (using same 5-minute threshold)
if is_message_too_old(message.date.timestamp()):
    # This is a missed message
```

**The Problem**: This creates an impossible condition. Messages can't be both:
- After bot start time (recent)
- AND older than 5 minutes

**Correct Logic Should Be:**
```python
# Message is from before bot started AND within catchup window
if message.date.timestamp() < self.bot_start_time:
    # Check if within catchup window (last 5 minutes before startup)
    if message.date.timestamp() > (self.bot_start_time - MAX_MESSAGE_AGE_SECONDS):
        # This is a missed message we should catch up on
```

### 2. Promise System Limitations

Current promise system (as designed in the plan) lacks:
- ❌ Parallel promise execution
- ❌ Promise dependencies (Promise B waits for Promise A)
- ❌ Restart recovery (resume pending promises)
- ❌ Distributed execution capability
- ❌ Proper retry mechanisms with exponential backoff
- ❌ Task result storage and retrieval

## Proposed Solution: Celery-Based Task Queue

### Why Celery?

**Celery** is a distributed task queue that provides:
- ✅ **Parallel execution** with worker pools
- ✅ **Task dependencies** via chains, groups, and chords
- ✅ **Persistence** with result backends
- ✅ **Restart recovery** - tasks survive worker restarts
- ✅ **Retry mechanisms** with exponential backoff
- ✅ **Task routing** and priority queues
- ✅ **Monitoring** via Flower web interface
- ✅ **Production-ready** - used by Instagram, Mozilla, etc.

### Architecture Overview

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────────┐
│ Telegram Bot    │────▶│ Redis/SQLite │────▶│ Celery Workers  │
│ (Message Handler)│     │ (Task Queue) │     │ (Task Execution)│
└─────────────────┘     └──────────────┘     └─────────────────┘
         │                       │                      │
         │                       │                      │
         ▼                       ▼                      ▼
┌─────────────────┐     ┌──────────────┐     ┌─────────────────┐
│ Promise Manager │     │ Result Store │     │ Task Results    │
│ (Task Creation) │     │ (SQLite DB)  │     │ (Completion Msgs)│
└─────────────────┘     └──────────────┘     └─────────────────┘
```

### Implementation Design

#### 1. Enhanced Database Schema

```sql
-- Enhanced promises table with Celery integration
CREATE TABLE IF NOT EXISTS promises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    user_id INTEGER,
    username TEXT,
    task_description TEXT NOT NULL,
    task_type TEXT NOT NULL,
    celery_task_id TEXT UNIQUE,  -- Celery task UUID
    parent_task_ids TEXT,         -- JSON array of dependency task IDs
    status TEXT DEFAULT 'pending',
    priority INTEGER DEFAULT 5,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    result_summary TEXT,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    metadata TEXT                 -- JSON blob
);

-- Message queue for missed messages (unified with promises)
CREATE TABLE IF NOT EXISTS message_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    message_id INTEGER,
    message_text TEXT NOT NULL,
    message_type TEXT NOT NULL,   -- 'missed', 'scheduled', 'followup'
    sender_username TEXT,
    original_timestamp TIMESTAMP,
    status TEXT DEFAULT 'pending',
    processed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata TEXT                 -- JSON blob with full message data
);

CREATE INDEX idx_promises_celery_task_id ON promises(celery_task_id);
CREATE INDEX idx_promises_parent_tasks ON promises(parent_task_ids);
CREATE INDEX idx_message_queue_status ON message_queue(status);
CREATE INDEX idx_message_queue_chat_id ON message_queue(chat_id);
```

#### 2. Celery Task Definitions

```python
# tasks/telegram_tasks.py
from celery import Celery, group, chain, chord
from celery.result import AsyncResult
import json

app = Celery('valor_bot')
app.config_from_object('celeryconfig')

@app.task(bind=True, max_retries=3)
def execute_coding_task(self, promise_id: int, task_data: dict):
    """Execute a coding task with Claude Code."""
    try:
        promise = get_promise(promise_id)
        update_promise_status(promise_id, 'in_progress')
        
        # Execute using Claude Code
        result = await delegate_to_claude_code(
            task_data['description'],
            task_data['target_directory'],
            task_data['instructions']
        )
        
        # Update promise with result
        update_promise_status(promise_id, 'completed', result)
        
        # Send completion message
        send_completion_message.delay(promise_id, result)
        
        return result
        
    except Exception as exc:
        # Retry with exponential backoff
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

@app.task
def process_missed_message(message_data: dict):
    """Process a missed message asynchronously."""
    chat_id = message_data['chat_id']
    text = message_data['text']
    
    # Process through normal agent flow
    response = await process_with_agent(chat_id, text)
    
    # Send response
    await send_telegram_message(chat_id, response)
    
    return response

@app.task
def send_completion_message(promise_id: int, result: str):
    """Send completion notification to user."""
    promise = get_promise(promise_id)
    
    message = format_completion_message(promise, result)
    await send_telegram_message(promise.chat_id, message)
    
    return True
```

#### 3. Promise Manager with Dependencies

```python
# utilities/promise_manager_celery.py
from celery import group, chain, chord
from typing import List, Optional

class CeleryPromiseManager:
    """Manages promises with Celery task queue."""
    
    def create_promise_with_dependencies(
        self,
        chat_id: int,
        task_description: str,
        task_type: str,
        dependencies: Optional[List[int]] = None,
        parallel_tasks: Optional[List[dict]] = None
    ) -> int:
        """Create a promise that may depend on other promises."""
        
        # Create promise in database
        promise_id = create_promise_in_db(
            chat_id=chat_id,
            task_description=task_description,
            task_type=task_type,
            parent_task_ids=json.dumps(dependencies) if dependencies else None
        )
        
        # Build Celery task
        if parallel_tasks:
            # Execute tasks in parallel
            task_group = group([
                self._create_celery_task(task) for task in parallel_tasks
            ])
            celery_result = task_group.apply_async()
            
        elif dependencies:
            # Chain tasks with dependencies
            task_chain = self._build_dependency_chain(promise_id, dependencies)
            celery_result = task_chain.apply_async()
            
        else:
            # Single independent task
            celery_task = self._create_celery_task_for_promise(promise_id)
            celery_result = celery_task.apply_async()
        
        # Store Celery task ID
        update_promise_celery_id(promise_id, celery_result.id)
        
        return promise_id
    
    def _build_dependency_chain(self, promise_id: int, dependencies: List[int]):
        """Build a Celery chain for dependent tasks."""
        tasks = []
        
        for dep_id in dependencies:
            dep_promise = get_promise(dep_id)
            if dep_promise.status != 'completed':
                # Wait for dependency
                tasks.append(wait_for_promise.s(dep_id))
        
        # Add main task
        tasks.append(self._create_celery_task_for_promise(promise_id))
        
        return chain(*tasks)
    
    def resume_pending_promises(self):
        """Resume all pending promises after restart."""
        pending_promises = get_pending_promises()
        
        for promise in pending_promises:
            if promise.celery_task_id:
                # Check if task is still in queue
                result = AsyncResult(promise.celery_task_id)
                if result.state in ['PENDING', 'RETRY']:
                    # Task will resume automatically
                    continue
                elif result.state == 'FAILURE':
                    # Retry failed task
                    self._retry_promise(promise.id)
            else:
                # No Celery task created yet, create it
                self._create_and_execute_promise(promise.id)
```

#### 4. Fixed Missed Messages Handler

```python
# integrations/telegram/client.py
async def _check_startup_missed_messages(self):
    """Check for messages sent while bot was offline."""
    if not self.client or not self.message_handler:
        return
    
    print("🔍 Checking for missed messages during startup...")
    
    catchup_window = 300  # 5 minutes
    catchup_start_time = self.bot_start_time - catchup_window
    
    missed_count = 0
    
    async for dialog in self.client.get_dialogs():
        chat = dialog.chat
        chat_id = chat.id
        
        # Check if we should handle this chat
        if not self._should_handle_chat(chat_id, chat.type):
            continue
        
        # Get recent messages
        async for message in self.client.get_chat_history(chat_id, limit=50):
            # Skip non-text messages
            if not message.text:
                continue
            
            # Check if message is in catchup window
            msg_time = message.date.timestamp()
            if catchup_start_time < msg_time < self.bot_start_time:
                # This is a missed message!
                missed_count += 1
                
                # Queue for processing
                queue_missed_message.delay({
                    'chat_id': chat_id,
                    'message_id': message.id,
                    'text': message.text,
                    'username': message.from_user.username,
                    'timestamp': msg_time
                })
                
                print(f"📬 Queued missed message from {chat_id}: {message.text[:50]}...")
    
    if missed_count > 0:
        print(f"✅ Queued {missed_count} missed messages for processing")
```

#### 5. Unified Message & Promise Queue

```python
# utilities/unified_queue_manager.py
class UnifiedQueueManager:
    """Manages both missed messages and promises in a unified queue."""
    
    def __init__(self):
        self.celery_app = Celery('valor_bot')
        
    def queue_missed_message(self, message_data: dict):
        """Queue a missed message for processing."""
        # Store in database
        queue_id = insert_message_queue(
            chat_id=message_data['chat_id'],
            message_text=message_data['text'],
            message_type='missed',
            metadata=json.dumps(message_data)
        )
        
        # Create Celery task
        task = process_missed_message.apply_async(
            args=[message_data],
            countdown=1  # Small delay to batch messages
        )
        
        return queue_id
    
    def create_promise_group(self, chat_id: int, tasks: List[dict]):
        """Create a group of parallel promises."""
        promise_ids = []
        celery_tasks = []
        
        for task in tasks:
            # Create promise in DB
            promise_id = create_promise_in_db(
                chat_id=chat_id,
                task_description=task['description'],
                task_type=task['type']
            )
            promise_ids.append(promise_id)
            
            # Create Celery task
            celery_task = self._task_to_celery(task, promise_id)
            celery_tasks.append(celery_task)
        
        # Execute in parallel
        job = group(celery_tasks).apply_async()
        
        # Update promises with Celery IDs
        for promise_id, result in zip(promise_ids, job.results):
            update_promise_celery_id(promise_id, result.id)
        
        return promise_ids
    
    def create_dependent_promises(
        self, 
        chat_id: int, 
        tasks: List[dict],
        dependency_map: dict
    ):
        """Create promises with dependencies.
        
        dependency_map example:
        {
            'task2': ['task1'],  # task2 depends on task1
            'task3': ['task1', 'task2']  # task3 depends on both
        }
        """
        # First, create all promises in DB
        task_name_to_id = {}
        for task in tasks:
            promise_id = create_promise_in_db(
                chat_id=chat_id,
                task_description=task['description'],
                task_type=task['type']
            )
            task_name_to_id[task['name']] = promise_id
        
        # Build dependency chains
        for task in tasks:
            task_name = task['name']
            promise_id = task_name_to_id[task_name]
            
            if task_name in dependency_map:
                # Has dependencies
                dep_names = dependency_map[task_name]
                dep_ids = [task_name_to_id[name] for name in dep_names]
                
                # Create chain
                chain_tasks = []
                for dep_id in dep_ids:
                    chain_tasks.append(wait_for_promise.s(dep_id))
                chain_tasks.append(self._task_to_celery(task, promise_id))
                
                result = chain(*chain_tasks).apply_async()
            else:
                # No dependencies, execute immediately
                result = self._task_to_celery(task, promise_id).apply_async()
            
            update_promise_celery_id(promise_id, result.id)
        
        return task_name_to_id
```

## Implementation Plan

### Phase 1: Fix Missed Messages (2 hours)

1. **Fix the logic bug** in `_check_startup_missed_messages()`
2. **Add message queue table** to database schema
3. **Implement simple processing** without Celery initially
4. **Test with various scenarios**

### Phase 2: Celery Infrastructure (3 hours)

1. **Install Celery** and Redis/SQLite backend
2. **Configure Celery** with our project structure
3. **Create base task definitions**
4. **Set up worker processes**
5. **Add Flower monitoring** (optional but recommended)

### Phase 3: Promise System Enhancement (4 hours)

1. **Update database schema** with Celery fields
2. **Implement CeleryPromiseManager**
3. **Create task types** (coding, search, analysis)
4. **Add dependency support**
5. **Implement restart recovery**

### Phase 4: Integration & Testing (3 hours)

1. **Integrate with message handlers**
2. **Update agent tools** to create promises
3. **Test parallel execution**
4. **Test dependency chains**
5. **Test restart recovery**
6. **Performance testing**

**Total: 12 hours**

## Configuration

### Celery Configuration

```python
# celeryconfig.py
from kombu import Queue, Exchange

# Broker settings (using Redis or SQLite)
broker_url = 'redis://localhost:6379/0'  # or 'sqla+sqlite:///celery.db'
result_backend = 'db+sqlite:///celery_results.db'

# Task settings
task_serializer = 'json'
result_serializer = 'json'
accept_content = ['json']
timezone = 'UTC'
enable_utc = True

# Task execution settings
task_track_started = True
task_time_limit = 3600  # 1 hour hard limit
task_soft_time_limit = 3000  # 50 min soft limit
task_acks_late = True  # Tasks not lost if worker dies

# Retry settings
task_default_retry_delay = 30
task_max_retries = 3

# Queue configuration
task_default_queue = 'default'
task_queues = (
    Queue('default', Exchange('default'), routing_key='default'),
    Queue('promises', Exchange('promises'), routing_key='promises'),
    Queue('messages', Exchange('messages'), routing_key='messages'),
)

# Route tasks to appropriate queues
task_routes = {
    'tasks.telegram_tasks.execute_coding_task': {'queue': 'promises'},
    'tasks.telegram_tasks.process_missed_message': {'queue': 'messages'},
}
```

### Starting Workers

```bash
# Start Celery worker
celery -A tasks.telegram_tasks worker --loglevel=info --concurrency=4

# Start Celery beat (for scheduled tasks)
celery -A tasks.telegram_tasks beat --loglevel=info

# Start Flower monitoring (optional)
celery -A tasks.telegram_tasks flower
```

## Benefits Over Custom Solution

### Reliability
- ✅ **Battle-tested** in production environments
- ✅ **Automatic retry** with exponential backoff
- ✅ **Task persistence** survives crashes
- ✅ **Distributed execution** if needed

### Features
- ✅ **Parallel execution** out of the box
- ✅ **Complex workflows** with chains, groups, chords
- ✅ **Task priorities** and routing
- ✅ **Result storage** and retrieval
- ✅ **Monitoring** via Flower web UI

### Development Speed
- ✅ **Less code to write** - Celery handles the hard parts
- ✅ **Well-documented** with extensive examples
- ✅ **Active community** for support
- ✅ **Plugin ecosystem** for extensions

## Example Workflows

### 1. Parallel Code Review

```python
# User: "Review all Python files in the project for security issues"

# Creates parallel promises:
promise_ids = queue_manager.create_promise_group(
    chat_id=chat_id,
    tasks=[
        {'type': 'code_review', 'description': 'Review auth.py for security'},
        {'type': 'code_review', 'description': 'Review api.py for security'},
        {'type': 'code_review', 'description': 'Review database.py for security'},
    ]
)
# All three reviews happen in parallel
```

### 2. Dependent Tasks

```python
# User: "Set up the test environment, write tests, then run them"

# Creates dependent promises:
task_map = queue_manager.create_dependent_promises(
    chat_id=chat_id,
    tasks=[
        {'name': 'setup', 'type': 'code', 'description': 'Set up test environment'},
        {'name': 'write', 'type': 'code', 'description': 'Write unit tests'},
        {'name': 'run', 'type': 'code', 'description': 'Run test suite'},
    ],
    dependency_map={
        'write': ['setup'],  # Write tests after setup
        'run': ['write']     # Run tests after writing
    }
)
```

### 3. Restart Recovery

```python
# On startup:
queue_manager = UnifiedQueueManager()

# Automatically resumes:
# - Pending promises from before shutdown
# - Failed tasks that should retry
# - Missed messages in queue
queue_manager.resume_all_pending()
```

## Migration Strategy

### Step 1: Non-Breaking Addition
- Add new tables without removing old code
- Run Celery in parallel with existing system
- Gradually move tasks to Celery

### Step 2: Feature Flag Rollout
```python
if settings.USE_CELERY_PROMISES:
    # New Celery-based system
    promise_id = celery_promise_manager.create_promise(...)
else:
    # Existing synchronous system
    result = execute_task_synchronously(...)
```

### Step 3: Full Migration
- Move all promise creation to Celery
- Remove synchronous fallbacks
- Deprecate old promise code

## Monitoring & Operations

### Celery Flower Dashboard
- Real-time task monitoring
- Success/failure rates
- Task execution times
- Worker status

### Prometheus Metrics
```python
# Add Celery metrics for monitoring
from celery.events.state import State
celery_state = State()

# Export metrics:
# - celery_tasks_total
# - celery_tasks_running
# - celery_tasks_failed
# - celery_task_duration_seconds
```

### Logging
```python
# Structured logging for all tasks
@app.task(bind=True)
def any_task(self, *args, **kwargs):
    logger.info("Task started", extra={
        'task_id': self.request.id,
        'task_name': self.name,
        'args': args,
        'kwargs': kwargs
    })
```

## Conclusion

By combining:
1. **Fixed missed message logic** - Correct time window checking
2. **Celery task queue** - Professional-grade async execution
3. **Unified architecture** - Messages and promises in one system

We get a robust solution that:
- ✅ Properly catches messages sent while offline
- ✅ Executes promises in parallel with dependencies
- ✅ Survives restarts and recovers gracefully
- ✅ Scales to handle many concurrent operations
- ✅ Provides monitoring and debugging tools

This approach leverages proven technology instead of building a custom solution, reducing development time and increasing reliability.