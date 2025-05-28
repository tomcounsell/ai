# Agent Architecture

## Overview

This AI system uses **intelligent valor_agent architecture** that completely eliminates keyword triggers in favor of LLM-driven tool selection. The system routes ALL interactions through the valor_agent (agents/valor/) which uses natural language understanding to determine appropriate tool usage. The architecture emphasizes intelligent decision-making, context awareness, and zero rigid pattern matching.

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

### Valor Agent Intelligence System

#### Valor Agent (`agents/valor/`)
**Role**: SINGLE POINT of intelligent message routing - NO keyword triggers
- **Context**: `TelegramChatContext` with comprehensive conversation history
- **All Tools Integrated**:
  - `search_current_info` - Web search intelligence
  - `create_image` - AI image generation
  - `analyze_shared_image` - AI vision capabilities
  - `delegate_coding_task` - Claude Code session spawning
  - `save_link_for_later` - URL analysis and storage
  - `search_saved_links` - Link retrieval system
  - `query_notion_projects` - Project database queries
- **Intelligence Features**:
  - LLM-driven tool selection based on conversation context
  - Zero keyword matching - purely contextual understanding
  - Valor Engels persona maintained throughout all interactions
  - Complete conversation continuity across tool usage

#### Message Routing Logic (ELIMINATED KEYWORDS)
- **Ping Command**: ONLY system bypass for health checks
- **ALL Other Messages**: Route through valor_agent for intelligent processing
- **NO Keyword Detection**: Completely removed from system
- **NO Pattern Matching**: LLM intelligence determines tool usage

## Current Implementation Status

**PRODUCTION-READY VALOR AGENT SYSTEM:**
1. **Valor Agent (agents/valor/)**: Complete intelligent routing system with ALL tools integrated
2. **Notion Scout Agent**: UV script implementation for project data queries (supplementary)
3. **Telegram Handlers**: Streamlined to ONLY handle ping health checks and valor_agent routing
4. **Test Suite**: Comprehensive intelligence validation (zero keyword trigger tests remain)

**ARCHITECTURE BENEFITS ACHIEVED:**
- **Zero Maintenance**: No keyword lists to maintain or update
- **Natural Interaction**: Users communicate naturally without learning commands
- **Context Awareness**: Tool selection based on conversation flow and intent
- **Future-Proof**: LLM intelligence adapts to new tools automatically
- **Unified Experience**: Single agent handles ALL functionality consistently

Additional specialized agents are planned for future development (see `docs/plan/future-plans.md`).

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
@valor_agent.tool
def search_current_info(ctx: RunContext[TelegramChatContext], query: str) -> str:
    """Search for current information using Perplexity AI."""
    return search_web(query)

@valor_agent.tool
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

Current message routing uses simple keyword-based detection to route messages to appropriate handlers. Advanced multi-agent orchestration capabilities are planned for future development (see `docs/plan/future-plans.md`).

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
