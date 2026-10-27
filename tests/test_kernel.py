"""The ledger, effects, stop, and the binding of Tom's own chat and of team
chats to their chat sessions, on real Postgres, with no model call."""

import asyncio
import dataclasses
import json
import os
import pathlib
import subprocess
import sys
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
    slot,
    tasks,
    workspace,
)
from core.machine import State
from core.settings import settings
from tests import bridges, scripted
from tests.performers import OutboxAppend, WorkspaceWrite
from tests.test_machine import Ledger


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
    dot = tmp_path / "src" / "psyoptimal" / ".env"
    dot.parent.mkdir(parents=True)
    names = ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "GITHUB_PUSH_TOKEN", "PGPASSWORD")
    dot.write_text("".join(f"{n}=dev-{n}\n" for n in names) + "export DATABASE_URL='postgres://dev/db'\n")
    lay = workspace.Layout(tmp_path / "t")
    spec = workspace.Spec(
        name="p", repo="https://github.com/yudame/psyoptimal.git", kind="python-uv", suite="true"
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
    assert task["GH_REPO"] == "yudame/psyoptimal" and not any(v.startswith("dev-") for v in task.values())

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
    assert without == {**workspace.turn_environment({"env": bare}), "GH_REPO": "yudame/psyoptimal"}
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


async def posted(dsn, task, chat, ts, text, *, question=False) -> None:
    """Valor's post in a Slack chat: a send, or the notice of the task's
    open question sent there directly."""
    entries = [{"channel": "slack", "chat_id": chat, "message_id": ts, "thread_ts": None}]
    async with await db.connect(dsn) as conn:
        if question:
            about = f"question:{machine.fold(await ledger.read(conn, task)).open_question}"
            nid = await notices.request(
                conn, task, kind="question", about_key=about, text=text, channel="slack", chat_id=chat
            )
            await ledger.append(conn, task, "notice.sent", {"notice_id": nid, "sent": entries})
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
            sender_name="Lewis", sent_at="2026-10-23T11:00:00Z", text=slack_inbound.named(text, "UVALOR"), **kw,
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


def test_replies_bind_to_an_older_task_another_chats_session_or_this_chats_session(dsn, dm):
    """A threaded reply to a question sent before the DM had a session
    answers it; a reply to a team session's post steers that session, as
    any reply binds; a reply to this DM session's own post is its next
    prompt."""

    async def go():
        waiting = await asking(dsn)
        await posted(dsn, waiting, dm, at(10), "Which colour?", question=True)
        answer = await tom_says(dsn, dm, "blue", at(20), reply_to=at(10), topic_id=at(10))
        team_chat = {"channel": "slack", "chat_id": f"T0ABC/C{uuid.uuid4().hex[:8].upper()}"}
        team = await new_task(dsn, chat=team_chat, max_effect_class="act")
        await posted(dsn, team, dm, at(30), "From the team chat: can you confirm?")
        to_team = await tom_says(dsn, dm, "confirmed", at(40), reply_to=at(30))
        chat = (await tom_says(dsn, dm, "hello", at(50)))["task_id"]
        await posted(dsn, chat, dm, at(60), "Hello Tom.")
        thanks = await tom_says(dsn, dm, "thanks", at(70), reply_to=at(60))
        return waiting, answer, team, to_team, chat, thanks, await bridges.rows(dsn, waiting)

    waiting, answer, team, to_team, chat, thanks, rows_ = run(go())
    assert (answer["task_id"], answer["as"]) == (waiting, "answer")
    assert [r["payload"]["text"] for r in rows_ if r["type"] == "question.answered"] == ["blue"]
    assert (to_team["task_id"], to_team["as"]) == (team, "steer")
    assert chat not in (waiting, team) and (thanks["task_id"], thanks["as"]) == (chat, "steer")


def test_stop_in_toms_dm_stops_every_task_reporting_to_its_session_and_tells_the_session(dsn, dm):
    """Exactly `stop`, top level or in reply to the session's own post,
    stops at once each open task the session started or that reported to
    it, never the session; the session gets the stop as a steer. A reply
    `stop` to an older task's question still stops that task."""

    async def go():
        chat = (await tom_says(dsn, dm, "hello", at(10)))["task_id"]
        child, other = await child_of(dsn, chat), await new_task(dsn, max_effect_class="act")
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, chat, "child.reported",
                                {"child_id": other, "kind": "question", "about_key": "q", "text": "?"})  # fmt: skip
        top = await tom_says(dsn, dm, "stop", at(20))
        later = await child_of(dsn, chat)
        await posted(dsn, chat, dm, at(30), "Started the next one.")
        replied = await tom_says(dsn, dm, " Stop ", at(40), reply_to=at(30))
        waiting = await asking(dsn)
        await posted(dsn, waiting, dm, at(50), "Should I go on?", question=True)
        old = await tom_says(dsn, dm, "stop", at(60), reply_to=at(50))
        states = [await state(dsn, t) for t in (child, other, later, waiting, chat)]
        return chat, top, replied, old, waiting, states, await bridges.rows(dsn, chat)

    chat, top, replied, old, waiting, states, rows = run(go())
    assert (top["task_id"], top["as"]) == (chat, "stop") and (replied["task_id"], replied["as"]) == (
        chat,
        "stop",
    )
    assert (old["task_id"], old["as"]) == (waiting, "stop")
    assert states[:4] == [State.STOPPED] * 4 and states[4] is not State.STOPPED
    steered = [p["text"] for p in bridges.typed(rows, "message.steered")]
    assert steered[-2:] == ["stop", " Stop "]


