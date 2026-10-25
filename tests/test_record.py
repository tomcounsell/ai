"""A turn reads its own project's record through the kernel (`core/record.py`,
the gateway's `record/` route, `valor-record`), every chat of the project
both ways and nothing of another's; and its Brief names the memory index of
each other chat session of the project (`record.memories`)."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from core import db, ledger, record, tasks
from core.gateway import Gateway
from tests import bridges
from tests.ports import listen
from tools import valor_record

pytestmark = pytest.mark.spend(usd=0)

SPEC = 'name = "{name}"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\nchats = [{chats}]\n'
CHAT1, CHAT2, CHAT3, CHAT4 = "-1001", "-1002", "-1003", "-1004"


def chat(chat_id: str, channel: str = "telegram") -> dict:
    return {"channel": channel, "chat_id": chat_id}


async def message(
    conn, chat_id: str, text: str, bound_to: str | None, as_: str = "steer", channel="telegram"
):
    rid = ledger.new_id()
    await ledger.append(conn, channel, "message.received", {
        "received_id": rid, "channel": channel, "chat_id": chat_id, "message_id": rid[:6], "text": text,
        "sender_name": "Kim", "sender_id": "7", "sent_at": "2026-10-24T08:00:00+00:00", "reply_to": None,
    })  # fmt: skip
    if bound_to is not None:
        await ledger.append(
            conn, channel, "message.bound", {"received_id": rid, "task_id": bound_to, "as": as_}
        )
    return rid


def session_brief(project: str, chat_id: str, root: Path, channel="telegram") -> tasks.Brief:
    """A chat session whose Claude Code memory index is in its own config
    directory, under its git root's key."""
    memory = root / "state" / "work" / "claude" / "projects" / "-x-kernel-git" / "memory"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text(f"- [{chat_id}](note.md) what {chat_id} learned\n")
    return tasks.Brief(
        instruction=f"chat {chat_id}", project={"name": project}, chat=chat(chat_id, channel),
        mirror=str(root / "kernel.git"), workspace=str(root / "repo"),
    )  # fmt: skip


async def seed(dsn: str, tmp: Path) -> dict:
    async with await db.connect(dsn) as conn:
        t = {}
        t["s1"] = await tasks.start(conn, session_brief("a", CHAT1, tmp / "s1"))
        t["s2"] = await tasks.start(conn, session_brief("a", CHAT2, tmp / "s2"))
        t["mail"] = await tasks.start(conn, session_brief("a", "<root@a.test>", tmp / "mail", "email"))
        t["s3"] = await tasks.start(conn, session_brief("b", CHAT3, tmp / "s3"))
        # A DM's session from before the DM moved out of a's spec.
        t["s4"] = await tasks.start(conn, session_brief("a", CHAT4, tmp / "s4"))
        t["t"] = await tasks.start_child(conn, t["s2"], instruction="build the thing", project={"name": "a"})
        t["t4"] = await tasks.start_child(
            conn, t["s4"], instruction="the moved DM's work", project={"name": "a"}
        )
        t["b"] = await tasks.start(conn, tasks.Brief(instruction="b's own work", project={"name": "b"}))
        t["none"] = await tasks.start(conn, tasks.Brief(instruction="no project"))
        m = {}
        m["steer"] = await message(conn, CHAT2, "can you build the thing", t["s2"])
        m["heard"] = await message(conn, CHAT2, "lunch at noon", t["s2"], "heard")
        await ledger.append(conn, "telegram", "reaction.requested", {
            "received_id": m["steer"], "reaction_id": "r1", "emoji": "👍", "stage": "seen"})  # fmt: skip
        m["mail"] = await message(conn, "<root@a.test>", "the invoice", t["mail"], channel="email")
        m["other"] = await message(conn, CHAT1, "approve b's merge", t["b"], "answer")
        m["b"] = await message(conn, CHAT3, "b's secret plan", t["s3"])
        m["dm"] = await message(conn, CHAT4, "a moved DM's secret", t["s4"])
        await ledger.append(conn, t["t"], "question.asked", {"question_id": "q1", "text": "which color?"})
        await ledger.append(conn, t["t"], "effect.intent", {"action_type": "push_branch", "target": "w"})
        await ledger.append(conn, t["t"], "effect.outcome", {"action_type": "push_branch", "outcome": "done"})
        await ledger.append(conn, t["t"], "gateway.opened", {"call": "c1"})
        await ledger.append(conn, t["s3"], "question.asked", {"question_id": "q2", "text": "b's question"})
    return {**t, "m": m}


