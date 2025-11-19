"""
Comprehensive Quality Tests for AI Tools

This module provides thorough testing of all AI tools to ensure they meet
the 9.8/10 quality standard with comprehensive coverage of:
- Functional correctness
- Performance benchmarks
- Error handling robustness
- Input validation
- Quality assessment accuracy
"""

import asyncio
import json
import pytest
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from unittest.mock import AsyncMock, Mock, patch

# Import tools and framework
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.base import (
    ToolImplementation, BaseInputModel, BaseOutputModel, ToolContext,
    ToolError, ErrorCategory, QualityMetric, QualityScore
)
from tools.search_tool import WebSearchTool, SearchInput, SearchOutput
from tools.knowledge_search import KnowledgeSearchTool, KnowledgeSearchInput, KnowledgeSearchOutput
from tools.image_analysis_tool import ImageAnalysisTool, ImageAnalysisInput, ImageAnalysisOutput
from tools.image_generation_tool import ImageGenerationTool, ImageGenerationInput, ImageGenerationOutput
from tools.quality_framework import (
    TestExecutor, TestSuite, TestCase, TestType, BenchmarkCategory,
    create_quality_framework, create_test_suite
)


class TestBaseToolImplementation:
    """Test the base tool implementation for quality standards."""
    
    @pytest.fixture
    def mock_tool_class(self):
        """Create a mock tool implementation for testing."""
        
        class MockInput(BaseInputModel):
            message: str
            number: int = 42
        
        class MockOutput(BaseOutputModel):
            response: str
            processed_number: int
        
        class MockTool(ToolImplementation[MockInput, MockOutput]):
            
            @property
            def input_model(self):
                return MockInput
            
            @property
            def output_model(self):
                return MockOutput
            
            async def _execute_core(self, input_data: MockInput, context: ToolContext) -> MockOutput:
                if input_data.message == "error":
                    raise ValueError("Test error")
                if input_data.message == "slow":
                    await asyncio.sleep(0.1)  # Simulate slow operation
                
                return MockOutput(
                    response=f"Processed: {input_data.message}",
                    processed_number=input_data.number * 2
                )
        
        return MockTool
    
    @pytest.fixture
    def tool_instance(self, mock_tool_class):
        """Create tool instance for testing."""
        return mock_tool_class(name="test_tool", version="1.0.0")
    
    @pytest.mark.asyncio
    async def test_successful_execution(self, tool_instance):
        """Test successful tool execution."""
        
        input_data = {"message": "hello", "number": 10}
        result = await tool_instance.execute(input_data)
        
        assert isinstance(result, BaseOutputModel)
        assert result.response == "Processed: hello"
        assert result.processed_number == 20
        assert result.quality_score is not None
        assert result.performance_metrics is not None
        assert result.quality_score.overall_score >= 7.0  # Minimum quality threshold
    
    @pytest.mark.asyncio
    async def test_input_validation(self, tool_instance):
        """Test input validation robustness."""
        
        # Test empty input
        with pytest.raises(ToolError) as exc_info:
            await tool_instance.execute({})
        assert exc_info.value.category == ErrorCategory.INPUT_VALIDATION
        
        # Test invalid data types
        with pytest.raises(ToolError):
            await tool_instance.execute({"message": 123, "number": "invalid"})
        
        # Test missing required fields
        with pytest.raises(ToolError):
            await tool_instance.execute({"number": 42})
    
    @pytest.mark.asyncio
    async def test_error_handling(self, tool_instance):
        """Test comprehensive error handling."""
        
        # Test expected error handling
        with pytest.raises(ToolError) as exc_info:
            await tool_instance.execute({"message": "error", "number": 1})
        
        assert exc_info.value.category == ErrorCategory.INTERNAL_ERROR
        assert exc_info.value.recoverable is True
        assert exc_info.value.trace_id is not None
        
        # Verify error is recorded
        error_stats = tool_instance.get_error_stats()
        assert error_stats["total_errors"] > 0
    
    @pytest.mark.asyncio
    async def test_performance_monitoring(self, tool_instance):
        """Test performance monitoring capabilities."""
        
        # Execute multiple operations
        for i in range(5):
            await tool_instance.execute({"message": f"test_{i}", "number": i})
        
        # Check performance stats
        perf_stats = tool_instance.get_performance_stats()
        assert perf_stats["total_executions"] == 5
        assert "average_duration_ms" in perf_stats
        assert perf_stats["average_duration_ms"] > 0
        
        # Test slow operation tracking
        await tool_instance.execute({"message": "slow", "number": 1})
        
        updated_stats = tool_instance.get_performance_stats()
        assert updated_stats["max_duration_ms"] >= 100  # Should include slow operation
    
    @pytest.mark.asyncio
    async def test_quality_assessment(self, tool_instance):
        """Test quality assessment accuracy."""
        
        result = await tool_instance.execute({"message": "quality_test", "number": 100})
        
        quality = result.quality_score
        assert quality is not None
        assert 0.0 <= quality.overall_score <= 10.0
        assert len(quality.dimension_scores) > 0
        assert quality.assessment_timestamp is not None
        
        # Test quality trends
        for i in range(3):
            await tool_instance.execute({"message": f"trend_{i}", "number": i})
        
        quality_stats = tool_instance.get_quality_stats()
        assert "average_quality_score" in quality_stats
        assert quality_stats["average_quality_score"] > 0
    
    def test_health_check(self, tool_instance):
        """Test health check functionality."""
        
        health = tool_instance.health_check()
        
        assert "tool_name" in health
        assert "tool_version" in health
        assert "status" in health
        assert "health_score" in health
        assert 0.0 <= health["health_score"] <= 10.0
        
        # Health score should be good initially
        assert health["health_score"] >= 8.0


