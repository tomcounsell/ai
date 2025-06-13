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

# Ensure data directory exists
os.makedirs(os.path.dirname(HUEY_DB_PATH), exist_ok=True)

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
    
    # Connection settings for SQLite with lock prevention
    timeout=30.0,          # Connection timeout in seconds (increased)
    
    # Task expiration (results cleaned up after 1 week)
    # Note: result_expire is passed to huey, not to SqliteStorage
)

# Apply database optimizations to Huey's SQLite storage
# This ensures WAL mode and proper timeouts for the task queue
def _configure_huey_database():
    """Apply database optimizations to Huey's SQLite storage."""
    try:
        import sqlite3
        # Get a connection to the Huey database to configure it
        conn = sqlite3.connect(HUEY_DB_PATH, timeout=30)
        
        # Apply the same optimizations as our main database
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")  # 30 seconds
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA cache_size = -32000")   # 32MB cache for task queue
        conn.execute("PRAGMA temp_store = MEMORY")
        
        conn.close()
    except Exception as e:
        # Don't fail startup if this fails, but log the issue
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to configure Huey database optimizations: {e}")

# Configure the database when this module is imported
_configure_huey_database()

# IMPLEMENTATION NOTE: Import tasks here to register them with Huey
# This ensures all tasks are discovered when the consumer starts
try:
    from . import promise_tasks
    from . import telegram_tasks
    from . import test_runner_tasks
except ImportError:
    # Tasks not yet created or circular import during setup
    pass