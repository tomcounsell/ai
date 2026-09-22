"""Typed PydanticAI call wrapper for non-harness LLM calls (#1925, #3410).

Every non-harness LLM call (classification, extraction, judging) routes
through ``run_typed`` instead of hand-rolling a provider client. The caller
declares a typed ``output_type`` (a ``pydantic.BaseModel`` subclass) and its
:class:`agent.llm.tasks.LLMTask`, and gets a schema-validated instance back,
with PydanticAI's built-in single auto-retry on schema mismatch.

The wrapper owns five things: prompt validation, routing
(``agent/llm/router.py::resolve`` picks the backend leg from the task and
the ``project_key``), the degraded-stack guard with the axis the route
needs, the one-shot fallback inside the caller's budget, and the outer
``hard_timeout``. The provider bodies live in ``agent/llm/backends/`` (one
leg per :class:`agent.llm.tasks.Backend`), and the hotfix #1055 invariant
lives there with them: each leg's only timer around the live request is an
SDK-level client timeout, and the Anthropic leg holds ``semaphore_slot()``
for the whole ``Agent.run()``.

Fallback budget (plan, Data Flow step 6): the budget is the caller's,
``sdk_timeout`` if given else ``hard_timeout`` (both ``None`` means
uncapped). When the primary leg raises :class:`LLMCallError` and the route
carries a fallback, the fallback runs once with ``sdk_timeout`` and
``slot_timeout`` both bounded by what is left of that budget, ``max_retries=0``
(one timer bounds one attempt) and ``deadline=start + budget`` (the leg
re-checks it after its slot wait, so the slot wait and the request cannot
each spend the whole remainder), or is skipped under
:data:`agent.llm.backends.MIN_REMAINDER_S` of remaining budget.

Two fixed-prefix log lines are the operator's evidence of which backend
served a site: ``llm_route site=<site> backend=<backend> elapsed_ms=<int>``
at INFO after whichever leg answered (with `` confidence=<score>`` appended,
``%.3f``, whenever the returned instance carries a ``confidence`` attribute
that is not ``None``, on either leg), and ``llm_fallback site=<site>
primary=<backend> fallback=<backend> reason=<reason> elapsed_ms=<int>`` at
WARNING ahead of a fallback leg. ``docs/infra/llm-task-routing.md`` greps
for both.

``hard_timeout`` is the one coroutine-level cap and it wraps the *legs*
(primary and fallback together), never the request, from outside; the
three 3 s hot-path sites pass ``hard_timeout=None`` and rely on
``sdk_timeout`` + ``slot_timeout`` alone.

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
from time import monotonic
from typing import TYPE_CHECKING

from agent.anthropic_client import _load_stack
from agent.llm.backends import MIN_REMAINDER_S, default_sdk_timeout
from agent.llm.backends import anthropic as anthropic_leg
from agent.llm.backends import decisions as decisions_leg
from agent.llm.backends import local_encoder as local_encoder_leg
from agent.llm.backends import ollama as ollama_leg
from agent.llm.errors import LLMCallError, LLMStackIncompatible
from agent.llm.router import resolve
from agent.llm.tasks import Backend, LLMTask
from config.models import MODEL_FAST
from config.settings import settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pydantic import BaseModel

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
    Backend.LOCAL_ENCODER: local_encoder_leg.call,
    Backend.DECISIONS: decisions_leg.call,
}


def _guard_stack(caller: str, *, signature_axis: bool) -> None:
    """Fail fast on a degraded stack, forcing flag resolution on first use.

    Resolving here is what makes the alert unmissable in a process that
    never ran a startup hook: the first call *is* the first read, and the
    resolver alerts on the transition.

    ``signature_axis`` is ``False`` for an Ollama- or decisions-routed
    call, which never touches ``anthropic`` -- an Anthropic create-signature
    break must not fall the hot-path classifiers over; ``loader_ok`` is
    still required on every route.

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
    task: LLMTask,
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
        task: the call site's :class:`agent.llm.tasks.LLMTask`. Required:
            a call without one raises ``TypeError`` naming the kwarg, so a
            site the taxonomy missed fails loudly (Risk 2).
        project_key: the project the call is made for; the router's
            eligibility input (charter §7). ``None`` fails closed to the
            subscription backend on a local-backend task.
        model: the Anthropic model id. Defaults to
            ``config.models.MODEL_FAST`` (Haiku). Names the model on every
            Anthropic route, the fallback included; the Ollama leg always
            runs ``config.models.OLLAMA_CLASSIFIER_MODEL``, the local
            encoder leg runs the site's committed head, and the decisions
            leg always runs ``config.models.JEV``.
        system: an optional system prompt for the PydanticAI ``Agent``.
            The local encoder leg ignores it (the head was fit on the text
            alone); it is carried for the Anthropic fallback.
        sdk_timeout: the leg's SDK-level request timer (seconds). ``None``
            means the leg's default from ``settings.timeouts``
            (``anthropic_sdk_s`` for Anthropic, ``local_typed_hard_s`` for
            Ollama and the local encoder, ``decisions_sdk_s`` for the
            decisions leg), read at call time; an explicit value always
            wins and is also the fallback budget.
        slot_timeout: bounds the Anthropic leg's wait for the shared
            semaphore; ``None`` waits unbounded on the primary leg (the
            fallback's wait is bounded by its timer). A slot wait that
            expires raises ``LLMCallError(reason="slot_timeout")`` with no
            client constructed.
        hard_timeout: outer wall-clock cap (seconds) via
            ``asyncio.wait_for`` around the legs, applied here and never
            inside a leg; the fallback budget when ``sdk_timeout`` is
            ``None``. Pass ``None`` to disable it and rely on the SDK-level
            timers alone (the three 3 s hot-path sites).
        max_retries: the SDK retry count for the primary Anthropic client;
            ``None`` means the SDK default. The fallback leg always runs
            with 0.
        _skip_guard: internal-only (#3001). When ``True``, skips both
            ``_guard_stack`` calls (primary and fallback) and nothing else,
            so the call never reaches ``stack_axes()`` ->
            ``resolve_degraded_flag()``. The sole caller is
            ``agent.llm.compat._check_network``, the auto-bump ``llm``
            gate's live probe -- it must stay pure (never touch the
            memoized degraded flag) while still getting the shared
            ``semaphore_slot()`` and both timeouts. Not for use outside
            the compat gate.

    Returns:
        A validated instance of ``output_type``. It carries no marker of
        which leg answered; the ``llm_route`` log line does.

    Raises:
        ValueError: ``prompt`` is empty, ``None``, or whitespace-only.
        TypeError: ``task`` is missing or not an ``LLMTask``.
        LLMCallError: the leg failed (``reason`` in ``timeout``,
            ``slot_timeout``, ``transport``, ``validation``), the fallback
            failed too or was skipped for want of budget (the primary's
            error propagates), or the outer ``hard_timeout`` fired
            (``reason="timeout"``). The original exception is logged and
            chained as ``__cause__``.
    """
    if not prompt or not prompt.strip():
        raise ValueError("run_typed requires a non-empty, non-whitespace prompt")
    if not isinstance(task, LLMTask):
        raise TypeError(
            "run_typed() requires the keyword-only argument 'task' (an agent.llm.LLMTask "
            "declared at the call site; see docs/features/llm-task-taxonomy.md)"
        )

    route = resolve(task, project_key, model=model)

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

    effective = sdk_timeout if sdk_timeout is not None else default_sdk_timeout(route.backend)
    budget = sdk_timeout if sdk_timeout is not None else hard_timeout
    start = monotonic()
    deadline = None if budget is None else start + budget

    async def _legs() -> BaseModel:
        try:
            result = await _LEGS[route.backend](
                prompt,
                output_type,
                route,
                system=system,
                sdk_timeout=effective,
                slot_timeout=slot_timeout,
                max_retries=max_retries,
                deadline=None,
                stack=stack,
            )
            answered = route.backend
        except LLMCallError as primary_error:
            fallback = route.fallback
            if fallback is None:
                raise
            elapsed = monotonic() - start
            if budget is not None and budget - elapsed < MIN_REMAINDER_S:
                logger.warning(
                    "llm_no_fallback site=%s primary=%s reason=%s elapsed_ms=%d budget_s=%.1f",
                    task.site,
                    route.backend.value,
                    primary_error.reason,
                    int(elapsed * 1000),
                    budget,
                )
                raise
            fb_default = default_sdk_timeout(fallback.backend)
            fb_timeout = fb_default if budget is None else min(fb_default, budget - elapsed)
            fb_slot = fb_timeout if slot_timeout is None else min(slot_timeout, fb_timeout)
            if not _skip_guard:
                _guard_stack(
                    "run_typed:fallback",
                    signature_axis=(fallback.backend is Backend.ANTHROPIC),
                )
            logger.warning(
                "llm_fallback site=%s primary=%s fallback=%s reason=%s elapsed_ms=%d",
                task.site,
                route.backend.value,
                fallback.backend.value,
                primary_error.reason,
                int(elapsed * 1000),
            )
            result = await _LEGS[fallback.backend](
                prompt,
                output_type,
                fallback,
                system=system,
                sdk_timeout=fb_timeout,
                slot_timeout=fb_slot,
                max_retries=0,
                deadline=deadline,
                stack=stack,
            )
            answered = fallback.backend
        elapsed_ms = int((monotonic() - start) * 1000)
        confidence = getattr(result, "confidence", None)
        if confidence is None:
            logger.info(
                "llm_route site=%s backend=%s elapsed_ms=%d", task.site, answered.value, elapsed_ms
            )
        else:
            # Backend-neutral: any result carrying a ``confidence`` attribute logs
            # it, so one grep compares the two legs' score distributions per site
            # after deploy (#3420, Risk 2). Without the attribute the lane A line
            # is logged byte-for-byte.
            logger.info(
                "llm_route site=%s backend=%s elapsed_ms=%d confidence=%.3f",
                task.site,
                answered.value,
                elapsed_ms,
                confidence,
            )
        return result

    if hard_timeout is None:
        return await _legs()
    try:
        return await asyncio.wait_for(_legs(), timeout=hard_timeout)
    except TimeoutError as e:
        logger.error(
            "[agent.llm] hard timeout (%.1fs) exceeded at site=%s on the %s route for model=%s: %s",
            hard_timeout,
            task.site,
            route.backend.value,
            route.model,
            e,
        )
        raise LLMCallError(
            f"run_typed exceeded hard_timeout of {hard_timeout}s at site={task.site} on the "
            f"{route.backend.value} route for model={route.model}",
            reason="timeout",
        ) from e
