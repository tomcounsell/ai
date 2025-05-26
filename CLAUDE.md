# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Dependency Management
```bash
# Compile dependencies from base requirements
uv pip compile requirements/base.txt -o requirements.txt

# Create virtual environment
uv venv

# Install dependencies
uv pip install -r requirements.txt
```

### Server Management
```bash
# Start FastAPI development server with hot reload
scripts/start.sh

# Stop server and cleanup processes
scripts/stop.sh

# Update MCP configuration from .env
scripts/update_mcp.sh
```

### Agent Execution
```bash
# Run UV script agents directly
uv run agents/notion_scout.py --project PsyOPTIMAL "What tasks are ready for dev?"
uv run agents/notion_scout.py --project FlexTrip "Show me project status"

# Available project aliases: psy, optimal, flex, trip
uv run agents/notion_scout.py --project psy "Quick status check"

# Test PydanticAI Telegram chat agent
uv run agents/telegram_chat_agent.py

# Run comprehensive agent demo
scripts/demo_agent.sh
```

### Testing
```bash
# Quick agent functionality test
python tests/test_agent_quick.py

# Comprehensive agent demo (runs in background)
scripts/demo_agent.sh

# Monitor demo progress
tail -f logs/agent_demo.log

# Run all tests
cd tests && python run_tests.py
```

## Architecture Overview

### PydanticAI Agent System
This codebase uses **PydanticAI agents** as the primary AI interaction pattern:

- **Telegram Chat Agent**: Main conversational AI with Valor Engels persona
- **Tool Integration**: Function-based tools with automatic LLM selection
- **Message History**: Conversation continuity through context injection
- **Type Safety**: Full Pydantic validation and schema generation

### Agent Architecture
```
/agents/                    # PydanticAI agents
  ├── telegram_chat_agent.py # Main Telegram conversation agent
  ├── valor_agent.py         # Standalone Valor agent example
  └── notion_scout.py        # Notion database query agent

/tools/                     # PydanticAI function tools
  ├── search_tool.py         # Web search using Perplexity AI
  └── __init__.py

/models/                    # Base models for tools
  └── tools.py              # Tool input/output base classes
```

### Tool Development Pattern
Create tools as simple functions with proper typing:

```python
def search_web(query: str, max_results: int = 3) -> str:
    """Search the web and return AI-synthesized answers."""
    # Tool implementation
    return search_result

@agent.tool
def my_tool(ctx: RunContext[ContextType], param: str) -> str:
    """Tool description for LLM to understand when to use it."""
    return my_tool_function(param)
```

### Integration System
External service integrations support the agent system:

- `/integrations/telegram/` - Telegram bot with PydanticAI agent integration
- `/integrations/notion/` - Project data queries with database mapping
- `/integrations/search/` - Web search (replaced by PydanticAI tool)

#### Current Integration Capabilities:
- **Telegram Bot**: Valor Engels persona with conversation continuity
- **Notion Queries**: Project status, task management, database insights
- **Web Search**: Perplexity AI integration through PydanticAI tool
- **Agent Routing**: LLM-driven tool selection and orchestration

### Server Architecture
- Minimal FastAPI server (`main.py`) with basic health endpoints
- Designed for extension, not as a monolithic application
- Server management scripts handle PID tracking and orphaned process cleanup
- Hot reload enabled for development

### Project Structure Philosophy
- `/agents/` - PydanticAI agents for AI interactions
- `/tools/` - Function tools for agent capabilities
- `/integrations/` - External service configurations and connections
- `/scripts/` - Development and automation scripts
- `/tests/` - Agent testing and validation

### MCP Integration
- Uses Model Context Protocol for Claude Code tool access
- Auto-generates `.mcp.json` configuration from environment variables
- Supports Notion API integration out of the box

### Environment Configuration
- `.env` file contains API keys (Anthropic, OpenAI, Notion, Telegram, Perplexity)
- `.env.example` provides template with proper placeholder formats
- Environment variables drive MCP server configuration

#### Required API Keys:
- `ANTHROPIC_API_KEY` - For Claude AI conversations and analysis
- `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` - For Telegram bot functionality
- `NOTION_API_KEY` - For project data integration
- `PERPLEXITY_API_KEY` - For intelligent web search

## Valor - AI Assistant Persona
**Valor Engels** refers to the PydanticAI agent implementation with a complete persona:
- Software engineer at Yudame with German/Californian background
- Handles technical questions, web search, and general conversation
- Maintains persistent conversation context through message history
- Responds to @mentions in groups and all messages in direct chats
- Smart tool usage for current information and technical assistance
- Context-aware responses using chat history and available data
- Technical persona focused on implementation details and requirements clarification

## Agent Development Patterns

### Current Architecture: PydanticAI Agents with Function Tools
The system uses PydanticAI's function tool approach for intelligent AI interactions:

**Agent Creation Pattern**:
```python
agent = Agent(
    'anthropic:claude-3-5-sonnet-20241022',
    deps_type=ContextType,
    system_prompt="Agent instructions..."
)

@agent.tool
def tool_function(ctx: RunContext[ContextType], param: str) -> str:
    """Tool description that helps LLM understand when to use this tool."""
    return tool_implementation(param)
```

**Tool Integration**:
- LLM automatically selects appropriate tools based on conversation context
- Tools are simple Python functions with proper type hints
- No manual routing or keyword detection needed
- Intelligent orchestration by the language model

**Message History**:
- Conversation context included in enhanced messages
- PydanticAI manages internal conversation state
- Chat history from Telegram integrated seamlessly
- Context awareness maintained across interactions

### Creating New Tools
1. Create tool function in `/tools/{tool_name}_tool.py`
2. Implement function with proper type hints and docstrings
3. Add to agent using `@agent.tool` decorator
4. LLM automatically uses tool based on context and capability
5. Add environment configuration to `.env.example` if needed
6. Test tool independently before agent integration

### Creating New Agents
1. Create agent file in `/agents/{agent_name}_agent.py`
2. Define context model using Pydantic BaseModel
3. Create agent with appropriate system prompt
4. Add tools using `@agent.tool` decorator
5. Implement handler functions for external integration
6. Test agent with various conversation scenarios

### Integration Mappings
Service integrations use mapping files in `/integrations/{service}/` to translate user-friendly names to service-specific identifiers.

### Testing Strategy
- Quick tests for basic functionality verification
- Comprehensive demos for full conversation testing
- Background execution for long-running test scenarios
- Log monitoring for test progress and debugging

## Important Notes
- All AI interactions use PydanticAI agents with function tools
- LLM automatically selects and orchestrates tool usage
- Conversation continuity maintained through context injection
- Type safety enforced throughout the system
- Tools are simple, testable Python functions
- Agent behavior validated through comprehensive test batteries