"""The emulator sweep: every item in `$VALOR_DEMO/items`, in the `bare`,
`clarify` and `routed` arms, each a replay that is a child of this run.

The run is an objective node under the routine's objective, so the tree's
rollup is the sweep's spending. The runner holds the session lock
`run:<run>` for the driver's life, as `core run` holds `run:<task>`, and
runs the replay driver (`tests/emulator/replay.py`) as a subprocess, the
same code that runs by hand. It requests no effect and writes no verdict on
any task; an item that fails is recorded and the next one runs. A driver
that exits nonzero leaves its exit code and output in
`results/<run>.driver.log` at once.

Once the report is written, each item where `clarify` or `routed` did worse
than this run's `bare` (`regressions`, the D5 record's bar) becomes one
finding, `emulator:<item>`, started through `findings.start_fix` on the
routine's project.
"""

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

from core import db, findings, routines, tasks
from core.findings import Finding
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


def _driver_log(demo: Path, name: str, code: int, out: str, at: str) -> None:
    """Append a failed driver's exit code and output to
    `results/<name>.driver.log`, written at once, so it outlives a sweep
    that pauses or crashes before its report."""
    path = demo / "results" / f"{name}.driver.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(f"--- {at} exit {code}\n{out}\n")


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
        "hidden": hidden((judge.get("verification") or [{}])[0].get("output_tail")),
        "attention": result.get("attention", {}),
        "kernel_spend_usd": result.get("kernel_spend_usd"),
        "emulator_spend_usd": result.get("emulator_spend_usd"),
        "preempted": preempted,
    }


# -- the hidden test command's output ---------------------------------------------------

_PYTEST_SUMMARY = re.compile(r"^\W*((?:\d+ [a-z]+(?:, )?)+) in [\d.]+s\b", re.MULTILINE)
_DJANGO_FAILED = re.compile(r"^FAILED \(([^)]*)\)", re.MULTILINE)
_DJANGO_OK = re.compile(r"^OK\b", re.MULTILINE)
_PYTEST_ID = re.compile(r"^(?:FAILED|ERROR) ([^\s(]\S*)", re.MULTILINE)
_DJANGO_ID = re.compile(r"^(?:FAIL|ERROR): (.+)$", re.MULTILINE)


