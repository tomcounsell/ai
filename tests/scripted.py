"""A scripted Valor for the database tests: each turn is a real Python
subprocess that reads its Brief's stage section and plays that stage by
writing `.valor/` files and committing to a real git workspace, the way a
`claude -p` turn would. A test steers it through `.git/valor-script.json`
(outside the work tree, so it never dirties a candidate), and every turn is
logged to `.git/valor-turns.jsonl`.

Also: a workspace laid out the way `scripts/replay_workspace.py` lays one
out (a work branch at the base, a bare origin whose HEAD names `main`), a
task started on it and judged by the real judge runner against the local
judgement upstream (`tests/judgement_upstream.py`), the router with the
working-session runner, and manual verdicts.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from core import broker, db, judgement_sites, router, session, tasks, verdicts
from core.machine import Check, State
from harnesses import claude_code
from tests import judgement_upstream
from tools.push_branch import Merge, PushBranch

SCRIPT = r"""
import json, pathlib, re, subprocess, sys
prompt, resume, brief = sys.argv[1], sys.argv[2], sys.argv[3]
g = pathlib.Path(".git")
cfg_path = g / "valor-script.json"
cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
m = re.search(r"^# Stage: (\w+)", brief, re.M)
stage = m.group(1) if m else None
log = g / "valor-turns.jsonl"
n = len(log.read_text().splitlines()) + 1 if log.exists() else 1
with log.open("a") as f:
    f.write(json.dumps({"stage": stage, "prompt": prompt, "resume": resume, "brief": brief}) + "\n")
if cfg.pop("fail_next", False):
    cfg_path.write_text(json.dumps(cfg))
    sys.exit(1)
v = pathlib.Path(".valor")
(v / "effects").mkdir(parents=True, exist_ok=True)
git = ["git", "-c", "user.name=Valor", "-c", "user.email=valor@example.com"]

def commit(path, text, message):
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    subprocess.run(git + ["add", path], check=True)
    subprocess.run(git + ["commit", "-qm", message], check=True)

def head():
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

act = cfg.get(stage, "default")
if cfg.get("request_merge"):
    (v / "effects" / "merge.json").write_text(json.dumps({"action_type": "merge", "target": "main",
        "payload": {"head_sha": head()}}))
if act == "ask":
    (v / "question.md").write_text(f"A question from {stage}?")
elif stage == "clarify":
    if "# Tom's answer" in prompt or act == "proceed":
        (v / "no_question.md").write_text("Nothing material is open; I will write the greeting.")
    else:
        (v / "question.md").write_text("Which greeting do you want?")
elif stage == "plan":
    counts = cfg.get("counts", {"critique_rounds": 0, "review_rounds": 1})
    if act == "done":
        (v / "done.md").write_text("A delivery during the plan.")
    else:
        if act == "uncommitted":
            pathlib.Path("docs").mkdir(exist_ok=True)
            pathlib.Path("docs/plan.md").write_text(f"plan {n}\n")
        else:
            commit("docs/plan.md", f"plan {n}\n", "Plan")
        (v / "plan.json").write_text(json.dumps({"path": "docs/plan.md", "stakes": "a toy change",
            **counts, "scope": []}))
elif stage in ("build", "patch"):
    if act == "dirty":
        pathlib.Path("greeting.txt").write_text(f"uncommitted {n}\n")
    elif act == "nothing":
        pass
    elif act != "reasons":
        answer = re.search(r"# Tom's answer\n\n(.*)", prompt)
        commit("greeting.txt", (answer.group(1) if answer else f"greeting {n}") + "\n", stage)
    if cfg.get("push") and act != "nothing":
        (v / "effects" / "push.json").write_text(json.dumps({"action_type": "push_branch",
            "target": cfg["push"], "payload": {"head_sha": head()}}))
    if act != "nothing":
        (v / "done.md").write_text(f"{stage} turn {n}: greeting.txt, checked by reading it.")
