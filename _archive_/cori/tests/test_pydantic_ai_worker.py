"""The PydanticAI worker against a scripted FunctionModel, a FakeSandbox over
a temp directory, and a FakeDoor that records every call. Plan 05 tasks 5,
6, 7. The fakes at the top are shared with tests/test_runs.py."""

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

import adapters.pydantic_ai as adapter
from adapters.pydantic_ai import PydanticAIWorker
from schemas.brief import Brief, ContextBlock, ContextSlice
from schemas.budget import Budget, Ceilings
from schemas.capability import Capability
from schemas.records import Invocation, ToolLogRecord
from schemas.sandbox import (
    ExecResult,
    SandboxHandle,
    SandboxProfile,
    SnapshotRef,
    StopReceipt,
)

PROMPTS = {
    "Executor": "You carry out one node's contract in /work.",
    "Verifier": "You judge one snapshot against its criteria.",
    "Scribe": "You write what happened into memory.",
    "Planner": "You plan.",
}


def sha(data) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


# --- Fakes -------------------------------------------------------------------


class FakeSandbox:
    """A mount on a temp directory; `exec` runs `sh -c` there. `read` and
    `write` are host-side, as the port promises."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.roots: dict[str, str] = {}

    def _root(self, h: SandboxHandle) -> str:
        return self.roots[h.id]

    def _host(self, h: SandboxHandle, path: str) -> str:
        assert path.startswith("/work"), path
        return os.path.join(self._root(h), path[len("/work") :].lstrip("/"))

    async def create(self, profile: SandboxProfile) -> SandboxHandle:
        h = SandboxHandle(
            id=f"fake-{uuid.uuid4().hex[:8]}",
            profile=profile,
            created_at=datetime.now(UTC),
        )
        self.roots[h.id] = profile.mount_source
        self.calls.append(("create", h.id))
        return h

    async def exec(self, h, cmd, *, timeout):
        self.calls.append(("exec", h.id, cmd))
        t0 = datetime.now(UTC)
        p = await asyncio.create_subprocess_exec(
            "sh",
            "-c",
            cmd,
            cwd=self._root(h),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await p.communicate()
        except asyncio.CancelledError:
            p.kill()
            raise
        return ExecResult(
            exit_status=p.returncode,
            stdout=out.decode(errors="replace"),
            stderr=err.decode(errors="replace"),
            duration_ms=int((datetime.now(UTC) - t0).total_seconds() * 1000),
            timed_out=False,
        )

    async def read(self, h, path) -> bytes:
        self.calls.append(("read", h.id, path))
        with open(self._host(h, path), "rb") as f:
            return f.read()

    async def write(self, h, path, data: bytes) -> None:
        self.calls.append(("write", h.id, path))
        if h.profile.readonly:
            raise PermissionError(f"{h.profile.name} is read-only")
        host = self._host(h, path)
        os.makedirs(os.path.dirname(host), exist_ok=True)
        with open(host, "wb") as f:
            f.write(data)

    async def snapshot(self, h, *, snapshot_id) -> SnapshotRef:
        self.calls.append(("snapshot", h.id, snapshot_id))
        files = {}
        root = self._root(h)
        for dirpath, _, names in os.walk(root):
            for n in names:
                p = os.path.join(dirpath, n)
                with open(p, "rb") as f:
                    files[os.path.relpath(p, root)] = sha(f.read())
        return SnapshotRef(
            id=snapshot_id,
            handle_id=h.id,
            path=f"{root}.tar",
            sha256=sha(json.dumps(files, sort_keys=True)),
            files=files,
            taken_at=datetime.now(UTC),
        )

    async def stop(self, h) -> StopReceipt:
        self.calls.append(("stop", h.id))
        now = datetime.now(UTC)
        return StopReceipt(
            handle_id=h.id, killed_at=now, confirmed_dead_at=now, probe="fake"
        )

    async def destroy(self, h) -> None:
        self.calls.append(("destroy", h.id))


class FakeDoor:
    """Records every call in order and assigns row ids. `refuse_terminal`
    makes the terminal row fail as a stale kernel would."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.rows: list[ToolLogRecord] = []
        self.questions: list[str] = []
        self.refuse_terminal = False

    async def record_tool(self, token, record):
        self.calls.append(("record_tool", token, record))
        if record.event == "terminal" and self.refuse_terminal:
            raise RuntimeError("stale generation")
        row = record.model_copy(
            update={"id": len(self.rows) + 1, "at": datetime.now(UTC)}
        )
        self.rows.append(row)
        return row.id

    async def raise_question(self, token, text, tool_seq):
        qid = f"q-{uuid.uuid4().hex[:8]}"
        self.calls.append(("raise_question", token, text, tool_seq))
        self.questions.append(qid)
        self.rows.append(
            ToolLogRecord(
                id=len(self.rows) + 1,
                brief_id=token.brief_id,
                generation=token.generation,
                space_id="s",
                seq=tool_seq,
                event="question",
                tool="ask",
                question_id=qid,
                text=text,
                at=datetime.now(UTC),
            )
        )
        return qid

    async def request_effect(self, token, action):
        raise AssertionError("no worker tool calls request_effect")

    async def read_slice(self, token, query):
        raise AssertionError("no worker tool calls read_slice")

    async def write_episode(self, token, write):
        self.calls.append(("write_episode", token, write))
        return f"episode-{sum(1 for c in self.calls if c[0] == 'write_episode')}"

    async def propose_belief(self, token, proposal):
        self.calls.append(("propose_belief", token, proposal))
        return f"proposal-{sum(1 for c in self.calls if c[0] == 'propose_belief')}"

    async def delegate(self, token, request):
        raise AssertionError("no worker tool calls delegate")

    def events(self) -> list[str]:
        return [r.event for r in self.rows]

    def by_seq(self, seq: int) -> list[ToolLogRecord]:
        return [r for r in self.rows if r.seq == seq]


