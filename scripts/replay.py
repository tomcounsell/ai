"""Replay one item in one arm: build its workspace, start the task, and run
it with a stand-in for Tom until the stand-in accepts a delivery, the
feedback rounds run out, or the budget does. Writes the record to
$VALOR_DEMO/results/<run>.json.

    .venv/bin/python scripts/replay.py ITEM.json --arm bare|clarify [--judge]

An item is a JSON file:

    {
      "name": "psyoptimal-894",          run names are <name>-<arm>
      "repo": "yudame/psyoptimal",       OWNER/NAME, or a local repository path
      "base": "<sha>",                   the commit the request was made against
      "pr": 894,                         the merged reference PR (for the judge)
      "reference_diff": "ref.diff",      or a diff file in place of a PR
      "services": ["postgres"],          postgres, redis, or []
      "request": "Tom's request, verbatim",
      "answer_key": "psyoptimal-894.key.md",
      "verify": ["shell commands the judge runs in the workspace, sandboxed"]
    }

Relative paths are relative to the item file. Keep items and answer keys
outside every run directory (for example $VALOR_DEMO/items/): a turn's
sandbox lets it read its own run and nothing else in $VALOR_DEMO.

The task runs at effect ceiling `act` with no governance grant. Every held
`push_branch` is approved and released by this driver under Tom's standing
permission for pushes to local bare origins, and only when the workspace's
push URL is the run's own `origin.git`; any other held effect stays held and
the run ends. Each driver holds one of $VALOR_DEMO_SLOTS (default 3) machine slots
($VALOR_DEMO/claude-turn.lock.N) for its whole run. A run whose result file has no outcome yet is resumed: the
driver continues its task instead of starting another.

Live spend: up to the task's budget (default $8.00, Opus 5.5) through the
kernel's gateway, plus the stand-in's and judge's calls, logged in
$VALOR_DEMO/costs.jsonl.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import replay_workspace
from replay_common import DEMO, core, git, machine_lock, now, status, ws_git
from role_play_tom import stand_in

from core import db, ledger

MAX_RUNS = 16
MAX_FAILED_RUNS = 2
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
    """Approve and release every held push of the task whose push URL is
    the run's own bare origin. Returns the held effects left alone."""
    left = []
    for line in core("pending").splitlines():
        effect_id, owner, action, *_ = line.split()
        if owner != task_id:
            continue
        urls = ws_git(ws["workdir"], "remote", "get-url", "--push", "--all", "origin", check=False).split()
        if action != "push_branch" or urls != [ws["origin"]]:
            left.append(effect_id)
            log.append({"at": now(), "step": "held effect left for Tom", "effect": line, "push_urls": urls})
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


async def _rows(task_id: str) -> list[dict]:
    async with await db.connect() as conn:
        return await ledger.read(conn, task_id)


def summarize(result: dict, item: dict, ws: dict) -> None:
    """Fill in the record from the ledger and the bare origin."""
    task_id = result["task_id"]
    state = status(task_id)
    rows = asyncio.run(_rows(task_id))
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
    result["deliveries"] = [r["payload"]["summary"] for r in rows if r["type"] == "task.delivered"]
    ended = [r["payload"] for r in rows if r["type"] == "turn.ended"]
    result["turns"] = [
        {
            "turn_id": t["turn_id"],
            "outcome": t["outcome"],
            "metered_usd": t.get("metered_usd_micros", 0) / 1e6,
            "harness_session_cumulative_usd": (t.get("result") or {}).get("harness_reported_usd"),
        }
        for t in ended
    ]
    result["spend"] = {
        "gateway_usd": state["charged_usd_micros"] / 1e6,
        "committed_usd": state["committed_usd_micros"] / 1e6,
        "stand_in_usd": round(sum(s.get("usd") or 0 for s in result["log"] if s["step"] == "stand-in"), 6),
    }
    pushes = [
        r["payload"]["result"]
        for r in rows
        if r["type"] == "effect.outcome"
        and r["payload"]["kind"] == "done"
        and "sha" in r["payload"]["result"]
    ]
    origin = ws["origin"]
    final = None
    if pushes:
        final = {"branch": pushes[-1]["branch"], "sha": pushes[-1]["sha"], "pushed": True}
    head = ws_git(ws["workdir"], "rev-parse", "HEAD", check=False)
    if final is None and head and head != ws["base"]:
        final = {"branch": ws_git(ws["workdir"], "branch", "--show-current"), "sha": head, "pushed": False}
    result["final"] = final
    result["workspace_head"] = head
    if final:
        repo = origin if final["pushed"] else ws["workdir"]
        g = git if final["pushed"] else ws_git
        result["diff_stat"] = g(repo, "diff", "--no-ext-diff", "--stat", ws["base"], final["sha"])
        result["diff_shortstat"] = g(repo, "diff", "--no-ext-diff", "--shortstat", ws["base"], final["sha"])
    else:
        result["diff_stat"] = result["diff_shortstat"] = ""