def test_a_root_task_with_no_session_reports_to_the_operator_chats_session(dsn, dm):
    """Its question is `child.reported` on the operator chat's session,
    started for it; the session can answer, steer and stop it. Its push
    credential request still goes to Tom."""
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
    reported = [p for p in bridges.typed(operator_rows, "child.reported") if p["child_id"] == root]
    assert [p["kind"] for p in reported] == ["question"]
    assert [n["kind"] for n in bridges.typed(root_rows, "notice.requested")] == ["credential"]
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


def test_a_sessions_and_its_childs_notices_are_at_home_in_the_chat_they_came_from(dsn, dm):
    async def go():
        chat = (await tom_says(dsn, dm, "hello", at(10)))["task_id"]
        child = await child_of(dsn, chat)
        team = await new_task(dsn, chat={"channel": "telegram", "chat_id": "-1009"}, max_effect_class="act")
        async with await db.connect(dsn) as conn:
            await notices.credential(conn, child, "abc", missing=True)
            homes = [await notices.home(conn, t) for t in (chat, child, team)]
            heads = [await notices.headline(conn, t) for t in (chat, team, child)]
        child_rows, chat_rows = await bridges.rows(dsn, child), await bridges.rows(dsn, chat)
        return (
            homes,
            heads,
            bridges.typed(child_rows, "notice.requested"),
            [(r["child_id"] == child, r["kind"]) for r in bridges.typed(chat_rows, "child.reported")],
        )

    homes, heads, notes, reported = run(go())
    assert homes == [("slack", dm), ("slack", dm), ("telegram", "-1009")]
    assert heads[:2] == [None, None] and heads[2]
    assert not notes and reported == [(True, "credential")]


