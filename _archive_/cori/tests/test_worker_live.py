"""Spike 08's two scenarios on the real loop. Plan 05 task 10.

A real `cori-base:3.14` container through `adapters.apple_container`, the
real gateway started as its own process on 127.0.0.1:8788, the real tree,
events, door, and `kernel.runs`, with `kernel.verify` a recorder until the
verifier plan lands. Scenario A: a task that cannot finish without asking;
the gateway stays silent between the question row and the answer row, and
every artifact hash in the tool log is the file on the retained mount.
Scenario B: the same task stopped during the wait; the Brief's last row is
a terminal with outcome aborted.

Skips without the container runtime or the Anthropic key. The gateway
process reads the same `CORI_PG*` environment this test runs under.
"""

import asyncio
import hashlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psycopg
import pytest

import kernel.tree as tree
from adapters.apple_container import AppleContainer
from adapters.pydantic_ai import PydanticAIWorker
from gateway.core import Gateway
from kernel import runs
from kernel.api import Door
from kernel.events import append, read_for
from infra.secrets import MissingSecret, read_secret
from schemas.sandbox import SandboxProfile
from tests.conftest import dsn, requires_container, requires_postgres
from tests.evals.test_class_prompts import EXECUTOR_ASK, PROMPTS
from tests.test_runs import FakeVerify
from tests.tree_fakes import approval, bind, contract, make_space, request

pytestmark = [requires_postgres, requires_container]

ROOT = Path(__file__).resolve().parents[1]
HOST, PORT = "127.0.0.1", 8788
GATEWAY_URL = f"http://{HOST}:{PORT}"
BUDGET = 900_000  # usd_micros; one Executor run on the frontier seat
NAMES = ("read", "write", "bash", "ask")


def connect():
    return psycopg.AsyncConnection.connect(dsn("kernel_rw"))


def provider_key() -> str:
    try:
        return read_secret("anthropic_api_key")
    except MissingSecret:
        pytest.skip("no Anthropic key in the Keychain")


def port_free() -> bool:
    with socket.socket() as s:
        return s.connect_ex((HOST, PORT)) != 0


@pytest.fixture(scope="module")
def gateway_process():
    """`python -m gateway` on 8788, started here and gone after."""
    provider_key()
    assert port_free(), f"{HOST}:{PORT} is in use before the test"
    proc = subprocess.Popen(
        [sys.executable, "-m", "gateway"],
        cwd=ROOT,
        env=dict(os.environ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                httpx.post(GATEWAY_URL + "/v1/messages", json={}, timeout=2)
                break
            except httpx.HTTPError:
                if proc.poll() is not None:
                    raise RuntimeError("the gateway process exited during start")
                time.sleep(0.2)
        else:
            raise RuntimeError("the gateway never answered on 8788")
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(15)
        assert proc.poll() is not None
        deadline = time.monotonic() + 10
        while not port_free() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert port_free(), f"{HOST}:{PORT} still in use after the gateway stopped"


class WorktreeProfiles:
    """The tree's `profile_for`, with the mount under the adapter's sandbox
    root as `infra.sandbox.mounts` would place it."""

    def __init__(self, root: Path):
        self.root = root

    async def __call__(self, name, space, key, *, artifact_kind, **_):
        mount = self.root / space.id / "worktrees" / key
        mount.mkdir(parents=True, exist_ok=True)
        return SandboxProfile(
            name=name,
            space=space.id,
            mount_source=str(mount),
            readonly=name != "worktree",
            network="hostonly",
            key=key,
            env={},
        )


@pytest.fixture
async def live(space, monkeypatch, tmp_path, gateway_process):
    """The composition root for one scenario: real sandbox, worker, door,
    and the gateway's token issuer; the tree's other seams faked."""
    issuer = Gateway(provider_key=provider_key(), connect=connect)
    bind(
        monkeypatch,
        {space: make_space(space, max_effect_class="act")},
        profile_for=WorktreeProfiles(tmp_path),
    )
    sandbox = AppleContainer(sandbox_root=tmp_path)
    handles = []
    real_create = sandbox.create

    async def create(profile):
        h = await real_create(profile)
        handles.append(h)
        return h

    sandbox.create = create
    door = Door(connect)
    worker = PydanticAIWorker(sandbox, door, GATEWAY_URL, PROMPTS)
    monkeypatch.setitem(sys.modules, "kernel.verify", FakeVerify([]))
    monkeypatch.setattr(runs, "sandbox", sandbox)
    monkeypatch.setattr(runs, "worker", worker)
    monkeypatch.setattr(runs, "door", door)
    monkeypatch.setattr(runs, "connect", connect)
    runs.live.clear()

    async def executor(conn):
        oid = await tree.open_objective(
            conn, space=space, conversation_id="c1", contract=contract()
        )
        await tree.approve(conn, oid, approval("approved", oid, 1))
        brief = await tree.delegate(
            conn,
            request(
                oid,
                "Executor",
                space=space,
                names=NAMES,
                budget=BUDGET,
                instruction=EXECUTOR_ASK,
            ),
            issuer=frozenset(),
            parent_brief="turn-1",
            token_issuer=issuer,
        )
        await conn.commit()
        return oid, brief

    yield dict(issuer=issuer, sandbox=sandbox, handles=handles, executor=executor)
    for h in handles:
        await sandbox.destroy(h)
    await issuer.aclose()


async def question_row(brief_id):
    """The first question row for the Brief, or None."""
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT question_id, text, seq, at FROM tool_log "
            "WHERE brief_id = %s AND event = 'question' ORDER BY id LIMIT 1",
            (brief_id,),
        )
        return await cur.fetchone()


async def wait_for_question(brief_id, timeout=240):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = await question_row(brief_id)
        if row is not None:
            return row
        await asyncio.sleep(0.5)
    raise TimeoutError("the worker never asked")


async def rows_of(brief_id):
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT event, tool, seq, artifact, artifact_sha256, outcome, at "
            "FROM tool_log WHERE brief_id = %s ORDER BY id",
            (brief_id,),
        )
        return await cur.fetchall()


