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

import aiohttp
import psycopg
import pytest

from core import (
    broker,
    corrections,
    db,
    intake,
    ledger,
    machine,
    notices,
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
    old = tasks.Brief(
        instruction="x", mirror=str(tmp_path / "kernel.git"), harness=bare, harness_name="claude_code"
    )
    assert __main__._turn_for("p", None, old)("http://h:1", "b", "t").tmpdir == str(tmp_path / "tmp")
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


def test_a_sessions_and_its_childs_notices_are_at_home_in_toms_own_chat_only(dsn, dm):
    async def go():
        chat = (await tom_says(dsn, dm, "hello", at(10)))["task_id"]
        child = await child_of(dsn, chat)
        team = await new_task(dsn, chat={"channel": "telegram", "chat_id": "-1009"}, max_effect_class="act")
        async with await db.connect(dsn) as conn:
            await notices.credential(conn, child, "abc", missing=True)
            homes = [await notices.home(conn, t) for t in (chat, child, team)]
        return homes, bridges.typed(await bridges.rows(dsn, child), "notice.requested")

    homes, (credential,) = run(go())
    assert homes == [("slack", dm), ("slack", dm), (None, None)]
    assert (credential["channel"], credential["chat_id"]) == ("slack", dm)


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
    assert session._steering([steer]) == "Written to you while you worked:\n- valor: only the tests half"


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


# -- a team chat: one long-lived chat session, every message a steer ------------


# LONE: a chat session started directly, which no message there reaches.
GROUP, QUIET_GROUP, OTHER, LONE = "-1001", "-1002", "-1003", "-1004"
RAIN = "77"


@pytest.fixture
def team(tmp_path):
    """A project whose two team chats this machine reads, one of them
    mentions only; `OTHER` is a group no spec lists."""
    with bridges.operator(tmp_path):
        (pathlib.Path(settings.projects_dir) / "flowers.toml").write_text(
            'name = "flowers"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\n'
            f'chats = ["telegram:{GROUP}", "telegram:{QUIET_GROUP}"]\n'
            f'mentions = ["telegram:{QUIET_GROUP}"]\n'
        )
        yield


async def said(dsn, text, *, chat=GROUP, sender=RAIN, name="Rain", reply_to=None) -> dict:
    """A Telegram group message, received and bound: its binding."""
    mid = str(uuid.uuid4().int % 10**9)
    inbound = intake.Inbound(
        channel="telegram", chat_id=chat, chat_kind="group", message_id=mid, sender_id=sender,
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
        rows = await rows_of(dsn, woke["task_id"])
        return bound, waiting, woke, session.chat_waiting(rows), session.chat_prompt(rows)

    bound, waiting, woke, now, prompt = run(go())
    assert bound == ["heard", "heard"] and not waiting
    assert woke["as"] == "steer" and now
    assert (
        prompt.index("lunch at noon?") < prompt.index("valorous effort") < prompt.index("Valor, what broke?")
    )
    assert "Rain (message " in prompt


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
    asked = session._stage_verdict(State.CLARIFY, signals.Signals(question="?"), None, "t", True, b)
    assert asked == ("idle", {}, ["question means nothing in a chat session; ignored"])
    assert (
        session._stage_verdict(State.CLARIFY, signals.Signals(stop="bye"), None, "t", True, b)[0] == "ended"
    )


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
    (reported,) = [p for p in bridges.typed(parent_rows, "child.reported") if p["child_id"] == child]
    assert reported["kind"] == "question"
    notes = [r["payload"] for r in child_rows if r["type"] == "notice.requested"]
    assert [(n["kind"], n["chat_id"]) for n in notes] == [("credential", bridges.OPERATOR_CHAT)]
    (answered,) = [r["payload"] for r in child_rows if r["type"] == "question.answered"]
    assert (answered["text"], answered["provenance"]["by"]) == ("v12", "Kim")
    assert refused == f"task {session_id} does not report to this chat"
    assert machine.fold(child_rows).state is State.STOPPED


def test_a_chat_sessions_send_to_its_own_chat_owes_no_report_and_one_elsewhere_reaches_tom(dsn, team):
    async def go():
        task = (await said(dsn, "valor, tell the florist we're live"))["task_id"]
        async with await db.connect(dsn) as conn:
            for kind, target in (("telegram.send_message", GROUP), ("email.send", "x@florist.test")):
                effect = {"effect_id": ledger.new_id(), "effect_class": "act", "action_type": kind,
                          "target": target, "payload": {"text": "We're live.", "to": [target]}}  # fmt: skip
                await notices.report(conn, task, effect, {})
        return [r["payload"] for r in await rows_of(dsn, task) if r["type"] == "notice.requested"]

    (reported,) = run(go())
    assert (reported["chat_id"], reported["reply_to"]) == (bridges.OPERATOR_CHAT, None)
    assert "an email to x@florist.test" in reported["text"]
