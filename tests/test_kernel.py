"""The ledger, effects, stop, and the binding of Tom's own chat and of team
chats to their chat sessions, on real Postgres, with no model call."""

import asyncio
import dataclasses
import json
import os
import pathlib
import subprocess
import sys
import tomllib
import uuid
from types import SimpleNamespace

import aiohttp
import psycopg
import pytest

from bridges.slack import inbound as slack_inbound
from core import (
    broker,
    checks,
    corrections,
    db,
    git,
    intake,
    ledger,
    machine,
    notices,
    router,
    runs,
    session,
    signals,
    slot,
    tasks,
    workspace,
)
from core.machine import State
from core.settings import settings
from tests import bridges, scripted
from tests.performers import OutboxAppend, WorkspaceWrite
from tests.test_machine import Ledger

REPO = pathlib.Path(__file__).resolve().parent.parent


def audit(state) -> list[str]:
    """What a lossless stop must leave true. Empty means consistent: every
    gateway call charged, every turn ended, and no effect caught between
    intent and outcome."""
    problems = []
    for call_id in state["open_calls"]:
        problems.append(f"gateway call {call_id} opened and never charged")
    for turn_id, outcome in state["turns"].items():
        if outcome is None:
            problems.append(f"turn {turn_id} started and never ended")
    for effect_id, effect_state in state["effects"].items():
        if effect_state == "in_flight":
            problems.append(f"effect {effect_id} has an intent and no outcome")
    return problems


pytestmark = pytest.mark.spend(usd=0)


run = asyncio.run


async def new_task(dsn, **kw) -> str:
    async with await db.connect(dsn) as conn:
        return await tasks.start(conn, tasks.Brief(instruction="test", **kw))


# -- the ledger cannot be edited ------------------------------------------------


def test_kernel_role_cannot_edit_the_ledger(dsn):
    task = run(new_task(dsn))
    with psycopg.connect(dsn, autocommit=True) as conn:
        for statement in (
            "UPDATE events SET type = 'x' WHERE task_id = %s",
            "DELETE FROM events WHERE task_id = %s",
            "TRUNCATE events",
            "UPDATE documents SET body = '{}' WHERE id = %s",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement, (task,) * statement.count("%s"))


# -- spending is metered; only a stop refuses a call ------------------------------


def test_a_stopped_tasks_call_is_refused_with_a_ledger_row_before_any_provider_call(dsn):
    async def go():
        task = await new_task(dsn)
        gateway = await bridges.gateway(dsn, upstream="http://127.0.0.1:9")  # never reached
        base = gateway.issue(task, "turn-1")
        body = {"model": "claude-haiku-4-5", "max_tokens": 1000}
        body["messages"] = [{"role": "user", "content": "hi"}]
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, task, reason="test")
        async with aiohttp.ClientSession() as http, http.post(base + "/v1/messages", json=body) as r:
            refused = (r.status, await r.json())
        await gateway.close()
        async with await db.connect(dsn) as conn:
            return refused, await ledger.read(conn, task), await tasks.status(conn, task)

    (status, error), rows, state = run(go())
    assert status == 400 and "stopped" in error["error"]["message"]
    (row,) = [r for r in rows if r["type"].startswith("gateway.")]
    assert row["type"] == "gateway.refused" and row["payload"]["reason"] == "stopped"
    assert row["payload"]["turn_id"] == "turn-1" and row["payload"]["model"] == "claude-haiku-4-5"
    assert state["spent_usd_micros"] == 0 and not state["open_calls"]


def test_a_child_ceiling_above_its_parents_is_refused_and_writes_nothing(dsn):
    async def go():
        parent = await new_task(dsn, max_effect_class="propose")
        async with await db.connect(dsn) as conn:
            started = lambda: conn.execute("SELECT count(*) FROM events WHERE type = 'task.started'")
            (before,) = await (await started()).fetchone()
            with pytest.raises(tasks.CeilingRefused):
                await tasks.start_child(conn, parent, ceiling="act", instruction="too high")
            (after,) = await (await started()).fetchone()
            child = await tasks.start_child(conn, parent, instruction="inherits")
            return before, after, await tasks.brief(conn, child)

    before, after, child = run(go())
    assert after == before
    assert child.max_effect_class == "propose"


def test_a_stop_fences_the_grandchild_and_refuses_a_new_child(dsn):
    async def go():
        root = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            child = await tasks.start_child(conn, root, instruction="child")
            grandchild = await tasks.start_child(conn, child, instruction="grandchild")
            await tasks.stop(conn, root, reason="test")
            fence = await tasks.fenced_by(conn, grandchild)
            with pytest.raises(tasks.TaskStopped):
                await tasks.start_child(conn, grandchild, instruction="after the stop")
        gateway = await bridges.gateway(dsn, upstream="http://127.0.0.1:9")  # never reached
        body = {"model": "claude-haiku-4-5", "max_tokens": 1000}
        body["messages"] = [{"role": "user", "content": "hi"}]
        url = gateway.issue(grandchild, "turn-1") + "/v1/messages"
        async with aiohttp.ClientSession() as http, http.post(url, json=body) as r:
            refused = r.status, await r.json()
        await gateway.close()
        return grandchild, fence, refused

    grandchild, fence, (status, error) = run(go())
    assert fence == grandchild
    assert status == 400 and "stopped" in error["error"]["message"]


# -- effects: one request, one effect -------------------------------------------


def test_act_is_performed_at_request_and_one_request_id_is_one_effect(dsn, tmp_path):
    perf = broker.Performers(WorkspaceWrite(tmp_path), OutboxAppend(tmp_path / "outbox.jsonl"))

    async def go():
        task = await new_task(dsn, max_effect_class="act")
        async with await db.connect(dsn) as conn:
            write = broker.Action("workspace_write", "a.txt", {"text": "a"})
            wrote = await broker.request(conn, perf, task, write)
            send = broker.Action("outbox_send", "tom", {"text": "hi"})
            held = await broker.request(conn, perf, task, send)
            twin = await broker.request(conn, perf, task, send)
            once = await broker.request(conn, perf, task, send, request_id="t1/a.json")
            again = await broker.request(conn, perf, task, send, request_id="t1/a.json")
            state = await tasks.status(conn, task)
        return wrote, held, twin, once, again, state

    wrote, held, twin, once, again, state = run(go())
    assert wrote.kind == "done" and (tmp_path / "a.txt").read_text() == "a"
    # Two identical requests are two effects; one request_id is one.
    assert held.kind == "done" and twin.kind == "done" and twin.effect_id != held.effect_id
    assert again.effect_id == once.effect_id and again.kind == "done"
    assert len((tmp_path / "outbox.jsonl").read_text().splitlines()) == 3
    assert audit(state) == []


# -- stop is immediate and lossless ---------------------------------------------


def test_stop_from_another_connection_kills_the_turn_and_leaves_a_consistent_ledger(dsn, tmp_path):
    async def go():
        task = await new_task(dsn)
        gateway = await bridges.gateway(dsn)
        # A real subprocess that would run for a minute, with a child of its own.
        script = "import subprocess,time; subprocess.Popen(['sleep','60']); time.sleep(60)"
        build = lambda url, brief, turn_id: runs.TurnCommand(
            argv=[sys.executable, "-c", script], env={}, cwd=str(tmp_path), harness="sleeper",
            tmpdir=str(tmp_path / "tmp"),
        )  # fmt: skip
        turn = asyncio.create_task(runs.run_turn(gateway, task, build, dsn=dsn))
        await asyncio.sleep(1.0)
        started = asyncio.get_running_loop().time()
        async with await db.connect(dsn) as conn:
            assert await tasks.stop(conn, task, reason="test") is True
            assert await tasks.stop(conn, task, reason="twice") is False
        ended = await turn
        elapsed = asyncio.get_running_loop().time() - started
        await gateway.close()
        async with await db.connect(dsn) as conn:
            state = await tasks.status(conn, task)
            with pytest.raises(tasks.TaskStopped):
                await runs.run_turn(gateway, task, build, dsn=dsn)
        return ended, elapsed, state

    ended, elapsed, state = run(go())
    assert ended["outcome"] == "stopped" and ended["returncode"] == -9
    assert elapsed < 20  # far short of the turn's minute, with room for a loaded Mac (3.8 s seen)
    assert not (tmp_path / "tmp").exists()
    assert state["state"] == "stopped" and audit(state) == []


def test_stop_heard_ends_on_this_tasks_stop_only(dsn):
    async def go():
        task, other = await new_task(dsn), await new_task(dsn)
        ctx = SimpleNamespace(dsn=dsn, task_id=task)
        async with checks.stop_heard(ctx) as stop:
            async with await db.connect(dsn) as conn:
                await tasks.stop(conn, other, reason="test")
            await asyncio.sleep(0.5)
            assert not stop.done()
            async with await db.connect(dsn) as conn:
                await tasks.stop(conn, task, reason="test")
            await asyncio.wait_for(stop, 5)

    run(go())


# A turn's scratch in a review session: its TMPDIR was emptied before it, its
# passfile was not; a nested tree, a read-only file, a locked file, a link out.
SCRATCH = r"""
import os, pathlib, sys
t = pathlib.Path(os.environ["TMPDIR"])
assert list(t.iterdir()) == [] and pathlib.Path(os.environ["PGPASSFILE"]).exists()
(t / "a" / "b").mkdir(parents=True)
(t / "a" / "b" / "big").write_bytes(b"x" * 4096)
(t / "ro").write_text("r"); (t / "ro").chmod(0o400); (t / "a").chmod(0o500)
(t / "locked").write_text("l"); os.chflags(t / "locked", 2)  # UF_IMMUTABLE
(t / "out").symlink_to(sys.argv[1])
sys.exit(int(sys.argv[2]))
"""


@pytest.mark.parametrize("code,outcome", [(0, "done"), (3, "failed")])
def test_a_turns_tmpdir_is_emptied_before_it_and_removed_after_it(dsn, tmp_path, monkeypatch, code, outcome):
    from core import workspace

    def init(lay, task_id, spec, port):  # a turn cannot nest sandbox-exec to start a real cluster
        lay.home.mkdir(parents=True, exist_ok=True)
        (lay.home / "pgpass").write_text(f"127.0.0.1:{port}:*:app:pw\n")
        return {"app": "pw"}

    for name, stand_in in (("_init_postgres", init), ("start_services", print), ("stop_services", print)):
        monkeypatch.setattr(workspace, name, stand_in)
    lay, outside = workspace.Layout(tmp_path / "t"), tmp_path / "keep.txt"
    check_dir = workspace.fresh_dir(lay.checks / "review-x")
    (check_dir / "tmp" / "dead-turn").mkdir()
    outside.write_text("mine")

    async def go(harness):
        task = await new_task(dsn)
        gateway = await bridges.gateway(dsn)
        build = lambda url, brief, turn_id: runs.TurnCommand(
            argv=[sys.executable, "-c", SCRATCH, str(outside), str(code)], cwd=str(tmp_path),
            env={**harness["env"], "TMPDIR": harness["tmpdir"]}, harness="scratch", tmpdir=harness["tmpdir"],
        )  # fmt: skip
        try:
            return await runs.run_turn(gateway, task, build, dsn=dsn)
        finally:
            await gateway.close()

    with workspace.check_services(
        lay, check_dir, {"services": ["postgres"], "ports": {"postgres": 1}}, "t"
    ) as env:
        ended = run(go(workspace.check_harness(lay, check_dir, [], env, services=True)))
        assert ended["outcome"] == outcome and ended["returncode"] == code
        assert not (check_dir / "tmp").exists() and pathlib.Path(env["PGPASSFILE"]).exists()
    assert outside.read_text() == "mine" and (check_dir / "claude").is_dir()


def test_a_harness_turns_tmpdir_is_the_one_its_settings_name(tmp_path):
    from core import __main__, workspace
    from harnesses import claude_code, codex, pi

    harness = workspace.check_harness(workspace.Layout(tmp_path), workspace.fresh_dir(tmp_path / "c"), [], {})
    assert harness["tmpdir"] == str(tmp_path / "c" / "tmp")
    made = [
        m.workspace_turn("p", cwd=".", harness=harness)("http://h:1", "b", "t")
        for m in (claude_code, pi, codex)
    ]
    assert {c.tmpdir for c in made} | {c.env["TMPDIR"] for c in made} == {
        harness["tmpdir"],
        made[0].env["CLAUDE_CODE_TMPDIR"],
    }
    # A task provisioned before the harness named a TMPDIR gets its own; none named, none set.
    bare = {"sandbox_profile": harness["sandbox_profile"]}
    (tmp_path / "bin").mkdir()
    old = tasks.Brief(
        instruction="x", mirror=str(tmp_path / "t" / "kernel.git"), harness=bare, harness_name="claude_code"
    )
    assert __main__._turn_for("p", None, old)("http://h:1", "b", "t").tmpdir == str(tmp_path / "t" / "tmp")
    assert "tmpdir" not in bare
    assert claude_code.workspace_turn("p", cwd=".", harness=bare)("http://h:1", "b", "t").tmpdir is None


