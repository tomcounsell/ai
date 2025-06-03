"""Tests for doc_summary_tool.py - Document reading and summarization functionality."""

import pytest
import tempfile
import os
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from tools.doc_summary_tool import (
    DocumentSection,
    DocumentSummary,
    SummaryConfig,
    summarize_document,
    summarize_url_document,
    batch_summarize_docs,
    quick_doc_summary,
    technical_doc_analysis,
    _parse_markdown_sections,
    _parse_rst_sections,
    _parse_python_sections,
    _parse_text_sections,
    _extract_document_title,
    _detect_document_type
)


class TestDocumentSection:
    """Test DocumentSection model validation."""
    
    def test_valid_document_section(self):
        """Test creating valid document section."""
        section = DocumentSection(
            title="Introduction",
            content="This is the introduction section.",
            level=1,
            word_count=5,
            key_points=["Main concept", "Supporting idea"]
        )
        
        assert section.title == "Introduction"
        assert section.content == "This is the introduction section."
        assert section.level == 1
        assert section.word_count == 5
        assert section.key_points == ["Main concept", "Supporting idea"]
    
    def test_section_without_key_points(self):
        """Test document section without key points."""
        section = DocumentSection(
            title="Section",
            content="Content",
            level=2,
            word_count=1
        )
        
        assert section.key_points == []


class TestDocumentSummary:
    """Test DocumentSummary model validation."""
    
    def test_valid_document_summary(self):
        """Test creating valid document summary."""
        sections = [
            DocumentSection(
                title="Section 1",
                content="Content 1",
                level=1,
                word_count=2
            )
        ]
        
        summary = DocumentSummary(
            title="Test Document",
            total_words=100,
            total_sections=1,
            summary="This is a test document summary.",
            key_insights=["Key insight 1", "Key insight 2"],
            sections=sections,
            reading_time_minutes=1,
            document_type="markdown",
            main_topics=["topic1", "topic2"]
        )
        
        assert summary.title == "Test Document"
        assert summary.total_words == 100
        assert summary.total_sections == 1
        assert len(summary.sections) == 1
        assert summary.reading_time_minutes == 1
        assert summary.document_type == "markdown"


class TestSummaryConfig:
    """Test SummaryConfig model validation."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = SummaryConfig()
        
        assert config.max_section_words == 500
        assert config.include_code_blocks == True
        assert config.extract_key_points == True
        assert config.focus_topics is None
        assert config.summary_style == "comprehensive"
    
    def test_custom_config(self):
        """Test custom configuration."""
        config = SummaryConfig(
            max_section_words=200,
            include_code_blocks=False,
            summary_style="brief",
            focus_topics=["API", "integration"]
        )
        
        assert config.max_section_words == 200
        assert config.include_code_blocks == False
        assert config.summary_style == "brief"
        assert config.focus_topics == ["API", "integration"]


@pytest.fixture
def temp_markdown_file():
    """Create temporary markdown file."""
    content = '''# Main Title

This is the introduction paragraph.

## Section 1

Content for section 1 with some details.

- Key point 1
- Key point 2
- Key point 3

## Section 2

### Subsection 2.1

More detailed content here.

1. Numbered item 1
2. Numbered item 2

## Conclusion

Final thoughts and summary.
'''
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
        f.write(content)
        temp_path = f.name
    
    yield temp_path
    
    try:
        os.unlink(temp_path)
    except:
        pass


@pytest.fixture
def temp_python_file():
    """Create temporary Python file."""
    content = '''"""
Test module for document summarization.