def pair(rows: list[ToolLogRecord]) -> list[Invocation]:
    """The pairing `kernel.api.invocations` does, without the kernel: group
    by seq, never by adjacency."""
    starts = {r.seq: r for r in rows if r.event == "tool.start"}
    ends = {r.seq: r for r in rows if r.event == "tool.end"}
    terminal = any(r.event == "terminal" for r in rows)
    out = []
    for seq, start in sorted(starts.items()):
        end = ends.get(seq)
        closed = "end" if end else ("terminal" if terminal else None)
        out.append(Invocation(start=start, end=end, closed_by=closed))
    return out


# --- The scripted model -------------------------------------------------------


class Script:
    """Turn by turn: a list of (tool, args) calls, or ("final", output) for
    the output tool. Counts the model calls it serves."""

    def __init__(self, turns):
        self.turns = turns
        self.calls = 0
        self.tools_offered: list[list[str]] = []
        self.output_tools: list[list[str]] = []

    def model(self) -> FunctionModel:
        async def stream(messages, info):
            index = self.calls
            self.calls += 1
            self.tools_offered.append(sorted(t.name for t in info.function_tools))
            self.output_tools.append([t.name for t in info.output_tools])
            turn = self.turns[min(index, len(self.turns) - 1)]
            deltas = {}
            for k, (name, args) in enumerate(turn):
                if name == "final":
                    name = info.output_tools[0].name
                deltas[k] = DeltaToolCall(
                    name=name, json_args=json.dumps(args), tool_call_id=f"c{index}-{k}"
                )
            yield deltas

        return FunctionModel(stream_function=stream)


REPORT = {
    "artifact_refs": [],
    "evidence": [],
    "assumption_deltas": [],
    "summary": "done",
}


# --- Briefs ------------------------------------------------------------------


def cap(name, effect_class="propose", space="s"):
    return Capability(name=name, effect_class=effect_class, scope=space)


def profile(name, mount_source, space="s") -> SandboxProfile:
    return SandboxProfile(
        name=name,
        space=space,
        mount_source=mount_source,
        readonly=name != "worktree",
        network="hostonly",
        key=f"key-{name}",
        env={},
    )


