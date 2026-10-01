"""Judge a finished replay: its final branch against the merged reference
PR and the answer key, by an LLM judge blind to the arm, plus the item's
verification commands run in the workspace under its sandbox.

    .venv/bin/python scripts/judge_replay.py $VALOR_DEMO/results/<run>.json [--model sonnet]

The reference is `gh pr diff <pr>` (read-only) or the item's
`reference_diff` file. The judge sees the request, the answer key, the
reference diff, the candidate's diff against the base, and the verification
output, and nothing that names the arm, the questions asked, or the feedback
given. It scores, 0 to 5 each: fidelity to the human's intended scope,
correctness and tests, and simplicity, and lists the divergences from the
reference. The verdict goes into the result file under `judge` (an earlier
verdict moves to `judge_history`).

Verification commands run on the final commit and nothing else: it is
fetched into a repository of the judge's own ($VALOR_DEMO/judge/<run>.git,
so no git config or hook the turn wrote applies) and exported to a clean
tree at runs/<run>/verify/<name>, where the commands run with the turn's own
sandbox profile, environment, and services. Anything the turn left
uncommitted, a virtualenv included, is not there: an item's commands set up
what they need.

Live spend: one judge call (default Sonnet), logged in $VALOR_DEMO/costs.jsonl.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import replay_workspace
from replay_common import DEMO, claude_json, git, machine_lock, now, sh, ws_git

from harnesses.claude_code import KEEP_ENV

DIFF_LIMIT = 70_000
OUTPUT_TAIL = 4_000
VERIFY_TIMEOUT = 1_800

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


def candidate_diff(result: dict) -> str:
    final, ws = result.get("final"), result["workspace"]
    if not final:
        return ""
    if final["pushed"]:
        return git(ws["origin"], "diff", "--no-ext-diff", "--no-textconv", ws["base"], final["sha"])
    return ws_git(ws["workdir"], "diff", "--no-ext-diff", "--no-textconv", ws["base"], final["sha"])


def export_final(result: dict) -> Path:
    """A clean tree of the final commit inside the run directory."""
    ws, final = result["workspace"], result["final"]
    store = DEMO / "judge" / f"{result['run']}.git"
    if not store.exists():
        sh("git", "init", "--quiet", "--bare", str(store))
    source = ws["origin"] if final["pushed"] else ws["workdir"]
    git(store, "fetch", "--quiet", "--no-tags", source, "+refs/heads/*:refs/remotes/candidate/*")
    git(store, "cat-file", "-e", f"{final['sha']}^{{commit}}")
    tree = Path(ws["run_dir"]) / "verify" / Path(ws["workdir"]).name
    if tree.exists():
        shutil.rmtree(tree)
    tree.mkdir(parents=True)
    archive = subprocess.run(
        ["git", "-C", str(store), "archive", final["sha"]], capture_output=True, check=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(tree)], input=archive, check=True)
    return tree


def verify(result: dict) -> list[dict]:
    item, ws = result["item"], result["workspace"]
    if not item.get("verify") or not result.get("final"):
        return []
    tree = export_final(result)
    harness = json.loads(Path(ws["harness_config"]).read_text())
    env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
    env.update(harness.get("env", {}))
    env.update(
        {
            "GIT_CONFIG_GLOBAL": harness["gitconfig"],
            "GIT_CONFIG_NOSYSTEM": "1",
            "GH_CONFIG_DIR": harness["gh_config_dir"],
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    replay_workspace.ensure_services(item["services"])
    out = []
    for command in item["verify"]:
        argv = [
            "sandbox-exec",
            "-D",
            "GATEWAY_PORT=1",
            "-f",
            harness["sandbox_profile"],
            "/bin/bash",
            "-c",
            command,
        ]
        try:
            ran = subprocess.run(
                argv,
                cwd=tree,
                env=env,
                capture_output=True,
                text=True,
                timeout=VERIFY_TIMEOUT,
                check=False,
            )
            code, text = ran.returncode, (ran.stdout + ran.stderr)
        except subprocess.TimeoutExpired as exc:
            code, text = "timeout", f"{exc.stdout or ''}{exc.stderr or ''}"
            text = text.decode(errors="replace") if isinstance(text, bytes) else text
        out.append({"command": command, "exit": code, "output_tail": text[-OUTPUT_TAIL:]})
    return out


def judge(result_file: str, *, model: str = "sonnet") -> dict:
    with machine_lock(f"judge {Path(result_file).stem}"):
        return _judge(Path(result_file), model)


def _judge(path: Path, model: str) -> dict:
    result = json.loads(path.read_text())
    item, ws = result["item"], result["workspace"]
    head = ws_git(ws["workdir"], "rev-parse", "HEAD", check=False)
    checks = verify(result)
    candidate = candidate_diff(result)
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
    reply = claude_json(prompt, system=SYSTEM, model=model, purpose="judge", subject=result["run"])
    verdict = reply["json"] or {"unparsed": reply["text"]}
    if "judge" in result:
        result.setdefault("judge_history", []).append(result["judge"])
    result["judge"] = {
        "at": now(),
        "model": model,
        "usd": reply["usd"],
        "scores": verdict.get("scores"),
        "divergences": verdict.get("divergences"),
        "rationale": verdict.get("rationale"),
        "verification": checks,
        "workspace_head": head,
        **({"unparsed": verdict["unparsed"]} if "unparsed" in verdict else {}),
    }
    path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("result_file")
    parser.add_argument("--model", default="sonnet")
    args = parser.parse_args()
    result = judge(args.result_file, model=args.model)
    print(json.dumps({k: result["judge"][k] for k in ("scores", "divergences", "usd")}, indent=2))


if __name__ == "__main__":
    main()
