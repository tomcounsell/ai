"""`render_verifier_slice`: seven blocks in order, blind, deterministic. Plan
11 task 6 and Properties; seams §1.5, §3.6, §3.11; architecture §5.

The store sits behind four module names (`_events_of`, `_tool_rows`,
`_ledger_rows`, `_check_rows`); the examples pin them to drawn data so a
property runs without a database, and one test writes real events and tool
log rows to show the readers are the ones the store serves.
"""

import asyncio
import hashlib
import random
import subprocess
import tarfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import kernel.tree as tree
from kernel import api, runs, verify
from kernel.events import append, read_for
from schemas.brief import ContextSlice
from schemas.events import Event
from schemas.ids import new_id
from schemas.records import EffectLedgerRecord, ToolLogRecord
from schemas.report import ArtifactRef, CheckResult, EvidenceRef, Report
from schemas.sandbox import SnapshotRef
from schemas.trace import Terminal
from tests.conftest import dsn, requires_postgres
from tests.test_verify_checks import make_contract, make_objective
from tests.test_verify_verdict import verifying, wire
from tests.tree_fakes import snapshot

SPACE = "space-render"
BRIEF = "brief-executor-1"
SETTINGS = dict(
    deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)


def connect():
    return psycopg.AsyncConnection.connect(dsn("kernel_rw"))


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def prompts(tmp_path, monkeypatch):
    """Task 7 writes the real prompt files; here two short ones stand in."""
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "seat.md").write_text("This seat judges an artifact against a contract.\n")
    for kind in ("code", "document", "message"):
        (d / f"{kind}.md").write_text(f"Checklist for {kind}.\n")
    monkeypatch.setattr(verify, "PROMPTS_DIR", d)
    return d


def event(id_: int, type_: str, payload: dict) -> Event:
    return Event(
        id=id_,
        space_id=SPACE,
        type=type_,
        schema_version=1,
        occurred_at=datetime(2026, 9, 22, tzinfo=UTC),
        payload=payload,
    )


def landed(report: Report, *, brief_id=BRIEF, id_=10) -> Event:
    return event(
        id_,
        "report.landed",
        {
            "brief_id": brief_id,
            "objective_id": "o",
            "report": report.model_dump(mode="json"),
        },
    )


def issued(brief_id: str, agent_class: str, generation: int, id_: int) -> Event:
    return event(
        id_,
        "brief.issued",
        {
            "brief": {
                "id": brief_id,
                "agent_class": agent_class,
                "generation": generation,
            },
            "gateway_token_sha256": "0" * 64,
            "objective_id": "o",
        },
    )


def pin(monkeypatch, *, events=(), tool_rows=None, ledger=(), check_rows=()):
    tool_rows = tool_rows or {}

    async def _events_of(objective):
        return list(events)

    async def _tool_rows(brief_id, generation):
        return list(tool_rows.get((brief_id, generation), []))

    async def _ledger_rows(objective):
        return list(ledger)

    async def _check_rows(objective, brief_id):
        return [dict(r) for r in check_rows]

    monkeypatch.setattr(verify, "_events_of", _events_of)
    monkeypatch.setattr(verify, "_tool_rows", _tool_rows)
    monkeypatch.setattr(verify, "_ledger_rows", _ledger_rows)
    monkeypatch.setattr(verify, "_check_rows", _check_rows)


def objective(kind="code", criteria=("pytest green",), **fields):
    return make_objective(
        SPACE,
        contract=make_contract(artifact_kind=kind, criteria=criteria),
        brief_id=BRIEF,
        **fields,
    )


def row_of(check: CheckResult, id_: int = 1, resolutions=None) -> dict:
    return dict(
        id=id_,
        name=check.name,
        passed=check.passed,
        output_sha256=check.output_sha256,
        detail=check.detail,
        resolutions=resolutions,
    )


# --- examples ----------------------------------------------------------------


