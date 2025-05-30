#!/usr/bin/env python3
"""
Development Tools MCP Server

Provides comprehensive development tools including test parameter generation,
test judging, linting, document summarization, and image tagging.
"""

import json
import os
from typing import List, Optional, Dict, Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Import tool functions
from tools.test_params_tool import (
    TestParamConfig,
    generate_test_params,
    generate_ui_test_params,
    generate_code_quality_test_params,
    generate_custom_test_params
)
from tools.test_judge_tool import (
    JudgeConfig,
    judge_test_result,
    judge_code_quality,
    judge_response_quality,
    batch_judge_tests,
    judge_ui_feedback,
    judge_code_review
)
from tools.linting_tool import (
    LintConfig,
    run_linting,
    lint_files,
    quick_lint_check,
    lint_python_project,
    strict_lint_check,
    quick_format_check
)
from tools.doc_summary_tool import (
    SummaryConfig,
    summarize_document,
    summarize_url_document,
    batch_summarize_docs,
    quick_doc_summary,
    technical_doc_analysis
)
from tools.image_tagging_tool import (
    TaggingConfig,
    tag_image,
    batch_tag_images,
    extract_simple_tags,
    quick_tag_image,
    detailed_image_analysis,
    content_moderation_tags
)

# Add project root to path for workspace validation
import sys
from pathlib import Path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from utilities.workspace_validator import get_workspace_validator, WorkspaceAccessError

# Load environment variables
load_dotenv()

# Initialize MCP server
mcp = FastMCP("Development Tools")


def validate_directory_access(chat_id: str, file_path: str) -> Optional[str]:
    """Validate directory access for a chat ID and file path.
    
    Args:
        chat_id: Telegram chat ID making the request
        file_path: File or directory path to validate
        
    Returns:
        Error message if validation fails, None if access is allowed
    """
    if not chat_id:
        # Allow access if no chat_id provided (direct usage)
        return None
        
    try:
        validator = get_workspace_validator()
        validator.validate_directory_access(chat_id, file_path)
        return None  # Access allowed
    except WorkspaceAccessError as e:
        return f"❌ Directory Access Denied: {str(e)}"
    except Exception as e:
        return f"❌ Directory Validation Error: {str(e)}"


# ==================== TEST PARAMETER GENERATION TOOLS ====================

@mcp.tool()
def generate_test_parameters(
    test_type: str,
    param_categories: List[str],
    num_variations: int = 5,
    complexity_level: str = "medium",
    domain_context: Optional[str] = None
) -> str:
    """Generate diverse test parameters for AI subjective testing.
    
    Creates structured test scenarios with evaluation criteria for validating
    AI responses across different contexts and requirements.
    
    Args:
        test_type: Type of test (e.g., 'ui_feedback', 'code_quality', 'response_evaluation')
        param_categories: Categories of parameters to generate (e.g., ['ui_feedback', 'code_quality'])
        num_variations: Number of parameter variations to generate (default: 5)
        complexity_level: Complexity level - 'simple', 'medium', or 'complex' (default: 'medium')
        domain_context: Optional domain-specific context (e.g., 'healthcare', 'finance')
    
    Returns:
        JSON string containing generated test parameters with evaluation criteria
    """
    try:
        config = TestParamConfig(
            test_type=test_type,
            param_categories=param_categories,
            num_variations=num_variations,
            complexity_level=complexity_level,
            domain_context=domain_context
        )
        
        params = generate_test_params(config)
        return json.dumps([p.model_dump() for p in params], indent=2)
        
    except Exception as e:
        return f"❌ Error generating test parameters: {str(e)}"


@mcp.tool()
def generate_ui_testing_params(num_variations: int = 5, complexity: str = "medium") -> str:
    """Generate test parameters specifically for UI feedback evaluation.
    
    Creates parameters for testing AI responses to user interface feedback scenarios,
    including different user expertise levels, interface styles, and feedback contexts.
    
    Args:
        num_variations: Number of parameter variations to generate
        complexity: Complexity level - 'simple', 'medium', or 'complex'
    
    Returns:
        JSON string containing UI-specific test parameters
    """
    try:
        return generate_ui_test_params(num_variations, complexity)
    except Exception as e:
        return f"❌ Error generating UI test parameters: {str(e)}"