def make_brief(
    mount_source,
    *,
    agent_class="Executor",
    names=("read", "write", "bash", "ask"),
    effect_class="propose",
    profile_name="worktree",
    report_schema="Report",
    objective_id="obj-1",
    instruction=None,
    max_data_class="PROJECT",
    space="s",
    blocks=None,
) -> Brief:
    blocks = (
        blocks
        if blocks is not None
        else [
            ContextBlock(kind="voice", data_class="PROJECT", text="plain", sources=[]),
            ContextBlock(
                kind="node", data_class="PROJECT", text="the node", sources=[]
            ),
        ]
    )
    caps = frozenset(cap(n, effect_class, space) for n in names)
    return Brief(
        id=f"brief-{uuid.uuid4().hex[:8]}",
        objective_id=objective_id,
        space=space,
        agent_class=agent_class,
        harness="pydantic_ai",
        generation=1,
        context_slice=ContextSlice(
            space=space, blocks=blocks, sha256=ContextSlice.digest(blocks)
        ),
        instruction=instruction,
        max_data_class=max_data_class,
        budget=Budget(usd_micros=1000),
        ceilings=Ceilings(
            max_effect_class="act",
            deadline=datetime.now(UTC) + timedelta(hours=1),
            max_data_class="OPERATOR",
        ),
        capabilities=caps,
        sandbox_profile=profile(profile_name, mount_source, space),
        model_ref="claude-haiku-4-5-20251001",
        gateway_token=SecretStr("token-" + uuid.uuid4().hex),
        report_schema=report_schema,
        issued_at=datetime.now(UTC),
    )