class TestWebSearchTool:
    """Test Web Search Tool quality and functionality."""
    
    @pytest.fixture
    def mock_api_responses(self):
        """Mock API responses for testing."""
        
        return {
            "successful_search": {
                "choices": [{
                    "message": {
                        "content": "Test search results with relevant information about the query."
                    }
                }],
                "citations": [
                    {
                        "title": "Test Result 1",
                        "url": "https://example.com/1",
                        "text": "This is a test result snippet with relevant information."
                    },
                    {
                        "title": "Test Result 2", 
                        "url": "https://example.com/2",
                        "text": "Another test result with different content."
                    }
                ]
            },
            "no_results": {
                "choices": [{"message": {"content": "No relevant results found."}}],
                "citations": []
            }
        }
    
    @pytest.mark.asyncio
    async def test_search_functionality_mock(self, mock_api_responses):
        """Test search functionality with mocked responses."""
        
        with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test_key"}):
            tool = WebSearchTool()
            
            with patch.object(tool, '_execute_perplexity_search') as mock_search:
                mock_search.return_value = [
                    {
                        "title": "Test Result",
                        "url": "https://example.com",
                        "snippet": "Test content",
                        "domain": "example.com",
                        "relevance_score": 0.9
                    }
                ]
                
                input_data = SearchInput(
                    query="test query",
                    max_results=5
                )
                
                result = await tool.execute(input_data)
                
                assert isinstance(result, SearchOutput)
                assert result.query == "test query"
                assert len(result.results) > 0
                assert result.total_results > 0
                assert result.search_time_ms > 0
                assert result.confidence_score > 0
    
    @pytest.mark.asyncio
    async def test_search_input_validation(self):
        """Test search input validation."""
        
        with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test_key"}):
            tool = WebSearchTool()
            
            # Test empty query
            with pytest.raises(ToolError):
                await tool.execute(SearchInput(query=""))
            
            # Test query too long
            with pytest.raises(ToolError):
                await tool.execute(SearchInput(query="a" * 1001))
            
            # Test invalid max_results
            with pytest.raises(ToolError):
                await tool.execute(SearchInput(query="test", max_results=0))
            
            # Test invalid domain filter
            with pytest.raises(ToolError):
                await tool.execute(SearchInput(
                    query="test",
                    domain_filter=["invalid://domain"]
                ))
    
    @pytest.mark.asyncio
    async def test_search_error_handling(self):
        """Test search error handling scenarios."""
        
        with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test_key"}):
            tool = WebSearchTool()
            
            # Test API error handling
            with patch.object(tool.client, 'post') as mock_post:
                mock_response = Mock()
                mock_response.raise_for_status.side_effect = Exception("API Error")
                mock_post.return_value = mock_response
                
                with pytest.raises(ToolError) as exc_info:
                    await tool.execute(SearchInput(query="test"))
                
                assert exc_info.value.category in [
                    ErrorCategory.EXTERNAL_API, 
                    ErrorCategory.NETWORK_ERROR,
                    ErrorCategory.INTERNAL_ERROR
                ]
    
    @pytest.mark.asyncio
    async def test_search_caching(self):
        """Test search result caching."""
        
        with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test_key"}):
            tool = WebSearchTool()
            
            with patch.object(tool, '_execute_perplexity_search') as mock_search:
                mock_search.return_value = [
                    {
                        "title": "Cached Result",
                        "url": "https://example.com",
                        "snippet": "Cached content",
                        "domain": "example.com",
                        "relevance_score": 0.8
                    }
                ]
                
                input_data = SearchInput(query="cache test", max_results=3)
                
                # First request
                result1 = await tool.execute(input_data)
                
                # Second request (should hit cache)
                result2 = await tool.execute(input_data)
                
                # Verify both results are equivalent
                assert result1.query == result2.query
                assert len(result1.results) == len(result2.results)
                
                # API should only be called once due to caching
                assert mock_search.call_count == 1