def test_a_turn_loads_the_operators_own_mcp_servers_and_the_plugins_skills(tmp_path, monkeypatch):
    from core import workspace
    from harnesses import claude_code, pi

    harness = workspace.check_harness(workspace.Layout(tmp_path), workspace.fresh_dir(tmp_path / "c"), [], {})
    servers = {"byob": {"type": "stdio", "command": "byob-mcp"}}
    (tmp_path / ".claude.json").write_text(json.dumps({"mcpServers": servers}))
    (tmp_path / "plugin" / "skills").mkdir(parents=True)
    replaced = dataclasses.replace(
        settings,
        claude_json=str(tmp_path / ".claude.json"),
        plugin_dirs=(str(tmp_path / "plugin"), str(tmp_path / "skill-less")),
    )
    monkeypatch.setattr(claude_code, "settings", replaced)
    monkeypatch.setattr(pi, "settings", replaced)
    argv = claude_code.workspace_turn("p", cwd=".", harness=harness)("http://h:1", "b", "t").argv
    assert json.loads(argv[argv.index("--mcp-config") + 1]) == {"mcpServers": servers}
    # Pi loads the same servers from its agent directory, the project's context files and
    # (trusted) skills, and each plugin's skills.
    argv = pi.workspace_turn("p", cwd=".", harness=harness)("http://h:1", "b", "t").argv
    mcp = json.loads((pathlib.Path(harness["pi_agent_dir"]) / "mcp.json").read_text())
    assert mcp == {"mcpServers": servers} and "builtin:mcp" in argv and "--approve" in argv
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--skill"] == [
        str(tmp_path / "plugin" / "skills")
    ]
    assert not {"--no-skills", "--no-context-files"} & set(argv)


# What `codex exec --json` printed for one turn against a stub upstream (codex-cli 0.153.4).
CODEX_TURN = b"""\
{"type":"thread.started","thread_id":"01a14501-8d3f-7e43-ac99-8359ba467614"}
{"type":"item.completed","item":{"id":"item_0","type":"error","message":"Model metadata for `gpt-6.1-sol` not found."}}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"stub reply 1"}}
{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":0,"output_tokens":5}}
"""


def test_a_codex_turn_runs_on_the_gateway_with_the_brief_skills_and_mcp_servers(tmp_path, monkeypatch):
    import tomllib

    from core import transcripts, workspace
    from core.gateway import TURN_TOKEN
    from harnesses import claude_code, codex

    harness = workspace.check_harness(workspace.Layout(tmp_path), workspace.fresh_dir(tmp_path / "c"), [], {})
    servers = {"byob": {"type": "stdio", "command": "byob-mcp", "args": ["--x"]}, "web": {"url": "https://m"}}
    (tmp_path / ".claude.json").write_text(json.dumps({"mcpServers": servers}))
    (tmp_path / "yudame" / "skills").mkdir(parents=True)
    replaced = dataclasses.replace(settings, claude_json=str(tmp_path / ".claude.json"),
                                   plugin_dirs=(str(tmp_path / "yudame"), str(tmp_path / "skill-less")))  # fmt: skip
    monkeypatch.setattr(codex, "settings", replaced)
    monkeypatch.setattr(claude_code, "settings", replaced)
    brief = 'Brief "one"\n\U0001f600 \x7f'
    made = codex.workspace_turn("-p", cwd=".", harness=harness)("http://h:1/t/k", brief, "t")
    home = pathlib.Path(harness["codex_home"])
    config = tomllib.loads((home / "config.toml").read_text())
    assert config["model_providers"]["valor"]["base_url"] == "http://h:1/t/k/openai/v1"
    assert made.env[config["model_providers"]["valor"]["env_key"]] == TURN_TOKEN
    assert config["developer_instructions"] == brief and made.stdin == b"-p"
    assert config["mcp_servers"] == {
        "byob": {"command": "byob-mcp", "args": ["--x"]},
        "web": {"url": "https://m"},
    }
    assert os.readlink(home / "skills" / "yudame") == str(tmp_path / "yudame" / "skills")
    assert made.env["CODEX_HOME"] == str(home) and made.argv[-1] == "-"
    # A resumed session keeps the developer message it began with: the Brief opens the prompt.
    resumed = codex.workspace_turn("next", cwd=".", resume="th", harness=harness)("http://h:1", brief, "t")
    assert resumed.argv[-3:] == ["resume", "th", "-"] and resumed.stdin == f"{brief}\n\nnext".encode()
    assert codex.parse(CODEX_TURN) == {
        "text": "stub reply 1", "is_error": False, "session_id": "01a14501-8d3f-7e43-ac99-8359ba467614",
    }  # fmt: skip
    assert codex.parse(CODEX_TURN.replace(b"turn.completed", b"turn.failed"))["is_error"]
    # The rollout is found by the session id the turn printed.
    day = home / "sessions" / "2026" / "10" / "16"
    day.mkdir(parents=True)
    (day / "rollout-2026-10-16T20-58-12-th.jsonl").write_text("{}\n")
    files, skipped = transcripts.collect(transcripts.anchored(str(home), "th", layout="codex"))
    assert [name for name, _ in files] == ["2026/10/16/rollout-2026-10-16T20-58-12-th.jsonl"] and not skipped
    for _, fd in files:
        os.close(fd)


# -- reconcile from the intent row ----------------------------------------------------------


class StuckWrite(WorkspaceWrite):
    """Writes the file, then never returns: the process dies mid-perform."""

    def __init__(self, root, wrote: asyncio.Event):
        super().__init__(root)
        self.wrote = wrote

    async def perform(self, action, key):
        out = await super().perform(action, key)
        self.wrote.set()
        await asyncio.Event().wait()
        return out


def test_the_intent_row_carries_the_action_and_reconcile_rebuilds_it_from_that_alone(dsn, tmp_path):
    """A propose-class effect has no `effect.held` row; a perform cut off
    after its intent leaves only the intent, and reconcile settles it."""
    action = broker.Action("workspace_write", "note.txt", {"text": "hello"})

    async def go():
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="t"))
        wrote = asyncio.Event()
        stuck = broker.Performers(StuckWrite(tmp_path, wrote))
        conn = await db.connect(dsn)
        requesting = asyncio.create_task(broker.request(conn, stuck, task, action))
        await wrote.wait()
        requesting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await requesting
        await conn.close()
        async with await db.connect(dsn) as conn:
            rows = await ledger.read(conn, task)
            (intent,) = [r["payload"] for r in rows if r["type"] == "effect.intent"]
            settled = await broker.reconcile(
                conn, broker.Performers(WorkspaceWrite(tmp_path)), intent["effect_id"]
            )
            return rows, intent, settled, await scripted.task_of(conn, intent["effect_id"]), task

    rows, intent, settled, owner, task = run(go())
    assert not [r for r in rows if r["type"] in ("effect.held", "effect.outcome")]
    assert intent["action_type"] == "workspace_write" and intent["target"] == "note.txt"
    assert intent["payload"] == {"text": "hello"} and intent["effect_class"] == "propose"
    assert intent["payload_sha256"] == ledger.digest({"text": "hello"}) and "approval_id" not in intent
    assert settled.kind == "done" and owner == task


# -- turns run at once up to the machine's limit ---------------------------------


def test_max_turns_holders_run_at_once_and_the_next_waits_for_one(dsn, monkeypatch):
    monkeypatch.setattr(
        slot, "settings", dataclasses.replace(slot.settings, max_turns=2, machine=uuid.uuid4().hex)
    )

    async def go():
        inside: list[str] = []
        release = asyncio.Event()

        async def hold(name: str) -> None:
            async with slot.held(name, dsn):
                inside.append(name)
                await release.wait()

        first = [asyncio.create_task(hold(n)) for n in ("a", "b")]
        while len(inside) < 2:
            await asyncio.sleep(0.05)
        third = asyncio.create_task(hold("c"))
        await asyncio.sleep(3 * slot.POLL_S)
        assert sorted(inside) == ["a", "b"]
        release.set()
        await asyncio.wait_for(asyncio.gather(*first, third), timeout=10)
        assert sorted(inside) == ["a", "b", "c"]

    run(asyncio.wait_for(go(), timeout=15))


# -- the turn's environment ------------------------------------------------------


def test_a_turn_keeps_the_operators_path_and_knows_its_github_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/opt/homebrew/bin:/usr/bin")
    env = workspace.environment({"PATH": "/task/bin:/usr/bin", "REDIS_PORT": "6400"})
    assert env["PATH"] == "/task/bin:/usr/bin:/opt/homebrew/bin" and env["REDIS_PORT"] == "6400"
    spec = workspace.Spec(
        name="p", repo="https://github.com/tomcounsell/popoto.git", kind="python-uv", suite="true"
    )
    lay = workspace.Layout(tmp_path)
    found = workspace.harness_env(lay, spec, {}, {}, bin_dir=tmp_path / "bin", passfile=tmp_path / "pass")
    assert found["GH_REPO"] == "tomcounsell/popoto"


def test_a_step_gets_the_repos_env_and_the_agent_gets_it_less_the_kernels_names(tmp_path, monkeypatch):
    # #3666: a developer in ~/src/<repo> has its .env; a task's clones did not.
    from harnesses import claude_code

    monkeypatch.setattr(workspace, "HOME", tmp_path)
    dot = tmp_path / "src" / "fabrikam" / ".env"
    dot.parent.mkdir(parents=True)
    names = ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "GITHUB_PUSH_TOKEN", "PGPASSWORD")
    dot.write_text("".join(f"{n}=dev-{n}\n" for n in names) + "export DATABASE_URL='postgres://dev/db'\n")
    lay = workspace.Layout(tmp_path / "t")
    spec = workspace.Spec(
        name="p", repo="https://github.com/yudame/fabrikam.git", kind="python-uv", suite="true"
    )
    with_db = dataclasses.replace(spec, services=("postgres",))
    pg = {"bin_dir": tmp_path / "bin", "passfile": tmp_path / "pass"}
    task = workspace.harness_env(lay, with_db, {"postgres": 5440}, {"app": "pw"}, **pg)
    step = workspace.turn_environment({"env": task})
    # The suite gets the whole .env, as a developer's does...
    assert all(step[n] == f"dev-{n}" for n in names if n != "PGPASSWORD")
    # ...under the task's own database, every libpq name its own, and no
    # PGPASSWORD, so each role in its password file logs in with its line.
    assert step["DATABASE_URL"] == "postgresql://app:pw@127.0.0.1:5440/app" and "PGPASSWORD" not in step
    assert step["PGPASSFILE"] == str(tmp_path / "pass")
    # A task recorded with the app's PGPASSWORD (6074b1a2b) loses it too.
    assert "PGPASSWORD" not in workspace.turn_environment({"env": {**task, "PGPASSWORD": "pw"}})
    # A project with no database of its own gets the developer's.
    bare_step = workspace.turn_environment({"env": workspace.harness_env(lay, spec, {}, {}, **pg)})
    assert bare_step["PGPASSWORD"] == "dev-PGPASSWORD" and bare_step["DATABASE_URL"] == "postgres://dev/db"
    harness = {"sandbox_profile": "p", "env": task}
    agent = claude_code.workspace_turn("p", cwd=".", model="m", harness=harness)("http://gw:1", "b", "t").env
    assert agent["GEMINI_API_KEY"] == "dev-GEMINI_API_KEY" and agent["ANTHROPIC_BASE_URL"] == "http://gw:1"
    assert not {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GITHUB_PUSH_TOKEN", "PGPASSWORD"} & set(agent)
    # A check session's suite runs in the environment its own services made.
    check = workspace.check_harness(workspace.Layout(tmp_path / "t"), tmp_path / "c", [], task, services=True)
    checked = workspace.turn_environment(check)
    assert checked["GEMINI_API_KEY"] == "dev-GEMINI_API_KEY" and "PGPASSWORD" not in checked
    # What provisioning records (`harness_env`) holds none of it.
    assert task["GH_REPO"] == "yudame/fabrikam" and not any(v.startswith("dev-") for v in task.values())

    # Read at each step: an edit is seen, and an earlier suite run is not reused.
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "c"], check=True)
    sha = git.out(repo, "rev-parse", "HEAD").strip()

    def digest(env):
        return checks.environment_digest(repo, sha, {}, tmp_path / "bin", env)

    before = digest(task)
    dot.write_text("GEMINI_API_KEY=rotated\n")
    assert workspace.turn_environment({"env": task})["GEMINI_API_KEY"] == "rotated" and digest(task) != before
    # No checkout, or no .env in it: nothing changes.
    bare = {k: v for k, v in task.items() if k != "GH_REPO"}
    dot.unlink()
    assert workspace.repo_env(task) == {} and digest(task) == digest(bare)
    without = workspace.turn_environment({"env": task})
    assert without == {**workspace.turn_environment({"env": bare}), "GH_REPO": "yudame/fabrikam"}
    assert workspace.repo_env({"GH_REPO": "yudame/elsewhere"}) == {}


# -- Tom's own chat: one chat session, only Tom heard ---------------------------


def at(n: int) -> str:
    return f"1780000000.{n:06d}"


@pytest.fixture
def dm(tmp_path):
    """Tom's own Slack DM, fresh per test, with the `valor` project its
    chat session works under. The operator chat is Telegram's `1000`."""
    tom = f"T0ABC/U{uuid.uuid4().hex[:8].upper()}"
    with bridges.operator(tmp_path, operator_slack_ids=(tom,)):
        (pathlib.Path(settings.projects_dir) / "valor.toml").write_text(bridges.VALOR_SPEC)
        yield tom


async def tom_says(dsn, dm, text, ts, *, sender=None, **kw) -> dict:
    """A Slack message in Tom's DM, by Tom unless `sender` says otherwise,
    received and bound: its binding, with the started task's instruction
    when it started one."""
    inbound = intake.Inbound(
        channel="slack", chat_id=dm, chat_kind="dm", message_id=ts, sender_id=sender or dm,
        sender_name="Tom", sent_at="2026-10-15T00:00:00Z", text=text, **kw,
    )  # fmt: skip
    async with await db.connect(dsn) as conn:
        got = await intake.receive(conn, inbound)
        await intake.bind(conn)
    (bound,) = await bridges.of_type(dsn, "message.bound", received_id=got.received_id)
    if bound["as"] == "start":
        async with await db.connect(dsn) as conn:
            bound["instruction"] = (await tasks.brief(conn, bound["task_id"])).instruction
    return bound


