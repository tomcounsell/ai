"""A task over several real turns, end to end through the command line, on a
toy repository the kernel provisions (`start --project`): start; the judge
says precise (through the local judgement upstream, the way the emulator
forces an arm); run until Valor asks Tom a question; answer; run through the
plan, the fresh critique session, and the build until Valor builds a
candidate and requests a push; the test runner over the spec's suite
(`true`, so the suite was not run); the review and docs runners' fresh
sessions; approve and release the push and the merge; the
bare origin gets both; every turn's Brief carried the corrections and its
stage.

Every turn runs under the kernel's sandbox profiles, with its own Claude Code
config directory and the gateway supplying the credential.

Live spend: metered by the gateway: Haiku working turns, one Opus
critique, and the review and docs sessions at their seats' models, a
bound not yet measured with those two. Runs only when
`VALOR_LIVE=1`.
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
    pytest.mark.spend(usd=2.00),
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

    def rows(kind: str) -> list[dict]:
        async def read():
            async with await db.connect(dsn) as conn:
                return [r["payload"] for r in await ledger.read(conn, task) if r["type"] == kind]

        return asyncio.run(read())

    assert core("run", task).startswith("QUESTION")
    core("answer", task, "Say exactly: Morning, Tom.")
    # The plan is written, the fresh critique session reads it (sending it
    # back at most as often as the plan's counts allow), and the build runs:
    # the test, review, and docs runners record their verdicts, and the
    # merge is requested.
    assert core("run", task).startswith("DELIVERED")
    state = json.loads(core("status", task))
    assert state["state"] == "merge" and state["merge_effect"]["state"] == "held"
    # The plan and the build stages may each push their own commits; every
    # push is held for Tom, and the merge is held for Tom.
    pending = {e for e, s in state["effects"].items() if s == "pending"}
    held = [r for r in rows("effect.held") if r["effect_id"] in pending]
    assert len(held) == len(pending)
    assert [r["action_type"] for r in held].count("merge") == 1
    candidate = next(r for r in held if r["action_type"] == "merge")["payload"]["head_sha"]
    pushes = [r for r in held if r["action_type"] == "push_branch"]
    assert pushes, "expected at least one push_branch held for Tom"
    assert not rows("effect.outcome"), "nothing leaves before Tom's tap"
    for effect in [r["effect_id"] for r in pushes] + [state["merge_effect"]["effect_id"]]:
        core("approve", effect, "--note", "yes")
        core("release", effect)
    granted = {r["effect_id"] for r in rows("approval.granted")}
    # Every outcome follows a tap, and every tapped effect has its outcome.
    assert {r["effect_id"] for r in rows("effect.outcome")} == granted
    assert sh("git", "rev-parse", "main", cwd=origin) == candidate
    pushed = sh("git", "rev-parse", "valor/greeting", cwd=origin)
    assert pushed == pushes[-1]["payload"]["head_sha"]
    assert "Morning, Tom." in sh("git", "show", f"{pushed}:greeting.txt", cwd=origin)
    # What Tom approved to push is in what was merged.
    sh("git", "merge-base", "--is-ancestor", pushed, candidate, cwd=origin)
    assert json.loads(core("status", task))["state"] == "merged"

    tested = rows("test.decided")[0]
    assert tested["leg"] == "kernel" and tested["command"] == "true" and tested["verdict"] == "pass"
    assert {r["leg"] for r in rows("review.decided")} == {"session"}
    assert {r["leg"] for r in rows("docs.decided")} == {"session"}
    started = rows("turn.started")
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
    assert state["attention_counts"]["approval"]["total"] == len(held)
