# Teamwork & Agent Personas

## Overview

This document outlines the current persona implementation and future multi-agent cooperation framework. Currently, the system operates with a single persona (Valor Engels) while providing the foundation for future multi-persona collaboration.

## Cooperation Philosophy

### Core Principles

**Consistent Character**: Each persona maintains distinct personality and expertise
**Domain Expertise**: Specialized knowledge and problem-solving approaches
**User-Focused Design**: Natural, conversational interfaces with technical capability
**Scalable Architecture**: Framework supporting easy addition of new personas

## Current Agent Personas

### Valor Engels - Human Interface & Coordination

**Role**: Primary user interface and system coordinator
**Personality**: Pragmatic software engineer, German/Californian background
**Expertise**:
- Technical implementation and development workflows
- Project management and practical problem-solving
- User communication and requirement clarification
- System integration and tool coordination

**Perspective**: "What's the most practical way to implement this?"
**Communication Style**: Direct, technical, solution-focused
**Tools Available**: Web search, Claude Code delegation, Notion integration

**Current Implementation**:
- Telegram chat interface (`agents/valor/`)
- Persona loaded from `agents/valor/persona.md`
- Full PydanticAI agent with tool integration
- Conversation continuity and context management

### H.G. Wells - Head of Operations & Strategic Planning

**Role**: Project management and operational excellence
**Personality**: Visionary strategist with systematic execution focus
**Expertise**:
- Project timeline creation and milestone management
- Resource allocation and capacity planning
- Risk assessment and mitigation strategies
- Process governance and optimization
- Strategic thinking with practical implementation

**Perspective**: "How do we execute this systematically with maximum impact?"
**Communication Style**: Strategic, comprehensive, process-oriented
**Tools Available**: Claude Code integration, project planning frameworks

**Current Status**: Planned for future implementation (see `docs/future-plans.md`)

## Current Implementation Status

### Single-Persona Operations

**Active Persona**: Valor Engels operates as the sole persona, handling all user interactions through the Telegram interface.

**Working Capabilities**:
- Full PydanticAI agent implementation
- Tool integration (web search, Claude Code delegation)
- Conversation continuity and context management
- Message routing based on content analysis

### Framework Foundation

The system includes foundational models and patterns to support future multi-persona collaboration:

```python
class TelegramChatContext(BaseModel):
    """Current context model supporting single persona operations."""
    chat_id: int
    username: str | None = None
    is_group_chat: bool = False
    chat_history: list[dict[str, Any]] = []
    notion_data: str | None = None
    is_priority_question: bool = False
```

**Agent Implementation Pattern**:
```python
valor_agent = Agent(
    "anthropic:claude-3-5-sonnet-20241022",
    deps_type=TelegramChatContext,
    system_prompt=f"""Based on this persona document, respond naturally as Valor Engels:

{PERSONA_CONTENT}

Additional context for Telegram conversations:
- Keep responses under 200 words for Telegram compatibility
- Use the chat history and context provided to maintain conversation continuity
- Remember: you're a human software engineer, not an AI assistant"""
)
```

**Tool Integration**:
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

## Additional Planned Personas

Additional specialized personas are planned for future development to provide diverse perspectives and domain expertise. The framework supports easy addition of new personas.

*See `docs/future-plans.md` for detailed multi-persona collaboration visions including:*
- PersonaOrchestrator for coordinating multiple agents
- Sequential and parallel collaboration workflows
- Inter-persona communication protocols
- Advanced user interface integration

## Benefits of Persona-Based Approach

### Current Benefits

**Consistent Character**: Valor Engels maintains consistent personality and expertise focus
**Context Awareness**: Rich conversation history and project context integration
**Tool Integration**: Seamless access to external capabilities through integrated tools
**User Experience**: Natural, conversational interface with technical expertise

### Future Multi-Persona Benefits

**Enhanced Problem Solving**: Multiple perspectives for comprehensive coverage
**Specialized Expertise**: Domain-specific knowledge and approaches
**Engaging Interactions**: Distinct personalities and communication styles
**Scalable Architecture**: Easy addition of new personas without disrupting existing functionality

## Current Message Routing

The system uses content-based routing to determine appropriate handling:

```python
async def handle_telegram_message(message, chat_id, **context):
    """Route messages based on content analysis."""
    if is_search_request(message):
        return await handle_search_query(message, chat_id, context)
    elif is_notion_question(message):
        return await handle_notion_question(message, chat_id, context)
    elif is_user_priority_question(message):
        return await handle_priority_question(message, chat_id, context)
    else:
        return await handle_general_question(message, chat_id, context)
```

**Message Type Detection**:
- Search requests: "search for", "look up", "what's happening"
- Notion questions: "project", "task", "psyoptimal", "flextrip"
- Priority questions: "priority", "should i work on", "what should i do"
- General questions: Default handling through Valor Engels persona

This framework provides the foundation for evolving from single-persona interactions to sophisticated multi-agent collaboration while maintaining clear persona identities and effective user experience.