async def legacy_notice(conn, task, kind, about, text, entries) -> None:
    """A kernel notice as the bridge sent it before 2026-11-01: its
    `notice.requested` and `notice.sent` rows."""
    nid = ledger.new_id()
    await ledger.append(conn, task, "notice.requested", {"notice_id": nid, "channel": "slack",
                        "chat_id": entries[0]["chat_id"], "kind": kind, "about_key": about, "text": text,
                        "reply_to": None})  # fmt: skip
    await ledger.append(conn, task, "notice.sent", {"notice_id": nid, "sent": entries})


async def posted(dsn, task, chat, ts, text, *, question=False) -> None:
    """Valor's post in a Slack chat: a send, or with `question` the notice
    of the task's open question the kernel sent there itself, as it did
    before 2026-11-01."""
    entries = [{"channel": "slack", "chat_id": chat, "message_id": ts, "thread_ts": None}]
    async with await db.connect(dsn) as conn:
        if question:
            await legacy_notice(conn, task, "question",
                                f"question:{machine.fold(await ledger.read(conn, task)).open_question}",
                                text, entries)  # fmt: skip
            return
        effect, payload = ledger.new_id(), {"text": text}
        await ledger.append(conn, task, "effect.intent",
                            {"effect_id": effect, "action_type": "slack.send_message", "target": chat,
                             "payload": payload})  # fmt: skip
        await ledger.append(conn, task, "effect.outcome",
                            {"effect_id": effect, "idempotency_key": "k", "kind": "done",
                             "result": {"sent": entries}, "error": None})  # fmt: skip


async def asking(dsn) -> str:
    """A task waiting on its open question."""
    task = await new_task(dsn, max_effect_class="act")
    async with await db.connect(dsn) as conn:
        for row in Ledger().ask().rows[1:]:
            p = {k: f"{task}-{v}" if k == "turn_id" else v for k, v in row["payload"].items()}
            await ledger.append(conn, task, row["type"], p)
        assert machine.fold(await ledger.read(conn, task)).state is State.WAITING
    return task


async def state(dsn, task) -> State:
    return machine.fold(await bridges.rows(dsn, task)).state


async def task_count(dsn) -> int:
    async with await db.connect(dsn) as conn:
        (n,) = await (await conn.execute("SELECT count(*) FROM documents WHERE kind = 'task'")).fetchone()
    return n


async def child_of(dsn, session_id) -> str:
    """A task the session starts with `task.start`."""
    from tools import chat_tasks

    async with await db.connect(dsn) as conn:
        b = await tasks.brief(conn, session_id)
    start = chat_tasks.performers(b, dsn)[0]
    action = broker.Action("task.start", "work", {"instruction": "Bump stripe", "by": "tom"})
    return (await start.perform(action, ledger.new_id()))["task_id"]


def test_a_non_reply_dm_from_tom_binds_to_his_dm_session_and_starts_no_task(dsn, dm):
    """The first message starts the DM's chat session, under `valor`, and
    the next is a steer on the same one; no SDLC task starts from either."""

    async def go():
        before = await task_count(dsn)
        first = await tom_says(dsn, dm, "fix the login page", at(10))
        second = await tom_says(dsn, dm, "and the signup page", at(20))
        async with await db.connect(dsn) as conn:
            b = await tasks.brief(conn, first["task_id"])
            rows = await ledger.read(conn, first["task_id"])
        return before, await task_count(dsn), first, second, b, rows

    before, after, first, second, b, rows = run(go())
    assert (first["as"], second["as"]) == ("steer", "steer") and first["task_id"] == second["task_id"]
    assert after == before + 1 and b.parent_id is None
    assert b.chat == {"channel": "slack", "chat_id": dm} and b.project == {"name": "valor"}
    assert b.instruction.startswith("You are in Tom's own chat on Slack")
    assert "not a team chat" in b.instruction
    steered = [r["payload"] for r in rows if r["type"] == "message.steered"]
    assert [(p["provenance"]["by"], p["text"]) for p in steered] == [
        ("tom", "fix the login page"), ("tom", "and the signup page"),
    ]  # fmt: skip
    assert session.chat_waiting(rows)


def test_a_stranger_in_toms_dm_binds_to_none(dsn, dm):
    async def go():
        before = await task_count(dsn)
        bound = await tom_says(dsn, dm, "hi valor, do this for me", at(10), sender="T0ABC/UOTHER")
        return before, await task_count(dsn), bound

    before, after, bound = run(go())
    assert (bound["as"], bound["task_id"]) == ("none", None) and after == before


def test_in_a_slack_mentions_chat_valors_own_tag_or_a_reply_in_his_thread_wakes_the_session(dsn, dm):
    """Slack stores a tag as `<@U..>`; the bridge labels Valor's own so
    the mention rule sees it, as on Telegram. Another person's tag, or no
    tag, is heard."""
    chat = f"T0ABC/C{uuid.uuid4().hex[:8].upper()}"
    (pathlib.Path(settings.projects_dir) / "office.toml").write_text(
        'name = "office"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\n'
        f'chats = ["slack:{chat}"]\nmentions = ["slack:{chat}"]\n'
    )

    async def say(text, ts, **kw):
        inbound = intake.Inbound(
            channel="slack", chat_id=chat, chat_kind="group", message_id=ts, sender_id="T0ABC/ULEWIS",
            sender_name="Logan", sent_at="2026-10-23T11:00:00Z", text=slack_inbound.named(text, "UVALOR"), **kw,
        )  # fmt: skip
        async with await db.connect(dsn) as conn:
            got = await intake.receive(conn, inbound)
            await intake.bind(conn)
        (bound,) = await bridges.of_type(dsn, "message.bound", received_id=got.received_id)
        return bound

    async def go():
        quiet = [(await say(t, at(n)))["as"] for t, n in (("lunch at noon?", 10), ("ask <@UTOM>", 11))]
        tagged = await say("<@UVALOR> what's on today?", at(20))
        await posted(dsn, tagged["task_id"], chat, at(30), "Nothing booked.")
        reply = await say("thanks", at(40), reply_to=at(30), topic_id=at(30))
        async with await db.connect(dsn) as conn:
            b = await tasks.brief(conn, tagged["task_id"])
        return quiet, tagged, reply, b

    quiet, tagged, reply, b = run(go())
    assert quiet == ["heard", "heard"] and tagged["as"] == "steer"
    assert (reply["task_id"], reply["as"]) == (tagged["task_id"], "steer")
    assert b.chat == {"channel": "slack", "chat_id": chat} and b.project == {"name": "office"}


def test_every_reply_in_toms_dm_is_his_sessions_with_the_post_it_is_under(dsn, dm):
    """Only a chat session speaks as Valor, so every message of Tom's in
    his DM is his session's: a threaded reply under an older task's
    question notice (sent before 2026-11-01) carries the notice and its
    task, which then reports to the session, so the session's `task.answer`
    reaches it; a reply under a team session's post carries its text, no
    task; a reply under the DM session's own post carries that send."""
    from tools import chat_tasks

    async def go():
        waiting = await asking(dsn)
        await posted(dsn, waiting, dm, at(10), "Which colour?", question=True)
        answer = await tom_says(dsn, dm, "blue", at(20), reply_to=at(10), topic_id=at(10))
        team_chat = {"channel": "slack", "chat_id": f"T0ABC/C{uuid.uuid4().hex[:8].upper()}"}
        team = await new_task(dsn, chat=team_chat, max_effect_class="act")
        await posted(dsn, team, dm, at(30), "From the team chat: can you confirm?")
        to_team = await tom_says(dsn, dm, "confirmed", at(40), reply_to=at(30))
        chat = answer["task_id"]
        await posted(dsn, chat, dm, at(60), "Hello Tom.")
        thanks = await tom_says(dsn, dm, "thanks", at(70), reply_to=at(60))
        async with await db.connect(dsn) as conn:
            b = await tasks.brief(conn, chat)
            reporting = await tasks.reporting(conn, chat)
            rows = await ledger.read(conn, chat)
        _, answering, _, _ = chat_tasks.performers(b, dsn)
        async with await db.connect(dsn) as conn:
            refused = await answering.refuse(conn, broker.Action("task.answer", waiting, {"text": "blue"}))
        await answering.perform(broker.Action("task.answer", waiting, {"text": "blue", "by": "tom"}), "a1")
        return waiting, answer, team, to_team, chat, thanks, reporting, rows, refused, await bridges.rows(
            dsn, waiting
        )

    waiting, answer, team, to_team, chat, thanks, reporting, rows, refused, waited = run(go())
    assert chat not in (waiting, team)
    assert [(x["task_id"], x["as"]) for x in (answer, to_team, thanks)] == [(chat, "steer")] * 3
    under = [p.get("replying_to") for p in bridges.typed(rows, "message.steered")]
    assert under == [
        {"text": "Which colour?", "task_id": waiting},
        {"text": "From the team chat: can you confirm?"},
        {"text": "Hello Tom."},
    ]
    assert reporting == [waiting] and refused is None
    assert [r["payload"]["text"] for r in waited if r["type"] == "question.answered"] == ["blue"]
    shown = session.chat_prompt(rows)
    assert "under task " + waiting + "'s post:\n  > Which colour?" in shown
    assert "under your post:\n  > Hello Tom." in shown


def test_stop_in_toms_dm_stops_every_task_reporting_to_its_session_and_tells_the_session(dsn, dm):
    """Exactly `stop`, top level or in reply to the session's own post,
    stops at once each open task the session started or that reported to
    it, never the session; the session gets the stop as a steer. A reply
    `stop` under an older task's question stops that task alone. A kernel
    report (a credential, a stream's) puts no task in its reach."""

    async def go():
        chat = (await tom_says(dsn, dm, "hello", at(10)))["task_id"]
        child, other = await child_of(dsn, chat), await new_task(dsn, max_effect_class="act")
        merging = await new_task(dsn, max_effect_class="act")
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, chat, "child.reported",
                                {"child_id": other, "kind": "question", "about_key": "q", "text": "?"})  # fmt: skip
            for reporter, kind in ((merging, "credential"), (intake.UPDATES, "rollout")):
                await notices.request(conn, reporter, kind=kind, about_key=f"{kind}:{uuid.uuid4().hex}", text="!", session=chat)
        top = await tom_says(dsn, dm, "stop", at(20))
        later = await child_of(dsn, chat)
        await posted(dsn, chat, dm, at(30), "Started the next one.")
        replied = await tom_says(dsn, dm, " Stop ", at(40), reply_to=at(30))
        waiting, untouched = await asking(dsn), await child_of(dsn, chat)
        await posted(dsn, waiting, dm, at(50), "Should I go on?", question=True)
        old = await tom_says(dsn, dm, "stop", at(60), reply_to=at(50))
        states = [await state(dsn, t) for t in (child, other, later, waiting, chat, merging, untouched)]
        return chat, top, replied, old, states, await bridges.rows(dsn, chat)

    chat, top, replied, old, states, rows = run(go())
    assert [(x["task_id"], x["as"]) for x in (top, replied, old)] == [(chat, "stop")] * 3
    assert states[:4] == [State.STOPPED] * 4
    assert [s is State.STOPPED for s in states[4:]] == [False, False, False]
    steered = [p["text"] for p in bridges.typed(rows, "message.steered")]
    assert steered[-3:] == ["stop", " Stop ", "stop"]
    kernel = [p for p in bridges.typed(rows, "child.reported") if p.get("from")]
    assert [(p["kind"], p["child_id"]) for p in kernel] == [("credential", None), ("rollout", None)]


def test_a_root_task_with_no_session_reports_to_the_operator_chats_session(dsn, dm):
    """Its question is `child.reported` on the operator chat's session,
    started for it; the session can answer, steer and stop it. Its push
    credential request goes to that session too, waking it, and nothing
    is left for a bridge to send."""
    from tools import chat_tasks

    async def go():
        root = await asking(dsn)
        async with await db.connect(dsn) as conn:
            await notices.owe(conn, root)
            await notices.credential(conn, root, "abc", missing=True)
            operator = await intake.chat_session(conn, "telegram", bridges.OPERATOR_CHAT)
            b = await tasks.brief(conn, operator)
        _, answer, steer, stop = chat_tasks.performers(b, dsn)
        async with await db.connect(dsn) as conn:
            refused = await steer.refuse(
                conn, broker.Action("task.steer", await new_task(dsn), {"text": "x"})
            )
        await answer.perform(broker.Action("task.answer", root, {"text": "green", "by": "tom"}), "a1")
        await steer.perform(
            broker.Action("task.steer", root, {"text": "and make it bold", "by": "tom"}), "s1"
        )
        found = await steer.lookup(broker.Action("task.steer", root, {}), "s1")
        await stop.perform(broker.Action("task.stop", root, {"why": "not now", "by": "tom"}), "t1")
        return root, b, refused, found, await bridges.rows(dsn, operator), await bridges.rows(dsn, root)

    root, b, refused, found, operator_rows, root_rows = run(go())
    assert b.chat == {"channel": "telegram", "chat_id": bridges.OPERATOR_CHAT} and b.project == {
        "name": "valor"
    }
    assert "Tom's own chat on Telegram" in b.instruction
    reported = [p for p in bridges.typed(operator_rows, "child.reported") if p["from"] == root]
    assert [(p["kind"], p["child_id"]) for p in reported] == [("question", root), ("credential", None)]
    assert not any(p.get("quiet") for p in reported) and session.chat_waiting(operator_rows)
    assert "Your task " + root + " reports (credential):" in session.chat_prompt(operator_rows)
    assert not bridges.typed(root_rows, "notice.requested")
    assert refused.endswith("does not report to this chat") and found == {"task_id": root}
    assert [p["text"] for p in bridges.typed(root_rows, "question.answered")] == ["green"]
    assert [p["text"] for p in bridges.typed(root_rows, "message.steered")] == ["and make it bold"]
    assert machine.fold(root_rows).state is State.STOPPED


SESSION = "00000000-0000-4000-8000-0000000000aa"


