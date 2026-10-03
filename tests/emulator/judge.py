"""Judge a finished replay: its final commit against the merged reference
PR and the answer key, by an LLM judge blind to the arm, plus the item's
verification commands run on that commit under the baseline's profile.

    .venv/bin/python -m tests.emulator.judge $VALOR_DEMO/results/<run>.json

The reference is `gh pr diff <pr>` (read-only) or the item's
`reference_diff` file. The judge sees the request, the answer key, the
reference diff, the candidate's diff against the base, and the verification
output, and nothing that names the arm, the questions asked, or the feedback
given. It scores, 0 to 5 each: fidelity to the human's intended scope,
correctness and tests, and simplicity, and lists the divergences from the
reference. The verdict goes into the result file under `judge` (an earlier
verdict moves to `judge_history`).

The final commit is the result's `final_rev` (the held merge's head, else
the candidate). Everything is read from the task's kernel mirror, a
repository no turn writes, never from the turn's workdir. The candidate's
diff is the whole diff, as the baseline judge's was: nothing a turn chose
(its plan's path included) decides what the judge sees. The result records
the diff's size and whether the judge's truncation cut it.

Verification commands run in a tree of the final commit at
`<task_dir>/checks/verify-<run>/<repo>`, made fresh from the mirror with
`git archive`. `checks/` is written only by the kernel, so no turn can
write the tree. They run under the working turn's profile as the baseline
ran it: the temp directories shared, the verification directory added
read-write, `TMPDIR` inside it, the task's environment and services.
Whatever a command leaves running (a test setup's daemonized redis-server)
is stopped after it and listed under `reaped`. Anything the turn left
uncommitted, a virtualenv included, is not there: an item's commands set
up what they need.

The judge model is `JUDGE_MODEL`, one pinned Sonnet id: the id Claude
Code 2.1.286's model catalog, the version the baseline ran, resolves the
alias `sonnet` to for the first-party provider. The baseline judge called that alias
(5d90b4776:scripts/judge_replay.py:171) and did not record what it resolved
to. Its one call goes through the kernel's gateway and is metered on the
run's emulator task.
"""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

from core import ledger, runs
from core import workspace as kws
from harnesses.claude_code import KEEP_ENV
from tests.emulator.common import DEMO, Meter, claude_json, machine_lock, mirror_diff, now, sh

# Claude Code 2.1.286's catalog: alias `sonnet` -> claude-sonnet-5-5 (first party).
JUDGE_MODEL = "claude-sonnet-5-5"
DIFF_LIMIT = 70_000  # the baseline's value, kept so scores compare (5d90b4776:scripts/judge_replay.py:45)
OUTPUT_TAIL = 4_000  # the baseline's value, kept so scores compare (5d90b4776:scripts/judge_replay.py:46)
VERIFY_TIMEOUT = 1_800  # the baseline's value, kept so scores compare (5d90b4776:scripts/judge_replay.py:47)

SYSTEM = """You are judging one implementation of a software request against what the person \
who asked actually wanted. You get the request as given, an answer key with the requester's \
recorded intent, the reference implementation the team merged, the candidate's diff, and the \
output of verification commands run on the candidate.

Score the candidate from 0 to 5 on each of:
- fidelity: does it build what the requester intended, at the intended scope (the answer key \
is the authority on intent; the reference shows one accepted realization of it)? Missing \
intended behavior and unrequested additions both cost.
- correctness: does it work, including edge cases the intent implies, and is it tested? Use the \
verification output where there is any; a failing or missing test counts against it.
- simplicity: is it as small and clear as the job allows, fitting the codebase's existing \
patterns?

Differences from the reference that the answer key leaves open are not faults. List the \
divergences that matter, most important first.

Reply with one JSON object and nothing else:
{"scores": {"fidelity": 0-5, "correctness": 0-5, "simplicity": 0-5},
 "divergences": ["..."],
 "rationale": {"fidelity": "...", "correctness": "...", "simplicity": "..."}}"""


def _clip(text: str, limit: int = DIFF_LIMIT) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n... (truncated at {limit} characters)"


def reference_diff(item: dict) -> str:
    if item.get("reference_diff"):
        return Path(item["reference_diff"]).read_text()
    if item.get("pr"):
        return sh("gh", "pr", "diff", str(item["pr"]), "--repo", item["repo"])
    return ""


def candidate_diff(result: dict) -> tuple[str, dict]:
    """The final commit's whole diff against the base, from the mirror,
    and what the result records of it."""
    ws, rev = result["workspace"], result.get("final_rev")
    diff = mirror_diff(ws["mirror"], ws["base"], rev) if rev else ""
    return diff, {
        "chars": len(diff),
        "lines": diff.count("\n") + 1 if diff else 0,
        "truncated": len(diff) > DIFF_LIMIT,
    }


def export_final(result: dict) -> tuple[Path, Path]:
    """A fresh verification directory under the task's `checks/`, holding a
    tree of the final commit from the mirror and a `tmp`. Returns the
    directory and the tree."""
    ws = result["workspace"]
    lay = kws.Layout(Path(ws["task_dir"]))
    root = lay.checks / f"verify-{result['run']}"
    if root.is_symlink():
        root.unlink()
    elif root.exists():
        shutil.rmtree(root)
    tree = root / Path(ws["workdir"]).name
    tree.mkdir(parents=True)
    (root / "tmp").mkdir()
    archive = subprocess.run(
        ["git", "-C", ws["mirror"], "archive", result["final_rev"]], capture_output=True, check=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(tree)], input=archive, check=True)
    return root, tree


