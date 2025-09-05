"""
Test Suite for Tool Implementations
Tests all tool imports, initialization, and basic functionality.
"""

import pytest
import asyncio
from unittest.mock import Mock, patch
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.base import ToolContext, ErrorCategory, ToolError
from tools.search_tool import WebSearchTool, SearchInput
from tools.image_generation_tool import ImageGenerationTool, ImageGenerationInput
from tools.image_analysis_tool import ImageAnalysisTool, ImageAnalysisInput
from tools.knowledge_search import KnowledgeSearchTool, KnowledgeSearchInput
from tools.code_execution_tool import CodeExecutionTool, CodeExecutionInput
from tools.test_judge_tool import TestJudgeTool, TestJudgeInput


class TestToolImports:
    """Test that all tools can be imported successfully."""
    
    def test_base_tool_import(self):
        """Test base tool framework imports."""
        from tools.base import ToolImplementation, BaseInputModel, BaseOutputModel
        assert ToolImplementation is not None
        assert BaseInputModel is not None
        assert BaseOutputModel is not None
    
    def test_search_tool_import(self):
        """Test search tool imports."""
        assert WebSearchTool is not None
        assert SearchInput is not None
    
    def test_image_generation_tool_import(self):
        """Test image generation tool imports."""
        assert ImageGenerationTool is not None
        assert ImageGenerationInput is not None
    
    def test_image_analysis_tool_import(self):
        """Test image analysis tool imports."""
        assert ImageAnalysisTool is not None
        assert ImageAnalysisInput is not None
    
    def test_knowledge_search_tool_import(self):
        """Test knowledge search tool imports."""
        assert KnowledgeSearchTool is not None
        assert KnowledgeSearchInput is not None
    
    def test_code_execution_tool_import(self):
        """Test code execution tool imports."""
        assert CodeExecutionTool is not None
        assert CodeExecutionInput is not None
    
    def test_test_judge_tool_import(self):
        """Test test judge tool imports."""
        assert TestJudgeTool is not None
        assert TestJudgeInput is not None
    
    def test_quality_framework_import(self):
        """Test quality framework imports."""
        from tools.quality_framework import QualityAssessmentEngine, TestExecutor
        assert QualityAssessmentEngine is not None
        assert TestExecutor is not None


class TestToolInitialization:
    """Test tool initialization without API keys."""
    
    def test_search_tool_requires_api_key(self):
        """Test that search tool requires API key."""
        with pytest.raises(ValueError, match="API key"):
            WebSearchTool()
    
    def test_image_generation_tool_requires_api_key(self):
        """Test that image generation tool requires API key."""
        with pytest.raises(ValueError, match="API key"):
            ImageGenerationTool()
    
    def test_image_analysis_tool_requires_api_key(self):
        """Test that image analysis tool requires API key."""
        with pytest.raises(ValueError, match="API key"):
            ImageAnalysisTool()
    
    def test_code_execution_tool_initialization(self):
        """Test code execution tool can be initialized."""
        tool = CodeExecutionTool()
        assert tool.name == "code_execution"
        assert tool.version is not None
    
    def test_knowledge_search_tool_initialization(self):
        """Test knowledge search tool can be initialized."""
        tool = KnowledgeSearchTool()
        assert tool.name == "knowledge_search"
        assert tool.version is not None


class TestToolInputValidation:
    """Test tool input validation."""
    
    def test_search_input_validation(self):
        """Test search input validation."""
        # Valid input
        input_data = SearchInput(query="test query")
        assert input_data.query == "test query"
        assert input_data.max_results == 10
        
        # Invalid input - empty query
        with pytest.raises(ValueError):
            SearchInput(query="")
        
        # Invalid input - too many results
        with pytest.raises(ValueError):
            SearchInput(query="test", max_results=100)
    
    def test_code_execution_input_validation(self):
        """Test code execution input validation."""
        # Valid Python code
        input_data = CodeExecutionInput(
            code="print('hello')",
            language="python"
        )
        assert input_data.code == "print('hello')"
        assert input_data.language == "python"
        
        # Invalid language
        with pytest.raises(ValueError):
            CodeExecutionInput(
                code="print('hello')",
                language="invalid_lang"
            )
    
    def test_image_generation_input_validation(self):
        """Test image generation input validation."""
        # Valid input
        input_data = ImageGenerationInput(
            prompt="A beautiful sunset",
            model="dall-e-3"
        )
        assert input_data.prompt == "A beautiful sunset"
        assert input_data.model == "dall-e-3"
        
        # Invalid model
        with pytest.raises(ValueError):
            ImageGenerationInput(
                prompt="test",
                model="invalid-model"
            )
        
        # Invalid size
        with pytest.raises(ValueError):
            ImageGenerationInput(
                prompt="test",
                size="999x999"
            )


@pytest.mark.asyncio
class TestToolExecution:
    """Test tool execution with mocked APIs."""
    
    async def test_code_execution_tool_basic(self):
        """Test basic code execution."""
        tool = CodeExecutionTool()
        context = ToolContext()
        
        # Test simple Python code
        input_data = CodeExecutionInput(
            code="result = 2 + 2\nprint(result)",
            language="python"
        )
        
        result = await tool.execute(input_data, context)
        assert result is not None
        assert "4" in result.output or "4" in str(result.result)
    
    async def test_knowledge_search_basic(self):
        """Test knowledge search functionality."""
        tool = KnowledgeSearchTool()
        context = ToolContext()
        
        input_data = KnowledgeSearchInput(
            query="test search",
            max_results=5
        )
        
        result = await tool.execute(input_data, context)
        assert result is not None
        assert hasattr(result, 'results')


class TestToolErrorHandling:
    """Test tool error handling."""
    
    def test_tool_error_categories(self):
        """Test error category handling."""
        error = ToolError(
            "Test error",
            ErrorCategory.INPUT_VALIDATION,
            details={"test": "data"}
        )
        assert error.category == ErrorCategory.INPUT_VALIDATION
        assert error.recoverable is True
        assert error.details["test"] == "data"
    
    def test_tool_error_serialization(self):
        """Test error serialization."""
        error = ToolError(
            "Test error",
            ErrorCategory.NETWORK_ERROR,
            retry_after=5.0
        )
        error_dict = error.to_dict()
        assert error_dict["message"] == "Test error"
        assert error_dict["category"] == "NETWORK_ERROR"
        assert error_dict["retry_after"] == 5.0


class TestToolQualityMetrics:
    """Test tool quality assessment features."""
    
    def test_quality_score_creation(self):
        """Test quality score creation."""
        from tools.base import QualityScore, QualityMetric
        
        score = QualityScore(overall_score=8.5)
        assert score.overall_score == 8.5
        
        # Add dimension scores
        score.add_dimension(QualityMetric.PERFORMANCE, 9.0)
        score.add_dimension(QualityMetric.RELIABILITY, 8.0)
        
        assert QualityMetric.PERFORMANCE in score.dimension_scores
        assert score.dimension_scores[QualityMetric.PERFORMANCE] == 9.0
    
    def test_performance_metrics(self):
        """Test performance metrics tracking."""
        from tools.base import PerformanceMetrics
        import time
        
        metrics = PerformanceMetrics(start_time=time.time())
        time.sleep(0.01)  # Small delay
        metrics.finalize()
        
        assert metrics.duration_ms is not None
        assert metrics.duration_ms > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])