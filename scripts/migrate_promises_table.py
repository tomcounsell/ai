#!/usr/bin/env python
"""
Migrate promises table to add new columns for Huey integration.
"""

import sqlite3
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utilities.database import get_database_path


def migrate_promises_table():
    """Add missing columns to promises table."""
    db_path = get_database_path()
    
    if not os.path.exists(db_path):
        print("❌ Database not found. Run the app first to create it.")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if columns already exist
        cursor.execute("PRAGMA table_info(promises)")
        columns = {row[1] for row in cursor.fetchall()}
        
        # Add task_type column if missing
        if 'task_type' not in columns:
            print("Adding task_type column...")
            cursor.execute("""
                ALTER TABLE promises 
                ADD COLUMN task_type TEXT DEFAULT 'code'
            """)
            print("✅ Added task_type column")
        
        # Add metadata column if missing
        if 'metadata' not in columns:
            print("Adding metadata column...")
            cursor.execute("""
                ALTER TABLE promises 
                ADD COLUMN metadata TEXT
            """)
            print("✅ Added metadata column")
        
        conn.commit()
        print("✅ Migration complete!")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    migrate_promises_table()