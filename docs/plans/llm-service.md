---
status: Ready
type: feature
appetite: Small
owner: Valor
created: 2026-02-14
tracking: https://github.com/yudame/cuttlefish/issues/63
---

# LLM Service: Abstract Interface for AI Model Calls

## Problem

The podcast workflow makes 10+ Claude API calls (question discovery, cross-validation, briefing writer, synthesis writer, episode planner, metadata writer, chapter generation). Each needs: prompt template loading, retry on transient errors, token usage tracking, and consistent error handling. Duplicating this in every service function is wasteful.

**Current behavior:**
Each tool/service makes direct Anthropic API calls with ad-hoc error handling.

**Desired outcome:**
A unified LLM call module that handles retries, token tracking, and prompt templates — callers just describe what they want.

## Appetite

**Size:** Small

**Team:** Solo dev. Clean abstraction with retry logic and template loading.

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

None - this is a foundational service.

## Solution

### Key Elements

- **Simple call interface**: `call_llm()` for direct calls, `call_llm_with_prompt_template()` for templated calls
- **Automatic retry**: Exponential backoff on rate limits (429) and server errors (500, 529)
- **Token tracking**: Log usage per call, optionally store for cost analysis
- **Model flexibility**: Default to Claude Sonnet, allow per-call override (Opus for complex tasks, Haiku for cheap tasks)

### Technical Approach

1. **Create LLM service module at `apps/ai/services/llm.py`:**

   ```python
   import logging
   import time
   from pathlib import Path
   from typing import Any

   import anthropic
   from django.conf import settings

   logger = logging.getLogger(__name__)

   # Default configuration
   DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
   DEFAULT_MAX_TOKENS = 4096
   DEFAULT_TEMPERATURE = 0.7
   MAX_RETRIES = 3
   RETRY_BASE_DELAY = 1.0

   # Prompt template directory
   PROMPT_TEMPLATE_DIR = Path(settings.BASE_DIR) / "apps/podcast/services/prompts"

   def _get_client() -> anthropic.Anthropic:
       """Get Anthropic client with API key from settings."""
       return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

   def _should_retry(error: Exception) -> bool:
       """Check if error is retryable."""
       if isinstance(error, anthropic.RateLimitError):
           return True
       if isinstance(error, anthropic.APIStatusError):
           return error.status_code in (500, 502, 503, 529)
       return False

   def call_llm(
       prompt: str,
       context: str | list[str] = "",
       system_prompt: str = "",
       model: str = DEFAULT_MODEL,
       max_tokens: int = DEFAULT_MAX_TOKENS,
       temperature: float = DEFAULT_TEMPERATURE,
   ) -> str:
       """
       Call an LLM and return the text response.

       Args:
           prompt: The user prompt/question
           context: Additional context (string or list of strings)
           system_prompt: System instructions
           model: Model identifier (default: claude-sonnet-4-5-20250929)
           max_tokens: Maximum response tokens
           temperature: Sampling temperature

       Returns:
           The model's text response

       Raises:
           anthropic.APIError: After max retries exhausted
       """
       client = _get_client()

       # Build messages
       messages = []

       # Add context as user message if provided
       if context:
           if isinstance(context, list):
               context = "\n\n".join(context)
           messages.append({"role": "user", "content": f"Context:\n{context}"})
           messages.append({"role": "assistant", "content": "I've reviewed the context. What would you like me to do?"})

       # Add main prompt
       messages.append({"role": "user", "content": prompt})

       # Retry loop
       last_error = None
       for attempt in range(MAX_RETRIES):
           try:
               response = client.messages.create(
                   model=model,
                   max_tokens=max_tokens,
                   temperature=temperature,
                   system=system_prompt if system_prompt else anthropic.NOT_GIVEN,
                   messages=messages,
               )

               # Log token usage
               usage = response.usage
               logger.info(
                   f"LLM call: model={model} input_tokens={usage.input_tokens} "
                   f"output_tokens={usage.output_tokens}"
               )

               # Extract text response
               return response.content[0].text

           except Exception as e:
               last_error = e
               if _should_retry(e) and attempt < MAX_RETRIES - 1:
                   delay = RETRY_BASE_DELAY * (2 ** attempt)
                   logger.warning(f"Retrying LLM call after {delay}s: {e}")
                   time.sleep(delay)
               else:
                   raise

       raise last_error

   def call_llm_with_prompt_template(
       template_name: str,
       context: dict[str, Any],
       **kwargs,
   ) -> str:
       """
       Load a prompt template by name, format with context, call LLM.

       Args:
           template_name: Name of template file (without .txt extension)
           context: Dict of variables to substitute in template
           **kwargs: Additional arguments passed to call_llm()

       Returns:
           The model's text response
       """
       template_path = PROMPT_TEMPLATE_DIR / f"{template_name}.txt"

       if not template_path.exists():
           raise FileNotFoundError(f"Prompt template not found: {template_path}")

       template = template_path.read_text()
       prompt = template.format(**context)

       return call_llm(prompt, **kwargs)

   # Model shortcuts for common use cases
   def call_haiku(prompt: str, **kwargs) -> str:
       """Quick/cheap calls using Haiku."""
       return call_llm(prompt, model="claude-3-5-haiku-20241022", **kwargs)

   def call_opus(prompt: str, **kwargs) -> str:
       """Complex reasoning calls using Opus."""
       return call_llm(prompt, model="claude-sonnet-4-5-20250929", **kwargs)
   ```

