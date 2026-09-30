"""`verify_objective` end to end: the real loop, gateway, tree, door, and
container, on `code/clean-change` and `code/planted-bug` packed as snapshots
of an objective whose contract carries each fixture's criteria. Plan 11 task
11; seams §3.5, §3.6; architecture §5.

The Executor's side is the store's record, landed directly: a report on an
APPROVED node, released, with a snapshot the test packed. Everything from
there is the real path: checks in a fresh container, the blind slice, the
Verifier's Brief on the verifier seat through the gateway on 8788, its run
in a second fresh container, the landed terminal, the row and the event.

Skips without the container runtime or the Anthropic key.
"""

import asyncio
import hashlib
import uuid
from pathlib import Path

import psycopg
import pytest

import kernel.tree as tree
from adapters.apple_container import AppleContainer
from adapters.pydantic_ai import PydanticAIWorker
from gateway.core import Gateway
from infra.models import load as load_models
from infra.sandbox import mounts
from kernel import runs, verify
from kernel.api import Door
from kernel.events import append, read_for
from schemas.report import ArtifactRef, Report
from schemas.verifier_fixture import load_fixture
from tests.conftest import dsn, requires_container, requires_postgres
from tests.test_verify_checks import code_fixture_files, make_space, pack_snapshot
from tests.test_worker_live import GATEWAY_URL, gateway_process, provider_key
from tests.tree_fakes import (
    FakeProfileFor,
    approval,
    bind,
    contract,
    fake_root_capabilities,
    request,
    terminal,
    token,
)
from workers.verifier import CLASS_PROMPT

pytestmark = [requires_postgres, requires_container]

BUDGET = 5_000_000  # usd_micros on the objective; the Verifier takes at most 2
PLANTED_CRITERION = "line_total(99, 100, 5) == 9405"


def connect():
    return psycopg.AsyncConnection.connect(dsn("kernel_rw"))


class Profiles:
    """The real `profile_for` for the verify profile, a fake for the rest:
    the Executor's worktree is the store's record here, never a clone."""

    def __init__(self):
        self.fake = FakeProfileFor()

    async def __call__(self, name, space, key, **kw):
        if name == "verify":
            return await mounts.profile_for(name, space, key, **kw)
        return await self.fake(name, space, key, **kw)


class Created:
    """The real adapter with every container name `create` returned kept, so
    the closing assertion covers this test's containers and no other
    session's: `running()` lists every `cori-` container on the machine, and
    a suite that ran before this test may have left its own behind."""

    def __init__(self, inner: AppleContainer):
        self.inner = inner
        self.names: list[str] = []

    async def create(self, profile):
        handle = await self.inner.create(profile)
        self.names.append(handle.id)
        return handle

    def __getattr__(self, name):
        return getattr(self.inner, name)


@pytest.fixture
def sandbox_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "sandboxes"
    root.mkdir()
    monkeypatch.setenv("CORI_SANDBOX_ROOT", str(root))
    return root


@pytest.fixture
async def live(space, monkeypatch, sandbox_root, gateway_process):
    issuer = Gateway(provider_key=provider_key(), connect=connect)
    spaces = {space: make_space(space, roots=["/repo", str(sandbox_root)])}
    bind(monkeypatch, spaces, issuer=issuer, profile_for=Profiles())
    monkeypatch.setattr(verify, "load_all", lambda: spaces)
    monkeypatch.setattr(verify, "root_capabilities", fake_root_capabilities)
    sandbox = Created(AppleContainer(sandbox_root=sandbox_root))
    door = Door(connect)
    worker = PydanticAIWorker(sandbox, door, GATEWAY_URL, {"Verifier": CLASS_PROMPT})
    monkeypatch.setattr(runs, "sandbox", sandbox)
    monkeypatch.setattr(runs, "worker", worker)
    monkeypatch.setattr(runs, "door", door)
    monkeypatch.setattr(runs, "connect", connect)
    runs.live.clear()
    yield dict(issuer=issuer, sandbox=sandbox)
    await issuer.aclose()


async def landed_objective(space, slug, tmp_path):
    """A node at VERIFYING whose Executor report and snapshot are the
    fixture's files, with the fixture's criteria on the contract."""
    fixture = load_fixture("code", slug)
    files = code_fixture_files(slug)
    snapshot = pack_snapshot(tmp_path, files, name=slug)
    c = contract(budget=BUDGET).model_copy(
        update={"success_criteria": list(fixture.success_criteria)}
    )
    async with await connect() as conn:
        oid = await tree.open_objective(
            conn, space=space, conversation_id="c1", contract=c
        )
        await tree.approve(conn, oid, approval("approved", oid, 1))
        executor = await tree.delegate(
            conn,
            request(oid, "Executor", space=space, budget=1_000_000),
            issuer=frozenset(),
            parent_brief="turn-1",
        )
        report = Report(
            artifact_refs=[
                ArtifactRef(
                    kind="code",
                    path=f"/work/{name}",
                    sha256=hashlib.sha256(body).hexdigest(),
                )
                for name, body in files.items()
                if name.endswith(".py")
            ],
            evidence=[],
            assumption_deltas=[],
            summary="EXECUTORSUMMARY: all tests pass, the change is complete",
        )
        await tree.land_report(conn, token(executor), terminal(report=report))
        await tree.release(conn, executor.id)
        await append(
            conn,
            space_id=space,
            type="snapshot.taken",
            payload={
                "brief_id": executor.id,
                "objective_id": oid,
                "snapshot": snapshot.model_dump(mode="json"),
            },
        )
        await conn.commit()
    return oid, executor, snapshot


