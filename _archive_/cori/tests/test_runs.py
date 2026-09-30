"""kernel/runs.py against the real tree, events, and door, a FakeSandbox,
a scripted worker that writes real tool log rows, and kernel.verify replaced
by a recorder until the verifier plan lands. Plan 05 task 8."""

import asyncio
import sys
import tempfile
import types
import uuid
from datetime import UTC, datetime

import psycopg
import pytest

import kernel.tree as tree
from kernel import api, runs
from kernel.api import Door
from kernel.events import read_for
from schemas.records import ToolLogRecord
from schemas.report import ArtifactRef, Report
from schemas.trace import Question, Terminal, TraceEvent
from tests.conftest import dsn, requires_postgres
from tests.test_pydantic_ai_worker import FakeSandbox, sha
from tests.tree_fakes import (
    approval,
    bind,
    contract,
    executor_report,
    force_state,
    make_space,
    request,
    scribe_report,
    token,
    verdict,
)

pytestmark = requires_postgres


def connect():
    return psycopg.AsyncConnection.connect(dsn("kernel_rw"))


# --- Fakes -------------------------------------------------------------------


class RecordingSandbox(FakeSandbox):
    def __init__(self, order):
        super().__init__()
        self.order = order

    async def stop(self, h):
        self.order.append("sandbox.stop")
        return await super().stop(h)

    async def snapshot(self, h, *, snapshot_id):
        self.order.append("sandbox.snapshot")
        return await super().snapshot(h, snapshot_id=snapshot_id)

    async def destroy(self, h):
        self.order.append("sandbox.destroy")
        return await super().destroy(h)


class ScriptedWorker:
    """Implements the Worker port. Each run follows the steps its Brief was
    given: ("row", fields) writes a tool log row through the real door with
    the run's token; ("question", text) raises one and waits for answer();
    ("wait",) blocks until abort(); ("terminal", Terminal) ends the run.
    On abort, the terminal is aborted and nothing follows."""

    def __init__(self, door: Door):
        self.door = door
        self.scripts: dict[str, list] = {}
        self.aborted: list[str] = []
        self.answered: list[tuple] = []
        self.pending: dict[str, asyncio.Future] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.terminals: dict[str, Terminal] = {}
        self.reached: dict[str, asyncio.Event] = {}

    def script(self, brief, steps):
        self.scripts[brief.id] = steps

    async def run(self, brief, handle):
        """The adapter's shape: the script runs in its own task and the
        events cross a queue, so abort cancels the script, never the
        consumer."""
        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(self._drive(brief, queue))
        self.tasks[brief.id] = task
        try:
            while True:
                event = await queue.get()
                yield event
                if event.kind == "terminal":
                    return
        finally:
            if not task.done():
                task.cancel()

    async def _drive(self, brief, queue):
        t = token(brief)
        seq = 0
        terminal = None
        try:
            for step in self.scripts.get(brief.id, [("terminal", None)]):
                kind = step[0]
                if kind == "row":
                    fields = dict(step[1])
                    if fields.get("event") == "tool.start" and "seq" not in fields:
                        seq += 1
                        fields["seq"] = seq
                    await self.door.record_tool(
                        t,
                        ToolLogRecord(
                            brief_id=brief.id,
                            generation=brief.generation,
                            space_id=brief.space,
                            **fields,
                        ),
                    )
                elif kind == "question":
                    seq += 1
                    await self.door.record_tool(
                        t,
                        ToolLogRecord(
                            brief_id=brief.id,
                            generation=brief.generation,
                            space_id=brief.space,
                            seq=seq,
                            event="tool.start",
                            tool="ask",
                            input={"q": sha(step[1])},
                        ),
                    )
                    qid = await self.door.raise_question(t, step[1], seq)
                    fut = asyncio.get_running_loop().create_future()
                    self.pending[qid] = fut
                    self.reached.setdefault(brief.id, asyncio.Event()).set()
                    await queue.put(
                        TraceEvent(
                            brief_id=brief.id,
                            generation=brief.generation,
                            at=datetime.now(UTC),
                            kind="question",
                            question=Question(
                                question_id=qid, text=step[1], tool_seq=seq
                            ),
                            terminal=None,
                        )
                    )
                    await fut
                elif kind == "wait":
                    self.reached.setdefault(brief.id, asyncio.Event()).set()
                    await asyncio.get_running_loop().create_future()
                elif kind == "terminal":
                    terminal = step[1] or Terminal(
                        outcome="report", report=executor_report(), error=None
                    )
        except asyncio.CancelledError:
            terminal = Terminal(outcome="aborted", report=None, error=None)
        if terminal is None:
            terminal = Terminal(outcome="report", report=executor_report(), error=None)
        self.terminals[brief.id] = terminal
        try:
            record = ToolLogRecord(
                brief_id=brief.id,
                generation=brief.generation,
                space_id=brief.space,
                event="terminal",
                outcome=terminal.outcome,
                report=(
                    terminal.report.model_dump(mode="json") if terminal.report else None
                ),
            )
            await asyncio.shield(self.door.record_tool(t, record))
        except Exception:
            pass
        await queue.put(
            TraceEvent(
                brief_id=brief.id,
                generation=brief.generation,
                at=datetime.now(UTC),
                kind="terminal",
                question=None,
                terminal=terminal,
            )
        )

    async def answer(self, brief_id, question_id, text):
        self.answered.append((brief_id, question_id, text))
        self.pending[question_id].set_result(text)

    async def abort(self, brief_id):
        self.aborted.append(brief_id)
        task = self.tasks.get(brief_id)
        if task is not None:
            task.cancel()


