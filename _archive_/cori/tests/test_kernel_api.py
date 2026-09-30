"""The door: every call fenced, spaced, and capability-checked, and the
tool log's rules on the writer and the reader. Plan 05 task 4."""

import asyncio
import inspect
import uuid
from types import SimpleNamespace

import psycopg
import pytest

import kernel.tree as tree
from kernel import api
from kernel.api import Door, Refused
from schemas.brief import BriefToken
from schemas.memory import BeliefProposal, EpisodeWrite, SliceQuery
from schemas.records import ToolLogRecord
from tests.conftest import dsn, requires_postgres
from tests.tree_fakes import (
    approval,
    bind,
    contract,
    make_space,
    receipt,
    request,
    token,
)

pytestmark = requires_postgres


def connect():
    return psycopg.AsyncConnection.connect(dsn("kernel_rw"))


@pytest.fixture
def bound(space, monkeypatch):
    return bind(monkeypatch, {space: make_space(space, max_effect_class="act")})


async def approved_objective(kernel, space, **over):
    oid = await tree.open_objective(
        kernel, space=space, conversation_id="c1", contract=contract(**over)
    )
    await tree.approve(kernel, oid, approval("approved", oid, 1))
    return oid


async def executor(kernel, space, *, names=("read", "write", "bash", "ask")):
    oid = await approved_objective(kernel, space)
    brief = await tree.delegate(
        kernel,
        request(oid, "Executor", space=space, names=names),
        issuer=frozenset(),
        parent_brief="turn-1",
    )
    await kernel.commit()
    return oid, brief


async def scribe(kernel, space, *, names, effect_class="propose"):
    brief = await tree.delegate(
        kernel,
        request(
            None,
            "Scribe",
            space=space,
            names=names,
            effect_class=effect_class,
            max_data_class="OPERATOR",
        ),
        issuer=frozenset(),
        parent_brief="turn-1",
    )
    await kernel.commit()
    return brief


def row(brief, **over) -> ToolLogRecord:
    fields = dict(
        brief_id=brief.id,
        generation=brief.generation,
        space_id=brief.space,
        seq=1,
        event="tool.start",
        tool="read",
        input={"path": "/work/a"},
    )
    fields.update(over)
    return ToolLogRecord(**fields)


async def rows_of(kernel, brief_id):
    cur = await kernel.execute(
        "SELECT event, seq, tool FROM tool_log WHERE brief_id = %s ORDER BY id",
        (brief_id,),
    )
    return await cur.fetchall()


class FakeMemory:
    def __init__(self):
        self.calls: list[tuple] = []

    async def retrieve(self, query):
        self.calls.append(("retrieve", query))
        return []

    async def write(self, conn, write):
        self.calls.append(("write", conn, write))
        return "episode-1"

    async def propose(self, conn, proposal):
        self.calls.append(("propose", conn, proposal))
        return "proposal-1"


async def test_stale_generation_refused_with_no_row(kernel, space, bound):
    oid, brief = await executor(kernel, space)
    await tree.stop(kernel, oid, "stopped_by_person")
    await kernel.commit()
    door = Door(connect)
    door.register(brief)
    with pytest.raises(tree.StaleGeneration):
        await door.record_tool(token(brief), row(brief))
    with pytest.raises(tree.StaleGeneration):
        await door.raise_question(token(brief), "which?", 1)
    assert await rows_of(kernel, brief.id) == []


async def test_unregistered_brief_refused(kernel, space, bound):
    _, brief = await executor(kernel, space)
    door = Door(connect)
    with pytest.raises(Refused):
        await door.record_tool(token(brief), row(brief))
    assert await rows_of(kernel, brief.id) == []


async def test_wrong_space_refused(kernel, space, bound):
    _, brief = await executor(kernel, space)
    door = Door(connect)
    door.register(brief)
    with pytest.raises(Refused):
        await door.record_tool(token(brief), row(brief, space_id="elsewhere"))
    with pytest.raises(Refused):
        await door.read_slice(
            token(brief), SliceQuery(space="elsewhere", query="x", k=1)
        )
    with pytest.raises(Refused):
        await door.record_tool(
            BriefToken(brief_id=brief.id, generation=brief.generation + 1),
            row(brief, generation=brief.generation + 1),
        )
    assert await rows_of(kernel, brief.id) == []


async def test_missing_capability_refused(kernel, space, bound):
    _, brief = await executor(kernel, space, names=("read",))
    door = Door(connect)
    door.register(brief)
    for tool in ("write", "edit", "bash", "ask"):
        with pytest.raises(Refused):
            await door.record_tool(token(brief), row(brief, tool=tool))
    with pytest.raises(Refused):
        await door.raise_question(token(brief), "which?", 1)
    assert await door.record_tool(token(brief), row(brief, tool="read")) > 0
    assert await rows_of(kernel, brief.id) == [("tool.start", 1, "read")]