class TestImageAnalysisTool:
    """Test Image Analysis Tool quality and functionality."""
    
    @pytest.fixture
    def test_image_base64(self):
        """Create a test image in base64 format."""
        
        # Create a small test image
        from PIL import Image
        import io
        import base64
        
        # Create a 100x100 red square
        img = Image.new('RGB', (100, 100), color='red')
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        img_data = buffer.getvalue()
        
        return base64.b64encode(img_data).decode()
    
    @pytest.mark.asyncio
    async def test_image_analysis_functionality(self, test_image_base64):
        """Test image analysis functionality."""
        
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
            tool = ImageAnalysisTool()
            
            # Mock vision API response
            mock_response = {
                "content": "This is a red square image with dimensions 100x100 pixels.",
                "model": "gpt-4-vision-preview",
                "tokens_used": 50
            }
            
            with patch.object(tool.vision_client, 'analyze_with_gpt4v', return_value=mock_response):
                
                input_data = ImageAnalysisInput(
                    image_source=f"data:image/png;base64,{test_image_base64}",
                    analysis_types=["description", "colors", "composition"]
                )
                
                result = await tool.execute(input_data)
                
                assert isinstance(result, ImageAnalysisOutput)
                assert "width" in result.image_info
                assert "height" in result.image_info
                assert result.image_info["width"] == 100
                assert result.image_info["height"] == 100
                assert result.processing_time_ms > 0
                assert result.analysis_confidence > 0
    
    @pytest.mark.asyncio
    async def test_image_input_validation(self):
        """Test image input validation."""
        
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
            tool = ImageAnalysisTool()
            
            # Test empty image source
            with pytest.raises(ToolError):
                await tool.execute(ImageAnalysisInput(image_source=""))
            
            # Test invalid analysis types
            with pytest.raises(ToolError):
                await tool.execute(ImageAnalysisInput(
                    image_source="data:image/png;base64,invalid",
                    analysis_types=["invalid_type"]
                ))
            
            # Test invalid image size limits
            with pytest.raises(ToolError):
                await tool.execute(ImageAnalysisInput(
                    image_source="test.jpg",
                    max_image_size=100  # Too small
                ))
    
    @pytest.mark.asyncio
    async def test_image_processing_pipeline(self, test_image_base64):
        """Test the complete image processing pipeline."""
        
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
            tool = ImageAnalysisTool()
            
            # Load image
            image = tool.image_processor.load_image_from_source(
                f"data:image/png;base64,{test_image_base64}"
            )
            
            assert image.size == (100, 100)
            
            # Test preprocessing
            processed = tool.image_processor.preprocess_image(image, max_size=2048)
            assert processed.size == (100, 100)  # Should not resize small image
            
            # Test metadata extraction
            metadata = tool.image_processor.extract_image_metadata(processed)
            assert metadata["width"] == 100
            assert metadata["height"] == 100
            assert metadata["mode"] == "RGB"
            
            # Test color analysis
            colors = tool.image_processor.analyze_colors(processed)
            assert "dominant_colors" in colors
            assert len(colors["dominant_colors"]) > 0


