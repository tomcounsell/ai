"""Typed PydanticAI call wrapper for non-harness LLM calls (#1925, #3410).

Every non-harness LLM call (classification, extraction, judging) routes
through ``run_typed`` instead of hand-rolling a provider client. The caller
declares a typed ``output_type`` (a ``pydantic.BaseModel`` subclass) and its
:class:`agent.llm.tasks.LLMTask`, and gets a schema-validated instance back,
with PydanticAI's built-in single auto-retry on schema mismatch.

The wrapper owns four things: prompt validation, routing
(``agent/llm/router.py::resolve`` picks the backend leg from the task and
the ``project_key``), the degraded-stack guard with the axis the route
needs, and the outer ``hard_timeout``. The provider bodies live in
``agent/llm/backends/`` (one leg per :class:`agent.llm.tasks.Backend`), and
the hotfix #1055 invariant lives there with them: each leg's only timer
around the live request is an SDK-level client timeout, and the Anthropic
leg holds ``semaphore_slot()`` for the whole ``Agent.run()``.

``hard_timeout`` is the one coroutine-level cap and it wraps the *leg*, not
the request, from outside; the three 3 s hot-path sites pass
``hard_timeout=None`` and rely on ``sdk_timeout`` + ``slot_timeout`` alone.

Fail-safe posture: this wrapper does NOT implement a fail-safe default.
Provider errors and exhausted schema-validation retries are logged, then
re-raised as :class:`LLMCallError` with a ``reason``. Each call site owns
its own conservative default (respond / escalate / send / skip) on failure.

Import-safety contract (#3001): module scope here is **stdlib and our own
code only**. Every third-party LLM-stack symbol (``anthropic``,
``openai.AsyncOpenAI``, ``pydantic_ai.*``) is resolved through
:func:`agent.anthropic_client._load_stack`, the one memoized loader, only
from inside ``run_typed``, and handed to the leg as ``stack``. A machine
with a broken or missing stack can still ``import agent.llm`` (and
therefore ``import bridge.telegram_bridge``); the failure surfaces at the
call, where it can be reported. ``_load_stack`` is imported into this
module's namespace, so ``monkeypatch.setattr(wrapper_mod, "_load_stack",
...)`` is the network-isolation seam for tests on either leg.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from agent.anthropic_client import _load_stack
from agent.llm.backends import anthropic as anthropic_leg
from agent.llm.backends import default_sdk_timeout
from agent.llm.backends import ollama as ollama_leg
from agent.llm.errors import LLMCallError, LLMStackIncompatible
from agent.llm.router import Route, resolve
from agent.llm.tasks import Backend
from config.models import MODEL_FAST
from config.settings import settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pydantic import BaseModel

    from agent.llm.tasks import LLMTask

__all__ = [
    "DEFAULT_HARD_TIMEOUT",
    "LLMCallError",
    "LLMStackIncompatible",
    "run_typed",
]

logger = logging.getLogger(__name__)

# The outer wall-clock cap for thinking sites (hotfix #1055 double-timeout
# pattern, sourced from settings.timeouts.anthropic_hard_s, issue #1968).
# The inner SDK-level timer is chosen per backend leg at call time by
# ``agent.llm.backends.default_sdk_timeout``; this module owns no SDK
# timeout constant. Preserve the two-timer structure -- never collapse to
# one value.
DEFAULT_HARD_TIMEOUT = settings.timeouts.anthropic_hard_s

_LEGS = {
    Backend.ANTHROPIC: anthropic_leg.call,
    Backend.OLLAMA: ollama_leg.call,
}


def _guard_stack(caller: str, *, signature_axis: bool) -> None:
    """Fail fast on a degraded stack, forcing flag resolution on first use.

    Resolving here is what makes the alert unmissable in a process that
    never ran a startup hook: the first call *is* the first read, and the
    resolver alerts on the transition.

    ``signature_axis`` is ``False`` for an Ollama-routed call, which never
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
    task: LLMTask | None = None,
    project_key: str | None = None,
    model: str = MODEL_FAST,
    system: str | None = None,
    sdk_timeout: float | None = None,
    slot_timeout: float | None = None,
    hard_timeout: float | None = DEFAULT_HARD_TIMEOUT,
    max_retries: int | None = None,
    _skip_guard: bool = False,
) -> BaseModel:
    """Run a schema-validated LLM call on the backend leg the router picks.

    Args:
        prompt: the user prompt. Must be non-empty and not
            whitespace-only -- validated before any routing or network
            work, so a bad prompt fails fast with no LLM call and no hang.
        output_type: a ``pydantic.BaseModel`` subclass describing the
            desired structured output. PydanticAI validates the model's
            response against this schema and auto-retries once on
            mismatch before raising.
        task: the call site's :class:`agent.llm.tasks.LLMTask`. A task
            declared ``backend=OLLAMA`` runs on the Ollama leg; every other
            task, and a call that passes none, runs on the Anthropic leg.
        project_key: the project the call is made for; the router's
            eligibility input (charter §7).
        model: the Anthropic model id. Defaults to
            ``config.models.MODEL_FAST`` (Haiku). The Ollama leg always runs
            ``config.models.OLLAMA_CLASSIFIER_MODEL``.
        system: an optional system prompt for the PydanticAI ``Agent``.
        sdk_timeout: the leg's SDK-level request timer (seconds). ``None``
            means the leg's default from ``settings.timeouts``
            (``anthropic_sdk_s`` for Anthropic, ``local_typed_hard_s`` for
            Ollama), read at call time; an explicit value always wins.
        slot_timeout: bounds the Anthropic leg's wait for the shared
            semaphore; ``None`` waits unbounded. A slot wait that expires
            raises ``LLMCallError(reason="slot_timeout")`` with no client
            constructed.
        hard_timeout: outer wall-clock cap (seconds) via
            ``asyncio.wait_for`` around the whole leg, applied here and
            never inside a leg. Pass ``None`` to disable it and rely on
            the SDK-level timers alone (the three 3 s hot-path sites).
        max_retries: the SDK retry count for the Anthropic client;
            ``None`` means the SDK default.
        _skip_guard: internal-only (#3001). When ``True``, skips
            ``_guard_stack`` entirely, so the call never reaches
            ``stack_axes()`` -> ``resolve_degraded_flag()``. The sole
            caller is ``agent.llm.compat._check_network``, the auto-bump
            ``llm`` gate's live probe -- it must stay pure (never touch the
            memoized degraded flag) while still getting the shared
            ``semaphore_slot()`` and both timeouts. Not for use outside
            the compat gate.

    Returns:
        A validated instance of ``output_type``.

    Raises:
        ValueError: ``prompt`` is empty, ``None``, or whitespace-only.
        LLMCallError: the leg failed (``reason`` in ``timeout``,
            ``slot_timeout``, ``transport``, ``validation``) or the outer
            ``hard_timeout`` fired (``reason="timeout"``). The original
            exception is logged and chained as ``__cause__``.
    """
    if not prompt or not prompt.strip():
        raise ValueError("run_typed requires a non-empty, non-whitespace prompt")

    route = (
        resolve(task, project_key, model=model)
        if task is not None
        else Route(Backend.ANTHROPIC, model)
    )

    if not _skip_guard:
        _guard_stack("run_typed", signature_axis=(route.backend is Backend.ANTHROPIC))

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

    effective_sdk_timeout = (
        sdk_timeout if sdk_timeout is not None else default_sdk_timeout(route.backend)
    )
    leg = _LEGS[route.backend](
        prompt,
        output_type,
        route,
        system=system,
        sdk_timeout=effective_sdk_timeout,
        slot_timeout=slot_timeout,
        max_retries=max_retries,
        deadline=None,
        stack=stack,
    )
    if hard_timeout is None:
        return await leg
    try:
        return await asyncio.wait_for(leg, timeout=hard_timeout)
    except TimeoutError as e:
        logger.error(
            "[agent.llm] hard timeout (%.1fs) exceeded on the %s leg for model=%s: %s",
            hard_timeout,
            route.backend.value,
            route.model,
            e,
        )
        raise LLMCallError(
            f"run_typed exceeded hard_timeout of {hard_timeout}s on the "
            f"{route.backend.value} leg for model={route.model}",
            reason="timeout",
        ) from e