@pytest.fixture
def mount():
    d = tempfile.mkdtemp(prefix="cori-worker-test-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def scripted(monkeypatch):
    """Binds a Script's model as the adapter's model factory."""

    def bind(turns) -> Script:
        script = Script(turns)
        monkeypatch.setattr(adapter, "_model_for", lambda brief, url: script.model())
        return script

    return bind


async def drive(worker, brief, handle):
    """Run to the terminal, collecting every event."""
    events = []
    async for ev in worker.run(brief, handle):
        events.append(ev)
    return events


async def fresh(mount, scripted, turns, **over):
    sandbox = FakeSandbox()
    door = FakeDoor()
    brief = make_brief(mount, **over)
    handle = await sandbox.create(brief.sandbox_profile)
    script = scripted(turns)
    worker = PydanticAIWorker(sandbox, door, "http://127.0.0.1:1/", PROMPTS)
    return worker, brief, handle, sandbox, door, script


# --- Task 5: the four sandbox tools --------------------------------------------


async def test_start_row_is_recorded_before_the_port_call(mount, scripted):
    worker, brief, handle, sandbox, door, _ = await fresh(
        mount,
        scripted,
        [[("write", {"path": "/work/a.txt", "content": "hi"})], [("final", REPORT)]],
    )
    order = []
    real_record = door.record_tool
    real_write = sandbox.write

    async def record(token, record):
        order.append(("row", record.event, record.seq))
        return await real_record(token, record)

    async def write(h, path, data):
        order.append(("port", "write", path))
        return await real_write(h, path, data)

    door.record_tool = record
    sandbox.write = write
    events = await drive(worker, brief, handle)
    assert events[-1].kind == "terminal" and events[-1].terminal.outcome == "report"
    assert order[:2] == [("row", "tool.start", 1), ("port", "write", "/work/a.txt")]
    assert order[2] == ("row", "tool.end", 1)
    assert order[-1] == ("row", "terminal", None)
    start = door.by_seq(1)[0]
    assert start.tool == "write"
    assert start.input == {"path": "/work/a.txt", "content_sha256": sha("hi")}
    assert "hi" not in json.dumps(start.input)
    assert start.input_sha256 == sha(
        json.dumps(start.input, sort_keys=True, separators=(",", ":"))
    )


async def test_write_end_hash_equals_host_file(mount, scripted):
    worker, brief, handle, sandbox, door, _ = await fresh(
        mount,
        scripted,
        [
            [("write", {"path": "/work/greeting.txt", "content": "Hello, Tom!\n"})],
            [("edit", {"path": "/work/greeting.txt", "old": "Tom", "new": "Ann"})],
            [("read", {"path": "/work/greeting.txt"})],
            [("final", REPORT)],
        ],
    )
    await drive(worker, brief, handle)
    with open(os.path.join(mount, "greeting.txt"), "rb") as f:
        on_disk = f.read()
    assert on_disk == b"Hello, Ann!\n"
    write_end = [r for r in door.rows if r.event == "tool.end" and r.tool == "write"][0]
    edit_end = [r for r in door.rows if r.event == "tool.end" and r.tool == "edit"][0]
    assert write_end.artifact == "/work/greeting.txt"
    assert write_end.artifact_sha256 == sha(b"Hello, Tom!\n")
    assert edit_end.artifact_sha256 == sha(on_disk)
    assert write_end.exit_status == 0 and write_end.duration_ms is not None
    edit_start = [r for r in door.rows if r.event == "tool.start" and r.tool == "edit"][
        0
    ]
    assert edit_start.input == {
        "path": "/work/greeting.txt",
        "old_sha256": sha("Tom"),
        "new_sha256": sha("Ann"),
        "old_length": 3,
        "new_length": 3,
    }
    read_end = [r for r in door.rows if r.event == "tool.end" and r.tool == "read"][0]
    assert read_end.stdout_sha256 == sha(on_disk)


async def test_two_calls_in_one_turn_pair_by_seq(mount, scripted):
    worker, brief, handle, sandbox, door, _ = await fresh(
        mount,
        scripted,
        [
            [
                ("write", {"path": "/work/a.txt", "content": "a"}),
                ("bash", {"command": "sleep 0.05; echo hi; echo err >&2; exit 3"}),
            ],
            [("final", REPORT)],
        ],
    )
    await drive(worker, brief, handle)
    starts = [r for r in door.rows if r.event == "tool.start"]
    assert {r.seq for r in starts} == {1, 2}
    assert {r.tool for r in starts} == {"write", "bash"}
    invs = pair(door.rows)
    assert len(invs) == 2
    for inv in invs:
        assert inv.end is not None and inv.end.seq == inv.start.seq
        assert inv.end.tool == inv.start.tool
        assert inv.closed_by == "end"
    bash_end = [i.end for i in invs if i.start.tool == "bash"][0]
    assert bash_end.exit_status == 3
    assert bash_end.stdout_sha256 == sha("hi\n")
    assert bash_end.stderr_sha256 == sha("err\n")
    assert (
        "exec",
        handle.id,
        "sleep 0.05; echo hi; echo err >&2; exit 3",
    ) in sandbox.calls


async def test_report_artifact_hashes_are_the_bridges(mount, scripted):
    report = dict(
        REPORT,
        artifact_refs=[{"kind": "code", "path": "/work/x.py", "sha256": "0" * 64}],
    )
    worker, brief, handle, sandbox, door, script = await fresh(
        mount,
        scripted,
        [
            [("bash", {"command": "printf 'print(1)\\n' > x.py"})],
            [("final", report)],
        ],
    )
    events = await drive(worker, brief, handle)
    terminal = events[-1].terminal
    assert terminal.outcome == "report"
    expected = sha(b"print(1)\n")
    assert terminal.report.artifact_refs[0].sha256 == expected
    assert terminal.report.artifact_refs[0].path == "/work/x.py"
    reads = [
        r for r in door.rows if r.event == "tool.end" and r.artifact == "/work/x.py"
    ]
    assert reads and reads[-1].artifact_sha256 == expected and reads[-1].tool == "read"
    assert door.rows[-1].event == "terminal"
    assert door.rows[-1].report["artifact_refs"][0]["sha256"] == expected
    assert script.calls == 2


async def test_missing_artifact_is_a_retry(mount, scripted):
    bad = dict(
        REPORT,
        artifact_refs=[{"kind": "code", "path": "/work/nope.py", "sha256": "0" * 64}],
    )
    worker, brief, handle, sandbox, door, script = await fresh(
        mount, scripted, [[("final", bad)], [("final", REPORT)]]
    )
    events = await drive(worker, brief, handle)
    assert events[-1].terminal.outcome == "report"
    assert events[-1].terminal.report.artifact_refs == []
    assert script.calls == 2
    failed = [r for r in door.rows if r.event == "tool.end" and r.exit_status == 1]
    assert failed and failed[0].tool == "read"


async def test_path_outside_mount_is_refused(mount, scripted):
    outside = os.path.join(tempfile.gettempdir(), f"cori-outside-{uuid.uuid4().hex}")
    worker, brief, handle, sandbox, door, script = await fresh(
        mount,
        scripted,
        [
            [("write", {"path": outside, "content": "x"})],
            [("read", {"path": "/work/../etc/passwd"})],
            [("edit", {"path": "/etc/hosts", "old": "a", "new": "b"})],
            [("final", REPORT)],
        ],
    )
    events = await drive(worker, brief, handle)
    assert events[-1].terminal.outcome == "report"
    assert not os.path.exists(outside)
    assert [c[0] for c in sandbox.calls] == ["create"]
    assert door.events() == ["terminal"]
    assert script.calls == 4


async def test_system_prompt_is_the_class_paragraph_then_the_blocks(mount):
    brief = make_brief(mount)
    text = adapter.system_prompt(PROMPTS["Executor"], brief)
    assert text.startswith(PROMPTS["Executor"])
    assert "## voice\n\nplain" in text and "## node\n\nthe node" in text
    assert text.index("## voice") < text.index("## node")
    assert brief.gateway_token.get_secret_value() not in text


async def test_terminal_row_refused_is_swallowed_and_the_event_still_comes(
    mount, scripted
):
    worker, brief, handle, sandbox, door, _ = await fresh(
        mount, scripted, [[("final", REPORT)]]
    )
    door.refuse_terminal = True
    events = await drive(worker, brief, handle)
    assert events[-1].kind == "terminal"
    assert events[-1].terminal.outcome == "report"
    assert door.events() == []


# --- Task 6: ask, answer, abort -------------------------------------------------


async def asked(mount, scripted, turns=None):
    """Runs until the question event and returns everything needed to
    answer or abort it."""
    turns = turns or [
        [("ask", {"question": "what is the name?"})],
        [("write", {"path": "/work/greeting.txt", "content": "Hello, Tom!\n"})],
        [("final", REPORT)],
    ]
    worker, brief, handle, sandbox, door, script = await fresh(mount, scripted, turns)
    gen = worker.run(brief, handle)
    first = await gen.__anext__()
    assert first.kind == "question"
    return worker, brief, gen, first, door, script


async def test_ask_blocks_and_the_model_is_not_called_until_answer(mount, scripted):
    worker, brief, gen, question, door, script = await asked(mount, scripted)
    assert script.calls == 1
    assert question.question.text == "what is the name?"
    assert question.question.tool_seq == 1
    assert door.events() == ["tool.start", "question"]
    await asyncio.sleep(0.1)
    assert script.calls == 1
    assert door.events() == ["tool.start", "question"]
    await worker.answer(brief.id, question.question.question_id, "Tom")
    rest = [ev async for ev in gen]
    assert [ev.kind for ev in rest] == ["terminal"]
    assert rest[0].terminal.outcome == "report"
    assert script.calls == 3


async def test_answer_writes_answer_and_end_rows_with_the_ask_seq(mount, scripted):
    worker, brief, gen, question, door, _ = await asked(mount, scripted)
    qid = question.question.question_id
    await worker.answer(brief.id, qid, "Tom")
    async for _ in gen:
        pass
    events = door.events()
    assert events[:4] == ["tool.start", "question", "answer", "tool.end"]
    ask_rows = door.by_seq(1)
    assert [r.event for r in ask_rows] == [
        "tool.start",
        "question",
        "answer",
        "tool.end",
    ]
    assert all(r.tool == "ask" for r in ask_rows)
    answer = ask_rows[2]
    assert answer.question_id == qid and answer.text == "Tom"
    assert ask_rows[3].stdout_sha256 == sha("Tom") and ask_rows[3].exit_status == 0
    assert door.rows[0].input == {"question_sha256": sha("what is the name?")}
    assert door.rows[1].text == "what is the name?"
    with pytest.raises(KeyError):
        await worker.answer(brief.id, qid, "again")


async def test_abort_during_ask_ends_with_terminal_aborted_and_nothing_after(
    mount, scripted
):
    worker, brief, gen, question, door, script = await asked(mount, scripted)
    await worker.abort(brief.id)
    rest = [ev async for ev in gen]
    assert [ev.kind for ev in rest] == ["terminal"]
    assert rest[0].terminal.outcome == "aborted"
    assert rest[0].terminal.report is None
    assert door.events() == ["tool.start", "question", "terminal"]
    assert door.rows[-1].outcome == "aborted"
    await asyncio.sleep(0.05)
    assert script.calls == 1
    assert door.events() == ["tool.start", "question", "terminal"]
    with pytest.raises(KeyError):
        await worker.answer(brief.id, question.question.question_id, "late")


async def test_invocations_marks_the_open_ask_closed_by_terminal(mount, scripted):
    worker, brief, gen, question, door, _ = await asked(
        mount,
        scripted,
        [
            [("write", {"path": "/work/a.txt", "content": "a"})],
            [("ask", {"question": "and now?"})],
            [("final", REPORT)],
        ],
    )
    await worker.abort(brief.id)
    async for _ in gen:
        pass
    invs = pair(door.rows)
    assert [(i.start.tool, i.closed_by) for i in invs] == [
        ("write", "end"),
        ("ask", "terminal"),
    ]
    assert invs[1].end is None and invs[1].start.seq == 2


async def test_abort_mid_bash_leaves_the_start_without_an_end(mount, scripted):
    worker, brief, handle, sandbox, door, script = await fresh(
        mount, scripted, [[("bash", {"command": "sleep 5"})], [("final", REPORT)]]
    )
    gen = worker.run(brief, handle)
    started = asyncio.ensure_future(gen.__anext__())
    for _ in range(50):
        await asyncio.sleep(0.02)
        if door.events() == ["tool.start"]:
            break
    assert door.events() == ["tool.start"]
    await worker.abort(brief.id)
    event = await started
    assert event.kind == "terminal" and event.terminal.outcome == "aborted"
    assert door.events() == ["tool.start", "terminal"]
    assert pair(door.rows)[0].closed_by == "terminal"


# --- Task 7: capability-gated tools, the Scribe's tools, refusals --------------


async def test_registered_tools_match_capabilities(mount, scripted):
    worker, brief, handle, _, door, script = await fresh(
        mount, scripted, [[("final", REPORT)]], names=("read", "write")
    )
    await drive(worker, brief, handle)
    assert script.tools_offered == [["edit", "read", "write"]]
    worker, brief, handle, _, door, script = await fresh(
        mount, scripted, [[("final", REPORT)]], names=("read", "bash", "ask")
    )
    await drive(worker, brief, handle)
    assert script.tools_offered == [["ask", "bash", "read"]]
    worker, brief, handle, _, door, script = await fresh(
        mount,
        scripted,
        [[("final", {"written": [], "proposed": [], "could_not": []})]],
        agent_class="Scribe",
        names=("read", "memory.episodic.write", "memory.operator.propose"),
        profile_name="scratch",
        report_schema="ScribeReport",
        objective_id=None,
    )
    await drive(worker, brief, handle)
    assert script.tools_offered == [["propose_belief", "read", "write_episode"]]


VERDICT = {
    "outcome": "pass",
    "predicted_failure": 0.1,
    "criteria": [{"criterion": "pytest green", "met": True, "reason": "ran"}],
    "scope_findings": [],
    "summary": "fine",
}


async def verifier(mount, scripted, turns):
    return await fresh(
        mount,
        scripted,
        turns,
        agent_class="Verifier",
        names=("read", "bash"),
        effect_class="read",
        profile_name="verify",
        report_schema="Verdict",
    )


async def test_verifier_brief_offers_read_and_bash_and_no_ask(mount, scripted):
    worker, brief, handle, _, door, script = await verifier(
        mount, scripted, [[("final", VERDICT)]]
    )
    events = await drive(worker, brief, handle)
    assert script.tools_offered == [["bash", "read"]]
    assert script.output_tools == [["final_result"]]
    assert events[-1].terminal.outcome == "report"
    assert events[-1].terminal.report.outcome == "pass"


async def test_verifier_brief_validates_a_verdict(mount, scripted):
    wrong = dict(
        VERDICT,
        criteria=[{"criterion": "pytest green", "met": False, "reason": "red"}],
    )
    fixed = dict(wrong, outcome="fail")
    worker, brief, handle, _, door, script = await verifier(
        mount, scripted, [[("final", wrong)], [("final", fixed)]]
    )
    events = await drive(worker, brief, handle)
    assert script.calls == 2
    assert events[-1].terminal.outcome == "report"
    assert events[-1].terminal.report.outcome == "fail"
    assert door.rows[-1].report["outcome"] == "fail"


async def test_verifier_write_is_refused_by_the_readonly_mount(mount, scripted):
    """A verify Brief carries no write, so the model never sees the tool;
    the readonly fake refuses a write even when asked directly."""
    worker, brief, handle, sandbox, door, script = await verifier(
        mount, scripted, [[("bash", {"command": "echo ok"})], [("final", VERDICT)]]
    )
    await drive(worker, brief, handle)
    assert "write" not in script.tools_offered[0]
    with pytest.raises(PermissionError):
        await sandbox.write(handle, "/work/x", b"x")


@pytest.mark.usefixtures("bound_tree")
async def test_verifier_bash_row_is_accepted_by_the_door(mount, kernel, space):
    """A Brief holding exactly read@read and bash@read in a verify profile:
    the real door accepts its bash row, because bash is a read there."""
    import kernel.tree as tree
    from kernel.api import Door
    from tests.conftest import dsn
    from tests.tree_fakes import approval, contract, force_state, request, token
    import psycopg

    oid = await tree.open_objective(
        kernel, space=space, conversation_id="c1", contract=contract()
    )
    await tree.approve(kernel, oid, approval("approved", oid, 1))
    await force_state(kernel, oid, "RUNNING", "running", by="delegate")
    await force_state(kernel, oid, "VERIFYING", "verifying", by="land_report")
    brief = await tree.delegate(
        kernel,
        request(oid, "Verifier", space=space, effect_class="read"),
        issuer=frozenset(),
        parent_brief="turn-1",
    )
    await kernel.commit()
    assert {(c.name, c.effect_class) for c in brief.capabilities} == {
        ("read", "read"),
        ("bash", "read"),
    }
    assert brief.sandbox_profile.name == "verify"
    door = Door(lambda: psycopg.AsyncConnection.connect(dsn("kernel_rw")))
    door.register(brief)
    row = ToolLogRecord(
        brief_id=brief.id,
        generation=brief.generation,
        space_id=brief.space,
        seq=1,
        event="tool.start",
        tool="bash",
        input={"command": "pytest -q"},
    )
    assert await door.record_tool(token(brief), row) > 0
    from schemas.capability import Refused

    with pytest.raises(Refused):
        await door.record_tool(
            token(brief), row.model_copy(update={"seq": 2, "tool": "write"})
        )
    with pytest.raises(Refused):
        await door.record_tool(
            token(brief), row.model_copy(update={"seq": 3, "tool": "ask"})
        )


@pytest.fixture
def bound_tree(space, monkeypatch):
    from tests.conftest import requires_postgres
    from tests.tree_fakes import bind, make_space

    if requires_postgres.args[0]:
        pytest.skip(requires_postgres.kwargs["reason"])
    return bind(monkeypatch, {space: make_space(space, max_effect_class="act")})


async def scribe(mount, scripted, turns, **over):
    over.setdefault(
        "names", ("read", "memory.episodic.write", "memory.operator.propose")
    )
    return await fresh(
        mount,
        scripted,
        turns,
        agent_class="Scribe",
        profile_name="scratch",
        report_schema="ScribeReport",
        objective_id=None,
        max_data_class="OPERATOR",
        instruction="Write down what the person decided.",
        **over,
    )


async def test_scribe_brief_without_an_objective_runs(mount, scripted):
    worker, brief, handle, _, door, script = await scribe(
        mount, scripted, [[("final", {"written": [], "proposed": [], "could_not": []})]]
    )
    assert brief.objective_id is None
    events = await drive(worker, brief, handle)
    assert events[-1].terminal.outcome == "report"
    assert events[-1].terminal.report.written == []
    assert door.events() == ["terminal"]


async def test_scribe_run_writes_an_episode_through_the_door_and_reports_its_id(
    mount, scripted
):
    secret_text = "the person decided to move the launch to Friday"
    worker, brief, handle, _, door, script = await scribe(
        mount,
        scripted,
        [
            [
                (
                    "write_episode",
                    {
                        "kind": "decision",
                        "text": secret_text,
                        "data_class": "OPERATOR",
                        "provenance": [41, 42],
                        "regards": "conv-1",
                    },
                )
            ],
            [
                (
                    "propose_belief",
                    {
                        "statement": "the person prefers Friday launches",
                        "kind": "preference",
                        "domain": "planning",
                        "supporting_events": [42],
                        "test": "ask before the next launch",
                        "proposed_source_class": "decision",
                    },
                )
            ],
            [
                (
                    "final",
                    {
                        "written": ["episode-1"],
                        "proposed": ["proposal-1"],
                        "could_not": [],
                    },
                )
            ],
        ],
    )
    events = await drive(worker, brief, handle)
    report = events[-1].terminal.report
    assert report.written == ["episode-1"] and report.proposed == ["proposal-1"]
    calls = [c for c in door.calls if c[0] in ("write_episode", "propose_belief")]
    assert [c[0] for c in calls] == ["write_episode", "propose_belief"]
    write = calls[0][2]
    assert write.space == brief.space and write.text == secret_text
    assert write.kind == "decision" and write.data_class == "OPERATOR"
    proposal = calls[1][2]
    assert proposal.space == brief.space and proposal.supporting_events == [42]
    dumped = json.dumps([r.model_dump(mode="json") for r in door.rows])
    assert secret_text not in dumped
    assert "prefers Friday" not in dumped
    start = door.by_seq(1)[0]
    assert start.tool == "write_episode"
    assert start.input == {
        "kind": "decision",
        "data_class": "OPERATOR",
        "regards": "conv-1",
        "provenance": [41, 42],
        "text_sha256": sha(secret_text),
    }
    assert door.by_seq(1)[1].stdout_sha256 == sha("episode-1")
    assert door.by_seq(2)[0].input["statement_sha256"] == sha(
        "the person prefers Friday launches"
    )
    assert door.by_seq(2)[1].stdout_sha256 == sha("proposal-1")
    assert pair(door.rows)[0].closed_by == "end"


async def test_scribe_bad_episode_is_a_retry_with_no_row(mount, scripted):
    worker, brief, handle, _, door, script = await scribe(
        mount,
        scripted,
        [
            [
                (
                    "write_episode",
                    {
                        "kind": "summary",
                        "text": "x",
                        "data_class": "PROJECT",
                        "provenance": [],
                    },
                )
            ],
            [("final", {"written": [], "proposed": [], "could_not": ["summary"]})],
        ],
    )
    events = await drive(worker, brief, handle)
    assert events[-1].terminal.outcome == "report"
    assert door.events() == ["terminal"]
    assert script.calls == 2


# A gateway that refuses. The SDK's default retries never retry a 403, so the
# server sees exactly one request per run.


class FakeGateway:
    def __init__(self, status: int, body: dict):
        self.status = status
        self.body = json.dumps(body).encode()
        self.requests: list[bytes] = []
        self.server = None

    async def _handle(self, reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        length = 0
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":")[1])
        body = await reader.readexactly(length) if length else b""
        self.requests.append(head + body)
        writer.write(
            f"HTTP/1.1 {self.status} Refused\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(self.body)}\r\n"
            "Connection: close\r\n\r\n".encode() + self.body
        )
        await writer.drain()
        writer.close()

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"


