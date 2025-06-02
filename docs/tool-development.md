# Tool Development Guide

## Overview

Tools extend agent capabilities by providing access to external services, APIs, and complex operations. This guide covers PydanticAI tool patterns, development practices, and integration strategies.

## PydanticAI Tool Patterns

### Function Tools with Context

Use `@agent.tool` for tools that need agent context:

```python
@agent.tool
def context_aware_tool(ctx: RunContext[ContextType], param: str) -> str:
    """Tool description that helps LLM understand when to use this tool."""
    # Access agent context
    user_data = ctx.deps.user_specific_data

    # Implement tool logic
    result = process_with_context(param, user_data)
    return result
```

### Plain Function Tools

Use `@agent.tool_plain` for stateless tools:

```python
@agent.tool_plain
def stateless_tool(param: str) -> str:
    """Simple tool that doesn't need agent context."""
    return external_api_call(param)
```

### Tool Registration at Agent Creation

```python
agent = Agent(
    'anthropic:claude-3-5-sonnet-20241022',
    deps_type=ContextType,
    tools=[external_tool_function]  # Register tools from other modules
)
```

## Current Tool Implementations

### Web Search Tool (`tools/search_tool.py`)

**Purpose**: Current information retrieval via Perplexity AI

```python
def search_web(query: str, max_results: int = 3) -> str:
    """Search web and return AI-synthesized answers using Perplexity."""
    # Implementation details
    return f"🔍 **{query}**\n\n{answer}"

# Integration with agent
@telegram_chat_agent.tool
def search_current_info(ctx: RunContext[TelegramChatContext], query: str) -> str:
    """Search for current information on the web using Perplexity AI."""
    return search_web(query)
```

**Key Features**:
- Perplexity API integration
- Concise messaging format (under 300 words)
- Error handling with graceful fallbacks
- Environment-based configuration

### Claude Code Tool (`tools/claude_code_tool.py`)

**Purpose**: Delegate complex coding tasks to new Claude sessions

```python
def spawn_claude_session(
    task_description: str,
    target_directory: str,
    specific_instructions: str | None = None
) -> str:
    """Spawn new Claude Code session for development tasks."""
    # Build comprehensive prompt
    # Execute Claude Code with proper working directory
    # Return execution results

# Integration with agent
@telegram_chat_agent.tool
def delegate_coding_task(
    ctx: RunContext[TelegramChatContext],
    task_description: str,
    target_directory: str,
    specific_instructions: str = ""
) -> str:
    """Spawn Claude Code session to handle complex coding tasks."""
    return spawn_claude_session(task_description, target_directory, specific_instructions)
```

**Key Features**:
- Subprocess management with timeout
- Working directory context
- Comprehensive prompt building
- Error handling and recovery

## Tool Development Best Practices

### 1. Clear Function Signatures

```python
def well_typed_tool(
    required_param: str,
    optional_param: int = 10,
    max_results: int = Field(default=3, ge=1, le=10)
) -> str:
    """
    Clear description of what this tool does.

    Args:
        required_param: Description of required parameter
        optional_param: Description with default value
        max_results: Constrained parameter with validation

    Returns:
        Description of return value format
    """
```

### 2. Comprehensive Error Handling

```python
def robust_tool(api_key: str, query: str) -> str:
    """Tool with proper error handling."""

    if not api_key:
        return "❌ Tool unavailable: Missing API key configuration."

    try:
        result = external_api_call(api_key, query)
        return f"✅ {result}"
    except APIRateLimitError:
        return "⏱️ Rate limit reached. Please try again later."
    except APIConnectionError:
        return "🔌 Connection error. Service may be temporarily unavailable."
    except Exception as e:
        return f"❌ Unexpected error: {str(e)}"
```

### 3. Environment Configuration

```python
import os
from dotenv import load_dotenv

load_dotenv()

def configured_tool(query: str) -> str:
    """Tool with environment-based configuration."""
    api_key = os.getenv("EXTERNAL_SERVICE_API_KEY")
    base_url = os.getenv("EXTERNAL_SERVICE_URL", "https://api.default.com")

    if not api_key:
        return "🔧 Configuration error: Missing API key."

    # Use configuration
    return call_external_service(base_url, api_key, query)
```

### 4. Response Formatting

```python
def well_formatted_tool(query: str) -> str:
    """Tool with consistent response formatting."""
    try:
        data = fetch_data(query)

        # Format for messaging platforms
        response = f"🔍 **{query}**\n\n"
        response += f"📊 **Results:**\n{format_data(data)}\n\n"
        response += f"⏰ *Updated: {datetime.now().strftime('%H:%M')}*"

        return response
    except Exception as e:
        return f"❌ **Error:** {str(e)}"
```

## Testing Tools

### Unit Testing with PydanticAI

