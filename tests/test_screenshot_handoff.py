"""
Test suite for screenshot handoff functionality.

This module tests the complete screenshot handoff workflow from Claude Code
session execution through screenshot retrieval and Telegram upload.
"""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from mcp_servers.development_tools import (
    execute_bug_report_with_screenshot,
    retrieve_workspace_screenshot,
)
from tools.valor_delegation_tool import spawn_valor_session
from utilities.workspace_validator import WorkspaceAccessError, get_workspace_validator


class TestScreenshotHandoff(unittest.TestCase):
    """Test screenshot handoff functionality."""

    def setUp(self):
        """Set up test environment."""
        self.test_dir = tempfile.mkdtemp()
        self.screenshot_dir = os.path.join(self.test_dir, "tmp", "ai_screenshots")
        os.makedirs(self.screenshot_dir, exist_ok=True)
        
        # Create a mock screenshot file
        self.test_task_id = "test_login_bug"
        self.timestamp = int(time.time())
        self.screenshot_path = os.path.join(
            self.screenshot_dir, f"{self.test_task_id}_{self.timestamp}.png"
        )
        
        # Create a minimal PNG file (1x1 pixel)
        png_data = (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
            b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\tpHYs\x00\x00\x0b\x13'
            b'\x00\x00\x0b\x13\x01\x00\x9a\x9c\x18\x00\x00\x00\x0cIDATx\xdac'
            b'\xf8\x0f\x00\x00\x01\x00\x01'
        )
        
        with open(self.screenshot_path, "wb") as f:
            f.write(png_data)

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_workspace_screenshot_directory_access(self):
        """Test that workspace/tmp/ai_screenshots/ is accessible within workspace boundaries."""
        with patch.object(get_workspace_validator(), 'get_workspace_for_chat') as mock_get_workspace:
            with patch.object(get_workspace_validator(), 'get_allowed_directories') as mock_get_dirs:
                # Mock workspace configuration
                mock_get_workspace.return_value = "test_workspace"
                mock_get_dirs.return_value = [self.test_dir]
                
                # Test screenshot directory access
                validator = get_workspace_validator()
                
                # This should not raise an exception due to our screenshot directory exception
                try:
                    validator.validate_directory_access("test_chat", self.screenshot_path)
                except WorkspaceAccessError:
                    self.fail("Screenshot directory access should be allowed within workspace")

    @patch('tools.image_analysis_tool.analyze_image')
    def test_screenshot_retrieval_mcp_tool(self, mock_analyze):
        """Test MCP tool can find and process screenshots with TELEGRAM_IMAGE_GENERATED marker."""
        # Mock image analysis response
        mock_analyze.return_value = "👁️ **What I see:** This is a test screenshot showing a login form."
        
        with patch('os.getcwd', return_value=self.test_dir):
            result = retrieve_workspace_screenshot(
                task_id=self.test_task_id,
                chat_id="",
                max_age_minutes=10
            )
            
            # Should return TELEGRAM_IMAGE_GENERATED marker
            self.assertTrue(result.startswith("TELEGRAM_IMAGE_GENERATED|"))
            self.assertIn(self.test_task_id, result)
            self.assertIn("Screenshot Evidence", result)
            
            # Screenshot file should be cleaned up
            self.assertFalse(os.path.exists(self.screenshot_path))

    def test_screenshot_retrieval_no_files(self):
        """Test behavior when no screenshots are found."""
        with patch('os.getcwd', return_value=self.test_dir):
            result = retrieve_workspace_screenshot(
                task_id="nonexistent_task",
                chat_id="",
                max_age_minutes=10
            )
            
            self.assertIn("No screenshots found", result)
            self.assertIn("nonexistent_task", result)

    def test_screenshot_retrieval_old_files(self):
        """Test behavior when screenshots are too old."""
        # Create an old file by modifying timestamp
        old_time = time.time() - (20 * 60)  # 20 minutes ago
        os.utime(self.screenshot_path, (old_time, old_time))
        
        with patch('os.getcwd', return_value=self.test_dir):
            result = retrieve_workspace_screenshot(
                task_id=self.test_task_id,
                chat_id="",
                max_age_minutes=10
            )
            
            self.assertIn("No recent screenshots found", result)

    @patch('tools.valor_delegation_tool.spawn_valor_session')
    @patch('tools.image_analysis_tool.analyze_image')
    def test_bug_report_workflow_orchestration(self, mock_analyze, mock_spawn):
        """Test end-to-end bug report workflow with Claude Code delegation."""
        # Mock delegation result with screenshot marker
        mock_spawn.return_value = f"Test completed successfully.\nSCREENSHOT_CAPTURED:{self.screenshot_path}"
        
        # Mock image analysis
        mock_analyze.return_value = "👁️ **What I see:** Login form with error message visible."
        
        with patch('os.getcwd', return_value=self.test_dir):
            result = execute_bug_report_with_screenshot(
                task_description="Login form shows error message",
                notion_task_id=self.test_task_id,
                chat_id=""
            )
            
            # Should return successful TELEGRAM_IMAGE_GENERATED result
            self.assertTrue(result.startswith("TELEGRAM_IMAGE_GENERATED|"))
            self.assertIn("Screenshot Evidence", result)
            self.assertIn("Login form with error message", result)

    @patch('tools.valor_delegation_tool.spawn_valor_session')
    def test_bug_report_workflow_no_screenshot(self, mock_spawn):
        """Test bug report workflow when no screenshot is captured."""
        # Mock delegation result without screenshot marker
        mock_spawn.return_value = "Test completed successfully but no screenshot was taken."
        
        with patch('os.getcwd', return_value=self.test_dir):
            result = execute_bug_report_with_screenshot(
                task_description="Simple test without screenshot",
                notion_task_id="test_task",
                chat_id=""
            )
            
            self.assertIn("Task completed but no screenshot captured", result)
            self.assertIn("Test completed successfully", result)

    def test_screenshot_cleanup_automation(self):
        """Test automatic file cleanup after processing."""
        # Ensure file exists before processing
        self.assertTrue(os.path.exists(self.screenshot_path))
        
        with patch('tools.image_analysis_tool.analyze_image') as mock_analyze:
            mock_analyze.return_value = "Test analysis"
            
            with patch('os.getcwd', return_value=self.test_dir):
                result = retrieve_workspace_screenshot(
                    task_id=self.test_task_id,
                    chat_id="",
                    max_age_minutes=10
                )
                
                # File should be cleaned up after successful processing
                self.assertFalse(os.path.exists(self.screenshot_path))

    def test_workspace_security_boundaries(self):
        """Verify that screenshot access respects workspace isolation."""
        # Create screenshot in a different "workspace"
        other_workspace = tempfile.mkdtemp()
        other_screenshot_dir = os.path.join(other_workspace, "tmp", "ai_screenshots")
        os.makedirs(other_screenshot_dir, exist_ok=True)
        
        other_screenshot = os.path.join(other_screenshot_dir, f"{self.test_task_id}_{self.timestamp}.png")
        with open(other_screenshot, "wb") as f:
            f.write(b"fake image data")
        
        try:
            with patch.object(get_workspace_validator(), 'get_workspace_for_chat') as mock_get_workspace:
                with patch.object(get_workspace_validator(), 'get_allowed_directories') as mock_get_dirs:
                    # Mock workspace that only allows access to self.test_dir
                    mock_get_workspace.return_value = "test_workspace"
                    mock_get_dirs.return_value = [self.test_dir]
                    
                    # Try to access screenshot from other workspace - should fail
                    validator = get_workspace_validator()
                    with self.assertRaises(WorkspaceAccessError):
                        validator.validate_directory_access("test_chat", other_screenshot)
                        
        finally:
            import shutil
            shutil.rmtree(other_workspace, ignore_errors=True)

    def test_valor_delegation_screenshot_detection(self):
        """Test that valor delegation tool detects screenshot markers."""
        with patch('tools.valor_delegation_tool.execute_valor_delegation') as mock_execute:
            # Mock Claude Code output with screenshot marker
            mock_execute.return_value = f"""
Test execution completed successfully.
Creating Playwright test for login page...
SCREENSHOT_CAPTURED:{self.screenshot_path}
Screenshot saved successfully.
"""
            
            result = spawn_valor_session(
                task_description="Create Playwright test with screenshot",
                target_directory=self.test_dir,
                force_sync=True
            )
            
            # Should detect screenshot and add indicator
            self.assertIn("Screenshot(s) Captured", result)
            self.assertIn("ready for retrieval", result)


