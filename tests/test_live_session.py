"""A task over several real turns, end to end through the command line, on a
toy git repository: start; run until Valor asks Tom a question; answer; run
until Valor delivers and requests a push; approve; release; the bare origin
gets the branch; every turn's Brief carried the corrections.

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
from tests.conftest import TEST_DB

pytestmark = [
    pytest.mark.spend(usd=0.25),
    pytest.mark.skipif(os.environ.get("VALOR_LIVE") != "1", reason="live spend needs VALOR_LIVE=1"),
]

ROOT = Path(__file__).resolve().parent.parent
INSTRUCTION = (
    "Create greeting.txt in the workspace holding a one-line greeting for Tom. "
    "Tom has a particular greeting in mind that you cannot infer, so on your first turn "
    "do nothing except ask him which greeting he wants, then end the turn. Once he answers: "
    "write the file, commit it on the current branch, request a push_branch of that commit "
    "to the branch valor/greeting, write done.md, and end the turn."
)


def sh(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def core(*args: str) -> str:
    out = subprocess.run(
        [sys.executable, "-m", "core", *args],
        cwd=ROOT,
        env={**os.environ, "VALOR_DB": TEST_DB},
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
    sh("git", "init", "-q", "-b", "main", str(ws))
    (ws / "README.md").write_text("A toy repository.\n")
    (home / "gitconfig").write_text("[user]\n\tname = Valor Engels\n\temail = valor@yuda.me\n")
    git = ["git", "-c", "user.name=Tom", "-c", "user.email=tom@example.com"]
    sh(*git, "add", "README.md", cwd=ws)
    sh(*git, "commit", "-qm", "base", cwd=ws)
    sh("git", "remote", "add", "origin", str(origin), cwd=ws)
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
    assert core("run", task).startswith("QUESTION")
    core("answer", task, "Say exactly: Morning, Tom.")
    assert core("run", task).startswith("DELIVERED")
    held = [e for e, s in json.loads(core("status", task))["effects"].items() if s == "pending"]
    assert held, "expected a push_branch held for Tom"
    assert sh("git", "branch", "--list", cwd=origin) == ""
    core("approve", held[0], "--note", "yes, push it")
    core("release", held[0])
    pushed = sh("git", "rev-parse", "valor/greeting", cwd=origin)
    assert pushed == sh("git", "rev-parse", "HEAD", cwd=ws)
    assert "Morning, Tom." in sh("git", "show", f"{pushed}:greeting.txt", cwd=origin)

    async def turns():
        async with await db.connect(dsn) as conn:
            return [r["payload"] for r in await ledger.read(conn, task) if r["type"] == "turn.started"]

    started = asyncio.run(turns())
    assert len(started) >= 2
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
    assert state["attention_counts"]["approval"]["total"] == 1
