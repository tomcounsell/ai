# Agents Directory

This directory contains PydanticAI conversational agents that provide intelligent AI interactions with tool integration capabilities.

## Overview

The agents in this directory implement the core conversational AI functionality using PydanticAI's structured agent framework. Each agent has a specific purpose and persona, with access to specialized tools for handling different types of requests.

## Agent Files

### Core Agents

#### `telegram_chat_agent.py`
**Main Telegram conversation agent with Valor Engels persona**

- **Purpose**: Primary AI agent for Telegram chat integration
- **Persona**: Valor Engels - German-Californian software engineer at Yudame
- **Key Features**:
  - Conversational AI with persistent context
  - Tool integration for web search, image generation/analysis, and coding
  - Message history management and conversation continuity
  - Special handling for priority questions with Notion data integration
  - Group chat and direct message support

- **Available Tools**:
  - `search_current_info()` - Web search using Perplexity AI
  - `create_image()` - Image generation with DALL-E 3
  - `analyze_shared_image()` - AI vision analysis of shared images
  - `delegate_coding_task()` - Spawn Claude Code sessions for complex tasks

- **Context Model**: `TelegramChatContext` with chat metadata and conversation state

#### `valor_agent.py`
**Standalone Valor Engels agent for general use**

- **Purpose**: Reusable Valor Engels agent for non-Telegram contexts
- **Features**:
  - Same persona as Telegram agent but context-agnostic
  - Web search and Claude Code delegation capabilities
  - Simplified context model for general conversations
  - Test framework for validation

- **Available Tools**:
  - `search_current_info()` - Web search functionality
  - `delegate_coding_task()` - Code delegation to Claude Code sessions

- **Context Model**: `ValorContext` with basic chat information

#### `notion_scout.py`
**Notion database query agent for project management**

- **Purpose**: AI-powered analysis of Notion database content
- **Key Features**:
  - Direct Notion API integration
  - Project-specific database filtering
  - Natural language questions about tasks and priorities
  - AI analysis of database entries using Claude
  - Command-line interface with project name/alias support

- **Main Functions**:
  - `query_notion_directly()` - Query and analyze Notion databases
  - `analyze_entries_with_claude()` - AI analysis of database content
  - `extract_property_value()` - Parse Notion property types
  - Project mapping and alias resolution

- **Usage Examples**:
  ```bash
  uv run notion_scout.py "What tasks need attention?"
  uv run notion_scout.py --project PsyOPTIMAL "Show current status"
  uv run notion_scout.py --project psy "What are my priorities?"
  ```

### Utility Modules

#### `message_history_converter.py`
**Message history integration for PydanticAI agents**

- **Purpose**: Bridge between Telegram chat history and PydanticAI conversation context
- **Key Functions**:
  - `merge_telegram_with_pydantic_history()` - Comprehensive history merging
  - `integrate_with_existing_telegram_chat()` - Legacy compatibility wrapper
  - `_remove_duplicate_messages()` - Deduplication logic

- **Features**:
  - Chronological message ordering
  - Duplicate detection and removal
  - Context size management
  - Multiple source integration (Telegram + PydanticAI)

#### `__init__.py`
**Package initialization and documentation**

- Contains package-level documentation
- Describes the purpose and structure of the agents package

## Architecture Patterns

### PydanticAI Agent Structure
All agents follow a consistent pattern:

1. **Context Model**: Pydantic BaseModel defining conversation context
2. **Agent Creation**: PydanticAI Agent with model, context type, and system prompt
3. **Tool Definitions**: Functions decorated with `@agent.tool` for capabilities
4. **Handler Functions**: High-level functions for external integration

### Tool Integration
Agents use PydanticAI's tool system for capabilities:

- **Automatic Selection**: LLM chooses appropriate tools based on context
- **Type Safety**: Full Pydantic validation for tool parameters
- **Error Handling**: Graceful degradation when tools are unavailable

### Context Management
- **Enhanced Messages**: Recent conversation context embedded in messages
- **State Persistence**: PydanticAI manages internal conversation state
- **External Integration**: Chat history from external systems (Telegram)

## Development Guidelines

### Creating New Agents
1. Define a Pydantic context model
2. Create PydanticAI Agent with appropriate system prompt
3. Add tools using `@agent.tool` decorator
4. Implement handler functions for external integration
5. Add comprehensive Google-style docstrings

### Tool Development
- Create tools as simple functions with proper type hints
- Use descriptive docstrings that help LLM understand when to use tools
- Handle errors gracefully and return user-friendly messages
- Follow the patterns established in existing tools

### Testing Strategy
- Use real integrations rather than mocks when possible
- Test main conversation flows and tool interactions
- Validate persona consistency and response quality
- Include examples in docstrings for documentation

## Usage Patterns

### Telegram Integration
```python
from agents.telegram_chat_agent import handle_telegram_message

response = await handle_telegram_message(
    message="What's the weather like?",
    chat_id=12345,
    username="user123",
    chat_history_obj=history_manager
)
```

### Standalone Usage
```python
from agents.valor_agent import run_valor_agent, ValorContext

context = ValorContext(chat_id=12345, username="user")
response = await run_valor_agent("How do I deploy a FastAPI app?", context)
```

### Notion Queries
```bash
# Command line usage
uv run agents/notion_scout.py "What tasks are ready for development?"
uv run agents/notion_scout.py --project FlexTrip "Show me current milestones"
```

## Configuration

### Environment Variables
- `ANTHROPIC_API_KEY` - For Claude AI conversations
- `OPENAI_API_KEY` - For image generation and analysis
- `PERPLEXITY_API_KEY` - For web search functionality
- `NOTION_API_KEY` - For Notion database access (notion_scout)

### Project Configuration
- Notion project mappings in `integrations/notion/database_mapping.json`
- Valor Engels persona definition in `integrations/persona.md`

## Dependencies

### Core Dependencies
- `pydantic-ai` - Agent framework and tool integration
- `anthropic` - Claude AI API access
- `openai` - DALL-E and GPT-4 Vision
- `python-dotenv` - Environment variable management

### Optional Dependencies
- `requests` - HTTP operations for Notion API
- `rich` - CLI formatting for notion_scout
- Tool-specific dependencies as imported

This directory represents the core conversational AI capabilities of the system, providing intelligent, tool-enabled agents for various use cases while maintaining consistent patterns and robust error handling.