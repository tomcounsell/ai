# Development Tools MCP Server

## Overview

The Development Tools MCP Server provides comprehensive development utilities including test parameter generation, test judging, linting, document summarization, image analysis, workspace context management, and automated bug report workflows. This server represents a full-stack development support system with security validation and workspace-aware operations.

## Server Architecture

### Import Strategy and Tool Organization

The server follows a modular architecture importing specialized tools from dedicated modules:

```python
# Test and Quality Tools
from tools.test_params_tool import generate_test_params, generate_ui_test_params
from tools.test_judge_tool import judge_test_result, batch_judge_tests
from tools.linting_tool import run_linting, quick_lint_check
from tools.doc_summary_tool import summarize_document, batch_summarize_docs
from tools.image_tagging_tool import tag_image, detailed_image_analysis

# Workspace Security
from utilities.workspace_validator import WorkspaceAccessError, get_workspace_validator

# Integration Tools  
from tools.documentation_tool import read_documentation, list_documentation_files
from utilities.claude_sdk_wrapper import ClaudeCodeSDK, ClaudeTaskOptions
```

### Security-First Architecture

All file system operations include directory access validation:

```python
def validate_directory_access(chat_id: str, file_path: str) -> str | None:
    """Validate directory access with comprehensive security checks"""
    if not chat_id:
        return None  # Allow direct usage
    
    try:
        validator = get_workspace_validator()
        validator.validate_directory_access(chat_id, file_path)
        return None  # Access allowed
    except WorkspaceAccessError as e:
        return f"❌ Directory Access Denied: {str(e)}"
```

### Workspace Context Integration

```python
def get_project_context(chat_id: str = "") -> str:
    """Comprehensive project context with workspace resolution"""
    
    # Workspace detection
    if chat_id:
        validator = get_workspace_validator()
        workspace = validator.get_workspace_for_chat(chat_id)
        working_dir = validator.get_working_directory(chat_id)
    
    # Context assembly: README, CLAUDE.md, workspace metadata
    # Enhanced with security validation and fallback strategies
```

## Tool Categories and Specifications

## Test Parameter Generation Tools

### generate_test_parameters

**Purpose**: Generate diverse test parameters for AI subjective testing with structured evaluation criteria

#### Input Parameters
```python
def generate_test_parameters(
    test_type: str,
    param_categories: list[str],
    num_variations: int = 5,
    complexity_level: str = "medium",
    domain_context: str | None = None
) -> str:
```

- **test_type** (required): Type of test - "ui_feedback", "code_quality", "response_evaluation"
- **param_categories** (required): Categories array - ["ui_feedback", "code_quality", "performance"]
- **num_variations** (optional): Number of parameter variations (default 5)
- **complexity_level** (optional): "simple", "medium", or "complex" (default "medium")
- **domain_context** (optional): Domain-specific context - "healthcare", "finance", "e-commerce"

#### Implementation Pattern

```python
# Configuration-driven parameter generation
config = TestParamConfig(
    test_type=test_type,
    param_categories=param_categories,
    num_variations=num_variations,
    complexity_level=complexity_level,
    domain_context=domain_context,
)

params = generate_test_params(config)
return json.dumps([p.model_dump() for p in params], indent=2)
```

#### Output Format

```json
[
  {
    "test_id": "ui_feedback_001",
    "test_type": "ui_feedback",
    "parameters": {
      "user_expertise": "novice",
      "interface_style": "minimalist",
      "feedback_context": "first_time_user",
      "evaluation_focus": "usability"
    },
    "evaluation_criteria": [
      "clarity_of_feedback",
      "actionability_of_suggestions",
      "user_empathy_level"
    ],
    "complexity_level": "medium",
    "domain_context": "healthcare"
  }
]
```

### generate_ui_testing_params

**Purpose**: Specialized UI feedback evaluation parameter generation

#### Input Parameters
```python
def generate_ui_testing_params(num_variations: int = 5, complexity: str = "medium") -> str:
```

- **num_variations** (optional): Number of UI test variations (default 5)
- **complexity** (optional): Test complexity level

#### UI-Specific Parameters

Generated parameters cover:
- **User Expertise Levels**: Novice, intermediate, expert, accessibility-focused
- **Interface Styles**: Minimalist, rich, mobile-first, desktop-heavy
- **Feedback Contexts**: First-time use, power user, error recovery, workflow completion
- **Evaluation Focus**: Usability, accessibility, visual design, interaction flow

### generate_code_testing_params

**Purpose**: Code quality evaluation parameter generation with language-specific considerations