async def delivered(dsn, session_id, where: State) -> str:
    """A child of the session, its rows played to `where` (`_reach`), each
    turn on harness session `SESSION`."""
    from tests.test_machine import _reach

    child = await child_of(dsn, session_id)
    async with await db.connect(dsn) as conn:
        for row in _reach(where).rows[1:]:
            p = {k: f"{child}-{v}" if k in ("turn_id", "effect_id") else v for k, v in row["payload"].items()}
            if row["type"] == "turn.ended":
                p["result"] = {**p["result"], "session_id": SESSION}
            await ledger.append(conn, child, row["type"], p)
    assert await state(dsn, child) is where
    return child


async def team_session(dsn) -> str:
    return await new_task(dsn, chat={"channel": "telegram", "chat_id": f"-{uuid.uuid4().int % 10**9}"},
                          project={"name": "valor"}, max_effect_class="act")  # fmt: skip


def _steer(b, dsn):
    from tools import chat_tasks

    return chat_tasks.performers(b, dsn)[2]


@pytest.mark.parametrize("where", [State.ENDED, State.MERGED])
def test_a_chat_sessions_steer_of_a_delivered_task_is_feedback_that_reopens_it(dsn, where):
    """The task goes to `patch` on a `feedback.given` naming who said it,
    via the chat, and the steer's key; its next turn resumes its own
    harness session, opening with the feedback under the sender's name."""

    async def go():
        chat = await team_session(dsn)
        child = await delivered(dsn, chat, where)
        async with await db.connect(dsn) as conn:
            steer = _steer(await tasks.brief(conn, chat), dsn)
        action = broker.Action("task.steer", child, {"text": "make the video shorter", "by": "Ann"})
        done = await steer.perform(action, "k1")
        found = await steer.lookup(action, "k1")
        async with await db.connect(dsn) as conn:
            prompt, resume, _ = await session.next_prompt(conn, child)
        return done, found, prompt, resume, await bridges.rows(dsn, child)

    done, found, prompt, resume, rows = run(go())
    (given,) = bridges.typed(rows, "feedback.given")
    assert given["text"] == "make the video shorter" and given["effect_key"] == "k1"
    assert (given["provenance"]["by"], given["provenance"]["via"]) == ("Ann", "chat")
    assert done == found == {"task_id": done["task_id"], "feedback_id": given["feedback_id"]}
    assert not bridges.typed(rows, "message.steered") and machine.fold(rows).state is State.PATCH
    assert prompt.startswith("# Feedback on the delivery from Ann, via chat\n\nmake the video shorter")
    assert resume == SESSION


@pytest.mark.parametrize("where", [State.BUILD, State.MERGE, State.STOPPED])
def test_a_chat_sessions_steer_of_a_task_not_delivered_or_stopped_stays_a_steer(dsn, where):
    async def go():
        chat = await team_session(dsn)
        child = await delivered(dsn, chat, where)
        async with await db.connect(dsn) as conn:
            steer = _steer(await tasks.brief(conn, chat), dsn)
        done = await steer.perform(broker.Action("task.steer", child, {"text": "bolder", "by": "tom"}), "k1")
        return done, await bridges.rows(dsn, child)

    done, rows = run(go())
    assert [p["text"] for p in bridges.typed(rows, "message.steered")] == ["bolder"]
    assert "feedback_id" not in done and not bridges.typed(rows, "feedback.given")
    assert machine.fold(rows).state is where


def test_a_chat_sessions_steer_of_a_waiting_task_answers_its_question(dsn):
    """A waiting task reads nothing but an answer, so a steer is one: it
    leaves `waiting`, and the steer's key finds the answer."""

    async def go():
        chat = await team_session(dsn)
        child = await delivered(dsn, chat, State.WAITING)
        async with await db.connect(dsn) as conn:
            steer = _steer(await tasks.brief(conn, chat), dsn)
        action = broker.Action("task.steer", child, {"text": "identity first", "by": "tom"})
        done = await steer.perform(action, "k1")
        return done, await steer.lookup(action, "k1"), await bridges.rows(dsn, child)

    done, found, rows = run(go())
    (answered,) = bridges.typed(rows, "question.answered")
    assert answered["text"] == "identity first" and answered["effect_key"] == "k1"
    assert done == found == {"task_id": done["task_id"], "question_id": answered["question_id"]}
    assert not bridges.typed(rows, "message.steered") and machine.fold(rows).state is not State.WAITING


def test_a_second_revision_round_with_the_same_words_is_a_second_feedback(dsn):
    """Each round is found by its own key, so a repeat is never taken for
    the round before it."""

    async def go():
        chat = await team_session(dsn)
        child = await delivered(dsn, chat, State.ENDED)
        async with await db.connect(dsn) as conn:
            steer = _steer(await tasks.brief(conn, chat), dsn)
        action = broker.Action("task.steer", child, {"text": "again", "by": "tom"})
        first = await steer.perform(action, "k1")
        async with await db.connect(dsn) as conn:
            await ledger.append(
                conn, child, "task.ended", {"kind": "decided", "by": "valor", "text": "Done: v2"}
            )
        second = await steer.perform(action, "k2")
        return (
            first,
            second,
            [await steer.lookup(action, k) for k in ("k1", "k2")],
            await bridges.rows(dsn, child),
        )

    first, second, found, rows = run(go())
    assert [p["effect_key"] for p in bridges.typed(rows, "feedback.given")] == ["k1", "k2"]
    assert found == [first, second] and first["feedback_id"] != second["feedback_id"]
    assert machine.fold(rows).state is State.PATCH


def test_an_empty_steer_or_one_under_a_stop_on_a_delivered_task_fails_and_writes_nothing(dsn):
    """Feedback has text, and a merged task under a stopped session takes
    none: the effect fails with feedback's own error, the task unmoved (the
    stop itself stopped the ended one)."""

    async def go():
        chat = await team_session(dsn)
        ended, merged = await delivered(dsn, chat, State.ENDED), await delivered(dsn, chat, State.MERGED)
        async with await db.connect(dsn) as conn:
            steer = _steer(await tasks.brief(conn, chat), dsn)
        errors = []
        try:
            await steer.perform(broker.Action("task.steer", ended, {"text": "  ", "by": "tom"}), "k1")
        except ValueError as exc:
            errors.append(str(exc))
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, chat, reason="the chat closed")
        try:
            await steer.perform(broker.Action("task.steer", merged, {"text": "redo", "by": "tom"}), "k2")
        except LookupError as exc:
            errors.append(str(exc))
        return errors, [await bridges.rows(dsn, t) for t in (ended, merged)]

    errors, both = run(go())
    assert errors[0] == "feedback has text" and "under stopped task" in errors[1]
    for rows, where in zip(both, (State.STOPPED, State.MERGED)):
        assert not bridges.typed(rows, "feedback.given") and not bridges.typed(rows, "message.steered")
        assert machine.fold(rows).state is where


def test_the_patch_stage_tells_a_revised_deliverable_how_to_come_back():
    text = tasks.stage_text(State.PATCH)
    assert "**Feedback on a delivery.**" in text
    assert "absolute path" in text and "opening `Done:`" in text


def test_a_sessions_child_and_grandchild_report_to_it_and_nothing_goes_to_a_bridge(dsn, dm):
    """A routine run is a child of its objective, not of a session: the
    nearest chat session up the tree hears a task's report, at any depth."""

    async def go():
        chat = (await tom_says(dsn, dm, "hello", at(10)))["task_id"]
        child = await child_of(dsn, chat)
        grandchild = await new_task(dsn, parent_id=child)
        async with await db.connect(dsn) as conn:
            for t in (child, grandchild):
                await notices.credential(conn, t, "abc", missing=True)
            heads = [await notices.headline(conn, t) for t in (chat, child)]
            rows = await ledger.read(conn, chat)
            (n,) = await (
                await conn.execute(
                    "SELECT count(*) FROM events WHERE type = 'notice.requested' AND task_id = ANY(%s)",
                    ([chat, child, grandchild],),
                )
            ).fetchone()
        return heads, [(p["from"], p["kind"]) for p in bridges.typed(rows, "child.reported")], n

    heads, reported, n = run(go())
    assert heads[0] is None and heads[1]
    assert [k for _, k in reported] == ["credential", "credential"] and n == 0


def test_a_chat_session_that_kept_failing_on_a_message_hands_it_to_the_next_session_once(dsn, dm):
    """The adatum chat, 2026-10-23: a broken install failed four turns on
    Tom's message. The chat's next session hears which message went
    unanswered and answers it; a session that ends holding only that report
    passes nothing on, so the chain ends; one that answered says nothing."""
    ended = {"by": "kernel", "kind": "step", "check": None, "state": "clarify", "tries": 4, "reason": "boom"}

    async def fail(conn, task, *verdicts):
        for verdict in verdicts:
            turn = {"state": "clarify", "verdict": verdict, "turn_id": ledger.new_id()}
            await ledger.append(conn, task, "turn.collected", turn)
        await ledger.append(conn, task, "task.ended", ended)
        await notices.owe(conn, task)

    async def go():
        first = (await tom_says(dsn, dm, "Can this be used?", at(10)))["task_id"]
        async with await db.connect(dsn) as conn:
            await fail(conn, first, "failed")
            second = await intake.chat_session(conn, "slack", dm)
            await fail(conn, second, "failed")
            third = await intake.chat_session(conn, "slack", dm)
        answered = (await tom_says(dsn, dm, "hello", at(20)))["task_id"]
        async with await db.connect(dsn) as conn:
            await fail(conn, answered, "idle", "failed")
            fourth = await intake.chat_session(conn, "slack", dm)
        return first, second, third, answered, fourth, [await bridges.rows(dsn, t) for t in (second, answered)]

    first, second, third, answered, fourth, (second_rows, answered_rows) = run(go())
    assert second not in (None, first) and third is None and answered not in (first, second) and fourth is None
    handed, held = bridges.typed(second_rows, "child.reported")
    assert handed["from"] == first and not handed.get("quiet")
    assert (held["from"], held["quiet"]) == (second, True)
    assert handed["text"].startswith("The session before you in this chat failed 4 times in a row on message")
    assert '("Can this be used?")' in handed["text"] and handed["text"].endswith("answer it.")
    assert [p.get("quiet") for p in bridges.typed(answered_rows, "child.reported")] == [True]


def test_a_session_ended_after_its_pick_gives_way_to_a_new_one(dsn, dm):
    """The binder picks the DM's session, then waits on its tree's lock
    while another connection stops it; once the lock is free the binder
    sees the stop and binds the message to a new session."""

    async def go():
        old = (await tom_says(dsn, dm, "hello", at(10)))["task_id"]
        inbound = intake.Inbound(
            channel="slack", chat_id=dm, chat_kind="dm", message_id=at(20), sender_id=dm,
            sender_name="Tom", sent_at="2026-10-15T00:00:00Z", text="are you there?",
        )  # fmt: skip
        async with await db.connect(dsn) as conn:
            got = await intake.receive(conn, inbound)
        async with await db.connect(dsn) as holder, await db.connect(dsn) as binder:
            async with holder.transaction():
                await tasks.lock_tree(holder, old)
                binding = asyncio.create_task(intake.bind(binder))
                pid = binder.info.backend_pid
                for _ in range(500):
                    (waits,) = await (
                        await holder.execute(
                            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", (pid,)
                        )
                    ).fetchone()
                    if waits == "Lock":
                        break
                    await asyncio.sleep(0.01)
                await tasks.stop(holder, old, reason="ended meanwhile")
            await binding
        (bound,) = await bridges.of_type(dsn, "message.bound", received_id=got.received_id)
        async with await db.connect(dsn) as conn:
            b = await tasks.brief(conn, bound["task_id"])
        return old, waits, bound, b, await bridges.rows(dsn, bound["task_id"])

    old, waits, bound, b, rows = run(go())
    assert waits == "Lock" and bound["as"] == "steer" and bound["task_id"] != old
    assert b.chat == {"channel": "slack", "chat_id": dm} and "): hello" in b.instruction
    assert [p["text"] for p in bridges.typed(rows, "message.steered")] == ["are you there?"]


def test_with_no_project_to_hold_it_toms_dm_message_starts_a_task(dsn, tmp_path):
    tom = f"T0ABC/U{uuid.uuid4().hex[:8].upper()}"

    async def go():
        return await tom_says(dsn, tom, "write a haiku", at(10))

    with bridges.operator(tmp_path, operator_slack_ids=(tom,)):
        bound = run(go())
    assert (bound["as"], bound["instruction"]) == ("start", "write a haiku")


# -- core shape and the queue (docs/plans/ask-reconciliation.md) --------------------


def test_queue_order_is_start_order_moved_by_each_placement_in_turn():
    def at(where, task, anchor):
        return {"task": task, "anchor": anchor, "where": where}

    def order(*moves):
        q = tasks.queue("abcd", moves)
        return "".join(sorted(q, key=q.get))

    assert order() == "abcd"
    assert order(at("after", "a", "c")) == "bcad"  # moved later
    assert order(at("before", "d", "b"), at("before", "c", "d")) == "acdb"  # a chain, in order
    assert order(at("before", "a", "b"), at("before", "b", "a")) == "bacd"  # a cycle: the latest wins
    assert order(at("before", "d", "gone"), at("after", "a", "a")) == "abcd"  # not open, or itself


