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
from core import ledger, outcomes, routines, spending, tasks, workspace
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


# -- what came after a merge -----------------------------------------------------------


async def _merged(conn, path: str) -> str:
    """A task with one merge whose recorded paths include `path`, written
    as the broker writes it, on a target no other test merges to."""
    url = f"/toy/{ledger.new_id()}.git"
    t = await tasks.start(conn, tasks.Brief(instruction="merged", origin_url=url, target_branch="main"))
    await ledger.append(
        conn, t, "task.delivered", {"candidate": "a" * 40, "outcome": "merged", "summary": "s"}
    )
    effect = ledger.new_id()
    payload = {"url": url, "target_branch": "main", "head_sha": "a" * 40, "candidate": "a" * 40}
    await ledger.append(
        conn, t, "effect.held", {"effect_id": effect, "action_type": "merge", "payload": payload}
    )
    landed = {"before": None, "commits": ["a" * 40], "paths": [path, "b.py"], "why": None}
    await ledger.append(
        conn, t, "effect.intent", {"effect_id": effect, "action_type": "merge", "landed": landed}
    )
    await ledger.append(conn, t, "effect.outcome", {"effect_id": effect, "kind": "done"})
    return t


def test_a_merged_tasks_page_shows_what_came_after_and_escapes_its_paths(dsn):
    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            t = await _merged(conn, HOSTILE)
            await ledger.append(
                conn, t, "feedback.given",
                {"feedback_id": ledger.new_id(), "on_delivery": "s", "candidate": None, "text": HOSTILE, "provenance": ledger.provenance("tom", "test", False)},
            )  # fmt: skip
        return t, await fetch(dsn, f"/task/{t}"), await fetch(dsn, "/")

    _t, (status, page), (_, index) = run(go())
    assert status == 200 and HOSTILE not in page
    assert "<h2>After merge</h2>" in page and "&lt;script&gt;alert(1)&lt;/script&gt;, b.py" in page
    assert "tom: &lt;script&gt;" in page and "no kernel copy of the target" in page
    assert "merges 1, feedback after 1, used 0, reworked by 0" in index


def test_a_delivered_task_with_a_mark_and_no_merge_shows_the_used_list(dsn):
    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            t = await tasks.start(conn, tasks.Brief(instruction="delivered"))
            await ledger.append(
                conn, t, "task.delivered", {"candidate": None, "outcome": "answered", "summary": "s"}
            )
            await outcomes.mark_used(conn, t, by="tom", note=HOSTILE)
        return await fetch(dsn, f"/task/{t}")

    status, page = run(go())
    assert status == 200 and HOSTILE not in page
    assert "<h2>Used</h2>" in page and "used 1" in page and "tom: &lt;script&gt;" in page
    assert "<h2>After merge</h2>" not in page


def test_a_task_with_no_merge_and_no_mark_shows_none(dsn):
    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            t = await tasks.start(conn, tasks.Brief(instruction="plain task, nothing after"))
        return t, await fetch(dsn, f"/task/{t}"), await fetch(dsn, "/")

    t, (_, page), (_, index) = run(go())
    assert "<h2>After merge</h2>" not in page and "<h2>Used</h2>" not in page
    row = next(r for r in index.split("<tr>") if t in r)
    assert "merges " not in row


def test_the_audit_page_is_the_blind_list_only_and_the_scores_page_shows_audit_scores_escaped(dsn):
    from core import audit_sample

    model = "<b>ui-audit-fixture</b>"

    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            t = await tasks.start(conn, tasks.Brief(instruction=HOSTILE, base_sha="b" * 40))
            await ledger.append(
                conn,
                t,
                "review.decided",
                {
                    "candidate": {"sha": "c" * 40, "turn_id": "x"},
                    "verdict": "pass",
                    "reviewer_verdict": "pass",
                    "predicted_failure": 0.37,
                    "findings": [{"kind": "review", "text": "AUDIT-PAGE-FINDING"}],
                    "model": model,
                    "leg": "session",
                },
            )
            before = await count(dsn)
            posted = await fetch(dsn, "/audit", "POST")
            posted_scores = await fetch(dsn, "/audit/scores", "POST")
            after = await count(dsn)
            got = await fetch(dsn, "/audit")
            got_scores = await fetch(dsn, "/audit/scores")
            scores = audit_sample.render_scores(await audit_sample.scores(conn))
        return t, posted, posted_scores, before, after, got, got_scores, scores

    t, posted, posted_scores, before, after, (status, text), (s_status, s_text), scores = run(go())
    assert posted[0] == posted_scores[0] == 405 and before == after
    assert status == 200 and HOSTILE not in text and "&lt;script&gt;" in text
    assert t in text and f"/task/{t}" not in text and "c" * 40 in text
    assert "AUDIT-PAGE-FINDING" not in text and "0.37" not in text
    # The list page carries no scores: no stratum counts, no model, no verdict.
    assert "Strata" not in text and " N 1 n " not in text and "ui-audit-fixture" not in text
    assert "pass" not in text.replace("pass|changes", "")
    assert '<a href="/audit/scores">' in text
    assert s_status == 200 and model not in s_text and "&lt;b&gt;ui-audit-fixture&lt;/b&gt;" in s_text
    assert "Strata (verdict, w)" in scores and ui_app.esc(scores) in s_text
    assert '<a href="/audit">audit</a>' in text and '<a href="/audit">audit</a>' in s_text
