#\!/usr/bin/env python
"""Test promise execution after fixes."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utilities.promise_manager_huey import HueyPromiseManager
from utilities.database import get_promise, get_database_connection

# Create a test promise
manager = HueyPromiseManager()
promise_id = manager.create_promise(
    chat_id=99999,
    message_id=88888,
    task_description="Test promise execution after fixes",
    task_type="code",
    username="test_user"
)

print(f"Created test promise with ID: {promise_id}")

# Check the promise status
with get_database_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT id, status, task_description FROM promises WHERE id = ?", (promise_id,))
    result = cursor.fetchone()
    if result:
        print(f"Promise {result[0]}: status={result[1]}, task={result[2]}")
