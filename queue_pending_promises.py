#\!/usr/bin/env python
"""Queue all pending promises for execution."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utilities.database import get_database_connection
from tasks.promise_tasks import execute_promise_by_type

# Get all pending promises
with get_database_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, task_description 
        FROM promises 
        WHERE status = 'pending'
        ORDER BY created_at ASC
    """)
    pending_promises = cursor.fetchall()

print(f"Found {len(pending_promises)} pending promises")

# Queue each one
for promise_id, description in pending_promises:
    print(f"Queueing promise {promise_id}: {description[:50]}...")
    result = execute_promise_by_type.schedule(args=(promise_id,), delay=0)
    print(f"  Queued with Huey task ID: {result.id}")

print(f"\nQueued {len(pending_promises)} promises for execution")
