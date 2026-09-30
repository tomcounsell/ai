"""Exceptions shared by the wrapper and the backend legs (#1925, #3001, #3410).

They live apart from ``wrapper.py`` because the legs under
``agent/llm/backends/`` raise them and the wrapper imports the legs; the
public names are re-exported from :mod:`agent.llm`.
"""

from __future__ import annotations

from typing import Literal

Reason = Literal["timeout", "slot_timeout", "transport", "validation"]
"""Why a leg failed, as the wrapper's fallback logic and tests read it.

``timeout``: the SDK-level request timer fired, or the leg's deadline
re-check found under 0.5 s of budget. ``slot_timeout``: the Anthropic
semaphore was not acquired within ``slot_timeout``. ``transport``: the
provider refused, errored, or the stack could not be loaded.
``validation``: PydanticAI exhausted its schema-validation retries.
"""


class LLMCallError(Exception):
    """Raised when ``run_typed`` cannot produce a validated output.

    Wraps the underlying PydanticAI/provider exception (available via
    ``__cause__``) after it has already been logged. Callers apply their
    own site-specific conservative default on this exception -- the
    wrapper deliberately does not pick one for them.

    ``reason`` says which of the four failure shapes this was. The default
    is ``transport`` so a bare ``LLMCallError("...")`` (test fakes, the
    stack-incompatible subclass) still carries a valid reason.
    """

    def __init__(self, message: str = "", *, reason: Reason = "transport") -> None:
        super().__init__(message)
        self.reason: Reason = reason


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