#### Input Parameters
```python
def generate_code_testing_params(num_variations: int = 5, complexity: str = "medium") -> str:
```

- **num_variations** (optional): Number of code test variations
- **complexity** (optional): Code complexity level

#### Code Quality Dimensions

Parameters include:
- **Code Languages**: Python, JavaScript, TypeScript, Go, Rust
- **Quality Aspects**: Readability, maintainability, performance, security
- **Review Contexts**: Production code, prototype, refactoring, new features
- **Team Contexts**: Junior developer, senior review, architecture decisions

## Test Judging Tools

### judge_ai_response

**Purpose**: AI response quality evaluation using local models for fast, cost-effective assessment

#### Input Parameters
```python
def judge_ai_response(
    response_text: str,
    evaluation_criteria: list[str],
    test_context: dict[str, Any],
    model: str = "gemma2:3b",
    strict_mode: bool = True
) -> str:
```

- **response_text** (required): AI response to evaluate
- **evaluation_criteria** (required): Criteria list - ["accuracy", "clarity", "completeness"]
- **test_context** (required): Context dict with test_id, test_type, parameters
- **model** (optional): Local model for judging (default "gemma2:3b")
- **strict_mode** (optional): Strict evaluation standards (default True)

#### Local Model Integration

```python
# Configuration for consistent local evaluation
config = JudgeConfig(model=model, strict_mode=strict_mode)

# Structured judgment with scoring
judgment = judge_test_result(response_text, evaluation_criteria, test_context, config)
return json.dumps(judgment.model_dump(), indent=2)
```

#### Output Format

```json
{
  "test_id": "ui_feedback_001",
  "judgment_result": "pass",
  "overall_score": 8.5,
  "criteria_scores": {
    "accuracy": 9.0,
    "clarity": 8.0,
    "completeness": 8.5
  },
  "feedback": {
    "strengths": ["Clear explanations", "Actionable suggestions"],
    "weaknesses": ["Could provide more examples"],
    "improvement_suggestions": ["Add visual mockups for UI suggestions"]
  },
  "model_used": "gemma2:3b",
  "evaluation_time": "2024-01-15T10:30:00Z"
}
```

### judge_code_quality_response

**Purpose**: Specialized code quality evaluation with language-specific best practices

#### Input Parameters
```python
def judge_code_quality_response(
    code: str, 
    language: str, 
    quality_criteria: list[str], 
    model: str = "gemma2:3b"
) -> str:
```

- **code** (required): Code to evaluate
- **language** (required): Programming language - "python", "javascript", "typescript"
- **quality_criteria** (required): Quality criteria - ["readability", "correctness", "maintainability"]
- **model** (optional): Local model for judging

#### Language-Specific Evaluation

```python
# Language-aware code quality assessment
config = JudgeConfig(model=model)
judgment = judge_code_quality(code, language, quality_criteria, config)

# Detailed technical feedback with language-specific best practices
return json.dumps(judgment.model_dump(), indent=2)
```

### batch_judge_responses

**Purpose**: Efficient batch evaluation for multiple test cases simultaneously

#### Input Parameters
```python
def batch_judge_responses(test_cases: list[dict[str, Any]], model: str = "gemma2:3b") -> str:
```

- **test_cases** (required): List of test cases with 'output', 'criteria', and 'context' keys
- **model** (optional): Local model for batch judging

#### Batch Processing Pattern

```python
# Efficient batch processing with consistent model usage
config = JudgeConfig(model=model)
judgments = batch_judge_tests(test_cases, config)

# Aggregated results with comparative analysis
return json.dumps([j.model_dump() for j in judgments], indent=2)
```

## Code Linting Tools

### lint_python_code

**Purpose**: Comprehensive Python code linting with multiple tools and directory access controls

#### Input Parameters
```python
def lint_python_code(
    project_path: str,
    run_ruff: bool = True,
    run_black: bool = True,
    run_mypy: bool = False,
    fix_issues: bool = False,
    chat_id: str = ""
) -> str:
```

- **project_path** (required): Path to Python project or file
- **run_ruff** (optional): Enable Ruff linter (default True)
- **run_black** (optional): Enable Black formatter check (default True)
- **run_mypy** (optional): Enable Mypy type checker (default False - can be slow)
- **fix_issues** (optional): Auto-fix fixable issues (default False)
- **chat_id** (optional): Chat ID for directory access validation

#### Security Integration

```python
# Directory access validation before linting
access_error = validate_directory_access(chat_id, project_path)
if access_error:
    return access_error

# Multi-tool linting configuration
config = LintConfig(
    run_ruff=run_ruff, 
    run_black=run_black, 
    run_mypy=run_mypy, 
    fix_issues=fix_issues
)

result = run_linting(project_path, config)
```