def test_the_queue_line_counts_ahead_only_tasks_with_a_step_to_run(dsn):
    # #3668: idle chat sessions and waiting tasks were counted as a backlog.
    from tests.test_machine import _reach

    async def go():
        chat = await new_task(dsn, chat={"channel": "telegram", "chat_id": f"-{uuid.uuid4().int % 10**9}"})
        waiting, a, b = [await new_task(dsn) for _ in range(3)]
        async with await db.connect(dsn) as conn:
            for row in _reach(State.WAITING).rows[1:]:
                p = {
                    k: f"{waiting}-{v}" if k in ("turn_id", "effect_id") else v
                    for k, v in row["payload"].items()
                }
                await ledger.append(conn, waiting, row["type"], p)
            # Placed before the idle chat session: its place still counts.
            await ledger.append(conn, b, "task.placed", {"anchor": chat, "where": "before"})
            idle = [await router.queue_line(conn, t) for t in (a, b)]
            await ledger.append(conn, chat, "message.steered", {"text": "hi"})
            heard = await router.queue_line(conn, a)
            rows = await ledger.read(conn, chat)
        return idle, heard, rows

    idle, heard, rows = run(go())
    # Other tests' open tasks are ahead of all three; count from b's line.
    a_idle, b_idle, a_heard = [int(line.split(", ")[1].split(" ahead")[0]) for line in (*idle, heard)]
    assert (a_idle - b_idle, a_heard - b_idle) == (1, 2) and "waiting on an answer" in idle[0]
    quiet = [r for r in rows if r["type"] != "message.steered"]
    f = machine.fold(quiet)
    assert not router.has_step(quiet, f, True)
    failed = {"id": 10**9, "type": "step.failed", "payload": {}, "at": None}
    assert router.has_step([*quiet, failed], f, True) and router.has_step(quiet, f, False)


def test_core_shape_labels_the_decision_steers_an_amend_and_places_the_task(dsn):
    async def go():
        async with await db.connect(dsn) as conn:
            x, y, z, bare = [await new_task(dsn) for _ in range(4)]
            await ledger.append(conn, x, "reconcile.decided", {"decision": "overlaps", "named": y})
            await ledger.append(
                conn, z, "reconcile.decided", {"decision": "overlaps", "named": x, "placement": "before"}
            )
            await ledger.append(
                conn, y, "reconcile.decided", {"decision": "overlaps", "named": x, "placement": "before"}
            )
            refused = []
            for t, kw in ((bare, {"confirm": True}), (x, {"before": x}), (x, {"after": "nope"}),
                          (x, {"confirm": True, "amend": "y"}), (x, {})):  # fmt: skip
                try:
                    await session.shape(conn, t, by="valor", **kw)
                except (LookupError, ValueError) as exc:
                    refused.append(str(exc))
            got = [
                await session.shape(conn, x, confirm=True, by="valor"),
                await session.shape(conn, y, amend="only the tests half", after=z, by="valor"),
            ]
            ids = [x, y, z, bare]
            queued = tasks.queue(ids, await tasks.moves(conn, ids))
            return got, refused, queued, ids, await ledger.read(conn, x), await ledger.read(conn, y)

    got, refused, queued, (x, y, z, bare), xs, ys = run(go())
    assert got == [["shape.confirmed"], ["shape.confirmed", "message.steered", "task.placed"]]
    assert len(refused) == 5 and "no reconciler decision" in refused[0]
    # z's proposal applies (z before x); y's writer placed it, so y's own proposal does not.
    assert sorted(queued, key=queued.get) == [z, y, x, bare]
    label = next(r["payload"] for r in xs if r["type"] == "shape.confirmed")
    assert (label["confirmed"], label["decision"], label["provenance"]["by"]) == (True, "overlaps", "valor")
    steer = next(r["payload"] for r in ys if r["type"] == "message.steered")
    assert session._steering([steer], {}) == "Written to you while you worked:\n- valor: only the tests half"


# -- corrections and exemplars ----------------------------------------------------


def test_direct_corrections_render_as_before_and_exemplars_follow_in_their_own_section():
    by = {"by": "tom", "at": "2026-10-16T00:00:00+00:00", "via": "the command line"}
    one, two, three = [
        {"number": n, "scope": "global", "source_class": k, "text": t, "provenance": by}
        for n, k, t in ((1, "direct", "Fix the code."), (2, "exemplar", "One page."), (3, "direct", "Ask."))
    ]
    none = "# Corrections from Tom\n\nNone recorded."
    line = "{}. (global, {}; tom, 2026-10-16, via the command line)\n{}"
    direct = "# Corrections from Tom\n\nIn force for this turn, in the order Tom gave them.\n\n"
    direct += line.format(1, "direct", "Fix the code.") + "\n\n" + line.format(3, "direct", "Ask.")
    exemplars = "\n\n# Exemplars from Tom\n\nWork Tom loved, and why, in the order he gave them.\n\n"
    exemplars += line.format(2, "exemplar", "One page.")
    assert corrections.render([]) == none
    assert corrections.render([one, three]) == direct
    assert corrections.render([one, two, three]) == direct + exemplars
    assert corrections.render([two]) == none + exemplars


def test_exemplars_are_recorded_numbered_with_corrections_and_reach_a_turn_once(dsn):
    env = {**os.environ, "VALOR_DB": settings.test_database}

    def core(*argv):
        argv = [sys.executable, "-m", "core", *argv]
        return subprocess.run(argv, env=env, capture_output=True, text=True, check=False)

    async def go():
        async with await db.connect(dsn) as conn:
            c = await corrections.record(conn, "Say less.", by="tom", via="test")
            e = await corrections.record(conn, "The c4 plan.", by="tom", via="t", source_class="exemplar")
            d = await corrections.record(conn, "Say it plainly.", by="tom", via="test")
            assert [e["number"], d["number"]] == [c["number"] + 1, c["number"] + 2]
            kinds = (c["source_class"], e["source_class"], e["provenance"]["via"])
            assert kinds == ("direct", "exemplar", "t")
            with pytest.raises(ValueError, match="an exemplar has text"):
                await corrections.record(conn, "  ", by="tom", via="t", source_class="exemplar")
            turn = await tasks.dispatch(conn, await tasks.start(conn, tasks.Brief(instruction="test")))
            # The turn's `corrections` lists every number it carries, exemplars included.
            assert {c["number"], e["number"], d["number"]} <= set(turn["corrections"])
            text = turn["text"]
            assert text.count("The c4 plan.") == 1
            assert text.index("# Corrections from Tom") < text.index("# Exemplars from Tom")
            assert text.index("# Exemplars from Tom") < text.index("The c4 plan.")

    run(go())
    assert "exemplar TEXT" in core("--help").stdout
    made = core("exemplar", "The c4 plan: three fixes, one page.", "--via", "test")
    out = made.stdout
    assert made.returncode == 0 and out.startswith("exemplar ") and " recorded, ledger row " in out
    listed = core("corrections").stdout
    assert f"{out.split()[1]}. (global, exemplar; tom, " in listed and "three fixes, one page." in listed
    blank = core("exemplar", "   ")
    assert blank.returncode != 0 and "exemplar refused: an exemplar has text" in blank.stderr
    assert core("corrections").stdout == listed


def test_a_withdrawn_or_replaced_correction_leaves_the_next_turn_and_stays_in_the_ledger(dsn):
    env = {**os.environ, "VALOR_DB": settings.test_database}

    def core(*argv):
        argv = [sys.executable, "-m", "core", *argv]
        return subprocess.run(argv, env=env, capture_output=True, text=True, check=False)

    async def go():
        async with await db.connect(dsn) as conn:
            old = await corrections.record(conn, "Write tabs.", by="tom", via="test")
            gone = await corrections.record(conn, "Use emoji.", by="tom", via="test")
            new = await corrections.record(
                conn, "Write spaces.", by="tom", via="test", replaces=old["number"]
            )
            await corrections.withdraw(conn, gone["number"], by="tom", via="test")
            with pytest.raises(ValueError, match=f"no correction {gone['number']} is in force"):
                await corrections.withdraw(conn, gone["number"], by="tom", via="test")
            turn = await tasks.dispatch(conn, await tasks.start(conn, tasks.Brief(instruction="test")))
            assert new["number"] in turn["corrections"]
            assert not {old["number"], gone["number"]} & set(turn["corrections"])
            assert "Write spaces." in turn["text"]
            assert "Write tabs." not in turn["text"] and "Use emoji." not in turn["text"]
            rows = [
                r
                for r in await ledger.read(conn, corrections.STREAM)
                if r["payload"]["number"] >= old["number"]
            ]
            withdrawn = {
                r["payload"]["number"]: r["payload"].get("replaced_by")
                for r in rows
                if r["type"] == "correction.withdrawn"
            }
            assert withdrawn == {old["number"]: new["number"], gone["number"]: None}
            assert sum(r["type"] == "correction.recorded" for r in rows) == 3
            return await corrections.record(conn, "One page.", by="tom", via="t", source_class="exemplar")

    exemplar = run(go())
    made = core("exemplar", "Half a page.", "--replaces", str(exemplar["number"]), "--via", "test")
    assert made.returncode == 0, made.stderr
    listed = core("corrections").stdout
    assert "Half a page." in listed and "One page." not in listed
    number = made.stdout.split()[1]
    out = core("withdraw", number, "--via", "test")
    assert out.returncode == 0 and out.stdout.startswith(f"{number} withdrawn, ledger row ")
    assert "Half a page." not in core("corrections").stdout
    again = core("withdraw", number)
    assert again.returncode != 0 and f"withdraw refused: no correction {number} is in force" in again.stderr


# -- a team chat: one long-lived chat session, every message a steer ------------


# LONE: a chat session started directly, which no message there reaches.
GROUP, QUIET_GROUP, OTHER, LONE = "-1001", "-1002", "-1003", "-1004"
RAIN = "77"
# KIM_DM: someone else's DM the spec lists.
KIM_DM = "78"


@pytest.fixture
def team(tmp_path):
    """A project whose two team chats this machine reads, one of them
    mentions only; `OTHER` is a group no spec lists."""
    with bridges.operator(tmp_path):
        (pathlib.Path(settings.projects_dir) / "flowers.toml").write_text(
            'name = "flowers"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\n'
            f'chats = ["telegram:{GROUP}", "telegram:{QUIET_GROUP}", "telegram:{KIM_DM}"]\n'
            f'mentions = ["telegram:{QUIET_GROUP}"]\n'
        )
        yield


async def said(dsn, text, *, chat=GROUP, sender=RAIN, name="Robin", reply_to=None, kind="group") -> dict:
    """A Telegram message, in a group unless `kind` says, received and
    bound: its binding."""
    mid = str(uuid.uuid4().int % 10**9)
    inbound = intake.Inbound(
        channel="telegram", chat_id=chat, chat_kind=kind, message_id=mid, sender_id=sender,
        sender_name=name, sent_at="2026-10-16T09:30:00+00:00", text=text, reply_to=reply_to,
    )  # fmt: skip
    async with await db.connect(dsn) as conn:
        got = await intake.receive(conn, inbound)
        await intake.bind(conn)
    (bound,) = await bridges.of_type(dsn, "message.bound", received_id=got.received_id)
    bound["message_id"] = mid
    return bound


async def rows_of(dsn, task) -> list[dict]:
    async with await db.connect(dsn) as conn:
        return await ledger.read(conn, task)


async def turn(dsn, task) -> None:
    """A finished turn of the chat session: what came before it is spent."""
    turn_id = ledger.new_id()
    async with await db.connect(dsn) as conn:
        await ledger.append(conn, task, "turn.started", {"turn_id": turn_id, "state": "clarify"})
        await ledger.append(conn, task, "turn.ended", {"turn_id": turn_id, "outcome": "done", "result": {}})


def test_a_team_chat_has_one_chat_session_and_every_message_reaches_it(dsn, team):
    """Anyone's message binds to the chat's one session, started by the
    first with the chat's earlier messages; each is a steer naming its
    sender. A group no spec lists stays unheard."""

    async def go():
        before = (await said(dsn, "morning all", sender="78", name="Kim"))["task_id"]
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, before, reason="a new session")
        first = await said(dsn, "the florist page 500s on mobile")
        second = await said(dsn, "valor can you look?", sender="78", name="Kim")
        other = await said(dsn, "hello valor", chat=OTHER)
        async with await db.connect(dsn) as conn:
            b = await tasks.brief(conn, first["task_id"])
        return first, second, other, b, await rows_of(dsn, first["task_id"])

    first, second, other, b, rows = run(go())
    assert first["task_id"] == second["task_id"] and (first["as"], second["as"]) == ("steer", "steer")
    assert other["as"] == "none"
    assert b.chat == {"channel": "telegram", "chat_id": GROUP} and b.project == {"name": "flowers"}
    assert "flowers team chat on Telegram" in b.instruction and "Kim (message " in b.instruction
    assert "): morning all" in b.instruction
    steered = [r["payload"] for r in rows if r["type"] == "message.steered"]
    assert [(p["provenance"]["by"], p["text"]) for p in steered] == [
        ("Robin", "the florist page 500s on mobile"), ("Kim", "valor can you look?"),
    ]  # fmt: skip


def test_in_a_mentions_chat_only_a_message_naming_valor_wakes_the_session(dsn, team):
    """The rest are heard, quiet, and ride along with the next wake."""

    async def go():
        bound = [(await said(dsn, t, chat=QUIET_GROUP))["as"] for t in ("lunch at noon?", "valorous effort")]
        waiting = session.chat_waiting(
            await rows_of(dsn, (await said(dsn, "x", chat=QUIET_GROUP))["task_id"])
        )
        woke = await said(dsn, "Valor, what broke?", chat=QUIET_GROUP)
        handle = (await said(dsn, "@valorengels can you look?", chat=QUIET_GROUP))["as"]
        rows = await rows_of(dsn, woke["task_id"])
        return bound, waiting, woke, session.chat_waiting(rows), session.chat_prompt(rows), handle

    bound, waiting, woke, now, prompt, handle = run(go())
    assert bound == ["heard", "heard"] and not waiting and handle == "steer"
    assert woke["as"] == "steer" and now
    assert (
        prompt.index("lunch at noon?") < prompt.index("valorous effort") < prompt.index("Valor, what broke?")
    )
    assert "Robin (message " in prompt


