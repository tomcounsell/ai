"""The `verify` routine: the project's suite at the head of its branch, run
in the kernel's check sandbox, each red group started as a fix task.

A run is a node (no `sdlc` marker) on a workspace the kernel provisions from
the routine's project spec, a child of the routine's objective with the
marker `{"objective": NAME, "kernel_tip": SHA}`. Its Brief's `base_sha` is
the branch head it tests, so the suite never runs in the kernel's own
checkout. The runner holds `run:<run>` and a turn slot while `checks.suite` runs, appends the usable
payload as `suite.ran`, starts each finding through `findings.start_fix`,
and removes the workspace last.
"""

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core import checks, db, findings, ledger, routines, slot, tasks, workspace
from core.findings import Finding
from core.routines import Context, Ran

ROLE = "verify"
SUITE_KEY = f"{ROLE}:{findings.SUITE_KEY}"
COLLECTION = "collection failure"  # pytest's JUnit message for a module that did not load


@dataclass
class CheckContext:
    """What `checks.suite`, `checks.stop_heard` and `checks.interrupted` read
    from a check's context, for a routine's run: the run's id and the dsn.
    A run has no task lock to lose, so `alive` is always true."""

    task_id: str
    dsn: str

    async def alive(self) -> bool:
        return True


def check_context(run: str, dsn: str) -> CheckContext:
    return CheckContext(task_id=run, dsn=dsn)


def kernel_tip(root: Path = routines.ROOT) -> str:
    """The kernel checkout's HEAD, read and nothing else."""
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


# -- findings from one suite run ---------------------------------------------------


def _first_line(failure: dict[str, str]) -> str:
    for text in (failure["message"], failure["text"]):
        for ln in text.splitlines():
            if ln.strip():
                return ln.strip()
    return f"{failure['kind']} with no message"


def _show(failure: dict[str, str]) -> str:
    return (
        f"{failure['id']} ({failure['kind']}): {findings.head(failure['message'])}\n"
        f"{findings.head(failure['text'])}".rstrip()
    )


def usable(payload: dict[str, Any]) -> bool:
    """A payload with a test list to read: no cause, and at least one test."""
    tests = payload.get("tests")
    return payload.get("cause") is None and tests is not None and sum(len(v) for v in tests.values()) > 0


def grouped(payload: dict[str, Any], failures: list[dict[str, str]], branch: str) -> list[Finding]:
    """The run's findings. A payload with no usable test list, or any
    collection failure, is the one `verify:suite` finding. Every other
    failed or errored case is grouped by the first line of its message;
    groups that share a key (the cases of one parametrized test) are one."""
    sha = payload["commit"]
    where = f"commit {sha}\ncommand: {payload['command']}"
    out: list[Finding] = []
    collection = [f for f in failures if f["message"].strip() == COLLECTION]
    if not usable(payload) or collection:
        why = (
            "a test module did not load"
            if collection
            else (payload.get("why") or payload.get("junit") or checks.NO_TESTS)
        )
        lines = [where, f"exit: {payload.get('exit')}", f"why: {why}"]
        lines += [_show(f) for f in collection]
        lines += ["output tail:", findings.tail(payload.get("tail") or "")]
        out.append(
            Finding(
                keys=(SUITE_KEY,),
                text=f"The suite at the head of {branch} ({sha[:12]}) gave no usable test report: {why}.",
                evidence="\n".join(lines),
            )
        )
        if not usable(payload):
            return out
    by_message: dict[str, list[dict[str, str]]] = {}
    for f in failures:
        if f["message"].strip() != COLLECTION:
            by_message.setdefault(_first_line(f), []).append(f)
    groups: list[tuple[set[str], list[str], list[dict[str, str]]]] = []
    for message, cases in by_message.items():
        keys = {f"{ROLE}:{findings.key_of(c['id'])}" for c in cases}
        joined = [g for g in groups if g[0] & keys]
        for g in joined:
            groups.remove(g)
            keys |= g[0]
        groups.append(
            (
                keys,
                [m for g in joined for m in g[1]] + [message],
                [c for g in joined for c in g[2]] + cases,
            )
        )
    for keys, messages, cases in groups:
        named = ", ".join(sorted({c["id"] for c in cases}))
        out.append(
            Finding(
                keys=tuple(sorted(keys)),
                text=(
                    f"{len(cases)} red at the head of {branch} ({sha[:12]}), failing with "
                    f"{'; '.join(findings.head(m) for m in messages)}: {named}."
                ),
                evidence="\n\n".join([where, *(_show(c) for c in cases)]),
            )
        )
    return out


UNREAD = "the JUnit report could not be read again; the failure's message is not kept"


def failures_of(kept: dict[str, Any], read: list[dict[str, str]]) -> list[dict[str, str]]:
    """The failures `read` from the JUnit report, or, when a resumed run can
    no longer read it, each failed and errored id the kept `suite.ran`
    recorded, with no message: the red is kept, not lost."""
    if read or not usable(kept):
        return read
    tests = kept["tests"]
    return [
        {"id": t, "kind": k, "message": UNREAD, "text": ""} for k in ("failed", "errored") for t in tests[k]
    ]


def seen_gone(payload: dict[str, Any], failures: list[dict[str, str]]) -> set[str]:
    """The keys this run saw pass: each passing test's, less any key a case
    still fails under; and `verify:suite` when the report was usable and
    no module failed to load."""
    if not usable(payload):
        return set()
    failing = {f"{ROLE}:{findings.key_of(f['id'])}" for f in failures}
    passed = {f"{ROLE}:{findings.key_of(t)}" for t in payload["tests"]["passed"]}
    gone = passed - failing
    if not any(f["message"].strip() == COLLECTION for f in failures):
        gone.add(SUITE_KEY)
    return gone


