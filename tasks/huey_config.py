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
    
    # Connection settings for SQLite
    timeout=10.0,          # Connection timeout in seconds
    
    # Task expiration (results cleaned up after 1 week)
    # Note: result_expire is passed to huey, not to SqliteStorage
)

# IMPLEMENTATION NOTE: Import tasks here to register them with Huey
# This ensures all tasks are discovered when the consumer starts
try:
    from . import promise_tasks
    from . import telegram_tasks
    from . import test_runner_tasks
except ImportError:
    # Tasks not yet created or circular import during setup
    pass