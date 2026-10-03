"""Replay one item in one arm: build its workspace, start the task, and run
it with a stand-in for Tom until the task reaches a held merge the
stand-in accepts (or its feedback rounds are spent), stops, or is handed
to Tom. Writes the record to $VALOR_DEMO/results/<run>.json.

    .venv/bin/python -m tests.emulator.replay ITEM.json --arm bare|clarify|routed \
        [--run NAME] [--judge]

The arm is what the kernel's judge decides. `routed` uses the real
judgement legs. `bare` and `clarify` force it without any switch in the
kernel: the driver starts the local judgement upstream
(`tests/judgement_upstream.py`) answering `precise` or `thin` and points the
kernel's leg endpoints at it (`VALOR_JEV_URL`, `VALOR_OPEN_WEIGHT_URL`), so
the real judge runner, port, metering, and rows run, and each row's
endpoint (127.0.0.1) shows the arm was forced. A loopback endpoint is sent
a placeholder key, never a real one.

An item is a JSON file:

    {
      "name": "psyoptimal-894",          run names default to <name>-<arm>
      "repo": "yudame/psyoptimal",       OWNER/NAME, or a local repository path
      "base": "<sha>",                   the commit the request was made against
      "pr": 894,                         the merged reference PR (for the judge)
      "reference_diff": "ref.diff",      or a diff file in place of a PR
      "services": ["postgres"],          postgres, redis, or []
      "request": "Tom's request, verbatim",
      "answer_key": "psyoptimal-894.key.md",
      "verify": ["shell commands the judge runs on the final commit, sandboxed"],
      "project": {"kind": "python-uv", "setup": ["uv sync --frozen"],
                  "suite": "uv run pytest -q --junitxml={junit} tests",
                  "env": {"UV_PYTHON": "3.12"}}
    }

`project` is optional; without it the suite is `true`, and a delivery says
the suite was not run.

Relative paths are relative to the item file. Keep items and answer keys
outside every run directory (for example $VALOR_DEMO/items/): a turn's
sandbox lets it read its own run and nothing else in $VALOR_DEMO.

The task runs at effect ceiling `act` with no governance grant. Every held
`push_branch` is approved and released by this driver under Tom's standing
permission for pushes to local bare origins, and only when the push URL in
the kernel's record of the task (`core workspace show`, the URL the
kernel's performer pushes to) is the bare origin the kernel provisioned in
the task's directory. Nothing is read from the turn's workdir, whose git
config the turn can rewrite. The driver never answers a merge:
every `merge` effect of the task is skipped, the current one and any a later
candidate superseded. Any other held effect stays held and the run ends.

A task in `merge` is read from its status, in this order: a delivery that
did not pass ends the run `to tom`; a governance instance not granted, or
a join of `governance_refused`, exits the driver to await Tom's grant; a
refused merge ends the run `to tom`; a held merge goes to the stand-in,
whose accept or spent feedback rounds end the run `held`; otherwise the
task runs on. A stopped task ends the run `stopped`.

Each answer of `core run` ends the step one way. `QUESTION`, `DELIVERED`,
`STOPPED` and `MERGED` go back to the task's status. `ALREADY RUNNING`
(another run of the task holds its run lock) waits on that lock until it is
free, then steps again. `NO RUNNER` (a check stage with no runner),
`FAILED`, `IDLE` (turns that ended without their stage's signal), `LOCK
LOST` and `LEGACY`, and any other answer, exit the driver with the outcome
unset and the answer recorded as the reason; the next invocation resumes
the same task. A run whose result has an outcome is refused unless `--rebuild` is
given. Each driver holds one of $VALOR_DEMO_SLOTS (default 3) machine
slots ($VALOR_DEMO/claude-turn.lock.N) for its whole invocation.

Spend: the item task's metered spending through the kernel's gateway
(`kernel_spend_usd`), and the stand-in's and judge's calls, metered through
a gateway of the driver's own onto the run's emulator task, a calibration
task (`emulator_spend_usd`). Both are read from the ledger and reported;
nothing stops on them.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from core import workspace as kws
from tests.emulator import workspace as replay_workspace
from tests.emulator.common import (
    DEMO,
    Meter,
    core,
    machine_lock,
    mirror_diff,
    now,
    review_rev,
    rows,
    spend_of,
    start_emulator_task,
    status,
    wait_run_lock,
)
from tests.emulator.stand_in import MODEL as STAND_IN_MODEL
from tests.emulator.stand_in import stand_in

# The answers of `core run` after which the next step reads the task's status.
GOES_ON = ("QUESTION", "DELIVERED", "STOPPED", "MERGED")
PUSH_NOTE = (
    "Tom's standing permission: pushes to a replay workspace's local bare origin are "
    "pre-authorized (replay driver)"
)


def load_item(path: str) -> dict:
    item_path = Path(path).resolve()
    item = json.loads(item_path.read_text())
    for key in ("answer_key", "reference_diff"):
        if item.get(key):
            item[key] = str((item_path.parent / item[key]).resolve())
    if Path(item["repo"]).expanduser().exists() or item["repo"].startswith((".", "/")):
        item["repo"] = str((item_path.parent / item["repo"]).resolve())
    item["item_file"] = str(item_path)
    item.setdefault("services", [])
    item.setdefault("verify", [])
    item.setdefault("name", item_path.stem)
    for key in ("repo", "base", "request", "answer_key"):
        if not item.get(key):
            raise SystemExit(f"{item_path}: missing {key!r}")
    return item


def _save(result: dict) -> None:
    path = Path(result["result_file"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=2) + "\n")
    tmp.replace(path)


def release_pushes(task_id: str, ws: dict, log: list) -> list[str]:
    """Approve and release every held push of the task when the push URL
    in the kernel's record of the task (`ws["origin"]`, from `core
    workspace show`) is the bare origin the kernel provisioned in the
    task's directory. Every merge effect is skipped: the driver never
    answers a merge. Returns the held effects left alone."""
    own_origin = ws["origin"] == str(kws.Layout(Path(ws["task_dir"])).origin)
    left = []
    for line in core("pending").splitlines():
        effect_id, owner, action, *_ = line.split()
        if owner != task_id or action == "merge":
            continue
        if action != "push_branch" or not own_origin:
            left.append(effect_id)
            log.append(
                {"at": now(), "step": "held effect left for Tom", "effect": line, "push_url": ws["origin"]}
            )
            continue
        core(
            "approve",
            effect_id,
            "--note",
            PUSH_NOTE,
            "--by",
            "replay driver",
            "--via",
            "replay driver",
            "--role-played",
        )
        released = core("release", effect_id)
        log.append({"at": now(), "step": "push released", "effect_id": effect_id, "outcome": released})
    return left


def merge_case(state: dict) -> str:
    """What a task in `merge` waits on: `to tom`, `awaiting a grant`,
    `held`, or `run`."""
    if (state.get("delivery") or {}).get("outcome") == "did_not_pass":
        return "to tom"
    join = state.get("join") or {}
    if (
        any(not g["granted"] for g in state.get("governance") or [])
        or join.get("outcome") == "governance_refused"
    ):
        return "awaiting a grant"
    effect = state.get("merge_effect") or {}
    if effect.get("state") == "refused":
        return "to tom"
    if effect.get("state") == "held":
        return "held"
    return "run"


def summarize(result: dict, meter: Meter | None = None) -> None:
    """Fill in the record from the ledger."""
    task_id = result["task_id"]
    state = status(task_id)
    ledger_rows = rows(task_id)
    result["questions"] = [
        {"question": a["question"], "answer": a["answer"], "provenance": a.get("provenance")}
        for a in state["attention"]
        if a["kind"] == "question"
    ]
    result["feedback"] = [
        {"feedback": a["feedback"], "on_delivery": a["on_delivery"], "provenance": a["provenance"]}
        for a in state["attention"]
        if a["kind"] == "feedback"
    ]
    result["feedback_rounds"] = len(result["feedback"])
    result["attention"] = dict(Counter(a["kind"] for a in state["attention"]))
    result["deliveries"] = [r["payload"]["summary"] for r in ledger_rows if r["type"] == "task.delivered"]
    result["turns"] = [
        {
            "turn_id": t["turn_id"],
            "outcome": t["outcome"],
            "metered_usd": t.get("metered_usd_micros", 0) / 1e6,
            "harness_session_cumulative_usd": (t.get("result") or {}).get("harness_reported_usd"),
        }
        for t in (r["payload"] for r in ledger_rows if r["type"] == "turn.ended")
    ]
    result["kernel_spend_usd"] = state["spent_usd_micros"] / 1e6
    spent = meter.spend() if meter else spend_of(result["emulator_task"])
    result["emulator_spend_usd"] = spent["usd"]
    result["open_calls"] = spent["open_calls"]
    result["merge_effect_id"] = (state.get("merge_effect") or {}).get("effect_id")
    result["final_rev"] = review_rev(task_id, state)
    ws = result["workspace"]
    if result["final_rev"]:
        result["diff_stat"] = mirror_diff(ws["mirror"], ws["base"], result["final_rev"], "--stat")
        result["diff_shortstat"] = mirror_diff(ws["mirror"], ws["base"], result["final_rev"], "--shortstat")
    else:
        result["diff_stat"] = result["diff_shortstat"] = ""


FORCED = {"bare": "precise", "clarify": "thin"}


@contextmanager
def judged_as(arm: str):
    """For a forced arm, a local judgement upstream answering the arm's
    verdict, with the kernel's leg endpoints pointed at it while the run
    lasts; for `routed`, nothing."""
    if arm not in FORCED:
        yield
        return
    root = Path(__file__).resolve().parent.parent.parent
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.judgement_upstream", "--answer", FORCED[arm]],
        cwd=root,
        stdout=subprocess.PIPE,
        text=True,
    )
    saved = {k: os.environ.get(k) for k in ("VALOR_JEV_URL", "VALOR_OPEN_WEIGHT_URL")}
    try:
        os.environ.update(json.loads(proc.stdout.readline()))
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        proc.kill()
        proc.wait()


def replay(item: dict, arm: str, args) -> dict:
    with judged_as(arm):
        return _replay(item, arm, args)


def _replay(item: dict, arm: str, args) -> dict:
    run_name = args.run or f"{item['name']}-{arm}"
    result_file = DEMO / "results" / f"{run_name}.json"
    result = json.loads(result_file.read_text()) if result_file.exists() else None
    if result and result.get("outcome") and not args.rebuild:
        raise SystemExit(
            f"{result_file} is finished ({result['outcome']}); pass --rebuild to replay it again"
        )
    if result is None or args.rebuild:
        result = None

    with machine_lock(f"replay {run_name}"):
        started = time.monotonic()
        ws = replay_workspace.build(
            item["repo"],
            item["base"],
            run_name,
            item["services"],
            max_output_tokens=args.max_output_tokens,
            rebuild=args.rebuild,
            **item.get("project", {}),
        )
        if result is not None and result.get("task_id"):
            ws = replay_workspace.attach(
                {**ws, "task_id": result["task_id"]}, json.loads(core("workspace", "show", result["task_id"]))
            )
        if result is None:
            result = {
                "run": run_name,
                "item": item,
                "arm": arm,
                "model": args.model,
                "stand_in_model": args.stand_in_model,
                "max_feedback": args.max_feedback,
                "workspace": ws,
                "result_file": str(result_file),
                "started_at": now(),
                "wall_seconds": 0.0,
                "outcome": None,
                "log": [],
            }
            result["task_id"] = core(
                "start",
                item["request"],
                "--ceiling",
                "act",
                "--project",
                ws["spec"],
                "--base",
                ws["base"],
                "--model",
                args.model,
            )
            ws = replay_workspace.attach(
                {**ws, "task_id": result["task_id"]}, json.loads(core("workspace", "show", result["task_id"]))
            )
            result["workspace"] = ws
            _save(result)
        if not result.get("emulator_task"):
            result["emulator_task"] = start_emulator_task(run_name, result["task_id"])
            _save(result)
        result.pop("paused", None)
        task_id = result["task_id"]
        print(f"{run_name}: task {task_id}, emulator task {result['emulator_task']}", file=sys.stderr)

        with Meter(result["emulator_task"]) as meter:
            while result["outcome"] is None and not result.get("paused"):
                step(result, item, ws, args, meter)
                result["wall_seconds"] = round(result["wall_seconds"] + time.monotonic() - started, 1)
                started = time.monotonic()
                _save(result)
            if result["outcome"] is not None:
                result["ended_at"] = now()
            summarize(result, meter)
        _save(result)
    if result.get("paused"):
        print(f"{run_name}: paused, outcome unset: {result['paused']}", file=sys.stderr)
    return result


def step(result: dict, item: dict, ws: dict, args, meter: Meter) -> None:
    """One move of the run: answer the stand-in's part, end the run, pause
    the driver, or run the task once."""
    task_id, log, run_name = result["task_id"], result["log"], result["run"]
    state = status(task_id)
    if state["state"] in ("stopped", "merged"):
        result["outcome"] = state["state"]
        return
    case = merge_case(state) if state["state"] == "merge" else None
    if case == "to tom":
        result["outcome"] = "to tom"
        return
    if case == "awaiting a grant":
        result["paused"] = "awaiting a grant"
        return
    if state["state"] == "waiting" or case == "held":
        reply = stand_in(
            task_id,
            item["answer_key"],
            meter=meter,
            mirror=ws["mirror"],
            base=ws["base"],
            model=args.stand_in_model,
            max_feedback=args.max_feedback,
            workdir=Path(ws["run_dir"]),
        )
        log.append({"at": now(), "step": "stand-in", **reply})
        print(
            f"{run_name}: stand-in {reply['kind']}: {(reply['text'] or reply['reason'] or '')[:200]}",
            file=sys.stderr,
        )
        if reply["kind"] in ("accept", "cap"):
            result["outcome"] = "held"
        if reply["kind"] != "nothing":
            return
    try:
        line = core("run", task_id)
    except RuntimeError as exc:
        log.append({"at": now(), "step": "run failed", "error": str(exc)})
        result["paused"] = f"failed: {exc}"
        return
    log.append({"at": now(), "step": "run", "said": line[:2000]})
    said = line.splitlines()[0] if line else ""
    print(f"{run_name}: {said}", file=sys.stderr)
    if release_pushes(task_id, ws, log):
        result["outcome"] = "an effect other than a local push is held for Tom"
    elif said.startswith("ALREADY RUNNING"):
        log.append({"at": now(), "step": "waiting on the run lock"})
        wait_run_lock(task_id)
    elif not said.startswith(GOES_ON):
        # NO RUNNER (a verdict recorded by hand, from a blind checkout of
        # the mirror, lets the next invocation resume), FAILED, IDLE, LOCK
        # LOST, LEGACY, or an answer this driver does not know.
        result["paused"] = said or "core run said nothing"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("item")
    parser.add_argument("--arm", required=True, choices=["bare", "clarify", "routed"])
    parser.add_argument("--run", help="the run's name; default <item>-<arm>")
    parser.add_argument("--model", default="claude-opus-5-5", help="the working turn's model")
    parser.add_argument("--stand-in-model", default=STAND_IN_MODEL)
    parser.add_argument("--max-feedback", type=int, default=2)
    parser.add_argument(
        "--max-output-tokens", type=int, help="per-call output cap for the turn's model calls"
    )
    parser.add_argument("--rebuild", action="store_true", help="delete the run and its result and start over")
    parser.add_argument("--judge", action="store_true", help="judge the result when the run ends")
    args = parser.parse_args()
    item = load_item(args.item)
    result = replay(item, args.arm, args)
    if args.judge and result.get("outcome"):
        from tests.emulator.judge import judge

        result = judge(result["result_file"])
    shown = {
        k: result.get(k)
        for k in (
            "run",
            "task_id",
            "emulator_task",
            "outcome",
            "paused",
            "feedback_rounds",
            "kernel_spend_usd",
            "emulator_spend_usd",
            "final_rev",
        )
    }
    shown["questions"] = len(result.get("questions", []))
    shown["diff"] = result.get("diff_shortstat")
    if "judge" in result:
        shown["judge"] = result["judge"].get("scores")
    print(json.dumps(shown, indent=2))


if __name__ == "__main__":
    main()
