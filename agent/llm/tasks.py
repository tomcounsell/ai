"""The LLM task taxonomy (#3410).

Every non-harness LLM call site declares one :class:`LLMTask` as a module
constant next to its output type and passes it as ``task=`` to
:func:`agent.llm.run_typed`. The declaration is the only per-site backend
choice in the repo: ``agent/llm/router.py::resolve`` reads it together with
the call's ``project_key`` and returns the backend leg to run, so moving a
site between backends is a one-word diff on its declaration.

Protocol:

* ``kind`` separates the two populations. A ``CLASSIFICATION`` task returns
  one of a closed set of labels (``bool`` / ``Literal`` fields); a
  ``THINKING`` task writes prose or extracts structure. Thinking tasks never
  leave the subscription backend under this taxonomy.
* ``backend`` is the backend the site lands on for eligible context. There
  is no separate "incumbent" concept: the value in code is the truth, and
  the comparison record in the site's row of
  ``docs/features/llm-task-taxonomy.md`` is the argument for it.
* ``error_cost`` is the tier the PR reviewer applies when a site lands on a
  local backend (``high`` 95%, ``medium`` 90%, ``low`` 85% agreement with
  the reference arm).
* ``client_only`` pins a site to the subscription backend for every project
  key (charter §7: client work stays on the Claude and Codex subscriptions).

Fail-closed rule: the router routes a local-backend task to its declared
backend only for context it can prove eligible (``valor`` is pinned; other
keys are a cache-only read). A ``None`` key, a cache miss, or a client key
resolves to the subscription backend. The wrapper never invents a default
answer: on :class:`agent.llm.LLMCallError` every call site applies its own
conservative fail-safe, exactly as before this taxonomy existed.

Lane B (#3420) and lane C (#3421) append their own :class:`Backend` members
and their own keyword fields with defaults; nothing for them lives here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TaskKind(str, Enum):
    """The two populations of non-harness LLM calls."""

    CLASSIFICATION = "classification"
    THINKING = "thinking"


class Backend(str, Enum):
    """A backend leg under ``agent/llm/backends/``; the value is its log token."""

    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"


class ErrorCost(str, Enum):
    """What a wrong answer costs at the site; sets the acceptance-bar tier."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class LLMTask:
    """One call site's declaration, read by the router and the taxonomy tests.

    ``site`` is a stable dotted id (``routing.needs_response``,
    ``job_router.route``) that keys the comparison records and the doc table.
    """

    site: str
    kind: TaskKind
    backend: Backend
    error_cost: ErrorCost = ErrorCost.MEDIUM
    client_only: bool = False
