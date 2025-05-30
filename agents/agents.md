# Tools Directory

This directory contains PydanticAI function tools that provide specialized capabilities to AI agents, enabling them to perform complex tasks beyond conversation.

## Overview

The tools in this directory implement the function tool pattern for PydanticAI agents. Each tool is a simple Python function that can be automatically selected and executed by language models based on conversation context and capability descriptions. Tools provide the "hands and eyes" for AI agents, allowing them to interact with external services, analyze content, and delegate complex tasks.

## Tool Categories

### Web and Search Tools

#### `search_tool.py`
**Web search functionality using Perplexity AI**

- **Purpose**: Provides current web information to agents
- **Main Function**: `search_web(query, max_results=3)`
- **API**: Perplexity AI (sonar-pro model)
- **Features**:
  - AI-synthesized answers from current web content
  - Optimized for messaging platforms (300-word responses)
  - Error handling for missing API keys
  - Async compatibility wrapper

- **Usage Example**:
  ```python
  result = search_web("latest Python 3.12 features")
  # Returns: "🔍 **latest Python 3.12 features**\n\nPython 3.12 includes..."
  ```

### Visual Content Tools

#### `image_generation_tool.py`
**AI image creation using DALL-E 3**

- **Purpose**: Generate custom images from text descriptions
- **Main Function**: `generate_image(prompt, size, quality, style, save_directory)`
- **API**: OpenAI DALL-E 3
- **Features**:
  - Multiple size options (1024x1024, 1792x1024, 1024x1792)
  - Quality settings (standard, hd)
  - Style options (natural, vivid)
  - Local file saving with sanitized filenames
  - Feedback formatting for conversational use

- **Functions**:
  - `generate_image()` - Core image generation
  - `generate_image_async()` - Async wrapper
  - `create_image_with_feedback()` - Returns path + user message

#### `image_analysis_tool.py`
**AI vision analysis using GPT-4 Vision**

- **Purpose**: Analyze images shared in conversations
- **Main Function**: `analyze_image(image_path, question, context)`
- **API**: OpenAI GPT-4 Vision (gpt-4o model)
- **Features**:
  - Image description and content analysis
  - Question-based analysis
  - OCR capabilities
  - Context-aware responses
  - Support for common image formats

- **Use Cases**:
  - Photo description and analysis
  - Text extraction from images (OCR)
  - Object and scene recognition
  - Question answering about image content

### Development Tools

#### `claude_code_tool.py`
**Code delegation to Claude Code sessions**

- **Purpose**: Handle complex coding tasks requiring specialized tools
- **Main Functions**:
  - `execute_claude_code()` - Low-level Claude execution
  - `spawn_claude_session()` - High-level task delegation
- **Features**:
  - Directory context and validation
  - Tool permission management
  - Timeout handling
  - Comprehensive prompt formatting
  - Error handling and reporting

- **Use Cases**:
  - Feature development across multiple files
  - Git workflows and repository management
  - Complex refactoring tasks
  - Project scaffolding and setup

#### `documentation_tool.py`
**Local documentation access using FileReader utility**

- **Purpose**: Provide agents with access to project documentation
- **Main Functions**:
  - `read_documentation()` - Read specific documentation files
  - `list_documentation_files()` - Discover available documentation
  - `read_documentation_structured()` - Structured request/response
- **Features**:
  - Safe file reading with error handling
  - Documentation discovery
  - Encoding support
  - Formatted responses for agent consumption

- **Models**:
  - `DocumentationRequest` - Structured request model
  - `DocumentationResponse` - Structured response model

### Link and Content Analysis

#### `link_analysis_tool.py`
**URL analysis and storage using Perplexity AI**

- **Purpose**: Analyze shared links and maintain link archives
- **Key Functions**:
  - `analyze_url_content()` - Extract structured data from URLs
  - `store_link_with_analysis()` - Save links with AI analysis
  - `search_stored_links()` - Search previously analyzed links
  - `extract_urls()` - Find URLs in text
  - `is_url_only_message()` - Detect URL-only messages

- **Features**:
  - Automatic content analysis (title, topic, reasons to care)
  - Persistent storage in docs/links.json
  - Automatic git commits for link data
  - Search and retrieval functionality
  - URL validation and sanitization

### Project Management Tools

#### `notion_tool.py`
**Workspace-based Notion database queries with AI analysis**

- **Purpose**: Query Notion project databases and provide intelligent task analysis
- **Main Functions**:
  - `query_notion_workspace()` - Core workspace querying function
  - `query_psyoptimal_workspace()` - PsyOPTIMAL-specific wrapper
- **APIs**: Notion API + Anthropic Claude for analysis
- **Features**:
  - Workspace-based configuration with hardcoded database mappings
  - Complete property extraction for all Notion field types
  - AI-powered analysis of project data for task recommendations
  - Support for multiple workspace configurations
  - Intelligent priority and status analysis