@mcp.tool()
def generate_code_testing_params(num_variations: int = 5, complexity: str = "medium") -> str:
    """Generate test parameters for code quality evaluation.
    
    Creates parameters for testing AI responses to code review and quality assessment
    scenarios with different coding styles, priorities, and contexts.
    
    Args:
        num_variations: Number of parameter variations to generate
        complexity: Complexity level - 'simple', 'medium', or 'complex'
    
    Returns:
        JSON string containing code quality test parameters
    """
    try:
        return generate_code_quality_test_params(num_variations, complexity)
    except Exception as e:
        return f"❌ Error generating code test parameters: {str(e)}"


# ==================== TEST JUDGING TOOLS ====================

@mcp.tool()
def judge_ai_response(
    response_text: str,
    evaluation_criteria: List[str],
    test_context: Dict[str, Any],
    model: str = "gemma2:3b",
    strict_mode: bool = True
) -> str:
    """Judge AI response quality using local models for fast, cost-effective evaluation.
    
    Uses local models (via Ollama) to provide consistent, objective evaluation of
    AI responses against specified criteria with structured feedback.
    
    Args:
        response_text: The AI response to evaluate
        evaluation_criteria: List of criteria to judge against (e.g., ['accuracy', 'clarity'])
        test_context: Context information including test_id, test_type, etc.
        model: Local model to use for judging (default: 'gemma2:3b')
        strict_mode: Whether to apply strict evaluation standards
    
    Returns:
        JSON string containing judgment results with scores and feedback
    """
    try:
        config = JudgeConfig(
            model=model,
            strict_mode=strict_mode
        )
        
        judgment = judge_test_result(response_text, evaluation_criteria, test_context, config)
        return json.dumps(judgment.model_dump(), indent=2)
        
    except Exception as e:
        return f"❌ Error judging response: {str(e)}"


@mcp.tool()
def judge_code_quality_response(
    code: str,
    language: str,
    quality_criteria: List[str],
    model: str = "gemma2:3b"
) -> str:
    """Judge code quality using local AI models.
    
    Evaluates code against quality criteria like readability, correctness,
    maintainability, and best practices using local models.
    
    Args:
        code: The code to evaluate
        language: Programming language (e.g., 'python', 'javascript')
        quality_criteria: Quality criteria to evaluate (e.g., ['readability', 'correctness'])
        model: Local model to use for judging
    
    Returns:
        JSON string containing code quality judgment
    """
    try:
        config = JudgeConfig(model=model)
        judgment = judge_code_quality(code, language, quality_criteria, config)
        return json.dumps(judgment.model_dump(), indent=2)
        
    except Exception as e:
        return f"❌ Error judging code quality: {str(e)}"


@mcp.tool()
def batch_judge_responses(test_cases: List[Dict[str, Any]], model: str = "gemma2:3b") -> str:
    """Judge multiple test responses in batch for efficiency.
    
    Processes multiple test cases simultaneously, useful for evaluating
    AI performance across different scenarios or parameter sets.
    
    Args:
        test_cases: List of test cases, each with 'output', 'criteria', and 'context'
        model: Local model to use for judging
    
    Returns:
        JSON string containing batch judgment results
    """
    try:
        config = JudgeConfig(model=model)
        judgments = batch_judge_tests(test_cases, config)
        return json.dumps([j.model_dump() for j in judgments], indent=2)
        
    except Exception as e:
        return f"❌ Error in batch judging: {str(e)}"


# ==================== CODE LINTING TOOLS ====================

