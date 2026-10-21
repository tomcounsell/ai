"""A scripted Valor for the database tests: each turn is a real Python
subprocess that reads its Brief's stage section and plays that stage by
writing `.valor/` files and committing to a real git workspace, the way a
`claude -p` turn would; a chat session's turn, which has no stage, writes
the requests and signals of the next entry of `chat`, and its `/compact`
turn only answers (or fails, with `fail_compact`). A test steers it through `.git/valor-script.json`
(outside the work tree, so it never dirties a candidate), and every turn is
logged to `.git/valor-turns.jsonl`.

Also: a workspace (a work branch at the base, a bare origin whose HEAD names `main`), a
task started on it and judged by the real judge runner against the local
judgement upstream (`tests/judgement_upstream.py`), the router with the
working-session runner, and scripted verdicts: session-leg rows with a
scripted turn id and model, a test's breadth judgement asked of the local
upstream.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from core import broker, db, judgement_sites, ledger, machine, router, session, tasks, verdicts
from core.__main__ import _performers
from core.machine import Check, State
from core.settings import settings
from harnesses import claude_code
from tests import judgement_upstream

SCRIPT = r"""
import json, os, pathlib, re, subprocess, sys
prompt, resume, brief = sys.argv[1], sys.argv[2], sys.argv[3]
g = pathlib.Path(".git")
cfg_path = g / "valor-script.json"
cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
m = re.search(r"^# Stage: (\w+)", brief, re.M)
stage = m.group(1) if m else None
log = g / "valor-turns.jsonl"
n = len(log.read_text().splitlines()) + 1 if log.exists() else 1
port_open = None
if cfg.get("probe_port"):
    import socket
    s = socket.socket()
    s.settimeout(1)
    port_open = s.connect_ex(("127.0.0.1", int(cfg["probe_port"]))) == 0
    s.close()
with log.open("a") as f:
    f.write(json.dumps({"stage": stage, "prompt": prompt, "resume": resume, "brief": brief,
                        "port_open": port_open}) + "\n")
if prompt.startswith("/compact"):  # a chat session's compaction turn: Claude Code's own command
    if cfg.pop("fail_compact", False):
        cfg_path.write_text(json.dumps(cfg))
        sys.exit(1)
    print(json.dumps({"result": "", "session_id": resume, "is_error": False}))
    sys.exit(0)
if cfg.get("sleep_once"):
    import time
    pause = cfg.pop("sleep_once")
    cfg_path.write_text(json.dumps(cfg))
    time.sleep(pause)
if cfg.pop("fail_next", False):
    cfg_path.write_text(json.dumps(cfg))
    sys.exit(1)
if "turns" in cfg:  # the steering lasts this many turns, then the defaults play
    cfg["turns"] -= 1
    cfg_path.write_text(json.dumps(cfg if cfg["turns"] else {}))
v = pathlib.Path(".valor")
(v / "effects").mkdir(parents=True, exist_ok=True)
git = [os.environ["VALOR_GIT"], "-c", "user.name=Valor", "-c", "user.email=valor@example.com"]
if stage is None and cfg.get("chat"):  # a chat session's turn: the next entry's requests
    for name, effect in cfg["chat"].pop(0).items():
        if name.endswith(".md"):  # a signal: compact.md, stop.md
            (v / name).write_text(effect)
        else:
            (v / "effects" / f"{name}.json").write_text(json.dumps(effect))
    cfg_path.write_text(json.dumps(cfg))

def commit(path, text, message):
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    subprocess.run(git + ["add", path], check=True)
    subprocess.run(git + ["commit", "-qm", message], check=True)

