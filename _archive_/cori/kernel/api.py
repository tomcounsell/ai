"""The worker's door into the kernel. Seams §2.4, §3.11, §5.2; plan 05 task 4.

`Door` implements `ports/kernel.py::KernelAPI` over a psycopg connection
factory. Every call runs in one transaction: `tree.check_generation` on the
token, the record's or request's space equals the Brief's, a capability the
Brief holds covers the one the call needs, then the write. Nothing here
consumes budget: money is spent at the gateway and nowhere else (seams v3).

Live Briefs are held in memory (`register` and `forget`, called by
`kernel/runs.py`), because the kernel is one process (tech stack §2) and a
Brief that outlives the process is stopped by the generation fence anyway.

The class a row needs comes from the sandbox profile the kernel already
issued (plan 05, critique 2): `read` and `ask` at `read`; `bash` at the
profile's class, `read` in `scratch` and `verify` and `propose` in
`worktree`; `write` and `edit` under `write` at `propose`; the memory family
at `propose`. `memory` and `perform` are bindings to modules that land later
in the build order, in the tree plan's pattern: `kernel.memory` is resolved
on first use so importing this module reaches no Redis client, and
`request_effect` raises `Refused` while `perform` is `None`. Nothing here
imports `broker/`.

The reader lives beside the writer so one module owns the table's rules:
`read_tool_log` and `invocations` are the one reader of `tool_log`
(seams §3.11).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

from kernel import tree
from schemas.brief import Brief, BriefToken, DelegateRequest
from schemas.capability import Capability, Refused
from schemas.ids import BriefId, EpisodeId, QuestionId, new_id
from schemas.memory import BeliefProposal, EpisodeWrite, MemoryHit, SliceQuery
from schemas.records import Invocation, ToolLogRecord
from schemas.space import EffectClass

if TYPE_CHECKING:
    from schemas.effect import Action, EffectOutcome

__all__ = [
    "Door",
    "Refused",
    "capability_for",
    "invocations",
    "read_tool_log",
]

COLUMNS = (
    "id",
    "brief_id",
    "generation",
    "space_id",
    "seq",
    "event",
    "tool",
    "input",
    "input_sha256",
    "exit_status",
    "stdout_sha256",
    "stderr_sha256",
    "artifact",
    "artifact_sha256",
    "duration_ms",
    "question_id",
    "text",
    "outcome",
    "report",
    "schema_version",
    "at",
)

# Tool name -> (capability name, the class the row needs). `bash` is not
# here because its class is the profile's.
_TOOL_CAPABILITY: dict[str, tuple[str, EffectClass]] = {
    "read": ("read", "read"),
    "ask": ("ask", "read"),
    "write": ("write", "propose"),
    "edit": ("write", "propose"),
    "write_episode": ("memory.episodic.write", "propose"),
    "propose_belief": ("memory.operator.propose", "propose"),
}


def capability_for(brief: Brief, tool: str) -> Capability:
    """The capability a row for `tool` needs under this Brief, scoped to the
    Brief's space (plan 05, Design)."""
    if tool == "bash":
        klass: EffectClass = (
            "propose" if brief.sandbox_profile.name == "worktree" else "read"
        )
        return Capability(name="bash", effect_class=klass, scope=brief.space)
    try:
        name, klass = _TOOL_CAPABILITY[tool]
    except KeyError:
        raise Refused(f"{tool!r} is not a tool the door records") from None
    return Capability(name=name, effect_class=klass, scope=brief.space)


def _covered(brief: Brief, needed: Capability) -> None:
    if not any(c.covers(needed) for c in brief.capabilities):
        raise Refused(
            f"{brief.id} holds no capability covering "
            f"{needed.name}@{needed.effect_class} on {needed.scope!r}"
        )


