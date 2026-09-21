"""Backend legs for :func:`agent.llm.run_typed` (#3410).

One leg per :class:`agent.llm.tasks.Backend`, each a module exposing::

    async def call(
        prompt, output_type, route, *,
        system, sdk_timeout, slot_timeout, max_retries, deadline=None, stack,
    ) -> BaseModel

Leg protocol:

* A leg returns a validated instance of ``output_type`` or raises
  :class:`agent.llm.LLMCallError` with a ``reason`` from
  :data:`agent.llm.errors.Reason`. It never invents a default; the call
  site's fail-safe does that.
* ``sdk_timeout`` is a required float and the only timer around the live
  request: an SDK-level client timeout, never a coroutine-level
  ``asyncio.wait_for`` (hotfix #1055: cancelling a coroutine mid-request
  leaks httpx connections). The wrapper applies ``hard_timeout`` outside
  the leg, and only when the caller passes one.
* ``slot_timeout`` bounds the wait for the shared Anthropic semaphore; a leg
  with no semaphore accepts and ignores it. ``max_retries`` is the SDK
  retry count, ``None`` meaning the SDK default; the Ollama leg pins 0.
* ``deadline`` is an absolute ``time.monotonic()`` instant the wrapper
  passes on the fallback leg only. A leg re-checks it once, after any
  queue wait and before constructing its client: under 0.5 s of remainder
  raises ``LLMCallError(reason="timeout")`` with no client ever built,
  otherwise the request timer shrinks to the remainder. ``None`` means no
  check.
* ``stack`` is the :class:`agent.anthropic_client.LLMStack` the wrapper
  resolved through its ``_load_stack`` seam. A leg takes every
  third-party symbol from it and imports none at module scope (#3001), so
  ``dataclasses.replace(real_stack, <Symbol>=Fake)`` on that seam is the
  network-isolation point for every test.
* ``CancelledError`` is expected (the wrapper's ``hard_timeout``, a caller
  going away) and is never caught: the ``async with`` blocks release the
  slot and close the client on the way out.

Every leg reads its default timer from ``config.settings.TimeoutSettings``
at call time through :func:`default_sdk_timeout`, so an env bump takes
effect without a reload.
"""

from __future__ import annotations

from agent.llm.errors import LLMCallError, Reason
from agent.llm.tasks import Backend
from config.settings import settings


def default_sdk_timeout(backend: Backend) -> float:
    """The leg's SDK-level timer when the caller passes no ``sdk_timeout``.

    ``ANTHROPIC`` reads ``settings.timeouts.anthropic_sdk_s`` (30 s, env
    ``TIMEOUTS__ANTHROPIC_SDK_S``); ``OLLAMA`` reads
    ``settings.timeouts.local_typed_hard_s`` (20 s, env
    ``TIMEOUTS__LOCAL_TYPED_HARD_S``, operator lever 2 for a degraded
    daemon); ``DECISIONS`` reads ``settings.timeouts.decisions_sdk_s`` (3 s,
    env ``TIMEOUTS__DECISIONS_SDK_S``). Read per call, never cached at
    module scope.
    """
    if backend is Backend.ANTHROPIC:
        return settings.timeouts.anthropic_sdk_s
    if backend is Backend.OLLAMA:
        return settings.timeouts.local_typed_hard_s
    if backend is Backend.DECISIONS:
        return settings.timeouts.decisions_sdk_s
    raise ValueError(f"no default SDK timeout for backend {backend!r}")


def reason_for(exc: BaseException) -> Reason:
    """Classify a leg exception onto :data:`Reason`.

    PydanticAI wraps provider errors (``ModelAPIError`` around an
    ``APITimeoutError`` / ``APIConnectionError``, ``ModelHTTPError`` around
    a status error) and raises ``UnexpectedModelBehavior`` when its schema
    retries run out. The legs hold no third-party symbols at module scope
    (#3001), so the classification keys on class names along the ``__mro__``
    of each exception in the ``__cause__`` chain rather than on imported
    classes; both SDKs name their timeout ``APITimeoutError``.
    """
    seen: BaseException | None = exc
    while seen is not None:
        names = {cls.__name__ for cls in type(seen).__mro__}
        if "APITimeoutError" in names or isinstance(seen, TimeoutError):
            return "timeout"
        if "UnexpectedModelBehavior" in names:
            return "validation"
        seen = seen.__cause__
    return "transport"


MIN_REMAINDER_S = 0.5
"""Below this much budget a leg raises ``timeout`` rather than start a request."""


def bound_to_deadline(sdk_timeout: float, deadline: float | None, now: float, *, leg: str) -> float:
    """The leg's deadline re-check: the request timer that fits the budget.

    ``None`` deadline returns ``sdk_timeout`` unchanged. Otherwise under
    :data:`MIN_REMAINDER_S` of remainder raises
    ``LLMCallError(reason="timeout")`` so the caller never constructs a
    client it cannot use, else returns ``min(sdk_timeout, remaining)``.
    """
    if deadline is None:
        return sdk_timeout
    remaining = deadline - now
    if remaining < MIN_REMAINDER_S:
        raise LLMCallError(
            f"{leg} leg: {remaining:.2f}s of budget left, request not started",
            reason="timeout",
        )
    return min(sdk_timeout, remaining)