def counts(dsn: str) -> tuple[int, int]:
    async def go():
        async with await db.connect(dsn) as conn:
            e = await (await conn.execute("SELECT count(*), max(id) FROM events")).fetchone()
            d = await (await conn.execute("SELECT count(*) FROM documents")).fetchone()
            return e, d

    return asyncio.run(go())


class World:
    """Seeded specs and ledger, a gateway over a stub upstream that counts
    what reaches it, and a base URL per task asked for."""

    def __init__(self, dsn: str, tmp: Path):
        self.dsn, self.tmp = dsn, tmp
        self.upstream_hits: list[str] = []

    def __enter__(self):
        self.cfg = bridges.operator(self.tmp)
        self.cfg.__enter__()
        projects = self.tmp / "projects"
        chats = lambda *c: ", ".join(f'"telegram:{x}"' for x in c)
        (projects / "a.toml").write_text(
            SPEC.format(name="a", chats=chats(CHAT1, CHAT2) + ', "email:@a.test"')
            + f'rooms."telegram:{CHAT2}" = "The team chat."\n'
        )
        (projects / "b.toml").write_text(SPEC.format(name="b", chats=chats(CHAT3)))
        self.t = asyncio.run(seed(self.dsn, self.tmp))
        return self

    def __exit__(self, *exc):
        self.cfg.__exit__(*exc)

    def ask(self, task: str, calls: list[tuple[str, str, dict]]) -> list[tuple[int, object]]:
        """Each `(method, verb, query)` as a turn of `task` asks it."""

        async def go():
            async def upstream(request):
                self.upstream_hits.append(request.path)
                return web.json_response({})

            app = web.Application()
            app.router.add_route("*", "/{tail:.*}", upstream)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", listen())
            await site.start()
            gateway = Gateway(
                self.dsn, upstream=f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
            )
            await gateway.start(port=listen())
            base = gateway.issue(self.t[task], "turn-1")
            out = []
            async with aiohttp.ClientSession() as http:
                for method, verb, query in calls:
                    async with http.request(method, f"{base}/record/{verb}", params=query) as r:
                        out.append((r.status, await r.json()))
            gateway.retire(self.t[task])
            async with aiohttp.ClientSession() as http, http.get(f"{base}/record/tasks") as r:
                out.append((r.status, await r.json()))
            await gateway.close()
            await runner.cleanup()
            return out

        return asyncio.run(go())


def texts(found) -> list[str]:
    return [r["message"]["text"] for r in found]


