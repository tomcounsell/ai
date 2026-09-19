"""The routing point for non-harness LLM calls (#3410).

:func:`resolve` is a pure function of ``(task, project_key)`` returning the
:class:`Route` the wrapper runs: which backend leg, which model, and the
fallback leg (if any) to try once when the primary raises
:class:`agent.llm.LLMCallError`. It is the only place that consults
context eligibility, and it reads model names from ``config.models`` and
nothing from per-site settings, because there are none.

The four rules, in order:

1. ``kind == THINKING`` or ``task.client_only`` -> Anthropic with the
   call's model. Thinking sites never leave the subscription backend under
   this taxonomy; ``client_only`` sites never leave it for any key.
2. ``task.backend == ANTHROPIC`` -> Anthropic with the call's model.
3. ``task.backend == OLLAMA`` and ``is_eligible(project_key)`` -> Ollama on
   ``OLLAMA_CLASSIFIER_MODEL`` with an Anthropic fallback on the call's
   model.
4. ``task.backend == OLLAMA`` otherwise -> Anthropic with the call's model.

Fail-closed rule (charter §7): rule 3 fires only for context the router can
prove eligible. ``tools.improvement_eligibility.is_eligible`` pins
``valor`` ``True`` in code, reads the process-local cache for every other
key, and treats a ``None`` key or a cache miss as ineligible, so client
context and unknown context both land on the subscription backend. The
eligibility import is late, inside :func:`resolve` (the same shape as
``tools/improvement_eval/judges/serves_charter.py``), so importing
``agent.llm`` never pulls ``bridge.routing`` in through the eligibility
module's config reader.

#3421 adds a fifth rule ahead of rule 3 for ``backend == DECISIONS`` and
#3420 adds ``LOCAL_ZERO_SHOT`` the same way, each with a local fallback.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.llm.tasks import Backend, LLMTask, TaskKind
from config.models import MODEL_FAST, OLLAMA_CLASSIFIER_MODEL


@dataclass(frozen=True)
class Route:
    """What the wrapper runs: the leg, its model, and the one-shot fallback."""

    backend: Backend
    model: str
    fallback: Route | None = None


def resolve(task: LLMTask, project_key: str | None, *, model: str = MODEL_FAST) -> Route:
    """Pick the backend leg for ``task`` in the context of ``project_key``.

    ``model`` is the call's ``model=`` kwarg and names the Anthropic model
    on every Anthropic route, the fallback included; the Ollama leg always
    runs ``config.models.OLLAMA_CLASSIFIER_MODEL``.
    """
    anthropic = Route(Backend.ANTHROPIC, model)
    if task.kind is TaskKind.THINKING or task.client_only:
        return anthropic
    if task.backend is Backend.ANTHROPIC:
        return anthropic
    if task.backend is Backend.OLLAMA:
        from tools.improvement_eligibility import is_eligible  # noqa: PLC0415

        if is_eligible(project_key):
            return Route(Backend.OLLAMA, OLLAMA_CLASSIFIER_MODEL, fallback=anthropic)
        return anthropic
    raise ValueError(f"no routing rule for backend {task.backend!r} (site {task.site})")