```python
from pydantic_ai.models.test import TestModel, FunctionModel
import pytest

def test_search_tool():
    """Test search tool with minimal external mocking."""
    
    # Mock only external API - everything else should be real
    with patch('tools.search_tool.OpenAI') as mock_openai:
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="Test search result"))]
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        
        # Test the real function with real business logic
        result = search_web("test query")
        assert "🔍" in result
        assert "test query" in result

def test_agent_with_tool():
    """Test agent tool integration."""

    # Create test model
    test_model = TestModel()

    # Override agent model for testing
    test_agent = telegram_chat_agent.override(test_model)

    # Test agent with tool
    result = test_agent.run_sync(
        "Search for Python best practices",
        deps=TelegramChatContext(chat_id=123)
    )

    assert result.output
    # Verify tool was called (check test_model.call_count if needed)

def test_tool_error_handling():
    """Test tool error scenarios."""

    # Test with invalid configuration
    with patch.dict(os.environ, {}, clear=True):
        result = search_web("test")
        assert "unavailable" in result.lower()
```

## Testing Best Practices

### Mock Minimally - Test Real Functionality

**Golden Rule**: Only mock external services you don't control. Everything else should use real implementations.

**What to Mock:**
- ✅ External APIs (OpenAI, Perplexity, GitHub API)
- ✅ Network requests to third-party services  
- ✅ RunContext for agent integration tests

**What NOT to Mock:**
- ❌ Database connections (use real SQLite)
- ❌ File operations (use real files/temp directories)
- ❌ Internal business logic and utility functions
- ❌ Your own modules and functions

### Recommended Testing Pattern

```python
# ✅ Good: Minimal mocking, real functionality
@patch('tools.search_tool.OpenAI')  # Only mock external API
def test_search_tool_complete(self, mock_openai):
    # Mock external service
    mock_openai.return_value.chat.completions.create.return_value = mock_response
    
    # Use real business logic, real validation, real formatting
    result = search_web("test query")
    assert "🔍" in result
    
# ❌ Bad: Over-mocking, doesn't test real functionality  
@patch('tools.link_tool.get_database_connection')
@patch('tools.link_tool.validate_url')
@patch('tools.link_tool.datetime')
def test_link_tool_overmocked(self, mock_datetime, mock_validate, mock_db):
    # This doesn't test that your actual code works
    pass
```

### Integration Testing

```python
async def test_tool_integration():
    """Test tool integration in realistic scenarios."""

    context = TelegramChatContext(
        chat_id=12345,
        username="test_user",
        is_priority_question=False
    )

    # Test search tool integration
    search_response = await telegram_chat_agent.run(
        "What's the latest news about AI?",
        deps=context
    )

    assert search_response.output
    assert len(search_response.output) < 500  # Telegram compatibility
```

## Tool Integration Patterns

### Multi-Tool Workflows

```python
@agent.tool
def multi_step_tool(ctx: RunContext[ContextType], task: str) -> str:
    """Tool that orchestrates multiple operations."""

    # Step 1: Gather information
    info = search_web(f"information about {task}")

    # Step 2: Process with context
    context_data = ctx.deps.relevant_context

    # Step 3: Generate result
    result = process_information(info, context_data, task)

    return f"🔄 **Multi-step result for {task}:**\n\n{result}"
```

### Conditional Tool Usage

```python
@agent.tool
def smart_tool(ctx: RunContext[ContextType], query: str) -> str:
    """Tool that adapts behavior based on context."""

    if ctx.deps.is_priority_question:
        # Use detailed analysis for priority questions
        return detailed_analysis(query, ctx.deps.notion_data)
    else:
        # Use quick response for general questions
        return quick_response(query)
```

### Tool Composition

```python
# Base tool functions
def fetch_data(query: str) -> dict:
    """Fetch raw data from external service."""
    pass

def analyze_data(data: dict, context: str) -> str:
    """Analyze data with given context."""
    pass

# Composed agent tool
@agent.tool
def comprehensive_analysis(ctx: RunContext[ContextType], query: str) -> str:
    """Comprehensive analysis combining fetch and analysis."""

    # Compose multiple tool functions
    raw_data = fetch_data(query)
    analysis = analyze_data(raw_data, ctx.deps.analysis_context)

    return f"📊 **Analysis for {query}:**\n\n{analysis}"
```

## Tool Deployment Considerations

### Performance Optimization

```python
from functools import lru_cache
import asyncio

@lru_cache(maxsize=100)
def cached_expensive_operation(param: str) -> str:
    """Cache expensive operations."""
    return expensive_computation(param)

async def async_tool_operation(param: str) -> str:
    """Use async operations for better performance."""
    async with aiohttp.ClientSession() as session:
        result = await session.get(f"https://api.service.com/{param}")
        return await result.text()
```

### Resource Management

```python
import contextlib
from typing import Generator

@contextlib.contextmanager
def managed_resource() -> Generator[Resource, None, None]:
    """Properly manage external resources."""
    resource = acquire_resource()
    try:
        yield resource
    finally:
        release_resource(resource)

def resource_aware_tool(param: str) -> str:
    """Tool with proper resource management."""
    with managed_resource() as resource:
        return resource.process(param)
```

### Monitoring and Logging

