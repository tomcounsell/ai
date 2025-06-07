#!/usr/bin/env python3
"""
Promise monitoring script for checking the status of promises in the system.
"""
import sqlite3
from datetime import datetime
from utilities.database import get_database_connection, get_pending_promises, get_promise


def check_promise_status():
    """Check and display the status of all promises in the system."""
    
    print("=" * 80)
    print("PROMISE SYSTEM STATUS REPORT")
    print("=" * 80)
    print(f"Report generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Connect to database
    with get_database_connection() as conn:
        cursor = conn.cursor()
        
        # Get status summary
        cursor.execute("SELECT status, COUNT(*) as count FROM promises GROUP BY status")
        status_summary = cursor.fetchall()
        
        print("STATUS SUMMARY:")
        print("-" * 40)
        total = 0
        for status, count in status_summary:
            print(f"  {status.upper()}: {count}")
            total += count
        print(f"  TOTAL: {total}")
        print()
        
        # Get pending promises
        print("PENDING PROMISES:")
        print("-" * 40)
        cursor.execute("""
            SELECT id, chat_id, task_description, task_type, 
                   datetime(created_at, 'localtime') as created
            FROM promises 
            WHERE status='pending' 
            ORDER BY created_at DESC
        """)
        pending = cursor.fetchall()
        
        if pending:
            for promise in pending:
                print(f"  ID: {promise[0]}")
                print(f"  Chat ID: {promise[1]}")
                print(f"  Task: {promise[2]}")
                print(f"  Type: {promise[3]}")
                print(f"  Created: {promise[4]}")
                print()
        else:
            print("  No pending promises")
            print()
        
        # Get recently completed promises
        print("RECENTLY COMPLETED PROMISES (last 5):")
        print("-" * 40)
        cursor.execute("""
            SELECT id, task_description, 
                   datetime(created_at, 'localtime') as created,
                   datetime(completed_at, 'localtime') as completed,
                   result_summary
            FROM promises 
            WHERE status='completed' 
            ORDER BY completed_at DESC 
            LIMIT 5
        """)
        completed = cursor.fetchall()
        
        if completed:
            for promise in completed:
                print(f"  ID: {promise[0]}")
                print(f"  Task: {promise[1]}")
                print(f"  Created: {promise[2]}")
                print(f"  Completed: {promise[3]}")
                if promise[4]:
                    print(f"  Result: {promise[4][:100]}{'...' if len(promise[4]) > 100 else ''}")
                print()
        else:
            print("  No completed promises")
            print()
        
        # Check Huey status
        print("HUEY TASK QUEUE STATUS:")
        print("-" * 40)
        try:
            huey_conn = sqlite3.connect('/Users/valorengels/src/ai/data/huey.db')
            huey_cursor = huey_conn.cursor()
            huey_cursor.execute("SELECT COUNT(*) FROM task")
            pending_tasks = huey_cursor.fetchone()[0]
            print(f"  Pending Huey tasks: {pending_tasks}")
            huey_conn.close()
        except Exception as e:
            print(f"  Could not access Huey database: {e}")
        
        # Check if Huey consumer is running
        import subprocess
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        huey_running = 'huey_consumer' in result.stdout
        print(f"  Huey consumer running: {'YES' if huey_running else 'NO'}")
        
        if not huey_running:
            print("\n  ⚠️  WARNING: Huey consumer is not running!")
            print("  Start it with: scripts/start_huey.sh")
    
    print("\n" + "=" * 80)


def monitor_promise(promise_id: int):
    """Monitor a specific promise by ID."""
    promise = get_promise(promise_id)
    
    if not promise:
        print(f"Promise {promise_id} not found")
        return
    
    print(f"\nPROMISE DETAILS (ID: {promise_id}):")
    print("-" * 40)
    for key, value in promise.items():
        if value is not None:
            print(f"  {key}: {value}")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        # Monitor specific promise
        promise_id = int(sys.argv[1])
        monitor_promise(promise_id)
    else:
        # Check all promises
        check_promise_status()