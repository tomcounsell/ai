"""Tests for development_tools.py MCP server."""

import pytest
import json
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

# Import the MCP server functions directly
from mcp_servers.development_tools import (
    generate_test_parameters,
    generate_ui_testing_params,
    generate_code_testing_params,
    judge_ai_response,
    judge_code_quality_response,
    batch_judge_responses,
    lint_python_code,
    lint_specific_files,
    quick_code_check,
    comprehensive_project_lint,
    summarize_code_documentation,
    summarize_url_content,
    batch_summarize_documents,
    quick_document_overview,
    analyze_image_content,
    get_simple_image_tags,
    batch_analyze_images,
    analyze_image_for_moderation,
    detailed_image_assessment
)


class TestMCPTestParameters:
    """Test MCP interfaces for test parameter generation."""
    
    def test_generate_test_parameters_success(self):
        """Test successful test parameter generation via MCP."""
        result = generate_test_parameters(
            test_type="ui_feedback",
            param_categories=["ui_feedback"],
            num_variations=2,
            complexity_level="simple"
        )
        
        # Should return valid JSON
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) == 2
        
        # Check structure of first parameter set
        param_set = data[0]
        assert "test_id" in param_set
        assert "parameters" in param_set
        assert "expected_behavior" in param_set
        assert "evaluation_criteria" in param_set
        assert param_set["test_id"].startswith("ui_feedback_")
    
    def test_generate_test_parameters_error_handling(self):
        """Test error handling in test parameter generation."""
        result = generate_test_parameters(
            test_type="invalid_type",
            param_categories=[],  # Empty categories
            num_variations=-1  # Invalid number
        )
        
        # Should handle error gracefully
        assert "❌ Error generating test parameters" in result
    
    def test_generate_ui_testing_params(self):
        """Test UI-specific test parameter generation."""
        result = generate_ui_testing_params(num_variations=3, complexity="medium")
        
        data = json.loads(result)
        assert len(data) == 3
        assert all(item["test_id"].startswith("ui_feedback_") for item in data)
    
    def test_generate_code_testing_params(self):
        """Test code quality test parameter generation."""
        result = generate_code_testing_params(num_variations=2, complexity="complex")
        
        data = json.loads(result)
        assert len(data) == 2
        assert all(item["test_id"].startswith("code_quality_") for item in data)


class TestMCPTestJudging:
    """Test MCP interfaces for test judging."""
    
    @patch('mcp_servers.development_tools.judge_test_result')
    def test_judge_ai_response_success(self, mock_judge):
        """Test successful AI response judging via MCP."""
        from tools.test_judge_tool import TestJudgment, JudgmentScore
        
        mock_judgment = TestJudgment(
            test_id="test_001",
            overall_score=JudgmentScore.GOOD,
            criteria_scores={"accuracy": JudgmentScore.EXCELLENT},
            detailed_feedback="Good response",
            pass_fail=True,
            confidence=0.8,
            reasoning="Meets criteria"
        )
        mock_judge.return_value = mock_judgment
        
        result = judge_ai_response(
            response_text="This is a test response",
            evaluation_criteria=["accuracy", "clarity"],
            test_context={"test_id": "test_001", "test_type": "response"}
        )
        
        data = json.loads(result)
        assert data["test_id"] == "test_001"
        assert data["overall_score"] == "good"
        assert data["pass_fail"] == True
        assert mock_judge.called
    
    @patch('mcp_servers.development_tools.judge_code_quality')
    def test_judge_code_quality_response(self, mock_judge):
        """Test code quality judging via MCP."""
        from tools.test_judge_tool import TestJudgment, JudgmentScore
        
        mock_judgment = TestJudgment(
            test_id="code_001",
            overall_score=JudgmentScore.SATISFACTORY,
            criteria_scores={},
            detailed_feedback="Code quality is adequate",
            pass_fail=True,
            confidence=0.7,
            reasoning="Meets basic standards"
        )
        mock_judge.return_value = mock_judgment
        
        result = judge_code_quality_response(
            code="def hello(): print('Hello')",
            language="python",
            quality_criteria=["readability", "correctness"]
        )
        
        data = json.loads(result)
        assert data["overall_score"] == "satisfactory"
        assert mock_judge.called
    
    @patch('mcp_servers.development_tools.batch_judge_tests')
    def test_batch_judge_responses(self, mock_batch_judge):
        """Test batch response judging via MCP."""
        from tools.test_judge_tool import TestJudgment, JudgmentScore
        
        mock_judgments = [
            TestJudgment(
                test_id="test_1",
                overall_score=JudgmentScore.GOOD,
                criteria_scores={},
                detailed_feedback="Good",
                pass_fail=True,
                confidence=0.8,
                reasoning="Passes"
            )
        ]
        mock_batch_judge.return_value = mock_judgments
        
        test_cases = [
            {
                "output": "Test response",
                "criteria": ["accuracy"],
                "context": {"test_id": "test_1"}
            }
        ]
        
        result = batch_judge_responses(test_cases)
        
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["test_id"] == "test_1"