def test_block_order_and_kinds(monkeypatch):
    checks = [verify.check_result("tests", True, "4 passed")]
    pin(
        monkeypatch,
        events=[
            event(1, "objective.approved", {"objective_id": "o", "revision": 1}),
            event(9, "checks.recorded", {"objective_id": "o", "brief_id": BRIEF}),
        ],
        check_rows=[row_of(checks[0])],
    )
    slice_ = run(verify.render_verifier_slice(objective(), checks))
    assert [b.kind for b in slice_.blocks] == list(verify.BLOCK_KINDS)
    assert all(b.data_class == "PROJECT" for b in slice_.blocks)
    assert slice_.sha256 == ContextSlice.digest(slice_.blocks)
    by_kind = {b.kind: b for b in slice_.blocks}
    assert by_kind["voice"].text.startswith("# Voice and conduct")
    assert "Checklist for code." in by_kind["instruction"].text
    assert "1. pytest green" in by_kind["criteria"].text
    assert by_kind["criteria"].sources == ["1"]
    assert "tests: passed" in by_kind["checks"].text
    assert by_kind["checks"].sources == ["9"]
    assert by_kind["tool_log"].text == verify.NONE
    assert by_kind["effect_ledger"].text == verify.NONE
    assert slice_.space == SPACE


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_code_artifacts_carry_the_diff(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "user.email", "p@example.com", cwd=repo)
    git("config", "user.name", "P", cwd=repo)
    (repo / "a.txt").write_text("MAINONLY line\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "first commit on main MAINMSG", cwd=repo)
    git("checkout", "-q", "-b", "cori/x", cwd=repo)
    (repo / "b.txt").write_text("BRANCHCHANGE line\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "the change", cwd=repo)

    archive = tmp_path / "snap.tar.gz"
    with tarfile.open(archive, "w:gz", dereference=False) as tar:
        for path in sorted(repo.rglob("*")):
            tar.add(path, arcname=str(path.relative_to(repo)), recursive=False)
    snapshot = SnapshotRef(
        id=new_id(),
        handle_id="h",
        path=str(archive),
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        files={},
        taken_at=datetime.now(UTC),
    )
    report = Report(
        artifact_refs=[ArtifactRef(kind="code", path="/work/b.txt", sha256="ab" * 32)],
        evidence=[],
        assumption_deltas=[],
        summary="SUMMARYSENTINEL",
    )
    pin(
        monkeypatch,
        events=[
            landed(report),
            event(
                11,
                "snapshot.taken",
                {
                    "brief_id": BRIEF,
                    "objective_id": "o",
                    "snapshot": snapshot.model_dump(mode="json"),
                },
            ),
        ],
    )
    slice_ = run(verify.render_verifier_slice(objective(), []))
    block = next(b for b in slice_.blocks if b.kind == "artifacts")
    assert "+BRANCHCHANGE line" in block.text
    assert "b.txt | 1 +" in block.text
    assert "MAINONLY" not in block.text and "MAINMSG" not in block.text
    assert "SUMMARYSENTINEL" not in block.text
    assert "/work/b.txt sha256=" + "ab" * 32 in block.text
    assert block.sources == ["10", snapshot.id]
    again = run(verify.render_verifier_slice(objective(), []))
    assert again.sha256 == slice_.sha256


@requires_postgres
def test_every_executor_brief_is_rendered(monkeypatch):
    space = f"space-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(runs, "connect", connect)
    first, second = f"brief-{uuid.uuid4().hex[:6]}", f"brief-{uuid.uuid4().hex[:6]}"
    obj = make_objective(space, contract=make_contract(), brief_id=second)

    async def seed():
        async with await connect() as conn:
            for brief_id, generation in ((first, 1), (second, 2)):
                await append(
                    conn,
                    space_id=space,
                    type="brief.issued",
                    payload={
                        "brief": {
                            "id": brief_id,
                            "agent_class": "Executor",
                            "generation": generation,
                        },
                        "gateway_token_sha256": "0" * 64,
                        "objective_id": obj.id,
                    },
                )
                await api._insert(
                    conn,
                    ToolLogRecord(
                        brief_id=brief_id,
                        generation=generation,
                        space_id=space,
                        seq=1,
                        event="tool.start",
                        tool="bash",
                        input={"command": f"echo {brief_id}"},
                    ),
                )
            await append(
                conn,
                space_id=space,
                type="brief.issued",
                payload={
                    "brief": {"id": "v1", "agent_class": "Verifier", "generation": 2},
                    "gateway_token_sha256": "0" * 64,
                    "objective_id": obj.id,
                },
            )
            await conn.commit()

    run(seed())
    slice_ = run(verify.render_verifier_slice(obj, []))
    block = next(b for b in slice_.blocks if b.kind == "tool_log")
    assert block.text.index(f"brief {first} generation 1") < block.text.index(
        f"brief {second} generation 2"
    )
    assert f"echo {first}" in block.text and f"echo {second}" in block.text
    assert "v1" not in block.text
    assert block.sources == [f"{first}#1", f"{second}#1"]


# --- strategies --------------------------------------------------------------

text = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")), max_size=40
)
sha = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)