def hidden(tail: str | None) -> dict[str, Any] | None:
    """The hidden command's failures read from its output tail: `failed`
    (failed and errored) from the last summary line, pytest's `N failed,
    M error` or Django's `FAILED (failures=a, errors=b)`, `OK` being zero;
    `failing`, the ids on pytest's `FAILED`/`ERROR` lines and Django's
    `FAIL:`/`ERROR:` lines, best effort since the tail is the last 4000
    characters. None when no summary line is there."""
    if not tail:
        return None
    marks: list[tuple[int, int]] = []
    for m in _PYTEST_SUMMARY.finditer(tail):
        counts = {w: int(n) for n, w in re.findall(r"(\d+) ([a-z]+)", m.group(1))}
        marks.append((m.start(), counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0)))
    for m in _DJANGO_FAILED.finditer(tail):
        counts = {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", m.group(1))}
        marks.append((m.start(), counts.get("failures", 0) + counts.get("errors", 0)))
    for m in _DJANGO_OK.finditer(tail):
        marks.append((m.start(), 0))
    if not marks:
        return None
    failing = [*_PYTEST_ID.findall(tail), *(x.strip() for x in _DJANGO_ID.findall(tail))]
    return {"failed": max(marks)[1], "failing": sorted(set(failing))}


# -- the bar --------------------------------------------------------------------------


def _total(row: dict) -> int | None:
    scores = row.get("scores") or {}
    got = [scores.get(k) for k in ("fidelity", "correctness", "simplicity")]
    return sum(got) if all(isinstance(v, int | float) for v in got) else None


def _compared(row: dict | None) -> bool:
    """An arm with an outcome, not stopped, whose driver did not fail."""
    return bool(row) and "failed" not in row and bool(row.get("outcome")) and row.get("outcome") != "stopped"


def worse(arm: dict, bare: dict) -> list[str]:
    """The D5 record's rules `arm` breaks against `bare`. A rule whose
    numbers are missing on either side is not applied; a passed count is
    never compared."""
    broke = []
    if bare.get("outcome") == "merged" and arm.get("outcome") != "merged":
        broke.append(f"ended {arm.get('outcome')} where bare merged")
    mine, theirs = _total(arm), _total(bare)
    if mine is not None and theirs is not None and mine < theirs - 1:
        broke.append(f"judged total {mine} against bare's {theirs}")
    a, b = arm.get("hidden"), bare.get("hidden")
    if a and b and a["failed"] > b["failed"]:
        broke.append(f"{a['failed']} hidden failures against bare's {b['failed']}")
    return broke


def regressions(report: dict) -> dict[str, dict[str, list[str]]]:
    """Per item, each of `clarify` and `routed` that is worse than this
    run's `bare`, with the rules it broke. An item whose bare or arm was not
    compared is left out of that comparison."""
    out: dict[str, dict[str, list[str]]] = {}
    for name, rows in report["items"].items():
        bare = rows.get("bare")
        if not _compared(bare):
            continue
        for arm in ("clarify", "routed"):
            if _compared(rows.get(arm)) and (broke := worse(rows[arm], bare)):
                out.setdefault(name, {})[arm] = broke
    return out


def compared_items(report: dict) -> set[str]:
    """The items whose bare and at least one other arm were compared."""
    return {
        name
        for name, rows in report["items"].items()
        if _compared(rows.get("bare")) and any(_compared(rows.get(a)) for a in ("clarify", "routed"))
    }


def finding(name: str, rows: dict, broke: dict[str, list[str]], run_id: str, demo: Path) -> Finding:
    """One item's finding: the arms and the rules they broke, every arm's
    outcome, scores, hidden counts and replay task id, the hidden ids that
    fail in a worse arm and not in bare, and a clarify arm's questions and
    the stand-in's answers. Nothing points at the replay directory."""
    lines = [f"emulator run {run_id}, item {name}"]
    for arm in ARMS:
        row = rows.get(arm) or {}
        h = row.get("hidden") or {}
        lines.append(
            f"{arm}: outcome {row.get('outcome')}, replay task {row.get('task')}, scores {row.get('scores')}, "
            f"hidden failures {h.get('failed', 'not read')}"
        )
    bare_ids = set(((rows.get("bare") or {}).get("hidden") or {}).get("failing") or [])
    for arm in broke:
        new = sorted(set((rows[arm].get("hidden") or {}).get("failing") or []) - bare_ids)
        if new:
            lines.append(f"failing in {arm} and not in bare: {', '.join(new)}")
    if "clarify" in broke:
        result = _result(demo, f"{name}-clarify-{run_id}") or {}
        for q in result.get("questions") or []:
            lines.append(f"clarify asked:\n{findings.head(str(q.get('question', '')))}")
            lines.append(f"the stand-in answered:\n{findings.head(str(q.get('answer', '')))}")
    said = "; ".join(f"{arm} {', '.join(rules)}" for arm, rules in broke.items())
    return Finding(
        keys=(f"emulator:{name}",),
        text=f"In the emulator sweep, item {name} did worse than its bare run: {said}.",
        evidence="\n".join(lines),
    )


# -- the run --------------------------------------------------------------------------


async def run(ctx: Context) -> Ran:
    demo = Path(settings.demo_dir)
    found = items(demo)
    async with routines.holding(ctx.conn, f"routine:{ctx.routine.name}"):
        run_id = await routines.open_node(ctx)
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
        failed = paused = 0
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
                        _driver_log(demo, run_name, code, tail, ctx.now.isoformat())
                        failed += 1
                        report["items"][name][arm] = {"failed": tail}
                        continue
                if have.get("outcome") is None:
                    paused += 1  # the driver paused; the next firing resumes it
                report["items"][name][arm] = _row(have, await _preempted(ctx.conn, have.get("task_id")))
        if paused:
            return Ran(run_id, "running", f"{paused} replays paused; the next firing resumes them")
        out = demo / "sweeps" / f"{run_id}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n")
        worse_items = regressions(report)
        started = held = 0
        if worse_items and (ctx.start_project is None or not ctx.routine.project):
            raise routines.Refused(f"routine {ctx.routine.name} names no project for its findings")
        for name, broke in worse_items.items():
            _task, new = await findings.start_fix(
                ctx.conn,
                finding(name, report["items"][name], broke, run_id, demo),
                parent=run_id,
                routine=ctx.routine,
                start_project=ctx.start_project,
            )
            started, held = started + new, held + (not new)
        held_ended = await findings.held_ended(ctx.conn, "emulator:")
        cleared = sorted(held_ended & {f"emulator:{n}" for n in compared_items(report) - set(worse_items)})
        summary = (
            f"{len(found)} items, {failed} failed, {len(worse_items)} worse than bare "
            f"(started {started}, held {held}); report {out}"
        )
        return Ran(run_id, "finished", summary, cleared)
    finally:
        await lock.close()