def refusal(message):
    return {"type": "error", "error": {"type": "permission_error", "message": message}}


async def through_gateway(mount, gateway):
    sandbox = FakeSandbox()
    door = FakeDoor()
    brief = make_brief(mount)
    handle = await sandbox.create(brief.sandbox_profile)
    worker = PydanticAIWorker(sandbox, door, gateway.url, PROMPTS)
    events = await drive(worker, brief, handle)
    return events, door, brief


async def test_budget_exhausted_ends_the_run_failed(mount):
    async with FakeGateway(403, refusal("budget exhausted")) as gateway:
        events, door, brief = await through_gateway(mount, gateway)
    assert [ev.kind for ev in events] == ["terminal"]
    terminal = events[-1].terminal
    assert terminal.outcome == "failed" and terminal.error == "budget_exhausted"
    assert door.events() == ["terminal"] and door.rows[-1].outcome == "failed"
    assert len(gateway.requests) == 1
    assert brief.gateway_token.get_secret_value().encode() in gateway.requests[0]


async def test_gateway_refusal_ends_the_run_failed_without_retry(mount):
    async with FakeGateway(403, refusal("brief revoked")) as gateway:
        events, door, brief = await through_gateway(mount, gateway)
    terminal = events[-1].terminal
    assert terminal.outcome == "failed"
    assert terminal.error == "gateway refused: brief revoked"
    assert len(gateway.requests) == 1
    async with FakeGateway(
        401,
        {"type": "error", "error": {"type": "authentication_error", "message": "no"}},
    ) as gateway:
        events, door, brief = await through_gateway(mount, gateway)
    assert events[-1].terminal.outcome == "failed"
    assert events[-1].terminal.error == "gateway returned 401"
    assert len(gateway.requests) == 1
    assert door.events() == ["terminal"]