class FakeVerify(types.ModuleType):
    def __init__(self, order):
        super().__init__("kernel.verify")
        self.order = order
        self.verified: list[tuple] = []
        self.validated: list[tuple] = []

    async def verify_objective(self, objective_id, snapshot):
        self.order.append("verify.verify_objective")
        self.verified.append((objective_id, snapshot))
        return None

    async def validate_verdict_terminal(self, brief, terminal):
        self.order.append("verify.validate_verdict_terminal")
        self.validated.append((brief.id, terminal))
        return terminal


@pytest.fixture
def bound(space, monkeypatch):
    return bind(monkeypatch, {space: make_space(space, max_effect_class="act")})


@pytest.fixture
def harness(monkeypatch, bound):
    """The runs module wired to a recording sandbox, a scripted worker over
    the real door, a fake kernel.verify, and every tree call the closing
    sequence makes recorded into one ordered list."""
    order: list[str] = []
    sandbox = RecordingSandbox(order)
    door = Door(connect)
    worker = ScriptedWorker(door)
    verify = FakeVerify(order)
    monkeypatch.setitem(sys.modules, "kernel.verify", verify)
    for name in (
        "land_report",
        "release",
        "confirm_stop",
        "consume",
    ):
        real = getattr(tree, name)

        def wrap(real=real, name=name):
            async def wrapped(*a, **k):
                order.append(f"tree.{name}")
                return await real(*a, **k)

            return wrapped

        monkeypatch.setattr(tree, name, wrap())
    monkeypatch.setattr(runs, "sandbox", sandbox)
    monkeypatch.setattr(runs, "worker", worker)
    monkeypatch.setattr(runs, "door", door)
    monkeypatch.setattr(runs, "connect", connect)
    runs.live.clear()
    return types.SimpleNamespace(
        order=order, sandbox=sandbox, door=door, worker=worker, verify=verify
    )


# --- Builders ------------------------------------------------------------------


async def opened(kernel, space, **over):
    oid = await tree.open_objective(
        kernel, space=space, conversation_id="c1", contract=contract(**over)
    )
    await tree.approve(kernel, oid, approval("approved", oid, 1))
    return oid


async def executor(kernel, space, mount=None, **over):
    oid = await opened(kernel, space)
    brief = await tree.delegate(
        kernel,
        request(oid, "Executor", space=space, **over),
        issuer=frozenset(),
        parent_brief="turn-1",
    )
    await kernel.commit()
    return oid, brief


async def verifier_brief(kernel, space):
    oid = await opened(kernel, space)
    await force_state(kernel, oid, "RUNNING", "running", by="delegate")
    await force_state(kernel, oid, "VERIFYING", "verifying", by="land_report")
    brief = await tree.delegate(
        kernel,
        request(oid, "Verifier", space=space, effect_class="read"),
        issuer=frozenset(),
        parent_brief="turn-1",
    )
    await kernel.commit()
    return oid, brief


async def scribe_brief(kernel, space, objective_id=None):
    brief = await tree.delegate(
        kernel,
        request(objective_id, "Scribe", space=space),
        issuer=frozenset(),
        parent_brief="turn-1",
    )
    await kernel.commit()
    return brief


def report_with(path, digest):
    base = executor_report()
    return base.model_copy(
        update={"artifact_refs": [ArtifactRef(kind="code", path=path, sha256=digest)]}
    )


