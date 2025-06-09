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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    logger.info(f"Received signal {signum}, shutting down Huey consumer...")
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