#### Linting Tool Integration

```python
# Comprehensive linting pipeline
LINTING_TOOLS = {
    "ruff": {
        "enabled": True,
        "purpose": "Fast Python linting and formatting",
        "fixes": "Import sorting, unused imports, PEP compliance"
    },
    "black": {
        "enabled": True, 
        "purpose": "Code formatting consistency",
        "fixes": "Automatic code formatting"
    },
    "mypy": {
        "enabled": False,  # Optional due to performance
        "purpose": "Static type checking",
        "fixes": "Type annotation validation"
    }
}
```

#### Output Format

```json
{
  "project_path": "/path/to/project",
  "tools_executed": ["ruff", "black"],
  "summary": {
    "total_files": 25,
    "files_with_issues": 8,
    "total_issues": 42,
    "fixable_issues": 30,
    "fixed_issues": 0
  },
  "issues_by_tool": {
    "ruff": {
      "total_issues": 35,
      "issue_types": {
        "unused_import": 12,
        "line_too_long": 15,
        "missing_docstring": 8
      }
    },
    "black": {
      "total_issues": 7,
      "formatting_issues": 7
    }
  },
  "issues_by_file": {
    "src/main.py": {
      "ruff": ["F401: unused import", "E501: line too long"],
      "black": ["reformatting needed"]
    }
  }
}
```

### lint_specific_files

**Purpose**: Focused linting for specific files with security validation

#### Input Parameters
```python
def lint_specific_files(
    file_paths: list[str], 
    fix_formatting: bool = False, 
    chat_id: str = ""
) -> str:
```

- **file_paths** (required): List of specific Python files to lint
- **fix_formatting** (optional): Auto-fix formatting issues
- **chat_id** (optional): Chat ID for directory access validation

#### Multi-File Validation

```python
# Validate directory access for all file paths
for file_path in file_paths:
    access_error = validate_directory_access(chat_id, file_path)
    if access_error:
        return access_error

# Focused linting configuration
config = LintConfig(fix_issues=fix_formatting)
result = lint_files(file_paths, config)
```

### quick_code_check

**Purpose**: Fast pass/fail code quality check for CI/CD integration

#### Input Parameters
```python
def quick_code_check(file_path: str, chat_id: str = "") -> str:
```

- **file_path** (required): Python file to check
- **chat_id** (optional): Chat ID for validation

#### Rapid Quality Assessment

```python
# Fast validation with binary result
access_error = validate_directory_access(chat_id, file_path)
if access_error:
    return access_error

passed = quick_lint_check(file_path)
return f"✅ Code quality check: {'PASSED' if passed else 'FAILED'}"
```

### comprehensive_project_lint

**Purpose**: Comprehensive linting with all tools enabled for thorough assessment

#### Implementation

```python
def comprehensive_project_lint(project_path: str, chat_id: str = "") -> str:
    """Strict linting analysis with all tools"""
    
    access_error = validate_directory_access(chat_id, project_path)
    if access_error:
        return access_error

    result = strict_lint_check(project_path)  # All tools enabled
    return json.dumps(result.model_dump(), indent=2)
```

## Document Summarization Tools

### summarize_code_documentation

**Purpose**: AI-powered document analysis with automatic type detection and structured output

#### Input Parameters
```python
def summarize_code_documentation(
    document_path: str,
    max_section_words: int = 500,
    summary_style: str = "comprehensive",
    focus_topics: list[str] | None = None,
    chat_id: str = ""
) -> str:
```

- **document_path** (required): Path to document for summarization
- **max_section_words** (optional): Maximum words per section summary (default 500)
- **summary_style** (optional): "brief", "comprehensive", or "technical" (default "comprehensive")
- **focus_topics** (optional): Specific topics to emphasize
- **chat_id** (optional): Chat ID for directory access validation

#### Document Type Detection

```python
# Automatic document type detection and processing
SUPPORTED_FORMATS = {
    ".md": "markdown_processor",
    ".py": "python_code_processor", 
    ".js": "javascript_processor",
    ".ts": "typescript_processor",
    ".txt": "text_processor",
    ".rst": "restructuredtext_processor"
}

# Configuration-driven summarization
config = SummaryConfig(
    max_section_words=max_section_words,
    summary_style=summary_style,
    focus_topics=focus_topics,
)

summary = summarize_document(document_path, config)
```

#### Output Format