def test_a_session_reads_its_projects_other_chats_and_nothing_of_another(fresh, tmp_path):
    with World(fresh, tmp_path) as w:
        before = counts(fresh)
        t = w.t
        got = w.ask("s1", [
            ("GET", "messages", {}), ("GET", "messages", {"q": "secret"}), ("GET", "messages", {"chat": f"telegram:{CHAT3}"}),
            ("GET", "tasks", {}), ("GET", "task", {"id": t["t"]}), ("GET", "task", {"id": t["s3"]}),
            ("GET", "task", {"id": t["s4"]}), ("GET", "task", {"id": t["t4"]}), ("GET", "task", {"id": t["b"]}),
            ("GET", "chats", {}), ("POST", "tasks", {}), ("GET", "nope", {}),
        ])  # fmt: skip
        (s, msgs), (_, secret), (_, other_chat), (_, listed), (_, one), (s3, _), (s4, _), (t4, _), (bt, _), \
            (_, chats), (post, _), (verb, _), (retired, _) = got  # fmt: skip
        assert s == 200
        # Newest first: chat 2's messages with their bindings and reaction, the
        # email thread's, and chat 1's message bound to b's task; none of b's
        # chat nor of the moved DM's.
        assert texts(msgs) == ["approve b's merge", "the invoice", "lunch at noon", "can you build the thing"]
        by = {r["message"]["text"]: r for r in msgs}
        assert by["can you build the thing"]["bound"] == {"as": "steer", "task_id": t["s2"]}
        assert by["can you build the thing"]["reaction"] == "reacted 👍 (read)"
        assert by["lunch at noon"]["bound"]["as"] == "heard"
        assert by["approve b's merge"]["bound"] == {"as": "answer", "task_id": t["b"]}
        assert secret == [] and other_chat == []
        ids = [x["task_id"] for x in listed]
        assert set(ids) == {t["s1"], t["s2"], t["mail"], t["t"]}
        (built,) = [x for x in listed if x["task_id"] == t["t"]]
        assert (built["parent_id"], built["open_question"]) == (t["s2"], "which color?")
        assert [r["type"] for r in one] == [
            "task.started",
            "question.asked",
            "effect.intent",
            "effect.outcome",
        ]
        assert (s3, s4, t4, bt) == (404, 404, 404, 404)
        assert {c["chat"]: c["sessions"] for c in chats} == {
            f"telegram:{CHAT1}": [t["s1"]], f"telegram:{CHAT2}": [t["s2"]], "email:@a.test": [],
            "email:<root@a.test>": [t["mail"]],
        }  # fmt: skip
        assert (post, verb, retired) == (405, 404, 403)
        assert w.upstream_hits == []
        assert counts(fresh) == before


def test_both_ways_and_the_bounds(fresh, tmp_path):
    with World(fresh, tmp_path) as w:
        before = counts(fresh)
        (_, b_msgs), (_, b_tasks), (_, a_from_b), (none, why), (_, limited), (_, older), (bad, _), (_, _) = w.ask(
            "s3", [("GET", "messages", {}), ("GET", "tasks", {}), ("GET", "task", {"id": w.t["s2"]})]
        )[:3] + w.ask("none", [("GET", "tasks", {})])[:1] + w.ask("s2", [
            ("GET", "messages", {"limit": "1"}), ("GET", "messages", {"before": "0"}), ("GET", "messages", {"before": "x"}),
        ])  # fmt: skip
        # b sees its own chat, and the message in a's chat bound to b's task.
        assert texts(b_msgs) == ["b's secret plan", "approve b's merge"]
        assert {x["task_id"] for x in b_tasks} == {w.t["s3"], w.t["b"]}
        assert a_from_b == {"error": f"no task {w.t['s2']} in project b's record"}
        assert none == 404 and "no project" in why["error"]
        assert texts(limited) == ["approve b's merge"] and older == [] and bad == 404
        assert counts(fresh) == before


