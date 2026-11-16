"""The `summary` routine (`routines/summary`): a project's summary rebuilt
on a night after work on it, on top of the last, and carried in its
sessions' Briefs (`core/record.py`, `summary`). The model is a stub
`claude` calling the routine's own gateway over a local upstream, which
answers each prompt with a summary naming its project. Live spend: none."""

import asyncio
from datetime import UTC

import pytest
from aiohttp import web

from core import db, ledger, routines, tasks
from routines.summary import runner as summary
from tests import bridges
from tests.ports import listen
from tests.test_hours import SPEC, Calendar, _Gateway, at, turn

pytestmark = pytest.mark.spend(usd=0)


class Model(Calendar):
    """The stub `claude` and an upstream that answers `Summary of <project>
    #n`, or fails for a project named in `failing`."""

    def __init__(self, tmp):
        super().__init__(tmp)
        self.failing: set[str] = set()

    async def upstream(self):
        async def handle(request: web.Request) -> web.Response:
            body = await request.json()
            prompt = body["messages"][0]["content"]
            self.prompts.append(prompt)
            name = next(n for n in ("alpha", "beta", "gamma") if f"[{n}]" in prompt)
            if name in self.failing:
                return web.json_response({"type": "error", "error": {"type": "api_error", "message": "down"}},
                                         status=500)  # fmt: skip
            return web.json_response({
                "id": "msg", "type": "message", "role": "assistant", "model": body["model"],
                "content": [{"type": "text", "text": f"Summary of {name} #{len(self.prompts)}"}],
                "stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 3},
            })  # fmt: skip

        app = web.Application()
        app.router.add_post("/v1/messages", handle)
        r = web.AppRunner(app)
        await r.setup()
        site = web.TCPSite(r, "127.0.0.1", listen())
        await site.start()
        return r, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"


def projects(tmp_path):
    d = tmp_path / "projects"
    for name, chat in (("alpha", "-2001"), ("beta", "-2002"), ("gamma", "-2003")):
        (d / f"{name}.toml").write_text(SPEC.format(name=name, chats=f'"telegram:{chat}"', more=""))


