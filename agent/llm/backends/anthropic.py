"""The Anthropic (subscription) leg of :func:`agent.llm.run_typed`.

Event-loop safety invariant (hotfix #1055 / #1111), reconciled with
PydanticAI: ``agent/anthropic_client.py`` holds no long-lived shared
client, ``semaphore_slot()`` only gates concurrency, and this leg builds
its own client **per call**:

1. ``async with semaphore_slot(timeout=slot_timeout):`` holds the shared
   slot for the *entire* ``Agent.run()`` call, matching
   ``agent/memory_extraction.py::_llm_call``. A slot wait that outlives
   ``slot_timeout`` raises ``LLMCallError(reason="slot_timeout")`` before
   any client exists.
2. Inside the slot, the deadline re-check (fallback calls only): under
   0.5 s of remainder raises ``LLMCallError(reason="timeout")`` with the
   constructor never entered; otherwise the request timer shrinks to what
   is left, so the slot wait and the request cannot each spend the whole
   remainder (Race 1).
3. A **fresh** ``async with stack.anthropic.AsyncAnthropic(api_key=...,
   timeout=sdk_timeout, max_retries=...)``: the SDK-level timer is the
   only timer around the live request, and ``async with`` preserves
   hotfix #1055's httpx cleanup. ``max_retries=None`` leaves the SDK
   default (2 on anthropic 1.7.0); the fallback leg passes 0 so one timer
   bounds one attempt.
4. That client is injected into PydanticAI:
   ``AnthropicProvider(anthropic_client=client)`` ->
   ``AnthropicModel(route.model, provider=...)`` ->
   ``Agent(model, output_type=output_type, system_prompt=system)``.
5. The slot is released on ``__aexit__``. ``CancelledError`` (the
   wrapper's ``hard_timeout``) passes through untouched.

The leg publishes how long it waited for the slot through
:data:`slot_wait_ms`, a context variable set the moment the slot is held, so
a caller that audits queue time (``bridge/promise_gate.py``'s
``queue_wait_ms`` column) reads it after ``run_typed`` returns or raises.
It stays at whatever the caller set (``None``) when the slot was never
acquired.

No ``asyncio.wait_for`` appears in this module: the wrapper applies
``hard_timeout`` outside the leg. Import-safety contract (#3001): module
scope here is stdlib and our own code only; every third-party symbol comes
from the ``stack`` the wrapper resolved.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from time import monotonic
from typing import TYPE_CHECKING, Any

from agent.anthropic_client import semaphore_slot
from agent.llm.backends import bound_to_deadline, reason_for
from agent.llm.errors import LLMCallError
from utils.api_keys import get_anthropic_api_key

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pydantic import BaseModel

    from agent.anthropic_client import LLMStack
    from agent.llm.router import Route

logger = logging.getLogger(__name__)

#: Milliseconds this leg's last call in the current context waited for the
#: shared semaphore; ``None`` until a slot is acquired. Callers that audit
#: queue time reset it before ``run_typed`` and read it after.
slot_wait_ms: ContextVar[float | None] = ContextVar("llm_anthropic_slot_wait_ms", default=None)


async def call(
    prompt: str,
    output_type: type[BaseModel],
    route: Route,
    *,
    system: str | None,
    sdk_timeout: float,
    slot_timeout: float | None,
    max_retries: int | None,
    deadline: float | None = None,
    stack: LLMStack,
) -> BaseModel:
    """Run one schema-validated call on ``route.model`` through the shared slot."""
    acquire_start = monotonic()
    try:
        async with semaphore_slot(timeout=slot_timeout):
            slot_wait_ms.set((monotonic() - acquire_start) * 1000)
            sdk_timeout = bound_to_deadline(sdk_timeout, deadline, monotonic(), leg="anthropic")
            client_kwargs: dict[str, Any] = {
                "api_key": get_anthropic_api_key(),
                "timeout": sdk_timeout,
            }
            if max_retries is not None:
                client_kwargs["max_retries"] = max_retries
            async with stack.anthropic.AsyncAnthropic(**client_kwargs) as client:
                provider = stack.AnthropicProvider(anthropic_client=client)
                pydantic_model = stack.AnthropicModel(route.model, provider=provider)
                agent_kwargs: dict[str, Any] = {"output_type": output_type}
                if system is not None:
                    agent_kwargs["system_prompt"] = system
                agent = stack.Agent(pydantic_model, **agent_kwargs)
                try:
                    result = await agent.run(prompt)
                except Exception as e:
                    reason = reason_for(e)
                    logger.error(
                        "[agent.llm] anthropic leg %s for model=%s: %s",
                        reason,
                        route.model,
                        e,
                        exc_info=reason != "timeout",
                    )
                    raise LLMCallError(
                        f"anthropic leg failed ({reason}) for model={route.model}: {e}",
                        reason=reason,
                    ) from e
    except TimeoutError as e:
        # Only the slot wait raises a bare TimeoutError here: the request
        # itself is bounded by the SDK timer and surfaces as a typed provider
        # error, mapped above. The client constructor was never entered.
        logger.error(
            "[agent.llm] anthropic leg slot_timeout (%.1fs) for model=%s", slot_timeout, route.model
        )
        raise LLMCallError(
            f"anthropic leg: no semaphore slot within {slot_timeout}s for model={route.model}",
            reason="slot_timeout",
        ) from e
    return result.output