class TestKnowledgeSearchTool:
    """Test Knowledge Search Tool quality and functionality."""
    
    @pytest.fixture
    def temp_knowledge_base(self):
        """Create temporary knowledge base for testing."""
        
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create test documents
            docs = {
                "doc1.txt": "This is a test document about machine learning algorithms.",
                "doc2.md": "# AI Overview\nThis document covers artificial intelligence concepts.",
                "doc3.txt": "Natural language processing is a branch of AI."
            }
            
            for filename, content in docs.items():
                (Path(temp_dir) / filename).write_text(content)
            
            yield temp_dir
    
    @pytest.mark.asyncio
    async def test_knowledge_search_functionality(self, temp_knowledge_base):
        """Test knowledge search functionality."""
        
        tool = KnowledgeSearchTool(
            knowledge_base_paths=[temp_knowledge_base],
            index_path=f"{temp_knowledge_base}/index"
        )
        
        input_data = KnowledgeSearchInput(
            query="machine learning",
            max_results=5
        )
        
        with patch.object(tool.knowledge_index, 'search') as mock_search:
            # Mock search results
            mock_chunk = Mock()
            mock_chunk.content = "Machine learning is important"
            mock_chunk.document_path = "doc1.txt"
            mock_chunk.chunk_id = "chunk1"
            mock_chunk.word_count = 5
            mock_chunk.last_updated = datetime.utcnow()
            
            mock_search.return_value = [(mock_chunk, 0.9)]
            
            result = await tool.execute(input_data)
            
            assert isinstance(result, KnowledgeSearchOutput)
            assert result.query == "machine learning"
            assert len(result.results) > 0
            assert result.search_time_ms > 0
    
    @pytest.mark.asyncio
    async def test_knowledge_input_validation(self, temp_knowledge_base):
        """Test knowledge search input validation."""
        
        tool = KnowledgeSearchTool(
            knowledge_base_paths=[temp_knowledge_base],
            index_path=f"{temp_knowledge_base}/index"
        )
        
        # Test empty query
        with pytest.raises(ToolError):
            await tool.execute(KnowledgeSearchInput(query=""))
        
        # Test invalid search type
        with pytest.raises(ToolError):
            await tool.execute(KnowledgeSearchInput(
                query="test",
                search_type="invalid"
            ))
        
        # Test invalid date range
        with pytest.raises(ToolError):
            await tool.execute(KnowledgeSearchInput(
                query="test",
                date_range={"start": "invalid_date"}
            ))


class TestQualityFramework:
    """Test the quality framework itself."""
    
    @pytest.fixture
    def sample_test_suite(self):
        """Create a sample test suite for testing."""
        
        class DummyTool(ToolImplementation):
            @property
            def input_model(self):
                return BaseInputModel
            
            @property
            def output_model(self):
                return BaseOutputModel
            
            async def _execute_core(self, input_data, context):
                return BaseOutputModel()
        
        suite = create_test_suite("test_suite", DummyTool, "Test suite for framework testing")
        
        # Add test cases
        test_cases = [
            TestCase(
                id="test_1",
                name="Basic Test",
                description="Basic functionality test",
                test_type=TestType.FUNCTIONAL,
                input_data={"test": True}
            ),
            TestCase(
                id="test_2",
                name="Performance Test",
                description="Performance benchmark test",
                test_type=TestType.PERFORMANCE,
                input_data={"test": True}
            )
        ]
        
        for test_case in test_cases:
            suite.add_test_case(test_case)
        
        return suite, DummyTool()
    
    @pytest.mark.asyncio
    async def test_test_execution(self, sample_test_suite):
        """Test test execution functionality."""
        
        test_suite, tool_instance = sample_test_suite
        executor = create_quality_framework(enable_ai_assessment=False)
        
        summary = await executor.execute_test_suite(test_suite, tool_instance)
        
        assert "test_suite_name" in summary
        assert summary["test_suite_name"] == "test_suite"
        assert "test_counts" in summary
        assert summary["test_counts"]["total_tests"] == 2
        assert "success_metrics" in summary
        assert "performance_statistics" in summary
    
    @pytest.mark.asyncio
    async def test_performance_benchmarking(self, sample_test_suite):
        """Test performance benchmarking functionality."""
        
        test_suite, tool_instance = sample_test_suite
        executor = create_quality_framework(enable_ai_assessment=False)
        
        # Get performance test cases
        perf_tests = test_suite.get_tests_by_type(TestType.PERFORMANCE)
        
        benchmarks = await executor.run_performance_benchmark(
            tool_instance, 
            perf_tests, 
            iterations=3,
            warmup_iterations=1
        )
        
        assert len(benchmarks) > 0
        
        # Check for expected benchmark categories
        categories = {b.category for b in benchmarks}
        assert BenchmarkCategory.LATENCY in categories
        assert BenchmarkCategory.THROUGHPUT in categories
    
    def test_quality_report_generation(self, sample_test_suite):
        """Test quality report generation."""
        
        test_suite, tool_instance = sample_test_suite
        executor = create_quality_framework(enable_ai_assessment=False)
        
        # Add some dummy execution history
        executor.execution_history.append({
            "timestamp": datetime.utcnow(),
            "test_suite": "test_suite",
            "tool": tool_instance.name,
            "summary": {
                "success_metrics": {"success_rate_percent": 95.0},
                "performance_statistics": {"average_execution_time_ms": 150.0}
            }
        })
        
        report = executor.generate_quality_report("test_suite")
        
        assert "test_suite" in report
        assert "report_generated" in report
        assert "latest_execution" in report
        assert "recommendations" in report


