#!/usr/bin/env python3
"""
Test workspace isolation in the promise queue system.

This test verifies that promises execute in the correct workspace directories
based on the chat_id -> workspace mapping in config/workspace_config.json.
"""
import json
import os
import time
from unittest.mock import Mock, patch

from utilities.database import init_database, get_database_connection, get_promise
from utilities.promise_manager_huey import HueyPromiseManager, _resolve_workspace_context
from tasks.huey_config import huey


def test_workspace_context_resolution():
    """Test that workspace context is correctly resolved from chat_id."""
    print("🔍 Testing workspace context resolution...")
    
    # Test cases based on workspace_config.json
    test_cases = [
        # (chat_id, expected_workspace_name, expected_working_directory)
        (-1002600253717, "PsyOPTIMAL", "/Users/valorengels/src/psyoptimal"),
        (-4897329503, "PsyOPTIMAL Dev", "/Users/valorengels/src/psyoptimal"),
        (-4818206585, "Valor Test Team", "/Users/valorengels/src/ai"),
        (-1002553869320, "DeckFusion Dev", "/Users/valorengels/src/deckfusion"),
        (-4719889199, "Yudame", "/Users/valorengels/src/ai"),
        (-4891178445, "Yudame Dev", "/Users/valorengels/src/ai"),
        (-1002455228990, "Verkstad", "/Users/valorengels/src/verkstad"),
        # Test unknown chat_id (should default to Yudame)
        (99999999, "Yudame", "/Users/valorengels/src/ai"),
    ]
    
    for chat_id, expected_workspace, expected_directory in test_cases:
        context = _resolve_workspace_context(chat_id)
        
        print(f"  Chat {chat_id}:")
        print(f"    Expected: {expected_workspace} -> {expected_directory}")
        print(f"    Actual:   {context['workspace_name']} -> {context['working_directory']}")
        
        if chat_id == 99999999:  # Unknown chat_id case
            assert context['workspace_name'] == expected_workspace, f"Expected default workspace for unknown chat_id"
            assert context['working_directory'] == expected_directory, f"Expected default directory for unknown chat_id"
        else:
            assert context['workspace_name'] == expected_workspace, f"Wrong workspace for chat {chat_id}"
            assert context['working_directory'] == expected_directory, f"Wrong directory for chat {chat_id}"
        
        print(f"    ✅ Correct workspace resolution")
    
    print("✅ All workspace context resolution tests passed!")


def test_promise_workspace_metadata():
    """Test that promises include correct workspace metadata."""
    print("\n🔍 Testing promise workspace metadata...")
    
    # Initialize database
    init_database()
    
    # Enable immediate mode for testing
    huey.immediate = True
    
    try:
        manager = HueyPromiseManager()
        
        # Test different workspaces
        test_cases = [
            # (chat_id, expected_workspace_name, expected_working_directory)
            (-1002600253717, "PsyOPTIMAL", "/Users/valorengels/src/psyoptimal"),
            (-4818206585, "Valor Test Team", "/Users/valorengels/src/ai"),
            (-4719889199, "Yudame", "/Users/valorengels/src/ai"),
        ]
        
        promise_ids = []
        
        for chat_id, expected_workspace, expected_directory in test_cases:
            # Create promise
            promise_id = manager.create_promise(
                chat_id=chat_id,
                message_id=12345,
                task_description=f"Test task for {expected_workspace}",
                task_type="code"
            )
            promise_ids.append(promise_id)
            
            # Check metadata includes workspace context
            promise = get_promise(promise_id)
            assert promise is not None, f"Promise {promise_id} not found"
            
            metadata = json.loads(promise.get('metadata') or '{}')
            workspace_context = metadata.get('workspace_context', {})
            
            print(f"  Promise {promise_id} for chat {chat_id}:")
            print(f"    Workspace: {workspace_context.get('workspace_name')}")
            print(f"    Directory: {workspace_context.get('working_directory')}")
            
            assert workspace_context.get('workspace_name') == expected_workspace, f"Wrong workspace in metadata"
            assert workspace_context.get('working_directory') == expected_directory, f"Wrong directory in metadata"
            
            print(f"    ✅ Correct workspace metadata")
        
        print("✅ All promise workspace metadata tests passed!")
        
        return promise_ids
        
    finally:
        # Cleanup
        huey.immediate = False
        with get_database_connection() as conn:
            conn.execute("DELETE FROM promises")
            conn.commit()


def test_promise_execution_with_workspace():
    """Test that promise execution uses correct workspace directories."""
    print("\n🔍 Testing promise execution with workspace isolation...")
    
    # Initialize database
    init_database()
    
    # Enable immediate mode for testing
    huey.immediate = True
    
    try:
        manager = HueyPromiseManager()
        
        # Track which directories tasks were executed in
        executed_directories = []
        
        def mock_spawn_valor_session(task_description, target_directory, **kwargs):
            executed_directories.append(target_directory)
            return f"Task executed in {target_directory}: {task_description}"
        
        with patch('tools.valor_delegation_tool.spawn_valor_session', side_effect=mock_spawn_valor_session):
            # Create promises for different workspaces
            test_cases = [
                (-1002600253717, "/Users/valorengels/src/psyoptimal", "PsyOPTIMAL task"),
                (-4818206585, "/Users/valorengels/src/ai", "Test task"),
                (-4719889199, "/Users/valorengels/src/ai", "Yudame task"),
            ]
            
            promise_ids = []
            
            for chat_id, expected_directory, task_description in test_cases:
                promise_id = manager.create_promise(
                    chat_id=chat_id,
                    message_id=12345,
                    task_description=task_description,
                    task_type="code"
                )
                promise_ids.append(promise_id)
            
            # In immediate mode, tasks execute synchronously
            time.sleep(0.1)
            
            # Verify tasks were executed in correct directories
            print(f"  Executed in directories: {executed_directories}")
            
            expected_directories = [
                "/Users/valorengels/src/psyoptimal",
                "/Users/valorengels/src/deckfusion", 
                "/Users/valorengels/src/ai"
            ]
            
            assert len(executed_directories) == len(expected_directories), f"Expected {len(expected_directories)} executions, got {len(executed_directories)}"
            
            for i, (expected_dir, actual_dir) in enumerate(zip(expected_directories, executed_directories)):
                print(f"    Task {i+1}: Expected {expected_dir}, Got {actual_dir}")
                assert actual_dir == expected_dir, f"Task {i+1} executed in wrong directory"
                print(f"    ✅ Correct execution directory")
            
            # Verify all promises completed successfully
            for promise_id in promise_ids:
                promise = get_promise(promise_id)
                print(f"  Promise {promise_id}: {promise['status']}")
                assert promise['status'] == 'completed', f"Promise {promise_id} not completed"
            
            print("✅ All workspace execution tests passed!")
        
    finally:
        # Cleanup
        huey.immediate = False
        with get_database_connection() as conn:
            conn.execute("DELETE FROM promises")
            conn.commit()


def main():
    """Run all workspace isolation tests."""
    print("🧪 Promise Workspace Isolation Test Suite")
    print("=" * 50)
    
    try:
        # Test workspace context resolution
        test_workspace_context_resolution()
        
        # Test promise metadata includes workspace info
        test_promise_workspace_metadata()
        
        # Test promise execution uses correct directories
        test_promise_execution_with_workspace()
        
        print("\n" + "=" * 50)
        print("🎉 ALL WORKSPACE ISOLATION TESTS PASSED!")
        print("✅ Promises correctly resolve workspace context")
        print("✅ Promises execute in correct project directories")
        print("✅ Multi-project workspace isolation working")
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)