# PydanticAI Integration Guide

This document provides best practices and conventions for integrating PydanticAI into Django applications, based on lessons learned from the Cuttlefish project.

## Table of Contents
- [Overview](#overview)
- [Architecture Principles](#architecture-principles)
- [Model Naming Conventions](#model-naming-conventions)
- [Agent Configuration](#agent-configuration)
- [Django Integration Patterns](#django-integration-patterns)
- [Tool Development](#tool-development)
- [Security Guidelines](#security-guidelines)
- [Testing Strategies](#testing-strategies)
- [Common Pitfalls](#common-pitfalls)

---

## Overview

PydanticAI is a Python agent framework built on Pydantic for type-safe AI agent development. When integrating with Django, special care must be taken to avoid naming conflicts, maintain clear separation of concerns, and handle async/sync boundaries properly.

**Requirements:**
- PydanticAI v1.0+
- Python 3.11+
- OpenAI SDK v1.107.2+
- Django 4.0+ (for async view support)

### Key Integration Points

```
Django Models (Database) ↔ Adapter Layer ↔ PydanticAI Models (In-Memory)
                                ↓
                        PydanticAI Agent
                                ↓
                        LLM Provider (OpenAI, Anthropic, etc.)
```

---

## Architecture Principles

### 1. Clear Separation of Concerns

**Organize code into distinct layers:**

```
apps/ai/
├── agent/          # PydanticAI agent logic (in-memory)
│   ├── chat.py     # Agent definitions and orchestration
│   └── tools.py    # Tool implementations
├── adapters/       # Django ↔ PydanticAI conversion
│   └── chat.py     # Convert between model types
├── llm/            # LLM provider configuration
│   └── providers.py
├── models/         # Django ORM models (database)
│   └── chat.py
└── views/          # Django views that use the agent
    └── chat.py
```

### 2. Dependency Flow

```
Django Views → Adapters → PydanticAI Agents → LLM Providers
     ↓              ↓
Django Models  PydanticAI Models
```

**Rules:**
- Django views should **never** directly manipulate PydanticAI models
- Use adapter pattern for all conversions between Django and PydanticAI models
- Keep PydanticAI logic independent of Django for testability

### 3. Async-First Architecture

PydanticAI is async-native. Choose one of these approaches:

**Option A: Fully Async (Recommended)**
```python
# Django 4.1+ supports async views
class ChatView(View):
    async def post(self, request):
        result = await agent.run(message)
        await sync_to_async(chat_message.save)()
        return JsonResponse({"response": result.output})
```

**Option B: Sync Wrapper with Django Utilities**
```python
from asgiref.sync import async_to_sync

def process_message_sync(message: str) -> str:
    return async_to_sync(process_message_async)(message)
```

**❌ Avoid: Custom Event Loop Handling**
```python
# DON'T DO THIS - fragile and may break in production
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
```

---

## Model Naming Conventions

### The Problem: Name Collisions

Django and PydanticAI both use models. Without clear naming, you end up with:
- `from apps.ai.models import ChatSession` (Django model)
- `from apps.ai.agent.chat import ChatSession` (Pydantic model)

This causes confusion and bugs.

### Solution: Prefix PydanticAI Models

**✅ Good:**
```python
# apps/ai/agent/chat.py
from pydantic import BaseModel

class AgentChatSession(BaseModel):
    """In-memory chat session for PydanticAI agent."""
    session_id: str
    user_id: int | None
    messages: list["AgentChatMessage"]

class AgentChatMessage(BaseModel):
    """In-memory message for PydanticAI agent."""
    role: str
    content: str
    timestamp: datetime
```

**✅ Also Good:**
```python
# Alternative: Use a submodule
# apps/ai/agent/schemas.py
class ChatSessionSchema(BaseModel):
    """Schema for in-memory chat session."""
    ...
```

**❌ Bad:**
```python
# apps/ai/agent/chat.py - CONFUSING!
class ChatSession(BaseModel):  # Clashes with Django model name
    ...
```

### Import Clarity

With proper naming, imports are self-documenting:

```python
# apps/ai/views/chat.py
from apps.ai.models import ChatSession  # Django ORM model
from apps.ai.agent.chat import AgentChatSession  # PydanticAI model
from apps.ai.adapters.chat import django_to_agent  # Converter

# Clear which is which
db_session: ChatSession = ChatSession.objects.get(id=session_id)
agent_session: AgentChatSession = django_to_agent(db_session)
```

---

## Agent Configuration

### Model Selection

**❌ Don't hardcode model names:**
```python
# apps/ai/agent/chat.py - BAD
chat_agent = Agent(
    "openai:gpt-4.1",  # Hardcoded
    ...
)
```

**✅ Use provider configuration:**
```python
# apps/ai/llm/providers.py
def get_default_model() -> OpenAIModel:
    """Get default model from settings/env."""
    model_name = settings.DEFAULT_LLM_MODEL
    return OpenAIModel(model_name)

# apps/ai/agent/chat.py - GOOD
from apps.ai.llm.providers import get_default_model

chat_agent = Agent(
    get_default_model(),
    deps_type=ChatDependencies,
    tools=[...],
)
```

### System Prompts

**❌ Don't define prompts twice:**
```python
# BAD - decorator overrides constructor parameter
chat_agent = Agent(
    model,
    system_prompt="You are helpful",  # This is ignored!
)

@chat_agent.system_prompt
async def get_prompt(ctx):
    return "You are very helpful"  # This is what's used
```

**✅ Choose one approach:**
```python
# Option 1: Static prompt
chat_agent = Agent(
    model,
    system_prompt="You are a helpful assistant with access to tools.",
)

# Option 2: Dynamic prompt (recommended)
chat_agent = Agent(model, deps_type=ChatDeps, tools=[...])

@chat_agent.system_prompt
async def dynamic_prompt(ctx: RunContext[ChatDeps]) -> str:
    base = "You are a helpful assistant."
    if ctx.deps.user_id:
        base += f" You're helping user {ctx.deps.user_id}."
    return base
```

### Dependencies Pattern

Use `deps_type` for context that agents need:

```python
from dataclasses import dataclass
from pydantic_ai import Agent, RunContext

@dataclass
class ChatDependencies:
    """Context for chat agent execution."""
    user_id: int | None = None
    session_id: str | None = None
    organization_id: int | None = None
    custom_context: dict = field(default_factory=dict)

agent = Agent(
    model,
    deps_type=ChatDependencies,
    tools=[search, calculate],
)

# Tools can access dependencies
@agent.tool
async def get_user_data(ctx: RunContext[ChatDependencies]) -> dict:
    if ctx.deps.user_id:
        return fetch_user_data(ctx.deps.user_id)
    return {}
```

---

## Django Integration Patterns

### Adapter Pattern

Create explicit converters between Django and PydanticAI models:

```python
# apps/ai/adapters/chat.py
from apps.ai.models import ChatSession, ChatMessage
from apps.ai.agent.chat import AgentChatSession, AgentChatMessage

def django_to_agent_session(session: ChatSession) -> AgentChatSession:
    """Convert Django ChatSession to AgentChatSession."""
    return AgentChatSession(
        session_id=str(session.id),
        user_id=session.user.id if session.user else None,
        messages=[
            AgentChatMessage(
                role=msg.role,
                content=msg.content,
                timestamp=msg.created_at,
                metadata=msg.metadata,
            )
            for msg in session.messages.filter(is_processed=True)
        ],
    )

def agent_to_django_message(
    agent_msg: AgentChatMessage,
    django_session: ChatSession
) -> ChatMessage:
    """Convert AgentChatMessage to Django ChatMessage."""
    return ChatMessage.objects.create(
        session=django_session,
        role=agent_msg.role,
        content=agent_msg.content,
        metadata=agent_msg.metadata,
    )
```

### View Integration

**Use adapters in views:**
```python
# apps/ai/views/chat.py
from apps.ai.adapters.chat import django_to_agent_session
from apps.ai.agent.chat import chat_agent, ChatDependencies

class ChatSendMessageView(View):
    async def post(self, request):
        # Get Django model
        db_session = await sync_to_async(
            ChatSession.objects.get
        )(id=request.session["chat_session_id"])

        # Convert to agent model
        agent_session = django_to_agent_session(db_session)

        # Prepare dependencies
        deps = ChatDependencies(
            user_id=request.user.id if request.user.is_authenticated else None,
            session_id=str(db_session.id),
        )

        # Run agent
        result = await chat_agent.run(
            message,
            deps=deps,
            message_history=agent_session.get_history(),
        )

        # Save to Django model
        await sync_to_async(ChatMessage.objects.create)(
            session=db_session,
            role="assistant",
            content=result.output,
        )

        return JsonResponse({"response": result.output})
```

---

## Tool Development

### Standalone AI Tools (Named AI Tools)

For self-contained AI processing tasks that don't need Django model interaction, use the **Named AI Tools** pattern: one file, one function, one Agent. This is simpler than the full adapter pattern and appropriate for tasks like text analysis, content generation, and data transformation.

See [Named AI Tools](AI_CONVENTIONS.md#named-ai-tools) for the full convention and examples.

### Agent Tool Organization

For tools that are registered on a PydanticAI Agent via `@agent.tool`, keep them focused and in separate files:

```
apps/ai/agent/tools/
├── __init__.py
├── code_execution.py    # Python execution tool
├── web_search.py        # Web search tool
├── database.py          # Database query tools
└── document.py          # Document processing tools
```

### Tool Implementation Pattern

```python
# apps/ai/agent/tools/code_execution.py
from pydantic_ai import RunContext

async def execute_python(
    ctx: RunContext[ChatDependencies],
    code: str
) -> dict[str, Any]:
    """
    Execute Python code in a sandboxed environment.

    Args:
        ctx: Run context with dependencies
        code: Python code to execute

    Returns:
        Dictionary with execution results
    """
    # Validate
    if len(code) > 10000:
        return {"error": "Code too long (max 10000 chars)"}

    # Execute with restricted environment
    result = await execute_in_sandbox(code)

    # Log for auditing
    await log_tool_use(
        user_id=ctx.deps.user_id,
        tool="execute_python",
        input_size=len(code),
    )

    return result
```

### Consolidate Tool Implementations

**❌ Don't maintain multiple versions:**
```
apps/ai/agent/tools.py          # Advanced implementation
apps/ai/agent/simple_tools.py   # Basic implementation - WHY?
```

**✅ Keep one canonical implementation:**
```python
# apps/ai/agent/tools/python_execution.py
# Single, well-tested implementation
```

If you need simpler versions for testing:
```python
# tests/fixtures/mock_tools.py
# Simplified versions for unit tests only
```

---

## Security Guidelines

### Code Execution Sandboxing

**❌ Never use unrestricted exec():**
```python
# DANGEROUS!
async def run_python(code: str):
    exec(code)  # Can do anything!
```

**✅ Restrict execution environment:**
```python
from RestrictedPython import compile_restricted
import io
import sys

SAFE_BUILTINS = {
    'abs', 'all', 'any', 'bool', 'dict', 'float', 'int',
    'len', 'list', 'max', 'min', 'print', 'range', 'str',
    'sum', 'tuple', 'enumerate', 'zip',
}

BLOCKED_IMPORTS = {'os', 'sys', 'subprocess', 'socket', '__import__'}

async def run_python(code: str) -> dict:
    """Execute Python in restricted environment."""

    # Compile with restrictions
    byte_code = compile_restricted(code, '<string>', 'exec')

    # Create restricted globals
    safe_globals = {
        '__builtins__': {
            name: __builtins__[name]
            for name in SAFE_BUILTINS
        }
    }

    # Execute with timeout
    try:
        exec(byte_code, safe_globals, {})
    except Exception as e:
        return {"error": str(e)}

    return {"success": True}
```

**Best approach: Use container-based sandboxing:**
```python
# Use Docker or similar for true isolation
import docker

async def run_python(code: str) -> dict:
    client = docker.from_env()
    result = client.containers.run(
        "python:3.11-slim",
        f"python -c '{code}'",
        mem_limit="128m",
        cpu_period=100000,
        cpu_quota=50000,  # 50% of one CPU
        timeout=5,
        remove=True,
    )
    return {"output": result.decode()}
```

### API Key Management

**❌ Don't mutate os.environ:**
```python
# BAD - affects entire process
def get_openai_model(api_key: str = None):
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key  # DANGEROUS!
    return OpenAIModel("gpt-4")
```

**✅ Pass API keys explicitly:**
```python
# GOOD - isolated to this call
def get_openai_model(
    model_name: str = "gpt-4o",
    api_key: str | None = None
) -> OpenAIModel:
    key = api_key or settings.OPENAI_API_KEY
    if not key:
        raise ValueError("OpenAI API key required")

    # Pass directly to model
    return OpenAIModel(model_name, api_key=key)
```

### User Input Validation

**Always validate tool inputs:**
```python
from pydantic import BaseModel, Field, validator

class CodeExecutionInput(BaseModel):
    """Validated input for code execution."""
    code: str = Field(..., max_length=10000)
    timeout: int = Field(5, ge=1, le=30)

    @validator('code')
    def validate_code(cls, v):
        # Block dangerous patterns
        dangerous = ['__import__', 'eval', 'exec', 'open']
        if any(pattern in v for pattern in dangerous):
            raise ValueError("Code contains blocked patterns")
        return v

async def execute_code(ctx: RunContext, input: CodeExecutionInput):
    # Input is pre-validated by Pydantic
    return await sandbox_execute(input.code, input.timeout)
```

---

## Testing Strategies

### Unit Testing Agents

**Test agents in isolation:**
```python
# tests/ai/test_chat_agent.py
import pytest
from apps.ai.agent.chat import chat_agent, ChatDependencies, AgentChatSession

@pytest.mark.asyncio
async def test_chat_agent_basic_response():
    """Test agent handles basic conversation."""
    deps = ChatDependencies(user_id=1, session_id="test-123")

    result = await chat_agent.run(
        "What is 2+2?",
        deps=deps,
    )

    assert result.output is not None
    assert "4" in result.output.lower()

@pytest.mark.asyncio
async def test_chat_agent_uses_context():
    """Test agent uses dependencies context."""
    deps = ChatDependencies(
        user_id=42,
        custom_context={"user_name": "Alice"}
    )

    result = await chat_agent.run(
        "What's my user ID?",
        deps=deps,
    )

    assert "42" in result.output
```

### Mocking LLM Responses

**Use test mode or mocks:**
```python
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_chat_agent_mocked():
    """Test agent logic without calling LLM."""
    with patch('apps.ai.agent.chat.chat_agent.run') as mock_run:
        # Mock the result
        mock_result = AsyncMock()
        mock_result.output = "Mocked response"
        mock_run.return_value = mock_result

        # Test view logic
        response = await process_chat_message("Hello")

        assert response == "Mocked response"
        mock_run.assert_called_once()
```

### Integration Testing

**Test full Django → PydanticAI flow:**
```python
@pytest.mark.django_db
@pytest.mark.asyncio
class TestChatIntegration:
    async def test_full_chat_flow(self):
        """Test complete chat flow from Django to PydanticAI."""
        # Create Django models
        user = await sync_to_async(User.objects.create)(
            username="test_user"
        )
        session = await sync_to_async(ChatSession.objects.create)(
            user=user
        )

        # Send message through view
        result = await process_chat_message_view(
            session_id=session.id,
            message="Hello",
        )

        # Verify Django models updated
        await sync_to_async(session.refresh_from_db)()
        assert session.messages.count() == 2  # user + assistant

        # Verify response
        assert result["response"] is not None
```

---

## Common Pitfalls

### 1. ❌ Duplicate Model Names

**Problem:**
```python
# apps/ai/models/chat.py
class ChatSession(models.Model):
    ...

# apps/ai/agent/chat.py
class ChatSession(BaseModel):  # Name clash!
    ...
```

**Solution:** Prefix PydanticAI models with `Agent` or move to `schemas.py`

### 2. ❌ Dead Code from Override

**Problem:**
```python
agent = Agent(
    model,
    system_prompt="This is ignored!",  # Dead code
)

@agent.system_prompt  # This overrides the constructor
async def prompt(ctx):
    return "Actual prompt"
```

**Solution:** Use only decorator OR constructor, not both

### 3. ❌ Fragile Async Wrappers

**Problem:**
```python
def sync_wrapper(message):
    try:
        loop = asyncio.get_event_loop()  # May fail in ASGI
    except:
        loop = asyncio.new_event_loop()  # Creates issues
    return loop.run_until_complete(async_func(message))
```

**Solution:** Use Django's `async_to_sync` or go fully async

### 4. ❌ Result Attribute Guessing

**Problem:**
```python
# Defensive programming - unnecessary complexity
if hasattr(result, "output"):
    response = result.output
elif hasattr(result, "text"):
    response = result.text
else:
    response = str(result)
```

**Solution:** Use `result.output` directly
```python
response = result.output  # Standard PydanticAI attribute - always present
```

### 5. ❌ Manual History Management

**Problem:**
```python
# Manually building message list
messages = []
for msg in session.messages.all():
    messages.append({"role": msg.role, "content": msg.content})

result = await agent.run(message, message_history=messages)
```

**Solution:** Use adapter pattern and agent models
```python
agent_session = django_to_agent(db_session)
result = await agent.run(
    message,
    message_history=agent_session.get_history()
)
```

### 6. ❌ Unused Tool Files

**Problem:**
Having multiple tool implementations where only one is used creates confusion.

**Solution:**
- Delete unused implementations
- Or clearly mark as `_experimental` or `_legacy`
- Document why multiple versions exist if necessary

### 7. ❌ Storing Secrets in Environment Variables at Runtime

**Problem:**
```python
os.environ["API_KEY"] = user_provided_key  # Affects whole process
```

**Solution:**
Pass secrets as function parameters, not environment variables

### 8. ❌ No Model Name Validation

**Problem:**
```python
def get_model(model_name: str):
    return OpenAIModel(model_name)  # Accepts anything!
```

**Solution:**
```python
VALID_MODELS = {"gpt-4o", "gpt-4o-mini", "gpt-4-turbo"}

def get_model(model_name: str):
    if model_name not in VALID_MODELS:
        raise ValueError(f"Invalid model. Choose from: {VALID_MODELS}")
    return OpenAIModel(model_name)
```

---

## Configuration Best Practices

### Settings Organization

```python
# settings/third_party.py
OPENAI_API_KEY = env("OPENAI_API_KEY", default=None)
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default=None)

# AI Configuration
DEFAULT_LLM_MODEL = env("DEFAULT_LLM_MODEL", default="gpt-4o")
DEFAULT_LLM_TEMPERATURE = env.float("DEFAULT_LLM_TEMPERATURE", default=0.7)
MAX_TOKENS = env.int("MAX_TOKENS", default=4000)

# Tool Execution Limits
CODE_EXECUTION_TIMEOUT = env.int("CODE_EXECUTION_TIMEOUT", default=5)
CODE_EXECUTION_MAX_LENGTH = env.int("CODE_EXECUTION_MAX_LENGTH", default=10000)
```

### Environment Variables

```bash
# .env.local
OPENAI_API_KEY=sk-...
DEFAULT_LLM_MODEL=gpt-4o
DEFAULT_LLM_TEMPERATURE=0.7
CODE_EXECUTION_TIMEOUT=5
```

---

## Summary Checklist

Before merging PydanticAI integration code, verify:

- [ ] Correct pattern chosen: [Named AI Tool](AI_CONVENTIONS.md#named-ai-tools) for standalone processing, adapter pattern for Django model interaction
- [ ] PydanticAI models are clearly named (e.g., `Agent*` prefix) when coexisting with Django models
- [ ] Adapter pattern is used for Django ↔ PydanticAI conversion (when applicable)
- [ ] Only one system prompt definition (decorator OR constructor)
- [ ] Async boundaries handled with Django utilities (`async_to_sync`)
- [ ] Result attributes used correctly (`result.output`)
- [ ] No duplicate tool implementations
- [ ] Code execution tools are properly sandboxed
- [ ] API keys passed as parameters, not stored in `os.environ`
- [ ] Model names validated against allowed list
- [ ] Comprehensive tests for agent, tools, and Django integration
- [ ] Dead code removed
- [ ] Error handling includes logging and user-friendly messages

---

## References

- [PydanticAI Documentation](https://ai.pydantic.dev/)
- [PydanticAI Changelog](https://ai.pydantic.dev/changelog/)
- [Django Async Views](https://docs.djangoproject.com/en/stable/topics/async/)
- [RestrictedPython](https://pypi.org/project/RestrictedPython/)
- [Django's async_to_sync and sync_to_async](https://docs.djangoproject.com/en/stable/topics/async/#async-to-sync-and-sync-to-async)