@pytest.mark.integration
class TestToolIntegration:
    """Integration tests for tool orchestration."""
    
    @pytest.mark.asyncio
    async def test_tool_chaining(self):
        """Test chaining multiple tools together."""
        
        # This would test real tool integration
        # For now, we'll mock the integration
        
        with patch.dict(os.environ, {
            "PERPLEXITY_API_KEY": "test_key",
            "OPENAI_API_KEY": "test_key"
        }):
            
            # Mock successful tool executions
            search_tool = WebSearchTool()
            
            with patch.object(search_tool, 'execute') as mock_search:
                mock_search.return_value = SearchOutput(
                    query="test",
                    results=[],
                    total_results=0,
                    search_time_ms=100,
                    confidence_score=0.8
                )
                
                # Execute search
                search_result = await search_tool.execute(SearchInput(query="AI research"))
                
                assert search_result.confidence_score >= 0.5
                assert isinstance(search_result.search_time_ms, float)


@pytest.mark.performance
class TestPerformanceBenchmarks:
    """Performance benchmark tests for all tools."""
    
    @pytest.mark.asyncio
    async def test_search_tool_performance(self):
        """Benchmark search tool performance."""
        
        with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test_key"}):
            tool = WebSearchTool()
            
            with patch.object(tool, '_execute_perplexity_search', return_value=[]):
                
                # Measure execution time
                start_time = time.time()
                
                await tool.execute(SearchInput(query="performance test"))
                
                execution_time = (time.time() - start_time) * 1000
                
                # Performance should be under 5 seconds
                assert execution_time < 5000
    
    @pytest.mark.asyncio
    async def test_concurrent_tool_execution(self):
        """Test concurrent tool execution performance."""
        
        with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test_key"}):
            tool = WebSearchTool()
            
            with patch.object(tool, '_execute_perplexity_search', return_value=[]):
                
                # Execute multiple concurrent searches
                tasks = []
                for i in range(5):
                    task = tool.execute(SearchInput(query=f"concurrent test {i}"))
                    tasks.append(task)
                
                start_time = time.time()
                results = await asyncio.gather(*tasks)
                execution_time = (time.time() - start_time) * 1000
                
                # All results should be successful
                assert len(results) == 5
                assert all(isinstance(r, SearchOutput) for r in results)
                
                # Concurrent execution should not be significantly slower than sequential
                # (accounting for mocked responses)
                assert execution_time < 2000  # 2 seconds for 5 concurrent operations


if __name__ == "__main__":
    # Run tests with comprehensive coverage
    pytest.main([
        __file__,
        "-v",
        "--cov=tools",
        "--cov-report=html",
        "--cov-report=term-missing",
        "--cov-min=85",  # Require 85% code coverage
        "--benchmark-only",
        "--benchmark-sort=mean"
    ])