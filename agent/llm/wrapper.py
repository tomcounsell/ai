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

Import-safety contract (#3001): module scope here is **stdlib and our own
code only**. Every third-party LLM-stack symbol (``anthropic``,
``pydantic_ai.*``) is resolved through
:func:`agent.anthropic_client._load_stack`, the one memoized loader, and
only from inside the call paths below. A machine with a broken or missing
stack can still ``import agent.llm`` (and therefore
``import bridge.telegram_bridge``); the failure surfaces at the call, where
it can be reported. ``_load_stack`` is imported into this module's
namespace, so ``monkeypatch.setattr(wrapper_mod, "_load_stack", ...)`` is
the network-isolation seam for tests -- it replaces the old
``wrapper_mod.OpenAIChatModel`` seam, which no longer exists because
``OpenAIChatModel`` is not a module attribute of anything here.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel

from agent.anthropic_client import _load_stack, semaphore_slot
from config.models import MODEL_FAST, OLLAMA_CLASSIFIER_MODEL
from config.settings import settings
from utils.api_keys import get_anthropic_api_key

logger = logging.getLogger(__name__)

# Mirrors agent/memory_extraction.py's double-timeout constants (hotfix #1055):
# the SDK-level timeout lets httpx/anthropic raise a typed error first for
# cleaner logs; the outer hard timeout fires even on half-open sockets where
# the SDK timer never gets a socket event to fire on.
#
# Sourced from settings.timeouts.anthropic_sdk_s / anthropic_hard_s (issue
# #1968) -- these two fields are the single source of truth for BOTH this
# module's constants and agent/memory_extraction.py's
# `_EXTRACTION_SDK_TIMEOUT` / `_EXTRACTION_HARD_TIMEOUT`, which previously
# duplicated the same 30.0/35.0 pair verbatim. Preserve the two-timer
# structure -- never collapse to one value.
DEFAULT_SDK_TIMEOUT = settings.timeouts.anthropic_sdk_s
DEFAULT_HARD_TIMEOUT = settings.timeouts.anthropic_hard_s


class LLMCallError(Exception):
    """Raised when ``run_typed`` cannot produce a validated output.

    Wraps the underlying PydanticAI/Anthropic exception (available via
    ``__cause__``) after it has already been logged. Callers apply their
    own site-specific conservative default on this exception -- the
    wrapper deliberately does not pick one for them.
    """


# N818 (Error suffix) is waived: the name is fixed by #3001's plan and its
# verification greps, and it inherits the suffix-free house style of
# LLMCallError, the class every call site already catches.
class LLMStackIncompatible(LLMCallError):  # noqa: N818
    """Raised when this process's LLM stack is degraded (#3001).

    A subclass of :class:`LLMCallError` on purpose: every existing
    ``except LLMCallError`` fail-safe keeps working unchanged, so a
    degraded stack degrades each call site to its own conservative default
    instead of surfacing a raw provider ``TypeError`` from deep inside
    ``pydantic_ai``. The alert has already fired from
    ``agent.llm.compat.resolve_degraded_flag`` by the time this is raised.
    """


def _guard_stack(caller: str, *, signature_axis: bool) -> None:
    """Fail fast on a degraded stack, forcing flag resolution on first use.

    Resolving here is what makes the alert unmissable in a process that
    never ran a startup hook: the first call *is* the first read, and the
    resolver alerts on the transition.

    ``signature_axis`` is ``False`` for the local granite leg, which never
    touches ``anthropic`` -- an Anthropic create-signature break must not
    fall the two hot-path classifiers over.

    ``stack_axes`` is imported here rather than at module scope so that
    importing the ``agent.llm`` package does not import
    ``agent.llm.compat``. `python -m agent.llm.compat` -- the argv the
    update gate and `verify.py` both run -- imports the package before
    executing the module, and a pre-imported ``compat`` makes runpy emit a
    "found in sys.modules" RuntimeWarning onto stderr that is quoted
    verbatim into operator-facing gate output.
    """
    from agent.llm.compat import stack_axes  # noqa: PLC0415

    loader_ok, compatible = stack_axes()
    if not loader_ok:
        raise LLMStackIncompatible(
            f"{caller} refused: the LLM stack failed to import "
            "(see the LLM_STACK_COMPAT critical log for the reason)"
        )
    if signature_axis and not compatible:
        raise LLMStackIncompatible(
            f"{caller} refused: the installed anthropic + pydantic-ai pair is "
            "incompatible (see the LLM_STACK_COMPAT critical log for the reason)"
        )


async def run_typed(
    prompt: str,
    output_type: type[BaseModel],
    *,
    model: str = MODEL_FAST,
    sdk_timeout: float = DEFAULT_SDK_TIMEOUT,
    hard_timeout: float | None = DEFAULT_HARD_TIMEOUT,
    _skip_guard: bool = False,
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
        _skip_guard: internal-only (#3001). When ``True``, skips
            ``_guard_stack`` entirely, so the call never reaches
            ``stack_axes()`` -> ``resolve_degraded_flag()``. The sole
            caller is ``agent.llm.compat._check_network``, the auto-bump
            ``llm`` gate's live probe -- it must stay pure (never touch the
            memoized degraded flag) while still getting the shared
            ``semaphore_slot()`` and both timeouts this function already
            applies. Not for use outside the compat gate.

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

    if not _skip_guard:
        _guard_stack("run_typed", signature_axis=True)

    try:
        stack = _load_stack()
    except Exception as e:
        # LLM_STACK_COMPAT_OVERRIDE=healthy short-circuits _guard_stack
        # before the predicate runs, so a genuinely broken stack can reach
        # here past the guard. `_load_stack`'s own contract is broader than
        # ImportError ("raises whatever the import raises"), and a raw
        # exception of any class would bypass every existing
        # `except LLMCallError` fail-safe -- the exact property
        # LLMStackIncompatible exists to preserve.
        raise LLMStackIncompatible(f"run_typed: LLM stack failed to import: {e}") from e

    async with semaphore_slot():
        async with stack.anthropic.AsyncAnthropic(
            api_key=get_anthropic_api_key(), timeout=sdk_timeout
        ) as client:
            provider = stack.AnthropicProvider(anthropic_client=client)
            pydantic_model = stack.AnthropicModel(model, provider=provider)
            agent = stack.Agent(pydantic_model, output_type=output_type)

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


async def run_typed_local(
    prompt: str,
    output_type: type[BaseModel],
    *,
    model: str = OLLAMA_CLASSIFIER_MODEL,
    hard_timeout: float | None = None,
) -> BaseModel:
    """Run a schema-validated call against the LOCAL granite model via Ollama.

    The granite-on-Ollama leg of the non-harness wrapper (durability plan
    #2494 Task 13): both hot-path classifiers — the intake classifier and the
    Job bind-or-mint router — route through here. Same typed-output contract
    as :func:`run_typed` (PydanticAI schema validation, single auto-retry,
    :class:`LLMCallError` on failure), with three deliberate differences:

    * **No Anthropic client and no shared Anthropic semaphore** — the call
      never leaves this machine, so the #1111 concurrency slot (which guards
      the Anthropic API) does not apply.
    * **Ollama provider** — ``OllamaProvider`` against
      ``settings.models.ollama_host`` (the ``/v1`` OpenAI-compatible surface),
      model defaulting to ``config.models.OLLAMA_CLASSIFIER_MODEL`` (granite).
    * **Single timeout** — one outer ``asyncio.wait_for`` wall-clock cap
      (``settings.timeouts.local_typed_hard_s``, env-overridable via
      ``TIMEOUTS__LOCAL_TYPED_HARD_S`` and read here per call, not at
      module scope). The hotfix-#1055 double-timeout pattern
      exists for half-open WAN sockets; a localhost daemon either answers or
      refuses, so one cap suffices.

    Fail-safe posture matches :func:`run_typed`: no fail-safe default here —
    each call site owns its own conservative default (intake → default
    classification, router → NEW Job) on :class:`LLMCallError`.

    Raises:
        ValueError: ``prompt`` is empty, ``None``, or whitespace-only.
        LLMCallError: provider/transport failure, schema-validation
            exhaustion, or the hard timeout fired.
    """
    if not prompt or not prompt.strip():
        raise ValueError("run_typed_local requires a non-empty, non-whitespace prompt")

    _guard_stack("run_typed_local", signature_axis=False)

    if hard_timeout is None:
        hard_timeout = settings.timeouts.local_typed_hard_s

    try:
        stack = _load_stack()
    except Exception as e:
        # `_load_stack`'s own contract is broader than ImportError ("raises
        # whatever the import raises"); catch the same breadth run_typed and
        # check_llm_stack_compat already do on this call.
        raise LLMStackIncompatible(f"run_typed_local: LLM stack failed to import: {e}") from e

    base_url = f"{settings.models.ollama_host.rstrip('/')}/v1"
    provider = stack.OllamaProvider(base_url=base_url)
    pydantic_model = stack.OpenAIChatModel(model, provider=provider)
    agent = stack.Agent(pydantic_model, output_type=output_type)

    try:
        result = await asyncio.wait_for(agent.run(prompt), timeout=hard_timeout)
    except TimeoutError as e:
        logger.error(
            "[agent.llm] local hard timeout (%.1fs) exceeded for model=%s: %s",
            hard_timeout,
            model,
            e,
        )
        raise LLMCallError(
            f"run_typed_local exceeded hard_timeout of {hard_timeout}s for model={model}"
        ) from e
    except Exception as e:
        logger.error(
            "[agent.llm] local provider error or schema-validation exhaustion for model=%s: %s",
            model,
            e,
            exc_info=True,
        )
        raise LLMCallError(f"run_typed_local failed for model={model}: {e}") from e

    return result.output
