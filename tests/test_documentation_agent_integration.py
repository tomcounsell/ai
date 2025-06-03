#!/usr/bin/env python3
"""
Integration tests for documentation tool PydanticAI agent integration.
Tests the agent tool wrapper functions added to the valor_agent.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Add the parent directory to the path to import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.valor.agent import read_project_documentation, list_project_documentation, ValorContext


class TestDocumentationAgentIntegration(unittest.TestCase):
    """Test cases for documentation tool agent integration"""
    
    def setUp(self):
        """Set up test fixtures"""
        # Create a temporary directory for testing
        self.test_dir = tempfile.mkdtemp()
        self.test_path = Path(self.test_dir)
        
        # Create docs directory with test files
        self.docs_dir = self.test_path / "docs"
        self.docs_dir.mkdir()
        
        # Create test documentation files
        self.arch_doc = self.docs_dir / "architecture.md"
        self.arch_doc.write_text(
            "# Architecture Documentation\n\nThis is the system architecture.",
            encoding='utf-8'
        )
        
        self.api_doc = self.docs_dir / "api-guide.md"
        self.api_doc.write_text(
            "# API Guide\n\nAPI documentation here.",
            encoding='utf-8'
        )
        
        # Save original working directory and change to test directory
        self.original_cwd = os.getcwd()
        os.chdir(self.test_dir)
        
    def tearDown(self):
        """Clean up test fixtures"""
        os.chdir(self.original_cwd)
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_read_project_documentation_with_mock_context(self):
        """Test read_project_documentation agent tool with mock RunContext"""
        # Create lightweight mock for RunContext - only mock the interface
        mock_ctx = MagicMock()
        mock_ctx.deps = ValorContext(
            chat_id=12345,
            username="test_user"
        )
        
        # Test reading existing file
        result = read_project_documentation(mock_ctx, "architecture.md")
        
        # Verify agent-friendly formatting
        self.assertIn("📖 **architecture.md**", result)
        self.assertIn("Architecture Documentation", result)
        self.assertIn("This is the system architecture.", result)
    
    def test_read_project_documentation_file_not_found(self):
        """Test read_project_documentation with non-existent file"""
        mock_ctx = MagicMock()
        mock_ctx.deps = ValorContext(chat_id=12345, username="test_user")
        
        # Test reading non-existent file
        result = read_project_documentation(mock_ctx, "nonexistent.md")
        
        # Verify error handling
        self.assertIn("📖 Documentation read error", result)
        self.assertIn("nonexistent.md", result)
    
    def test_read_project_documentation_empty_filename(self):
        """Test read_project_documentation with empty filename"""
        mock_ctx = MagicMock()
        mock_ctx.deps = ValorContext(chat_id=12345, username="test_user")
        
        # Test with empty filename
        result = read_project_documentation(mock_ctx, "")
        
        # Verify validation error
        self.assertIn("📖 Error: Please specify a documentation filename.", result)
    
    def test_read_project_documentation_whitespace_filename(self):
        """Test read_project_documentation with whitespace-only filename"""
        mock_ctx = MagicMock()
        mock_ctx.deps = ValorContext(chat_id=12345, username="test_user")
        
        # Test with whitespace filename
        result = read_project_documentation(mock_ctx, "   ")
        
        # Verify validation error
        self.assertIn("📖 Error: Please specify a documentation filename.", result)
    
    def test_read_project_documentation_strips_whitespace(self):
        """Test read_project_documentation strips whitespace from filename"""
        mock_ctx = MagicMock()
        mock_ctx.deps = ValorContext(chat_id=12345, username="test_user")
        
        # Test with filename that has whitespace
        result = read_project_documentation(mock_ctx, "  architecture.md  ")
        
        # Should successfully read the file (whitespace stripped)
        self.assertIn("📖 **architecture.md**", result)
        self.assertIn("Architecture Documentation", result)
    
    def test_list_project_documentation_with_mock_context(self):
        """Test list_project_documentation agent tool with mock RunContext"""
        mock_ctx = MagicMock()
        mock_ctx.deps = ValorContext(
            chat_id=12345,
            username="test_user"
        )
        
        # Test listing files
        result = list_project_documentation(mock_ctx)
        
        # Verify agent-friendly formatting and content
        self.assertIn("📖 **Available Documentation Files:**", result)
        self.assertIn("- api-guide.md", result)
        self.assertIn("- architecture.md", result)
    
    def test_list_project_documentation_empty_directory(self):
        """Test list_project_documentation with empty docs directory"""
        # Remove all files from docs directory
        for file in self.docs_dir.iterdir():
            file.unlink()
        
        mock_ctx = MagicMock()
        mock_ctx.deps = ValorContext(chat_id=12345, username="test_user")
        
        result = list_project_documentation(mock_ctx)
        
        # Verify empty directory handling
        self.assertIn("📖 No documentation files found", result)
    
    def test_agent_tools_context_parameter_handling(self):
        """Test that agent tools properly handle context parameters"""
        mock_ctx = MagicMock()
        mock_ctx.deps = ValorContext(
            chat_id=98765,
            username="another_user",
            is_group_chat=True
        )
        
        # Both tools should accept context and work regardless of context content
        read_result = read_project_documentation(mock_ctx, "architecture.md")
        list_result = list_project_documentation(mock_ctx)
        
        # Verify both work with different context
        self.assertIn("📖 **architecture.md**", read_result)
        self.assertIn("📖 **Available Documentation Files:**", list_result)
    
    def test_agent_tools_no_hanging_behavior(self):
        """Test that agent tools don't cause hanging or blocking"""
        import time
        
        mock_ctx = MagicMock()
        mock_ctx.deps = ValorContext(chat_id=12345, username="test_user")
        
        # Test that tools complete quickly
        start_time = time.time()
        
        read_result = read_project_documentation(mock_ctx, "architecture.md")
        list_result = list_project_documentation(mock_ctx)
        
        end_time = time.time()
        execution_time = end_time - start_time
        
        # Both operations should complete in under 1 second
        self.assertLess(execution_time, 1.0)
        
        # Verify results are valid
        self.assertIn("📖", read_result)
        self.assertIn("📖", list_result)
    
    def test_integration_with_real_project_docs(self):
        """Test integration with actual project docs if they exist"""
        # Change back to project directory to test with real docs
        os.chdir(self.original_cwd)
        
        mock_ctx = MagicMock()
        mock_ctx.deps = ValorContext(chat_id=12345, username="test_user")
        
        # Check if project has docs directory
        project_docs = Path("docs")
        if project_docs.exists() and project_docs.is_dir():
            # Test listing real documentation files
            result = list_project_documentation(mock_ctx)
            self.assertIn("📖 **Available Documentation Files:**", result)
            
            # Test reading a real file if any exist
            real_files = list(project_docs.glob("*.md"))
            if real_files:
                real_file = real_files[0].name
                result = read_project_documentation(mock_ctx, real_file)
                self.assertIn(f"📖 **{real_file}**", result)


class TestDocumentationAgentIntegrationErrorScenarios(unittest.TestCase):
    """Test cases for error scenarios in agent integration"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.test_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.test_dir)
    
    def tearDown(self):
        """Clean up test fixtures"""
        os.chdir(self.original_cwd)
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_path_traversal_protection(self):
        """Test that agent tools protect against path traversal"""
        mock_ctx = MagicMock()
        mock_ctx.deps = ValorContext(chat_id=12345, username="test_user")
        
        # Try path traversal attack
        result = read_project_documentation(mock_ctx, "../../../etc/passwd")
        
        # Should return error, not system file content
        self.assertIn("📖 Documentation read error", result)
        self.assertNotIn("root:", result)  # Should not contain system file content
    
    def test_context_dependency_handling(self):
        """Test tools work correctly with minimal context"""
        mock_ctx = MagicMock()
        mock_ctx.deps = ValorContext()  # Minimal context
        
        # Tools should still work with minimal context
        result = list_project_documentation(mock_ctx)
        
        # Should handle missing context gracefully
        self.assertIn("📖", result)


if __name__ == '__main__':
    print("🧪 Running Documentation Agent Integration tests...")
    unittest.main(verbosity=2)