# -- the run ----------------------------------------------------------------------------


async def _remove(conn, run: str, b: tasks.Brief | None) -> None:
    """Remove the run's workspace and record it, so its ports free."""
    path = Path(b.mirror).parent if b and b.mirror else workspace.layout(run).root
    await asyncio.to_thread(workspace.remove, run, workspace.Layout(path))
    await ledger.append(
        conn,
        run,
        "workspace.removed",
        {
            "path": str(path),
            "provenance": ledger.provenance("routine", f"python -m core routine {ROLE}", False),
        },
    )


async def _reap(ctx: Context) -> None:
    """Remove the workspace of each earlier run that is stopped or closed
    and still has one: a run Tom stopped between firings."""
    closed = {r.get("run") for r in ctx.ran if r["outcome"] in ("finished", "failed")}
    for child in await tasks.children(ctx.conn, ctx.objective):
        row = await (
            await ctx.conn.execute(
                "SELECT payload->>%s FROM events WHERE task_id = %s AND type = 'task.started'",
                (routines.OBJECTIVE, child),
            )
        ).fetchone()
        if row is None or row[0] != ctx.routine.name:
            continue
        removed = await (
            await ctx.conn.execute(
                "SELECT 1 FROM events WHERE task_id = %s AND type = 'workspace.removed'", (child,)
            )
        ).fetchone()
        if removed or not (child in closed or await tasks.is_stopped(ctx.conn, child)):
            continue
        b = await tasks.brief(ctx.conn, child)
        if b.mirror:
            await _remove(ctx.conn, child, b)


async def run(ctx: Context) -> Ran:
    r = ctx.routine
    if ctx.start_project is None or not r.project or not ctx.dsn:
        raise routines.Refused(f"routine {r.name} starts a project run; none is given")
    async with routines.holding(ctx.conn, f"routine:{r.name}"):
        run_id = await routines.open_node(ctx)
        if run_id is None:
            await _reap(ctx)
            run_id = await ctx.start_project(
                ctx.conn,
                parent=ctx.objective,
                instruction=f"routine {r.name}: the suite at the head of {r.branch or 'the project branch'}",
                routine=r.name,
                ceiling=r.ceiling,
                model=r.model,
                project=r.project,
                branch=r.branch,
                marker={routines.OBJECTIVE: r.name, "kernel_tip": await asyncio.to_thread(kernel_tip)},
            )
    lock = await db.connect(ctx.dsn)
    try:
        got = await (
            await lock.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (f"run:{run_id}",))
        ).fetchone()
        if not got[0]:
            return Ran(run_id, "running", "already running")
        return await _run(ctx, run_id)
    finally:
        await lock.close()


async def _stopped(ctx: Context, run_id: str, b: tasks.Brief) -> Ran:
    """A stopped run starts no findings: remove its workspace and fail it."""
    await _remove(ctx.conn, run_id, b)
    return Ran(run_id, "failed", "stopped")


async def _run(ctx: Context, run_id: str) -> Ran:
    cc = check_context(run_id, ctx.dsn)
    rows = await ledger.read(ctx.conn, run_id)
    b = await tasks.brief(ctx.conn, run_id)
    tip = rows[0]["payload"].get("kernel_tip", "")
    if not b.mirror or not b.base_sha:
        return Ran(run_id, "failed", "the run has no provisioned workspace")
    lay = workspace.Layout(Path(b.mirror).parent)
    kept = next((x["payload"] for x in reversed(rows) if x["type"] == checks.SUITE), None)
    if kept is None:
        async with slot.held(run_id, ctx.dsn):
            # A stop that came while the run waited for the slot was sent
            # before `stop_heard` listens.
            if await tasks.is_stopped(ctx.conn, run_id):
                return await _stopped(ctx, run_id, b)
            digest = await asyncio.to_thread(
                checks.environment_digest, b.mirror, b.base_sha, b.project or {}, lay.root.parent / "bin"
            )
            try:
                async with checks.stop_heard(cc) as stop:
                    payload = await checks.suite(cc, lay, b, b.base_sha, ROLE, stop, digest=digest)
            except checks._Stopped:
                return await _stopped(ctx, run_id, b)
        if payload["cause"] == "kernel":
            await _remove(ctx.conn, run_id, b)
            return Ran(run_id, "failed", payload["why"])
        event_id, why = await ledger.try_append(ctx.conn, run_id, checks.SUITE, payload)
        if event_id is None:
            await _remove(ctx.conn, run_id, b)
            return Ran(run_id, "failed", f"the {checks.SUITE} row: {ledger.UNSTORABLE}: {why}")
        kept = payload
    if await tasks.is_stopped(ctx.conn, run_id):
        return await _stopped(ctx, run_id, b)  # a stopped run is refused as a parent
    read = await asyncio.to_thread(checks.junit_failures, lay.checks, f"test-{ROLE}-{b.base_sha[:12]}")
    failures = failures_of(kept, read)
    found = grouped(kept, failures, b.target_branch or ctx.routine.branch or "")
    held_ended = await findings.held_ended(ctx.conn, f"{ROLE}:")
    cleared = sorted(held_ended & seen_gone(kept, failures))
    started = held = 0
    for finding in found:
        _task, new = await findings.start_fix(
            ctx.conn, finding, parent=run_id, routine=ctx.routine, start_project=ctx.start_project
        )
        started, held = started + new, held + (not new)
    await _remove(ctx.conn, run_id, b)
    red = f"{len(failures)} red in {len(found)} groups" if usable(kept) else "no usable report"
    summary = f"{red} at {b.base_sha[:12]} (kernel at {tip[:12]}); started {started}, held {held}"
    return Ran(run_id, "finished", summary, cleared)