def head():
    return subprocess.run([git[0], "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

act = cfg.get(stage, "default")
asks = cfg.get("advise") or []
if asks:  # each entry is one working turn's: `text` for advice.md, `act` for the stage
    entry = asks.pop(0)
    cfg_path.write_text(json.dumps(cfg))
    act = entry.get("act", act)
    if "text" in entry:
        (v / "advice.md").write_text(entry["text"])
if cfg.get("request_merge"):
    (v / "effects" / "merge.json").write_text(json.dumps({"action_type": "merge", "target": "main",
        "payload": {"head_sha": head()}}))
if act == "ask":
    (v / "question.md").write_text(f"A question from {stage}?")
elif act == "stop":
    (v / "stop.md").write_text(cfg.get("note", "The greeting still fails its check; I stopped rather than patch again."))
    if cfg.get("beside"):
        (v / cfg["beside"]).write_text("beside the stop")
elif act == "nothing":
    pass
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
        if act in ("valor_symlink", "valor_verdict"):
            (v / "inputs").mkdir(exist_ok=True)
            if act == "valor_symlink":
                target = pathlib.Path(cfg["target"])
                (v / "inputs" / "request.md").symlink_to(target)
            else:
                (v / "verdict.json").write_text(json.dumps({"verdict": "sound", "findings": []}))
            subprocess.run(git + ["add", "-f", ".valor"], check=True)
            commit("docs/plan.md", f"plan {n}\n", "Plan")
            subprocess.run([git[0], "rm", "-q", "-r", "--cached", ".valor"], check=True)
            for p in (v / "inputs" / "request.md", v / "verdict.json"):
                if p.is_symlink() or p.exists():
                    p.unlink()
            (v / "plan.json").write_text(json.dumps({"path": "docs/plan.md", "stakes": "a toy change",
                **counts, "scope": []}))
            print(json.dumps({"result": "ok", "session_id": resume or "00000000-0000-4000-8000-000000000001", "is_error": False}))
            sys.exit(0)
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
    elif act != "reasons":
        answer = re.search(r"# Tom's answer\n\n(.*)", prompt)
        commit("greeting.txt", (answer.group(1) if answer else f"greeting {n}") + "\n", stage)
    for command in cfg.get("run", []):
        subprocess.run(command, shell=True, check=False)
    if cfg.get("push"):
        (v / "effects" / "push.json").write_text(json.dumps({"action_type": "push_branch",
            "target": cfg["push"], "payload": {"head_sha": head()}}))
    (v / "done.md").write_text(f"{stage} turn {n}: greeting.txt, checked by reading it.")
print(json.dumps({"result": "ok", "session_id": resume or "00000000-0000-4000-8000-000000000001", "is_error": False}))
"""


def git(cwd, *args) -> str:
    return subprocess.run(
        [settings.git_bin, "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def commit(cwd, path: str, text: str, message: str = "change") -> str:
    p = Path(cwd) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    git(cwd, "add", "--force", path)  # the operator's global excludes do not apply
    git(cwd, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", message)
    return git(cwd, "rev-parse", "HEAD")


def workspace(tmp_path: Path) -> tuple[Path, Path]:
    """A work branch at the base commit and a bare origin with `main` at the
    base and HEAD naming `main`."""
    origin, ws = tmp_path / "origin.git", tmp_path / "ws"
    subprocess.run([settings.git_bin, "init", "-q", "--bare", str(origin)], check=True)
    git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    subprocess.run([settings.git_bin, "init", "-q", "-b", "valor/work", str(ws)], check=True)
    commit(ws, "README.md", "a toy repository\n", "base")
    git(ws, "remote", "add", "origin", str(origin))
    git(ws, "push", "-q", "origin", "HEAD:refs/heads/main")
    (ws / ".git" / "info" / "exclude").write_text(".valor/\n")
    return ws, origin


def steer(ws: Path, **cfg) -> None:
    (ws / ".git" / "valor-script.json").write_text(json.dumps(cfg))


def turn_for(prompt, resume, b):
    def build(url, brief, turn_id):
        return claude_code.TurnCommand(
            argv=[sys.executable, "-c", SCRIPT, prompt, resume or "", brief], harness="script",
            env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "VALOR_GIT": settings.git_bin},
            cwd=b.workspace,
            parse=claude_code.parse,
        )  # fmt: skip

    return build


async def working(ctx: router.Context) -> dict:
    return await session.run(
        ctx.gateway, ctx.task_id, turn_for, dsn=ctx.dsn, alive=ctx.alive, performers=ctx.performers
    )


def judge(answer: str = "precise"):
    """The real judge runner, its port pointed at the local upstream
    answering `precise` or `thin` with 0.95."""
    return judgement_sites.judge_runner(judgement_upstream.shared().port(fixed=answer))


RUNNERS = {
    State.JUDGE: judge("precise"),
    **dict.fromkeys((State.CLARIFY, State.PLAN, State.BUILD, State.PATCH), working),
}
SESSION_LEG = {"leg": "session", "turn_id": "scripted-turn", "model": "scripted"}
SCRIPTED_FAILURE = "a scripted test failure"
performers = _performers  # the task's performers, as the composition root builds them


async def route(gateway, task: str, runners=None, dsn: str | None = None) -> dict:
    """`router.run` with the task's own performers."""
    return await router.run(gateway, task, runners or RUNNERS, dsn=dsn, performers=performers)


async def _always() -> bool:
    return True


async def run_judge(dsn: str, task: str, answer: str) -> dict:
    """The judge state's runner, once, answering `precise` or `thin`."""
    return await judge(answer)(router.Context(None, task, dsn, _always))


async def start(dsn: str, ws: Path, judge: str | None = "precise", **kw) -> str:
    """A task on the workspace, judged `precise` or `thin` by the real judge
    runner against the local upstream (None leaves it in `judge`)."""
    where = tasks.resolve_workspace(str(ws), kw.pop("target_branch", None))
    kw = {"instruction": "Write Tom a greeting.", "max_effect_class": "act", **kw}
    b = tasks.Brief(workspace=str(ws), **where, **kw)
    async with await db.connect(dsn) as conn:
        task = await tasks.start(conn, b)
    if judge is not None:
        await run_judge(dsn, task, judge)
    return task


async def critique(dsn: str, task: str, verdict: str = "sound", **kw) -> None:
    async with await db.connect(dsn) as conn:
        await verdicts.record_critique(conn, task, verdict, **{**SESSION_LEG, **kw})


async def judgements(dsn: str, task: str, which: Check, answer: str = "false", **kw) -> dict:
    """The judgement a scripted test verdict names, asked of the local
    upstream with every question answered `answer` (`true` lists every
    gap)."""
    up = judgement_upstream.shared()
    if which is Check.TEST and "breadth" not in kw:
        kw["breadth"] = await judgement_sites.breadth(up.port(fixed=answer), dsn, task)
    return kw


async def check(dsn: str, task: str, which: str, verdict: str, answer: str = "false", **kw):
    """One scripted check verdict on the session leg, its judgements asked
    as `judgements` says (a `red` test with no failures gets one scripted
    failure). Review takes the reviewer's `pass` or `changes`."""
    stage = Check(which)
    kw = await judgements(dsn, task, stage, answer, **kw)
    if stage is Check.TEST and verdict == "red" and not kw.get("failures"):
        kw["failures"] = [SCRIPTED_FAILURE]
    async with await db.connect(dsn) as conn:
        f = await verdicts.record_check(conn, task, stage, verdict, **{**SESSION_LEG, **kw})
        await verdicts.ensure_merge(conn, performers(await tasks.brief(conn, task)), task)
        return f


async def checks(
    dsn: str, task: str, test: str = "pass", review: str = "pass", docs: str = "no_change", **kw
):
    await check(dsn, task, "test", test)
    await check(dsn, task, "review", review)
    return await check(dsn, task, "docs", docs, **kw)


# -- the broker, with the task's own performers ---------------------------------


async def _of(conn, task: str) -> broker.Performers:
    return performers(await tasks.brief(conn, task))


async def request(conn, task: str, action: broker.Action) -> broker.Outcome:
    return await broker.request(conn, await _of(conn, task), task, action)


async def task_of(conn, effect_id: str) -> str:
    """The task an effect is of, from its first ledger row."""
    sql = "SELECT task_id FROM events WHERE type LIKE 'effect.%%' AND payload->>'effect_id' = %s ORDER BY id"
    return (await (await conn.execute(sql + " LIMIT 1", (effect_id,))).fetchone())[0]


async def ensure_merge(conn, task: str) -> broker.Outcome | None:
    return await verdicts.ensure_merge(conn, await _of(conn, task), task)


async def merged(conn, task: str) -> broker.Outcome:
    """Where the task's merge stands: the step that wrote its last verdict
    asked for it at once, so this only asks when nothing has."""
    asked = await ensure_merge(conn, task)
    if asked is not None:
        return asked
    effect = machine.fold(await ledger.read(conn, task)).merge_effect
    return await broker._standing(conn, effect["effect_id"])