class Door:
    """`KernelAPI` over a connection factory (seams §2.4)."""

    def __init__(
        self,
        connect: Callable[[], Any],
        *,
        memory: Any = None,
        perform: Callable[..., Any] | None = None,
    ) -> None:
        self.connect = connect
        self._memory = memory
        self.perform = perform
        self._live: dict[BriefId, Brief] = {}
        # Question ids minted by `raise_question`, per Brief; a `question`
        # or `answer` row names one of them or is refused.
        self._questions: dict[BriefId, set[QuestionId]] = {}

    # -- late-bound collaborators -------------------------------------------

    @property
    def memory(self):
        """`kernel.memory` by default, resolved on first use: importing it
        binds a Redis client at import time (its module docstring), which is
        no business of a module that only records rows."""
        if self._memory is None:
            import kernel.memory

            self._memory = kernel.memory
        return self._memory

    # -- the registry -------------------------------------------------------

    def register(self, brief: Brief) -> None:
        self._live[brief.id] = brief
        self._questions.setdefault(brief.id, set())

    def forget(self, brief_id: BriefId) -> None:
        self._live.pop(brief_id, None)
        self._questions.pop(brief_id, None)

    def _brief(self, token: BriefToken) -> Brief:
        brief = self._live.get(token.brief_id)
        if brief is None:
            raise Refused(f"{token.brief_id} is not a registered Brief")
        if token.generation != brief.generation:
            raise Refused(
                f"{token.brief_id} runs at generation {brief.generation}, "
                f"the token says {token.generation}"
            )
        return brief

    # -- the checks every call makes -----------------------------------------

    async def _admit(self, conn, token: BriefToken, space: str) -> Brief:
        """Generation, then space. The capability is the caller's to name."""
        brief = self._brief(token)
        await tree.check_generation(conn, token)
        if space != brief.space:
            raise Refused(f"{brief.id} is in {brief.space!r}, the call names {space!r}")
        return brief

    async def _refuse_after_terminal(self, conn, token: BriefToken) -> None:
        cur = await conn.execute(
            "SELECT 1 FROM tool_log WHERE brief_id = %s AND generation = %s "
            "AND event = 'terminal' LIMIT 1",
            (token.brief_id, token.generation),
        )
        if await cur.fetchone() is not None:
            raise Refused(
                f"{token.brief_id} generation {token.generation} already has "
                "a terminal row; nothing follows it"
            )

    async def _has_start(self, conn, token: BriefToken, seq: int) -> bool:
        cur = await conn.execute(
            "SELECT 1 FROM tool_log WHERE brief_id = %s AND generation = %s "
            "AND seq = %s AND event = 'tool.start' LIMIT 1",
            (token.brief_id, token.generation, seq),
        )
        return await cur.fetchone() is not None

    # -- the seven calls ----------------------------------------------------

    async def record_tool(self, token: BriefToken, record: ToolLogRecord) -> int:
        """One row, durable before this returns (seams §2.1)."""
        if (record.brief_id, record.generation) != (token.brief_id, token.generation):
            raise Refused(
                f"the row names {record.brief_id} generation {record.generation}, "
                f"the token {token.brief_id} generation {token.generation}"
            )
        async with await self.connect() as conn:
            brief = await self._admit(conn, token, record.space_id)
            await self._refuse_after_terminal(conn, token)
            if record.event in ("tool.start", "tool.end"):
                if record.tool is None or record.seq is None:
                    raise Refused(f"a {record.event} row names its tool and seq")
                _covered(brief, capability_for(brief, record.tool))
                if record.event == "tool.end" and not await self._has_start(
                    conn, token, record.seq
                ):
                    raise Refused(
                        f"seq {record.seq} has no tool.start to end for {brief.id}"
                    )
            elif record.event in ("question", "answer"):
                _covered(brief, capability_for(brief, "ask"))
                if record.question_id not in self._questions.get(brief.id, ()):
                    raise Refused(
                        f"question id {record.question_id!r} was not minted by "
                        "raise_question"
                    )
            elif record.event == "terminal":
                if record.outcome is None:
                    raise Refused("a terminal row names its outcome")
            row_id = await _insert(conn, record)
            await conn.commit()
        return row_id

    async def raise_question(
        self, token: BriefToken, text: str, tool_seq: int
    ) -> QuestionId:
        """Mints the id, writes the `question` row, returns the id (§2.4)."""
        async with await self.connect() as conn:
            brief = await self._admit(conn, token, self._brief(token).space)
            _covered(brief, capability_for(brief, "ask"))
            await self._refuse_after_terminal(conn, token)
            question_id = new_id()
            await _insert(
                conn,
                ToolLogRecord(
                    brief_id=brief.id,
                    generation=brief.generation,
                    space_id=brief.space,
                    seq=tool_seq,
                    event="question",
                    tool="ask",
                    question_id=question_id,
                    text=text,
                ),
            )
            await conn.commit()
        self._questions.setdefault(brief.id, set()).add(question_id)
        return question_id

    async def request_effect(
        self, token: BriefToken, action: "Action"
    ) -> "EffectOutcome":
        """Refused for every worker at M0 (seams ruling 9), and `Refused`
        while no broker is bound. The branch below is what the Protocol
        promises once one is: the action's own name at its class, on a
        connection with no open transaction (seams §3.10)."""
        if self.perform is None:
            raise Refused("no broker is bound; request_effect is refused at M0")
        brief = self._brief(token)
        async with await self.connect() as conn:
            await tree.check_generation(conn, token)
            await conn.commit()
        try:
            needed = Capability(
                name=action.action_type,
                effect_class=action.effect_class,
                scope=brief.space,
            )
        except (AttributeError, ValueError) as e:
            raise Refused(f"the action names no capability: {e}") from None
        _covered(brief, needed)
        async with await self.connect() as conn:
            await conn.set_autocommit(True)
            return await self.perform(conn, action, None, token=token)

    async def read_slice(self, token: BriefToken, query: SliceQuery) -> list[MemoryHit]:
        async with await self.connect() as conn:
            brief = await self._admit(conn, token, query.space)
            _covered(brief, capability_for(brief, "read"))
            await conn.commit()
        # The Brief's ceiling, whatever the query said (§2.4).
        narrowed = query.model_copy(update={"max_data_class": brief.max_data_class})
        return await self.memory.retrieve(narrowed)

    async def write_episode(self, token: BriefToken, write: EpisodeWrite) -> EpisodeId:
        async with await self.connect() as conn:
            brief = await self._admit(conn, token, write.space)
            _covered(brief, capability_for(brief, "write_episode"))
            episode_id = await self.memory.write(conn, write)
            await conn.commit()
        return episode_id

    async def propose_belief(self, token: BriefToken, proposal: BeliefProposal) -> str:
        async with await self.connect() as conn:
            brief = await self._admit(conn, token, proposal.space)
            _covered(brief, capability_for(brief, "propose_belief"))
            proposal_id = await self.memory.propose(conn, proposal)
            await conn.commit()
        return proposal_id

    async def delegate(self, token: BriefToken, request: DelegateRequest) -> BriefId:
        raise Refused("delegate is refused for every worker at M0 (seams §2.4)")


