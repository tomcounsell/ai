"""The Ollama (local granite) leg of :func:`agent.llm.run_typed`.

Runs a schema-validated PydanticAI call against the local Ollama daemon's
OpenAI-compatible surface (``settings.models.ollama_host`` + ``/v1``). It
differs from the Anthropic leg in three deliberate ways:

* **No Anthropic client and no shared Anthropic semaphore**: the call never
  leaves this machine, so the #1111 concurrency slot does not apply.
  ``slot_timeout`` is accepted for the leg protocol and unused.
* **Per-call ``AsyncOpenAI`` client, closed on exit**: ``async with
  stack.AsyncOpenAI(base_url=..., api_key="ollama", timeout=sdk_timeout,
  max_retries=0) as client`` then ``stack.OllamaProvider(openai_client=
  client)`` inside the block, so no call leaves an unclosed httpx client.
* **One SDK-level timer**: ``timeout=sdk_timeout`` on the client is the only
  timer around the live request (hotfix #1055: no ``asyncio.wait_for``
  around an LLM call); ``max_retries=0`` keeps that one timer bounding one
  attempt regardless of the caller's ``max_retries``. The deadline
  re-check (fallback calls only) runs once before the client is built.

Import-safety contract (#3001): module scope here is stdlib and our own
code only; ``AsyncOpenAI``, ``OllamaProvider``, ``OpenAIChatModel`` and
``Agent`` all come from the ``stack`` the wrapper resolved, so
``dataclasses.replace(real_stack, AsyncOpenAI=Fake)`` on the wrapper's
``_load_stack`` seam is the test seam for this leg.
"""

from __future__ import annotations

import logging
from time import monotonic
from typing import TYPE_CHECKING, Any

from agent.llm.backends import bound_to_deadline, reason_for
from agent.llm.errors import LLMCallError
from config.settings import settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pydantic import BaseModel

    from agent.anthropic_client import LLMStack
    from agent.llm.router import Route

logger = logging.getLogger(__name__)


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
    """Run one schema-validated call on ``route.model`` against local Ollama."""
    sdk_timeout = bound_to_deadline(sdk_timeout, deadline, monotonic(), leg="ollama")
    base_url = f"{settings.models.ollama_host.rstrip('/')}/v1"
    async with stack.AsyncOpenAI(
        base_url=base_url, api_key="ollama", timeout=sdk_timeout, max_retries=0
    ) as client:
        provider = stack.OllamaProvider(openai_client=client)
        pydantic_model = stack.OpenAIChatModel(route.model, provider=provider)
        agent_kwargs: dict[str, Any] = {"output_type": output_type}
        if system is not None:
            agent_kwargs["system_prompt"] = system
        agent = stack.Agent(pydantic_model, **agent_kwargs)
        try:
            result = await agent.run(prompt)
        except Exception as e:
            reason = reason_for(e)
            logger.error(
                "[agent.llm] ollama leg %s for model=%s: %s",
                reason,
                route.model,
                e,
                exc_info=reason != "timeout",
            )
            raise LLMCallError(
                f"ollama leg failed ({reason}) for model={route.model}: {e}", reason=reason
            ) from e
    return result.output
