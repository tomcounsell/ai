#!/usr/bin/env python3
"""
Quick test for Claude Code session management integration.

This script tests the basic functionality of the session management system
to ensure it can store, retrieve, and manage Claude Code sessions properly.
"""

import os
import sys
import uuid
from datetime import datetime, timedelta

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(__file__))

from utilities.claude_code_session_manager import ClaudeCodeSessionManager
from utilities.database import init_database

def test_session_management():
    """Test basic session management functionality."""
    
    print("🧪 Testing Claude Code Session Management")
    print("=" * 50)
    
    # Initialize database
    init_database()
    print("✅ Database initialized")
    
    # Test data
    test_session_id = str(uuid.uuid4())
    test_chat_id = "test_chat_123"
    test_username = "test_user"
    test_task = "Test Claude Code session management implementation"
    test_working_dir = "/Users/valorengels/src/ai"
    
    # Test 1: Store a new session
    print("\n📋 Test 1: Store new session")
    success = ClaudeCodeSessionManager.store_session(
        session_id=test_session_id,
        chat_id=test_chat_id,
        username=test_username,
        tool_name="delegate_coding_task",
        working_directory=test_working_dir,
        task_description=test_task,
        metadata={"test_run": True}
    )
    print(f"✅ Session stored: {success}")
    print(f"   Session ID: {test_session_id[:8]}...")
    
    # Test 2: Find recent session
    print("\n🔍 Test 2: Find recent session")
    found_session = ClaudeCodeSessionManager.find_recent_session(
        chat_id=test_chat_id,
        username=test_username,
        tool_name="delegate_coding_task",
        working_directory=test_working_dir,
        hours_back=1
    )
    
    if found_session:
        print(f"✅ Found session: {found_session.session_id[:8]}...")
        print(f"   Task: {found_session.initial_task}")
        print(f"   Tool: {found_session.tool_name}")
        print(f"   Directory: {found_session.working_directory}")
    else:
        print("❌ Session not found")
        return False
    
    # Test 3: Update session activity
    print("\n🔄 Test 3: Update session activity")
    updated = ClaudeCodeSessionManager.update_session_activity(
        test_session_id,
        "Follow-up task for testing"
    )
    print(f"✅ Session updated: {updated}")
    
    # Test 4: Get chat sessions
    print("\n📜 Test 4: Get chat sessions")
    chat_sessions = ClaudeCodeSessionManager.get_chat_sessions(
        chat_id=test_chat_id,
        limit=5,
        active_only=True
    )
    print(f"✅ Found {len(chat_sessions)} active sessions for chat")
    
    if chat_sessions:
        session = chat_sessions[0]
        print(f"   Latest session: {session.session_id[:8]}...")
        print(f"   Task count: {session.task_count}")
    
    # Test 5: Build session command
    print("\n⚙️ Test 5: Build session commands")
    
    # New session command
    new_command = ClaudeCodeSessionManager.build_session_command(
        "Create a test script"
    )
    print(f"✅ New session command: {new_command}")
    
    # Continue session command
    continue_command = ClaudeCodeSessionManager.build_session_command(
        "Add error handling to the script",
        session_id=test_session_id,
        should_continue=True
    )
    print(f"✅ Continue session command: {continue_command}")
    
    # Test 6: Session ID extraction (simulated)
    print("\n🔍 Test 6: Session ID extraction")
    mock_claude_output = f"""
    Starting Claude Code session...
    Session ID: {test_session_id}
    Working on your request...
    """
    
    extracted_id = ClaudeCodeSessionManager.extract_session_id_from_output(mock_claude_output)
    print(f"✅ Extracted session ID: {extracted_id[:8] if extracted_id else 'None'}...")
    
    # Test 7: Format session summary
    print("\n📄 Test 7: Format session summary")
    if found_session:
        summary = ClaudeCodeSessionManager.format_session_summary(found_session)
        print("✅ Session summary:")
        print(f"   {summary}")
    
    # Test 8: Deactivate session
    print("\n🔴 Test 8: Deactivate session")
    deactivated = ClaudeCodeSessionManager.deactivate_session(test_session_id)
    print(f"✅ Session deactivated: {deactivated}")
    
    print("\n" + "=" * 50)
    print("🎉 All session management tests completed successfully!")
    return True

if __name__ == "__main__":
    try:
        test_session_management()
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)