This module demonstrates various Python constructs.
"""

import os
import sys

class TestClass:
    """A test class for demonstration."""
    
    def __init__(self, value):
        """Initialize with a value."""
        self.value = value
    
    def get_value(self):
        """Return the stored value."""
        return self.value

def main_function():
    """
    Main function that does important work.
    
    This function demonstrates:
    - Variable assignment
    - Function calls
    - Return statements
    """
    test_obj = TestClass(42)
    result = test_obj.get_value()
    return result

if __name__ == "__main__":
    print(main_function())
'''
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(content)
        temp_path = f.name
    
    yield temp_path
    
    try:
        os.unlink(temp_path)
    except:
        pass


@pytest.fixture
def temp_text_file():
    """Create temporary text file."""
    content = '''Introduction to Document Processing

Document processing is an important task in many applications. It involves reading, analyzing, and extracting information from various document formats.

Key Benefits

The main benefits of automated document processing include:
- Efficiency improvements
- Consistency in analysis
- Scalability for large document sets

Implementation Considerations

When implementing document processing systems, consider the following factors:

1. File format support
2. Performance requirements
3. Accuracy needs
4. Integration requirements

Conclusion

Effective document processing can significantly improve workflow efficiency and information extraction capabilities.
'''
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(content)
        temp_path = f.name
    
    yield temp_path
    
    try:
        os.unlink(temp_path)
    except:
        pass


class TestParseMarkdownSections:
    """Test markdown parsing functionality."""
    
    def test_basic_markdown_parsing(self, temp_markdown_file):
        """Test parsing basic markdown structure."""
        with open(temp_markdown_file, 'r') as f:
            content = f.read()
        
        sections = _parse_markdown_sections(content)
        
        assert len(sections) >= 3  # Should have multiple sections
        
        # Check first section
        intro_section = sections[0]
        assert intro_section.title == "Main Title"
        assert intro_section.level == 1
        assert "introduction paragraph" in intro_section.content
        
        # Find section with bullet points
        bullet_section = next((s for s in sections if "Key point" in s.content), None)
        assert bullet_section is not None
        assert len(bullet_section.key_points) == 3
        assert "Key point 1" in bullet_section.key_points
    
    def test_nested_heading_levels(self):
        """Test parsing nested heading levels."""
        content = '''# Level 1

Content for level 1.

## Level 2

Content for level 2.

### Level 3

Content for level 3.

#### Level 4

Content for level 4.
'''
        sections = _parse_markdown_sections(content)
        
        assert len(sections) == 4
        assert sections[0].level == 1
        assert sections[1].level == 2
        assert sections[2].level == 3
        assert sections[3].level == 4
    
    def test_markdown_with_code_blocks(self):
        """Test parsing markdown with code blocks."""
        content = '''# Code Example

Here's a Python example:

```python
def hello():
    print("Hello, World!")
```

This code demonstrates basic function syntax.
'''
        sections = _parse_markdown_sections(content)
        
        assert len(sections) == 1
        assert "```python" in sections[0].content
        assert "def hello" in sections[0].content


class TestParseRstSections:
    """Test reStructuredText parsing functionality."""
    
    def test_basic_rst_parsing(self):
        """Test parsing basic RST structure."""
        content = '''Main Title
==========

This is the introduction.

Section 1
---------

Content for section 1.

Section 2
---------

Content for section 2.

Subsection 2.1
~~~~~~~~~~~~~~

Subsection content.
'''
        sections = _parse_rst_sections(content)
        
        assert len(sections) >= 3
        
        # Check title levels
        title_section = next((s for s in sections if s.title == "Main Title"), None)
        assert title_section is not None
        assert title_section.level == 1
        
        section1 = next((s for s in sections if s.title == "Section 1"), None)
        assert section1 is not None
        assert section1.level == 2
    
    def test_rst_with_different_underlines(self):
        """Test RST with different underline characters."""
        content = '''Title One
=========

Content one.

Title Two
---------

Content two.

Title Three
~~~~~~~~~~~

Content three.

Title Four
^^^^^^^^^^

Content four.
'''
        sections = _parse_rst_sections(content)
        
        assert len(sections) == 4
        # Different underline characters should create different levels
        levels = [s.level for s in sections]
        assert len(set(levels)) > 1  # Should have different levels


class TestParsePythonSections:
    """Test Python code parsing functionality."""
    
    def test_python_class_and_function_parsing(self, temp_python_file):
        """Test parsing Python classes and functions."""
        with open(temp_python_file, 'r') as f:
            content = f.read()
        
        sections = _parse_python_sections(content)
        
        assert len(sections) >= 2  # Should have class and function sections
        
        # Check for class section
        class_section = next((s for s in sections if "TestClass" in s.title), None)
        assert class_section is not None
        assert "class TestClass" in class_section.content
        
        # Check for function section
        func_section = next((s for s in sections if "main_function" in s.title), None)
        assert func_section is not None
        assert "def main_function" in func_section.content
    
    def test_python_with_docstrings(self):
        """Test Python parsing extracts docstring information."""
        content = '''
class ExampleClass:
    """
    An example class for testing.
    
    This class demonstrates various features.
    """
    
    def example_method(self):
        """Return an example value."""
        return "example"

def example_function():
    """
    Example function with detailed docstring.
    
    Returns:
        str: Example return value
    """
    return "function result"
'''
        sections = _parse_python_sections(content)
        
        assert len(sections) == 2
        
        # Check that docstrings are included in content
        class_section = next((s for s in sections if "ExampleClass" in s.title), None)
        assert class_section is not None
        assert "example class for testing" in class_section.content.lower()


class TestParseTextSections:
    """Test plain text parsing functionality."""
    
    def test_text_paragraph_parsing(self, temp_text_file):
        """Test parsing plain text into paragraphs."""
        with open(temp_text_file, 'r') as f:
            content = f.read()
        
        sections = _parse_text_sections(content)
        
        assert len(sections) >= 4  # Should split into multiple paragraphs
        
        # Check first section
        intro_section = sections[0]
        assert "Introduction to Document Processing" in intro_section.title
        
        # Check that bullet points are extracted
        benefits_section = next((s for s in sections if "benefits" in s.content.lower()), None)
        if benefits_section:
            assert len(benefits_section.key_points) > 0
    
    def test_text_with_numbered_lists(self):
        """Test text parsing with numbered lists."""
        content = '''Main Topic

This is an introduction.

Important Steps

Follow these steps:

1. First step description
2. Second step description  
3. Third step description

Conclusion

Final summary paragraph.
'''
        sections = _parse_text_sections(content)
        
        # Find section with numbered list
        steps_section = next((s for s in sections if "First step" in s.content), None)
        assert steps_section is not None
        assert len(steps_section.key_points) == 3
        assert "First step description" in steps_section.key_points


class TestDocumentTypeDetection:
    """Test document type detection functionality."""
    
    def test_markdown_detection(self, temp_markdown_file):
        """Test markdown file type detection."""
        with open(temp_markdown_file, 'r') as f:
            content = f.read()
        
        doc_type = _detect_document_type(temp_markdown_file, content)
        assert doc_type == "markdown"
    
    def test_python_detection(self, temp_python_file):
        """Test Python file type detection."""
        with open(temp_python_file, 'r') as f:
            content = f.read()
        
        doc_type = _detect_document_type(temp_python_file, content)
        assert doc_type == "python_code"
    
    def test_text_detection(self, temp_text_file):
        """Test plain text file type detection."""
        with open(temp_text_file, 'r') as f:
            content = f.read()
        
        doc_type = _detect_document_type(temp_text_file, content)
        assert doc_type == "text"
    
    def test_unknown_extension(self):
        """Test unknown file extension handling."""
        doc_type = _detect_document_type("test.unknown", "content")
        assert doc_type == "unknown"


class TestExtractDocumentTitle:
    """Test document title extraction functionality."""
    
    def test_markdown_title_extraction(self):
        """Test extracting title from markdown content."""
        content = '''# Main Document Title

This is the content.
'''
        title = _extract_document_title(content, "test.md")
        assert title == "Main Document Title"
    
    def test_multiple_headings_takes_first(self):
        """Test that first heading is used as title."""
        content = '''# First Title

Some content.

## Second Title

More content.
'''
        title = _extract_document_title(content, "test.md")
        assert title == "First Title"
    
    def test_fallback_to_filename(self):
        """Test fallback to filename when no heading found."""
        content = '''This is content without headings.'''
        title = _extract_document_title(content, "/path/to/my_test_document.md")
        assert title == "My Test Document"  # Should format filename
    
    def test_filename_formatting(self):
        """Test filename formatting for title."""
        title = _extract_document_title("", "complex_file-name_with-underscores.txt")
        assert title == "Complex File Name With Underscores"


class TestSummarizeDocument:
    """Test main document summarization functionality."""
    
    def test_markdown_summarization(self, temp_markdown_file):
        """Test summarizing markdown document."""
        summary = summarize_document(temp_markdown_file)
        
        assert isinstance(summary, DocumentSummary)
        assert summary.title is not None
        assert summary.total_words > 0
        assert summary.total_sections > 0
        assert len(summary.sections) > 0
        assert summary.document_type == "markdown"
        assert summary.reading_time_minutes > 0
        assert len(summary.main_topics) > 0
    
    def test_python_file_summarization(self, temp_python_file):
        """Test summarizing Python code file."""
        summary = summarize_document(temp_python_file)
        
        assert summary.document_type == "python_code"
        assert summary.total_sections >= 2  # Should have class and function sections
        
        # Check that Python constructs are identified
        section_titles = [s.title for s in summary.sections]
        assert any("TestClass" in title for title in section_titles)
        assert any("main_function" in title for title in section_titles)
    
    def test_custom_config_application(self, temp_markdown_file):
        """Test that custom configuration is applied."""
        config = SummaryConfig(
            max_section_words=100,
            summary_style="brief"
        )
        
        summary = summarize_document(temp_markdown_file, config)
        
        assert summary.summary is not None
        # With brief style, summary should be concise
        assert len(summary.summary.split()) < 50
    
    def test_nonexistent_file_error(self):
        """Test error handling for nonexistent file."""
        with pytest.raises(FileNotFoundError):
            summarize_document("/nonexistent/file.md")


class TestSummarizeUrlDocument:
    """Test URL document summarization with mocking."""
    
    @patch('tools.doc_summary_tool.subprocess.run')
    def test_successful_url_download(self, mock_run):
        """Test successful URL document download and summarization."""
        # Mock successful curl response
        mock_content = '''# GitHub README

This is a test README from GitHub.

## Installation

Run these commands to install.

## Usage

Here's how to use this tool.
'''
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=mock_content
        )
        
        summary = summarize_url_document("https://github.com/user/repo/blob/main/README.md")
        
        assert isinstance(summary, DocumentSummary)
        assert "GitHub README" in summary.title
        assert "github.com" in summary.title  # Should include domain
        assert summary.total_sections > 0
        
        # Verify curl was called
        assert mock_run.called
        call_args = mock_run.call_args[0][0]
        assert "curl" in call_args
        assert "https://github.com/user/repo/blob/main/README.md" in call_args
    
    @patch('tools.doc_summary_tool.subprocess.run')
    def test_failed_url_download(self, mock_run):
        """Test handling of failed URL download."""
        # Mock failed curl response
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="Connection failed"
        )
        
        # Use a valid URL format that passes validation
        summary = summarize_url_document("https://github.com/user/repo/blob/main/nonexistent.md")
        
        assert summary.document_type == "error"
        assert summary.total_words == 0
        assert "Failed to process URL" in summary.summary
        assert "Connection failed" in summary.summary  # Error message should be in summary


class TestBatchSummarizeDocs:
    """Test batch document summarization."""
    
    def test_successful_batch_processing(self, temp_markdown_file, temp_python_file):
        """Test batch processing of multiple documents."""
        doc_paths = [temp_markdown_file, temp_python_file]
        
        summaries = batch_summarize_docs(doc_paths)
        
        assert len(summaries) == 2
        assert temp_markdown_file in summaries
        assert temp_python_file in summaries
        
        # Check that both were processed successfully
        md_summary = summaries[temp_markdown_file]
        py_summary = summaries[temp_python_file]
        
        assert md_summary.document_type == "markdown"
        assert py_summary.document_type == "python_code"
        assert md_summary.total_sections > 0
        assert py_summary.total_sections > 0
    
    def test_batch_with_invalid_file(self, temp_markdown_file):
        """Test batch processing with some invalid files."""
        doc_paths = [temp_markdown_file, "/nonexistent/file.md"]
        
        summaries = batch_summarize_docs(doc_paths)
        
        assert len(summaries) == 2
        
        # Valid file should process successfully
        valid_summary = summaries[temp_markdown_file]
        assert valid_summary.document_type == "markdown"
        
        # Invalid file should have error summary
        error_summary = summaries["/nonexistent/file.md"]
        assert error_summary.document_type == "error"
        assert "Failed to process" in error_summary.summary
    
    def test_empty_batch(self):
        """Test batch processing with empty list."""
        summaries = batch_summarize_docs([])
        assert len(summaries) == 0


class TestConvenienceFunctions:
    """Test convenience functions for common use cases."""
    
    def test_quick_doc_summary(self, temp_markdown_file):
        """Test quick document summary function."""
        result = quick_doc_summary(temp_markdown_file)
        
        assert isinstance(result, str)
        assert ":" in result  # Should have title: summary format
        
        # Should be concise
        assert len(result.split()) < 100
    
    def test_technical_doc_analysis(self, temp_python_file):
        """Test technical document analysis function."""
        summary = technical_doc_analysis(temp_python_file)
        
        assert isinstance(summary, DocumentSummary)
        assert summary.document_type == "python_code"
        
        # Should extract key points
        assert any(len(s.key_points) > 0 for s in summary.sections)
        
        # Should have detailed analysis
        assert summary.total_sections > 0
        assert len(summary.key_insights) > 0


class TestEdgeCases:
    """Test edge cases and error conditions."""
    
    def test_empty_file(self):
        """Test handling of empty file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("")
            temp_path = f.name
        
        try:
            summary = summarize_document(temp_path)
            assert summary.total_words == 0
            assert summary.total_sections == 0
        finally:
            try:
                os.unlink(temp_path)
            except:
                pass
    
    def test_very_large_sections(self):
        """Test handling of very large document sections."""
        # Create content with very long section
        large_content = "# Large Section\n\n" + " ".join(["word"] * 1000)
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write(large_content)
            temp_path = f.name
        
        try:
            config = SummaryConfig(max_section_words=100)
            summary = summarize_document(temp_path, config)
            
            # Section should be summarized/truncated
            section_content = summary.sections[0].content
            assert len(section_content.split()) <= 200  # Should be reduced
        finally:
            try:
                os.unlink(temp_path)
            except:
                pass
    
    def test_unicode_content(self):
        """Test handling of Unicode content."""
        unicode_content = '''# Tëst Dócümënt

This contains ünicödë characters: 中文, العربية, Русский.

## Sëctión 1

More ünicödë content here.
'''
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8') as f:
            f.write(unicode_content)
            temp_path = f.name
        
        try:
            summary = summarize_document(temp_path)
            assert "Tëst Dócümënt" in summary.title
            assert summary.total_sections > 0
        finally:
            try:
                os.unlink(temp_path)
            except:
                pass