class TestMCPLinting:
    """Test MCP interfaces for code linting."""
    
    @patch('mcp_servers.development_tools.run_linting')
    def test_lint_python_code_success(self, mock_lint):
        """Test successful Python code linting via MCP."""
        from tools.linting_tool import LintResult, LintSeverity
        
        mock_result = LintResult(
            success=True,
            total_issues=0,
            issues_by_severity={severity: 0 for severity in LintSeverity},
            issues=[],
            execution_time=1.5,
            tools_run=["ruff", "black"],
            summary="All checks passed"
        )
        mock_lint.return_value = mock_result
        
        result = lint_python_code("/test/project")
        
        data = json.loads(result)
        assert data["success"] == True
        assert data["total_issues"] == 0
        assert "ruff" in data["tools_run"]
        assert mock_lint.called
    
    @patch('mcp_servers.development_tools.lint_files')
    def test_lint_specific_files(self, mock_lint_files):
        """Test specific file linting via MCP."""
        from tools.linting_tool import LintResult, LintSeverity, LintIssue
        
        mock_result = LintResult(
            success=False,
            total_issues=1,
            issues_by_severity={LintSeverity.ERROR: 1, LintSeverity.WARNING: 0},
            issues=[
                LintIssue(
                    file_path="test.py",
                    line=1,
                    column=1,
                    severity=LintSeverity.ERROR,
                    code="E001",
                    message="Syntax error"
                )
            ],
            execution_time=0.5,
            tools_run=["ruff"],
            summary="1 error found"
        )
        mock_lint_files.return_value = mock_result
        
        result = lint_specific_files(["test.py"])
        
        data = json.loads(result)
        assert data["success"] == False
        assert data["total_issues"] == 1
        assert len(data["issues"]) == 1
    
    @patch('mcp_servers.development_tools.quick_lint_check')
    def test_quick_code_check(self, mock_quick_check):
        """Test quick code check via MCP."""
        mock_quick_check.return_value = True
        
        result = quick_code_check("test.py")
        
        assert "✅ Code quality check: PASSED" in result
        assert mock_quick_check.called


class TestMCPDocumentSummarization:
    """Test MCP interfaces for document summarization."""
    
    @pytest.fixture
    def temp_markdown_file(self):
        """Create temporary markdown file for testing."""
        content = "# Test Doc\n\nThis is test content.\n\n## Section\n\nMore content."
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write(content)
            temp_path = f.name
        
        yield temp_path
        
        try:
            os.unlink(temp_path)
        except:
            pass
    
    @patch('mcp_servers.development_tools.summarize_document')
    def test_summarize_code_documentation(self, mock_summarize, temp_markdown_file):
        """Test document summarization via MCP."""
        from tools.doc_summary_tool import DocumentSummary, DocumentSection
        
        mock_summary = DocumentSummary(
            title="Test Document",
            total_words=50,
            total_sections=2,
            summary="Test document with sections",
            key_insights=["Key insight"],
            sections=[
                DocumentSection(
                    title="Section 1",
                    content="Content",
                    level=1,
                    word_count=10
                )
            ],
            reading_time_minutes=1,
            document_type="markdown",
            main_topics=["test"]
        )
        mock_summarize.return_value = mock_summary
        
        result = summarize_code_documentation(temp_markdown_file)
        
        data = json.loads(result)
        assert data["title"] == "Test Document"
        assert data["document_type"] == "markdown"
        assert data["total_sections"] == 2
        assert mock_summarize.called
    
    @patch('mcp_servers.development_tools.summarize_url_document')
    def test_summarize_url_content(self, mock_summarize_url):
        """Test URL content summarization via MCP."""
        from tools.doc_summary_tool import DocumentSummary
        
        mock_summary = DocumentSummary(
            title="URL Document",
            total_words=100,
            total_sections=1,
            summary="URL content summary",
            key_insights=[],
            sections=[],
            reading_time_minutes=1,
            document_type="markdown",
            main_topics=[]
        )
        mock_summarize_url.return_value = mock_summary
        
        result = summarize_url_content("https://example.com/doc.md")
        
        data = json.loads(result)
        assert data["title"] == "URL Document"
        assert mock_summarize_url.called
    
    @patch('mcp_servers.development_tools.quick_doc_summary')
    def test_quick_document_overview(self, mock_quick_summary):
        """Test quick document overview via MCP."""
        mock_quick_summary.return_value = "Test Document: Brief overview of content"
        
        result = quick_document_overview("test.md")
        
        assert "Brief overview of content" in result
        assert mock_quick_summary.called


