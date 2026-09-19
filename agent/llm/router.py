"""The routing point for non-harness LLM calls (#3410).

:func:`resolve` is a pure function of ``(task, project_key)`` returning the
:class:`Route` the wrapper runs: which backend leg, which model, and the
fallback leg (if any) to try once when the primary raises
:class:`agent.llm.LLMCallError`. It is the only place that consults
context eligibility, and it reads model names from ``config.models`` and
nothing from per-site settings, because there are none.

Fail-closed rule: a local-backend task runs on its declared backend only for
context the router can prove eligible; ``valor`` is pinned eligible in code,
every other key is a cache-only read, and a ``None`` key or a miss resolves
to the subscription backend (charter §7).

Lane-A Task 1 ships the two-leg split: a task declared ``backend=OLLAMA``
routes to the Ollama leg and everything else to the Anthropic leg. Task 2
installs the four rules (thinking / ``client_only`` short-circuit, declared
Anthropic, eligible Ollama with an Anthropic fallback, ineligible Ollama to
Anthropic) and the eligibility read behind them.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.llm.tasks import Backend, LLMTask
from config.models import MODEL_FAST, OLLAMA_CLASSIFIER_MODEL


@dataclass(frozen=True)
class Route:
    """What the wrapper runs: the leg, its model, and the one-shot fallback."""

    backend: Backend
    model: str
    fallback: Route | None = None


def resolve(task: LLMTask, project_key: str | None, *, model: str = MODEL_FAST) -> Route:
    """Pick the backend leg for ``task`` in the context of ``project_key``.

    ``model`` is the call's ``model=`` kwarg and names the Anthropic model;
    the Ollama leg always runs ``config.models.OLLAMA_CLASSIFIER_MODEL``.
    """
    if task.backend is Backend.OLLAMA:
        return Route(Backend.OLLAMA, OLLAMA_CLASSIFIER_MODEL)
    return Route(Backend.ANTHROPIC, model)