def replay(item: dict, arm: str, args) -> dict:
    run_name = f"{item['name']}-{arm}"
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
        )
        if result is None:
            result = {
                "run": run_name,
                "item": item,
                "arm": arm,
                "model": args.model,
                "budget_usd": args.budget,
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
                "--budget-usd",
                str(args.budget),
                "--ceiling",
                "act",
                "--workspace",
                ws["workdir"],
                "--model",
                args.model,
                "--harness-config",
                ws["harness_config"],
                "--mode",
                arm,
            )
            _save(result)
        task_id, log = result["task_id"], result["log"]
        print(f"{run_name}: task {task_id}", file=sys.stderr)

        runs = failed = 0
        while result["outcome"] is None:
            if runs >= MAX_RUNS:
                result["outcome"] = "run cap"
                break
            replay_workspace.ensure_services(
                item["services"], ws.get("redis_port", replay_workspace.REDIS_PORT)
            )
            line = core("run", task_id)
            runs += 1
            log.append({"at": now(), "step": "run", "said": line[:2000]})
            print(f"{run_name}: {line.splitlines()[0]}", file=sys.stderr)
            if release_pushes(task_id, ws, log):
                result["outcome"] = "an effect other than a local push is held for Tom"
                break
            state = status(task_id)
            if state["state"] in ("waiting for Tom", "delivered"):
                reply = stand_in(
                    task_id,
                    item["answer_key"],
                    model=args.stand_in_model,
                    max_feedback=args.max_feedback,
                    base=ws["base"],
                )
                log.append({"at": now(), "step": "stand-in", **reply})
                print(
                    f"{run_name}: stand-in {reply['kind']}: {(reply['text'] or reply['reason'] or '')[:200]}",
                    file=sys.stderr,
                )
                if reply["kind"] == "accept":
                    result["outcome"] = "accepted"
                elif reply["kind"] == "cap":
                    result["outcome"] = "feedback rounds used up"
            elif state["state"] == "stopped":
                result["outcome"] = "stopped"
            elif line.startswith("BUDGET EXHAUSTED") or state["remaining_usd_micros"] <= 0:
                result["outcome"] = "budget exhausted"
            elif line.startswith("FAILED"):
                failed += 1
                if failed > MAX_FAILED_RUNS:
                    result["outcome"] = "failed"
            result["wall_seconds"] = round(result["wall_seconds"] + time.monotonic() - started, 1)
            started = time.monotonic()
            _save(result)

        result["ended_at"] = now()
        result["wall_seconds"] = round(result["wall_seconds"] + time.monotonic() - started, 1)
        summarize(result, item, ws)
        _save(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("item")
    parser.add_argument("--arm", required=True, choices=["bare", "clarify"])
    parser.add_argument("--model", default="claude-opus-5-5")
    parser.add_argument("--budget", type=float, default=8.0)
    parser.add_argument("--stand-in-model", default="sonnet")
    parser.add_argument("--max-feedback", type=int, default=2)
    parser.add_argument(
        "--max-output-tokens", type=int, help="per-call output cap for the turn's model calls"
    )
    parser.add_argument("--rebuild", action="store_true", help="delete the run and its result and start over")
    parser.add_argument("--judge", action="store_true", help="judge the result when the run ends")
    parser.add_argument("--judge-model", default="sonnet")
    args = parser.parse_args()
    item = load_item(args.item)
    result = replay(item, args.arm, args)
    if args.judge:
        from judge_replay import judge

        result = judge(result["result_file"], model=args.judge_model)
    shown = {k: result.get(k) for k in ("run", "task_id", "outcome", "feedback_rounds", "spend", "final")}
    shown["questions"] = len(result.get("questions", []))
    shown["diff"] = result.get("diff_shortstat")
    if "judge" in result:
        shown["judge"] = result["judge"].get("scores")
    print(json.dumps(shown, indent=2))


if __name__ == "__main__":
    main()
