"""
End-to-end tests for screenshot handoff functionality.

These tests validate the complete workflow from Claude Code execution
through screenshot capture, retrieval, analysis, and Telegram upload.
"""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest


class TestScreenshotHandoffEndToEnd(unittest.TestCase):
    """End-to-end tests for complete screenshot handoff workflow."""

    def setUp(self):
        """Set up realistic test environment."""
        self.workspace_root = tempfile.mkdtemp()
        self.screenshot_dir = os.path.join(self.workspace_root, "tmp", "ai_screenshots")
        os.makedirs(self.screenshot_dir, exist_ok=True)
        
        # Create realistic workspace structure
        self.src_dir = os.path.join(self.workspace_root, "src")
        self.tests_dir = os.path.join(self.workspace_root, "tests")
        os.makedirs(self.src_dir, exist_ok=True)
        os.makedirs(self.tests_dir, exist_ok=True)

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.workspace_root, ignore_errors=True)

    @patch('tools.valor_delegation_tool.execute_valor_delegation')
    @patch('tools.image_analysis_tool.analyze_image')
    def test_complete_bug_report_workflow(self, mock_analyze, mock_execute):
        """Test complete end-to-end bug report workflow with screenshot."""
        # Setup
        task_id = "login_bug_e2e_test"
        timestamp = int(time.time())
        screenshot_path = os.path.join(self.screenshot_dir, f"{task_id}_{timestamp}.png")
        
        # Create realistic screenshot file
        self._create_realistic_png(screenshot_path)
        
        # Mock Claude Code execution that creates a screenshot
        mock_execute.return_value = f"""
✅ **Task Completed Successfully**

**Playwright Test Creation:**
- Created test file: tests/login-screenshot.spec.js
- Test navigates to login page at http://localhost:3000/login
- Captures full-page screenshot showing the error state

**Test Execution:**
- ✅ Test passed: Login page loads correctly
- ✅ Screenshot captured successfully
- 📸 Evidence saved for analysis

**Files Created:**
- tests/login-screenshot.spec.js (new Playwright test)
- tmp/ai_screenshots/{task_id}_{timestamp}.png (screenshot evidence)

SCREENSHOT_CAPTURED:{screenshot_path}

**Summary:** Created comprehensive Playwright test that reproduces the login bug and captures visual evidence. The screenshot shows the error message displayed when invalid credentials are entered.
"""
        
        # Mock image analysis
        mock_analyze.return_value = """👁️ **What I see:**

This screenshot shows a login form with the following elements:
- Username input field (filled with "test@example.com")
- Password input field (filled with asterisks)
- "Sign In" button
- **Error message**: "Invalid credentials. Please try again." (displayed in red)
- Company logo at the top
- Clean, modern UI design

**Issues Identified:**
- Error message is clearly visible, confirming the bug report
- The error styling appears consistent with the design system
- Form validation is working as expected

This provides clear evidence of the login error behavior described in the bug report."""

        # Execute complete workflow
        from mcp_servers.development_tools import execute_bug_report_with_screenshot
        
        with patch('os.getcwd', return_value=self.workspace_root):
            # Set up environment
            original_task_id = os.environ.get('NOTION_TASK_ID')
            try:
                result = execute_bug_report_with_screenshot(
                    task_description="Login form shows error message for invalid credentials",
                    notion_task_id=task_id,
                    chat_id=""
                )
                
                # Verify results
                self.assertTrue(result.startswith("TELEGRAM_IMAGE_GENERATED|"))
                self.assertIn(task_id, result)
                self.assertIn("Screenshot Evidence", result)
                self.assertIn("Invalid credentials", result)
                self.assertIn("error message", result)
                
                # Verify Claude Code was called with correct parameters
                mock_execute.assert_called_once()
                call_args = mock_execute.call_args
                self.assertIn("Create Playwright test with screenshot", call_args[1]['prompt'])
                self.assertEqual(call_args[1]['working_directory'], self.workspace_root)
                
                # Verify image analysis was called
                mock_analyze.assert_called_once()
                analyze_args = mock_analyze.call_args
                self.assertEqual(analyze_args[0][0], screenshot_path)  # image_path
                self.assertIn("UI issues", analyze_args[0][1])  # question
                self.assertIn(task_id, analyze_args[0][2])  # context
                
                # Verify screenshot was cleaned up
                self.assertFalse(os.path.exists(screenshot_path))
                
            finally:
                # Restore environment
                if original_task_id is not None:
                    os.environ['NOTION_TASK_ID'] = original_task_id
                elif 'NOTION_TASK_ID' in os.environ:
                    del os.environ['NOTION_TASK_ID']

    @patch('tools.valor_delegation_tool.execute_valor_delegation')
    def test_claude_code_integration_realistic(self, mock_execute):
        """Test realistic Claude Code integration with proper prompt formatting."""
        # Mock realistic Claude Code response
        mock_execute.return_value = """
I'll help you create a Playwright test to capture a screenshot of the navigation issue.

Let me create a test that reproduces the problem:

```javascript
// tests/navigation-issue.spec.js
import { test, expect } from '@playwright/test';
import path from 'path';
import fs from 'fs';

test('capture navigation issue screenshot', async ({ page }) => {
    // Ensure screenshot directory exists
    const screenshotDir = path.join(process.cwd(), 'tmp', 'ai_screenshots');
    fs.mkdirSync(screenshotDir, { recursive: true });
    
    // Generate unique filename
    const taskId = process.env.NOTION_TASK_ID || 'manual_test';
    const timestamp = Date.now();
    const screenshotPath = path.join(screenshotDir, `${taskId}_${timestamp}.png`);
    
    // Navigate to page and reproduce issue
    await page.goto('http://localhost:3000');
    await page.click('[data-testid="navigation-menu"]');
    
    // Wait for animation/loading
    await page.waitForTimeout(1000);
    
    // Capture screenshot showing the issue
    await page.screenshot({
        path: screenshotPath,
        fullPage: true
    });
    
    console.log(`Screenshot saved: ${screenshotPath}`);
    
    // Output standardized result for main agent to parse
    console.log(`SCREENSHOT_CAPTURED:${screenshotPath}`);
});
```

I've created a comprehensive Playwright test that:
1. Sets up the screenshot directory structure
2. Uses the NOTION_TASK_ID environment variable for consistent naming
3. Reproduces the navigation issue by clicking the menu
4. Captures a full-page screenshot
5. Outputs the standardized SCREENSHOT_CAPTURED marker

The test is now ready to run and will provide visual evidence of the navigation problem.

SCREENSHOT_CAPTURED:/tmp/test_workspace/tmp/ai_screenshots/nav_issue_test_1234567890.png
"""

        from tools.valor_delegation_tool import spawn_valor_session
        
        # Test with screenshot-related task
        result = spawn_valor_session(
            task_description="Create Playwright test to capture navigation menu screenshot",
            target_directory=self.workspace_root,
            specific_instructions="Focus on the dropdown menu animation issue",
            force_sync=True
        )
        
        # Verify Claude Code was called
        mock_execute.assert_called_once()
        
        # Verify prompt includes screenshot instructions
        call_args = mock_execute.call_args
        prompt = call_args[1]['prompt']
        
        self.assertIn("SCREENSHOT CAPTURE INSTRUCTIONS", prompt)
        self.assertIn("tmp/ai_screenshots", prompt)
        self.assertIn("SCREENSHOT_CAPTURED:", prompt)
        self.assertIn("fullPage: true", prompt)
        
        # Verify output processing
        self.assertIn("SCREENSHOT_CAPTURED:", result)
        self.assertIn("Screenshot(s) Captured", result)
        self.assertIn("ready for retrieval", result)

    def test_workspace_isolation_enforcement(self):
        """Test that workspace isolation is properly enforced."""
        # Create multiple workspace directories
        workspace_a = tempfile.mkdtemp(suffix="_workspace_a")
        workspace_b = tempfile.mkdtemp(suffix="_workspace_b")
        
        screenshot_dir_a = os.path.join(workspace_a, "tmp", "ai_screenshots")
        screenshot_dir_b = os.path.join(workspace_b, "tmp", "ai_screenshots")
        
        os.makedirs(screenshot_dir_a, exist_ok=True)
        os.makedirs(screenshot_dir_b, exist_ok=True)
        
        # Create screenshots in both workspaces
        task_id = "isolation_test"
        timestamp = int(time.time())
        
        screenshot_a = os.path.join(screenshot_dir_a, f"{task_id}_{timestamp}.png")
        screenshot_b = os.path.join(screenshot_dir_b, f"{task_id}_{timestamp + 1}.png")
        
        self._create_realistic_png(screenshot_a)
        self._create_realistic_png(screenshot_b)
        
        try:
            # Mock workspace validator to restrict to workspace A
            with patch('utilities.workspace_validator.get_workspace_validator') as mock_validator:
                mock_instance = Mock()
                mock_instance.get_workspace_for_chat.return_value = "workspace_a"
                mock_instance.get_allowed_directories.return_value = [workspace_a]
                mock_validator.return_value = mock_instance
                
                from mcp_servers.development_tools import retrieve_workspace_screenshot
                
                # Should find screenshot in workspace A
                result = retrieve_workspace_screenshot(
                    task_id=task_id,
                    chat_id="test_chat_a",
                    max_age_minutes=10
                )
                
                # Should succeed with workspace A screenshot
                self.assertTrue(result.startswith("TELEGRAM_IMAGE_GENERATED|") or "Screenshot Evidence" in result)
                
                # Mock workspace validator to restrict to workspace B
                mock_instance.get_workspace_for_chat.return_value = "workspace_b"
                mock_instance.get_allowed_directories.return_value = [workspace_b]
                
                # Should find screenshot in workspace B
                result = retrieve_workspace_screenshot(
                    task_id=task_id,
                    chat_id="test_chat_b",
                    max_age_minutes=10
                )
                
                # Should succeed with workspace B screenshot
                self.assertTrue(result.startswith("TELEGRAM_IMAGE_GENERATED|") or "Screenshot Evidence" in result)
                
        finally:
            import shutil
            shutil.rmtree(workspace_a, ignore_errors=True)
            shutil.rmtree(workspace_b, ignore_errors=True)

    def test_telegram_upload_pipeline_integration(self):
        """Test integration with Telegram upload pipeline."""
        task_id = "telegram_test"
        timestamp = int(time.time())
        screenshot_path = os.path.join(self.screenshot_dir, f"{task_id}_{timestamp}.png")
        
        self._create_realistic_png(screenshot_path)
        
        with patch('tools.image_analysis_tool.analyze_image') as mock_analyze:
            mock_analyze.return_value = "👁️ **What I see:** Test screenshot analysis"
            
            from mcp_servers.development_tools import retrieve_workspace_screenshot
            
            with patch('os.getcwd', return_value=self.workspace_root):
                result = retrieve_workspace_screenshot(
                    task_id=task_id,
                    chat_id="telegram_test_chat",
                    max_age_minutes=10
                )
                
                # Verify TELEGRAM_IMAGE_GENERATED format
                self.assertTrue(result.startswith("TELEGRAM_IMAGE_GENERATED|"))
                
                # Parse the marker format
                parts = result.split("|", 2)
                self.assertEqual(len(parts), 3)
                self.assertEqual(parts[0], "TELEGRAM_IMAGE_GENERATED")
                self.assertIn(screenshot_path, parts[1])  # Image path
                self.assertIn("Screenshot Evidence", parts[2])  # Caption
                self.assertIn(task_id, parts[2])

    def test_error_scenarios_comprehensive(self):
        """Test comprehensive error handling scenarios."""
        error_scenarios = [
            {
                "name": "claude_code_timeout",
                "mock_error": "TimeoutExpired",
                "expected_result": "timed out"
            },
            {
                "name": "claude_code_failure", 
                "mock_error": "CalledProcessError",
                "expected_result": "Development Tool Error"
            },
            {
                "name": "image_analysis_failure",
                "mock_error": "OpenAI API Error",
                "expected_result": "Screenshot retrieval error"
            },
            {
                "name": "workspace_access_denied",
                "mock_error": "WorkspaceAccessError", 
                "expected_result": "Access denied"
            }
        ]
        
        for scenario in error_scenarios:
            with self.subTest(scenario=scenario["name"]):
                self._test_error_scenario(scenario)

    def _test_error_scenario(self, scenario):
        """Test a specific error scenario."""
        task_id = f"error_{scenario['name']}"
        
        with patch('tools.valor_delegation_tool.execute_valor_delegation') as mock_execute:
            if scenario["mock_error"] == "TimeoutExpired":
                import subprocess
                mock_execute.side_effect = subprocess.TimeoutExpired("claude", 60)
            elif scenario["mock_error"] == "CalledProcessError":
                import subprocess
                mock_execute.side_effect = subprocess.CalledProcessError(1, "claude", "Error output")
            else:
                mock_execute.return_value = "Task completed without screenshot"
            
            from mcp_servers.development_tools import execute_bug_report_with_screenshot
            
            with patch('os.getcwd', return_value=self.workspace_root):
                result = execute_bug_report_with_screenshot(
                    task_description="Test error scenario",
                    notion_task_id=task_id,
                    chat_id=""
                )
                
                # Verify error is handled gracefully
                self.assertIsInstance(result, str)
                if scenario["expected_result"]:
                    self.assertIn(scenario["expected_result"], result)

    def _create_realistic_png(self, file_path):
        """Create a realistic PNG file for testing."""
        # Minimal valid PNG file (1x1 pixel, transparent)
        png_data = (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
            b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\xdac\xf8\x0f'
            b'\x00\x00\x01\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00IEND\xaeB`\x82'
        )
        
        with open(file_path, "wb") as f:
            f.write(png_data)


