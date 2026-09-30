"""The worker port. Seams §2.1; tech stack §5.

Three messages. `run` drives one Brief inside the sandbox the kernel created
and yields its trace: a `question` pauses it, a `terminal` ends it. `answer`
and `abort` are the only messages into a running worker.

What an implementation must meet, from spike 08: after yielding a `question`
it makes zero gateway requests until `answer()` arrives; the last event of
every run is a `terminal`; `abort()` during a wait ends the run with
`terminal(outcome="aborted")` and nothing after it; tool calls in one model
turn may run concurrently, so tool log rows pair by `seq`; every `tool.start`
is durable through `KernelAPI.record_tool` before the sandbox call begins.
The adapter never calls `create`, `snapshot`, `stop`, or `destroy` on the
sandbox, and offers only the tools the Brief's capabilities cover.
"""

from collections.abc import AsyncIterator
from typing import Protocol

from schemas.brief import Brief
from schemas.ids import BriefId, QuestionId
from schemas.sandbox import SandboxHandle
from schemas.trace import TraceEvent


class Worker(Protocol):
    def run(self, brief: Brief, handle: SandboxHandle) -> AsyncIterator[TraceEvent]: ...

    async def answer(
        self, brief_id: BriefId, question_id: QuestionId, text: str
    ) -> None: ...

    async def abort(self, brief_id: BriefId) -> None: ...