@mcp.tool()
def lint_python_code(
    project_path: str,
    run_ruff: bool = True,
    run_black: bool = True,
    run_mypy: bool = False,
    fix_issues: bool = False,
    chat_id: str = ""
) -> str:
    """Run comprehensive Python code linting and formatting checks with directory access controls.
    
    Executes multiple linting tools (ruff, black, mypy, flake8) and provides
    aggregated results with issue categorization and fix suggestions.
    
    Args:
        project_path: Path to the Python project or file to lint
        run_ruff: Whether to run ruff linter (recommended)
        run_black: Whether to run black formatter check
        run_mypy: Whether to run mypy type checker (can be slow)
        fix_issues: Whether to automatically fix fixable issues
        chat_id: Telegram chat ID for directory access validation (optional)
    
    Returns:
        JSON string containing linting results with issues and summary
    """
    # Validate directory access
    access_error = validate_directory_access(chat_id, project_path)
    if access_error:
        return access_error
    
    try:
        config = LintConfig(
            run_ruff=run_ruff,
            run_black=run_black,
            run_mypy=run_mypy,
            fix_issues=fix_issues
        )
        
        result = run_linting(project_path, config)
        return json.dumps(result.model_dump(), indent=2)
        
    except Exception as e:
        return f"❌ Error running linting: {str(e)}"


@mcp.tool()
def lint_specific_files(file_paths: List[str], fix_formatting: bool = False, chat_id: str = "") -> str:
    """Lint specific Python files rather than entire project with directory access controls.
    
    Focused linting for specific files, useful for targeted code review
    or when working with individual modules.
    
    Args:
        file_paths: List of specific Python files to lint
        fix_formatting: Whether to automatically fix formatting issues
        chat_id: Telegram chat ID for directory access validation (optional)
    
    Returns:
        JSON string containing linting results for specified files
    """
    # Validate directory access for all file paths
    for file_path in file_paths:
        access_error = validate_directory_access(chat_id, file_path)
        if access_error:
            return access_error
    
    try:
        config = LintConfig(fix_issues=fix_formatting)
        result = lint_files(file_paths, config)
        return json.dumps(result.model_dump(), indent=2)
        
    except Exception as e:
        return f"❌ Error linting files: {str(e)}"


@mcp.tool()
def quick_code_check(file_path: str, chat_id: str = "") -> str:
    """Quick pass/fail code quality check for a single file with directory access controls.
    
    Fast code quality validation, useful for CI/CD or rapid feedback
    during development.
    
    Args:
        file_path: Path to the Python file to check
        chat_id: Telegram chat ID for directory access validation (optional)
    
    Returns:
        Simple pass/fail result with basic summary
    """
    # Validate directory access
    access_error = validate_directory_access(chat_id, file_path)
    if access_error:
        return access_error
    
    try:
        passed = quick_lint_check(file_path)
        return f"✅ Code quality check: {'PASSED' if passed else 'FAILED'}"
        
    except Exception as e:
        return f"❌ Error in quick check: {str(e)}"


@mcp.tool()
def comprehensive_project_lint(project_path: str, chat_id: str = "") -> str:
    """Run comprehensive linting with all tools enabled and directory access controls.
    
    Strict linting analysis using all available tools for thorough
    code quality assessment.
    
    Args:
        project_path: Path to the Python project to analyze
        chat_id: Telegram chat ID for directory access validation (optional)
    
    Returns:
        JSON string containing comprehensive linting results
    """
    # Validate directory access
    access_error = validate_directory_access(chat_id, project_path)
    if access_error:
        return access_error
    
    try:
        result = strict_lint_check(project_path)
        return json.dumps(result.model_dump(), indent=2)
        
    except Exception as e:
        return f"❌ Error in comprehensive linting: {str(e)}"


# ==================== DOCUMENT SUMMARIZATION TOOLS ====================

@mcp.tool()
def summarize_code_documentation(
    document_path: str,
    max_section_words: int = 500,
    summary_style: str = "comprehensive",
    focus_topics: Optional[List[str]] = None,
    chat_id: str = ""
) -> str:
    """Read and summarize large documents (markdown, code files, text) with directory access controls.
    
    Automatically detects document type and creates structured summaries
    with section analysis, key insights, and reading time estimates.
    
    Args:
        document_path: Path to the document to summarize
        max_section_words: Maximum words per section summary
        summary_style: Style of summary - 'brief', 'comprehensive', or 'technical'
        focus_topics: Optional list of topics to focus on during analysis
        chat_id: Telegram chat ID for directory access validation (optional)
    
    Returns:
        JSON string containing structured document summary
    """
    # Validate directory access
    access_error = validate_directory_access(chat_id, document_path)
    if access_error:
        return access_error
    
    try:
        config = SummaryConfig(
            max_section_words=max_section_words,
            summary_style=summary_style,
            focus_topics=focus_topics
        )
        
        summary = summarize_document(document_path, config)
        return json.dumps(summary.model_dump(), indent=2)
        
    except Exception as e:
        return f"❌ Error summarizing document: {str(e)}"


