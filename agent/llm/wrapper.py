"""Typed PydanticAI call wrapper for non-harness LLM calls (#1925).

Every non-harness LLM call (classification, extraction, judging) should
route through ``run_typed`` instead of hand-rolling an ``anthropic``
client. The caller declares a typed ``output_type`` (a ``pydantic.BaseModel``
subclass) and gets a schema-validated instance back, with PydanticAI's
built-in single auto-retry on schema mismatch.

Event-loop safety invariant (hotfix #1055 / #1111), reconciled with
PydanticAI (see ``docs/plans/pydantic-ai-nonharness-llm-standardization.md``
Spike Results spike-1): ``agent/anthropic_client.py`` holds no long-lived
shared client -- ``semaphore_slot()`` only gates concurrency, the caller
constructs its own client. ``run_typed`` follows that pattern **per call**:

1. ``async with semaphore_slot():`` -- hold the shared semaphore for the
   *entire* ``Agent.run()`` call (not just client construction), matching
   how ``agent/memory_extraction.py::_llm_call`` uses the slot today.
2. Inside the slot, construct a **fresh**
   ``async with anthropic.AsyncAnthropic(api_key=..., timeout=sdk_timeout)``
   -- per-call, per-site timeout; ``async with`` preserves hotfix #1055's
   httpx cleanup.
3. Inject that client into PydanticAI:
   ``AnthropicProvider(anthropic_client=client)`` ->
   ``AnthropicModel(model, provider=...)`` ->
   ``Agent(model, output_type=output_type)``.
4. When ``hard_timeout`` is not ``None``, wrap ``await agent.run(prompt)``
   in ``asyncio.wait_for(..., timeout=hard_timeout)`` for an outer
   wall-clock cap regardless of the SDK-level ``timeout`` kwarg.
5. The slot is released on ``__aexit__`` (automatic via ``async with``).

Fail-safe posture: this wrapper does NOT implement a fail-safe default.
Provider errors and exhausted schema-validation retries are logged, then
re-raised as :class:`LLMCallError`. Each call site owns its own
conservative default (respond / escalate / send / skip) on failure -- see
"Preserve fail-safe posture per site" in the plan's Solution section.
"""

from __future__ import annotations

import asyncio
import logging

import anthropic
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from agent.anthropic_client import semaphore_slot
from config.models import MODEL_FAST
from utils.api_keys import get_anthropic_api_key

logger = logging.getLogger(__name__)

# Mirrors agent/memory_extraction.py's double-timeout constants (hotfix #1055):
# the SDK-level timeout lets httpx/anthropic raise a typed error first for
# cleaner logs; the outer hard timeout fires even on half-open sockets where
# the SDK timer never gets a socket event to fire on.
DEFAULT_SDK_TIMEOUT = 30.0
DEFAULT_HARD_TIMEOUT = 35.0


class LLMCallError(Exception):
    """Raised when ``run_typed`` cannot produce a validated output.

    Wraps the underlying PydanticAI/Anthropic exception (available via
    ``__cause__``) after it has already been logged. Callers apply their
    own site-specific conservative default on this exception -- the
    wrapper deliberately does not pick one for them.
    """


async def run_typed(
    prompt: str,
    output_type: type[BaseModel],
    *,
    model: str = MODEL_FAST,
    sdk_timeout: float = DEFAULT_SDK_TIMEOUT,
    hard_timeout: float | None = DEFAULT_HARD_TIMEOUT,
) -> BaseModel:
    """Run a schema-validated LLM call through PydanticAI.

    Args:
        prompt: the user prompt. Must be non-empty and not
            whitespace-only -- validated before any client/network work,
            so a bad prompt fails fast with no LLM call and no hang.
        output_type: a ``pydantic.BaseModel`` subclass describing the
            desired structured output. PydanticAI validates the model's
            response against this schema and auto-retries once on
            mismatch before raising.
        model: the model id to call. Defaults to ``config.models.MODEL_FAST``
            (Haiku) so a single config edit swaps every non-harness call's
            model. Per-call overrides are supported (e.g. a cheaper/local
            model for a high-frequency hot path).
        sdk_timeout: per-call SDK-level timeout (seconds), passed to
            ``anthropic.AsyncAnthropic(timeout=...)``. This is the inner
            timer of the hotfix #1055 double-timeout pattern.
        hard_timeout: outer wall-clock cap (seconds) via
            ``asyncio.wait_for``. Fires even when the SDK timer doesn't
            (e.g. half-open TCP sockets with no socket event). Pass
            ``None`` to disable the outer cap and rely on ``sdk_timeout``
            alone.

    Returns:
        A validated instance of ``output_type``.

    Raises:
        ValueError: ``prompt`` is empty, ``None``, or whitespace-only.
        LLMCallError: the provider call failed, or PydanticAI's schema
            validation retries were exhausted. The original exception is
            logged and chained as ``__cause__``.
    """
    if not prompt or not prompt.strip():
        raise ValueError("run_typed requires a non-empty, non-whitespace prompt")

    async with semaphore_slot():
        async with anthropic.AsyncAnthropic(
            api_key=get_anthropic_api_key(), timeout=sdk_timeout
        ) as client:
            provider = AnthropicProvider(anthropic_client=client)
            pydantic_model = AnthropicModel(model, provider=provider)
            agent = Agent(pydantic_model, output_type=output_type)

            try:
                if hard_timeout is not None:
                    result = await asyncio.wait_for(agent.run(prompt), timeout=hard_timeout)
                else:
                    result = await agent.run(prompt)
            except TimeoutError as e:
                logger.error(
                    "[agent.llm] hard timeout (%.1fs) exceeded for model=%s: %s",
                    hard_timeout,
                    model,
                    e,
                )
                raise LLMCallError(
                    f"run_typed exceeded hard_timeout of {hard_timeout}s for model={model}"
                ) from e
            except Exception as e:
                logger.error(
                    "[agent.llm] provider error or schema-validation exhaustion for model=%s: %s",
                    model,
                    e,
                    exc_info=True,
                )
                raise LLMCallError(f"run_typed failed for model={model}: {e}") from e

    return result.output