```json
{
  "document_path": "/path/to/README.md",
  "document_type": "markdown",
  "summary_metadata": {
    "total_words": 2500,
    "reading_time_minutes": 10,
    "summary_style": "comprehensive",
    "processing_time": "2024-01-15T10:30:00Z"
  },
  "sections": [
    {
      "title": "Getting Started",
      "summary": "Installation and basic setup instructions...",
      "key_points": ["Prerequisites", "Installation", "Configuration"],
      "word_count": 450
    }
  ],
  "key_insights": [
    "Project uses microservices architecture",
    "Requires Docker for development",
    "Has comprehensive testing suite"
  ],
  "focus_topic_analysis": {
    "architecture": "Detailed coverage in sections 2-4",
    "deployment": "Limited coverage, mainly in appendix"
  }
}
```

### summarize_url_content

**Purpose**: Web document analysis with automatic content extraction

#### Input Parameters
```python
def summarize_url_content(url: str, summary_style: str = "comprehensive") -> str:
```

- **url** (required): URL of document to summarize
- **summary_style** (optional): Summary style preference

#### Web Content Processing

```python
# URL document processing with content extraction
config = SummaryConfig(summary_style=summary_style)
summary = summarize_url_document(url, config)

# Structured web content analysis
return json.dumps(summary.model_dump(), indent=2)
```

### batch_summarize_documents

**Purpose**: Efficient multi-document processing with workspace security

#### Input Parameters
```python
def batch_summarize_documents(
    document_paths: list[str], 
    summary_style: str = "comprehensive", 
    chat_id: str = ""
) -> str:
```

- **document_paths** (required): List of documents to process
- **summary_style** (optional): Consistent style across all documents
- **chat_id** (optional): Chat ID for security validation

#### Batch Processing Security

```python
# Validate directory access for all document paths
for document_path in document_paths:
    access_error = validate_directory_access(chat_id, document_path)
    if access_error:
        return access_error

# Efficient batch processing
config = SummaryConfig(summary_style=summary_style)
summaries = batch_summarize_docs(document_paths, config)

# Structured multi-document results
result = {}
for path, summary in summaries.items():
    result[path] = summary.model_dump()
```

## Image Analysis Tools

### analyze_image_content

**Purpose**: Comprehensive AI-powered image analysis with multiple provider support

#### Input Parameters
```python
def analyze_image_content(
    image_path: str,
    max_tags: int = 20,
    min_confidence: float = 0.3,
    api_provider: str = "openai",
    use_local_model: bool = False,
    chat_id: str = ""
) -> str:
```

- **image_path** (required): Path to image file
- **max_tags** (optional): Maximum tags to generate (default 20)
- **min_confidence** (optional): Confidence threshold 0.0-1.0 (default 0.3)
- **api_provider** (optional): "openai", "anthropic", or "local" (default "openai")
- **use_local_model** (optional): Use local vision model via Ollama
- **chat_id** (optional): Chat ID for directory access validation

#### Vision Analysis Pipeline

```python
# Multi-provider vision analysis
config = TaggingConfig(
    max_tags=max_tags,
    min_confidence=min_confidence,
    api_provider=api_provider,
    local_model=use_local_model,
)

analysis = tag_image(image_path, config)
```

#### Comprehensive Image Analysis Output

```json
{
  "image_path": "/path/to/image.jpg",
  "image_metadata": {
    "dimensions": {"width": 1920, "height": 1080},
    "file_size": "2.5MB",
    "format": "JPEG"
  },
  "analysis_results": {
    "objects": [
      {"name": "laptop", "confidence": 0.95, "bounding_box": [100, 50, 400, 300]},
      {"name": "coffee_cup", "confidence": 0.87, "bounding_box": [450, 200, 500, 280]}
    ],
    "scenes": ["office", "workspace", "indoor"],
    "colors": {
      "dominant": ["#2F4F4F", "#D2B48C", "#708090"],
      "palette": "professional_blue_brown"
    },
    "style_analysis": {
      "photography_style": "professional",
      "lighting": "natural",
      "composition": "rule_of_thirds"
    },
    "mood_tags": ["productive", "organized", "professional"],
    "technical_quality": {
      "sharpness": 0.92,
      "exposure": "well_balanced",
      "noise_level": "low"
    }
  },
  "api_provider": "openai",
  "processing_time": "2.3s",
  "total_tags": 15
}
```

### get_simple_image_tags

**Purpose**: Fast image tagging for basic categorization and search indexing

#### Input Parameters
```python
def get_simple_image_tags(image_path: str, max_tags: int = 10, chat_id: str = "") -> str:
```

