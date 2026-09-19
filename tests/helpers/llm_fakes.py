"""The one ``run_typed`` fake every migrated classification site's tests use (#3410, Risk 6).

A migrated site calls ``agent.llm.run_typed`` through a module-scope import,
so its tests patch ``<module>.run_typed`` with a :class:`FakeRunTyped`. The
fake records every call (prompt, output type, and every keyword the site
passed, ``task`` and ``project_key`` included), answers with a configured
instance of the site's output type, or raises the :class:`LLMCallError`
the site's fail-safe must absorb. One fake rather than one per file keeps
every test on the wrapper's real contract: a call that reaches no fake is
a test that reaches no leg.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from agent.llm import LLMCallError
from agent.llm.errors import Reason


@dataclass
class RecordedCall:
    """One ``run_typed`` invocation as the fake saw it."""

    prompt: str
    output_type: type[BaseModel]
    kwargs: dict[str, Any]

    @property
    def task(self):
        return self.kwargs.get("task")

    @property
    def project_key(self):
        return self.kwargs.get("project_key")


Responder = Callable[[RecordedCall], BaseModel | Awaitable[BaseModel]]


@dataclass
class FakeRunTyped:
    """A ``run_typed`` stand-in.

    ``result`` is returned as-is (it must already be an instance of the
    site's output type). ``error`` is raised instead when set. ``responder``
    takes precedence over both and is called with the :class:`RecordedCall`,
    so a test can answer per prompt or raise mid-sequence. ``on_call`` runs
    before the answer is produced, for tests that need a side effect (a
    contextvar the real leg would set, a clock advance).
    """

    result: BaseModel | None = None
    error: LLMCallError | None = None
    responder: Responder | None = None
    on_call: Callable[[RecordedCall], None] | None = None
    calls: list[RecordedCall] = field(default_factory=list)

    async def __call__(self, prompt: str, output_type: type[BaseModel], **kwargs: Any) -> BaseModel:
        call = RecordedCall(prompt=prompt, output_type=output_type, kwargs=dict(kwargs))
        self.calls.append(call)
        if self.on_call is not None:
            self.on_call(call)
        if self.responder is not None:
            answer = self.responder(call)
            if hasattr(answer, "__await__"):
                answer = await answer
            return answer
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise AssertionError("FakeRunTyped has neither a result, an error nor a responder")
        return self.result

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last(self) -> RecordedCall:
        return self.calls[-1]


def failing(reason: Reason = "transport", message: str = "fake leg failure") -> FakeRunTyped:
    """A fake whose every call raises ``LLMCallError(reason=reason)``."""
    return FakeRunTyped(error=LLMCallError(message, reason=reason))