2. **Create prompt template directory:**

   ```
   apps/podcast/services/prompts/
   ├── question_discovery.txt
   ├── cross_validation.txt
   ├── briefing_synthesis.txt
   ├── episode_planning.txt
   ├── metadata_generation.txt
   └── chapter_generation.txt
   ```

3. **Add settings configuration:**

   ```python
   # settings/base.py
   ANTHROPIC_API_KEY = env.str("ANTHROPIC_API_KEY", default="")

   # Optional: for cost tracking
   LLM_LOG_USAGE = env.bool("LLM_LOG_USAGE", default=True)
   ```

4. **Write tests in `apps/ai/tests/test_llm_service.py`:**

   ```python
   import pytest
   from unittest.mock import Mock, patch
   from apps.ai.services.llm import call_llm, call_llm_with_prompt_template

   @pytest.fixture
   def mock_anthropic():
       with patch('apps.ai.services.llm._get_client') as mock:
           client = Mock()
           response = Mock()
           response.content = [Mock(text="Test response")]
           response.usage = Mock(input_tokens=100, output_tokens=50)
           client.messages.create.return_value = response
           mock.return_value = client
           yield client

   def test_call_llm_basic(mock_anthropic):
       result = call_llm("What is 2+2?")
       assert result == "Test response"
       mock_anthropic.messages.create.assert_called_once()

   def test_call_llm_with_context(mock_anthropic):
       result = call_llm("Summarize this", context="Long document here")
       assert result == "Test response"
       call_args = mock_anthropic.messages.create.call_args
       messages = call_args.kwargs['messages']
       assert len(messages) == 3  # context + ack + prompt

   def test_call_llm_with_system_prompt(mock_anthropic):
       result = call_llm("Hello", system_prompt="You are helpful")
       call_args = mock_anthropic.messages.create.call_args
       assert call_args.kwargs['system'] == "You are helpful"

   def test_retry_on_rate_limit(mock_anthropic):
       import anthropic
       mock_anthropic.messages.create.side_effect = [
           anthropic.RateLimitError("rate limited", response=Mock(), body={}),
           Mock(content=[Mock(text="Success")], usage=Mock(input_tokens=10, output_tokens=5))
       ]

       with patch('time.sleep'):  # Don't actually sleep
           result = call_llm("test")

       assert result == "Success"
       assert mock_anthropic.messages.create.call_count == 2

   def test_template_loading(mock_anthropic, tmp_path, settings):
       # Create temp template
       template_dir = tmp_path / "prompts"
       template_dir.mkdir()
       (template_dir / "test_template.txt").write_text("Hello {name}!")

       with patch('apps.ai.services.llm.PROMPT_TEMPLATE_DIR', template_dir):
           result = call_llm_with_prompt_template("test_template", {"name": "World"})

       call_args = mock_anthropic.messages.create.call_args
       messages = call_args.kwargs['messages']
       assert "Hello World!" in messages[-1]['content']
   ```

### File Changes

| File | Action | Description |
|------|--------|-------------|
| `apps/ai/services/__init__.py` | Create | Empty init |
| `apps/ai/services/llm.py` | Create | LLM service module |
| `apps/ai/tests/test_llm_service.py` | Create | Unit tests |
| `apps/podcast/services/prompts/` | Create | Prompt template directory |
| `settings/base.py` | Modify | Add ANTHROPIC_API_KEY setting |

## Rabbit Holes

- **Don't add OpenAI support yet** - We use Claude; add other providers when needed
- **Don't add streaming** - Podcast tools don't need streaming responses
- **Don't persist token usage to DB** - Logging is sufficient for now; add DB storage when we need cost reports

## No-Gos

- No async support (sync is fine for background tasks)
- No conversation memory (each call is independent)
- No function calling (not needed for text generation tasks)

## Acceptance Criteria

- [ ] Module importable: `from apps.ai.services.llm import call_llm`
- [ ] Retry logic for Anthropic rate limits (429) and server errors (500, 529)
- [ ] Prompt template loading from file path
- [ ] Token usage tracked (logged)
- [ ] Tests pass with mocked API responses
