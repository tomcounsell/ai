#!/usr/bin/env python3
"""
Unit tests for the documentation_tool
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Add the parent directory to the path to import tools
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.documentation_tool import (
    read_documentation,
    list_documentation_files,
    read_documentation_structured,
    DocumentationRequest,
    DocumentationResponse
)


class TestDocumentationTool(unittest.TestCase):
    """Test cases for documentation tool functions"""
    
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
        
        self.readme = self.docs_dir / "README.md"
        self.readme.write_text(
            "# Project README\n\nProject overview and setup instructions.",
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
    
    def test_read_documentation_success(self):
        """Test successful documentation reading"""
        result = read_documentation("architecture.md")
        
        # Check that result contains expected content
        self.assertIn("📖 **architecture.md**", result)
        self.assertIn("Architecture Documentation", result)
        self.assertIn("This is the system architecture.", result)
    
    def test_read_documentation_file_not_found(self):
        """Test reading non-existent documentation file"""
        result = read_documentation("nonexistent.md")
        
        # Check that error message is returned
        self.assertIn("📖 Documentation read error", result)
        self.assertIn("nonexistent.md", result)
        self.assertIn("File not found", result)
    
    def test_read_documentation_with_encoding(self):
        """Test reading documentation with specific encoding"""
        # Create a file with unicode content
        unicode_doc = self.docs_dir / "unicode.md"
        unicode_doc.write_text("# Café Documentation\n\nñoño 中文", encoding='utf-8')
        
        result = read_documentation("unicode.md", encoding="utf-8")
        
        # Check that unicode content is properly read
        self.assertIn("📖 **unicode.md**", result)
        self.assertIn("Café Documentation", result)
        self.assertIn("ñoño 中文", result)
    
    def test_list_documentation_files_success(self):
        """Test listing documentation files"""
        result = list_documentation_files()
        
        # Check that all test files are listed
        self.assertIn("📖 **Available Documentation Files:**", result)
        self.assertIn("- README.md", result)
        self.assertIn("- api-guide.md", result)
        self.assertIn("- architecture.md", result)
    
    def test_list_documentation_files_empty_directory(self):
        """Test listing files when docs directory is empty"""
        # Remove all files from docs directory
        for file in self.docs_dir.iterdir():
            file.unlink()
        
        result = list_documentation_files()
        
        # Check that empty directory message is returned
        self.assertIn("📖 No documentation files found", result)
    
    def test_list_documentation_files_no_docs_directory(self):
        """Test listing files when docs directory doesn't exist"""
        # Remove docs directory
        import shutil
        shutil.rmtree(self.docs_dir)
        
        result = list_documentation_files()
        
        # Check that error message is returned
        self.assertIn("📖 Error listing documentation files", result)
        self.assertIn("Docs directory not found", result)
    
    def test_read_documentation_structured_success(self):
        """Test structured documentation reading with success"""
        request = DocumentationRequest(filename="architecture.md", encoding="utf-8")
        response = read_documentation_structured(request)
        
        # Check response structure
        self.assertIsInstance(response, DocumentationResponse)
        self.assertTrue(response.success)
        self.assertEqual(response.filename, "architecture.md")
        self.assertIsNone(response.error_message)
        self.assertIn("Architecture Documentation", response.content)
    
    def test_read_documentation_structured_file_not_found(self):
        """Test structured documentation reading with file not found"""
        request = DocumentationRequest(filename="nonexistent.md")
        response = read_documentation_structured(request)
        
        # Check error response structure
        self.assertIsInstance(response, DocumentationResponse)
        self.assertFalse(response.success)
        self.assertEqual(response.filename, "nonexistent.md")
        self.assertIsNotNone(response.error_message)
        self.assertIn("File not found", response.error_message)
        self.assertEqual(response.content, "")
    
    def test_documentation_request_validation(self):
        """Test DocumentationRequest model validation"""
        # Test valid request
        request = DocumentationRequest(filename="test.md")
        self.assertEqual(request.filename, "test.md")
        self.assertEqual(request.encoding, "utf-8")  # default value
        
        # Test with custom encoding
        request = DocumentationRequest(filename="test.md", encoding="latin-1")
        self.assertEqual(request.encoding, "latin-1")
    
    def test_documentation_response_model(self):
        """Test DocumentationResponse model structure"""
        response = DocumentationResponse(
            success=True,
            content="Test content",
            filename="test.md"
        )
        
        self.assertTrue(response.success)
        self.assertEqual(response.content, "Test content")
        self.assertEqual(response.filename, "test.md")
        self.assertIsNone(response.error_message)
    
    @patch('tools.documentation_tool.FileReader')
    def test_unexpected_error_handling(self, mock_file_reader):
        """Test handling of unexpected errors"""
        # Mock FileReader to raise an unexpected exception
        mock_file_reader.return_value.read_docs_file.side_effect = RuntimeError("Unexpected error")
        
        result = read_documentation("test.md")
        
        # Check that unexpected error is handled gracefully
        self.assertIn("📖 Unexpected error reading", result)
        self.assertIn("test.md", result)
        self.assertIn("Unexpected error", result)
    
    def test_integration_with_real_docs(self):
        """Test integration with actual project docs if they exist"""
        # Change back to project directory to test with real docs
        os.chdir(self.original_cwd)
        
        # Check if project has docs directory
        project_docs = Path("docs")
        if project_docs.exists() and project_docs.is_dir():
            # Test listing real documentation files
            result = list_documentation_files()
            self.assertIn("📖 **Available Documentation Files:**", result)
            
            # Test reading a real file if any exist
            real_files = list(project_docs.glob("*.md"))
            if real_files:
                real_file = real_files[0].name
                result = read_documentation(real_file)
                self.assertIn(f"📖 **{real_file}**", result)


class TestDocumentationToolErrorScenarios(unittest.TestCase):
    """Test cases for error scenarios and edge cases"""
    
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
    
    def test_empty_filename(self):
        """Test behavior with empty filename"""
        result = read_documentation("")
        self.assertIn("📖 Documentation read error", result)
    
    def test_filename_with_path_traversal(self):
        """Test security: filename with path traversal attempt"""
        result = read_documentation("../../../etc/passwd")
        self.assertIn("📖 Documentation read error", result)
    
    def test_structured_request_with_invalid_encoding(self):
        """Test structured request with invalid encoding"""
        # Create a file
        docs_dir = Path("docs")
        docs_dir.mkdir(exist_ok=True)
        test_file = docs_dir / "test.md"
        test_file.write_text("Test content", encoding='utf-8')
        
        # Try to read with invalid encoding
        request = DocumentationRequest(filename="test.md", encoding="invalid-encoding")
        response = read_documentation_structured(request)
        
        # Should handle encoding error gracefully
        self.assertFalse(response.success)
        self.assertIsNotNone(response.error_message)


if __name__ == '__main__':
    print("🧪 Running Documentation Tool tests...")
    unittest.main(verbosity=2)