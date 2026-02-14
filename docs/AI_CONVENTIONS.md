# AI Integration Conventions

This document outlines general conventions and best practices for AI integrations in the Cuttlefish Django project, including chat agents, MCP servers, and LLM provider management.

## Table of Contents
- [Directory Structure](#directory-structure)
- [Model Organization](#model-organization)
- [Naming Conventions](#naming-conventions)
- [Provider Management](#provider-management)
- [Message History](#message-history)
- [Error Handling](#error-handling)
- [Monitoring and Logging](#monitoring-and-logging)
- [Cost Management](#cost-management)
- [Production Considerations](#production-considerations)
- [Named AI Tools](#named-ai-tools)

---

## Directory Structure

### Standard AI App Organization

```
apps/ai/
├── __init__.py
├── admin.py                    # Django admin for AI models
├── apps.py
├── models/                     # Django ORM models (database)
│   ├── __init__.py
│   └── chat.py                 # ChatSession, ChatMessage, ChatFeedback
├── agent/                      # PydanticAI agents (in-memory)
│   ├── __init__.py
│   ├── chat.py                 # Agent definitions
│   └── tools/                  # Agent tools
│       ├── __init__.py
│       ├── code_execution.py
│       ├── web_search.py
│       └── database.py
├── adapters/                   # Django ↔ PydanticAI conversion
│   ├── __init__.py
│   └── chat.py
├── llm/                        # LLM provider configuration
│   ├── __init__.py
│   └── providers.py
├── mcp/                        # Model Context Protocol servers
│   ├── __init__.py
│   ├── server.py               # Base MCP server
│   ├── quickbooks_server.py    # QuickBooks MCP
│   ├── quickbooks_tools.py
│   └── creative_juices_server.py
├── views/                      # Django views
│   ├── __init__.py
│   ├── chat.py                 # HTMX chat interface
│   └── test_chat.py            # Test endpoints
├── tests/                      # Tests
│   ├── __init__.py
│   ├── factories.py
│   ├── test_e2e_chat.py
│   └── test_mcp_*.py
└── migrations/
```

### Rationale

**`models/`**: Django models for database persistence
- `chat.py`: Chat sessions, messages, feedback
- Future: `completion.py`, `embedding.py`, etc.

**`agent/`**: PydanticAI agent logic (framework-specific, in-memory)
- Isolated from Django for testability
- Can be reused outside Django context

**`adapters/`**: Explicit conversion layer
- Prevents tight coupling
- Makes data flow clear
- Easier to test

**`llm/`**: Provider configuration and model selection
- Centralized provider management
- Environment-based configuration
- Model validation

**`mcp/`**: Model Context Protocol servers
- Each integration gets its own server file
- Tools defined separately from server logic
- Shared utilities in `server.py`

---

## Model Organization

### Django Models (Database Layer)

**Location:** `apps/ai/models/`

**Purpose:** Persist AI-related data

```python
# apps/ai/models/chat.py
from django.db import models
from apps.common.behaviors import Timestampable

class ChatSession(Timestampable, models.Model):
    """Database-backed chat session."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict)

    class Meta:
        ordering = ["-modified_at"]
        indexes = [
            models.Index(fields=["-modified_at"]),
            models.Index(fields=["user", "-modified_at"]),
        ]

class ChatMessage(Timestampable, models.Model):
    """Individual message in a chat session."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20, choices=[("user", "User"), ("assistant", "Assistant")])
    content = models.TextField()
    metadata = models.JSONField(default=dict)  # Store token counts, model used, etc.
    is_processed = models.BooleanField(default=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["session", "created_at"]),
        ]
```

**Key fields in metadata:**
```python
# ChatSession.metadata
{
    "model": "gpt-4o",
    "total_tokens": 15234,
    "total_cost_usd": 0.45,
    "first_message_at": "2025-01-15T10:30:00Z",
    "last_message_at": "2025-01-15T11:45:00Z",
}

# ChatMessage.metadata
{
    "model": "gpt-4o",
    "prompt_tokens": 150,
    "completion_tokens": 320,
    "cost_usd": 0.012,
    "tool_calls": ["search_web", "execute_python"],
    "finish_reason": "stop",
}
```

### PydanticAI Models (In-Memory Layer)

**Location:** `apps/ai/agent/`

**Purpose:** Runtime agent execution

**Naming:** Prefix with `Agent` to avoid confusion

```python
# apps/ai/agent/chat.py
from pydantic import BaseModel, Field
from datetime import datetime

class AgentChatSession(BaseModel):
    """In-memory chat session for PydanticAI agent."""
    session_id: str
    user_id: int | None = None
    messages: list["AgentChatMessage"] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)

    def add_message(self, role: str, content: str, metadata: dict = None):
        """Add a message to the session."""
        msg = AgentChatMessage(
            role=role,
            content=content,
            metadata=metadata or {}
        )
        self.messages.append(msg)
        return msg

    def get_history(self) -> list[dict]:
        """Get conversation history for agent."""
        return [
            {"role": msg.role, "content": msg.content}
            for msg in self.messages
        ]

class AgentChatMessage(BaseModel):
    """In-memory message for PydanticAI agent."""
    role: str
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: dict = Field(default_factory=dict)
```

---

## Naming Conventions

### Model Names

| Type | Convention | Example | Notes |
|------|------------|---------|-------|
| Django model | Plain name | `ChatSession` | Database-backed |
| Pydantic model | `Agent*` prefix | `AgentChatSession` | In-memory |
| Pydantic schema | `*Schema` suffix | `ChatSessionSchema` | Alternative to Agent prefix |
| Dependencies | `*Dependencies` | `ChatDependencies` | PydanticAI context |

### File Names

| Type | Convention | Example |
|------|------------|---------|
| Django models | Singular noun | `chat.py`, `completion.py` |
| Agent definitions | Singular noun | `chat.py`, `assistant.py` |
| Tools | Function/feature name | `code_execution.py`, `web_search.py` |
| MCP servers | `{service}_server.py` | `quickbooks_server.py` |
| MCP tools | `{service}_tools.py` | `quickbooks_tools.py` |
| Adapters | Match model file | `chat.py` (in `adapters/`) |
| Providers | `providers.py` | Standard name |

### Variable Names

```python
# Django models
db_session: ChatSession
db_message: ChatMessage

# PydanticAI models
agent_session: AgentChatSession
agent_message: AgentChatMessage

# LLM results
result: RunResult
response_text: str = result.output

# Dependencies
deps: ChatDependencies
ctx: RunContext[ChatDependencies]
```

---

## Provider Management

### Centralized Configuration

**Location:** `apps/ai/llm/providers.py`

```python
# apps/ai/llm/providers.py
import os
from typing import Literal
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.models.anthropic import AnthropicModel
from django.conf import settings

ModelName = Literal[
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
]

VALID_OPENAI_MODELS = {"gpt-4o", "gpt-4o-mini", "gpt-4-turbo"}
VALID_ANTHROPIC_MODELS = {"claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022"}

def get_openai_model(
    model_name: str = "gpt-4o",
    api_key: str | None = None
) -> OpenAIModel:
    """Get OpenAI model instance with validation."""
    if model_name not in VALID_OPENAI_MODELS:
        raise ValueError(
            f"Invalid OpenAI model: {model_name}. "
            f"Valid options: {VALID_OPENAI_MODELS}"
        )

    key = api_key or settings.OPENAI_API_KEY
    if not key:
        raise ValueError("OPENAI_API_KEY not configured")

    return OpenAIModel(model_name, api_key=key)

def get_anthropic_model(
    model_name: str = "claude-3-5-sonnet-20241022",
    api_key: str | None = None
) -> AnthropicModel:
    """Get Anthropic model instance with validation."""
    if model_name not in VALID_ANTHROPIC_MODELS:
        raise ValueError(
            f"Invalid Anthropic model: {model_name}. "
            f"Valid options: {VALID_ANTHROPIC_MODELS}"
        )

    key = api_key or settings.ANTHROPIC_API_KEY
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not configured")

    return AnthropicModel(model_name, api_key=key)

def get_default_model():
    """Get default model based on settings."""
    provider = settings.DEFAULT_LLM_PROVIDER  # "openai" or "anthropic"
    model_name = settings.DEFAULT_LLM_MODEL

    if provider == "openai":
        return get_openai_model(model_name)
    elif provider == "anthropic":
        return get_anthropic_model(model_name)
    else:
        raise ValueError(f"Unknown provider: {provider}")
```

### Settings Configuration

```python
# settings/third_party.py
OPENAI_API_KEY = env("OPENAI_API_KEY", default=None)
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default=None)

DEFAULT_LLM_PROVIDER = env("DEFAULT_LLM_PROVIDER", default="openai")
DEFAULT_LLM_MODEL = env("DEFAULT_LLM_MODEL", default="gpt-4o")
DEFAULT_LLM_TEMPERATURE = env.float("DEFAULT_LLM_TEMPERATURE", default=0.7)
DEFAULT_LLM_MAX_TOKENS = env.int("DEFAULT_LLM_MAX_TOKENS", default=4000)
```

---

## Message History

### History Management Best Practices

**1. Sliding Window for Long Conversations**

```python
class AgentChatSession(BaseModel):
    messages: list[AgentChatMessage] = Field(default_factory=list)

    def get_history(self, max_messages: int = 20) -> list[dict]:
        """Get recent conversation history with sliding window."""
        # Keep system message + recent messages
        recent = self.messages[-max_messages:]
        return [
            {"role": msg.role, "content": msg.content}
            for msg in recent
        ]

    def get_history_with_token_limit(self, max_tokens: int = 4000) -> list[dict]:
        """Get history that fits within token budget."""
        history = []
        token_count = 0

        # Work backwards from most recent
        for msg in reversed(self.messages):
            # Rough token estimate (actual tokenization would be better)
            msg_tokens = len(msg.content) // 4
            if token_count + msg_tokens > max_tokens:
                break
            history.insert(0, {"role": msg.role, "content": msg.content})
            token_count += msg_tokens

        return history
```

**2. Summarization for Very Long Conversations**

```python
async def get_summarized_history(
    session: ChatSession,
    max_messages: int = 50
) -> list[dict]:
    """Get history with old messages summarized."""
    all_messages = session.messages.all().order_by("created_at")

    if all_messages.count() <= max_messages:
        # No summarization needed
        return [
            {"role": msg.role, "content": msg.content}
            for msg in all_messages
        ]

    # Split into old (to summarize) and recent (keep as-is)
    old_messages = all_messages[:all_messages.count() - max_messages]
    recent_messages = all_messages[all_messages.count() - max_messages:]

    # Generate summary of old messages
    old_content = "\n".join([
        f"{msg.role}: {msg.content}"
        for msg in old_messages
    ])

    summary = await summarize_conversation(old_content)

    # Combine summary + recent messages
    history = [{"role": "system", "content": f"Previous conversation summary: {summary}"}]
    history.extend([
        {"role": msg.role, "content": msg.content}
        for msg in recent_messages
    ])

    return history
```

**3. Conversation Branching**

For advanced use cases where users can branch conversations:

```python
class ChatSession(models.Model):
    parent_session = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="branches"
    )
    branch_point_message = models.ForeignKey(
        ChatMessage,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    def get_full_history_with_branches(self) -> list[dict]:
        """Get history including parent session up to branch point."""
        if not self.parent_session:
            return self.get_history()

        # Get parent history up to branch point
        parent_history = self.parent_session.messages.filter(
            created_at__lte=self.branch_point_message.created_at
        )

        # Get this session's messages
        this_history = self.messages.all()

        # Combine
        return [
            {"role": msg.role, "content": msg.content}
            for msg in parent_history
        ] + [
            {"role": msg.role, "content": msg.content}
            for msg in this_history
        ]
```

---

## Error Handling

### Error Classification

Categorize errors for appropriate handling:

```python
# apps/ai/exceptions.py
class AIError(Exception):
    """Base exception for AI-related errors."""
    pass

class AIConfigurationError(AIError):
    """AI service is misconfigured (API key missing, invalid model, etc.)."""
    pass

class AIProviderError(AIError):
    """Error from AI provider (rate limit, server error, etc.)."""
    pass

class AIValidationError(AIError):
    """Input validation error."""
    pass

class AIToolExecutionError(AIError):
    """Error during tool execution."""
    pass
```

### View Error Handling

```python
# apps/ai/views/chat.py
import logging
from apps.ai.exceptions import (
    AIConfigurationError,
    AIProviderError,
    AIValidationError,
    AIToolExecutionError,
)

logger = logging.getLogger(__name__)

class ChatSendMessageView(View):
    async def post(self, request):
        try:
            # Process message
            result = await process_chat_message(message, session, deps)

        except AIConfigurationError as e:
            # Configuration errors - show helpful message
            logger.error(f"AI configuration error: {e}")
            return JsonResponse({
                "error": "AI service is not properly configured. Please contact support."
            }, status=503)

        except AIValidationError as e:
            # User input errors - show to user
            logger.warning(f"AI validation error: {e}")
            return JsonResponse({
                "error": str(e)
            }, status=400)

        except AIProviderError as e:
            # Provider errors - retry or show generic message
            logger.error(f"AI provider error: {e}")
            return JsonResponse({
                "error": "AI service is temporarily unavailable. Please try again."
            }, status=503)

        except AIToolExecutionError as e:
            # Tool execution errors - safe to show to user
            logger.warning(f"Tool execution error: {e}")
            return JsonResponse({
                "error": f"Unable to execute tool: {e}"
            }, status=500)

        except Exception as e:
            # Unexpected errors - log and show generic message
            logger.exception(f"Unexpected error in chat: {e}")
            return JsonResponse({
                "error": "An unexpected error occurred. Please try again."
            }, status=500)
```

---

## Monitoring and Logging

### Structured Logging

```python
# apps/ai/logging.py
import logging
import json
from datetime import datetime

logger = logging.getLogger(__name__)

def log_agent_call(
    user_id: int | None,
    session_id: str,
    message: str,
    model: str,
    duration_ms: int,
    tokens: dict,
    cost_usd: float,
    success: bool,
):
    """Log agent call for monitoring and analytics."""
    logger.info(
        "agent_call",
        extra={
            "user_id": user_id,
            "session_id": session_id,
            "message_length": len(message),
            "model": model,
            "duration_ms": duration_ms,
            "prompt_tokens": tokens.get("prompt_tokens"),
            "completion_tokens": tokens.get("completion_tokens"),
            "cost_usd": cost_usd,
            "success": success,
            "timestamp": datetime.utcnow().isoformat(),
        }
    )

def log_tool_execution(
    user_id: int | None,
    tool_name: str,
    duration_ms: int,
    success: bool,
    error: str | None = None,
):
    """Log tool execution for security and debugging."""
    logger.info(
        "tool_execution",
        extra={
            "user_id": user_id,
            "tool_name": tool_name,
            "duration_ms": duration_ms,
            "success": success,
            "error": error,
            "timestamp": datetime.utcnow().isoformat(),
        }
    )
```

### Usage in Views

```python
import time
from apps.ai.logging import log_agent_call

async def process_chat_message(message: str, session, deps):
    start_time = time.time()
    success = False

    try:
        result = await chat_agent.run(message, deps=deps)
        success = True
        return result

    finally:
        duration_ms = int((time.time() - start_time) * 1000)

        # Extract metadata from result or session
        log_agent_call(
            user_id=deps.user_id,
            session_id=deps.session_id,
            message=message,
            model="gpt-4o",
            duration_ms=duration_ms,
            tokens={"prompt_tokens": 150, "completion_tokens": 320},
            cost_usd=0.012,
            success=success,
        )
```

---

## Cost Management

### Token Tracking

```python
# apps/ai/usage.py
from decimal import Decimal

# Pricing per 1M tokens (as of 2025)
PRICING = {
    "gpt-4o": {
        "prompt": Decimal("2.50"),  # $2.50 per 1M prompt tokens
        "completion": Decimal("10.00"),  # $10.00 per 1M completion tokens
    },
    "gpt-4o-mini": {
        "prompt": Decimal("0.150"),
        "completion": Decimal("0.600"),
    },
    "claude-3-5-sonnet-20241022": {
        "prompt": Decimal("3.00"),
        "completion": Decimal("15.00"),
    },
}

def calculate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int
) -> Decimal:
    """Calculate cost in USD for a completion."""
    if model not in PRICING:
        raise ValueError(f"Unknown model: {model}")

    pricing = PRICING[model]
    prompt_cost = (Decimal(prompt_tokens) / 1_000_000) * pricing["prompt"]
    completion_cost = (Decimal(completion_tokens) / 1_000_000) * pricing["completion"]

    return prompt_cost + completion_cost
```

### Usage Limits

```python
# apps/ai/models/usage.py
class UserUsage(Timestampable, models.Model):
    """Track AI usage per user."""
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    month = models.DateField()  # First day of month
    total_tokens = models.IntegerField(default=0)
    total_cost_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    request_count = models.IntegerField(default=0)

    class Meta:
        unique_together = [["user", "month"]]
        indexes = [
            models.Index(fields=["user", "month"]),
        ]

    @classmethod
    def check_limit(cls, user: User, estimated_cost: Decimal) -> bool:
        """Check if user is within their monthly limit."""
        from datetime import date
        month_start = date.today().replace(day=1)

        usage, _ = cls.objects.get_or_create(
            user=user,
            month=month_start,
        )

        # Get user's plan limit
        limit = user.get_monthly_ai_limit_usd()  # e.g., $10.00

        return (usage.total_cost_usd + estimated_cost) <= limit
```

---

## Production Considerations

### Background Processing

For production, process AI responses in background tasks:

```python
# apps/ai/tasks.py
from celery import shared_task

@shared_task
def process_chat_message_task(session_id: str, message_id: str):
    """Process chat message in background."""
    from apps.ai.models import ChatSession, ChatMessage
    from apps.ai.agent.chat import process_chat_message

    session = ChatSession.objects.get(id=session_id)
    user_message = ChatMessage.objects.get(id=message_id)

    # Create placeholder assistant message
    assistant_message = ChatMessage.objects.create(
        session=session,
        role="assistant",
        content="",
        is_processed=False,
    )

    try:
        # Process message
        result = process_chat_message_sync(
            user_message.content,
            session,
        )

        # Update message
        assistant_message.content = result
        assistant_message.is_processed = True
        assistant_message.save()

    except Exception as e:
        assistant_message.content = "I encountered an error. Please try again."
        assistant_message.metadata = {"error": str(e)}
        assistant_message.is_processed = True
        assistant_message.save()

# In views
class ChatSendMessageView(View):
    def post(self, request):
        # Save user message
        user_message = ChatMessage.objects.create(...)

        # Queue background task
        process_chat_message_task.delay(
            session_id=str(session.id),
            message_id=str(user_message.id),
        )

        return JsonResponse({"status": "processing"})
```

### Rate Limiting

```python
# apps/ai/middleware.py
from django.core.cache import cache
from django.http import JsonResponse

class AIRateLimitMiddleware:
    """Rate limit AI requests per user."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/ai/chat/"):
            user_id = request.user.id if request.user.is_authenticated else request.session.session_key

            # Allow 10 requests per minute
            cache_key = f"ai_rate_limit:{user_id}"
            request_count = cache.get(cache_key, 0)

            if request_count >= 10:
                return JsonResponse({
                    "error": "Rate limit exceeded. Please wait before sending more messages."
                }, status=429)

            cache.set(cache_key, request_count + 1, timeout=60)

        return self.get_response(request)
```

### Caching Responses

For deterministic queries, cache responses:

```python
from django.core.cache import cache
import hashlib

async def get_cached_completion(
    message: str,
    model: str,
    temperature: float = 0.0,
) -> str | None:
    """Get cached completion if available (for temperature=0 queries)."""
    if temperature > 0:
        return None  # Don't cache non-deterministic responses

    cache_key = hashlib.sha256(
        f"{model}:{message}".encode()
    ).hexdigest()

    return cache.get(f"ai_completion:{cache_key}")

async def cache_completion(
    message: str,
    model: str,
    response: str,
    ttl_seconds: int = 86400,  # 24 hours
):
    """Cache completion response."""
    cache_key = hashlib.sha256(
        f"{model}:{message}".encode()
    ).hexdigest()

    cache.set(f"ai_completion:{cache_key}", response, timeout=ttl_seconds)
```

---

## Summary

**Key principles for AI integration:**

1. **Separation of concerns**: Django models (database) ↔ Adapters ↔ PydanticAI models (in-memory)
2. **Clear naming**: Prefix PydanticAI models with `Agent` to avoid confusion
3. **Centralized providers**: Single source of truth for model configuration
4. **Proper async handling**: Use Django's async utilities, not custom event loop code
5. **Comprehensive logging**: Track usage, costs, and performance
6. **Error handling**: Classify errors and handle appropriately
7. **Security**: Sandbox tool execution, validate inputs
8. **Production-ready**: Background tasks, rate limiting, caching

**See also:**
- [PydanticAI Integration Guide](PYDANTIC_AI_INTEGRATION.md) - Detailed PydanticAI patterns
- [MCP Development Guide](MCP_DEVELOPMENT_GUIDE.md) - Model Context Protocol servers
- [Error Handling](ERROR_HANDLING.md) - General error handling patterns

---

## Named AI Tools

Named AI tools are self-contained PydanticAI modules that perform specific AI tasks. Each tool is a single Python file with one public function, one output model, and one PydanticAI Agent.

### Location

`apps/podcast/services/` — one file per tool, flat alongside existing services.

### Convention Rules

| Rule | Description |
|------|-------------|
| File name = tool name | `generate_chapters.py` contains `generate_chapters()` |
| One public function per module | Same name as the file |
| Pydantic output model in same file | No shared output models |
| Module-level Agent | `agent = Agent(...)` defined at module scope |
| Model is tool's decision | Each tool picks what's appropriate (Sonnet for simple, Opus for complex) |
| Logging includes usage | `logger.info(...)` with model name, input tokens, output tokens |
| No shared base class | Each tool is fully self-contained |
| Sync only | Use `run_sync()`, no async |

### Example: `generate_chapters.py`

```python
"""Generate chapter markers from a podcast transcript."""

import logging

from pydantic import BaseModel
from pydantic_ai import Agent

logger = logging.getLogger(__name__)


# --- Output schema ---


class Chapter(BaseModel):
    title: str
    start_time: str  # "MM:SS"
    summary: str


class ChapterList(BaseModel):
    chapters: list[Chapter]


# --- Agent ---

agent = Agent(
    "anthropic:claude-sonnet-4-5-20250929",
    output_type=ChapterList,
    system_prompt=(
        "You are a podcast editor. Given a transcript with timestamps, "
        "identify 10-15 natural topic transitions and generate chapter markers. "
        "Each chapter should have a concise, descriptive title."
    ),
    defer_model_check=True,
)


# --- Public interface ---


def generate_chapters(transcript: str, episode_title: str) -> ChapterList:
    """Generate chapter markers from a transcript.

    Args:
        transcript: Full episode transcript with timestamps.
        episode_title: Title of the episode for context.

    Returns:
        ChapterList with 10-15 chapters.
    """
    result = agent.run_sync(
        f"Episode: {episode_title}\n\nTranscript:\n{transcript}"
    )
    logger.info(
        "generate_chapters: model=%s input_tokens=%d output_tokens=%d",
        agent.model,
        result.usage().input_tokens,
        result.usage().output_tokens,
    )
    return result.output
```

### Long Prompts

For tools with lengthy system prompts, store the prompt in `apps/podcast/services/prompts/{tool_name}.md` and load at module level:

```python
from pathlib import Path

_PROMPT_FILE = Path(__file__).parent / "prompts" / "write_synthesis.md"
_SYSTEM_PROMPT = _PROMPT_FILE.read_text()

agent = Agent(
    "anthropic:claude-opus-4-6",
    output_type=SynthesisReport,
    system_prompt=_SYSTEM_PROMPT,
    defer_model_check=True,
)
```

### Available Tools

| Tool | Purpose | Model |
|------|---------|-------|
| `generate_chapters` | Chapter markers from transcript | Sonnet |
| `digest_research` | Compact research digest | Sonnet |
| `discover_questions` | Gap analysis and followup questions | Sonnet |
| `write_metadata` | Episode publishing metadata | Sonnet |
| `cross_validate` | Cross-source verification matrix | Sonnet |
| `write_briefing` | Master research briefing | Sonnet |
| `write_synthesis` | Narrative report (5,000-8,000 words) | Opus |
| `plan_episode` | Episode structure for NotebookLM | Opus |

### Testing

Each tool has tests in `apps/podcast/tests/test_ai_tools/`. Tests mock the PydanticAI Agent to avoid real API calls:

```python
from unittest.mock import MagicMock, patch

with patch("apps.podcast.services.generate_chapters.agent") as mock_agent:
    mock_agent.run_sync.return_value = mock_result
    result = generate_chapters("transcript", "Episode Title")
```