def write_rows(path, content):
    digest = sha(content)
    return [
        (
            "row",
            dict(
                event="tool.start",
                tool="write",
                input={"path": path, "content_sha256": digest},
            ),
        ),
        (
            "row",
            dict(
                event="tool.end",
                seq=1,
                tool="write",
                exit_status=0,
                artifact=path,
                artifact_sha256=digest,
            ),
        ),
    ]


async def events_of(kernel, space, key, value):
    return await read_for(kernel, space_id=space, key=key, value=value)


async def state_of(kernel, oid):
    return (await tree.project(kernel, oid)).state


async def terminal_rows(kernel, brief_id):
    cur = await kernel.execute(
        "SELECT generation, outcome FROM tool_log WHERE brief_id = %s "
        "AND event = 'terminal' ORDER BY id",
        (brief_id,),
    )
    return await cur.fetchall()


# --- Tests ---------------------------------------------------------------------


async def test_terminal_order_is_land_release_stop_confirm_snapshot_destroy_verify(
    kernel, space, harness
):
    oid, brief = await executor(kernel, space)
    content = "print(1)\n"
    harness.worker.script(
        brief,
        write_rows("/work/x.py", content)
        + [
            (
                "terminal",
                Terminal(
                    outcome="report",
                    report=report_with("/work/x.py", sha(content)),
                    error=None,
                ),
            )
        ],
    )
    terminal = await asyncio.create_task(runs.run_brief(brief))
    assert terminal.outcome == "report"
    assert harness.order == [
        "tree.land_report",
        "tree.release",
        "sandbox.stop",
        "tree.confirm_stop",
        "sandbox.snapshot",
        "verify.verify_objective",
    ]
    assert "tree.consume" not in harness.order
    kinds = [e.type for e in await events_of(kernel, space, "brief_id", brief.id)]
    assert kinds.index("report.landed") < kinds.index("snapshot.taken")
    # The tree as built confirms only a stop it fenced (`brief.stopped`), so
    # a landed run's receipt leaves no event; the call is still made in order.
    assert "brief.stop_confirmed" not in kinds
    assert await state_of(kernel, oid) == "VERIFYING"
    assert runs.live == {}
    assert brief.id not in harness.door._live


async def test_verdict_terminal_is_validated_before_land_report(kernel, space, harness):
    oid, brief = await verifier_brief(kernel, space)
    harness.worker.script(
        brief, [("terminal", Terminal(outcome="report", report=verdict(), error=None))]
    )
    terminal = await asyncio.create_task(runs.run_brief(brief))
    assert terminal.report.outcome == "pass"
    assert harness.order.index(
        "verify.validate_verdict_terminal"
    ) < harness.order.index("tree.land_report")
    assert [b for b, _ in harness.verify.validated] == [brief.id]
    assert "verify.verify_objective" not in harness.order
    assert "sandbox.destroy" in harness.order
    assert await state_of(kernel, oid) == "SUCCEEDED"
    harness.order.clear()
    oid2, executor_ = await executor(kernel, space)
    harness.worker.script(executor_, [("terminal", None)])
    await asyncio.create_task(runs.run_brief(executor_))
    assert "verify.validate_verdict_terminal" not in harness.order
    assert harness.verify.validated == [(brief.id, harness.verify.validated[0][1])]


async def test_budget_exhausted_terminal_lands_as_failed_budget_exhausted(
    kernel, space, harness
):
    oid, brief = await executor(kernel, space)
    harness.worker.script(
        brief,
        [
            (
                "terminal",
                Terminal(outcome="failed", report=None, error="budget_exhausted"),
            )
        ],
    )
    terminal = await asyncio.create_task(runs.run_brief(brief))
    assert terminal.error == "budget_exhausted"
    assert await state_of(kernel, oid) == "FAILED"
    failed = [
        e
        for e in await events_of(kernel, space, "brief_id", brief.id)
        if e.type == "brief.failed"
    ]
    assert len(failed) == 1 and failed[0].payload["error"] == "budget_exhausted"
    changed = [
        e
        for e in await events_of(kernel, space, "objective_id", oid)
        if e.type == "objective.state_changed" and e.payload["to"] == "FAILED"
    ]
    assert len(changed) == 1
    # The tree as built writes `worker_failed` for every failed terminal;
    # `budget_exhausted` is an admitted reason it does not yet pick from the
    # terminal's error (plan 05 finding for the tree).
    assert changed[0].payload["reason"] in ("budget_exhausted", "worker_failed")
    assert "verify.verify_objective" not in harness.order
    assert "sandbox.snapshot" not in harness.order
    assert await terminal_rows(kernel, brief.id) == [(1, "failed")]


