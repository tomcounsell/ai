# Agent Architecture

## Overview

This AI system uses **PydanticAI agents** as the primary interface for LLM interactions, with specialized agents handling different domains through a Telegram interface. The architecture emphasizes type safety, tool integration, and conversation continuity.

## Core Architecture

### PydanticAI Foundation

Our agents are built on PydanticAI's `Agent` class, which provides:
- **Type Safety**: Full Pydantic validation and schema generation
- **Dependency Injection**: Context-specific dependencies via `deps_type`
- **Tool Integration**: Function tools with `@agent.tool` decorator
- **Message History**: Built-in conversation persistence
- **Model Agnostic**: Support for multiple LLM providers

```python
agent = Agent(
    'anthropic:claude-3-5-sonnet-20241022',
    deps_type=ContextType,
    system_prompt="Agent instructions..."
)

@agent.tool
def tool_function(ctx: RunContext[ContextType], param: str) -> str:
    """Tool description for LLM understanding."""
    return implementation(param)
```

### Current Agent System

#### Telegram Chat Agent (`agents/telegram_chat_agent.py`)
**Role**: Primary conversational interface with Valor Engels persona
- **Context**: `TelegramChatContext` with chat history and Notion data
- **Tools**: Web search, Claude Code delegation
- **Features**: Group/DM handling, conversation continuity, priority detection

#### Notion Scout Agent (`agents/notion_scout.py`)
**Role**: Project data queries and task management
- **Context**: Project-specific database access
- **Tools**: Notion API integration, Claude analysis
- **Features**: Project filtering, task prioritization, development recommendations

## Current Implementation Status

The system currently operates with two main agents:
1. **Telegram Chat Agent**: Production-ready with full PydanticAI integration
2. **Notion Scout Agent**: UV script implementation for project data queries

Additional specialized agents are planned for future development (see `docs/future-plans.md`).

## Agent Communication Patterns

### Context Dependency Injection

Each agent receives typed context through PydanticAI's dependency system:

```python
class AgentContext(BaseModel):
    user_id: int
    project_context: str | None = None
    conversation_history: list[dict] = []

@agent.tool
def context_aware_tool(ctx: RunContext[AgentContext], query: str) -> str:
    # Access context via ctx.deps
    user_context = ctx.deps.project_context
    return process_with_context(query, user_context)
```

### Message History Management

PydanticAI handles conversation persistence automatically:

```python
# Get conversation history
messages = result.all_messages()

# Continue conversation with history
result = await agent.run(
    new_message,
    message_history=previous_messages,
    deps=context
)
```

### Tool Orchestration

Agents automatically select and execute tools based on conversation context:

```python
@telegram_chat_agent.tool
def search_current_info(ctx: RunContext[TelegramChatContext], query: str) -> str:
    """Search for current information using Perplexity AI."""
    return search_web(query)

@telegram_chat_agent.tool
def delegate_coding_task(
    ctx: RunContext[TelegramChatContext],
    task_description: str,
    target_directory: str
) -> str:
    """Spawn Claude Code session for complex development tasks."""
    return spawn_claude_session(task_description, target_directory)
```

## System Orchestration

### Current Implementation: Direct Agent Access

```python
# Telegram message → Appropriate agent
async def handle_telegram_message(message, chat_id, **context):
    if is_priority_question(message):
        return await handle_user_priority_question(message, chat_id, **context)
    else:
        return await handle_general_question(message, chat_id, **context)
```

### System Orchestration

Current message routing uses simple keyword-based detection to route messages to appropriate handlers. Advanced multi-agent orchestration capabilities are planned for future development (see `docs/future-plans.md`).

## Agent Development Patterns

### Creating New Agents

1. **Define Context Model**
```python
class NewAgentContext(BaseModel):
    domain_specific_data: str
    user_preferences: dict = {}
```

2. **Create Agent with Tools**
```python
new_agent = Agent(
    'anthropic:claude-3-5-sonnet-20241022',
    deps_type=NewAgentContext,
    system_prompt="Domain-specific instructions..."
)

@new_agent.tool
def domain_tool(ctx: RunContext[NewAgentContext], input: str) -> str:
    """Domain-specific tool implementation."""
    return process_domain_task(input, ctx.deps)
```

3. **Integration with System**
```python
async def handle_domain_request(message: str, context: dict) -> str:
    agent_context = NewAgentContext(**context)
    result = await new_agent.run(message, deps=agent_context)
    return result.output
```

### Testing Agents

Use PydanticAI's testing framework:

```python
from pydantic_ai.models.test import TestModel

def test_agent_response():
    test_model = TestModel()
    agent_with_test_model = agent.override(test_model)

    result = agent_with_test_model.run_sync("test message")
    assert result.output == expected_response
```

## Benefits of This Architecture

### Type Safety
- Full Pydantic validation throughout the system
- Static type checking with mypy
- Runtime validation of agent inputs/outputs

### Scalability
- Easy addition of new agents via configuration
- Tool reuse across multiple agents
- Independent agent development and testing

### Reliability
- Built-in error handling and retries
- Conversation context preservation
- Comprehensive testing framework

### Maintainability
- Clear separation of concerns
- Standardized agent patterns
- Documentation-driven development

This architecture provides a foundation for scaling from single-agent interactions to sophisticated multi-agent workflows while maintaining type safety and operational reliability.