class TestSecurity:
    """Test security aspects of document summarization."""
    
    def test_url_validation_blocks_invalid_schemes(self):
        """Test that invalid URL schemes are blocked."""
        from tools.doc_summary_tool import _is_safe_url
        
        # Test invalid schemes
        assert not _is_safe_url("file:///etc/passwd")
        assert not _is_safe_url("ftp://example.com/file.txt")
        assert not _is_safe_url("javascript:alert(1)")
        assert not _is_safe_url("data:text/html,<script>alert(1)</script>")
        
        # Test valid schemes
        assert _is_safe_url("https://github.com/user/repo/file.md")
        assert _is_safe_url("http://example.com/doc.txt")
    
    def test_url_validation_blocks_localhost(self):
        """Test that localhost and private IPs are blocked."""
        from tools.doc_summary_tool import _is_safe_url
        
        # Test localhost variations
        assert not _is_safe_url("https://localhost/file.md")
        assert not _is_safe_url("https://127.0.0.1/file.md")
        assert not _is_safe_url("https://0.0.0.0/file.md")
        
        # Test private network ranges
        assert not _is_safe_url("https://192.168.1.1/file.md")
        assert not _is_safe_url("https://10.0.0.1/file.md")
        assert not _is_safe_url("https://172.16.0.1/file.md")
    
    def test_url_validation_blocks_malformed_urls(self):
        """Test that malformed URLs are blocked."""
        from tools.doc_summary_tool import _is_safe_url
        
        # Test malformed URLs
        assert not _is_safe_url("")
        assert not _is_safe_url("not-a-url")
        assert not _is_safe_url("https://")
        assert not _is_safe_url("https:///file.md")
        assert not _is_safe_url("https://invalid..domain.com/file.md")
    
    def test_unsafe_url_returns_error_summary(self):
        """Test that unsafe URLs return error summaries without subprocess execution."""
        summary = summarize_url_document("file:///etc/passwd")
        
        assert summary.document_type == "error"
        assert summary.total_words == 0
        assert "Invalid or unsafe URL" in summary.summary
        assert "URL validation failed" in summary.key_insights
    
    @patch('tools.doc_summary_tool.subprocess.run')
    def test_subprocess_security_restrictions(self, mock_run):
        """Test that subprocess is called with security restrictions."""
        mock_run.return_value = MagicMock(returncode=0, stdout="# Test content")
        
        summarize_url_document("https://github.com/user/repo/file.md")
        
        # Verify subprocess was called with security restrictions
        assert mock_run.called
        call_args = mock_run.call_args
        
        # Check for security flags
        cmd = call_args[0][0]
        assert "--max-filesize" in cmd
        assert "--connect-timeout" in cmd
        
        # Check for restricted environment
        kwargs = call_args[1]
        assert "env" in kwargs
        assert kwargs["env"]["PATH"] == "/usr/bin:/bin"