- **Configuration**:
  - Workspace settings dictionary with database IDs
  - Alias support for flexible workspace naming
  - Environment-based API key validation

- **Use Cases**:
  - Project status queries
  - Task priority analysis
  - Development workload assessment
  - Milestone and deadline tracking

### Infrastructure Tools

#### `models.py`
**Base models for tool infrastructure**

- **Purpose**: Provide common models for tool execution tracking
- **Models**:
  - `ToolStatus` - Enumeration of tool operational states
  - `ToolResult` - Standardized result model for tool executions
- **Features**:
  - Execution time tracking
  - Success/failure reporting
  - Metadata support
  - Timestamp recording

## Tool Development Patterns

### Function Tool Pattern
All tools follow the PydanticAI function tool pattern:

```python
def tool_function(param1: str, param2: int = 10) -> str:
    """Tool description that helps LLM understand when to use this tool.

    Detailed description of what the tool does and when to use it.

    Args:
        param1: Description of first parameter.
        param2: Description of second parameter with default.

    Returns:
        str: Description of return value.

    Example:
        >>> tool_function("example", 5)
        'Expected output format'
    """
    # Tool implementation
    return result
```

### Agent Integration
Tools are integrated with agents using decorators:

```python
@agent.tool
def agent_tool_wrapper(ctx: RunContext[ContextType], param: str) -> str:
    """Agent-specific wrapper for the tool."""
    return tool_function(param)
```

### Error Handling
All tools implement consistent error handling:

- Graceful degradation when APIs are unavailable
- User-friendly error messages
- Environment validation (API keys, file paths)
- Exception catching and reporting

### Environment Configuration
Tools require various API keys and configuration:

- `OPENAI_API_KEY` - Image generation and analysis
- `PERPLEXITY_API_KEY` - Web search and link analysis
- `ANTHROPIC_API_KEY` - Claude Code delegation and Notion analysis
- `NOTION_API_KEY` - Notion workspace database access
- File system access for local operations

## Usage Examples

### Web Search
```python
from tools.search_tool import search_web

result = search_web("latest developments in AI")
print(result)  # Formatted search results
```

### Image Generation
```python
from tools.image_generation_tool import generate_image

image_path = generate_image(
    "a sunset over mountains",
    size="1792x1024",
    quality="hd",
    style="vivid"
)
```

### Image Analysis
```python
from tools.image_analysis_tool import analyze_image

analysis = analyze_image(
    "/path/to/image.jpg",
    question="What objects are in this image?",
    context="User asked about the contents"
)
```

### Code Delegation
```python
from tools.claude_code_tool import spawn_claude_session

result = spawn_claude_session(
    task_description="Create a FastAPI application",
    target_directory="/home/user/projects",
    specific_instructions="Include authentication and tests"
)
```

### Documentation Access
```python
from tools.documentation_tool import read_documentation, list_documentation_files

# List available docs
docs = list_documentation_files()

# Read specific documentation
content = read_documentation("agent-architecture.md")
```

### Link Analysis
```python
from tools.link_analysis_tool import analyze_url_content, store_link_with_analysis

# Analyze a URL
analysis = analyze_url_content("https://example.com/article")

# Store with analysis
success = store_link_with_analysis("https://example.com/article")
```

### Notion Workspace Queries
```python
from tools.notion_tool import query_notion_workspace, query_psyoptimal_workspace

# Query specific workspace
result = query_notion_workspace("PsyOPTIMAL", "What tasks are ready for dev?")

# Query PsyOPTIMAL workspace directly
result = query_psyoptimal_workspace("Show me high priority tasks")
```

## Tool Architecture Benefits

### Automatic Selection
- LLMs choose appropriate tools based on conversation context
- No manual routing or keyword detection required
- Intelligent orchestration of multiple tools

### Type Safety
- Full Pydantic validation for tool parameters
- Clear interfaces and error handling
- Documentation-driven development

### Modularity
- Tools are independent and reusable
- Easy to test in isolation
- Simple integration with new agents

### Extensibility
- Easy to add new tools following established patterns
- Consistent error handling and response formatting
- Environment-based configuration

## Development Guidelines

### Creating New Tools
1. Implement core functionality as a simple Python function
2. Add comprehensive Google-style docstrings
3. Include type hints for all parameters and return values
4. Implement error handling and validation
5. Add usage examples in docstrings
6. Create async wrappers if needed for compatibility
7. Test the tool independently before agent integration

### Tool Function Design
- Keep functions focused on a single capability
- Use descriptive names that indicate functionality
- Return user-friendly strings formatted for conversation
- Handle missing dependencies gracefully
- Include helpful error messages

### Documentation Standards
- Use Google-style docstrings with examples
- Document all parameters and return values
- Include usage examples and common patterns
- Explain when to use each tool
- Note any required environment variables

This directory provides the core capabilities that make AI agents useful beyond conversation, enabling them to interact with the real world through web search, content creation, code generation, and data analysis.
