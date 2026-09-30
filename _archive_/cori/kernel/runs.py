"""Live runs: the kernel side of one Brief's life from sandbox to terminal.
Plan 05; seams §3.5, rulings 2, 3, 4, and Round two; tech stack §4, §5.

`run_brief` creates the sandbox, registers the Brief with the door, drives
the worker, and on the terminal does the closing sequence in one order:
artifact hashes checked against the durable tool log, a Verdict validated,
`tree.land_report`, `tree.release`, `sandbox.stop`, `tree.confirm_stop`,
then a snapshot for a worktree that reported or `destroy` for the other two
profiles, then `verify.verify_objective` for an Executor report. `stop` is
the one owner of abort, sandbox stop, confirm, the kernel-side terminal row,
and release, called with the ids `tree.stop` returned after its transaction.

The collaborators are late-bound module names so the composition root sets
them once and a test replaces them: `sandbox`, `worker`, `door`, `connect`.
`kernel.verify` is resolved inside the two calls that need it, because
`verify_objective` delegates the Verifier and calls `run_brief` back.
"""

from __future__ import annotations

import asyncio
import importlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg

from kernel import api, tree
from kernel.events import append
from ports.sandbox import SandboxProvider
from ports.worker import Worker
from schemas.brief import Brief, BriefToken
from schemas.ids import BriefId, QuestionId, new_id
from schemas.report import Report
from schemas.sandbox import SandboxHandle, SnapshotRef, StopReceipt
from schemas.trace import Terminal

HASH_DISAGREES = "artifact hash disagrees with the tool log"


def default_connect():
    """A `kernel_rw` connection to the local database, the way
    `tests/conftest.py` builds one."""
    host = os.environ.get("CORI_PGHOST", "localhost")
    port = os.environ.get("CORI_PGPORT", "5432")
    database = os.environ.get("CORI_PGDATABASE", "cori")
    return psycopg.AsyncConnection.connect(
        f"postgresql://kernel_rw@{host}:{port}/{database}"
    )


# -- late-bound collaborators, set by the composition root -------------------

sandbox: SandboxProvider | None = None
worker: Worker | None = None
door: api.Door | None = None
connect = default_connect


def configure(
    *,
    sandbox: SandboxProvider,
    worker: Worker,
    door: api.Door,
    connect=None,
) -> None:
    g = globals()
    g["sandbox"] = sandbox
    g["worker"] = worker
    g["door"] = door
    if connect is not None:
        g["connect"] = connect


def _bound():
    if sandbox is None or worker is None or door is None:
        raise RuntimeError("kernel.runs is not configured: sandbox, worker, door")
    return sandbox, worker, door


def _verify():
    return importlib.import_module("kernel.verify")


@dataclass
class LiveRun:
    brief: Brief
    handle: SandboxHandle
    task: asyncio.Task
    created_at: datetime


live: dict[BriefId, LiveRun] = {}


# -- run_brief ---------------------------------------------------------------


async def run_brief(brief: Brief) -> Terminal:
    """Drive one Brief to its terminal and do everything that follows
    (seams §3.5). An aborted terminal, or a stale generation at the end,
    returns the terminal and touches nothing: that run was stopped from
    outside and `stop` owns what follows."""
    sandbox_, worker_, door_ = _bound()
    token = BriefToken(brief_id=brief.id, generation=brief.generation)
    handle = await sandbox_.create(brief.sandbox_profile)
    door_.register(brief)
    task = asyncio.current_task()
    assert task is not None
    live[brief.id] = LiveRun(
        brief=brief, handle=handle, task=task, created_at=datetime.now(UTC)
    )
    try:
        terminal: Terminal | None = None
        async for event in worker_.run(brief, handle):
            if event.kind == "question" and event.question is not None:
                await _question_raised(brief, event.question)
            elif event.kind == "terminal":
                terminal = event.terminal
        if terminal is None:
            terminal = Terminal(
                outcome="failed", report=None, error="worker ended with no terminal"
            )
        if terminal.outcome == "aborted":
            return terminal
        async with await connect() as conn:
            try:
                await tree.check_generation(conn, token)
            except tree.StaleGeneration:
                return terminal
            terminal = await _checked(conn, brief, terminal)
            if brief.report_schema == "Verdict" and terminal.outcome == "report":
                terminal = await _verify().validate_verdict_terminal(brief, terminal)
            await tree.land_report(conn, token, terminal)
            await tree.release(conn, brief.id)
            await conn.commit()
        receipt = await sandbox_.stop(handle)
        await _confirm_landed(brief, receipt)
        snapshot: SnapshotRef | None = None
        profile = brief.sandbox_profile.name
        if profile == "worktree" and terminal.outcome == "report":
            snapshot = await sandbox_.snapshot(handle, snapshot_id=new_id())
            async with await connect() as conn:
                await append(
                    conn,
                    space_id=brief.space,
                    type="snapshot.taken",
                    payload={
                        "brief_id": brief.id,
                        "objective_id": brief.objective_id,
                        "snapshot": snapshot.model_dump(mode="json"),
                    },
                )
                await conn.commit()
        elif profile in ("scratch", "verify"):
            await sandbox_.destroy(handle)
        if brief.agent_class == "Executor" and terminal.outcome == "report":
            await _verify().verify_objective(brief.objective_id, snapshot)
        return terminal
    finally:
        door_.forget(brief.id)
        live.pop(brief.id, None)


