"""
Integration tests for screenshot handoff with real system components.

These tests use real workspace configurations, actual directory structures,
and integration with the MCP server to validate end-to-end functionality.
"""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_servers.development_tools import mcp
from utilities.workspace_validator import get_workspace_validator


class TestScreenshotHandoffRealWorkspace(unittest.TestCase):
    """Test screenshot handoff with real workspace configurations."""

    def setUp(self):
        """Set up test environment with real workspace structure."""
        # Create temporary workspace that mimics real structure
        self.workspace_root = tempfile.mkdtemp()
        self.workspace_name = "test_workspace"
        
        # Create realistic directory structure
        self.src_dir = os.path.join(self.workspace_root, "src")
        self.tests_dir = os.path.join(self.workspace_root, "tests")
        os.makedirs(self.src_dir, exist_ok=True)
        os.makedirs(self.tests_dir, exist_ok=True)
        
        # Create tmp/ai_screenshots directory
        self.screenshot_dir = os.path.join(self.workspace_root, "tmp", "ai_screenshots")
        os.makedirs(self.screenshot_dir, exist_ok=True)

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.workspace_root, ignore_errors=True)

    def test_real_workspace_validation(self):
        """Test with actual workspace validator configuration."""
        # Create mock workspace configuration
        test_config = {
            "workspaces": {
                self.workspace_name: {
                    "database_id": "test-db-id",
                    "workspace_type": "test",
                    "working_directory": self.workspace_root,
                    "telegram_chat_ids": ["-123456789"],
                    "aliases": ["test"],
                    "allowed_directories": [self.workspace_root]
                }
            },
            "telegram_groups": {
                "-123456789": self.workspace_name
            }
        }
        
        import json
        config_path = os.path.join(self.workspace_root, "test_workspace_config.json")
        with open(config_path, 'w') as f:
            json.dump(test_config, f)
        
        # Test workspace validator with real config
        from utilities.workspace_validator import WorkspaceValidator
        validator = WorkspaceValidator(config_path)
        
        # Test screenshot directory access
        test_screenshot = os.path.join(self.screenshot_dir, "test_task_123.png")
        with open(test_screenshot, 'w') as f:
            f.write("fake image")
        
        try:
            validator.validate_directory_access("-123456789", test_screenshot)
            # Should not raise exception
        except Exception as e:
            self.fail(f"Screenshot access should be allowed: {e}")

    def test_concurrent_screenshot_access(self):
        """Test multiple screenshot operations simultaneously."""
        import threading
        import queue
        
        results = queue.Queue()
        
        def create_screenshot(task_id):
            """Create a screenshot file and attempt to retrieve it."""
            try:
                screenshot_path = os.path.join(self.screenshot_dir, f"{task_id}_{int(time.time())}.png")
                
                # Create minimal PNG
                with open(screenshot_path, "wb") as f:
                    f.write(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01')
                
                # Simulate retrieval
                time.sleep(0.1)  # Brief delay
                
                # Check file exists and clean up
                exists = os.path.exists(screenshot_path)
                if exists:
                    os.remove(screenshot_path)
                
                results.put((task_id, exists, None))
                
            except Exception as e:
                results.put((task_id, False, str(e)))
        
        # Start multiple threads
        threads = []
        for i in range(5):
            thread = threading.Thread(target=create_screenshot, args=(f"concurrent_task_{i}",))
            threads.append(thread)
            thread.start()
        
        # Wait for completion
        for thread in threads:
            thread.join()
        
        # Check results
        successes = 0
        while not results.empty():
            task_id, success, error = results.get()
            if success:
                successes += 1
            elif error:
                print(f"Task {task_id} failed: {error}")
        
        self.assertEqual(successes, 5, "All concurrent screenshot operations should succeed")

    def test_environment_variable_isolation(self):
        """Test NOTION_TASK_ID environment variable handling."""
        original_task_id = os.environ.get('NOTION_TASK_ID')
        
        try:
            # Test setting and restoration
            test_task_id = "test_env_task_123"
            os.environ['NOTION_TASK_ID'] = test_task_id
            
            # Verify it's set
            self.assertEqual(os.environ.get('NOTION_TASK_ID'), test_task_id)
            
            # Simulate the environment management from bug report workflow
            saved_task_id = os.environ.get('NOTION_TASK_ID')
            new_task_id = "new_test_task_456"
            os.environ['NOTION_TASK_ID'] = new_task_id
            
            self.assertEqual(os.environ.get('NOTION_TASK_ID'), new_task_id)
            
            # Restore
            if saved_task_id is not None:
                os.environ['NOTION_TASK_ID'] = saved_task_id
            else:
                del os.environ['NOTION_TASK_ID']
            
            # Should be restored to original
            self.assertEqual(os.environ.get('NOTION_TASK_ID'), test_task_id)
            
        finally:
            # Restore original state
            if original_task_id is not None:
                os.environ['NOTION_TASK_ID'] = original_task_id
            elif 'NOTION_TASK_ID' in os.environ:
                del os.environ['NOTION_TASK_ID']

    def test_file_system_permissions(self):
        """Test actual file system operations and permissions."""
        # Test directory creation
        nested_dir = os.path.join(self.screenshot_dir, "nested", "deep")
        os.makedirs(nested_dir, exist_ok=True)
        self.assertTrue(os.path.exists(nested_dir))
        
        # Test file creation with timestamp
        timestamp = int(time.time())
        screenshot_path = os.path.join(self.screenshot_dir, f"permission_test_{timestamp}.png")
        
        # Create realistic PNG file
        png_header = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
        with open(screenshot_path, "wb") as f:
            f.write(png_header)
        
        # Verify file properties
        self.assertTrue(os.path.exists(screenshot_path))
        self.assertTrue(os.path.isfile(screenshot_path))
        self.assertGreater(os.path.getsize(screenshot_path), 0)
        
        # Test file modification time
        file_mtime = os.path.getmtime(screenshot_path)
        self.assertAlmostEqual(file_mtime, timestamp, delta=2)
        
        # Test file cleanup
        os.remove(screenshot_path)
        self.assertFalse(os.path.exists(screenshot_path))

    def test_error_recovery_scenarios(self):
        """Test error recovery in various failure scenarios."""
        test_cases = [
            {
                "name": "missing_directory",
                "setup": lambda: None,  # Don't create directory
                "screenshot_dir": os.path.join(self.workspace_root, "nonexistent", "ai_screenshots"),
                "expected_error": "No screenshot directory found"
            },
            {
                "name": "permission_denied",
                "setup": lambda: os.makedirs(os.path.join(self.workspace_root, "readonly", "ai_screenshots"), exist_ok=True),
                "screenshot_dir": os.path.join(self.workspace_root, "readonly", "ai_screenshots"),
                "expected_error": None  # Depends on system permissions
            },
            {
                "name": "corrupted_file",
                "setup": lambda: self._create_corrupted_file(),
                "screenshot_dir": self.screenshot_dir,
                "expected_error": None  # Should handle gracefully
            }
        ]
        
        for case in test_cases:
            with self.subTest(case=case["name"]):
                case["setup"]()
                
                from mcp_servers.development_tools import retrieve_workspace_screenshot
                
                with patch('os.getcwd', return_value=self.workspace_root):
                    result = retrieve_workspace_screenshot(
                        task_id="error_test",
                        chat_id="",
                        max_age_minutes=10
                    )
                    
                    if case["expected_error"]:
                        self.assertIn(case["expected_error"], result)
                    else:
                        # Should not crash
                        self.assertIsInstance(result, str)

    def _create_corrupted_file(self):
        """Create a corrupted 'screenshot' file for testing."""
        corrupted_path = os.path.join(self.screenshot_dir, "error_test_123.png")
        with open(corrupted_path, "w") as f:
            f.write("This is not a valid PNG file")


class TestMCPServerIntegration(unittest.TestCase):
    """Test MCP server integration for screenshot tools."""

    def test_mcp_server_tool_discovery(self):
        """Test that screenshot tools are discoverable via MCP."""
        # Import MCP server
        from mcp_servers.development_tools import mcp
        
        # Get all registered tools
        tool_names = list(mcp.tools.keys())
        
        # Verify screenshot tools are registered
        self.assertIn("retrieve_workspace_screenshot", tool_names)
        self.assertIn("execute_bug_report_with_screenshot", tool_names)
        
        # Verify tool metadata
        screenshot_tool = mcp.tools["retrieve_workspace_screenshot"]
        self.assertIsNotNone(screenshot_tool.description)
        self.assertIn("screenshot", screenshot_tool.description.lower())

    def test_tool_parameter_validation(self):
        """Test MCP tool parameter validation."""
        from mcp_servers.development_tools import retrieve_workspace_screenshot
        
        # Test with missing required parameters
        with self.assertRaises(TypeError):
            retrieve_workspace_screenshot()  # Missing task_id
        
        # Test with valid parameters
        result = retrieve_workspace_screenshot(
            task_id="test_task",
            chat_id="test_chat",
            max_age_minutes=5
        )
        
        # Should return error message since no screenshots exist
        self.assertIsInstance(result, str)
        self.assertIn("No screenshot directory found", result)

    def test_context_injection_patterns(self):
        """Test context injection for MCP tools."""
        # Test that tools can handle empty context gracefully
        from mcp_servers.development_tools import retrieve_workspace_screenshot
        
        # Call with minimal context
        result = retrieve_workspace_screenshot(task_id="minimal_test")
        
        # Should handle gracefully without chat_id
        self.assertIsInstance(result, str)
        
        # Call with full context
        result = retrieve_workspace_screenshot(
            task_id="full_test",
            chat_id="test_chat_123",
            max_age_minutes=15
        )
        
        # Should also handle gracefully
        self.assertIsInstance(result, str)


if __name__ == "__main__":
    # Run with verbose output for integration tests
    unittest.main(verbosity=2)