- **image_path** (required): Image file path
- **max_tags** (optional): Maximum tags to return (default 10)
- **chat_id** (optional): Chat ID for validation

#### Simple Tagging Output

```json
[
  "laptop",
  "coffee",
  "workspace",
  "professional",
  "indoor",
  "technology",
  "business",
  "modern",
  "clean",
  "organized"
]
```

## Workspace Context Tools

### get_project_context_tool

**Purpose**: Comprehensive project context with workspace detection and documentation parsing

#### Input Parameters
```python
def get_project_context_tool(chat_id: str = "") -> str:
```

- **chat_id** (optional): Telegram chat ID for workspace detection

#### Context Assembly Process

```python
def get_project_context(chat_id: str = "") -> str:
    """Multi-layer context assembly"""
    
    context_parts = []
    
    # Layer 1: Workspace Detection
    if chat_id:
        validator = get_workspace_validator()
        workspace = validator.get_workspace_for_chat(chat_id)
        working_dir = validator.get_working_directory(chat_id)
        
        if workspace and working_dir:
            context_parts.append(f"**Current Workspace**: {workspace}")
            context_parts.append(f"**Working Directory**: {working_dir}")
    
    # Layer 2: Documentation Parsing
    readme_patterns = ["README.md", "readme.md", "README", "readme.txt"]
    for pattern in readme_patterns:
        readme_path = Path(working_dir) / pattern
        if readme_path.exists():
            readme_content = readme_path.read_text(encoding="utf-8")[:2000]
            context_parts.append(f"**Project README**:\n{readme_content}")
            break
    
    # Layer 3: Project Instructions  
    claude_md_path = Path(working_dir) / "CLAUDE.md"
    if claude_md_path.exists():
        claude_content = claude_md_path.read_text(encoding="utf-8")[:2000]
        context_parts.append(f"**Project Instructions (CLAUDE.md)**:\n{claude_content}")
    
    return "\n\n".join(context_parts)
```

### run_project_prime_command

**Purpose**: Simulate Claude Code's /prime command for comprehensive project overview

#### Input Parameters
```python
def run_project_prime_command(chat_id: str = "") -> str:
```

- **chat_id** (optional): Chat ID for workspace-specific priming

#### Prime Command Output

```
# Project Primer

**Current Workspace**: ai-system
**Working Directory**: /Users/valorengels/src/ai

**Project README**:
# AI System
Conversational development environment with Claude Code integration...

**Project Instructions (CLAUDE.md)**:
# CLAUDE.md
**IMPORTANT CONTEXT**: When working with this codebase...

## Workspace Configuration
- **Workspace**: ai-system
- **Working Directory**: /Users/valorengels/src/ai
- **Chat ID**: 123456789

## Project Structure
```
📁 agents/
📁 integrations/
📁 mcp_servers/
📁 tools/
📁 utilities/
📄 CLAUDE.md
📄 README.md
```

## Development Context
You are now working in this project's workspace. All development tasks should consider:
1. The current workspace configuration and restrictions
2. Project-specific patterns and conventions from CLAUDE.md
3. Existing project structure and files
4. Any workspace-specific development workflows

Ready for development tasks in this context!
```

## Advanced Workflow Tools

### retrieve_workspace_screenshot

**Purpose**: Automated screenshot retrieval from Claude Code sessions with AI analysis

#### Input Parameters
```python
def retrieve_workspace_screenshot(
    task_id: str,
    chat_id: str = "",
    max_age_minutes: int = 10
) -> str:
```

- **task_id** (required): Task identifier for screenshot matching
- **chat_id** (optional): Telegram chat ID for workspace detection
- **max_age_minutes** (optional): Maximum screenshot age to accept (default 10)

#### Screenshot Processing Pipeline

```python
# Workspace-aware screenshot discovery
if chat_id:
    validator = get_workspace_validator()
    workspace_name = validator.get_workspace_for_chat(chat_id)
    allowed_dirs = validator.get_allowed_directories(chat_id)
    working_dir = allowed_dirs[0] if allowed_dirs else os.getcwd()
else:
    working_dir = os.getcwd()

# Screenshot pattern matching
screenshot_dir = os.path.join(working_dir, "tmp", "ai_screenshots")
pattern = os.path.join(screenshot_dir, f"{task_id}_*.png")
matching_files = glob.glob(pattern)

# Time-based filtering
cutoff_time = time.time() - (max_age_minutes * 60)
recent_files = [f for f in matching_files if os.path.getmtime(f) > cutoff_time]

# AI analysis integration
screenshot_path = max(recent_files, key=os.path.getmtime)
analysis = analyze_image(
    screenshot_path, 
    question="What does this screenshot show? Focus on any UI issues, errors, or relevant details.",
    context=f"Screenshot captured for task: {task_id}"
)

# Telegram integration response
return f"TELEGRAM_IMAGE_GENERATED|{screenshot_path}|📸 **Task {task_id}**\n\n{analysis}"
```