def test_a_chat_session_that_kept_failing_on_a_message_says_so_in_reply_to_it(dsn):
    """The mastery chat, 2026-10-23: a broken install failed four turns on
    Tom's message, and the chat was told "one of its steps failed" about
    no work it could name."""
    ended = {"by": "kernel", "kind": "step", "check": None, "state": "clarify", "tries": 4, "reason": "boom"}

    async def go():
        team = await new_task(dsn, chat={"channel": "telegram", "chat_id": "-1009"}, max_effect_class="act")
        answered = await new_task(dsn, chat={"channel": "telegram", "chat_id": "-1010"}, max_effect_class="act")
        async with await db.connect(dsn) as conn:
            for task in (team, answered):
                said = {"text": "Can this be used?", "message_id": "11706"}
                await ledger.append(conn, task, "message.steered", said)
                for verdict in ("idle", "failed") if task == answered else ("failed",):
                    turn = {"state": "clarify", "verdict": verdict, "turn_id": ledger.new_id()}
                    await ledger.append(conn, task, "turn.collected", turn)
                await ledger.append(conn, task, "task.ended", ended)
                await notices.owe(conn, task)
        return [bridges.typed(await bridges.rows(dsn, t), "notice.requested")[0] for t in (team, answered)]

    failed, later = run(go())
    assert failed["reply_to"] == "11706" and failed["chat_id"] == "-1009"
    assert failed["text"].startswith("I could not answer this: something in my own system failed 4 times")
    assert "Send it again" in failed["text"] and notices.AGAIN not in failed["text"]
    assert later["reply_to"] is None and later["text"].endswith(notices.AGAIN)


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


async def said(dsn, text, *, chat=GROUP, sender=RAIN, name="Rain", reply_to=None, kind="group") -> dict:
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
        ("Rain", "the florist page 500s on mobile"), ("Kim", "valor can you look?"),
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
    assert "Rain (message " in prompt


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
    asked = session._stage_verdict(State.CLARIFY, signals.Signals(question="?"), None, "t", True, b)
    assert asked == ("idle", {}, ["question means nothing in a chat session; ignored"])
    assert (
        session._stage_verdict(State.CLARIFY, signals.Signals(stop="bye"), None, "t", True, b)[0] == "ended"
    )


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


def test_every_listed_chat_resolves_to_its_description_on_its_machine():
    real = pathlib.Path(__file__).parent.parent / "projects"
    toms = ("telegram:179144806", "slack:T0B6E8JPE3C/U0B78H17XQ8")
    with bridges.configure(
        projects_dir=str(real), operator_telegram_id="179144806", operator_slack_ids=("T0B6E8JPE3C/U0B78H17XQ8",)
    ):
        specs = workspace.Spec.all()
        listed = {chat: spec for spec in specs for chat in spec.chats}
        assert set(listed) >= {
            "telegram:179144806",
            "telegram:-1003890616618",
            "telegram:-1003449100931",
            "telegram:-5189826365",
            "telegram:-1003801797780",
            "telegram:-1003743854645",
            "telegram:-5302491300",
            "telegram:-1003513124705",
            "telegram:-5174105183",
            "telegram:-4719889199",
        }
        for chat, spec in listed.items():
            channel, _, ident = chat.partition(":")
            with bridges.configure(machine=spec.machine or settings.default_machine):
                found = intake.room(channel, "<root@mail.test>" if channel == "email" else ident, spec.name)
            if channel == "email":
                assert found == spec.rooms["email"] != intake.EMAIL_ROOM, chat
            else:
                assert found == (
                    intake.TOMS_OWN_ROOM if chat in toms else spec.rooms[chat]
                ), chat
        assert intake.room("email", "<root@mail.test>", "valor") == intake.EMAIL_ROOM


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