async def _confirm_landed(brief: Brief, receipt: StopReceipt) -> None:
    """Seams §3.5 confirm the stop of a landed run too. The tree as built
    admits `confirm_stop` only after `brief.stopped`, which a landed run
    never has, so its refusal is the tree saying the run was not stopped
    from outside; the receipt then has no durable home (plan 05 finding)."""
    async with await connect() as conn:
        try:
            await tree.confirm_stop(conn, brief.id, receipt)
        except tree.NotLive:
            await conn.rollback()
            return
        await conn.commit()


async def _question_raised(brief: Brief, question) -> None:
    async with await connect() as conn:
        await append(
            conn,
            space_id=brief.space,
            type="question.raised",
            payload={
                "brief_id": brief.id,
                "objective_id": brief.objective_id,
                "question_id": question.question_id,
                "question": question.text,
                "tool_seq": question.tool_seq,
            },
        )
        await conn.commit()


async def _checked(conn, brief: Brief, terminal: Terminal) -> Terminal:
    """Every `ArtifactRef.sha256` equals the last host-side hash the tool
    log holds for that path, else the terminal becomes a failure (seams
    §1.6). The Report's own rows are durable, so the check reads the table
    and never the adapter."""
    if terminal.outcome != "report" or not isinstance(terminal.report, Report):
        return terminal
    rows = await api.read_tool_log(conn, brief.id, brief.generation)
    hashes = api.latest_artifact_hashes(rows)
    for ref in terminal.report.artifact_refs:
        if hashes.get(ref.path) != ref.sha256:
            return Terminal(outcome="failed", report=None, error=HASH_DISAGREES)
    return terminal


# -- answer ------------------------------------------------------------------


async def answer(brief_id: BriefId, question_id: QuestionId, text: str) -> None:
    """The supervisor appends `question.answered` first (§3.3); the adapter
    writes the answer row."""
    _, worker_, _ = _bound()
    await worker_.answer(brief_id, question_id, text)


# -- stop --------------------------------------------------------------------


async def stop(brief_ids: list[BriefId]) -> list[StopReceipt]:
    """For each Brief `tree.stop` fenced: abort the worker, wait for its run
    to return, stop the sandbox (probe-confirmed), confirm, write the
    terminal row for the old generation kernel-side, release. The disk
    stays. A Brief this process is not running is skipped: recovery of a
    run from before a restart is M1."""
    sandbox_, worker_, _ = _bound()
    receipts: list[StopReceipt] = []
    for brief_id in brief_ids:
        run = live.get(brief_id)
        if run is None:
            continue
        await worker_.abort(brief_id)
        if run.task is not asyncio.current_task():
            await asyncio.wait([run.task])
        receipt = await sandbox_.stop(run.handle)
        async with await connect() as conn:
            await tree.confirm_stop(conn, brief_id, receipt)
            await api.insert_terminal(
                conn,
                brief_id,
                run.brief.generation,
                run.brief.space,
                outcome="aborted",
            )
            await tree.release(conn, brief_id)
            await conn.commit()
        receipts.append(receipt)
    return receipts