@mcp.tool()
def summarize_url_content(url: str, summary_style: str = "comprehensive") -> str:
    """Summarize document content from a URL.
    
    Downloads and analyzes documents from web URLs, useful for
    processing GitHub READMEs, documentation sites, or online articles.
    
    Args:
        url: URL of the document to summarize
        summary_style: Style of summary - 'brief', 'comprehensive', or 'technical'
    
    Returns:
        JSON string containing URL document summary
    """
    try:
        config = SummaryConfig(summary_style=summary_style)
        summary = summarize_url_document(url, config)
        return json.dumps(summary.model_dump(), indent=2)
        
    except Exception as e:
        return f"❌ Error summarizing URL content: {str(e)}"


@mcp.tool()
def batch_summarize_documents(
    document_paths: List[str],
    summary_style: str = "comprehensive",
    chat_id: str = ""
) -> str:
    """Summarize multiple documents in batch for efficiency with directory access controls.
    
    Process multiple documents simultaneously, useful for analyzing
    entire documentation sets or code repositories.
    
    Args:
        document_paths: List of document paths to summarize
        summary_style: Style of summary for all documents
        chat_id: Telegram chat ID for directory access validation (optional)
    
    Returns:
        JSON string containing summaries for all documents
    """
    # Validate directory access for all document paths
    for document_path in document_paths:
        access_error = validate_directory_access(chat_id, document_path)
        if access_error:
            return access_error
    
    try:
        config = SummaryConfig(summary_style=summary_style)
        summaries = batch_summarize_docs(document_paths, config)
        
        # Convert to serializable format
        result = {}
        for path, summary in summaries.items():
            result[path] = summary.model_dump()
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        return f"❌ Error in batch summarization: {str(e)}"


@mcp.tool()
def quick_document_overview(file_path: str, chat_id: str = "") -> str:
    """Get a quick text overview of a document with directory access controls.
    
    Fast document analysis providing a brief summary, useful for
    quick document assessment or content verification.
    
    Args:
        file_path: Path to the document to analyze
        chat_id: Telegram chat ID for directory access validation (optional)
    
    Returns:
        Brief text summary of the document
    """
    # Validate directory access
    access_error = validate_directory_access(chat_id, file_path)
    if access_error:
        return access_error
    
    try:
        return quick_doc_summary(file_path)
        
    except Exception as e:
        return f"❌ Error getting document overview: {str(e)}"


# ==================== IMAGE ANALYSIS TOOLS ====================

@mcp.tool()
def analyze_image_content(
    image_path: str,
    max_tags: int = 20,
    min_confidence: float = 0.3,
    api_provider: str = "openai",
    use_local_model: bool = False,
    chat_id: str = ""
) -> str:
    """Analyze image and generate comprehensive tags using AI vision models with directory access controls.
    
    Uses AI vision capabilities to identify objects, scenes, styles, colors,
    and mood in images with structured tag output and confidence scores.
    
    Args:
        image_path: Path to the image file to analyze
        max_tags: Maximum number of tags to generate
        min_confidence: Minimum confidence threshold for tags (0.0-1.0)
        api_provider: API provider - 'openai', 'anthropic', or 'local'
        use_local_model: Whether to use local vision model (via Ollama)
        chat_id: Telegram chat ID for directory access validation (optional)
    
    Returns:
        JSON string containing comprehensive image analysis
    """
    # Validate directory access
    access_error = validate_directory_access(chat_id, image_path)
    if access_error:
        return access_error
    
    try:
        config = TaggingConfig(
            max_tags=max_tags,
            min_confidence=min_confidence,
            api_provider=api_provider,
            local_model=use_local_model
        )
        
        analysis = tag_image(image_path, config)
        return json.dumps(analysis.model_dump(), indent=2)
        
    except Exception as e:
        return f"❌ Error analyzing image: {str(e)}"