### execute_bug_report_with_screenshot

**Purpose**: Complete bug report workflow with automated Playwright test generation and screenshot capture

#### Input Parameters
```python
def execute_bug_report_with_screenshot(
    task_description: str,
    notion_task_id: str,
    chat_id: str = ""
) -> str:
```

- **task_description** (required): Bug or issue description to investigate
- **notion_task_id** (required): Notion task ID for tracking and file naming
- **chat_id** (optional): Chat ID for workspace detection

#### Orchestrated Workflow

```python
# SDK-powered bug report workflow
from utilities.claude_sdk_wrapper import ClaudeCodeSDK, ClaudeTaskOptions

# Environment setup for Claude Code session
os.environ['NOTION_TASK_ID'] = notion_task_id

# Enhanced prompt with screenshot requirements
enhanced_prompt = f"""Create and run a Playwright test to investigate: {task_description}

Requirements:
1. Navigate to the relevant page/component
2. Capture a full-page screenshot showing the issue
3. Save screenshot to ./tmp/ai_screenshots/{notion_task_id}_{{timestamp}}.png
4. Output the exact text: SCREENSHOT_CAPTURED:{{path}}

The screenshot will be automatically retrieved and uploaded to Telegram with AI analysis.
"""

# SDK execution with workspace-aware options
options = ClaudeTaskOptions(
    max_turns=15,
    working_directory=target_directory,
    permission_mode=PermissionMode.ACCEPT_EDITS,
    allowed_tools=[AllowedTool.BASH, AllowedTool.EDITOR, AllowedTool.FILE_READER],
    chat_id=chat_id  # Enable workspace-aware enhancements
)

delegation_result = anyio_run_sync(
    ClaudeCodeSDK().execute_task_sync(enhanced_prompt, options)
)

# Screenshot retrieval and processing
if "SCREENSHOT_CAPTURED:" in delegation_result:
    screenshot_result = retrieve_workspace_screenshot(
        task_id=notion_task_id,
        chat_id=chat_id,
        max_age_minutes=5
    )
    
    if screenshot_result.startswith("TELEGRAM_IMAGE_GENERATED|"):
        return screenshot_result  # Triggers automatic upload
```

### trigger_error_recovery_analysis

**Purpose**: Manual error recovery analysis for system issues with comprehensive investigation

#### Input Parameters
```python
def trigger_error_recovery_analysis(error_description: str, chat_id: str = "") -> str:
```

- **error_description** (required): Description of error or issue to investigate
- **chat_id** (optional): Chat ID context for workspace-aware analysis

#### Comprehensive Error Investigation

```python
recovery_prompt = f"""Investigate and provide analysis for this reported system issue:

**Issue Description:**
{error_description}

**Chat Context:** {chat_id if chat_id else 'N/A'}

**Analysis Task:**
1. Search the codebase for potential causes of this issue
2. Check recent logs for related errors (logs/system.log, logs/tasks.log)
3. Look for error patterns in integrations/telegram/handlers.py
4. Check if utilities/auto_error_recovery.py is functioning correctly
5. Identify any missing variable definitions or import issues
6. Test for syntax/indentation problems
7. Provide specific recommendations for fixing
8. If appropriate, implement a fix immediately
9. Test the fix if implemented

**Focus Areas:**
- Error patterns in Telegram handlers
- Auto-recovery system effectiveness  
- Missing variable definitions (like is_priority issues)
- Timeout and connectivity issues
- System stability and reliability

Provide a comprehensive analysis and implement fixes where possible.
"""
```

## Documentation Tools

### read_documentation

**Purpose**: Access project documentation files with workspace-aware resolution

#### Input Parameters
```python
def read_documentation(filename: str, chat_id: str = "") -> str:
```

- **filename** (required): Documentation filename - "agent-architecture.md", "system-operations.md"
- **chat_id** (optional): Chat ID for workspace context

#### Documentation Access Pattern

```python
# Import with circular import protection
from tools.documentation_tool import read_documentation as read_doc

# Context enhancement with workspace awareness
if chat_id:
    # Workspace-specific context enhancement could be added here
    pass
    
return read_doc(filename)
```

### list_documentation_files