@st.composite
def objectives(draw):
    kind = draw(st.sampled_from(["code", "document", "message"]))
    criteria = draw(
        st.lists(text.filter(str.strip), min_size=1, max_size=6, unique=True)
    )
    premise = draw(text)
    sentinel = uuid.uuid4().hex
    evidence = [
        EvidenceRef(kind=k, ref=f"{sentinel}-{i}", sha256=None)
        for i, k in enumerate(
            draw(
                st.lists(
                    st.sampled_from(["test_output", "diff", "tool_log_seq", "excerpt"]),
                    max_size=3,
                )
            )
        )
    ]
    report = Report(
        artifact_refs=[],
        evidence=evidence,
        assumption_deltas=[],
        summary=f"{sentinel} {draw(text)}"[:2000],
    )
    obj = make_objective(
        SPACE,
        contract=make_contract(
            artifact_kind=kind, criteria=criteria, premise=premise or "p"
        ),
        brief_id=BRIEF,
    )
    return obj, report, sentinel


@st.composite
def tool_rows(draw, brief_id=BRIEF, generation=1):
    n = draw(st.integers(min_value=0, max_value=6))
    with_terminal = draw(st.booleans())
    rows = []
    for seq in range(1, n + 1):
        tool = draw(st.sampled_from(["bash", "read", "write", "edit", "ask"]))
        rows.append(
            ToolLogRecord(
                brief_id=brief_id,
                generation=generation,
                space_id=SPACE,
                seq=seq,
                event="tool.start",
                tool=tool,
                input=(
                    {"command": f"cmd {seq}"}
                    if tool == "bash"
                    else {"path": f"/work/{seq}"}
                ),
            )
        )
        if draw(st.booleans()):
            rows.append(
                ToolLogRecord(
                    brief_id=brief_id,
                    generation=generation,
                    space_id=SPACE,
                    seq=seq,
                    event="tool.end",
                    tool=tool,
                    exit_status=draw(st.integers(min_value=0, max_value=2)),
                    duration_ms=draw(st.integers(min_value=0, max_value=5000)),
                    artifact=f"/work/{seq}" if tool in ("write", "edit") else None,
                    artifact_sha256=draw(sha) if tool in ("write", "edit") else None,
                )
            )
    if with_terminal:
        rows.append(
            ToolLogRecord(
                brief_id=brief_id,
                generation=generation,
                space_id=SPACE,
                event="terminal",
                outcome="report",
            )
        )
    return rows


@st.composite
def invocation_lists(draw):
    briefs = draw(
        st.sampled_from([[(BRIEF, 1)], [(BRIEF, 1), ("brief-executor-2", 2)]])
    )
    rows = {}
    for brief_id, generation in briefs:
        rows[(brief_id, generation)] = draw(tool_rows(brief_id, generation))
    return briefs, rows