class TestScreenshotHandoffIntegration(unittest.TestCase):
    """Integration tests for screenshot handoff with real workspace configuration."""

    def test_workspace_integration(self):
        """Test integration with actual workspace configuration."""
        # This test would use real workspace configuration
        # but requires actual workspace setup
        pass

    def test_mcp_tool_registration(self):
        """Test that screenshot tools are properly registered in MCP server."""
        from mcp_servers.development_tools import mcp
        
        # Check that our tools are registered
        tool_names = [tool.name for tool in mcp.tools.values()]
        
        self.assertIn("retrieve_workspace_screenshot", tool_names)
        self.assertIn("execute_bug_report_with_screenshot", tool_names)

    def test_telegram_image_generated_marker(self):
        """Test that TELEGRAM_IMAGE_GENERATED marker is properly formatted."""
        expected_pattern = r"TELEGRAM_IMAGE_GENERATED\|.+\|.+"
        
        # Test with mock screenshot
        test_dir = tempfile.mkdtemp()
        screenshot_dir = os.path.join(test_dir, "tmp", "ai_screenshots")
        os.makedirs(screenshot_dir, exist_ok=True)
        
        task_id = "test_task"
        timestamp = int(time.time())
        screenshot_path = os.path.join(screenshot_dir, f"{task_id}_{timestamp}.png")
        
        # Create minimal PNG
        with open(screenshot_path, "wb") as f:
            f.write(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01')
        
        try:
            with patch('tools.image_analysis_tool.analyze_image') as mock_analyze:
                mock_analyze.return_value = "Test analysis"
                
                with patch('os.getcwd', return_value=test_dir):
                    result = retrieve_workspace_screenshot(
                        task_id=task_id,
                        chat_id="",
                        max_age_minutes=10
                    )
                    
                    import re
                    self.assertRegex(result, expected_pattern)
                    
        finally:
            import shutil
            shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()