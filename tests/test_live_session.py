"""A task over several real turns, end to end through the command line, on a
toy git repository: start; the judge says precise (through the local judgement upstream, the
way the emulator forces an arm); run until Valor asks Tom a question; answer; run
until the plan is committed; critique by hand; run until Valor builds a
candidate and requests a push; the three checks by hand; approve and release
the push and the merge; the bare origin gets both; every turn's Brief
carried the corrections and its stage.

Every turn runs under a sandbox-exec profile that keeps the bare origin
unwritable and loopback closed except for the gateway.

Live spend: at most $0.25 per run, the task's committed budget, which the
gateway never lets it pass. Two or three Haiku turns typically cost under
$0.10; the task commits more because the gateway reserves each call's
worst case before it runs. Runs
only when `VALOR_LIVE=1`.
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import db, ledger
from tests import judgement_upstream
from tests.conftest import TEST_DB

pytestmark = [
    pytest.mark.spend(usd=0.25),
    pytest.mark.skipif(os.environ.get("VALOR_LIVE") != "1", reason="live spend needs VALOR_LIVE=1"),
]

ROOT = Path(__file__).resolve().parent.parent
INSTRUCTION = (
    "Create greeting.txt in the workspace holding a one-line greeting for Tom. "
    "Tom has a particular greeting in mind that you cannot infer, so on your first turn "
    "do nothing except ask him which greeting he wants, then end the turn. "
    "When you build: write the file, commit it on the current branch, request a push_branch "
    "of that commit to the branch valor/greeting, and finish the stage as it says."
)


def sh(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def core(*args: str) -> str:
    jev, ow = judgement_upstream.shared().urls(fixed="precise")
    out = subprocess.run(
        [sys.executable, "-m", "core", *args],
        cwd=ROOT,
        env={**os.environ, "VALOR_DB": TEST_DB, "VALOR_JEV_URL": jev, "VALOR_OPEN_WEIGHT_URL": ow},
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, f"python -m core {' '.join(args)} failed:\n{out.stderr}"
    return out.stdout.strip()


def test_a_question_an_answer_a_delivery_and_a_held_push_from_the_command_line(dsn, tmp_path):
    root = tmp_path.resolve()
    ws, origin, home = root / "ws", root / "origin.git", root / "home"
    home.mkdir()
    sh("git", "init", "-q", "--bare", str(origin))
    sh("git", "symbolic-ref", "HEAD", "refs/heads/main", cwd=origin)
    sh("git", "init", "-q", "-b", "main", str(ws))
    (ws / "README.md").write_text("A toy repository.\n")
    (home / "gitconfig").write_text("[user]\n\tname = Valor Engels\n\temail = valor@yuda.me\n")
    git = ["git", "-c", "user.name=Tom", "-c", "user.email=tom@example.com"]
    sh(*git, "add", "README.md", cwd=ws)
    sh(*git, "commit", "-qm", "base", cwd=ws)
    sh("git", "remote", "add", "origin", str(origin), cwd=ws)
    sh("git", "push", "-q", "origin", "HEAD:refs/heads/main", cwd=ws)
    (ws / ".git" / "info" / "exclude").write_text(".valor/\n")
    (home / "sandbox.sb").write_text(
        "(version 1)\n(allow default)\n"
        f'(deny file-write* (subpath "{origin}"))\n'
        '(deny network-outbound (remote ip "localhost:*"))\n'
        '(allow network-outbound (remote ip (string-append "localhost:" (param "GATEWAY_PORT"))))\n'
    )
    (home / "harness.json").write_text(
        json.dumps(
            {
                "sandbox_profile": str(home / "sandbox.sb"),
                "gitconfig": str(home / "gitconfig"),
                "gh_config_dir": str(home),
                "max_output_tokens": 2048,
            }
        )
    )

    task = core(
        "start", INSTRUCTION, "--budget-usd", "0.25", "--ceiling", "act",
        "--workspace", str(ws), "--model", "light", "--harness-config", str(home / "harness.json"),
    )  # fmt: skip
    who = ["--by", "live test", "--role-played"]
    assert core("run", task).startswith("QUESTION")
    core("answer", task, "Say exactly: Morning, Tom.")
    assert core("run", task).startswith("NO RUNNER")  # the plan is written; critique has no runner
    core("verdict", task, "critique", "sound", *who)
    assert core("run", task).startswith("NO RUNNER")  # the candidate waits on its checks
    state = json.loads(core("status", task))
    candidate = state["candidate"]["sha"]
    for stage, verdict in (("test", "pass"), ("review", "pass"), ("docs", "no_change")):
        core("verdict", task, stage, verdict, *who)
    state = json.loads(core("status", task))
    assert state["state"] == "merge" and state["merge_effect"]["state"] == "held"
    held = [e for e, s in state["effects"].items() if s == "pending"]
    assert len(held) == 2, "expected a push_branch and the merge held for Tom"
    for effect in held:
        core("approve", effect, "--note", "yes")
        core("release", effect)
    assert sh("git", "rev-parse", "main", cwd=origin) == candidate
    pushed = sh("git", "rev-parse", "valor/greeting", cwd=origin)
    assert "Morning, Tom." in sh("git", "show", f"{pushed}:greeting.txt", cwd=origin)
    assert json.loads(core("status", task))["state"] == "merged"

    async def turns():
        async with await db.connect(dsn) as conn:
            return [r["payload"] for r in await ledger.read(conn, task) if r["type"] == "turn.started"]

    started = asyncio.run(turns())
    assert len(started) >= 3
    assert "# Stage: plan" in started[0]["brief"]
    for t in started:
        argv = t["argv"]
        assert t["corrections"] and t["corrections"][0] == 1
        assert (
            "# Corrections from Tom" in t["brief"]
            and t["brief"] in argv[argv.index("--append-system-prompt") + 1]
        )
    assert all("--resume" in t["argv"] for t in started[1:])
    state = json.loads(core("status", task))
    assert state["attention_counts"]["question"]["total"] == 1
    assert state["attention_counts"]["approval"]["total"] == 2