class TestMCPImageAnalysis:
    """Test MCP interfaces for image analysis."""
    
    @pytest.fixture
    def temp_image_file(self):
        """Create temporary image file for testing."""
        # Simple 1x1 PNG data
        import base64
        png_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChAGAD7TL5gAAAABJRU5ErkJggg=="
        )
        
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            f.write(png_data)
            temp_path = f.name
        
        yield temp_path
        
        try:
            os.unlink(temp_path)
        except:
            pass
    
    @patch('mcp_servers.development_tools.tag_image')
    def test_analyze_image_content(self, mock_tag_image, temp_image_file):
        """Test image content analysis via MCP."""
        from tools.image_tagging_tool import ImageAnalysis, ImageTag
        
        mock_analysis = ImageAnalysis(
            file_path=temp_image_file,
            tags=[
                ImageTag(tag="test", confidence=0.9, category="object"),
                ImageTag(tag="image", confidence=0.8, category="general")
            ],
            description="Test image analysis",
            primary_objects=["test"],
            scene_type="digital",
            dominant_colors=["gray"],
            style_tags=["test"],
            mood_sentiment="neutral",
            technical_quality={"quality": "good"},
            ai_confidence=0.8
        )
        mock_tag_image.return_value = mock_analysis
        
        result = analyze_image_content(temp_image_file)
        
        data = json.loads(result)
        assert data["file_path"] == temp_image_file
        assert len(data["tags"]) == 2
        assert data["tags"][0]["tag"] == "test"
        assert data["ai_confidence"] == 0.8
    
    @patch('mcp_servers.development_tools.extract_simple_tags')
    def test_get_simple_image_tags(self, mock_extract_tags, temp_image_file):
        """Test simple image tag extraction via MCP."""
        mock_extract_tags.return_value = ["test", "image", "digital"]
        
        result = get_simple_image_tags(temp_image_file, max_tags=5)
        
        data = json.loads(result)
        assert isinstance(data, list)
        assert "test" in data
        assert "image" in data
        assert mock_extract_tags.called
    
    @patch('mcp_servers.development_tools.batch_tag_images')
    def test_batch_analyze_images(self, mock_batch_tag, temp_image_file):
        """Test batch image analysis via MCP."""
        from tools.image_tagging_tool import ImageAnalysis, ImageTag
        
        mock_results = {
            temp_image_file: ImageAnalysis(
                file_path=temp_image_file,
                tags=[ImageTag(tag="batch", confidence=0.7, category="test")],
                description="Batch analysis",
                primary_objects=["batch"],
                scene_type="test",
                dominant_colors=[],
                style_tags=[],
                mood_sentiment="neutral",
                technical_quality={},
                ai_confidence=0.7
            )
        }
        mock_batch_tag.return_value = mock_results
        
        result = batch_analyze_images([temp_image_file])
        
        data = json.loads(result)
        assert temp_image_file in data
        assert data[temp_image_file]["tags"][0]["tag"] == "batch"
    
    @patch('mcp_servers.development_tools.content_moderation_tags')
    def test_analyze_image_for_moderation(self, mock_moderation_tags, temp_image_file):
        """Test image moderation analysis via MCP."""
        mock_moderation_tags.return_value = ["safe", "family_friendly"]
        
        result = analyze_image_for_moderation(temp_image_file)
        
        data = json.loads(result)
        assert isinstance(data, list)
        assert "safe" in data
        assert "family_friendly" in data


class TestMCPErrorHandling:
    """Test error handling across MCP interfaces."""
    
    def test_generate_test_parameters_exception(self):
        """Test exception handling in test parameter generation."""
        # Force an error by passing invalid types
        result = generate_test_parameters(
            test_type=None,  # Invalid type
            param_categories=None,  # Invalid type
            num_variations="invalid"  # Invalid type
        )
        
        assert "❌ Error generating test parameters" in result
    
    def test_lint_python_code_exception(self):
        """Test exception handling in linting."""
        # Test with non-existent path
        result = lint_python_code("/nonexistent/path")
        
        assert "❌ Error running linting" in result
    
    def test_analyze_image_content_exception(self):
        """Test exception handling in image analysis."""
        # Test with non-existent image
        result = analyze_image_content("/nonexistent/image.jpg")
        
        assert "❌ Error analyzing image" in result


class TestMCPIntegration:
    """Integration tests for MCP server functionality."""
    
    def test_mcp_server_import(self):
        """Test that MCP server can be imported without errors."""
        import mcp_servers.development_tools
        
        # Should have the mcp object
        assert hasattr(mcp_servers.development_tools, 'mcp')
    
    def test_all_tools_registered(self):
        """Test that all expected tools are registered with MCP."""
        from mcp_servers.development_tools import mcp
        
        # Get list of registered tools
        tool_names = []
        for tool in mcp.tools:
            tool_names.append(tool.__name__)
        
        # Check that key tools are registered
        expected_tools = [
            'generate_test_parameters',
            'judge_ai_response', 
            'lint_python_code',
            'summarize_code_documentation',
            'analyze_image_content'
        ]
        
        for expected_tool in expected_tools:
            assert expected_tool in tool_names, f"Tool {expected_tool} not found in registered tools"
    
    @patch.dict(os.environ, {}, clear=True)
    def test_mcp_server_environment_handling(self):
        """Test MCP server handles missing environment variables gracefully."""
        # Import should still work even without environment variables
        import mcp_servers.development_tools
        
        # Server should still be created
        assert mcp_servers.development_tools.mcp is not None