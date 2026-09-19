"""PydanticAI-based wrapper for non-harness LLM calls (#1925, #3410).

Non-harness callers (classification, extraction, judging -- anything that
is NOT a ``claude -p`` harness session) call :func:`run_typed` with their
declared :class:`~agent.llm.tasks.LLMTask` instead of hand-rolling a
provider client. See ``docs/features/nonharness-llm-wrapper.md`` and
``docs/features/llm-task-taxonomy.md`` for the design,
``agent/llm/router.py`` for the routing rules, and ``agent/llm/backends/``
for the per-backend legs that carry the per-call slot + fresh-client
invariant.
"""

from .errors import LLMCallError, LLMStackIncompatible
from .tasks import Backend, ErrorCost, LLMTask, TaskKind
from .wrapper import DEFAULT_HARD_TIMEOUT, run_typed

__all__ = [
    "run_typed",
    "LLMTask",
    "TaskKind",
    "Backend",
    "ErrorCost",
    "LLMCallError",
    "LLMStackIncompatible",
    "DEFAULT_HARD_TIMEOUT",
]