def verify_profile(lay: kws.Layout, ports: list[int], root: Path) -> str:
    """The baseline's verification profile: the working turn's, with the
    temp directories shared and `root` added read-write."""
    return kws.turn_profile(lay, ports, tmp=True, rw=[root])


def verify(result: dict) -> list[dict]:
    item, ws = result["item"], result["workspace"]
    if not item.get("verify") or not result.get("final_rev"):
        return []
    root, tree = export_final(result)
    lay = kws.Layout(Path(ws["task_dir"]))
    project = ws.get("project") or {}
    services, ports = project.get("services") or [], project.get("ports") or {}
    profile = lay.checks / f"verify-{result['run']}.sb"
    profile.write_text(verify_profile(lay, [ports[s] for s in services], root))
    harness = json.loads(Path(ws["harness_config"]).read_text())
    env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
    env.update(harness.get("env", {}))
    env.update(
        {
            "GIT_CONFIG_GLOBAL": harness["gitconfig"],
            "GIT_CONFIG_NOSYSTEM": "1",
            "GH_CONFIG_DIR": harness["gh_config_dir"],
            "GIT_TERMINAL_PROMPT": "0",
            "TMPDIR": str(root / "tmp"),
        }
    )
    kws.start_services(ws["task_id"], lay, services, ports)
    out = []
    try:
        for command in item["verify"]:
            mark = ledger.new_id()
            argv = [
                "/usr/bin/sandbox-exec",
                "-D",
                "GATEWAY_PORT=1",
                "-D",
                f"VALOR_TURN={mark}",
                "-f",
                str(profile),
                "/bin/bash",
                "-c",
                command,
            ]
            try:
                ran = subprocess.run(
                    argv,
                    cwd=tree,
                    env={**env, runs.TURN_ENV: mark},
                    capture_output=True,
                    text=True,
                    timeout=VERIFY_TIMEOUT,
                    check=False,
                )
                code, text = ran.returncode, (ran.stdout + ran.stderr)
            except subprocess.TimeoutExpired as exc:
                code, text = "timeout", f"{exc.stdout or ''}{exc.stderr or ''}"
                text = text.decode(errors="replace") if isinstance(text, bytes) else text
            reaped = runs.reap(mark)
            out.append(
                {"command": command, "exit": code, "output_tail": text[-OUTPUT_TAIL:], "reaped": reaped}
            )
    finally:
        kws.stop_services(ws["task_id"], lay)
    return out


def judge(result_file: str, *, meter: Meter | None = None, model: str = JUDGE_MODEL) -> dict:
    """Judge the run. `meter` is the driver's, when it judges at the end of
    a run; otherwise the judge meters on the result's emulator task itself."""
    path = Path(result_file)
    with machine_lock(f"judge {path.stem}"):
        if meter is not None:
            return _judge(path, model, meter)
        result = json.loads(path.read_text())
        with Meter(result["emulator_task"]) as own:
            return _judge(path, model, own)


def _judge(path: Path, model: str, meter: Meter) -> dict:
    result = json.loads(path.read_text())
    item = result["item"]
    checks = verify(result)
    candidate, diff = candidate_diff(result)
    verification = (
        "\n\n".join(f"$ {c['command']}\n(exit {c['exit']})\n{c['output_tail']}" for c in checks)
        or "(no verification commands ran)"
    )
    prompt = "\n\n".join(
        [
            f"# The request\n\n{item['request']}",
            f"# Answer key: the requester's intent\n\n{Path(item['answer_key']).read_text()}",
            f"# Reference implementation (merged)\n\n```diff\n{_clip(reference_diff(item)) or '(none)'}\n```",
            f"# Candidate implementation\n\n```diff\n{_clip(candidate) or '(the candidate changed nothing)'}\n```",
            f"# Verification of the candidate\n\n{verification}",
        ]
    )
    workdir = Path(result.get("workspace", {}).get("run_dir") or DEMO)
    reply = claude_json(prompt, system=SYSTEM, model=model, meter=meter, call_id="judge", workdir=workdir)
    verdict = reply["json"] or {"unparsed": reply["text"]}
    if "judge" in result:
        result.setdefault("judge_history", []).append(result["judge"])
    result["judge_model"] = model
    result["judge"] = {
        "at": now(),
        "model": model,
        "rev": result.get("final_rev"),
        "diff": diff,
        "scores": verdict.get("scores"),
        "divergences": verdict.get("divergences"),
        "rationale": verdict.get("rationale"),
        "verification": checks,
        **({"unparsed": verdict["unparsed"]} if "unparsed" in verdict else {}),
    }
    spent = meter.spend()
    result["emulator_spend_usd"] = spent["usd"]
    result["open_calls"] = spent["open_calls"]
    path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("result_file")
    parser.add_argument("--model", default=JUDGE_MODEL, help="a full model id, for a deliberate comparison")
    args = parser.parse_args()
    result = judge(args.result_file, model=args.model)
    print(json.dumps({k: result["judge"][k] for k in ("scores", "divergences", "diff")}, indent=2))


if __name__ == "__main__":
    main()