def test_a_night_after_work_updates_the_summary_its_sessions_briefs_carry(fresh, tmp_path):
    model = Model(tmp_path)
    with bridges.operator(tmp_path):
        projects(tmp_path)

        async def go():
            up, url = await model.upstream()
            run = summary.runner(claude=str(model.claude), gateway=lambda d: _Gateway(d, upstream=url))
            async with await db.connect(fresh) as conn:

                async def fire(now: str) -> str:
                    return await routines.run(
                        conn, "summary", {"summary": run}, now=at(now).astimezone(UTC), dsn=fresh
                    )

                async def project_task(name: str, chat: str | None = None) -> str:
                    b = tasks.Brief(instruction=f"[{name}] build the export", project={"name": name},
                                    workspace=str(tmp_path / name))  # fmt: skip
                    if chat:
                        b = tasks.Brief(instruction=f"You are in the chat {chat}", project={"name": name},
                                        workspace=str(tmp_path / name), chat={"channel": "telegram", "chat_id": chat})  # fmt: skip
                    return await tasks.start(conn, b)

                a = await project_task("alpha")
                a_chat = await project_task("alpha", "-2001")
                b = await project_task("beta")
                loose = await tasks.start(
                    conn, tasks.Brief(instruction="[gamma] no project", workspace=str(tmp_path))
                )
                await turn(conn, a, "11-10 10:00", "11-10 11:00", "Shipped the alpha export")
                await turn(conn, b, "11-10 14:00", "11-10 14:30", "Fixed the beta login")
                await turn(conn, loose, "11-10 15:00", "11-10 15:30", "Not any project's work")

                before = (await tasks.dispatch(conn, a))["text"]
                first = await fire("11-11 03:00")
                one = list(model.prompts)
                # The second night: only alpha worked, and beta's call fails once.
                await turn(conn, a, "11-11 09:00", "11-11 09:30", "Merged the alpha reports page")
                model.failing.add("beta")
                await turn(conn, b, "11-11 10:00", "11-11 10:20", "Beta docs")
                await fire("11-12 03:00")
                two = model.prompts[len(one) :]
                model.failing.clear()
                await fire("11-13 03:00")
                three = model.prompts[len(one) + len(two) :]
                briefs = {k: (await tasks.dispatch(conn, t))["text"] for k, t in
                          (("a", a), ("chat", a_chat), ("b", b), ("loose", loose))}  # fmt: skip
                review = (await tasks.dispatch(conn, a, fresh="review"))["text"]
                objective = (await routines.ensure(conn, routines.load("summary")))[0]
                rows = []
                for child in await tasks.children(conn, objective):
                    rows += await ledger.read(conn, child)
            await up.cleanup()
            return before, first, one, two, three, briefs, review, rows

        before, first, one, two, three, briefs, review, rows = asyncio.run(go())

    assert "Where the project stands" not in before
    assert "finished" in first
    # Night one: a call per project with work, each from its own project's record only.
    assert len(one) == 2
    alpha1, beta1 = sorted(one, key=lambda p: "[beta]" in p)
    assert "Shipped the alpha export" in alpha1 and "(none: this is the first)" in alpha1
    for p in one:
        assert "Not any project's work" not in p
    assert "Fixed the beta login" not in alpha1 and "Shipped the alpha export" not in beta1
    # Night two: on top of night one, with only the new day; beta fails, alpha still written.
    alpha2 = next(p for p in two if "[alpha]" in p)
    assert "Summary of alpha" in alpha2 and "Merged the alpha reports page" in alpha2
    assert "Shipped the alpha export" not in alpha2
    # Night three: alpha had no work, so no call; beta's failed day is covered again.
    assert len(three) == 1 and "[beta]" in three[0] and "Beta docs" in three[0]
    written = [r["payload"] for r in rows if r["type"] == "summary.written"]
    unwritten = [r["payload"] for r in rows if r["type"] == "summary.unwritten"]
    assert [(w["project"], w["through"]) for w in written] == [
        ("alpha", "2026-11-10"), ("beta", "2026-11-10"), ("alpha", "2026-11-11"), ("beta", "2026-11-12"),
    ]  # fmt: skip
    assert [u["project"] for u in unwritten] == ["beta"]
    # The Brief of a working and a chat session carries its project's latest summary, never another's.
    for k in ("a", "chat"):
        assert "## Where the project stands" in briefs[k] and "through 2026-11-11" in briefs[k]
        assert written[2]["text"] in briefs[k] and "Summary of beta" not in briefs[k]
    assert written[3]["text"] in briefs["b"] and "Summary of alpha" not in briefs["b"]
    assert "Where the project stands" not in briefs["loose"]
    assert "Where the project stands" not in review


def test_a_week_with_no_work_adds_no_entry(fresh, tmp_path):
    model = Model(tmp_path)
    with bridges.operator(tmp_path):
        projects(tmp_path)

        async def go():
            up, url = await model.upstream()
            run = summary.runner(claude=str(model.claude), gateway=lambda d: _Gateway(d, upstream=url))
            async with await db.connect(fresh) as conn:
                a = await tasks.start(conn, tasks.Brief(instruction="[alpha] x", project={"name": "alpha"}))
                await turn(conn, a, "11-01 10:00", "11-01 11:00", "Shipped the alpha export")
                await routines.run(
                    conn, "summary", {"summary": run}, now=at("11-02 03:00").astimezone(UTC), dsn=fresh
                )
                calls = len(model.prompts)
                lines = []
                for day in range(3, 10):
                    lines.append(await routines.run(conn, "summary", {"summary": run},
                                                    now=at(f"11-{day:02d} 03:00").astimezone(UTC), dsn=fresh))  # fmt: skip
                objective = (await routines.ensure(conn, routines.load("summary")))[0]
                runs = await tasks.children(conn, objective)
                rows = [r for c in runs for r in await ledger.read(conn, c) if r["type"] == "summary.written"]
            await up.cleanup()
            return calls, lines, runs, rows

        calls, lines, runs, rows = asyncio.run(go())

    assert calls == 1 and len(model.prompts) == 1
    assert all("nothing_due" in line for line in lines)
    assert len(runs) == 1 and len(rows) == 1 and rows[0]["payload"]["through"] == "2026-11-01"