def test_someone_elses_dm_a_spec_lists_is_a_chat_every_message_of_theirs_wakes(dsn, team):
    """Not Tom's chat and not mentions only; a DM no spec lists stays unheard."""

    async def go():
        first = await said(dsn, "hi, quick question", chat=KIM_DM, sender=KIM_DM, name="Kim", kind="dm")
        second = await said(dsn, "is the shop open?", chat=KIM_DM, sender=KIM_DM, name="Kim", kind="dm")
        other = await said(dsn, "valor?", chat="79", sender="79", name="Lee", kind="dm")
        async with await db.connect(dsn) as conn:
            return first, second, other, await tasks.brief(conn, first["task_id"])

    first, second, other, b = run(go())
    assert (first["as"], second["as"], other["as"]) == ("steer", "steer", "none")
    assert first["task_id"] == second["task_id"] and b.chat == {"channel": "telegram", "chat_id": KIM_DM}
    assert b.instruction.startswith("You are in a direct message with Kim on Telegram")


def test_a_chat_session_wakes_on_messages_and_reports_and_reads_only_whats_new(dsn, team):
    async def go():
        task = (await said(dsn, "is staging up?"))["task_id"]
        await turn(dsn, task)
        idle = session.chat_waiting(await rows_of(dsn, task))
        await said(dsn, "and prod?")
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "child.reported", {"child_id": "c1", "kind": "question",
                                                               "about_key": "q", "text": "Which region?"})  # fmt: skip
        rows = await rows_of(dsn, task)
        return idle, session.chat_waiting(rows), session.chat_prompt(rows)

    idle, waiting, prompt = run(go())
    assert not idle and waiting
    assert "is staging up?" not in prompt and "and prod?" in prompt
    assert "Your task c1 reports (question):\n  Which region?" in prompt


def test_a_tasks_channel_says_no_one_holds_the_merge_and_each_signal_is_taken(dsn, tmp_path):
    # #3663: a plan's "hold the merge" held nothing; #3665: a revised plan
    # assumed the taken plan.json still stood.
    async def go():
        async with await db.connect(dsn) as conn:
            return (
                await tasks.dispatch(
                    conn, await tasks.start(conn, tasks.Brief(instruction="test", workspace=str(tmp_path)))
                )
            )["text"]

    text = run(go())
    assert "# How this task reaches Tom" in text and text.count("the kernel merges") == 1
    assert "no one approves it, so\n  nothing in a plan, a commit or a note can hold it" in text
    assert "taking each file" in text and "is written again" in text


def test_a_plan_turn_asks_the_session_it_reports_to_before_the_first_plan(dsn, tmp_path):
    # 991c42b25472: a feature plan revised four times and asked nothing,
    # its channel sending product and UX questions to the advisor.
    async def go():
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="test", workspace=str(tmp_path)))
            return (await tasks.dispatch(conn, task, state=State.PLAN))["text"]

    text = " ".join(run(go()).split())
    assert "# Stage: plan" in text and "user-facing behaviour or open product choices" in text
    assert "ask before the first plan is written" in text and "one numbered batch" in text
    assert "A revision whose findings would change a product or UX choice asks" in text
    stakeholder = "the operator chat's), which answers what it can and raises the rest to Tom"
    assert stakeholder in text and "what its users see or hear, who does what" in text
    assert "for anything else, ask the advisor" not in text and "For how to build it, ask the advisor" in text
    clarify = " ".join(tasks.stage_text(State.CLARIFY).split())
    assert "That session answers what it can and raises the rest to Tom" in clarify
    assert "what its users see or hear" in clarify and "on anything else, ask the advisor" not in clarify


def test_a_chat_session_gets_the_converse_channel_and_only_stop_counts(dsn, team, tmp_path):
    from core import signals, verdicts
    from tools import chat_tasks

    async def go():
        async with await db.connect(dsn) as conn:
            b = tasks.Brief(
                instruction="chat", workspace=str(tmp_path), chat={"channel": "telegram", "chat_id": LONE}
            )
            task = await tasks.start(conn, b)
            await verdicts.record_chat(conn, task)
            offered = [p.usage for p in chat_tasks.performers(b)]
            return b, (await tasks.dispatch(conn, task, offered=offered))["text"]

    b, text = run(go())
    assert "# A chat session" in text and "`task.start`" in text and "# Stage: clarify" not in text
    assert "# How this task reaches Tom" not in text
    assert "`yudame-dev:ask-me`" in text and "Anything I missed? If not, I'll go ahead." in text
    assert "one at a time in a live chat" in text and "One batch from a task" in text
    flat = " ".join(text.split())
    assert "Otherwise raise only the rest in the chat" in flat and "pass one `task.answer`" in flat
    assert "only the items left for Tom" in flat and "one line naming where it was asked" in flat
    # A chat session sets a wake on each question: silence is never go on a real question,
    # five quiet minutes are go on the read-back, and a quiet task is checked on.
    assert "`.valor/wake.md` wakes it later" in flat and "set a wake on it" in flat
    assert "silence is never go" in flat and "set a new wake on the same post" in flat
    assert "wake after 5 minutes" in flat and "(`valor-record task <id>`)" in flat
    asked = session._stage_verdict(State.CLARIFY, signals.Signals(question="?"), None, "t", True, b)
    assert asked == ("idle", {}, ["question means nothing in a chat session; ignored"])
    assert (
        session._stage_verdict(State.CLARIFY, signals.Signals(stop="bye"), None, "t", True, b)[0] == "ended"
    )


def test_a_chat_session_drafts_in_the_askers_voice_and_sends_only_when_told(dsn, team, tmp_path):
    """2026-11-01: asked to draft a client email, a chat session wrote it as from Valor,
    ended "Anything I missed? If not, I'll send it" and sent it when Tom was silent."""
    from core import verdicts

    async def go():
        async with await db.connect(dsn) as conn:
            b = tasks.Brief(
                instruction="chat", workspace=str(tmp_path), chat={"channel": "telegram", "chat_id": LONE}
            )
            task = await tasks.start(conn, b)
            await verdicts.record_chat(conn, task)
            return (await tasks.dispatch(conn, task))["text"]

    flat = " ".join(run(go()).split())
    assert "write it in the asker's voice, as from them, and give it back in the chat as text" in flat
    assert "for them to send; send nothing" in flat
    assert "A send to anyone outside the chat goes when the asker says send" in flat
    assert "or on a standing say-so from Tom (a routine he set up)" in flat
    assert "Silence, a wake or a timer is never a yes to a send" in flat
    assert "When Tom says send, send it, with no further check" in flat
    assert "This is for work you build, never for a send" in flat
    # The persona, rendered into every turn, says a draft is the asker's, not words in Tom's mouth.
    assert "A draft someone asks for is theirs: write it in their voice and hand it back as text" in flat
    assert "A draft someone asks for is theirs, in their voice; what you send is yours." in flat
    # A task that drafted a message hands it back as text; only one asked to be sent is an effect.
    for state in (State.CLARIFY, State.PLAN, State.BUILD):
        stage = " ".join(tasks.stage_text(state).split())
        assert "email asked to be drafted goes in the note as text in the asker's voice" in stage
        assert "One asked to be sent goes in the note as its effect" in stage


def test_an_email_is_drafted_in_full_then_edited_against_the_guide(dsn, team, tmp_path):
    """Tom, 2026-11-09: never one-shot an email from the guide; write it long, then at
    least one editorial pass against it (more when the stakes are high), then de-slop.
    The chat session and every task stage (patch included, via channel.md) say so."""
    from core import verdicts

    async def go():
        async with await db.connect(dsn) as conn:
            b = tasks.Brief(
                instruction="chat", workspace=str(tmp_path), chat={"channel": "telegram", "chat_id": LONE}
            )
            task = await tasks.start(conn, b)
            await verdicts.record_chat(conn, task)
            return (await tasks.dispatch(conn, task))["text"]

    flow = (
        "An email, drafted for someone or sent, follows the email skill's flow (`yudame:email`): the full "
        "draft first, then at least one editorial pass against its guide (more when the stakes are high), "
        "then de-slop."
    )
    chat = " ".join(run(go()).split())
    assert flow in chat
    # Placed above both the draft rule and the email.send reply, so it covers each.
    assert (
        chat.index(flow) < chat.index("A draft is text; a send is said") < chat.index("request `email.send`")
    )
    assert flow in " ".join(tasks.channel_text([]).split())


def test_the_persona_runs_projects_and_writes_for_how_technical_the_reader_is():
    """Tom, 2026-11-06 (#makes-good-project): project-manager heuristics are general
    practice in every room; a repo path to a CEO is as bad as vagueness to an engineer;
    clarity above completeness; nothing stalls waiting on one person."""
    from core import persona

    flat = " ".join(persona.render(REPO / "persona").split())
    assert "adjusted for length and for how technical the reader is, never for identity" in flat
    assert "with no file paths, repo or branch names" in flat
    assert "To an engineer: exact, with the file and line, the command, the error" in flat
    assert "what reaches a person is retold in the reader's terms" in flat
    assert "Clarity above completeness" in flat
    assert "Few, easy questions" in flat and "answerable in a word or a pick" in flat
    for heading in (
        "Run the project", "No surprises.", "Unknowns first.", "Fixed time, variable scope.",
        "A date is small steps and names who it waits on.", "Act, don't lament.",
        "Unblock first; close every loop.", "Nothing stalls on one person.",
    ):
        assert heading in flat
    # The product methods are general practice (Tom, 2026-11-06), one copy: the persona's.
    for method in (
        "Jobs to be Done, as the inclusion test.", "Shape before building.",
        "Value for effort, in coarse bands.", "One ordered queue.", "Ready before build.", "Nothing new is built until", "A defect or mandatory fix is not a bet",
        "Ask plainly; apply the methods yourself.", "Never ask in their vocabulary",
    ):
        assert method in flat
    product = " ".join(tasks.role_text("product").split())
    assert 'persona\'s "Run the project"' in product and "Jobs to be Done, as the inclusion test" not in product
    # Waiting on someone: a day of their working day, a reminder, the next best person;
    # a two-way door goes ahead on its default, a one-way door waits (Tom, 2026-11-06).
    assert "Give them a day of their own working day (mind their time zone), then one reminder" in flat
    assert "A one-way door waits for its answer" in flat
    chat = " ".join((REPO / "skills/sdlc/converse.md").read_text().split())
    assert 'as your persona\'s "Nothing stalls on one person" says' in chat



def test_only_decisions_the_person_holds_reach_a_call_or_the_board():
    """Tom's first full call (2026-11-06): of four items, one was Valor's own
    product work ("entirely on your end as a project manager") and one a blocker
    on someone else with nothing for him; a task's question rested on a premise
    its own findings had ruled out."""
    from core import persona

    chat = " ".join((REPO / "skills/sdlc/converse.md").read_text().split())
    assert "Its questions are only decisions the people in the call hold" in chat
    assert "your own work (a Sheet row, a ranking, a draft) is done before the call and listed as done" in chat
    assert "a blocker on someone else is status, with who and since when" in chat
    assert "Open holds only what Tom holds" in chat and '(`task.answer`, `"by": "valor"`)' in chat
    product = " ".join(tasks.role_text("product").split())
    assert "You draft them from what the people who own the product have said, set them" in product
    assert "they correct or override them, and their word replaces yours" in product
    assert "Adding, rating, ranking and blocking rows are yours and never a question" in product
    sheet = " ".join((REPO / "skills/valor/skills/product-sheet/SKILL.md").read_text().split())
    assert "Valor drafts the goal and metric" in sheet and "never fills them in alone" not in sheet
    flat = " ".join(persona.render(REPO / "persona").split())
    assert "A question your own findings answer, or show does not arise, is not asked." in flat

def test_stakes_json_is_read_in_place_and_from_handled_and_only_as_an_object(tmp_path):
    v = tmp_path / ".valor"
    v.mkdir()
    (v / "stakes.json").write_text(json.dumps({"stakes": "stored data", "review_rounds": 1}))
    first = signals.collect(tmp_path, "t1")
    again = signals.recollect(tmp_path, "t1")  # the file is filed away: read from handled/
    assert first.stakes == again.stakes == {"stakes": "stored data", "review_rounds": 1}
    (v / "stakes.json").write_text("[1]")
    bad = signals.collect(tmp_path, "t2")
    assert bad.stakes is None and len(bad.unreadable) == 1 and "stakes.json is unreadable" in bad.unreadable[0]
    assert signals.collect(tmp_path, "t3").stakes is None


def test_a_rooms_entry_matches_its_chat_as_chats_normalises_it():
    spec = workspace.Spec.from_dict(
        {
            "name": "p",
            "repo": "r",
            "kind": "plain",
            "suite": "true",
            "chats": ["slack:t0abc/c0123", "telegram:-1001"],
            "rooms": {"slack:t0abc/c0123": " Support. ", "telegram:-1001": "Engineering."},
        }
    )
    assert spec.chats == ("slack:T0ABC/C0123", "telegram:-1001")
    assert spec.rooms == {"slack:T0ABC/C0123": "Support.", "telegram:-1001": "Engineering."}
    assert workspace.Spec.from_dict({"name": "p", "repo": "r", "kind": "plain", "suite": "true"}).rooms == {}


