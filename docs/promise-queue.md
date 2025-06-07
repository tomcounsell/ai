# Promise Queue System Documentation

## Overview

The Promise Queue System provides asynchronous task execution and message queue management using **Huey** - a lightweight task queue with native SQLite support. This system enables background processing of long-running tasks while maintaining responsive user interactions.

## Architecture

### Core Components

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

### Database Schema

#### Promises Table
```sql
CREATE TABLE IF NOT EXISTS promises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    task_description TEXT NOT NULL,
    task_type TEXT DEFAULT 'code',  -- 'code', 'search', 'analysis'
    status TEXT DEFAULT 'pending',  -- 'pending', 'in_progress', 'completed', 'failed'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    result_summary TEXT,
    error_message TEXT,
    metadata TEXT  -- JSON blob for task-specific data
);
```

## Implementation Details

### Task Flow

1. **Detection**: Telegram handler checks for long-running tasks
   - Explicit `ASYNC_PROMISE|` markers in responses
   - Estimated task duration > 30 seconds
   - Intent classification suggests background processing

2. **Creation**: Promise manager creates database record
   - Stores task details and context
   - Schedules Huey task for execution
   - Returns promise ID for tracking

3. **Execution**: Huey consumer processes tasks
   - Routes to appropriate handler by task type
   - Updates status during execution
   - Handles retries on failure

4. **Notification**: Sends completion message to user
   - Formats results summary
   - Includes execution duration
   - References original message

### Task Types

#### Code Tasks (`execute_coding_task`)
- Delegates to Claude Code via `valor_delegation_tool`
- Executes in specified working directory
- Handles git operations and testing

#### Search Tasks (`execute_search_task`)
- Web search and information retrieval
- Currently placeholder implementation

#### Analysis Tasks (`execute_analysis_task`)
- Data analysis and processing
- Currently placeholder implementation

### Promise Manager API

```python
from utilities.promise_manager_huey import HueyPromiseManager

# Create single promise
promise_manager = HueyPromiseManager()
promise_id = promise_manager.create_promise(
    chat_id=12345,
    message_id=67890,
    task_description="Fix authentication bug",
    task_type="code",
    username="user123"
)

# Create parallel promises
tasks = [
    {'description': 'Review file1.py', 'type': 'code'},
    {'description': 'Review file2.py', 'type': 'code'}
]
promise_ids = promise_manager.create_parallel_promises(
    chat_id=12345,
    message_id=67890,
    tasks=tasks
)
```

## Configuration

### Environment Variables
- `HUEY_DB_PATH`: SQLite database path (default: `data/huey.db`)
- `HUEY_IMMEDIATE`: Synchronous execution for testing (default: `false`)

### Huey Settings
- **Workers**: 4 threads (configurable)
- **Worker Type**: Thread-based for I/O operations
- **Result Storage**: 7-day retention
- **Retry Policy**: 3 attempts with 60-second delay

## Operations

### Starting the System
The promise queue is automatically started with the main application:
```bash
scripts/start.sh  # Starts FastAPI, Telegram, and Huey consumer
```

### Monitoring
```bash
# Check promise status
sqlite3 system.db "SELECT id, task_description, status FROM promises WHERE status='pending';"

# View Huey logs
tail -f logs/huey.log

# Monitor task throughput
watch -n 5 'sqlite3 data/huey.db "SELECT COUNT(*) FROM huey_task;"'
```

### Troubleshooting

#### Tasks Not Executing
1. Verify Huey consumer is running: `ps aux | grep huey`
2. Check `HUEY_IMMEDIATE` is `false` in production
3. Review logs for task scheduling errors

#### Database Locks
1. Ensure WAL mode is enabled
2. Keep transactions short
3. Monitor concurrent connections

## Integration Points

### Telegram Handler Integration
The message handler (`integrations/telegram/handlers.py`) automatically detects and creates promises:
- Checks for `ASYNC_PROMISE|` markers in agent responses
- Creates promise records in database
- Sends acknowledgment to user

### Valor Delegation Tool
The delegation tool (`tools/valor_delegation_tool.py`) supports async execution:
- Returns `ASYNC_PROMISE|` marker for long tasks
- Provides `force_sync` parameter for background execution
- Estimates task duration for smart routing

### Periodic Tasks
- **cleanup_old_promises**: Removes completed promises after 7 days
- **resume_stalled_promises**: Recovers stuck tasks every 5 minutes

## Current Limitations

1. **Dependency Management**: Basic structure exists but not fully implemented
2. **Message Queue**: Missed message handling planned but not implemented
3. **Progress Tracking**: No real-time progress updates for long tasks
4. **Cancellation**: Users cannot cancel pending promises

## Future Enhancements

1. **Advanced Dependencies**: Graph-based dependency resolution
2. **Priority Queues**: Multiple queues for different priority levels
3. **Progress Updates**: WebSocket integration for real-time status
4. **Distributed Execution**: Multiple consumer instances
5. **Enhanced Monitoring**: Grafana/Prometheus integration

## Related Documentation

- [Agent Architecture](agent-architecture.md) - Core conversational AI system
- [Tool Development](tool-development.md) - Creating tools that integrate with promises
- [System Operations](system-operations.md) - Running and monitoring the system
- [Telegram Integration](telegram-integration.md) - Message handling and bot interface