@mcp.tool()
def get_simple_image_tags(image_path: str, max_tags: int = 10, chat_id: str = "") -> str:
    """Extract simple list of tags from an image with directory access controls.
    
    Quick image tagging without detailed analysis, useful for basic
    content categorization or search indexing.
    
    Args:
        image_path: Path to the image file
        max_tags: Maximum number of tags to return
        chat_id: Telegram chat ID for directory access validation (optional)
    
    Returns:
        JSON array of tag strings
    """
    # Validate directory access
    access_error = validate_directory_access(chat_id, image_path)
    if access_error:
        return access_error
    
    try:
        tags = extract_simple_tags(image_path, max_tags)
        return json.dumps(tags)
        
    except Exception as e:
        return f"❌ Error extracting image tags: {str(e)}"


@mcp.tool()
def validate_directory_access_tool(chat_id: str, file_path: str) -> str:
    """Validate if a chat has access to a specific directory or file path.
    
    Args:
        chat_id: Telegram chat ID to validate
        file_path: File or directory path to check access for
        
    Returns:
        str: Validation result with access details
    """
    try:
        validator = get_workspace_validator()
        validator.validate_directory_access(chat_id, file_path)
        
        # Get workspace details
        allowed_workspace = validator.get_workspace_for_chat(chat_id)
        allowed_dirs = validator.get_allowed_directories(chat_id)
        
        return f"✅ **Directory Access Granted**\n\n" \
               f"• Chat: {chat_id}\n" \
               f"• File Path: {file_path}\n" \
               f"• Workspace: {allowed_workspace}\n" \
               f"• Allowed Directories: {', '.join(allowed_dirs)}"
        
    except WorkspaceAccessError as e:
        return f"❌ **Directory Access Denied**: {str(e)}"
    except Exception as e:
        return f"❌ **Directory Validation Error**: {str(e)}"


@mcp.tool()
def batch_analyze_images(
    image_paths: List[str],
    max_tags: int = 15,
    api_provider: str = "openai"
) -> str:
    """Analyze multiple images in batch for efficiency.
    
    Process multiple images simultaneously, useful for gallery analysis
    or bulk content processing.
    
    Args:
        image_paths: List of image file paths to analyze
        max_tags: Maximum tags per image
        api_provider: API provider for analysis
    
    Returns:
        JSON string containing analysis results for all images
    """
    try:
        config = TaggingConfig(max_tags=max_tags, api_provider=api_provider)
        results = batch_tag_images(image_paths, config)
        
        # Convert to serializable format
        serialized_results = {}
        for path, analysis in results.items():
            serialized_results[path] = analysis.model_dump()
        
        return json.dumps(serialized_results, indent=2)
        
    except Exception as e:
        return f"❌ Error in batch image analysis: {str(e)}"


@mcp.tool()
def analyze_image_for_moderation(image_path: str) -> str:
    """Analyze image for content moderation purposes.
    
    Extracts tags relevant for content moderation, safety assessment,
    and appropriateness evaluation.
    
    Args:
        image_path: Path to the image to analyze
    
    Returns:
        JSON array of moderation-relevant tags
    """
    try:
        tags = content_moderation_tags(image_path)
        return json.dumps(tags)
        
    except Exception as e:
        return f"❌ Error in content moderation analysis: {str(e)}"


@mcp.tool()
def detailed_image_assessment(image_path: str) -> str:
    """Comprehensive image analysis with technical details.
    
    Provides detailed analysis including technical quality assessment,
    composition analysis, and comprehensive tagging.
    
    Args:
        image_path: Path to the image file
    
    Returns:
        JSON string containing detailed image assessment
    """
    try:
        analysis = detailed_image_analysis(image_path)
        return json.dumps(analysis.model_dump(), indent=2)
        
    except Exception as e:
        return f"❌ Error in detailed image assessment: {str(e)}"


if __name__ == "__main__":
    mcp.run()