```python
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

def monitored_tool(param: str) -> str:
    """Tool with monitoring and logging."""
    start_time = datetime.now()

    try:
        logger.info(f"Tool execution started: {param}")
        result = tool_implementation(param)

        duration = (datetime.now() - start_time).total_seconds()
        logger.info(f"Tool execution completed in {duration:.2f}s")

        return result
    except Exception as e:
        logger.error(f"Tool execution failed: {str(e)}")
        return f"❌ Tool error: {str(e)}"
```

## Gold Standard Implementation Patterns

### Exemplary Implementation: `image_analysis_tool.py` (Quality Score: 9.8/10)

The image analysis tool serves as the **architectural reference** for tool development, demonstrating:

#### 1. Sophisticated Error Categorization
```python
except FileNotFoundError:
    return "👁️ Image analysis error: Image file not found."
except OSError as e:
    return f"👁️ Image file error: Failed to read image file - {str(e)}"
except Exception as e:
    error_type = type(e).__name__
    if "API" in str(e) or "OpenAI" in str(e):
        return f"👁️ OpenAI API error: {str(e)}"
    if "base64" in str(e).lower() or "encoding" in str(e).lower():
        return f"👁️ Image encoding error: Failed to process image format - {str(e)}"
    return f"👁️ Image analysis error ({error_type}): {str(e)}"
```

#### 2. Pre-Validation for Efficiency
```python
# Validate format BEFORE file operations (efficiency optimization)
valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
file_extension = Path(image_path).suffix.lower()
if file_extension not in valid_extensions:
    return f"👁️ Image analysis error: Unsupported format '{file_extension}'. Supported: {', '.join(valid_extensions)}"
```

#### 3. Context-Aware Prompting
```python
# Different system prompts for different use cases
if question:
    system_content = (
        "You are an AI assistant with vision capabilities. "
        "Analyze the provided image and answer the specific question about it. "
        "Be detailed and accurate in your response. "
        "Keep responses under 400 words for messaging platforms."
    )
else:
    system_content = (
        "You are an AI assistant with vision capabilities. "
        "Describe what you see in the image in a natural, conversational way. "
        "Focus on the most interesting or relevant aspects. "
        "Keep responses under 300 words for messaging platforms."
    )
```

#### 4. Three-Layer Architecture Excellence
```python
# Agent Layer: Context extraction and delegation
def analyze_shared_image(ctx: RunContext[ValorContext], image_path: str, question: str = "") -> str:
    # Extract chat context for relevance
    chat_context = None
    if ctx.deps.chat_history:
        recent_messages = ctx.deps.chat_history[-3:]
        chat_context = " ".join([msg.get("content", "") for msg in recent_messages])
    
    # Delegate to implementation with context
    return analyze_image(image_path=image_path, question=question if question else None, context=chat_context)

# Implementation Layer: Core functionality with comprehensive validation
def analyze_image(image_path: str, question: str | None = None, context: str | None = None) -> str:
    # Input validation, format checking, API integration, error handling
    
# MCP Layer: Claude Code integration with chat_id context
@mcp.tool()
def analyze_shared_image(image_path: str, question: str = "", chat_id: str = "") -> str:
    # MCP-specific handling with context injection
```

#### 5. Comprehensive Testing Patterns
```python
# Perfect test coverage example (22/22 tests passing, 100% success rate)
class TestImageAnalysisImplementation:
    def test_analyze_image_format_validation(self):
        """Test format validation before file operations."""
        
    def test_analyze_image_successful_response(self):
        """Test with proper API mocking and response validation."""
        
    def test_analyze_image_api_error(self):
        """Test sophisticated error categorization."""

class TestAnalyzeSharedImageAgentTool:
    def test_agent_tool_context_extraction(self):
        """Test context-aware delegation patterns."""

class TestImageAnalysisIntegration:
    def test_interface_consistency_across_implementations(self):
        """Test three-layer architecture consistency."""
```

### Key Principles from Gold Standard

1. **Validation Before Operations**: Check inputs and formats before expensive operations
2. **Sophisticated Error Categorization**: Specific error types with user-friendly messages
3. **Context-Aware Design**: Different behavior based on use case and available context
4. **Perfect Test Coverage**: Comprehensive testing across all scenarios and layers
5. **Three-Layer Consistency**: Clean separation between agent, implementation, and MCP layers
6. **Performance Optimization**: Pre-validation, efficient error handling, appropriate timeouts
7. **User Experience Focus**: Clear emoji indicators, helpful error messages, conversation-friendly responses

### Quality Standards

Tools should achieve:
- ✅ **9.0+ Quality Score**: Excellent implementation meeting all production standards
- ✅ **95%+ Test Success Rate**: Comprehensive test coverage with minimal failures
- ✅ **Sub-second Validation**: Efficient input validation and error handling
- ✅ **Context Integration**: Proper use of chat history and conversation context
- ✅ **Error Categorization**: Specific error types helping users understand and resolve issues

Use `image_analysis_tool.py` as the architectural reference when developing new tools or improving existing ones.

---

This guide provides the foundation for developing robust, well-integrated tools that extend agent capabilities while maintaining system reliability and performance.
