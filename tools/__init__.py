"""
AI Tools Package - Phase 3 Tool Orchestration System

This package provides a comprehensive suite of AI-powered tools with enterprise-grade
quality standards, implementing a 9.8/10 gold standard pattern for reliability,
performance, and maintainability.

Available Tools:
- WebSearchTool: Advanced web search with Perplexity API integration
- KnowledgeSearchTool: Local knowledge base search with semantic understanding
- ImageAnalysisTool: Multi-modal vision analysis with AI models
- ImageGenerationTool: DALL-E integration for image generation
- CodeExecutionTool: Secure sandboxed code execution environment

Quality Framework:
- Comprehensive testing and benchmarking
- Performance monitoring and optimization
- Security scanning and validation
- AI-powered quality assessment
"""

# Import base classes
from .base import (
    ToolImplementation,
    BaseInputModel,
    BaseOutputModel,
    ToolContext,
    ToolError,
    ErrorCategory,
    QualityMetric,
    QualityScore,
    PerformanceMetrics,
    performance_monitor,
    error_context
)

# Import concrete tools
from .search_tool import (
    WebSearchTool,
    SearchInput,
    SearchOutput,
    SearchResult,
    create_web_search_tool
)

from .knowledge_search import (
    KnowledgeSearchTool,
    KnowledgeSearchInput,
    KnowledgeSearchOutput,
    KnowledgeResult,
    create_knowledge_search_tool
)

from .image_analysis_tool import (
    ImageAnalysisTool,
    ImageAnalysisInput,
    ImageAnalysisOutput,
    DetectedObject,
    ExtractedText,
    SceneAnalysis,
    SafetyAnalysis,
    create_image_analysis_tool
)

from .image_generation_tool import (
    ImageGenerationTool,
    ImageGenerationInput,
    ImageGenerationOutput,
    GeneratedImage,
    create_image_generation_tool
)

from .code_execution_tool import (
    CodeExecutionTool,
    CodeExecutionInput,
    CodeExecutionOutput,
    ExecutionResult,
    create_code_execution_tool
)

# Import quality framework
from .quality_framework import (
    TestType,
    TestStatus,
    BenchmarkCategory,
    TestCase,
    TestResult,
    BenchmarkResult,
    TestSuite,
    TestExecutor,
    QualityAssessmentEngine,
    create_quality_framework,
    create_test_suite
)

# Tool registry for dynamic discovery
AVAILABLE_TOOLS = {
    'web_search': {
        'class': WebSearchTool,
        'factory': create_web_search_tool,
        'description': 'Advanced web search with Perplexity API integration',
        'category': 'search',
        'requires_api_key': True,
        'api_key_env': 'PERPLEXITY_API_KEY'
    },
    'knowledge_search': {
        'class': KnowledgeSearchTool,
        'factory': create_knowledge_search_tool,
        'description': 'Local knowledge base search with semantic understanding',
        'category': 'search',
        'requires_api_key': False
    },
    'image_analysis': {
        'class': ImageAnalysisTool,
        'factory': create_image_analysis_tool,
        'description': 'Multi-modal vision analysis with AI models',
        'category': 'vision',
        'requires_api_key': True,
        'api_key_env': 'OPENAI_API_KEY'
    },
    'image_generation': {
        'class': ImageGenerationTool,
        'factory': create_image_generation_tool,
        'description': 'DALL-E integration for image generation',
        'category': 'vision',
        'requires_api_key': True,
        'api_key_env': 'OPENAI_API_KEY'
    },
    'code_execution': {
        'class': CodeExecutionTool,
        'factory': create_code_execution_tool,
        'description': 'Secure sandboxed code execution environment',
        'category': 'development',
        'requires_api_key': False
    }
}

# Quality standards
QUALITY_STANDARDS = {
    'minimum_score': 8.0,
    'target_score': 9.8,
    'required_coverage': 0.85,
    'max_response_time_ms': 30000,
    'max_error_rate': 0.05
}


def get_tool_by_name(tool_name: str, **kwargs):
    """
    Get a tool instance by name.
    
    Args:
        tool_name: Name of the tool to instantiate
        **kwargs: Additional arguments to pass to the tool factory
    
    Returns:
        Tool instance
    
    Raises:
        ValueError: If tool name is not recognized
    """
    if tool_name not in AVAILABLE_TOOLS:
        available = ', '.join(AVAILABLE_TOOLS.keys())
        raise ValueError(f"Unknown tool '{tool_name}'. Available tools: {available}")
    
    tool_config = AVAILABLE_TOOLS[tool_name]
    factory_func = tool_config['factory']
    
    return factory_func(**kwargs)


