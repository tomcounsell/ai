"""The emulator sweep: every item in `$VALOR_DEMO/items`, in the `bare`,
`clarify` and `routed` arms, each a replay that is a child of this run.

The run is an objective node under the routine's objective, so the tree's
rollup is the sweep's spending. The runner holds the session lock
`run:<run>` for the driver's life, as `core run` holds `run:<task>`, and
runs the replay driver (`tests/emulator/replay.py`) as a subprocess, the
same code that runs by hand. It requests no effect and writes no verdict on
any task; an item that fails is recorded and the next one runs.
"""

import asyncio
import json
import sys
from pathlib import Path

from core import db, tasks
from core.routines import Context, Ran, state_of
from core.settings import resolve_model, settings

ARMS = ("bare", "clarify", "routed")
ROOT = Path(__file__).resolve().parent.parent.parent


def items(demo: Path) -> list[Path]:
    return sorted((demo / "items").glob("*.json"))


def _result(demo: Path, name: str) -> dict | None:
    path = demo / "results" / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else None


async def _drive(item: Path, arm: str, run: str, name: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.emulator.replay",
        str(item),
        "--arm",
        arm,
        "--parent",
        run,
        "--name",
        name,
        "--judge",
        cwd=ROOT,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode, out.decode(errors="replace")


async def _preempted(conn, task_id: str | None) -> int:
    if not task_id:
        return 0
    row = await (
        await conn.execute(
            "SELECT count(*) FROM events WHERE task_id = %s AND type = 'turn.ended' "
            "AND payload->>'outcome' = 'preempted'",
            (task_id,),
        )
    ).fetchone()
    return row[0]


def _row(result: dict | None, preempted: int) -> dict:
    if result is None:
        return {"outcome": None, "preempted": preempted}
    judge = result.get("judge") or {}
    return {
        "outcome": result.get("outcome"),
        "task": result.get("task_id"),
        "scores": judge.get("scores"),
        "hidden_tests": [c.get("exit") for c in judge.get("verification") or []],
        "attention": result.get("attention", {}),
        "kernel_spend_usd": result.get("kernel_spend_usd"),
        "emulator_spend_usd": result.get("emulator_spend_usd"),
        "preempted": preempted,
    }


async def _open(ctx: Context, demo: Path) -> str | None:
    """The objective's latest run that has no report yet and was not
    stopped: a firing that crashed, or one still running."""
    for child in reversed(await tasks.children(ctx.conn, ctx.objective)):
        if (demo / "sweeps" / f"{child}.json").exists() or await state_of(ctx.conn, child) == "stopped":
            return None
        return child
    return None


async def run(ctx: Context) -> Ran:
    demo = Path(settings.demo_dir)
    found = items(demo)
    run_id = await _open(ctx, demo)
    if run_id is None:
        run_id = await tasks.start_child(
            ctx.conn,
            ctx.objective,
            ceiling=ctx.routine.ceiling,
            marker={"objective": ctx.routine.name},
            by="routine",
            via=f"python -m core routine {ctx.routine.name}",
            instruction=f"routine {ctx.routine.name}: sweep of {ctx.now.date()}",
            model=resolve_model(ctx.routine.model),
            routine=ctx.routine.name,
        )
    lock = await db.connect(ctx.dsn)
    try:
        got = await (
            await lock.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (f"run:{run_id}",))
        ).fetchone()
        if not got[0]:
            return Ran(run_id, "running", "already running")
        report: dict = {"run": run_id, "at": ctx.now.isoformat(), "items": {}}
        failed = 0
        for item in found:
            name = json.loads(item.read_text()).get("name", item.stem)
            report["items"][name] = {"baseline": _row(_result(demo, f"{name}-bare"), 0)}
            for arm in ARMS:
                if await state_of(ctx.conn, run_id) == "stopped":
                    break
                run_name = f"{name}-{arm}-{run_id}"
                have = _result(demo, run_name)
                if have is None or not have.get("outcome") or "judge" not in have:
                    code, tail = await _drive(item, arm, run_id, run_name)
                    have = _result(demo, run_name)
                    if code != 0 or have is None:
                        failed += 1
                        report["items"][name][arm] = {"failed": tail}
                        continue
                report["items"][name][arm] = _row(have, await _preempted(ctx.conn, have.get("task_id")))
        out = demo / "sweeps" / f"{run_id}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n")
        summary = f"{len(found)} items, {failed} failed; report {out}"
        return Ran(run_id, "finished", summary)
    finally:
        await lock.close()
