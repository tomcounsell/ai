"""PydanticAI-based wrapper for non-harness LLM calls (#1925).

Non-harness callers (classification, extraction, judging -- anything that
is NOT a ``claude -p`` harness session) should call :func:`run_typed`
instead of hand-rolling an ``anthropic`` client. See
``docs/features/nonharness-llm-wrapper.md`` for the full design and
``agent/llm/wrapper.py`` for the per-call semaphore-slot + fresh-client
invariant this module preserves.
"""

from .wrapper import DEFAULT_HARD_TIMEOUT, DEFAULT_SDK_TIMEOUT, LLMCallError, run_typed

__all__ = [
    "run_typed",
    "LLMCallError",
    "DEFAULT_SDK_TIMEOUT",
    "DEFAULT_HARD_TIMEOUT",
]