**Purpose**: Discovery of available project documentation

#### Implementation

```python
def list_documentation_files(chat_id: str = "") -> str:
    """Documentation discovery with workspace context"""
    
    from tools.documentation_tool import list_documentation_files as list_docs
    
    # Workspace-specific filtering could be added
    if chat_id:
        pass
        
    return list_docs()
```

## Security and Validation

### Directory Access Control System

```python
def validate_directory_access(chat_id: str, file_path: str) -> str | None:
    """Multi-layer security validation"""
    
    if not chat_id:
        return None  # Allow direct usage without chat context
    
    try:
        validator = get_workspace_validator()
        validator.validate_directory_access(chat_id, file_path)
        return None  # Access granted
    except WorkspaceAccessError as e:
        return f"❌ Directory Access Denied: {str(e)}"
    except Exception as e:
        return f"❌ Directory Validation Error: {str(e)}"
```

### Workspace Security Integration

All file system operations include security checks:

```python
# Pattern used across all file system tools
access_error = validate_directory_access(chat_id, file_path)
if access_error:
    return access_error

# Proceed with validated file access
```

### Multi-File Validation

```python
# Batch validation for multi-file operations
for file_path in file_paths:
    access_error = validate_directory_access(chat_id, file_path)
    if access_error:
        return access_error  # Fail fast on any access violation
```

## Performance Characteristics

### Tool Response Times

| Tool Category | Typical Response | Heavy Processing | Batch Operations |
|---------------|------------------|------------------|------------------|
| Test Parameter Generation | 200-800ms | 1-2s | 2-5s |
| Test Judging (Local) | 1-3s | 3-8s | 5-15s |
| Code Linting | 500ms-2s | 2-10s | 5-30s |
| Document Summarization | 1-3s | 3-8s | 10-60s |
| Image Analysis | 2-5s | 5-15s | 20-120s |
| Screenshot Workflows | 10-30s | 30-60s | N/A |

### Resource Management

```python
# Memory optimization for batch operations
BATCH_LIMITS = {
    "max_test_cases": 50,           # Test judging batch limit
    "max_files_lint": 100,          # Linting file limit  
    "max_documents": 20,            # Document batch limit
    "max_images": 10,               # Image analysis limit
    "screenshot_retention": 300,    # 5 minutes
}
```

### Concurrent Processing

```python
# Asynchronous batch processing where supported
async def process_batch_items(items, processor_func, max_concurrent=5):
    """Concurrent processing with rate limiting"""
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def process_item(item):
        async with semaphore:
            return await processor_func(item)
    
    results = await asyncio.gather(*[process_item(item) for item in items])
    return results
```

## Error Handling and Recovery

### Hierarchical Error Management

```python
class DevelopmentToolsErrorHandler:
    """Comprehensive error handling for development tools"""
    
    def handle_workspace_errors(self, error):
        """Handle workspace and security-related errors"""
        if isinstance(error, WorkspaceAccessError):
            return f"❌ Directory Access Denied: {str(error)}"
        return f"❌ Workspace Error: {str(error)}"
    
    def handle_tool_execution_errors(self, error, tool_name):
        """Handle tool-specific execution errors"""
        return f"❌ Error in {tool_name}: {str(error)}"
    
    def handle_batch_processing_errors(self, errors, successes):
        """Handle partial failures in batch operations"""
        if not successes:
            return "❌ All batch operations failed"
        return f"⚠️ Partial success: {len(successes)} succeeded, {len(errors)} failed"
```

### Context Enhancement Error Recovery

```python
def enhance_tool_prompt(base_prompt: str, chat_id: str = "") -> str:
    """Error-resilient prompt enhancement"""
    try:
        project_context = get_project_context(chat_id)
        
        return f"""{base_prompt}

## Current Project Context
{project_context}

Please consider this context when providing assistance."""
    
    except Exception as e:
        # Graceful degradation - return base prompt with error note
        return f"""{base_prompt}

## Project Context
Context unavailable due to error: {str(e)}"""
```

## Integration Requirements

### Environment Configuration

```bash
# Local Model Integration
OLLAMA_HOST=http://localhost:11434    # Local model server
OLLAMA_MODEL=gemma2:3b               # Default judging model

# AI Service Integration
OPENAI_API_KEY=sk-proj-...           # OpenAI for image analysis
ANTHROPIC_API_KEY=sk-ant-...         # Claude for document analysis

# Workspace Security
WORKSPACE_CONFIG_PATH=/path/to/workspaces.json
CHAT_WORKSPACE_MAPPINGS=/path/to/chat_mappings.json

# SDK Integration
CLAUDE_CODE_SDK_PATH=/path/to/claude_sdk.py
SCREENSHOT_TEMP_DIR=/path/to/screenshots/
```