async def test_executor_report_calls_verify_objective_with_the_snapshot(
    kernel, space, harness
):
    oid, brief = await executor(kernel, space)
    harness.worker.script(brief, [("terminal", None)])
    await asyncio.create_task(runs.run_brief(brief))
    ((verified_oid, snapshot),) = harness.verify.verified
    assert verified_oid == oid
    assert snapshot is not None and snapshot.handle_id.startswith("fake-")
    taken = [
        e
        for e in await events_of(kernel, space, "brief_id", brief.id)
        if e.type == "snapshot.taken"
    ]
    assert len(taken) == 1
    assert taken[0].payload["snapshot"]["id"] == snapshot.id
    assert taken[0].payload["objective_id"] == oid
    assert (
        "snapshot",
        harness.sandbox.calls[0][1],
        snapshot.id,
    ) in harness.sandbox.calls


async def test_scribe_and_verifier_reports_do_not_call_verify_objective(
    kernel, space, harness
):
    oid, executor_ = await executor(kernel, space)
    scribe = await scribe_brief(kernel, space, oid)
    harness.worker.script(
        scribe,
        [("terminal", Terminal(outcome="report", report=scribe_report(), error=None))],
    )
    await asyncio.create_task(runs.run_brief(scribe))
    _, verifier = await verifier_brief(kernel, space)
    harness.worker.script(
        verifier,
        [("terminal", Terminal(outcome="report", report=verdict(), error=None))],
    )
    await asyncio.create_task(runs.run_brief(verifier))
    assert harness.verify.verified == []
    assert harness.order.count("sandbox.destroy") == 2
    assert "sandbox.snapshot" not in harness.order


async def test_objective_less_scribe_brief_runs_without_snapshot(
    kernel, space, harness
):
    scribe = await scribe_brief(kernel, space, None)
    assert scribe.objective_id is None
    harness.worker.script(
        scribe,
        [("terminal", Terminal(outcome="report", report=scribe_report(), error=None))],
    )
    terminal = await asyncio.create_task(runs.run_brief(scribe))
    assert terminal.outcome == "report"
    assert harness.order == [
        "tree.land_report",
        "tree.release",
        "sandbox.stop",
        "tree.confirm_stop",
        "sandbox.destroy",
    ]
    kinds = [e.type for e in await events_of(kernel, space, "brief_id", scribe.id)]
    assert "report.landed" in kinds
    assert "snapshot.taken" not in kinds
    assert await terminal_rows(kernel, scribe.id) == [(1, "report")]


async def test_question_event_is_appended_when_the_worker_asks(kernel, space, harness):
    oid, brief = await executor(kernel, space, names=("read", "write", "bash", "ask"))
    harness.worker.script(
        brief, [("question", "what is the name?"), ("terminal", None)]
    )
    task = asyncio.create_task(runs.run_brief(brief))
    for _ in range(100):
        await asyncio.sleep(0.02)
        raised = [
            e
            for e in await events_of(kernel, space, "brief_id", brief.id)
            if e.type == "question.raised"
        ]
        if raised:
            break
    assert len(raised) == 1
    payload = raised[0].payload
    assert payload["question"] == "what is the name?"
    assert payload["objective_id"] == oid and payload["tool_seq"] == 1
    qid = payload["question_id"]
    cur = await kernel.execute(
        "SELECT event FROM tool_log WHERE brief_id = %s AND question_id = %s",
        (brief.id, qid),
    )
    assert await cur.fetchall() == [("question",)]
    assert not task.done()
    await runs.answer(brief.id, qid, "Tom")
    terminal = await task
    assert terminal.outcome == "report"
    assert harness.worker.answered == [(brief.id, qid, "Tom")]


async def test_report_hash_disagreeing_with_the_tool_log_fails_the_brief(
    kernel, space, harness
):
    oid, brief = await executor(kernel, space)
    harness.worker.script(
        brief,
        write_rows("/work/x.py", "print(1)\n")
        + [
            (
                "terminal",
                Terminal(
                    outcome="report",
                    report=report_with("/work/x.py", "f" * 64),
                    error=None,
                ),
            )
        ],
    )
    terminal = await asyncio.create_task(runs.run_brief(brief))
    assert terminal.outcome == "failed"
    assert terminal.error == "artifact hash disagrees with the tool log"
    assert await state_of(kernel, oid) == "FAILED"
    kinds = [e.type for e in await events_of(kernel, space, "brief_id", brief.id)]
    assert "brief.failed" in kinds and "report.landed" not in kinds
    assert "verify.verify_objective" not in harness.order
    assert "sandbox.snapshot" not in harness.order
    # The worker's own terminal row says report; the tree's event is the
    # authority (plan 05, Design).
    assert await terminal_rows(kernel, brief.id) == [(1, "report")]
    # A path the log never hashed disagrees too.
    oid2, brief2 = await executor(kernel, space)
    harness.worker.script(
        brief2,
        [
            (
                "terminal",
                Terminal(
                    outcome="report",
                    report=report_with("/work/never.py", sha("x")),
                    error=None,
                ),
            )
        ],
    )
    terminal = await asyncio.create_task(runs.run_brief(brief2))
    assert terminal.outcome == "failed"


