"""A task over several turns, end to end through the command line, on a toy
git repository: start; run until Valor asks Tom a question; answer; run
until Valor delivers and requests a push; approve; release; check the bare
origin got the branch; show that every turn's Brief carried the corrections.

    .venv/bin/python scripts/session_smoke.py

Live spend: two or three Haiku turns, under $0.10. The task commits $0.25
because the gateway reserves each call's worst case before it runs. Every
turn runs under a sandbox-exec profile that keeps the bare origin
unwritable and loopback closed except for the gateway.
"""

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import db, ledger

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
        [sys.executable, "-m", "core", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if out.returncode != 0:
        raise SystemExit(f"python -m core {' '.join(args)} failed:\n{out.stderr}")
    return out.stdout.strip()


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="valor-session-")).resolve()
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
    print(f"toy workspace {ws}, bare origin {origin}")

    task = core(
        "start", INSTRUCTION, "--budget-usd", "0.25", "--ceiling", "act",
        "--workspace", str(ws), "--model", "haiku", "--harness-config", str(home / "harness.json"),
    )  # fmt: skip
    print(f"\n$ python -m core start ...\n{task}")
    first = core("run", task)
    print(f"\n$ python -m core run {task}\n{first}")
    if not first.startswith("QUESTION"):
        raise SystemExit("expected a question from the first run")
    print(f"\n$ python -m core answer {task} ...\n{core('answer', task, 'Say exactly: Morning, Tom.')}")
    second = core("run", task)
    print(f"\n$ python -m core run {task}\n{second}")
    if not second.startswith("DELIVERED"):
        raise SystemExit("expected a delivery from the second run")
    state = json.loads(core("status", task))
    held = [e for e, s in state["effects"].items() if s == "pending"]
    if not held:
        raise SystemExit("expected a push_branch held for Tom")
    print(f"\n$ python -m core pending\n{core('pending')}")
    print(f"origin branches before approval: {sh('git', 'branch', '--list', cwd=origin) or '(none)'}")
    print(
        f"\n$ python -m core approve {held[0]} --note ...\n{core('approve', held[0], '--note', 'yes, push it')}"
    )
    print(f"\n$ python -m core release {held[0]}\n{core('release', held[0])}")
    pushed = sh("git", "rev-parse", "valor/greeting", cwd=origin)
    head = sh("git", "rev-parse", "HEAD", cwd=ws)
    print(f"origin valor/greeting {pushed}; workspace HEAD {head}; match: {pushed == head}")
    print(f"greeting.txt at that commit: {sh('git', 'show', f'{pushed}:greeting.txt', cwd=origin)!r}")

    state = json.loads(core("status", task))
    print(f"\n== attention log\n{json.dumps(state['attention'], indent=2)}")
    print(f"\n== ledger for task {task} ({state['state']})\n{core('ledger', task)}")

    async def turns():
        async with await db.connect() as conn:
            return [r["payload"] for r in await ledger.read(conn, task) if r["type"] == "turn.started"]

    started = asyncio.run(turns())
    print("\n== corrections in each turn's Brief")
    for t in started:
        argv = t["argv"]
        appended = argv[argv.index("--append-system-prompt") + 1]
        print(
            f"turn {t['turn_id']}: corrections {t['corrections']}, "
            f"rendered in the Brief: {'# Corrections from Tom' in t['brief'] and t['brief'] in appended}, "
            f"resume {argv[argv.index('--resume') + 1] if '--resume' in argv else None}"
        )
    print(
        f"\nspent ${state['charged_usd_micros'] / 1_000_000:.4f} of ${state['committed_usd_micros'] / 1_000_000:.2f}"
    )
    if pushed != head or len(started) < 2 or not all(t["corrections"] for t in started):
        raise SystemExit("smoke failed")


if __name__ == "__main__":
    main()
