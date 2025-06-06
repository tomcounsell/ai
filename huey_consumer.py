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