def test_each_chat_gets_its_rooms_entry_else_tom_owns_or_emails_else_none(tmp_path):
    tom_slack = "T0ABC/U0TOM"
    with bridges.operator(tmp_path, operator_slack_ids=(tom_slack,)):
        projects = pathlib.Path(settings.projects_dir)
        (projects / "valor.toml").write_text(
            bridges.VALOR_SPEC
            + f'chats = ["telegram:{bridges.OPERATOR_CHAT}", "telegram:{bridges.OPERATOR}", "telegram:{GROUP}", '
            f'"telegram:{QUIET_GROUP}"]\n'
            f'rooms."telegram:{bridges.OPERATOR_CHAT}" = "Status."\nrooms."telegram:{GROUP}" = "Engineering."\n'
        )
        (projects / "far.toml").write_text(
            'name = "far"\nrepo = "r"\nkind = "plain"\nsuite = "true"\nmachine = "elsewhere"\n'
            f'chats = ["telegram:{OTHER}"]\nrooms = {{ "telegram:{OTHER}" = "Far away." }}\n'
        )
        assert intake.room("telegram", GROUP) == "Engineering."
        # The operator group is Tom's own too; its own entry wins.
        assert intake.room("telegram", bridges.OPERATOR_CHAT) == "Status."
        for channel, chat in (
            ("telegram", bridges.OPERATOR),
            ("slack", tom_slack),
            ("local", intake.LOCAL_CHAT),
        ):
            assert intake.room(channel, chat) == intake.TOMS_OWN_ROOM
        assert intake.room("email", "<root@example.com>") == intake.EMAIL_ROOM
        assert intake.room("telegram", QUIET_GROUP) is None  # listed, no entry
        assert intake.room("telegram", LONE) is None  # listed nowhere
        assert intake.room("telegram", OTHER) is None  # another machine's


def test_a_chat_sessions_every_turn_says_what_its_room_is_for(dsn, tmp_path):
    group = f"-{uuid.uuid4().int % 10**12}"  # its own chat: the ledger is shared across tests

    async def text(task, **kw):
        async with await db.connect(dsn) as conn:
            return (await tasks.dispatch(conn, task, **kw))["text"]

    async def start(brief):
        async with await db.connect(dsn) as conn:
            return await tasks.start(conn, brief)

    with bridges.operator(tmp_path):
        spec = pathlib.Path(settings.projects_dir) / "flowers.toml"
        spec.write_text(
            f'name = "flowers"\nrepo = "r"\nkind = "plain"\nsuite = "true"\nchats = ["telegram:{group}"]\n'
        )
        chat = run(
            start(
                tasks.Brief(
                    instruction="chat",
                    workspace=str(tmp_path),
                    chat={"channel": "telegram", "chat_id": group},
                )
            )
        )
        plain = run(start(tasks.Brief(instruction="work", workspace=str(tmp_path))))
        # No description yet: the opening's project and kind stay the fallback.
        assert "\nRoom: " not in run(text(chat))
        spec.write_text(spec.read_text() + f'rooms = {{ "telegram:{group}" = "The florist\'s site." }}\n')
        # The open session gets it at its next turn, and every turn after.
        for _ in range(2):
            assert "Instruction: chat\nRoom: The florist's site.\nEffect ceiling:" in run(text(chat))
        # A fresh session of the chat task carries it too: it is the chat's.
        assert "Room: The florist's site." in run(text(chat, fresh="advice"))
        assert "\nRoom: " not in run(text(plain))


def test_a_chat_roles_entry_matches_its_chat_as_chats_normalises_it():
    spec = workspace.Spec.from_dict(
        {
            "name": "p",
            "repo": "r",
            "kind": "plain",
            "suite": "true",
            "chats": ["slack:t0abc/c0123", "telegram:-1001"],
            "chat_roles": {"slack:t0abc/c0123": " product ", "telegram:-1001": "engineer", "email": "teammate"},
        }
    )
    assert spec.chat_roles == {"slack:T0ABC/C0123": "product", "telegram:-1001": "engineer", "email": "teammate"}
    assert workspace.Spec.from_dict({"name": "p", "repo": "r", "kind": "plain", "suite": "true"}).chat_roles == {}


def test_each_role_file_renders_and_the_example_spec_names_only_roles_that_exist():
    example = workspace.Spec.from_dict(tomllib.loads((REPO / "projects" / "example.toml").read_text()))
    names = {p.stem for p in (pathlib.Path(settings.stages_dir) / "roles").glob("*.md")}
    assert names == {"engineer", "teammate", "customer-service", "product", "business"}
    assert set(example.chat_roles.values()) <= names
    for name in names:
        assert tasks.role_text(name).startswith("# Your role here: ")


def test_a_chat_sessions_turn_plays_its_chats_role_and_toms_own_chat_none(dsn, tmp_path):
    group, quiet = (f"-{uuid.uuid4().int % 10**12}" for _ in range(2))

    async def text(task, **kw):
        async with await db.connect(dsn) as conn:
            return (await tasks.dispatch(conn, task, **kw))["text"]

    async def start(chat, project=None):
        async with await db.connect(dsn) as conn:
            return await tasks.start(
                conn, tasks.Brief(instruction="chat", workspace=str(tmp_path), chat=chat, project=project)
            )

    with bridges.operator(tmp_path):
        (pathlib.Path(settings.projects_dir) / "flowers.toml").write_text(
            f'name = "flowers"\nrepo = "r"\nkind = "plain"\nsuite = "true"\n'
            f'chats = ["telegram:{group}", "telegram:{quiet}"]\n'
            f'chat_roles."telegram:{group}" = "product"\nchat_roles.email = "teammate"\n'
        )
        played = run(start({"channel": "telegram", "chat_id": group}, {"name": "flowers"}))
        none = run(start({"channel": "telegram", "chat_id": quiet}, {"name": "flowers"}))
        mail = run(start({"channel": "email", "chat_id": "<root@example.com>"}, {"name": "flowers"}))
        own = run(start({"channel": "telegram", "chat_id": bridges.OPERATOR}, {"name": "flowers"}))
        found = run(text(played))
        assert found.index("# A chat session") < found.index(tasks.role_text("product"))
        assert tasks.role_text("teammate") in run(text(mail))
        for task in (none, own):
            assert "# Your role here: " not in run(text(task))
        assert intake.role("telegram", bridges.OPERATOR, "flowers") is None


def test_task_start_s_context_is_in_every_turn_of_the_task_it_starts(dsn, tmp_path):
    """The lines a chat session writes as `task.start`'s `context` are the
    child's `Context:`, under its instruction, in its working turns and a
    fresh session's (review), and with `in` in a sibling chat's child; none,
    or only whitespace, renders no line."""
    from tools import chat_tasks

    def chat():
        return f"T0ABC/C{uuid.uuid4().int % 10**9}"

    room = "From #product (slack), asked by Tom. May open a PR changing only prompt text; must not merge."

    async def go(here, dev):
        (pathlib.Path(settings.projects_dir) / "flowers.toml").write_text(
            f'name = "flowers"\nrepo = "r"\nkind = "plain"\nsuite = "true"\nchats = ["slack:{here}", "slack:{dev}"]\n'
        )
        async with await db.connect(dsn) as conn:
            session_id = await tasks.start(
                conn,
                tasks.Brief(instruction="chat", chat={"channel": "slack", "chat_id": here},
                            project={"name": "flowers"}),
            )  # fmt: skip
            start = chat_tasks.performers(await tasks.brief(conn, session_id), dsn)[0]
        made = []
        for extra in ({"context": f"  {room}\n"}, {}, {"context": " \n "}, {"context": room, "in": f"slack:{dev}"}):
            action = broker.Action("task.start", "work", {"instruction": "Reword the greeting", **extra})
            made.append((await start.perform(action, f"cx-{uuid.uuid4()}"))["task_id"])
        async with await db.connect(dsn) as conn:
            briefs = [await tasks.brief(conn, c) for c in made]
            texts = [(await tasks.dispatch(conn, c))["text"] for c in made]
            review = (await tasks.dispatch(conn, made[0], fresh="review"))["text"]
        return briefs, texts, review

    with bridges.operator(tmp_path):
        briefs, texts, review = run(go(chat(), chat()))
    assert [b.context for b in briefs] == [room, None, None, room]
    line = f"Instruction: Reword the greeting\nContext: {room}\nEffect ceiling:"
    assert line in texts[0] and line in texts[3] and line in review
    assert "\nContext: " not in texts[1] and "\nContext: " not in texts[2]
    assert tasks.Brief.load({"instruction": "older"}).context is None


def test_task_start_in_another_chat_starts_under_that_chats_session_and_reads_it(dsn, tmp_path):
    """`in` names a chat of the session's project: the child starts under
    that chat's open session, one opened when it has none, reads that chat
    and reports to its session; on its board only when that chat is the
    board. A chat the project does not list fails with the reason."""
    from core import board
    from tools import chat_tasks

    def chat():
        return f"T0ABC/C{uuid.uuid4().int % 10**9}"

    async def go(product, dev, board_chat):
        (pathlib.Path(settings.projects_dir) / "flowers.toml").write_text(
            f'name = "flowers"\nrepo = "r"\nkind = "plain"\nsuite = "true"\n'
            f'chats = ["slack:{product}", "slack:{dev}"]\nboard = "slack:{board_chat}"\n'
        )
        async with await db.connect(dsn) as conn:
            session_id = await tasks.start(
                conn,
                tasks.Brief(instruction="chat", chat={"channel": "slack", "chat_id": product},
                            project={"name": "flowers"}),
            )  # fmt: skip
            b = await tasks.brief(conn, session_id)
        start = chat_tasks.performers(b, dsn)[0]
        made = []
        for n in (1, 2):
            action = broker.Action("task.start", "work", {"instruction": f"Build {n}", "in": f"slack:{dev.lower()}"})
            made.append((await start.perform(action, f"in-{uuid.uuid4()}"))["task_id"])
        with pytest.raises(ValueError, match="not one of this project's chats"):
            await start.perform(
                broker.Action("task.start", "work", {"instruction": "x", "in": f"slack:{chat()}"}), "in-x"
            )
        async with await db.connect(dsn) as conn:
            dev_session = await intake.chat_session(conn, "slack", dev)
            briefs = [await tasks.brief(conn, c) for c in made]
            reports = [await notices.hears(conn, c, "question") for c in made]
            on_board = await board.here(conn, made[0])
        return session_id, dev_session, briefs, reports, on_board

    with bridges.operator(tmp_path):
        product, dev = chat(), chat()
        session_id, dev_session, briefs, reports, on_board = run(go(product, dev, dev))
        assert dev_session is not None and dev_session != session_id
        assert [b.parent_id for b in briefs] == [dev_session] * 2  # one session, opened once
        assert [b.project for b in briefs] == [{"name": "flowers", "chat": f"slack:{dev}"}] * 2
        assert reports == [dev_session] * 2 and on_board is True
        product = chat()
        *_, on_board = run(go(product, chat(), product))  # the dev chat is not the board
        assert on_board is False


def test_a_chat_session_starts_answers_and_stops_its_tasks_which_report_only_to_it(dsn, team):
    """`task.start` makes a child on the project, named by who asked; its
    question reaches the session, never a chat, and `task.answer` answers
    it. The push credential request reaches it too, naming no task it can
    stop; a task not the session's is refused."""
    from tools import chat_tasks

    async def go():
        session_id = (await said(dsn, "valor, bump the stripe sdk"))["task_id"]
        async with await db.connect(dsn) as conn:
            b = await tasks.brief(conn, session_id)
        start, answer, _, stop = chat_tasks.performers(b, dsn)
        made = await start.perform(
            broker.Action("task.start", "work", {"instruction": "Bump stripe", "by": "Robin"}), "k1"
        )
        child = made["task_id"]
        async with await db.connect(dsn) as conn:
            cb = await tasks.brief(conn, child)
            for row in Ledger(judge="thin").ask().rows[1:]:
                await ledger.append(conn, child, row["type"], row["payload"])
            await notices.owe(conn, child)
            await notices.credential(conn, child, "abc", missing=True)
            refused = await answer.refuse(conn, broker.Action("task.answer", session_id, {"text": "x"}))
        await answer.perform(broker.Action("task.answer", child, {"text": "v12", "by": "Kim"}), "k2")
        found = await start.lookup(broker.Action("task.start", "work", {}), "k1")
        await stop.perform(broker.Action("task.stop", child, {"why": "not now", "by": "Robin"}), "k3")
        return session_id, child, cb, refused, found, *[await rows_of(dsn, t) for t in (session_id, child)]

    session_id, child, cb, refused, found, parent_rows, child_rows = run(go())
    # The child carries the chat it came from, which it reads (`_reading_env`).
    assert cb.parent_id == session_id and cb.project == {"name": "flowers", "chat": "telegram:-1001"}
    assert found == {"task_id": child}
    (started,) = [r["payload"] for r in child_rows if r["type"] == "task.started"]
    assert started["provenance"]["by"] == "Robin"
    reported = [(p["kind"], p["child_id"]) for p in bridges.typed(parent_rows, "child.reported") if p.get("from") == child]
    assert reported == [("question", child), ("credential", None)]
    assert not [r for r in child_rows if r["type"] == "notice.requested"]
    (answered,) = [r["payload"] for r in child_rows if r["type"] == "question.answered"]
    assert (answered["text"], answered["provenance"]["by"]) == ("v12", "Kim")
    assert refused == f"task {session_id} does not report to this chat"
    assert machine.fold(child_rows).state is State.STOPPED


def test_only_a_chat_session_is_offered_or_may_request_a_send(dsn, team):
    """Tom, 2026-11-01: "No routine or system should be impersonating
    you." A routine's run, or any task that is no chat session, has no
    send or canvas type among its performers, so its turn is told of none
    and a request for one is refused as unknown; a chat session has every
    one. An SDLC task is no different: it may not request `email.send`
    either (docs/plans/one-voice-mail.md). A send owes no report: the
    session that sent it knows."""
    from core import __main__ as composition
    from core.bridge import DECLARED

    sends = {t for t, d in DECLARED.items() if d.owner != "meet"}

    async def go():
        session_id = (await said(dsn, "valor, tell the florist we're live"))["task_id"]
        objective = await new_task(dsn, routine="sentry")
        run_ = await new_task(dsn, parent_id=objective, routine="sentry", max_effect_class="act")
        sdlc = await new_task(dsn, max_effect_class="act")
        mail = broker.Action("email.send", "x@florist.test", {"text": "We're live.", "to": ["x@florist.test"]})
        slack = broker.Action("slack.send_message", "T0ABC/U1", {"text": "hi"})
        async with await db.connect(dsn) as conn:
            briefs = [await tasks.brief(conn, t) for t in (session_id, run_, sdlc)]
            built = [composition._performers(b) for b in briefs]
            effect = {"effect_id": ledger.new_id(), "effect_class": "act", "action_type": "email.send",
                      "target": "x@florist.test", "payload": {"text": "We're live.", "to": ["x@florist.test"]}}  # fmt: skip
            owed = await notices.report(conn, session_id, effect)
            refused = [
                await broker.request(conn, built[1], run_, slack),
                await broker.request(conn, built[1], run_, mail),
                await broker.request(conn, built[2], sdlc, mail),
            ]
        return built, owed, refused

    (session_p, *tasks_p), owed, refused = run(go())
    assert {t for t in sends if session_p.get(t)} == sends and "email.send" in sends
    for task_p in tasks_p:
        assert not any(task_p.get(t) for t in sends)
        assert not any(t in line for line in task_p.offered("act") for t in sends)
    assert owed is None and [(r.kind, r.error) for r in refused] == [("refused", broker.NO_PERFORMER)] * 3


