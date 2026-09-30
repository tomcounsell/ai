"""The worker's door into the kernel. Seams §2.4; tech stack §2.

A worker that wants to write to the tree has exactly one way to do it: a call
here carrying its Brief-scoped token. `workers/` and `adapters/` never import
`kernel/`, so the door is this Protocol, handed to the adapter at
construction and implemented by `kernel/api.py`.

Every call refuses a stale generation, a space other than the Brief's, and a
call the Brief's capabilities do not cover. No call consumes budget: money is
spent at the gateway and nowhere else (seams version 3).

`Action` and `EffectOutcome` are seams §1.10, `schemas/effect.py`, which the
broker plan owns and which is not on this branch yet; the names are resolved
for type checking only so this module imports today.
"""

from typing import TYPE_CHECKING, Protocol

from schemas.brief import BriefToken, DelegateRequest
from schemas.ids import BriefId, EpisodeId, QuestionId
from schemas.memory import BeliefProposal, EpisodeWrite, MemoryHit, SliceQuery
from schemas.records import ToolLogRecord

if TYPE_CHECKING:
    from schemas.effect import Action, EffectOutcome


class KernelAPI(Protocol):
    async def record_tool(self, token: BriefToken, record: ToolLogRecord) -> int: ...

    async def raise_question(
        self, token: BriefToken, text: str, tool_seq: int
    ) -> QuestionId: ...

    async def request_effect(
        self, token: BriefToken, action: "Action"
    ) -> "EffectOutcome": ...

    async def read_slice(
        self, token: BriefToken, query: SliceQuery
    ) -> list[MemoryHit]: ...

    async def write_episode(
        self, token: BriefToken, write: EpisodeWrite
    ) -> EpisodeId: ...

    async def propose_belief(
        self, token: BriefToken, proposal: BeliefProposal
    ) -> str: ...

    async def delegate(
        self, token: BriefToken, request: DelegateRequest
    ) -> BriefId: ...