# ---------------------------------------------------------------------------
# The rows


async def _insert(conn, record: ToolLogRecord) -> int:
    cur = await conn.execute(
        "INSERT INTO tool_log (brief_id, generation, space_id, seq, event, tool, "
        "input, input_sha256, exit_status, stdout_sha256, stderr_sha256, artifact, "
        "artifact_sha256, duration_ms, question_id, text, outcome, report, "
        "schema_version) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            record.brief_id,
            record.generation,
            record.space_id,
            record.seq,
            record.event,
            record.tool,
            None if record.input is None else Jsonb(record.input),
            record.input_sha256,
            record.exit_status,
            record.stdout_sha256,
            record.stderr_sha256,
            record.artifact,
            record.artifact_sha256,
            record.duration_ms,
            record.question_id,
            record.text,
            record.outcome,
            None if record.report is None else Jsonb(record.report),
            record.schema_version,
        ),
    )
    row = await cur.fetchone()
    return int(row[0])


async def insert_terminal(
    conn, brief_id: BriefId, generation: int, space_id: str, *, outcome: str
) -> int | None:
    """The kernel-side terminal row `kernel/runs.py::stop` writes for a
    stopped generation. It bypasses only the generation check: the "no row
    after terminal" rule still holds, so when the adapter's own terminal
    landed first nothing is written and `None` comes back (plan 05,
    critique 11). Runs on the caller's connection."""
    cur = await conn.execute(
        "SELECT 1 FROM tool_log WHERE brief_id = %s AND generation = %s "
        "AND event = 'terminal' LIMIT 1",
        (brief_id, generation),
    )
    if await cur.fetchone() is not None:
        return None
    return await _insert(
        conn,
        ToolLogRecord(
            brief_id=brief_id,
            generation=generation,
            space_id=space_id,
            event="terminal",
            outcome=outcome,
        ),
    )


def _record(row: tuple) -> ToolLogRecord:
    data = dict(zip(COLUMNS, row))
    return ToolLogRecord(**data)


async def read_tool_log(
    conn, brief_id: BriefId, generation: int
) -> list[ToolLogRecord]:
    """Every row of one generation, in insertion order (seams §3.11)."""
    cur = await conn.execute(
        f"SELECT {', '.join(COLUMNS)} FROM tool_log "
        "WHERE brief_id = %s AND generation = %s ORDER BY id",
        (brief_id, generation),
    )
    return [_record(r) for r in await cur.fetchall()]


def invocations(rows: list[ToolLogRecord]) -> list[Invocation]:
    """Pair `tool.start` with `tool.end` by `(brief_id, generation, seq)`,
    never by adjacency (seams §5.2). A `terminal` closes every open seq.
    Order is by the start row's position, so a reader sees the calls in the
    order they were made."""
    starts: dict[tuple, ToolLogRecord] = {}
    ends: dict[tuple, ToolLogRecord] = {}
    terminals: set[tuple] = set()
    for row in rows:
        if row.event == "tool.start":
            starts.setdefault((row.brief_id, row.generation, row.seq), row)
        elif row.event == "tool.end":
            ends.setdefault((row.brief_id, row.generation, row.seq), row)
        elif row.event == "terminal":
            terminals.add((row.brief_id, row.generation))
    out: list[Invocation] = []
    for key, start in starts.items():
        end = ends.get(key)
        if end is not None:
            closed_by = "end"
        elif key[:2] in terminals:
            closed_by = "terminal"
        else:
            closed_by = None
        out.append(Invocation(start=start, end=end, closed_by=closed_by))
    return out


def latest_artifact_hashes(rows: list[ToolLogRecord]) -> dict[str, str]:
    """The last `artifact_sha256` recorded per artifact path, which is what
    `kernel/runs.py` checks a Report's refs against (seams §1.6)."""
    hashes: dict[str, str] = {}
    for row in rows:
        if row.event == "tool.end" and row.artifact and row.artifact_sha256:
            hashes[row.artifact] = row.artifact_sha256
    return hashes
