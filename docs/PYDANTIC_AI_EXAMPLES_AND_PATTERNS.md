# PydanticAI Examples and Patterns

Best practices for running PydanticAI agents on a web server with long-running
tasks and many tools.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Agent Definition](#agent-definition)
3. [Choosing a Run Method](#choosing-a-run-method)
4. [Dependency Injection](#dependency-injection)
5. [Tool Registration at Scale](#tool-registration-at-scale)
6. [Long-Running Task Execution](#long-running-task-execution)
7. [Timeout and Progress Management](#timeout-and-progress-management)
8. [Real-Time Streaming and Progress](#real-time-streaming-and-progress)
9. [Model Fallback and Resilience](#model-fallback-and-resilience)
10. [Self-Correcting Retry Loops](#self-correcting-retry-loops)
11. [Message History Management](#message-history-management)
12. [Usage Limits and Credit Tracking](#usage-limits-and-credit-tracking)
13. [Error Handling](#error-handling)
14. [Observability](#observability)
15. [Multi-Agent Patterns](#multi-agent-patterns)
16. [Durable Execution](#durable-execution)
17. [Anti-Patterns](#anti-patterns)

---

## Architecture Overview

A typical web server + PydanticAI setup separates request handling from agent
execution:

```
HTTP Request (user message)
  -> Web framework creates DB record
  -> Enqueue background task (Celery, Django Q2, Dramatiq, Arq, etc.)
  -> Return 201 immediately

Background Worker picks up task:
  -> Instantiate agent with context
  -> agent.run_sync() blocks until completion
  -> All tool calls execute synchronously within the run
  -> Save result to DB
  -> Publish real-time update (Redis pub/sub, SSE, WebSocket)

Client receives update via SSE/WebSocket
```

Key principle: **never run agents inside the request-response cycle**. Agent
runs can take seconds to minutes depending on tool complexity. Offload to a
background worker and notify the client asynchronously.

---

## Agent Definition

### Wrapper Class Pattern

Wrap the raw `pydantic_ai.Agent` in an application-level class that manages
tools, context, and lifecycle:

```python
from pydantic_ai import Agent
from pydantic_ai.models.fallback import FallbackModel

class ChatAgent:
    """Orchestrates a PydanticAI agent with dynamic tools and context."""

    def __init__(self, conversation: Conversation, user: User):
        self.conversation = conversation
        self.user = user
        self._agent: Agent | None = None
        self._tools_loaded = False

    def _create_agent(self) -> Agent[dict[str, Any], str]:
        self._ensure_tools_loaded()

        model = FallbackModel("anthropic:claude-sonnet-4-5", "openai:gpt-5")

        return Agent[dict[str, Any], str](
            model=model,
            instructions=self._build_instructions(),
            tools=list(self._tools.values()),
        )

    def process_input(self, prompt: str) -> AgentResult:
        agent = self._create_agent()
        context = self._build_context()

        result = agent.run_sync(
            prompt,
            deps=context,
            message_history=self.conversation.message_history,
            usage_limits=UsageLimits(request_limit=25),
        )

        self._save_history(result.new_messages())
        return AgentResult(output=result.output, usage=result.usage())
```

### Why a Wrapper?

- Lazy tool loading (import-heavy tool modules only when needed)
- Dynamic tool sets per user/conversation (different integrations = different tools)
- Lifecycle management (cleanup connections, track usage)
- Separation of framework concerns from business logic

### Instructions vs System Prompts

Prefer `instructions` over `system_prompt` for content that should refresh on
every run. System prompts persist in message history; instructions do not:

```python
# Good: instructions refresh each run, no history bloat
agent = Agent(
    "anthropic:claude-sonnet-4-5",
    instructions="You are a data analyst. Today is 2026-02-17.",
)

# Also good: dynamic instructions via decorator
@agent.instructions
def dynamic_instructions() -> str:
    return f"Current time: {datetime.now().isoformat()}"

# Caution: system_prompt persists in message history
# Use only for truly static context
agent = Agent("anthropic:claude-sonnet-4-5", system_prompt="Be concise.")
```

---

## Choosing a Run Method

| Method | When to Use |
|---|---|
| `run_sync()` | Background workers (Celery, Django Q2, Dramatiq). Simplest option. |
| `run()` | Async web frameworks (FastAPI, Starlette) or async task runners. |
| `run_stream()` | When you need token-by-token streaming to the client. |
| `iter()` | When you need node-by-node control over the agent graph. |

### Background Workers: Prefer `run_sync()`

Most background task systems (Celery, Django Q2, Dramatiq) run synchronous
Python. Use `run_sync()` to avoid event loop conflicts:

```python
# Inside a background task function
def process_ai_response_task(conversation_id: str, message_id: str):
    conversation = Conversation.objects.get(id=conversation_id)
    agent = ChatAgent(conversation=conversation, user=conversation.user)
    result = agent.process_input(user_message.text)
    # Save result and notify client
```

`run_sync()` internally calls `loop.run_until_complete(self.run())`. It blocks
the worker process for the entire agent run. This is fine - background workers
are designed for exactly this.

### Async Frameworks: Use `run()` or `run_stream()`

In FastAPI or similar async frameworks, use the async methods:

```python
@app.post("/chat")
async def chat(request: ChatRequest):
    # Enqueue to background - don't run inline
    task_id = await enqueue_agent_task(request.conversation_id, request.prompt)
    return {"task_id": task_id, "status": "processing"}
```

If you must run inline (short tasks only):

```python
@app.post("/quick-query")
async def quick_query(request: QueryRequest):
    result = await agent.run(
        request.prompt,
        deps=build_deps(request),
        usage_limits=UsageLimits(request_limit=3),
    )
    return {"output": result.output}
```

---

## Dependency Injection

### Typed Dependencies for Simple Cases

When your agent has a fixed set of dependencies, use a typed deps class:

```python
@dataclass
class AnalystDeps:
    db_connection: DatabaseConnection
    user_id: str
    tenant_id: str

agent = Agent[AnalystDeps, AnalystOutput](
    "anthropic:claude-sonnet-4-5",
    deps_type=AnalystDeps,
)

@agent.tool
async def query_data(ctx: RunContext[AnalystDeps], sql: str) -> str:
    return await ctx.deps.db_connection.execute(sql)
```

### Dict Dependencies for Dynamic Tool Sets

When tools vary per request (different integrations, different capabilities),
use a plain dict. This avoids coupling the deps type to a specific tool
combination:

```python
agent = Agent[dict[str, Any], str](
    model=model,
    instructions="...",
    tools=dynamic_tool_list,
)

deps = {
    "conversation_id": conversation.id,
    "user": user,
    "active_integrations": integrations,
    "memory_context": load_memory(user),
}

result = agent.run_sync(prompt, deps=deps)
```

### Injecting Fresh Context Per Run

Reload context at the start of each run to avoid stale data:

```python
def process_input(self, prompt: str) -> AgentResult:
    # Fresh context every time - data may have changed between turns
    fresh_context = load_recent_context(self.conversation)

    deps = {
        "conversation": self.conversation,
        "context": fresh_context,
    }

    # Also inject into the prompt for immediate visibility
    augmented_prompt = (
        f"<context>\n{fresh_context}\n</context>\n\n{prompt}"
    )

    result = self.agent.run_sync(augmented_prompt, deps=deps)
    return result
```

---

## Tool Registration at Scale

When an agent has 15-30+ tools, organization and lazy loading become critical.

### Tool Registry Pattern

Create a centralized registry that tools register themselves into:

```python
# tools/registry.py
class ToolRegistry:
    _instance: "ToolRegistry | None" = None
    _tools: dict[str, ToolModule] = {}

    @classmethod
    def register(cls, config: dict) -> Callable:
        def decorator(func: Callable) -> Callable:
            cls._tools[config["name"]] = ToolModule(
                function=func, config=config
            )
            return func
        return decorator

    @classmethod
    def get_pydantic_tools(
        cls, names: list[str]
    ) -> dict[str, Tool]:
        return {
            name: Tool(cls._tools[name].function, name=name)
            for name in names
            if name in cls._tools
        }

# tools/calculator.py
@ToolRegistry.register({
    "name": "calculator",
    "description": "Evaluate math expressions",
    "category": "utility",
})
def calculator(expression: str) -> float:
    """Evaluate a mathematical expression."""
    return safe_eval(expression)
```

### Lazy Tool Loading

Import tool modules only when the agent is about to run. This prevents heavy
imports (pandas, API clients) from slowing down application startup:

```python
def _ensure_tools_loaded(self):
    if self._tools_loaded:
        return

    # Heavy imports happen here, not at module level
    import myapp.tools.calculator_tool
    import myapp.tools.chart_generator
    import myapp.tools.data_query

    self._tool_registry = ToolRegistry.get_instance()
    self._tools = self._tool_registry.get_pydantic_tools([
        "calculator", "chart_generator", "data_query",
    ])
    self._tools_loaded = True
```

### Dynamic Tool Generation from Schemas

When tools come from external systems (MCP servers, API specs), generate
PydanticAI `Tool` instances at runtime:

```python
from pydantic import create_model
from pydantic_ai import Tool

def create_tool_from_schema(
    name: str,
    description: str,
    input_schema: dict,
    handler: Callable,
) -> Tool:
    """Build a PydanticAI Tool from a JSON schema at runtime."""
    fields = {}
    for prop_name, prop_def in input_schema.get("properties", {}).items():
        python_type = JSON_TYPE_MAP.get(prop_def["type"], str)
        required = prop_name in input_schema.get("required", [])
        default = ... if required else None
        fields[prop_name] = (python_type, default)

    InputModel = create_model(f"{name}_input", **fields)

    def tool_function(input_data: InputModel) -> dict[str, Any]:
        return handler(name, input_data.model_dump())

    return Tool(tool_function, name=name, description=description)
```

### Tool Wrapping for Cross-Cutting Concerns

Wrap tools with hooks for telemetry, usage tracking, and error handling:

```python
def create_tool_wrapper(
    original_tool: Callable,
    tool_name: str,
    hooks: list[ToolHook],
) -> Callable:
    @functools.wraps(original_tool)
    def wrapper(*args, **kwargs):
        for hook in hooks:
            hook.before_execution(tool_name, kwargs)
        try:
            result = original_tool(*args, **kwargs)
            for hook in hooks:
                hook.after_execution(tool_name, result)
            return result
        except Exception as exc:
            for hook in hooks:
                hook.on_error(tool_name, exc)
            raise
    return wrapper
```

---

## Long-Running Task Execution

### The Single-Task Model

Run the entire agent execution inside one background task. Do not spawn
sub-tasks for individual tool calls:

```python
# Good: one task, synchronous tool execution
def process_ai_response_task(conversation_id: str, message_id: str):
    conversation = Conversation.objects.get(id=conversation_id)
    agent = ChatAgent(conversation=conversation, user=conversation.user)
    result = agent.process_input(user_message.text)  # All tools run here
    save_result(result)

# Bad: spawning sub-tasks per tool call
def process_ai_response_task(conversation_id, message_id):
    ...
    # Don't do this - adds complexity and breaks agent flow
    sub_task_id = enqueue("run_tool", tool_name, tool_args)
    wait_for_task(sub_task_id)  # Blocks anyway, with extra overhead
```

Why single-task? PydanticAI's `run_sync()` is a tight loop: model returns tool
calls, tools execute, results feed back to model. Breaking this loop across
task boundaries adds latency, error surface, and state management complexity
for no benefit.

### Worker Configuration for Long Tasks

Configure your task queue for the reality of LLM calls:

```python
# Celery example
CELERY_TASK_TIME_LIMIT = 600       # 10-minute hard kill
CELERY_TASK_SOFT_TIME_LIMIT = 540  # Soft limit for graceful shutdown
CELERY_TASK_ACKS_LATE = True       # Acknowledge after completion
CELERY_WORKER_MAX_TASKS_PER_CHILD = 50  # Restart worker periodically

# Django Q2 example
Q_CLUSTER = {
    "workers": 4,              # Reserve 1 CPU for web server
    "timeout": 600,            # 10-minute hard kill
    "max_attempts": 1,         # No auto-retry (handle retries in agent)
    "retry": 601,              # Must be > timeout
    "queue_limit": 20,         # Prevent memory pressure
    "recycle": 3,              # Restart worker after N tasks (memory leaks)
}

# Dramatiq example
dramatiq.set_broker(RedisBroker())
@dramatiq.actor(max_retries=0, time_limit=600_000)  # 10 min, no retry
def process_ai_response(conversation_id: str): ...
```

Key insight: set max retries to 0 or 1. Agent tasks are not idempotent -
re-running from scratch can produce duplicate messages, double-charge users, or
cause confusion. Handle retries inside the agent logic itself.

---

## Timeout and Progress Management

### Layered Timeout Strategy

Apply timeouts at multiple levels. Each layer catches different failure modes:

```
Layer 1: Task queue timeout (10 min)
  - Catches: worker crash, deadlock, runaway agent
  - Action: kills worker process

Layer 2: Tool-level timeout (30s - 10 min, varies by tool)
  - Catches: hung API calls, slow external services
  - Action: raises TimeoutError, agent can recover

Layer 3: Progress monitor (4 min between updates)
  - Catches: agent stuck in loop, tool producing no output
  - Action: raises TimeoutError with diagnostic info
```

### Tool-Level Timeout with ThreadPoolExecutor

For tools that call external APIs, wrap execution in a timeout:

```python
from concurrent.futures import ThreadPoolExecutor, TimeoutError

TOOL_TIMEOUTS = {
    "quick_lookup": 10,
    "research_query": 300,
    "data_export": 600,
    "default": 120,
}

executor = ThreadPoolExecutor(max_workers=4)

def execute_tool_with_timeout(
    tool_name: str,
    tool_func: Callable,
    arguments: dict,
) -> Any:
    timeout = TOOL_TIMEOUTS.get(tool_name, TOOL_TIMEOUTS["default"])
    future = executor.submit(tool_func, **arguments)
    try:
        return future.result(timeout=timeout)
    except TimeoutError:
        future.cancel()
        raise TimeoutError(
            f"Tool '{tool_name}' exceeded {timeout}s timeout"
        )
```

### Progress Monitor

Track tool execution progress and detect stalls:

```python
import time

class ProgressMonitor:
    STALL_TIMEOUT = 240  # 4 minutes with no progress update

    def __init__(self, message_id: str):
        self.message_id = message_id
        self.last_update = time.time()

    def update(self, current: int, total: int, message: str = ""):
        self.last_update = time.time()
        # Publish progress to client via SSE/WebSocket
        publish_progress(self.message_id, current, total, message)

    def check_timeout(self):
        elapsed = time.time() - self.last_update
        if elapsed > self.STALL_TIMEOUT:
            raise TimeoutError(
                f"No progress for {elapsed:.0f}s"
            )

    def create_callback(self) -> Callable:
        """Pass this to long-running tools as a progress callback."""
        def callback(current: int, total: int, message: str = ""):
            self.update(current, total, message)
        return callback
```

Pass the progress callback into tools via dependencies:

```python
deps = {
    "progress_callback": monitor.create_callback(),
}

@agent.tool
def research_data(ctx: RunContext[dict], query: str) -> dict:
    callback = ctx.deps["progress_callback"]
    callback(1, 5, "Fetching data...")
    data = fetch_from_api(query)
    callback(3, 5, "Processing results...")
    results = process(data)
    callback(5, 5, "Complete")
    return results
```

---

## Real-Time Streaming and Progress

### Pub/Sub Pattern for Progress Updates

Use your ORM's save hooks or an event system to broadcast updates whenever the
message record changes:

```python
# Using Django signals
@receiver(post_save, sender=Message)
def publish_message(sender, instance, **kwargs):
    redis_client.publish(
        f"conversation:{instance.conversation_id}",
        json.dumps(instance.to_dict()),
    )

# Using SQLAlchemy events
@event.listens_for(Message, "after_update")
def publish_message(mapper, connection, target):
    redis_client.publish(
        f"conversation:{target.conversation_id}",
        json.dumps(target.to_dict()),
    )

# Framework-agnostic: call publish explicitly after saves
def save_and_publish(message: Message):
    message.save()
    redis_client.publish(
        f"conversation:{message.conversation_id}",
        json.dumps(message.to_dict()),
    )
```

### Safe Updates That Trigger Events

When using ORMs with save hooks/signals, always save through the model instance
rather than using bulk update queries that bypass hooks:

```python
# Good: triggers save hooks -> pub/sub -> SSE
def safe_message_update(message_id: str, **fields) -> None:
    message = Message.objects.get(id=message_id)
    for field, value in fields.items():
        setattr(message, field, value)
    message.save(update_fields=list(fields.keys()))

# Bad: bypasses save hooks, client never gets notified
Message.objects.filter(id=message_id).update(status="complete")
#  ^-- Django .update() skips signals
#  Same issue: SQLAlchemy session.execute(update(...))
```

### Status Ownership

Let the top-level orchestrator (your wrapper class or the task function) own
the message status. Tools should write results into content fields, never set
`status`:

```python
# In the task function - orchestrator owns status
def process_ai_response_task(conversation_id, message_id):
    message = Message.objects.get(id=message_id)
    try:
        result = agent.process_input(...)
        safe_message_update(message.id, status="complete", text=result.output)
    except Exception as e:
        safe_message_update(message.id, status="error", text=str(e))
        capture_exception(e)

# In a tool - return content, not status
@agent.tool
def analyze_data(ctx: RunContext, query: str) -> dict:
    result = run_analysis(query)
    # Return data - let the orchestrator handle status
    return {"data": result, "row_count": len(result)}
```

---

## Model Fallback and Resilience

### FallbackModel

Use `FallbackModel` to automatically try a backup model when the primary fails:

```python
from pydantic_ai.models.fallback import FallbackModel

model = FallbackModel(
    "anthropic:claude-sonnet-4-5",  # Primary
    "openai:gpt-5",                # Fallback on 4xx/5xx
)

agent = Agent(model=model, instructions="...")
```

When the primary model returns an HTTP error, PydanticAI automatically retries
with the fallback. The agent code is unaware of which model handled the
request.

### Handling FallbackExceptionGroup

When both models fail, PydanticAI raises a `FallbackExceptionGroup`. Handle it
explicitly:

```python
from pydantic_ai.models.fallback import FallbackExceptionGroup

try:
    result = agent.run_sync(prompt, deps=deps)
except FallbackExceptionGroup as eg:
    # Both models failed - log each error
    for exc in eg.exceptions:
        logger.error("Model failure: %s", exc)
    capture_exception(eg)
    return error_response("All models unavailable")
```

### Model-Specific Settings

Different models support different features. Apply settings conditionally:

```python
from pydantic_ai.settings import (
    AnthropicModelSettings,
    OpenAIResponsesModelSettings,
)

# Claude with extended thinking
claude_settings = AnthropicModelSettings(
    anthropic_thinking={"type": "enabled", "budget_tokens": 8192}
)

# GPT with reasoning controls
gpt_settings = OpenAIResponsesModelSettings(
    openai_reasoning_effort="low",
    openai_reasoning_summary="concise",
)

# Apply at run time
result = agent.run_sync(
    prompt,
    deps=deps,
    model_settings=claude_settings,
)
```

---

## Self-Correcting Retry Loops

For tools that generate code or queries (SQL, GraphQL, API calls), implement
retry loops with cumulative error context:

```python
MAX_RETRIES = 4

@dataclass
class AttemptRecord:
    attempt_number: int
    success: bool
    generated_code: str
    error_message: str = ""

def run_with_retries(
    agent: Agent,
    base_prompt: str,
    deps: dict,
) -> str:
    attempts: list[AttemptRecord] = []

    for attempt_num in range(1, MAX_RETRIES + 1):
        # Build prompt with all previous failures
        prompt = base_prompt
        if attempts:
            prompt += "\n\n--- SELF-CORRECTION REQUIRED ---\n"
            prompt += "<previous_failures>\n"
            for a in attempts:
                prompt += (
                    f"Attempt {a.attempt_number}:\n"
                    f"Code:\n{a.generated_code}\n"
                    f"Error:\n{a.error_message}\n\n"
                )
            prompt += "</previous_failures>\n"

        # Final attempt: force simplification
        if attempt_num == MAX_RETRIES:
            prompt += (
                "\nFINAL ATTEMPT: Use the simplest possible approach. "
                "Remove all optional fields and complex joins.\n"
            )

        result = agent.run_sync(prompt, deps=deps)
        generated_code = result.output

        try:
            output = execute_code(generated_code)
            return output
        except Exception as exc:
            # Enrich error with metadata for better self-correction
            error_msg = str(exc)
            if isinstance(exc, SchemaValidationError):
                error_msg += f"\nValid fields: {get_field_suggestions(exc)}"

            attempts.append(AttemptRecord(
                attempt_number=attempt_num,
                success=False,
                generated_code=generated_code,
                error_message=error_msg,
            ))

    raise AgentExhaustedException(
        f"Failed after {MAX_RETRIES} attempts", attempts=attempts
    )
```

### Key principles for self-correction:

1. **Cumulative context**: Include ALL previous failures in each retry prompt,
   not just the last one
2. **Error enrichment**: Add metadata (valid field names, schema hints) to
   error messages before feeding them back
3. **Escalating simplification**: On the final attempt, instruct the model to
   use the simplest possible approach
4. **Non-retryable errors**: Some errors (permission denied, invalid
   credentials) should terminate immediately
5. **Attempt telemetry**: Record every attempt for debugging and model
   evaluation

---

## Message History Management

### Storing and Restoring History

Store message history in the database to support multi-turn conversations:

```python
# After a successful run
new_messages = result.new_messages()
conversation.message_history = [m.model_dump() for m in new_messages]
conversation.save(update_fields=["message_history"])

# On the next run
history = [
    ModelMessage.model_validate(m)
    for m in conversation.message_history
]
result = agent.run_sync(prompt, deps=deps, message_history=history)
```

### Preventing History Bloat

Instructions are re-injected every run. Strip them from saved history to
prevent exponential growth:

```python
def clean_history(messages: list[dict]) -> list[dict]:
    """Remove instructions from history to prevent bloat."""
    cleaned = []
    for msg in messages:
        if msg.get("kind") == "request" and "instructions" in msg:
            msg = {k: v for k, v in msg.items() if k != "instructions"}
        cleaned.append(msg)
    return cleaned

conversation.message_history = clean_history(
    [m.model_dump() for m in result.new_messages()]
)
```

### Token Counting

Track token usage per conversation and truncate history when it gets too large:

```python
MAX_HISTORY_TOKENS = 100_000

def maybe_truncate_history(
    history: list[dict],
    current_tokens: int,
) -> list[dict]:
    if current_tokens < MAX_HISTORY_TOKENS:
        return history

    # Keep system messages and recent turns, drop middle
    system_msgs = [m for m in history if m.get("role") == "system"]
    recent = history[-10:]  # Last 10 messages
    return system_msgs + recent
```

---

## Usage Limits and Credit Tracking

### PydanticAI UsageLimits

Prevent runaway agents with built-in limits:

```python
from pydantic_ai import UsageLimits

result = agent.run_sync(
    prompt,
    deps=deps,
    usage_limits=UsageLimits(
        request_limit=25,       # Max model round-trips
        tool_calls_limit=50,    # Max successful tool executions
        response_tokens_limit=16_000,  # Max output tokens
    ),
)
```

### Application-Level Usage Tracking

Track per-user or per-tenant usage alongside the agent run. The orchestrator
(not individual tools) should own usage accounting:

```python
class ChatAgent:
    USAGE_CAP_PER_RUN = 3.0

    def __init__(self, ...):
        self._usage_consumed = 0.0

    def process_input(self, prompt: str) -> AgentResult:
        try:
            result = self.agent.run_sync(prompt, deps=self.deps)

            # Tools may report usage in their results
            for tool_result in extract_tool_results(result):
                if tool_result.get("credits_consumed"):
                    self._usage_consumed += tool_result["credits_consumed"]

            # Cap total usage
            base_cost = 1.0  # Base cost per response
            total = self._usage_consumed + base_cost
            actual_charge = min(total, self.USAGE_CAP_PER_RUN)

            self.tracker.track_usage(credits=actual_charge)
            return AgentResult(output=result.output)

        except Exception:
            # Error responses cost nothing
            self.tracker.track_usage(credits=0)
            raise
```

### Rule: Tools Report, Orchestrator Charges

Tools can include `credits_consumed` in their return value, but must never call
the usage tracker directly. This prevents double-charging and keeps billing
logic in one place:

```python
# Good: tool reports, orchestrator charges
@agent.tool
def research_data(ctx: RunContext, query: str) -> dict:
    result = expensive_api_call(query)
    return {"data": result, "credits_consumed": 1.0}

# Bad: tool charges directly
@agent.tool
def research_data(ctx: RunContext, query: str) -> dict:
    result = expensive_api_call(query)
    tracker.track_usage(credits=1.0)  # Don't do this
    return {"data": result}
```

---

## Error Handling

### Structured Error Handling in the Orchestrator

```python
from pydantic_ai import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.models.fallback import FallbackExceptionGroup

def process_input(self, prompt: str) -> AgentResult:
    try:
        result = self.agent.run_sync(prompt, deps=self.deps)
        return AgentResult(status="complete", output=result.output)

    except UsageLimitExceeded:
        # Agent hit request/token/tool limit - not a bug
        return AgentResult(
            status="complete",
            output="I've reached the limit for this request. "
                   "Please try a simpler question.",
        )

    except FallbackExceptionGroup:
        # Both primary and fallback models failed
        capture_exception()
        return AgentResult(
            status="error",
            output="Our AI services are temporarily unavailable.",
        )

    except UnexpectedModelBehavior as exc:
        # Model tried to call non-existent tool, invalid output, etc.
        capture_exception(exc)
        return AgentResult(
            status="error",
            output="Something went wrong. Please try again.",
        )

    except Exception as exc:
        capture_exception(exc)
        return AgentResult(
            status="error",
            output="An unexpected error occurred.",
        )
```

### Token Limit Errors

LLM providers raise errors when prompts exceed context limits. These are not
bugs - don't send them to error tracking:

```python
TOKEN_LIMIT_MARKERS = [
    "prompt is too long",
    "context length exceeded",
    "maximum context length",
    "token limit",
]

def is_token_limit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in TOKEN_LIMIT_MARKERS)

# In error handler
except Exception as exc:
    if not is_token_limit_error(exc):
        capture_exception(exc)
    return AgentResult(status="error", output=friendly_error(exc))
```

### Capturing Messages on Error

Use `capture_run_messages` to preserve the full conversation when errors occur.
This is invaluable for debugging:

```python
from pydantic_ai import capture_run_messages

with capture_run_messages() as messages:
    try:
        result = agent.run_sync(prompt, deps=deps)
    except Exception as exc:
        # messages contains the full request/response history
        logger.error(
            "Agent failed. Messages: %s",
            json.dumps([m.model_dump() for m in messages]),
        )
        raise
```

---

## Observability

### Logfire Integration

PydanticAI has first-class Logfire support for tracing agent runs:

```python
import logfire

logfire.configure()
logfire.instrument_pydantic_ai()

# Every run now produces traces showing:
# - Messages exchanged with the model
# - Tool calls with arguments and return values
# - Token usage per request and cumulative
# - Latency for each operation
# - Errors with full context
```

### Custom Telemetry

For environments without Logfire, build your own telemetry layer:

```python
@dataclass
class AgentTrace:
    trace_id: str
    started_at: datetime
    completed_at: datetime | None = None
    status: str = "running"
    attempts: list[AttemptRecord] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    total_tokens: int = 0
    total_cost: float = 0.0

class TelemetryHook(ToolHook):
    def __init__(self, trace: AgentTrace):
        self.trace = trace

    def before_execution(self, tool_name: str, args: dict):
        self.current_call = ToolCallRecord(
            tool_name=tool_name,
            started_at=datetime.now(),
            arguments=args,
        )

    def after_execution(self, tool_name: str, result: Any):
        self.current_call.completed_at = datetime.now()
        self.current_call.result_summary = summarize(result)
        self.trace.tool_calls.append(self.current_call)

    def on_error(self, tool_name: str, exc: Exception):
        self.current_call.error = str(exc)
        self.trace.tool_calls.append(self.current_call)
```

### Tagging AI-Generated Code Errors

When agents generate and execute code, tag errors distinctly from system bugs
so they can be triaged separately:

```python
# Sentry example
with sentry_sdk.push_scope() as scope:
    scope.set_tag("error_source", "ai_generated_code")
    scope.set_tag("agent_type", "data_analyst")
    scope.set_context("ai_script", {
        "script": generated_script,
        "attempt": attempt_number,
    })
    sentry_sdk.capture_exception(exc)

# Generic structured logging example
logger.error(
    "AI-generated code failed",
    extra={
        "error_source": "ai_generated_code",
        "agent_type": "data_analyst",
        "script": generated_script,
        "attempt": attempt_number,
    },
)
```

---

## Multi-Agent Patterns

### Agent Delegation via Tools

One agent delegates to another by calling it as a tool:

```python
analyst_agent = Agent(
    "anthropic:claude-haiku-4-5",
    output_type=AnalysisResult,
    instructions="Analyze data and return structured results.",
)

@chat_agent.tool
async def run_analysis(
    ctx: RunContext[dict], query: str
) -> str:
    result = await analyst_agent.run(
        query,
        deps=ctx.deps,
        usage=ctx.usage,  # Aggregate usage across agents
    )
    return result.output.summary
```

Pass `usage=ctx.usage` to aggregate token counts across the parent and
delegate agents.

### Programmatic Hand-Off

Run agents sequentially with application code controlling the flow:

```python
# Step 1: Research agent generates a query
research_result = await research_agent.run(
    user_prompt,
    deps=research_deps,
    usage_limits=UsageLimits(request_limit=12),
)

# Step 2: Execute the generated query
query_output = execute_query(research_result.output.script)

# Step 3: Analysis agent interprets results
analysis_result = await analysis_agent.run(
    f"Interpret these results:\n{query_output}",
    deps=analysis_deps,
)

return analysis_result.output
```

### Generator Pattern for Progress Reporting

Use Python generators to yield intermediate state from multi-step agents:

```python
def process_input(self, prompt: str) -> Generator[AgentState, None, None]:
    state = AgentState(status="connecting")
    yield state

    state.status = "generating_query"
    yield state

    query = self.generate_query(prompt)
    state.generated_query = query
    state.status = "executing"
    yield state

    result = self.execute_query(query)
    state.result = result
    state.status = "complete"
    yield state
```

The caller iterates and publishes each state to the client:

```python
for state in agent.process_input(prompt):
    publish_progress(message_id, state=state)
```

---

## Durable Execution

For tasks that may run for hours or need to survive server restarts, use
PydanticAI's Temporal integration:

```python
from pydantic_ai import Agent
from pydantic_ai.durable_exec.temporal import TemporalAgent

agent = Agent("anthropic:claude-sonnet-4-5", name="data-analyst")
temporal_agent = TemporalAgent(agent)

@workflow.defn
class AnalysisWorkflow:
    @workflow.run
    async def run(self, prompt: str) -> str:
        result = await temporal_agent.run(prompt)
        return result.output
```

Temporal automatically:
- Persists state to a database after each step
- Replays completed steps on resume (no re-execution)
- Handles retries with configurable policies
- Supports workflows that run for days or weeks

**When to use Temporal vs simple background tasks:**

| Concern | Background Task (Celery/Q2/Dramatiq) | Temporal |
|---|---|---|
| Duration | Minutes | Hours to days |
| Failure recovery | Restart from scratch | Resume from last checkpoint |
| State persistence | Manual (DB saves) | Automatic |
| Complexity | Low | Medium-high |
| Infrastructure | Redis/RabbitMQ | Temporal server cluster |

For most web applications, a simple background task with good error handling is
sufficient. Reach for Temporal when tasks genuinely need to survive restarts or
run for extended periods.

---

## Anti-Patterns

### Running Agents in Request Handlers

```python
# Bad: blocks the web server
@app.post("/chat")
async def chat(request: ChatRequest):
    result = await agent.run(request.prompt)  # 30s+ blocking
    return {"output": result.output}

# Good: enqueue and return immediately
@app.post("/chat")
async def chat(request: ChatRequest):
    task_id = enqueue_task(request.conversation_id, request.prompt)
    return {"task_id": task_id, "status": "processing"}
```

### Spawning Sub-Tasks for Tool Calls

```python
# Bad: unnecessary complexity, breaks agent flow
@agent.tool
def analyze(ctx: RunContext, query: str) -> str:
    task_id = enqueue("run_analysis", query)
    return wait_for_task(task_id)  # Blocks anyway

# Good: run synchronously within the agent
@agent.tool
def analyze(ctx: RunContext, query: str) -> str:
    return run_analysis(query)
```

### Bypassing ORM Save Hooks

```python
# Bad: bypasses save hooks/signals, breaks real-time updates
Message.objects.filter(id=msg_id).update(status="complete")  # Django
session.execute(update(Message).where(...).values(status="complete"))  # SQLAlchemy

# Good: triggers save hooks
msg = Message.objects.get(id=msg_id)  # Django
msg.status = "complete"
msg.save(update_fields=["status"])

msg = session.get(Message, msg_id)  # SQLAlchemy
msg.status = "complete"
session.commit()
```

### Tools Setting Message Status

```python
# Bad: tools fighting over status
@agent.tool
def research(ctx: RunContext, query: str) -> dict:
    update_message(msg_id, status="researching")  # Don't
    result = do_research(query)
    update_message(msg_id, status="complete")      # Don't
    return result

# Good: tools return content, orchestrator owns status
@agent.tool
def research(ctx: RunContext, query: str) -> dict:
    return {"data": do_research(query), "row_count": 42}
```

### Forgetting to Strip Instructions from History

```python
# Bad: history grows exponentially as instructions repeat
conversation.history = [m.model_dump() for m in result.new_messages()]

# Good: strip instructions before saving
conversation.history = clean_history(
    [m.model_dump() for m in result.new_messages()]
)
```

### Charging Usage in Tools

```python
# Bad: double-charging risk, distributed billing logic
@agent.tool
def expensive_query(ctx: RunContext, q: str) -> dict:
    tracker.charge(1.0)  # Tool charges
    return run_query(q)
# ... orchestrator also charges for the same tool

# Good: tools report, orchestrator charges once
@agent.tool
def expensive_query(ctx: RunContext, q: str) -> dict:
    return {"data": run_query(q), "credits_consumed": 1.0}
```

### No Usage Limits

```python
# Bad: agent can loop forever
result = agent.run_sync(prompt)

# Good: cap model round-trips and tool calls
result = agent.run_sync(
    prompt,
    usage_limits=UsageLimits(request_limit=25, tool_calls_limit=50),
)
```
