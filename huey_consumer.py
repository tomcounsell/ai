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
import signal
import os
from huey.consumer import Consumer
from tasks.huey_config import huey

# Configure consolidated logging to tasks.log
import logging.handlers
os.makedirs('logs', exist_ok=True)

# Create rotating file handler for task logs
file_handler = logging.handlers.RotatingFileHandler(
    'logs/tasks.log', 
    maxBytes=10*1024*1024,  # 10MB
    backupCount=3
)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
))

# Configure logging for Huey tasks
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        file_handler,
        logging.StreamHandler()  # Still log to console
    ]
)

logger = logging.getLogger(__name__)

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully with hot-reload protection."""
    import psutil
    import time
    
    # Check if this is a legitimate shutdown (e.g., user-initiated) vs hot-reload
    current_pid = os.getpid()
    parent_pid = os.getppid()
    
    try:
        parent_process = psutil.Process(parent_pid)
        parent_name = parent_process.name()
        parent_cmdline = ' '.join(parent_process.cmdline())
        
        # Check if parent is a Claude Code instance or file watcher
        is_claude_code = 'claude' in parent_cmdline.lower()
        is_file_watcher = any(term in parent_cmdline.lower() for term in ['watchdog', 'fsevents', 'inotify'])
        
        if is_claude_code or is_file_watcher:
            logger.warning(f"🔄 SIGNAL {signum} from Claude Code/file watcher (parent: {parent_name}) - IGNORING to prevent hot-reload shutdown")
            logger.warning(f"   Parent command: {parent_cmdline[:100]}...")
            logger.info("⚡ Huey consumer will continue running despite hot-reload signal")
            return  # Ignore the signal
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass  # If we can't check parent, proceed with normal shutdown
    
    logger.info(f"📴 Received legitimate shutdown signal {signum}, shutting down Huey consumer...")
    sys.exit(0)

if __name__ == '__main__':
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logger.info("🚀 STARTING HUEY TASK QUEUE CONSUMER")
    logger.info("=" * 50)
    logger.info(f"📁 Working directory: {os.getcwd()}")
    logger.info(f"🗄️  Huey database: {os.environ.get('HUEY_DB_PATH', 'data/huey.db')}")
    logger.info(f"⚡ Immediate mode: {os.environ.get('HUEY_IMMEDIATE', 'false')}")
    logger.info(f"🧵 Available task types:")
    
    # List available tasks
    try:
        # Try to get task names from registry
        if hasattr(huey._registry, '_registry'):
            tasks = list(huey._registry._registry.keys())
        else:
            tasks = ['Tasks will be listed once registered']
        
        for task_name in tasks:
            logger.info(f"   • {task_name}")
    except Exception:
        logger.info("   • Task registry will be populated at runtime")
    
    logger.info("=" * 50)
    
    try:
        # IMPLEMENTATION NOTE: The consumer handles all the complex
        # bits of task execution, retries, and scheduling.
        consumer = Consumer(huey)
        logger.info("✅ Huey consumer initialized successfully")
        logger.info("👀 Monitoring for queued tasks...")
        consumer.run()
    except Exception as e:
        logger.error(f"Failed to start Huey consumer: {str(e)}", exc_info=True)
        sys.exit(1)