# -- an email thread: a chat with its own session ------------------------------


@pytest.fixture
def mail(tmp_path):
    """Tom's address, and a project that lists a client's."""
    with bridges.operator(tmp_path):
        projects = pathlib.Path(settings.projects_dir)
        (projects / "valor.toml").write_text(bridges.VALOR_SPEC)
        (projects / "flowers.toml").write_text(
            'name = "flowers"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\nchats = ["email:kim@florist.test"]\n'
        )
        (projects / "garden.toml").write_text(
            'name = "garden"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\n'
            'chats = ["email:@florist.test", "email:@garden.test"]\n'
        )
        yield


async def emailed(dsn, text, *, sender=bridges.OPERATOR_EMAIL, name="Tom", thread=()) -> dict:
    """An email received and bound, in reply to the last of `thread`: its
    binding, with the email's id."""
    mid = f"<{uuid.uuid4().hex}@mail.test>"
    inbound = intake.Inbound(
        channel="email", chat_id=thread[0] if thread else mid, chat_kind="email", message_id=mid,
        sender_id=sender, sender_name=name, sent_at="2026-10-17T09:30:00+00:00", text=text,
        reply_to=thread[-1] if thread else None, thread=[{"id": i} for i in thread],
    )  # fmt: skip
    async with await db.connect(dsn) as conn:
        got = await intake.receive(conn, inbound)
        await intake.bind(conn)
    (bound,) = await bridges.of_type(dsn, "message.bound", received_id=got.received_id)
    bound["message_id"] = mid
    return bound


def test_an_email_domain_entry_takes_every_sender_at_exactly_that_domain(dsn, mail):
    """An exact address listed elsewhere wins over the domain."""
    assert intake.owns("email", "Ann@Garden.test") and intake.owns("email", "kim@florist.test")
    assert not intake.owns("email", "x@evilgarden.test") and not intake.owns("email", "x@garden.test.evil")

    async def go():
        ann = await emailed(dsn, "Beds\n\nmulch?", sender="ann@garden.test", name="Ann")
        kim = await emailed(dsn, "Hours\n\nopen?", sender="kim@florist.test", name="Kim")
        async with await db.connect(dsn) as conn:
            return [(await tasks.brief(conn, x["task_id"])).project for x in (ann, kim)]

    assert run(go()) == [{"name": "garden"}, {"name": "flowers"}]


YUDAME_SLACK = "T0D654H7PPX"
TOM_SLACK = f"{YUDAME_SLACK}/U0B9THUG5DN"


def placed_specs(root: pathlib.Path) -> dict[str, workspace.Spec]:
    """Made-up specs on two Macs: valor on the Cowboy with Tom's Slack DM
    and its dev channel, a client's on the Pirate with its own."""
    root.mkdir(exist_ok=True)
    base = 'repo = "r"\nkind = "plain"\nsuite = "true"\n'
    (root / "valor.toml").write_text(
        f'name = "valor"\n{base}machine = "Valor the Cowboy"\n'
        f'chats = ["slack:{TOM_SLACK}", "slack:{YUDAME_SLACK}/C0VALORDEV", "telegram:-1009000000002"]\n'
    )
    (root / "client.toml").write_text(
        f'name = "client"\n{base}machine = "Valor the Pirate"\nchats = ["slack:{YUDAME_SLACK}/C0CLIENTDEV"]\n'
    )
    with bridges.configure(projects_dir=str(root)):
        return {s.name: s for s in workspace.Spec.all()}


def test_toms_slack_dm_is_owned_only_on_the_mac_valor_is_placed_on(tmp_path):
    """Tom's ids alone own nothing, so a second Mac's Slack bridge never
    takes his DM; each Mac's bridge owns its own projects' dev channels."""
    root = tmp_path / "projects"
    specs = placed_specs(root)
    owned = {}
    for mac in ("Valor the Cowboy", "Valor the Pirate"):
        with bridges.configure(projects_dir=str(root), machine=mac, default_machine=mac,
                               operator_slack_ids=(TOM_SLACK,), operator_channel="telegram"):  # fmt: skip
            owned[mac] = set(intake.owned("slack"))
    assert TOM_SLACK in owned["Valor the Cowboy"] and TOM_SLACK not in owned["Valor the Pirate"]
    for name, spec in specs.items():
        slack = {c.removeprefix("slack:") for c in spec.chats if c.startswith("slack:")}
        mine = spec.machine if spec.machine in owned else None
        for mac, ids in owned.items():
            assert (slack <= ids) if mac == mine else not (slack & ids), (name, mac)


def test_toms_dm_as_operator_chat_has_a_session_only_on_the_mac_its_spec_is_placed_on(dsn, tmp_path):
    """Every Mac's kernel names Tom's Slack DM as its operator chat. Only
    the Cowboy, where `valor.toml` lists the DM, opens a session there; on
    the Pirate a root task's report has no session to hear it and stays in
    the task's own record, quiet: the kernel never posts to the DM itself.
    A group operator chat no spec lists keeps its session."""
    root = tmp_path / "projects"
    placed_specs(root)

    def mac(name, **more):
        return bridges.configure(**{
            "projects_dir": str(root), "work_dir": str(tmp_path / "work"), "machine": name, "default_machine": name,
            "operator_telegram_id": "963835355", "operator_slack_ids": (TOM_SLACK,),
            "operator_channel": "slack", "operator_chat": TOM_SLACK, **more,
        })  # fmt: skip

    async def go():
        task = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            with mac("Valor the Pirate"):
                assert TOM_SLACK in intake.owned("slack")  # the kernel's own view, which owns nothing by it
                pirate = await intake.operator_session(conn)
                await notices.request(conn, task, kind="ended", about_key="ended:1", text="Stopped.")
            with mac("Valor the Pirate", operator_channel="telegram", operator_chat="-1009999"):
                group = await intake.operator_session(conn)
            with mac("Valor the Cowboy"):
                cowboy = await intake.operator_session(conn)
                b = await tasks.brief(conn, cowboy)
        return pirate, group, cowboy, b, task, await bridges.rows(dsn, task)

    pirate, group, cowboy, b, task, rows = run(go())
    assert pirate is None and group is not None and cowboy is not None
    assert b.chat == {"channel": "slack", "chat_id": TOM_SLACK}
    (held,) = bridges.typed(rows, "child.reported")
    assert (held["from"], held["kind"], held["quiet"]) == (task, "ended", True)
    assert bridges.typed(rows, "notice.requested") == []


def test_in_a_slack_dev_channel_every_message_wakes_the_session_and_tom_is_tom(dsn, tmp_path):
    """As in an Eng group: anyone's message steers the one session, naming
    its sender; Tom, by his Slack id, is `tom`."""
    dev, tom = "T0ABC/C0DEV", "T0ABC/U0TOM"
    with bridges.operator(tmp_path, operator_slack_ids=(tom,)):
        (pathlib.Path(settings.projects_dir) / "flowers.toml").write_text(
            'name = "flowers"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\n'
            f'chats = ["slack:{dev}"]\nrooms."slack:{dev}" = "Engineering on the florist site."\n'
        )

        async def says(text, sender, name, ts):
            inbound = intake.Inbound(
                channel="slack", chat_id=dev, chat_kind="group", message_id=ts, sender_id=sender,
                sender_name=name, sent_at="2026-10-25T11:00:00Z", text=text,
            )  # fmt: skip
            async with await db.connect(dsn) as conn:
                got = await intake.receive(conn, inbound)
                await intake.bind(conn)
            (bound,) = await bridges.of_type(dsn, "message.bound", received_id=got.received_id)
            return bound

        async def go():
            first = await says("morning", "T0ABC/U0KIM", "Kim", at(1))
            said = [await says(t, who, name, at(n)) for n, (t, who, name) in enumerate(
                [("the checkout 500s", tom, "Tom"), ("I see it too", "T0ABC/U0KIM", "Kim")], 2)]  # fmt: skip
            async with await db.connect(dsn) as conn:
                b = await tasks.brief(conn, first["task_id"])
            return first, said, b, await rows_of(dsn, first["task_id"])

        first, said, b, rows = run(go())
        room = intake.room("slack", dev)
    assert [x["as"] for x in said] == ["steer", "steer"] and {x["task_id"] for x in said} == {first["task_id"]}
    assert b.chat == {"channel": "slack", "chat_id": dev} and room == "Engineering on the florist site."
    steered = [r["payload"]["provenance"]["by"] for r in rows if r["type"] == "message.steered"]
    assert steered == ["Kim", "tom", "Kim"]


def test_an_email_thread_is_a_chat_whose_session_every_email_in_it_steers(dsn, mail):
    """Each thread has one session, under the sender's project, else
    `valor`; an email names its sender, never Tom, so `stop` steers."""

    async def go():
        first = await emailed(dsn, "The florist page\n\nit 500s on mobile")
        root = first["message_id"]
        second = await emailed(dsn, "Re: The florist page\n\nstop", thread=(root,))
        empty = await emailed(dsn, "", thread=(root, second["message_id"]))
        client = await emailed(dsn, "Hours\n\nopen Sunday?", sender="kim@florist.test", name="Kim")
        task = first["task_id"]
        async with await db.connect(dsn) as conn:
            b, c = await tasks.brief(conn, task), await tasks.brief(conn, client["task_id"])
        return first, second, empty, client, b, c, await rows_of(dsn, task)

    first, second, empty, client, b, c, rows = run(go())
    assert (first["as"], second["as"], empty["as"]) == ("steer", "steer", "none")
    assert first["task_id"] == second["task_id"] != client["task_id"]
    assert b.chat == {"channel": "email", "chat_id": first["message_id"]} and b.project == {"name": "valor"}
    assert b.instruction.startswith("You are in an email thread") and c.project == {"name": "flowers"}
    steered = [r["payload"] for r in rows if r["type"] == "message.steered"]
    assert [p["provenance"]["by"] for p in steered] == ["Tom", "Tom"]
    assert machine.fold(rows).state is not State.STOPPED


def test_an_email_send_in_doubt_wakes_the_session_that_sent_it(dsn, mail):
    """The email bridge sends nothing of its own: a send in doubt is a
    report to the session that requested it, once, which wakes it."""
    from bridges.email import EmailBridge

    async def go():
        first = await emailed(dsn, "The florist page\n\nit 500s", sender="kim@florist.test", name="Kim")
        task, effect = first["task_id"], ledger.new_id()
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "effect.held", {"effect_id": effect, "action_type": "email.send",
                                "target": "kim@florist.test", "payload": {"to": ["kim@florist.test"]}})  # fmt: skip
            b = EmailBridge(SimpleNamespace(address="valor@yuda.me"), dsn)
            for _ in range(2):
                await b.in_doubt(conn, effect)
        return task, await rows_of(dsn, task)

    task, rows = run(go())
    (doubt,) = [p for p in bridges.typed(rows, "child.reported") if p["kind"] == "send_in_doubt"]
    assert doubt["from"] == task and doubt["child_id"] is None and not doubt.get("quiet")
    assert "may or may not have gone" in doubt["text"] and session.chat_waiting(rows)


def test_provisioning_fetches_as_valor_when_the_mac_has_the_github_key(tmp_path, monkeypatch):
    # A private repository is fetched with Valor's GitHub key; without it, anonymously.
    passfile = tmp_path / "keys" / "pgpass"
    passfile.parent.mkdir()
    monkeypatch.setattr(workspace, "settings", dataclasses.replace(settings, pg_passfile=str(passfile)))
    carried = []

    def cache(spec, source, work, *, credential=None, loopback=False):
        carried.append(credential)
        raise workspace.Refused("stop")

    monkeypatch.setattr(workspace, "_cache", cache)
    spec = workspace.Spec(
        name="p", repo="https://github.com/yudame/private.git", kind="python-uv", suite="true"
    )
    with pytest.raises(workspace.Refused):
        workspace.fetch_tip(spec, tmp_path)
    (passfile.parent / "github-keys").write_text("GITHUB_PUSH_TOKEN=x\n")
    with pytest.raises(workspace.Refused):
        workspace.fetch_tip(spec, tmp_path)
    with pytest.raises(workspace.Refused):
        workspace._provision(workspace.Layout(tmp_path / "t"), "t", spec, {}, base=None, source=None)
    key = str(passfile.parent / "github-keys")
    assert carried == [None, key, key]


def test_a_collected_row_postgres_refuses_whole_is_reduced_to_what_it_stores(dsn):
    """`_reduce` drops the largest part left until the row is stored; a
    signal the row never holds (`compact.md`) is no part of it."""

    async def go():
        task = await new_task(dsn)
        refused = session._collected("t", State.BUILD, signals.Signals(done="a\u0000b"), "candidate", None, [], [])
        async with await db.connect(dsn) as conn:
            return await session._reduce(conn, task, refused, True, "a NUL")

    row = run(go())
    assert row["done"] is None and row["verdict"] == "idle" and "done.md dropped" in row["errors"][-1]