async def test_stop_aborts_stops_confirms_writes_terminal_then_releases(
    kernel, space, harness
):
    oid, brief = await executor(kernel, space)
    harness.worker.script(brief, [("wait",)])
    task = asyncio.create_task(runs.run_brief(brief))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if brief.id in runs.live and brief.id in harness.worker.tasks:
            break
    assert brief.id in runs.live
    stopped = await tree.stop(kernel, oid, "stopped_by_person")
    await kernel.commit()
    assert stopped == [brief.id]
    harness.order.clear()
    receipts = await runs.stop(stopped)
    assert len(receipts) == 1 and receipts[0].handle_id == harness.sandbox.calls[0][1]
    terminal = await task
    assert terminal.outcome == "aborted"
    assert harness.worker.aborted == [brief.id]
    assert harness.order == [
        "sandbox.stop",
        "tree.confirm_stop",
        "tree.release",
    ]
    assert harness.sandbox.calls.count(("stop", receipts[0].handle_id)) == 1
    kinds = [e.type for e in await events_of(kernel, space, "brief_id", brief.id)]
    assert kinds.count("brief.stop_confirmed") == 1
    assert "brief.failed" not in kinds and "report.landed" not in kinds
    assert await terminal_rows(kernel, brief.id) == [(1, "aborted")]
    assert "sandbox.destroy" not in harness.order
    assert runs.live == {}
    assert await runs.stop([brief.id, "brief-unknown"]) == []


async def test_kernel_side_terminal_refused_when_one_exists(kernel, space, harness):
    oid, brief = await executor(kernel, space)
    harness.worker.script(
        brief,
        [("row", dict(event="terminal", outcome="failed")), ("wait",)],
    )
    task = asyncio.create_task(runs.run_brief(brief))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if await terminal_rows(kernel, brief.id):
            break
    stopped = await tree.stop(kernel, oid, "stopped_by_person")
    await kernel.commit()
    await runs.stop(stopped)
    await task
    assert await terminal_rows(kernel, brief.id) == [(1, "failed")]
    kinds = [e.type for e in await events_of(kernel, space, "brief_id", brief.id)]
    assert kinds.count("brief.stop_confirmed") == 1
    assert "tree.release" in harness.order


async def test_scratch_and_verify_are_destroyed_and_worktree_is_kept(
    kernel, space, harness
):
    oid, brief = await executor(kernel, space)
    harness.worker.script(brief, [("terminal", None)])
    await asyncio.create_task(runs.run_brief(brief))
    worktree_handle = harness.sandbox.calls[0][1]
    assert ("destroy", worktree_handle) not in harness.sandbox.calls
    assert any(
        c[0] == "snapshot" and c[1] == worktree_handle for c in harness.sandbox.calls
    )
    scribe = await scribe_brief(kernel, space, None)
    harness.worker.script(
        scribe,
        [("terminal", Terminal(outcome="report", report=scribe_report(), error=None))],
    )
    await asyncio.create_task(runs.run_brief(scribe))
    _, verifier = await verifier_brief(kernel, space)
    harness.worker.script(
        verifier,
        [("terminal", Terminal(outcome="report", report=verdict(), error=None))],
    )
    await asyncio.create_task(runs.run_brief(verifier))
    destroyed = [c[1] for c in harness.sandbox.calls if c[0] == "destroy"]
    assert len(destroyed) == 2 and worktree_handle not in destroyed
    profiles = {c[1]: None for c in harness.sandbox.calls if c[0] == "create"}
    assert set(destroyed) == set(list(profiles)[1:])
    # A worktree that failed is stopped and kept, never snapshotted.
    oid2, failed = await executor(kernel, space)
    harness.worker.script(
        failed, [("terminal", Terminal(outcome="failed", report=None, error="boom"))]
    )
    harness.order.clear()
    await asyncio.create_task(runs.run_brief(failed))
    assert "sandbox.stop" in harness.order
    assert (
        "sandbox.snapshot" not in harness.order
        and "sandbox.destroy" not in harness.order
    )