def list_available_tools(category: str = None) -> dict:
    """
    List all available tools, optionally filtered by category.
    
    Args:
        category: Optional category filter ('search', 'vision', 'development')
    
    Returns:
        Dictionary of tool configurations
    """
    if category:
        return {
            name: config for name, config in AVAILABLE_TOOLS.items()
            if config.get('category') == category
        }
    
    return AVAILABLE_TOOLS.copy()


def check_tool_requirements(tool_name: str) -> dict:
    """
    Check if a tool's requirements are met.
    
    Args:
        tool_name: Name of the tool to check
    
    Returns:
        Dictionary with requirement status
    """
    import os
    
    if tool_name not in AVAILABLE_TOOLS:
        return {'error': f"Unknown tool: {tool_name}"}
    
    config = AVAILABLE_TOOLS[tool_name]
    status = {
        'tool_name': tool_name,
        'available': True,
        'requirements_met': True,
        'missing_requirements': []
    }
    
    # Check API key requirements
    if config.get('requires_api_key'):
        api_key_env = config.get('api_key_env')
        if api_key_env and not os.getenv(api_key_env):
            status['requirements_met'] = False
            status['missing_requirements'].append(f"Environment variable {api_key_env} not set")
    
    return status


def create_tool_suite() -> TestSuite:
    """
    Create a comprehensive test suite for all tools.
    
    Returns:
        TestSuite with tests for all available tools
    """
    from .quality_framework import TestSuite, TestCase, TestType
    
    suite = TestSuite(
        name="comprehensive_tool_suite",
        tool_class=ToolImplementation,
        description="Comprehensive test suite for all AI tools"
    )
    
    # Add functional tests for each tool
    for tool_name, tool_config in AVAILABLE_TOOLS.items():
        tool_class = tool_config['class']
        
        # Basic functionality test
        basic_test = TestCase(
            id=f"{tool_name}_basic",
            name=f"Basic {tool_name} functionality",
            description=f"Test basic functionality of {tool_name}",
            test_type=TestType.FUNCTIONAL,
            input_data={'test': True},
            timeout_seconds=60,
            tags=[tool_name, 'basic', 'functional']
        )
        suite.add_test_case(basic_test)
        
        # Performance test
        perf_test = TestCase(
            id=f"{tool_name}_performance",
            name=f"{tool_name} performance benchmark",
            description=f"Performance benchmark for {tool_name}",
            test_type=TestType.PERFORMANCE,
            input_data={'benchmark': True},
            timeout_seconds=120,
            tags=[tool_name, 'performance', 'benchmark']
        )
        suite.add_test_case(perf_test)
    
    return suite


# Export everything
__all__ = [
    # Base classes
    'ToolImplementation', 'BaseInputModel', 'BaseOutputModel', 'ToolContext',
    'ToolError', 'ErrorCategory', 'QualityMetric', 'QualityScore', 'PerformanceMetrics',
    'performance_monitor', 'error_context',
    
    # Concrete tools
    'WebSearchTool', 'SearchInput', 'SearchOutput', 'SearchResult',
    'KnowledgeSearchTool', 'KnowledgeSearchInput', 'KnowledgeSearchOutput', 'KnowledgeResult',
    'ImageAnalysisTool', 'ImageAnalysisInput', 'ImageAnalysisOutput', 
    'DetectedObject', 'ExtractedText', 'SceneAnalysis', 'SafetyAnalysis',
    'ImageGenerationTool', 'ImageGenerationInput', 'ImageGenerationOutput', 'GeneratedImage',
    'CodeExecutionTool', 'CodeExecutionInput', 'CodeExecutionOutput', 'ExecutionResult',
    
    # Factory functions
    'create_web_search_tool', 'create_knowledge_search_tool', 'create_image_analysis_tool',
    'create_image_generation_tool', 'create_code_execution_tool',
    
    # Quality framework
    'TestType', 'TestStatus', 'BenchmarkCategory', 'TestCase', 'TestResult', 'BenchmarkResult',
    'TestSuite', 'TestExecutor', 'QualityAssessmentEngine',
    'create_quality_framework', 'create_test_suite',
    
    # Utility functions
    'get_tool_by_name', 'list_available_tools', 'check_tool_requirements', 'create_tool_suite',
    
    # Constants
    'AVAILABLE_TOOLS', 'QUALITY_STANDARDS'
]