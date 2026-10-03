"""A task over several real turns, end to end through the command line, on a
toy repository the kernel provisions (`start --project`): start; the judge
says precise (through the local judgement upstream, the way the emulator
forces an arm); run until Valor asks Tom a question; answer; run through the
plan, the fresh critique session, and the build until Valor builds a
candidate and requests a push; the three checks by hand; approve and release
the push and the merge; the bare origin gets both; every turn's Brief
carried the corrections and its stage.

Every turn runs under the kernel's sandbox profiles, with its own Claude Code
config directory and the gateway supplying the credential.

Live spend: about $1.00 per run, metered by the gateway: Haiku working
turns and one Opus critique. Runs only when `VALOR_LIVE=1`.
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
    pytest.mark.spend(usd=1.00),
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
    src = root / "src" / "toy"
    sh("git", "init", "-q", "-b", "main", str(src))
    (src / "README.md").write_text("A toy repository.\n")
    git = ["git", "-c", "user.name=Tom", "-c", "user.email=tom@example.com"]
    sh(*git, "add", "README.md", cwd=src)
    sh(*git, "commit", "-qm", "base", cwd=src)
    spec = root / "toy.toml"
    spec.write_text(
        f'name = "toy"\nrepo = "{src}"\nkind = "plain"\nsuite = "true"\ntarget_branch = "main"\n'
        "max_output_tokens = 2048\n"
    )
    task = core(
        "start", INSTRUCTION, "--ceiling", "act", "--project", str(spec),
        "--model", "light",
    )  # fmt: skip
    shown = json.loads(core("workspace", "show", task))
    origin = Path(shown["push_url"])
    who = ["--by", "live test", "--role-played"]
    assert core("run", task).startswith("QUESTION")
    core("answer", task, "Say exactly: Morning, Tom.")
    # The plan is written, the fresh critique session reads it (sending it
    # back at most as often as the plan's counts allow), and the build runs:
    # the candidate waits on its checks.
    assert core("run", task).startswith("NO RUNNER")
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
    working = [t for t in started if not t.get("fresh")]
    assert all("--resume" in t["argv"] for t in working[1:])
    assert all("--resume" not in t["argv"] for t in started if t.get("fresh"))
    state = json.loads(core("status", task))
    assert state["attention_counts"]["question"]["total"] == 1
    assert state["attention_counts"]["approval"]["total"] == 2
