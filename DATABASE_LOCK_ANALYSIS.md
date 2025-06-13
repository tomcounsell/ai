# Database Lock Issues Analysis

## Root Causes Identified

### 1. Inconsistent Journal Modes
- **Huey DB**: WAL mode (allows concurrent reads)
- **System DB**: DELETE mode (exclusive locks)
- **Impact**: When tasks access both databases, lock conflicts occur

### 2. Missing Busy Timeouts
- Current `busy_timeout = 0` causes immediate failures
- No retry mechanism for locked databases
- Tasks fail instead of waiting for lock release

### 3. Connection Management Issues
- Raw `sqlite3.connect()` without proper PRAGMA settings
- Missing connection cleanup in error cases
- Long-running transactions (up to 300s in daydream tasks)

### 4. Concurrent Access Patterns
- Main server and Huey consumer both access system.db
- Multiple Huey workers can create lock contention
- No coordination between processes

## Specific Problem Areas

### Promise Tasks (`tasks/promise_tasks.py`)
- Lines 114, 298, 473, 1072, etc.: Long-running connections
- Daydream analysis holds connections for 5+ minutes
- Missing timeout/cleanup in error cases

### Database Module (`utilities/database.py`)
- Line 35: Raw connection without PRAGMA configuration
- No busy timeout or WAL mode setup
- Missing connection context managers in some functions

### Huey Configuration (`tasks/huey_config.py`)
- Line 34: Timeout set but not applied to busy_timeout
- No WAL checkpoint configuration
- Missing connection optimization settings

## Recommended Solutions

### Immediate Fixes (High Priority)

1. **Standardize on WAL Mode**
   ```python
   # Apply to all database connections
   PRAGMA journal_mode = WAL;
   PRAGMA busy_timeout = 30000;  # 30 seconds
   PRAGMA synchronous = NORMAL;
   ```

2. **Add Connection Timeouts**
   ```python
   def get_database_connection(timeout=30):
       conn = sqlite3.connect(db_path, timeout=timeout)
       conn.execute("PRAGMA busy_timeout = 30000")
       conn.execute("PRAGMA journal_mode = WAL")
       return conn
   ```

3. **Implement Connection Context Managers**
   ```python
   @contextmanager
   def database_transaction(timeout=30):
       conn = get_database_connection(timeout)
       try:
           yield conn
           conn.commit()
       except:
           conn.rollback()
           raise
       finally:
           conn.close()
   ```

### Long-term Improvements (Medium Priority)

4. **Task-Level Connection Limits**
   - Add connection timeouts to all Huey tasks
   - Implement task-level retry with exponential backoff
   - Break long operations into smaller transactions

5. **Database Connection Pooling**
   - Implement SQLite connection pool
   - Reuse connections where appropriate
   - Monitor connection lifecycle

6. **WAL Checkpoint Management**
   ```python
   # Periodic WAL checkpoint
   @huey.periodic_task(crontab(minute='*/15'))
   def checkpoint_databases():
       for db_path in [system_db, huey_db]:
           with sqlite3.connect(db_path) as conn:
               conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
   ```

### Monitoring and Recovery (Lower Priority)

7. **Lock Detection and Recovery**
   - Add database lock monitoring
   - Implement automatic lock timeout recovery
   - Log lock wait times and patterns

8. **Performance Monitoring**
   - Track connection hold times
   - Monitor WAL file sizes
   - Alert on excessive lock waits

## Implementation Priority

1. **Phase 1**: Fix connection timeouts and WAL mode
2. **Phase 2**: Implement connection context managers
3. **Phase 3**: Add task-level connection management
4. **Phase 4**: Implement monitoring and optimization

## Files Requiring Changes

- `utilities/database.py` - Connection management overhaul
- `tasks/huey_config.py` - Add database optimization settings
- `tasks/promise_tasks.py` - Add connection timeouts to all tasks
- `tasks/telegram_tasks.py` - Fix connection management
- `tasks/test_runner_tasks.py` - Add timeout handling

## Testing Strategy

1. **Load Testing**: Simulate concurrent task execution
2. **Lock Testing**: Intentionally create lock scenarios
3. **Recovery Testing**: Verify timeout and retry behavior
4. **Performance Testing**: Measure impact of changes

## Risk Assessment

- **Low Risk**: Adding timeouts and WAL mode
- **Medium Risk**: Connection pooling implementation
- **High Risk**: Changing transaction patterns in tasks

The immediate fixes (Phase 1) can be implemented safely with minimal risk.