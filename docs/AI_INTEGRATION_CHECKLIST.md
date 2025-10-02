# AI Integration Checklist

Quick reference checklist for implementing or reviewing AI integrations in the Cuttlefish project.

## Pre-Implementation

- [ ] Read [PydanticAI Integration Guide](PYDANTIC_AI_INTEGRATION.md)
- [ ] Read [AI Conventions](AI_CONVENTIONS.md)
- [ ] Understand the adapter pattern for Django ↔ PydanticAI conversion
- [ ] Plan directory structure following conventions

## Model Design

- [ ] Django models in `apps/ai/models/` (database layer)
- [ ] PydanticAI models in `apps/ai/agent/` (in-memory layer)
- [ ] PydanticAI models use `Agent` prefix (e.g., `AgentChatSession` not `ChatSession`)
- [ ] Adapters in `apps/ai/adapters/` for conversion
- [ ] Clear naming to avoid confusion between Django and PydanticAI models

## Agent Configuration

- [ ] LLM provider configuration in `apps/ai/llm/providers.py`
- [ ] Model name validation against allowed list
- [ ] API keys passed as parameters, NOT stored in `os.environ`
- [ ] Default model from settings, not hardcoded
- [ ] System prompt defined ONCE (decorator OR constructor, not both)

## Tool Development

- [ ] Tools in separate files under `apps/ai/agent/tools/`
- [ ] Only ONE implementation per tool (no duplicate simple/advanced versions)
- [ ] Code execution tools properly sandboxed (use RestrictedPython or containers)
- [ ] Input validation with Pydantic models
- [ ] Tool execution logged for auditing
- [ ] Security review for any code execution or file access tools

## Django Integration

- [ ] Use Django's `async_to_sync` / `sync_to_async` utilities
- [ ] NO custom event loop handling
- [ ] Adapter pattern used in all views
- [ ] Result accessed via `result.output` (standard PydanticAI attribute)
- [ ] Background tasks for long-running operations (Celery/Django-Q)

## Error Handling

- [ ] Custom exception classes defined (`AIConfigurationError`, `AIProviderError`, etc.)
- [ ] Errors classified and handled appropriately in views
- [ ] User-friendly error messages (no internal details exposed)
- [ ] Comprehensive logging with structured data
- [ ] Unexpected errors logged with full traceback

## Testing

- [ ] Unit tests for agent logic (mocked LLM)
- [ ] Integration tests for Django ↔ PydanticAI flow
- [ ] Tool execution tests with various inputs
- [ ] Error handling tests for all error types
- [ ] Test coverage ≥80% for new code

## Security

- [ ] Code execution sandboxed (no unrestricted `exec()`)
- [ ] Blocked imports list for dangerous modules
- [ ] Input validation for all tool parameters
- [ ] Rate limiting implemented
- [ ] Usage limits per user/organization
- [ ] Audit logging for tool execution

## Production Readiness

- [ ] Background task processing configured
- [ ] Rate limiting middleware added
- [ ] Usage tracking and cost calculation
- [ ] Caching for deterministic queries (temperature=0)
- [ ] Response streaming for better UX (if applicable)
- [ ] Monitoring and alerting configured
- [ ] Error tracking (Sentry/etc.) integrated

## Documentation

- [ ] Code comments for complex logic
- [ ] Docstrings for all public functions
- [ ] README updated with new features
- [ ] API documentation for new endpoints
- [ ] Example usage in docs or tests

## Code Quality

- [ ] Black formatting applied
- [ ] isort for imports
- [ ] Type hints for all functions
- [ ] No dead code (unused imports, variables, functions)
- [ ] Pre-commit hooks passing
- [ ] No TODOs or FIXMEs without tickets

## Common Pitfalls to Avoid

- [ ] ❌ NOT using duplicate model names (`ChatSession` for both Django and PydanticAI)
- [ ] ❌ NOT defining system prompt twice (constructor + decorator)
- [ ] ❌ NOT using custom event loop handling
- [ ] ❌ NOT guessing result attributes (use `result.output`)
- [ ] ❌ NOT mutating `os.environ` for API keys
- [ ] ❌ NOT having multiple tool implementations
- [ ] ❌ NOT using unrestricted `exec()` for code execution
- [ ] ❌ NOT exposing raw error messages to users
- [ ] ❌ NOT hardcoding model names

## Review Questions

**Models:**
- Are PydanticAI models clearly named with `Agent` prefix?
- Is there an adapter pattern for conversion?
- Are there any naming conflicts?

**Configuration:**
- Is the model name configurable?
- Are API keys passed as parameters?
- Is there only one system prompt definition?

**Async Handling:**
- Are Django's async utilities used?
- Is there any custom event loop code?

**Tools:**
- Are all tools properly sandboxed?
- Is there only one implementation per tool?
- Are inputs validated?

**Error Handling:**
- Are errors properly classified?
- Are user messages safe and helpful?
- Is everything logged appropriately?

**Security:**
- Can code execution escape sandbox?
- Are there rate limits?
- Are usage limits enforced?

**Testing:**
- Are all code paths tested?
- Are error cases covered?
- Do integration tests exist?

---

## Quick Reference

**Documentation:**
- [PydanticAI Integration Guide](PYDANTIC_AI_INTEGRATION.md) - Detailed patterns and best practices
- [AI Conventions](AI_CONVENTIONS.md) - General AI integration patterns
- [Architecture](ARCHITECTURE.md) - Overall system architecture
- [CLAUDE.md](../CLAUDE.md) - Quick reference for Claude Code

**Key Files:**
- `apps/ai/agent/` - PydanticAI agent logic
- `apps/ai/adapters/` - Django ↔ PydanticAI conversion
- `apps/ai/llm/providers.py` - LLM provider configuration
- `apps/ai/models/` - Django models
- `apps/ai/views/` - Django views

**Commands:**
```bash
# Run AI tests
DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/ -v

# Format code
black apps/ai/ && isort apps/ai/ --profile black

# Type check
pyright apps/ai/
```