async def test_bash_needs_the_profiles_class(kernel, space, bound):
    # An Executor in a worktree holding bash@read: a bash row is a class 1 act
    # on a writable mount and is refused.
    _, brief = await executor(kernel, space, names=("read", "bash"))
    low = brief.model_copy(
        update={
            "capabilities": frozenset(
                c.model_copy(update={"effect_class": "read"})
                for c in brief.capabilities
            )
        }
    )
    door = Door(connect)
    door.register(low)
    with pytest.raises(Refused):
        await door.record_tool(token(low), row(low, tool="bash"))
    assert await door.record_tool(token(low), row(low, tool="read")) > 0


async def test_request_effect_refused_while_perform_is_none(kernel, space, bound):
    _, brief = await executor(kernel, space)
    door = Door(connect)
    door.register(brief)
    action = SimpleNamespace(action_type="push_branch", effect_class="propose")
    with pytest.raises(Refused):
        await door.request_effect(token(brief), action)


async def test_request_effect_refused_for_a_worker(kernel, space, bound):
    """With a broker bound, the action still needs its own name at its
    class, which no M0 worker holds."""
    _, brief = await executor(kernel, space)
    performed = []

    async def perform(conn, action, approval, *, token):
        performed.append(action)

    door = Door(connect, perform=perform)
    door.register(brief)
    action = SimpleNamespace(action_type="push_branch", effect_class="propose")
    with pytest.raises(Refused):
        await door.request_effect(token(brief), action)
    assert performed == []


async def test_tool_start_calls_no_tree_function_but_check_generation(
    kernel, space, bound, monkeypatch
):
    _, brief = await executor(kernel, space)
    calls: list[str] = []
    for name, fn in list(vars(tree).items()):
        if (
            inspect.iscoroutinefunction(fn)
            and fn.__module__ == tree.__name__
            and not name.startswith("_")
        ):

            def wrap(fn=fn, name=name):
                async def wrapped(*a, **k):
                    calls.append(name)
                    return await fn(*a, **k)

                return wrapped

            monkeypatch.setattr(tree, name, wrap())
    door = Door(connect)
    door.register(brief)
    await door.record_tool(token(brief), row(brief))
    assert calls == ["check_generation"]


async def test_row_after_terminal_refused(kernel, space, bound):
    _, brief = await executor(kernel, space)
    door = Door(connect)
    door.register(brief)
    await door.record_tool(token(brief), row(brief))
    await door.record_tool(
        token(brief),
        row(brief, event="terminal", seq=None, tool=None, outcome="failed"),
    )
    with pytest.raises(Refused):
        await door.record_tool(token(brief), row(brief, seq=2))
    with pytest.raises(Refused):
        await door.record_tool(
            token(brief), row(brief, event="tool.end", exit_status=0)
        )
    with pytest.raises(Refused):
        await door.record_tool(
            token(brief),
            row(brief, event="terminal", seq=None, tool=None, outcome="aborted"),
        )
    with pytest.raises(Refused):
        await door.raise_question(token(brief), "which?", 2)
    assert [r[0] for r in await rows_of(kernel, brief.id)] == [
        "tool.start",
        "terminal",
    ]


async def test_tool_end_without_start_refused(kernel, space, bound):
    _, brief = await executor(kernel, space)
    door = Door(connect)
    door.register(brief)
    with pytest.raises(Refused):
        await door.record_tool(
            token(brief), row(brief, event="tool.end", seq=7, exit_status=0)
        )
    await door.record_tool(token(brief), row(brief, seq=7))
    await door.record_tool(
        token(brief), row(brief, event="tool.end", seq=7, exit_status=0)
    )
    assert await rows_of(kernel, brief.id) == [
        ("tool.start", 7, "read"),
        ("tool.end", 7, "read"),
    ]


async def test_question_row_with_unminted_id_refused(kernel, space, bound):
    _, brief = await executor(kernel, space)
    door = Door(connect)
    door.register(brief)
    await door.record_tool(token(brief), row(brief, tool="ask", input={"q": 1}))
    with pytest.raises(Refused):
        await door.record_tool(
            token(brief),
            row(brief, event="question", tool="ask", question_id=uuid.uuid7().hex),
        )
    with pytest.raises(Refused):
        await door.record_tool(
            token(brief),
            row(brief, event="answer", tool="ask", question_id="q-nobody", text="x"),
        )
    qid = await door.raise_question(token(brief), "what is the name?", 1)
    await door.record_tool(
        token(brief),
        row(brief, event="answer", tool="ask", question_id=qid, text="Tom"),
    )
    assert [r[0] for r in await rows_of(kernel, brief.id)] == [
        "tool.start",
        "question",
        "answer",
    ]
    cur = await kernel.execute(
        "SELECT question_id, text, seq FROM tool_log WHERE brief_id = %s "
        "AND event = 'question'",
        (brief.id,),
    )
    assert await cur.fetchone() == (qid, "what is the name?", 1)