print(json.dumps({"result": "ok", "session_id": resume or "session-1", "is_error": False}))
"""


def git(cwd, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def commit(cwd, path: str, text: str, message: str = "change") -> str:
    p = Path(cwd) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    git(cwd, "add", path)
    git(cwd, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", message)
    return git(cwd, "rev-parse", "HEAD")


def workspace(tmp_path: Path) -> tuple[Path, Path]:
    """A work branch at the base commit and a bare origin with `main` at the
    base and HEAD naming `main`, as `scripts/replay_workspace.py` makes."""
    origin, ws = tmp_path / "origin.git", tmp_path / "ws"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    subprocess.run(["git", "init", "-q", "-b", "valor/work", str(ws)], check=True)
    commit(ws, "README.md", "a toy repository\n", "base")
    git(ws, "remote", "add", "origin", str(origin))
    git(ws, "push", "-q", "origin", "HEAD:refs/heads/main")
    (ws / ".git" / "info" / "exclude").write_text(".valor/\n")
    return ws, origin


def steer(ws: Path, **cfg) -> None:
    (ws / ".git" / "valor-script.json").write_text(json.dumps(cfg))


def turns(ws: Path) -> list[dict]:
    log = ws / ".git" / "valor-turns.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def turn_for(prompt, resume, b):
    def build(url, brief, turn_id):
        return claude_code.TurnCommand(
            argv=[sys.executable, "-c", SCRIPT, prompt, resume or "", brief],
            env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]},
            cwd=b.workspace,
            harness="script",
            parse=claude_code.parse,
        )

    return build


async def working(ctx: router.Context) -> dict:
    return await session.run(ctx.gateway, ctx.task_id, turn_for, dsn=ctx.dsn, alive=ctx.alive)


def judge(answer: str = "precise"):
    """The real judge runner, its port pointed at the local upstream
    answering `precise` or `thin` with 0.95."""
    return judgement_sites.judge_runner(judgement_upstream.shared().port(fixed=answer))


RUNNERS = {
    State.JUDGE: judge("precise"),
    State.CLARIFY: working,
    State.PLAN: working,
    State.BUILD: working,
    State.PATCH: working,
}
MANUAL = {"by": "test", "via": "the test suite", "role_played": True}


def performers(b: tasks.Brief) -> None:
    broker.register(PushBranch(b.workspace, url=b.origin_url, protected=b.target_branch))
    broker.register(Merge(b.workspace))


async def _always() -> bool:
    return True


async def run_judge(dsn: str, task: str, answer: str) -> dict:
    """The judge state's runner, once, answering `precise` or `thin`."""
    return await judge(answer)(router.Context(None, task, dsn, _always))


async def start(dsn: str, ws: Path, judge: str | None = "precise", **kw) -> str:
    """A task on the workspace, judged `precise` or `thin` by the real judge
    runner against the local upstream (None leaves it in `judge`)."""
    where = tasks.resolve_workspace(str(ws), kw.pop("target_branch", None))
    b = tasks.Brief(
        instruction=kw.pop("instruction", "Write Tom a greeting."),
        budget_usd_micros=kw.pop("budget_usd_micros", 1_000),
        max_effect_class=kw.pop("max_effect_class", "act"),
        workspace=str(ws),
        **where,
        **kw,
    )
    performers(b)
    async with await db.connect(dsn) as conn:
        task = await tasks.start(conn, b)
    if judge is not None:
        await run_judge(dsn, task, judge)
    return task


async def status(dsn: str, task: str) -> dict:
    async with await db.connect(dsn) as conn:
        return await tasks.status(conn, task)


async def critique(dsn: str, task: str, verdict: str = "sound", **kw) -> None:
    async with await db.connect(dsn) as conn:
        await verdicts.record_critique(conn, task, verdict, **MANUAL, **kw)


async def check(dsn: str, task: str, which: str, verdict: str, **kw):
    async with await db.connect(dsn) as conn:
        f = await verdicts.record_check(conn, task, Check(which), verdict, **MANUAL, **kw)
        await verdicts.ensure_merge(conn, task)
        return f


async def checks(
    dsn: str, task: str, test: str = "pass", review: str = "pass", docs: str = "no_change", **kw
):
    await check(dsn, task, "test", test)
    await check(dsn, task, "review", review, governance=kw.pop("governance", ()))
    return await check(dsn, task, "docs", docs, **kw)