async def rows(sql, params):
    async with await connect() as conn:
        return await (await conn.execute(sql, params)).fetchall()


async def check_one(space, slug, tmp_path, expected):
    oid, executor, snapshot = await landed_objective(space, slug, tmp_path)
    verdict = await asyncio.wait_for(verify.verify_objective(oid, snapshot), 900)
    assert verdict is not None, slug
    assert verdict.outcome == expected, (slug, verdict)

    async with await connect() as conn:
        node = await tree.project(conn, oid)
        events = await read_for(conn, space_id=space, key="objective_id", value=oid)
    seat = load_models().model("verifier").id
    issued = [
        e
        for e in events
        if e.type == "brief.issued" and e.payload["brief"]["agent_class"] == "Verifier"
    ]
    assert len(issued) == 1
    verifier_id = issued[0].payload["brief"]["id"]
    assert issued[0].payload["brief"]["model_ref"] == seat

    # The verdict row and event name the Executor brief judged and the seat.
    (row,) = await rows(
        "SELECT brief_id, verifier_brief_id, model_ref, outcome, criteria FROM verdicts "
        "WHERE objective_id = %s",
        (oid,),
    )
    assert row[:4] == (executor.id, verifier_id, seat, expected)
    recorded = [e for e in events if e.type == "verdict.recorded"]
    assert len(recorded) == 1 and recorded[0].payload["brief_id"] == executor.id

    # The transition was written by land_report: it follows the Verifier's
    # report.landed in the same transaction, and nothing else wrote one.
    types = [(e.type, e.payload) for e in events]
    landed_at = next(
        i
        for i, (t, p) in enumerate(types)
        if t == "report.landed" and p["brief_id"] == verifier_id
    )
    t, p = types[landed_at + 1]
    assert t == "objective.state_changed" and p["from"] == "VERIFYING"
    assert events[landed_at + 1].occurred_at == events[landed_at].occurred_at
    changes = [e for e in events if e.type == "objective.state_changed"]
    assert [c.payload["to"] for c in changes][-1] == node.state

    # The checks came before any prose: no gateway row for the Verifier's
    # brief precedes the objective's checks.recorded event.
    (checks_event,) = [e for e in events if e.type == "checks.recorded"]
    gateway = await rows(
        "SELECT model, event, at FROM gateway_log WHERE brief_id = %s ORDER BY id",
        (verifier_id,),
    )
    assert gateway, "the Verifier made no gateway calls"
    assert all(g[0] == seat for g in gateway if g[0] is not None)
    assert all(g[2] > checks_event.occurred_at for g in gateway)
    assert any(g[1] == "request" for g in gateway)

    # The Verifier's tool log holds read and bash and nothing else.
    tools = await rows(
        "SELECT DISTINCT tool FROM tool_log WHERE brief_id = %s AND tool IS NOT NULL",
        (verifier_id,),
    )
    assert {t[0] for t in tools} <= {"read", "bash"}, tools
    (last,) = await rows(
        "SELECT event, outcome FROM tool_log WHERE brief_id = %s ORDER BY id DESC LIMIT 1",
        (verifier_id,),
    )
    assert last == ("terminal", "report")
    assert verifier_id not in runs.live
    return node, verdict, verifier_id


async def test_planted_bug_fails_and_clean_change_passes(live, space, tmp_path):
    node, verdict, _ = await check_one(space, "clean-change", tmp_path, "pass")
    assert (node.state, node.state_reason) == ("SUCCEEDED", "verified")
    assert all(c.met for c in verdict.criteria)

    node, verdict, _ = await check_one(space, "planted-bug", tmp_path, "fail")
    assert (node.state, node.state_reason) == ("FAILED", "verification_failed")
    by_criterion = {c.criterion: c for c in verdict.criteria}
    assert by_criterion[PLANTED_CRITERION].met is False, verdict

    # Two fresh verify containers per verification, the checks' and the
    # Verifier's; every one this test created is gone, and the extractions
    # with them.
    created = live["sandbox"].names
    assert len(created) == 4 and all(n.startswith("cori-verify-") for n in created)
    up = {h.id for h in await live["sandbox"].running()}
    assert not (set(created) & up), sorted(set(created) & up)
    verify_dir = live["sandbox"].sandbox_root / space / "verify"
    assert not verify_dir.exists() or not any(verify_dir.iterdir())