async def test_read_slice_forwards_the_briefs_max_data_class(kernel, space, bound):
    brief = await scribe(kernel, space, names=("read", "memory.episodic.write"))
    assert brief.max_data_class == "OPERATOR"
    memory = FakeMemory()
    door = Door(connect, memory=memory)
    door.register(brief)
    project = brief.model_copy(update={"max_data_class": "PROJECT"})
    door.register(project)
    await door.read_slice(
        token(project),
        SliceQuery(space=space, query="x", k=3, max_data_class="OPERATOR"),
    )
    assert memory.calls[-1][1].max_data_class == "PROJECT"
    door.register(brief)
    await door.read_slice(
        token(brief), SliceQuery(space=space, query="x", k=3, max_data_class="PROJECT")
    )
    assert memory.calls[-1][1].max_data_class == "OPERATOR"
    assert memory.calls[-1][1].k == 3


async def test_delegate_refused(kernel, space, bound):
    _, brief = await executor(kernel, space)
    door = Door(connect)
    door.register(brief)
    with pytest.raises(Refused):
        await door.delegate(token(brief), request(None, "Scribe", space=space))


async def test_memory_calls_reach_a_fake_kernel_memory(kernel, space, bound):
    brief = await scribe(
        kernel,
        space,
        names=("read", "memory.episodic.write", "memory.operator.propose"),
    )
    memory = FakeMemory()
    door = Door(connect, memory=memory)
    door.register(brief)
    write = EpisodeWrite(
        space=space,
        kind="turn",
        text="the person said hello",
        provenance=[],
        data_class="OPERATOR",
    )
    assert await door.write_episode(token(brief), write) == "episode-1"
    proposal = BeliefProposal(
        space=space,
        statement="the person prefers short replies",
        kind="preference",
        domain="style",
        supporting_events=[1],
        test="ask them",
        proposed_source_class="direct",
    )
    assert await door.propose_belief(token(brief), proposal) == "proposal-1"
    assert [c[0] for c in memory.calls] == ["write", "propose"]
    assert memory.calls[0][2] is write
    assert memory.calls[1][2] is proposal
    with pytest.raises(Refused):
        await door.write_episode(
            token(brief), write.model_copy(update={"space": "elsewhere"})
        )
    without = brief.model_copy(
        update={
            "capabilities": frozenset(c for c in brief.capabilities if c.name == "read")
        }
    )
    door.register(without)
    with pytest.raises(Refused):
        await door.write_episode(token(without), write)
    with pytest.raises(Refused):
        await door.propose_belief(token(without), proposal)
    assert len(memory.calls) == 2


async def test_read_tool_log_and_invocations(kernel, space, bound):
    _, brief = await executor(kernel, space)
    door = Door(connect)
    door.register(brief)
    t = token(brief)
    await door.record_tool(t, row(brief, seq=1, tool="write"))
    await door.record_tool(t, row(brief, seq=2, tool="bash"))
    await door.record_tool(
        t, row(brief, seq=2, tool="bash", event="tool.end", exit_status=0)
    )
    await door.record_tool(
        t,
        row(
            brief,
            seq=1,
            tool="write",
            event="tool.end",
            exit_status=0,
            artifact="/work/a",
            artifact_sha256="ab" * 32,
        ),
    )
    await door.record_tool(t, row(brief, seq=3, tool="ask"))
    await door.record_tool(
        t, row(brief, event="terminal", seq=None, tool=None, outcome="aborted")
    )
    rows = await api.read_tool_log(kernel, brief.id, brief.generation)
    assert [r.event for r in rows] == [
        "tool.start",
        "tool.start",
        "tool.end",
        "tool.end",
        "tool.start",
        "terminal",
    ]
    assert all(r.id is not None and r.at is not None for r in rows)
    invs = api.invocations(rows)
    assert [(i.start.seq, i.closed_by) for i in invs] == [
        (1, "end"),
        (2, "end"),
        (3, "terminal"),
    ]
    assert invs[0].end.artifact_sha256 == "ab" * 32
    assert invs[2].end is None
    assert api.latest_artifact_hashes(rows) == {"/work/a": "ab" * 32}
    assert await api.read_tool_log(kernel, brief.id, brief.generation + 1) == []


async def test_kernel_side_terminal_bypasses_only_the_generation_check(
    kernel, space, bound
):
    oid, brief = await executor(kernel, space)
    await tree.stop(kernel, oid, "stopped_by_person")
    await tree.confirm_stop(kernel, brief.id, receipt())
    assert (
        await api.insert_terminal(
            kernel, brief.id, brief.generation, brief.space, outcome="aborted"
        )
        is not None
    )
    assert (
        await api.insert_terminal(
            kernel, brief.id, brief.generation, brief.space, outcome="aborted"
        )
        is None
    )
    await kernel.commit()
    assert [r[0] for r in await rows_of(kernel, brief.id)] == ["terminal"]