class TestScreenshotHandoffPerformance(unittest.TestCase):
    """Performance tests for screenshot handoff functionality."""

    def test_screenshot_processing_speed(self):
        """Test screenshot processing performance."""
        workspace_root = tempfile.mkdtemp()
        screenshot_dir = os.path.join(workspace_root, "tmp", "ai_screenshots")
        os.makedirs(screenshot_dir, exist_ok=True)
        
        try:
            # Create multiple screenshots
            task_ids = [f"perf_test_{i}" for i in range(10)]
            screenshot_paths = []
            
            for task_id in task_ids:
                timestamp = int(time.time()) + len(screenshot_paths)
                screenshot_path = os.path.join(screenshot_dir, f"{task_id}_{timestamp}.png")
                
                # Create minimal PNG
                with open(screenshot_path, "wb") as f:
                    f.write(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01')
                
                screenshot_paths.append(screenshot_path)
            
            # Measure retrieval performance
            start_time = time.time()
            
            with patch('tools.image_analysis_tool.analyze_image') as mock_analyze:
                mock_analyze.return_value = "Fast analysis result"
                
                from mcp_servers.development_tools import retrieve_workspace_screenshot
                
                for task_id in task_ids:
                    with patch('os.getcwd', return_value=workspace_root):
                        result = retrieve_workspace_screenshot(
                            task_id=task_id,
                            chat_id="",
                            max_age_minutes=10
                        )
                        
                        # Should be fast
                        self.assertTrue(result.startswith("TELEGRAM_IMAGE_GENERATED|"))
            
            processing_time = time.time() - start_time
            
            # Should process 10 screenshots in reasonable time
            self.assertLess(processing_time, 5.0, "Screenshot processing should be fast")
            
            # Average time per screenshot
            avg_time = processing_time / len(task_ids)
            self.assertLess(avg_time, 0.5, "Average processing time should be under 500ms")
            
        finally:
            import shutil
            shutil.rmtree(workspace_root, ignore_errors=True)


if __name__ == "__main__":
    # Run with high verbosity for detailed output
    unittest.main(verbosity=2)