async def test_scenario_a_asks_answers_and_reports(live, space):
    async with await connect() as conn:
        oid, brief = await live["executor"](conn)
    task = asyncio.create_task(runs.run_brief(brief))
    question_id, text, seq, asked_at = await wait_for_question(brief.id)
    assert "name" in text.lower()
    raised = await read_for_type(space, brief.id, "question.raised")
    assert len(raised) == 1 and raised[0].payload["question_id"] == question_id

    # The worker is blocked; the gateway must stay silent.
    await asyncio.sleep(3.0)
    async with await connect() as conn:
        await append(
            conn,
            space_id=space,
            type="question.answered",
            payload={
                "brief_id": brief.id,
                "objective_id": oid,
                "question_id": question_id,
                "text": "Tom",
                "answered_by": "supervisor",
            },
        )
        await conn.commit()
    await runs.answer(brief.id, question_id, "Tom")

    terminal = await asyncio.wait_for(task, 600)
    assert terminal.outcome == "report", terminal
    assert terminal.report.artifact_refs, "no artifacts reported"

    rows = await rows_of(brief.id)
    events = [r[0] for r in rows]
    assert events[-1] == "terminal" and rows[-1][5] == "report"
    answer = next(r for r in rows if r[0] == "answer")
    assert answer[2] == seq

    # Zero gateway requests between the question row and the answer row.
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM gateway_log WHERE brief_id = %s "
            "AND event = 'request' AND at BETWEEN %s AND %s",
            (brief.id, asked_at, answer[6]),
        )
        (between,) = await cur.fetchone()
        cur = await conn.execute(
            "SELECT count(*) FROM gateway_log WHERE brief_id = %s "
            "AND event = 'request'",
            (brief.id,),
        )
        (total,) = await cur.fetchone()
    assert between == 0
    assert total >= 2

    # Every artifact hash in the log is the file on the retained mount.
    mount = Path(brief.sandbox_profile.mount_source)
    hashed = [(r[3], r[4]) for r in rows if r[0] == "tool.end" and r[3]]
    assert hashed
    for path, digest in hashed:
        host = mount / path[len("/work/") :]
        assert hashlib.sha256(host.read_bytes()).hexdigest() == digest, path
    for ref in terminal.report.artifact_refs:
        host = mount / ref.path[len("/work/") :]
        assert hashlib.sha256(host.read_bytes()).hexdigest() == ref.sha256
    assert (mount / "greeting.txt").read_text().strip() == "Hello, Tom!"

    # The closing sequence ran: the report landed and the node is verifying.
    async with await connect() as conn:
        assert (await tree.project(conn, oid)).state == "VERIFYING"
    taken = await read_for_type(space, brief.id, "snapshot.taken")
    assert len(taken) == 1


async def test_scenario_b_stop_during_the_wait_ends_aborted(live, space):
    async with await connect() as conn:
        oid, brief = await live["executor"](conn)
    task = asyncio.create_task(runs.run_brief(brief))
    await wait_for_question(brief.id)

    async with await connect() as conn:
        stopped = await tree.stop(
            conn, oid, "stopped_by_person", token_issuer=live["issuer"]
        )
        await conn.commit()
    assert stopped == [brief.id]
    receipts = await runs.stop(stopped)
    assert len(receipts) == 1
    terminal = await asyncio.wait_for(task, 60)
    assert terminal.outcome == "aborted"
    await asyncio.sleep(2.0)  # anything that leaks would show up here

    rows = await rows_of(brief.id)
    assert rows[-1][0] == "terminal" and rows[-1][5] == "aborted"
    assert [r[0] for r in rows].count("terminal") == 1
    confirmed = await read_for_type(space, brief.id, "brief.stop_confirmed")
    assert len(confirmed) == 1
    assert runs.live == {}
    # The disk stays.
    assert Path(brief.sandbox_profile.mount_source).is_dir()


async def read_for_type(space, brief_id, type_):
    async with await connect() as conn:
        events = await read_for(conn, space_id=space, key="brief_id", value=brief_id)
    return [e for e in events if e.type == type_]