### Tool Dependencies

```python
# Required tool imports with version specifications
REQUIRED_TOOLS = {
    "linting": ["ruff>=0.1.0", "black>=22.0", "mypy>=1.0"],
    "image_analysis": ["Pillow>=9.0", "opencv-python>=4.5"],
    "document_processing": ["beautifulsoup4>=4.10", "markdown>=3.3"],
    "local_models": ["ollama-python>=0.1.0"],
    "testing": ["playwright>=1.20", "pytest>=7.0"],
}
```

### Database Schema for Caching

```sql
-- Tool execution caching
CREATE TABLE tool_execution_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT NOT NULL,
    input_hash TEXT NOT NULL UNIQUE,  -- Hash of input parameters
    result_data TEXT NOT NULL,        -- JSON result
    execution_time REAL NOT NULL,     -- Execution time in seconds
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME,              -- Cache expiration
    
    INDEX idx_tool_hash (tool_name, input_hash),
    INDEX idx_expires (expires_at)
);

-- Workspace access logs
CREATE TABLE workspace_access_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    access_granted BOOLEAN NOT NULL,
    access_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_chat_access (chat_id, access_timestamp)
);
```

## Testing and Validation

### Comprehensive Test Suite

```python
class TestDevelopmentToolsIntegration:
    """Complete development tools testing"""
    
    async def test_security_validation(self):
        """Test workspace security validation"""
        # Test authorized access
        result = validate_directory_access("authorized_chat", "/allowed/path")
        assert result is None
        
        # Test unauthorized access
        result = validate_directory_access("unauthorized_chat", "/forbidden/path")
        assert result.startswith("❌ Directory Access Denied")
    
    async def test_linting_integration(self):
        """Test code linting with real projects"""
        result = await lint_python_code(
            project_path="test_project/",
            run_ruff=True,
            run_black=True,
            chat_id="test_chat"
        )
        parsed_result = json.loads(result)
        assert "summary" in parsed_result
        assert parsed_result["tools_executed"]
    
    async def test_batch_processing(self):
        """Test batch operations efficiency"""
        test_cases = [{"output": "test", "criteria": ["accuracy"], "context": {}}] * 10
        result = await batch_judge_responses(test_cases)
        judgments = json.loads(result)
        assert len(judgments) == 10
        
    async def test_screenshot_workflow(self):
        """Test end-to-end screenshot workflow"""
        result = await execute_bug_report_with_screenshot(
            task_description="Test UI bug investigation",
            notion_task_id="TEST-123",
            chat_id="test_chat"
        )
        assert "TELEGRAM_IMAGE_GENERATED" in result or "Task Completed" in result
```

### Performance Benchmarks

| Test Category | Performance Target | Success Criteria |
|---------------|-------------------|------------------|
| Linting (small project) | <2s | 95% under threshold |
| Linting (large project) | <10s | 90% under threshold |
| Document summarization | <5s | 98% under threshold |
| Image analysis | <8s | 95% under threshold |
| Test parameter generation | <1s | 99% under threshold |
| Security validation | <100ms | 100% under threshold |
| Screenshot workflow | <45s | 85% under threshold |

## Future Enhancements

### Planned Features

1. **Enhanced AI Integration**: Multi-model support with automatic fallback
2. **Advanced Security**: Fine-grained permission system with audit trails
3. **Performance Optimization**: Intelligent caching and batch processing
4. **Workflow Automation**: Custom workflow definition and execution
5. **Integration Expansion**: Additional tool integrations and API support

### Architectural Evolution

- **Microservice Architecture**: Split into specialized tool services
- **Event-Driven Processing**: Asynchronous tool execution with event streaming
- **ML-Powered Optimization**: Predictive caching and intelligent resource allocation
- **Cross-Platform Support**: Windows and Linux compatibility expansion

## Conclusion

The Development Tools MCP Server represents a comprehensive, security-first development support system that provides:

- **Complete Development Lifecycle Support**: From test generation to bug reporting with automated workflows
- **Security-First Architecture**: Comprehensive workspace validation and access control
- **Multi-Provider AI Integration**: Flexible AI service integration with local model support
- **Performance Excellence**: Optimized batch processing and intelligent caching
- **Extensible Design**: Modular architecture supporting easy integration of new tools and services

This server serves as both a production-ready development tool suite and an architectural reference for building secure, scalable development support systems.