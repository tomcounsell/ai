#!/usr/bin/env python3
"""
Unit tests for the file_reader utility
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Add the parent directory to the path to import utilities
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utilities.file_reader import FileReader, FileReaderError, read_file, read_docs_file, list_docs_files


class TestFileReader(unittest.TestCase):
    """Test cases for FileReader class"""
    
    def setUp(self):
        """Set up test fixtures"""
        # Create a temporary directory for testing
        self.test_dir = tempfile.mkdtemp()
        self.test_path = Path(self.test_dir)
        
        # Create test files
        self.test_file = self.test_path / "test.txt"
        self.test_file.write_text("Hello, World!", encoding='utf-8')
        
        # Create docs directory with test files
        self.docs_dir = self.test_path / "docs"
        self.docs_dir.mkdir()
        
        self.docs_file = self.docs_dir / "test-doc.md"
        self.docs_file.write_text("# Test Documentation\n\nThis is a test.", encoding='utf-8')
        
        self.json_file = self.docs_dir / "config.json"
        self.json_file.write_text('{"key": "value"}', encoding='utf-8')
        
        # Create a subdirectory outside the test directory
        self.outside_dir = Path(tempfile.mkdtemp())
        self.outside_file = self.outside_dir / "outside.txt"
        self.outside_file.write_text("Outside content", encoding='utf-8')
        
    def tearDown(self):
        """Clean up test fixtures"""
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
        shutil.rmtree(self.outside_dir, ignore_errors=True)
    
    def test_init_with_base_path(self):
        """Test FileReader initialization with base path"""
        reader = FileReader(self.test_path)
        self.assertEqual(reader.base_path, self.test_path.resolve())
    
    def test_init_without_base_path(self):
        """Test FileReader initialization without base path"""
        reader = FileReader()
        self.assertEqual(reader.base_path, Path.cwd())
    
    def test_read_file_success(self):
        """Test successful file reading"""
        reader = FileReader(self.test_path)
        content = reader.read_file(self.test_file)
        self.assertEqual(content, "Hello, World!")
    
    def test_read_file_with_relative_path(self):
        """Test reading file with relative path"""
        # Change to test directory to make relative path work
        original_cwd = os.getcwd()
        try:
            os.chdir(self.test_path)
            reader = FileReader(self.test_path)
            content = reader.read_file("test.txt")
            self.assertEqual(content, "Hello, World!")
        finally:
            os.chdir(original_cwd)
    
    def test_read_file_not_found(self):
        """Test reading non-existent file"""
        reader = FileReader(self.test_path)
        nonexistent_path = self.test_path / "nonexistent.txt"
        with self.assertRaises(FileReaderError) as context:
            reader.read_file(nonexistent_path)
        self.assertIn("File not found", str(context.exception))
    
    def test_read_file_outside_base_path(self):
        """Test reading file outside allowed base path"""
        reader = FileReader(self.test_path)
        with self.assertRaises(FileReaderError) as context:
            reader.read_file(self.outside_file)
        self.assertIn("outside allowed directory", str(context.exception))
    
    def test_read_file_directory_instead_of_file(self):
        """Test trying to read a directory as a file"""
        reader = FileReader(self.test_path)
        with self.assertRaises(FileReaderError) as context:
            reader.read_file(self.docs_dir)
        self.assertIn("Path is not a file", str(context.exception))
    
    def test_read_file_with_different_encoding(self):
        """Test reading file with different encoding"""
        # Create a file with specific encoding
        unicode_file = self.test_path / "unicode.txt"
        unicode_file.write_text("Café ñoño 中文", encoding='utf-8')
        
        reader = FileReader(self.test_path)
        content = reader.read_file(unicode_file, encoding='utf-8')
        self.assertEqual(content, "Café ñoño 中文")
    
    def test_read_docs_file_success(self):
        """Test successful docs file reading"""
        reader = FileReader(self.test_path)
        content = reader.read_docs_file("test-doc.md")
        self.assertEqual(content, "# Test Documentation\n\nThis is a test.")
    
    def test_read_docs_file_not_found(self):
        """Test reading non-existent docs file"""
        reader = FileReader(self.test_path)
        with self.assertRaises(FileReaderError) as context:
            reader.read_docs_file("nonexistent.md")
        self.assertIn("File not found", str(context.exception))
    
    def test_list_docs_files_success(self):
        """Test listing docs directory files"""
        reader = FileReader(self.test_path)
        files = reader.list_docs_files()
        expected_files = ["config.json", "test-doc.md"]
        self.assertEqual(sorted(files), sorted(expected_files))
    
    def test_list_docs_files_no_docs_directory(self):
        """Test listing docs files when docs directory doesn't exist"""
        # Create a temporary directory without docs
        temp_dir = tempfile.mkdtemp()
        try:
            reader = FileReader(temp_dir)
            with self.assertRaises(FileReaderError) as context:
                reader.list_docs_files()
            self.assertIn("Docs directory not found", str(context.exception))
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_file_exists_true(self):
        """Test file_exists with existing file"""
        reader = FileReader(self.test_path)
        self.assertTrue(reader.file_exists(self.test_file))
    
    def test_file_exists_false(self):
        """Test file_exists with non-existent file"""
        reader = FileReader(self.test_path)
        self.assertFalse(reader.file_exists("nonexistent.txt"))
    
    def test_file_exists_directory(self):
        """Test file_exists with directory"""
        reader = FileReader(self.test_path)
        self.assertFalse(reader.file_exists(self.docs_dir))
    
    def test_file_exists_outside_base_path(self):
        """Test file_exists with file outside base path"""
        reader = FileReader(self.test_path)
        self.assertFalse(reader.file_exists(self.outside_file))
    
    @patch('utilities.file_reader.logger')
    def test_logging_on_success(self, mock_logger):
        """Test that successful operations are logged"""
        reader = FileReader(self.test_path)
        reader.read_file(self.test_file)
        mock_logger.info.assert_called()
    
    def test_logging_integration(self):
        """Test that logging is properly integrated"""
        # Just test that logger import works and is accessible
        from utilities.file_reader import logger
        self.assertIsNotNone(logger)