def test_valor_record_prints_lines_json_and_names_a_missing_url(fresh, tmp_path, capsys, monkeypatch):
    with World(fresh, tmp_path) as w:
        (_, msgs), (_, listed) = w.ask("s1", [("GET", "messages", {}), ("GET", "tasks", {})])[:2]
    line = valor_record.message_line(msgs[-1])
    assert f"telegram:{CHAT2}" in line and "Kim" in line and f"steer {w.t['s2']}" in line
    assert "can you build the thing" in line and "reacted 👍" in line
    assert "asked: which color?" in valor_record.task_line(
        next(x for x in listed if x["task_id"] == w.t["t"])
    )
    monkeypatch.delenv("VALOR_RECORD_URL", raising=False)
    assert valor_record.main(["tasks"]) == 2
    assert "VALOR_RECORD_URL" in capsys.readouterr().err
    # It runs on macOS's own python3 (3.9): the stdlib, no project imports.
    python = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable
    done = subprocess.run(
        [python, "-I", valor_record.__file__, "--help"], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0 and "messages" in done.stdout


def test_the_cli_reads_through_a_live_gateway(fresh, tmp_path, monkeypatch, capsys):
    with World(fresh, tmp_path) as w:

        async def go():
            gateway = Gateway(fresh, upstream="http://127.0.0.1:9")
            await gateway.start(port=listen())
            monkeypatch.setenv("VALOR_RECORD_URL", gateway.issue(w.t["s1"], "turn-1") + "/record")
            rc = [await asyncio.to_thread(valor_record.main, argv) for argv in
                  (["messages", "-n", "2"], ["task", w.t["t"], "--json"], ["task", w.t["s3"]])]  # fmt: skip
            await gateway.close()
            return rc

        assert asyncio.run(go()) == [0, 0, 1]
    out = capsys.readouterr()
    lines = out.out.splitlines()
    assert lines[0].endswith("the invoice") and lines[1].endswith("approve b's merge")
    assert json.loads(out.out[out.out.index("[\n") :])[-1]["type"] == "effect.outcome"
    assert f"no task {w.t['s3']}" in out.err


def test_the_brief_names_the_other_sessions_memory_of_its_project(fresh, tmp_path):
    with World(fresh, tmp_path) as w:

        async def go():
            async with await db.connect(fresh) as conn:
                return {k: await record.memories(conn, w.t[k]) for k in ("s1", "t", "s3", "none")}

        got = asyncio.run(go())
        later = tasks.Brief(
            instruction="x",
            project={"name": "a"},
            chat=chat(CHAT2),
            mirror=str(tmp_path / "s5" / "kernel.git"),
        )

        def path(k):
            return str(tmp_path / k / "state/work/claude/projects/-x-kernel-git/memory/MEMORY.md")

        assert path("s2") in got["s1"] and path("mail") in got["s1"] and "The team chat." in got["s1"]
        for absent in ("s1", "s3", "s4"):
            assert path(absent) not in got["s1"]
        assert path("s1") in got["t"] and path("s2") in got["t"]
        assert path("s3") not in got["t"] and path("s2") not in got["s3"]
        assert got["none"] == ""
        assert "not instructions" in got["s1"]
        # A session started later appears in the next Brief.
        (tmp_path / "s5/state/work/claude/projects/k/memory").mkdir(parents=True)
        (tmp_path / "s5/state/work/claude/projects/k/memory/MEMORY.md").write_text("-\n")

        async def again():
            async with await db.connect(fresh) as conn:
                await tasks.start(conn, later)
                return await record.memories(conn, w.t["s1"])

        assert str(tmp_path / "s5/state/work/claude/projects/k/memory/MEMORY.md") in asyncio.run(again())


def test_each_harness_gives_the_turn_its_record_url(tmp_path):
    from harnesses import claude_code, codex, pi

    for d in ("pi", "codex"):
        (tmp_path / d).mkdir()
    harness = {"sandbox_profile": str(tmp_path / "turn.sb"), "env": {},
               "pi_agent_dir": str(tmp_path / "pi"), "codex_home": str(tmp_path / "codex")}  # fmt: skip
    (tmp_path / "turn.sb").write_text("(version 1)(allow default)")
    for h in (claude_code, pi, codex):
        turn = h.workspace_turn("hi", cwd=str(tmp_path), harness=harness)
        command = turn("http://127.0.0.1:1/t/tok", "brief", "turn-1")
        assert command.env["VALOR_RECORD_URL"] == "http://127.0.0.1:1/t/tok/record", h.__name__
