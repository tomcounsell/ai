"""The status page on real Postgres through aiohttp's own test client: each
page renders, every other method is a 405 that writes nothing, ledger
values are escaped, the page's database session cannot write, and the bind
is the loopback address on a port no sandbox profile opens. No model call."""

import inspect

import psycopg
import pytest
from aiohttp.test_utils import TestClient, TestServer

import ui.__main__ as ui_main
import ui.app as ui_app
from core import ledger, routines, spending, tasks, workspace
from core.settings import settings
from tests.ports import listen
from tests.test_objective_tree import call, run

pytestmark = pytest.mark.spend(usd=0)

HOSTILE = "<script>alert(1)</script>"


async def fetch(dsn: str, path: str, method: str = "GET"):
    async with TestClient(TestServer(ui_app.make_app(dsn), port=listen())) as client:
        resp = await client.request(method, path)
        return resp.status, await resp.text()


async def count(dsn: str) -> int:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        return (await (await conn.execute("SELECT count(*) FROM events")).fetchone())[0]


def test_every_page_renders_and_a_hostile_ledger_value_is_escaped(dsn):
    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            t = await tasks.start(conn, tasks.Brief(instruction=HOSTILE))
            await ledger.append(conn, t, "question.asked", {"text": HOSTILE, "question_id": "q"})
        out = {}
        for path in ("/", f"/task/{t}", "/pending", "/attention", "/routines"):
            out[path] = await fetch(dsn, path)
        out["missing"] = await fetch(dsn, "/task/no-such-task")
        return t, out

    t, out = run(go())
    for path in ("/", f"/task/{t}", "/pending", "/attention", "/routines"):
        status, text = out[path]
        assert status == 200 and HOSTILE not in text, path
    assert "&lt;script&gt;" in out["/"][1] and "&lt;script&gt;" in out[f"/task/{t}"][1]
    assert out["missing"][0] == 404


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "HEAD"])
def test_any_method_but_get_is_refused_and_writes_nothing(dsn, method):
    async def go():
        before = await count(dsn)
        got = [await fetch(dsn, path, method) for path in ("/", "/pending", "/routines")]
        return before, got, await count(dsn)

    before, got, after = run(go())
    assert [s for s, _ in got] == [405, 405, 405] and before == after


def test_the_pages_database_session_cannot_write(dsn, monkeypatch):
    async def writer(conn):
        try:
            await conn.execute("INSERT INTO events (task_id, type, payload) VALUES ('x', 'ui.wrote', '{}')")
        except psycopg.errors.ReadOnlySqlTransaction as exc:
            return ui_app.web.Response(text=f"refused: {type(exc).__name__}")
        return ui_app.web.Response(text="wrote")

    monkeypatch.setattr(ui_app, "tasks_page", writer)

    async def go():
        before = await count(dsn)
        return before, await fetch(dsn, "/"), await count(dsn)

    before, (_, text), after = run(go())
    assert text == "refused: ReadOnlySqlTransaction" and before == after


def test_it_binds_the_loopback_address_on_a_port_no_sandbox_profile_opens():
    source = inspect.getsource(ui_main)
    assert 'host="127.0.0.1"' in source and "settings.ui_port" in source
    port = settings.ui_port
    taken = [
        range(*(workspace.DEV_PORTS.start, workspace.DEV_PORTS.stop)),
        range(settings.pg_ports[0], settings.pg_ports[1] + 1),
        range(settings.redis_ports[0], settings.redis_ports[1] + 1),
        [settings.pgport, settings.gateway_port if hasattr(settings, "gateway_port") else 0],
    ]
    assert not any(port in r for r in taken)


def test_the_routine_pages_thirty_day_figure_is_the_command_lines(dsn, monkeypatch):
    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            r = routines.load("expiry")
            objective, _ = await routines.ensure(conn, r)
            run_id = await tasks.start_child(conn, objective, instruction="a run")
            for micros in (1_250_000, 750_000):
                await spending.charge(
                    conn, run_id, await spending.open_call(conn, run_id, call()), micros, {}
                )
            rep = await routines.report(conn, "expiry")
            line = routines.listing(rep)
        _, text = await fetch(dsn, "/routines")
        return rep, line, text

    rep, line, text = run(go())
    assert tasks.usd(rep["period_spent_usd_micros"]) in line
    assert rep["period_spent_usd_micros"] >= 2_000_000
    assert ui_app.esc(line) in text