class TestConvenienceFunctions(unittest.TestCase):
    """Test cases for convenience functions"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.original_cwd = os.getcwd()
        
        # Create a temporary directory and change to it
        self.test_dir = tempfile.mkdtemp()
        os.chdir(self.test_dir)
        
        # Create test files
        self.test_file = Path("test.txt")
        self.test_file.write_text("Test content", encoding='utf-8')
        
        # Create docs directory
        self.docs_dir = Path("docs")
        self.docs_dir.mkdir()
        self.docs_file = self.docs_dir / "readme.md"
        self.docs_file.write_text("# README", encoding='utf-8')
    
    def tearDown(self):
        """Clean up test fixtures"""
        os.chdir(self.original_cwd)
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_read_file_function(self):
        """Test read_file convenience function"""
        content = read_file("test.txt")
        self.assertEqual(content, "Test content")
    
    def test_read_docs_file_function(self):
        """Test read_docs_file convenience function"""
        content = read_docs_file("readme.md")
        self.assertEqual(content, "# README")
    
    def test_list_docs_files_function(self):
        """Test list_docs_files convenience function"""
        files = list_docs_files()
        self.assertEqual(files, ["readme.md"])


class TestErrorHandling(unittest.TestCase):
    """Test cases for error handling scenarios"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.test_dir = tempfile.mkdtemp()
        self.test_path = Path(self.test_dir)
    
    def tearDown(self):
        """Clean up test fixtures"""
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_invalid_path_characters(self):
        """Test handling of invalid path characters"""
        reader = FileReader(self.test_path)
        with self.assertRaises(FileReaderError):
            # This might not fail on all systems, but should be handled gracefully
            reader.read_file("\x00invalid")
    
    @patch('builtins.open', side_effect=PermissionError("Permission denied"))
    def test_permission_error(self, mock_open):
        """Test handling of permission errors"""
        reader = FileReader(self.test_path)
        # Create a test file
        test_file = self.test_path / "test.txt"
        test_file.write_text("content")
        
        with self.assertRaises(FileReaderError) as context:
            reader.read_file(test_file)
        self.assertIn("Permission denied", str(context.exception))
    
    @patch('builtins.open', side_effect=UnicodeDecodeError('utf-8', b'', 0, 1, 'invalid'))
    def test_unicode_decode_error(self, mock_open):
        """Test handling of unicode decode errors"""
        reader = FileReader(self.test_path)
        # Create a test file
        test_file = self.test_path / "test.txt"
        test_file.write_text("content")
        
        with self.assertRaises(FileReaderError) as context:
            reader.read_file(test_file)
        self.assertIn("Error reading file", str(context.exception))


if __name__ == '__main__':
    print("🧪 Running FileReader utility tests...")
    unittest.main(verbosity=2)