@st.composite
def ledger_rows(draw):
    n = draw(st.integers(min_value=0, max_value=5))
    return [
        EffectLedgerRecord(
            id=i + 1,
            effect_id=f"e{i}",
            space_id=SPACE,
            objective_id="o",
            brief_id=BRIEF,
            generation=1,
            action_type=draw(st.sampled_from(["push_branch", "connector_read"])),
            effect_class=draw(st.sampled_from(["read", "propose"])),
            idempotency_key=f"k{i}",
            target="github.com/yudame",
            payload_sha256="0" * 64,
            payload={},
            event=draw(st.sampled_from(["intent", "outcome"])),
            outcome_kind=draw(st.sampled_from([None, "done"])),
        )
        for i in range(n)
    ]


@st.composite
def check_lists(draw):
    names = draw(
        st.lists(
            st.sampled_from(
                ["tests", "build", "citations_resolve", "length", "recipient_allowed"]
            ),
            max_size=8,
        )
    )
    return [
        verify.check_result(name, draw(st.booleans()), draw(text)) for name in names
    ]


def events_for(briefs, report, *, checks_id=20, verifier_id=30):
    evs = [event(1, "objective.approved", {"objective_id": "o", "revision": 1})]
    for i, (brief_id, generation) in enumerate(briefs):
        evs.append(issued(brief_id, "Executor", generation, 2 + i))
    evs.append(landed(report))
    evs.append(
        event(checks_id, "checks.recorded", {"objective_id": "o", "brief_id": BRIEF})
    )
    evs.append(issued("brief-verifier", "Verifier", briefs[-1][1], verifier_id))
    return evs


# --- properties --------------------------------------------------------------


@given(objectives(), invocation_lists(), ledger_rows(), check_lists())
@settings(max_examples=60, **SETTINGS)
def test_slice_is_blind(monkeypatch, drawn, invocations, ledger, checks):
    obj, report, sentinel = drawn
    briefs, rows = invocations
    with pytest.MonkeyPatch.context() as mp:
        pin(
            mp,
            events=events_for(briefs, report),
            tool_rows=rows,
            ledger=ledger,
            check_rows=[row_of(c, i + 1) for i, c in enumerate(checks)],
        )
        slice_ = run(verify.render_verifier_slice(obj, checks))
    rendered = "\n".join(b.text for b in slice_.blocks)
    assert sentinel not in rendered
    assert all(b.data_class == "PROJECT" for b in slice_.blocks)
    assert [b.kind for b in slice_.blocks] == list(verify.BLOCK_KINDS)


@given(objectives(), invocation_lists(), ledger_rows(), check_lists(), st.randoms())
@settings(max_examples=40, **SETTINGS)
def test_render_is_deterministic(monkeypatch, drawn, invocations, ledger, checks, rnd):
    obj, report, _ = drawn
    briefs, rows = invocations
    check_rows = [row_of(c, i + 1) for i, c in enumerate(checks)]
    with pytest.MonkeyPatch.context() as mp:
        pin(
            mp,
            events=events_for(briefs, report),
            tool_rows=rows,
            ledger=ledger,
            check_rows=check_rows,
        )
        first = run(verify.render_verifier_slice(obj, checks))
        second = run(verify.render_verifier_slice(obj, checks))
    shuffled_rows = {k: rnd.sample(v, len(v)) for k, v in rows.items()}
    shuffled_ledger = rnd.sample(ledger, len(ledger))
    with pytest.MonkeyPatch.context() as mp:
        pin(
            mp,
            events=events_for(briefs, report),
            tool_rows=shuffled_rows,
            ledger=shuffled_ledger,
            check_rows=check_rows,
        )
        third = run(verify.render_verifier_slice(obj, checks))
    assert first.sha256 == second.sha256 == third.sha256


@given(tool_rows(), st.randoms())
@settings(max_examples=60, **SETTINGS)
def test_invocations_render_the_same_in_any_order(rows, rnd):
    lines, sources = verify.render_tool_log(rows)
    for _ in range(3):
        shuffled = rnd.sample(rows, len(rows))
        assert verify.render_tool_log(shuffled) == (lines, sources)
    invs = api.invocations(rows)
    assert len(sources) == len(invs) == len({i.start.seq for i in invs})
    for inv in invs:
        matching = [
            l for l in lines if l.startswith(f"seq {inv.start.seq} {inv.start.tool}:")
        ]
        assert len(matching) == 1
        assert ("closed by terminal" in matching[0]) == (inv.closed_by == "terminal")