class TestIntegration:
    """Integration tests for the complete summarization workflow."""
    
    def test_end_to_end_markdown_workflow(self, temp_markdown_file):
        """Test complete end-to-end markdown summarization workflow."""
        # Custom configuration for comprehensive analysis
        config = SummaryConfig(
            max_section_words=300,
            extract_key_points=True,
            include_code_blocks=True,
            summary_style="comprehensive",
            focus_topics=["introduction", "conclusion"]
        )
        
        summary = summarize_document(temp_markdown_file, config)
        
        # Validate complete workflow results
        assert isinstance(summary, DocumentSummary)
        assert summary.title is not None and len(summary.title) > 0
        assert summary.total_words > 0
        assert summary.total_sections >= 3  # Should have multiple sections
        assert summary.document_type == "markdown"
        assert summary.reading_time_minutes > 0
        
        # Check section analysis
        assert len(summary.sections) >= 3
        for section in summary.sections:
            assert section.word_count > 0
            assert section.level >= 1
            if "Key point" in section.content:
                assert len(section.key_points) > 0
        
        # Check overall analysis
        assert len(summary.summary) > 50  # Comprehensive summary
        assert len(summary.main_topics) > 0
        assert summary.reading_time_minutes == max(1, summary.total_words // 200)
        
        # Validate key insights extraction
        if summary.key_insights:
            assert all(isinstance(insight, str) for insight in summary.key_insights)
            assert all(len(insight) > 10 for insight in summary.key_insights)
    
    def test_multi_format_batch_processing(self, temp_markdown_file, temp_python_file, temp_text_file):
        """Test batch processing of multiple document formats."""
        doc_paths = [temp_markdown_file, temp_python_file, temp_text_file]
        
        # Use custom config for each
        config = SummaryConfig(
            summary_style="technical",
            extract_key_points=True,
            max_section_words=250
        )
        
        summaries = batch_summarize_docs(doc_paths, config)
        
        # Validate all formats processed
        assert len(summaries) == 3
        
        # Check format-specific processing
        md_summary = summaries[temp_markdown_file]
        py_summary = summaries[temp_python_file]
        txt_summary = summaries[temp_text_file]
        
        assert md_summary.document_type == "markdown"
        assert py_summary.document_type == "python_code"
        assert txt_summary.document_type == "text"
        
        # All should have extracted sections
        assert all(s.total_sections > 0 for s in summaries.values())
        
        # Python file should identify code constructs
        py_titles = [section.title for section in py_summary.sections]
        assert any("Class" in title or "function" in title for title in py_titles)
        
        # Markdown should extract heading structure
        assert md_summary.total_sections >= 3
        
        # All should have reading time estimates
        assert all(s.reading_time_minutes > 0 for s in summaries.values())