def test_a_chat_session_starts_answers_and_stops_its_tasks_which_report_only_to_it(dsn, team):
    """`task.start` makes a child on the project, named by who asked; its
    question reaches the session, never a chat, and `task.answer` answers
    it. The push credential request stays Tom's; a task not the session's
    is refused."""
    from tools import chat_tasks

    async def go():
        session_id = (await said(dsn, "valor, bump the stripe sdk"))["task_id"]
        async with await db.connect(dsn) as conn:
            b = await tasks.brief(conn, session_id)
        start, answer, _, stop = chat_tasks.performers(b, dsn)
        made = await start.perform(
            broker.Action("task.start", "work", {"instruction": "Bump stripe", "by": "Rain"}), "k1"
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
        await stop.perform(broker.Action("task.stop", child, {"why": "not now", "by": "Rain"}), "k3")
        return session_id, child, cb, refused, found, *[await rows_of(dsn, t) for t in (session_id, child)]

    session_id, child, cb, refused, found, parent_rows, child_rows = run(go())
    assert cb.parent_id == session_id and cb.project == {"name": "flowers"} and found == {"task_id": child}
    (started,) = [r["payload"] for r in child_rows if r["type"] == "task.started"]
    assert started["provenance"]["by"] == "Rain"
    reported = [p["kind"] for p in bridges.typed(parent_rows, "child.reported") if p["child_id"] == child]
    assert reported == ["question", "credential"]
    assert not [r for r in child_rows if r["type"] == "notice.requested"]
    (answered,) = [r["payload"] for r in child_rows if r["type"] == "question.answered"]
    assert (answered["text"], answered["provenance"]["by"]) == ("v12", "Kim")
    assert refused == f"task {session_id} does not report to this chat"
    assert machine.fold(child_rows).state is State.STOPPED


def test_a_chat_sessions_send_to_its_own_chat_owes_no_report_and_one_elsewhere_is_told_there(dsn, team):
    async def go():
        task = (await said(dsn, "valor, tell the florist we're live"))["task_id"]
        async with await db.connect(dsn) as conn:
            for kind, target in (("telegram.send_message", GROUP), ("email.send", "x@florist.test")):
                effect = {"effect_id": ledger.new_id(), "effect_class": "act", "action_type": kind,
                          "target": target, "payload": {"text": "We're live.", "to": [target]}}  # fmt: skip
                await notices.report(conn, task, effect, {})
        return [r["payload"] for r in await rows_of(dsn, task) if r["type"] == "notice.requested"]

    (reported,) = run(go())
    assert (reported["chat_id"], reported["reply_to"]) == (GROUP, None)
    assert "an email to x@florist.test" in reported["text"]


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


def test_the_real_specs_keep_team_chats_quiet_and_the_rest_answering(tmp_path):
    """Every team chat waits to be mentioned and starts no engineering
    (Tom, 2026-10-17); Eng groups, the operator group and Tom's DM hear
    every message."""
    team = {"yudame": ["-4719889199"], "psyoptimal": ["-1002600253717"],
            "cyndra": ["-1003794218389", "-1004385743413"], "satsol": ["-5095889193"],
            "valor": ["-1003879986445", "-1003936769817"]}  # fmt: skip
    answering = {"valor": ["-1003890616618", "-1003449100931", "179144806"], "psyoptimal": ["-1003743854645"],
                 "cyndra": ["-1003900483201"], "satsol": ["-5136818964"]}  # fmt: skip
    root = pathlib.Path(__file__).parent.parent / "projects"
    with bridges.configure(projects_dir=str(root)):
        specs = {s.name: s for s in workspace.Spec.all()}
    assert len(specs) == len(list(root.glob("*.toml")))
    for name, ids in team.items():
        assert {f"telegram:{i}" for i in ids} <= set(specs[name].mentions), name
        for i in ids:
            assert "No engineering or SDLC work starts here" in specs[name].rooms[f"telegram:{i}"], i
    assert "a public group" in specs["valor"].rooms["telegram:-1003879986445"]
    general = (
        "slack:T0B6E8JPE3C/C0B67T44KEF"  # Yudame Slack #general, received on the Cowboy (Tom, 2026-10-23)
    )
    assert general in specs["valor"].mentions and specs["valor"].machine == "Valor the Cowboy"
    assert "No engineering or SDLC work starts here" in specs["valor"].rooms[general]
    for name, ids in answering.items():
        assert {f"telegram:{i}" for i in ids} <= set(specs[name].chats) - set(specs[name].mentions), name


YUDAME_SLACK = "T0B6E8JPE3C"
TOM_SLACK = f"{YUDAME_SLACK}/U0B78H17XQ8"


def real_specs(**more) -> dict[str, workspace.Spec]:
    root = pathlib.Path(__file__).parent.parent / "projects"
    with bridges.configure(projects_dir=str(root), **more):
        return {s.name: s for s in workspace.Spec.all()}


def test_each_project_has_a_slack_dev_channel_answering_as_its_eng_group():
    """`<project>-dev` in the Yudame Slack is the Slack twin of the
    project's Eng group: every message wakes it and its room is the Eng
    group's (Tom, 2026-10-25); yudame, with no Eng group, has its own."""
    eng = {"valor": "-1003449100931", "popoto": "-5189826365", "cuttlefish": "-1003801797780",
           "psyoptimal": "-1003743854645", "cyndra": "-1003900483201", "satsol": "-5136818964",
           "mastery": "-5565152737", "gato-os": "-1003513124705", "mondayflowers": "-5302491300",
           "counsell-home": "-5174105183", "yudame": None}  # fmt: skip
    specs = real_specs()
    channels = set()
    for name, spec in specs.items():
        dev = [c for c in spec.chats if c.startswith(f"slack:{YUDAME_SLACK}/C") and c != "slack:T0B6E8JPE3C/C0B67T44KEF"]
        if name not in eng:
            assert dev == [], name
            continue
        (chat,) = dev
        channels.add(chat)
        assert chat not in spec.mentions, name
        room = spec.rooms[chat]
        assert room == (spec.rooms[f"telegram:{eng[name]}"] if eng[name] else "Engineering on the yuda.me site.")
    assert len(channels) == len(eng)


def test_toms_slack_dm_is_owned_only_on_the_mac_valor_is_placed_on():
    """Tom's ids alone own nothing, so a second Mac's Slack bridge never
    takes his DM; each Mac's bridge owns its own projects' dev channels."""
    owned = {}
    for mac in ("Valor the Cowboy", "Valor the Pirate"):
        root = pathlib.Path(__file__).parent.parent / "projects"
        with bridges.configure(projects_dir=str(root), machine=mac, default_machine=mac,
                               operator_slack_ids=(TOM_SLACK,), operator_channel="telegram"):  # fmt: skip
            owned[mac] = set(intake.owned("slack"))
    specs = real_specs()
    assert TOM_SLACK in owned["Valor the Cowboy"] and TOM_SLACK not in owned["Valor the Pirate"]
    for name, spec in specs.items():
        slack = {c.removeprefix("slack:") for c in spec.chats if c.startswith("slack:")}
        mine = spec.machine if spec.machine in owned else None
        for mac, ids in owned.items():
            assert (slack <= ids) if mac == mine else not (slack & ids), (name, mac)


def test_toms_dm_as_operator_chat_has_a_session_only_on_the_mac_its_spec_is_placed_on(dsn, tmp_path):
    """Every Mac's kernel names Tom's Slack DM as its operator chat, so its
    notices reach him (the Bald's update failures of 2026-10-26 had no
    chat). Only the Cowboy, where `valor.toml` lists the DM, opens a
    session there; a root task's notice on the Pirate goes to the DM
    itself. A group operator chat no spec lists keeps its session."""
    root = pathlib.Path(__file__).parent.parent / "projects"

    def mac(name, **more):
        values = dict(projects_dir=str(root), work_dir=str(tmp_path / "work"), machine=name, default_machine=name,
                      operator_telegram_id="179144806", operator_slack_ids=(TOM_SLACK,),
                      operator_channel="slack", operator_chat=TOM_SLACK) | more  # fmt: skip
        return bridges.configure(**values)

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
        return pirate, group, cowboy, b, await bridges.rows(dsn, task)

    pirate, group, cowboy, b, rows = run(go())
    assert pirate is None and group is not None and cowboy is not None
    assert b.chat == {"channel": "slack", "chat_id": TOM_SLACK}
    (notice,) = bridges.typed(rows, "notice.requested")
    assert (notice["channel"], notice["chat_id"], notice["kind"]) == ("slack", TOM_SLACK, "ended")
    assert bridges.typed(rows, "notice.undeliverable") == []


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
    `valor`; an email names its sender, never Tom, so `stop` steers. The
    session's reply in its thread owes Tom no report; a send elsewhere does."""

    async def go():
        first = await emailed(dsn, "The florist page\n\nit 500s on mobile")
        root = first["message_id"]
        second = await emailed(dsn, "Re: The florist page\n\nstop", thread=(root,))
        empty = await emailed(dsn, "", thread=(root, second["message_id"]))
        client = await emailed(dsn, "Hours\n\nopen Sunday?", sender="kim@florist.test", name="Kim")
        task = first["task_id"]
        async with await db.connect(dsn) as conn:
            b, c = await tasks.brief(conn, task), await tasks.brief(conn, client["task_id"])
            for refs in ([root, second["message_id"]], ["<elsewhere@mail.test>"]):
                effect = {"effect_id": ledger.new_id(), "effect_class": "act", "action_type": "email.send",
                          "target": "kim@florist.test",
                          "payload": {"to": ["kim@florist.test"], "body": "On it.", "references": refs}}  # fmt: skip
                await notices.report(conn, task, effect, {})
        return first, second, empty, client, b, c, await rows_of(dsn, task)

    first, second, empty, client, b, c, rows = run(go())
    assert (first["as"], second["as"], empty["as"]) == ("steer", "steer", "none")
    assert first["task_id"] == second["task_id"] != client["task_id"]
    assert b.chat == {"channel": "email", "chat_id": first["message_id"]} and b.project == {"name": "valor"}
    assert b.instruction.startswith("You are in an email thread") and c.project == {"name": "flowers"}
    steered = [r["payload"] for r in rows if r["type"] == "message.steered"]
    assert [p["provenance"]["by"] for p in steered] == ["Tom", "Tom"]
    assert machine.fold(rows).state is not State.STOPPED
    (reported,) = [r["payload"] for r in rows if r["type"] == "notice.requested"]
    assert (reported["channel"], reported["chat_id"]) == ("email", first["message_id"])


def test_a_notice_to_an_email_thread_is_a_reply_to_all_of_its_newest_email(dsn, mail, monkeypatch):
    """The email bridge sends it over SMTP and marks it sent; a send in
    doubt is not sent again, but found in Sent Mail."""
    from bridges.email import EmailBridge, smtp
    from core import bridge

    calls = []

    def send(kind):
        def call(cfg, action, key, *rest):
            calls.append((kind, action.payload, key))
            if len(calls) == 1:
                raise broker.Unknown("cut off")
            return {"sent": [{"channel": "email", "chat_id": "root", "message_id": "<m>"}]}

        return call

    monkeypatch.setattr(smtp, "perform", send("perform"))
    monkeypatch.setattr(smtp, "lookup", send("lookup"))

    async def go():
        first = await emailed(dsn, "The florist page\n\nit 500s", sender="kim@florist.test", name="Kim")
        second = await emailed(dsn, "Re: The florist page\n\nstill", sender="kim@florist.test",
                               name="Kim", thread=(first["message_id"],))  # fmt: skip
        async with await db.connect(dsn) as conn:
            nid = await notices.request(conn, first["task_id"], kind="ended", about_key="x", text="Done.")
            outbox = bridge.Outbox(SimpleNamespace(channel="email"), {}, conn, conn, None)
            (due,) = [i for i in await outbox.due() if isinstance(i, bridge.NoticeDue) and i.notice_id == nid]
            b = EmailBridge(SimpleNamespace(address="valor@yuda.me"), dsn)
            with pytest.raises(broker.Unknown):
                await b.notice(outbox, due)
            await b.notice(outbox, due)
            return nid, second, await bridges.of_type(dsn, "notice.sent", notice_id=nid)

    nid, second, sent = run(go())
    assert [k for k, *_ in calls] == ["perform", "lookup"] and calls[0][2] == calls[1][2]
    payload = calls[0][1]
    assert payload["to"] == ["kim@florist.test"] and payload["in_reply_to"] == second["message_id"]
    assert payload["body"] == "Done."
    assert [s["notice_id"] for s in sent] == [nid]


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