@st.composite
def passing_check_lists(draw):
    """Check lists every one of which passed, so `verify_objective` goes on
    to render the slice and delegate the Verifier (a failed check is the
    kernel verdict's property, in tests/test_verify_verdict.py)."""
    names = draw(
        st.lists(
            st.sampled_from(
                ["tests", "build", "citations_resolve", "length", "recipient_allowed"]
            ),
            max_size=8,
        )
    )
    return [verify.check_result(name, True, draw(text)) for name in names]


def flipped(checks):
    """The same list with the last result's `passed` inverted."""
    last = checks[-1]
    return checks[:-1] + [
        CheckResult(
            name=last.name,
            passed=not last.passed,
            output_sha256=last.output_sha256,
            detail=last.detail,
        )
    ]


@requires_postgres
@given(passing_check_lists(), st.integers(min_value=1, max_value=6))
@settings(max_examples=40, **SETTINGS)
def test_checks_block_matches_recorded_rows(checks, n_criteria):
    """Checks before prose, over the real store: `verify_objective` with
    `run_checks` recording the drawn list and `run_brief` a stub that keeps
    the Verifier's Brief. The `checks.recorded` event is durable before the
    Verifier's `brief.issued`; the slice in that Brief names every recorded
    row; rendering before the rows exist, with a list that differs from the
    rows, or with fewer than the rows, is refused."""
    space = f"space-{uuid.uuid4().hex[:8]}"
    criteria = [f"criterion {i}" for i in range(n_criteria)]
    briefs = []

    async def fake_run_checks(objective, snap):
        async with await connect() as conn:
            await verify._record_checks(
                conn, objective, objective.reports[-1].brief_id, checks, {}
            )
            await conn.commit()
        return checks

    async def stub_run_brief(brief):
        briefs.append(brief)
        return Terminal(outcome="failed", report=None, error="stub: no loop here")

    async def go():
        async with await connect() as conn:
            oid, _ = await verifying(conn, space, criteria)
            before = await tree.project(conn, oid)
        # Nothing recorded yet: a non-empty list is refused, the empty one
        # renders (the rows read back are the empty list too).
        with pytest.raises(verify.ChecksMismatch):
            await verify.render_verifier_slice(
                before, checks or [verify.check_result("tests", True, "x")]
            )
        assert (await verify.render_verifier_slice(before, [])).blocks
        result = await verify.verify_objective(oid, snapshot())
        async with await connect() as conn:
            after = await tree.project(conn, oid)
            events = await read_for(conn, space_id=space, key="objective_id", value=oid)
        again = await verify.render_verifier_slice(after, checks)
        refused = []
        for wrong in ([flipped(checks), checks[:-1]] if checks else [[]]):
            if wrong == checks:
                continue
            try:
                await verify.render_verifier_slice(after, wrong)
            except verify.ChecksMismatch:
                refused.append(True)
            else:
                refused.append(False)
        return result, events, again, refused

    with pytest.MonkeyPatch.context() as mp:
        wire(mp, space)
        mp.setattr(verify, "run_checks", fake_run_checks)
        mp.setattr(runs, "run_brief", stub_run_brief)
        result, events, again, refused = run(go())
    assert result is None and all(refused)
    (brief,) = briefs
    (recorded,) = [e for e in events if e.type == "checks.recorded"]
    (issued,) = [
        e
        for e in events
        if e.type == "brief.issued" and e.payload["brief"]["agent_class"] == "Verifier"
    ]
    assert issued.payload["brief"]["id"] == brief.id
    assert recorded.id < issued.id
    block = next(b for b in brief.context_slice.blocks if b.kind == "checks")
    assert block.sources == [str(recorded.id)]
    for c in checks:
        assert f"{c.name}: passed" in block.text
    assert block.text.count(": passed") == len(checks)
    assert again.sha256 == brief.context_slice.sha256
