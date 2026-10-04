"""The status page: server-rendered HTML over `core/` read models, no
JavaScript and no forms. Every route answers GET only (any other method is
a 405). Every value that came from the ledger is escaped. The database
session is read-only (`default_transaction_read_only`), so nothing here can
write a row.

    /                  every task: state, metered spending, attention counts
    /task/ID           one task: its status and its ledger
    /pending           the effects held for Tom
    /attention         the attention log across tasks
    /routines          each routine: period spending, last run, runs
"""

import html
import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import psycopg
from aiohttp import web

from core import broker, ledger, routines, tasks
from core.settings import settings

STYLE = (
    "body{font:14px/1.4 system-ui,sans-serif;margin:1.5rem;max-width:72rem}"
    "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:.2rem .5rem;text-align:left;"
    "vertical-align:top}pre{white-space:pre-wrap;margin:0}nav a{margin-right:1rem}"
)


def esc(value: Any) -> str:
    """A ledger value as safe text."""
    if isinstance(value, datetime):
        value = value.isoformat()
    elif value is None:
        value = ""
    elif not isinstance(value, str):
        value = json.dumps(value, default=str, sort_keys=True)
    return html.escape(value, quote=True)


def table(head: list[str], body: list[list[str]]) -> str:
    """A table; each cell is HTML already (escaped or built here)."""
    rows = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in body)
    return f"<table><tr>{''.join(f'<th>{h}</th>' for h in head)}</tr>{rows}</table>"


def link(task_id: str) -> str:
    return f'<a href="/task/{esc(task_id)}">{esc(task_id)}</a>'


def layout(title: str, body: str) -> web.Response:
    nav = "".join(
        f'<a href="{href}">{name}</a>'
        for href, name in (
            ("/", "tasks"),
            ("/pending", "pending"),
            ("/attention", "attention"),
            ("/routines", "routines"),
        )
    )
    text = (
        f"<!doctype html><meta charset=utf-8><title>{esc(title)}</title><style>{STYLE}</style>"
        f"<nav>{nav}</nav><h1>{esc(title)}</h1>{body}"
    )
    return web.Response(text=text, content_type="text/html")


def usd(micros: int) -> str:
    return esc(tasks.usd(micros))


async def tasks_page(conn) -> web.Response:
    found = await tasks.index(conn)
    return layout(
        "Tasks",
        table(
            ["task", "instruction", "state", "metered spending", "attention", "last row"],
            [
                [
                    link(t["task_id"]),
                    esc((t["instruction"] or "")[:200]),
                    esc(t["state"]),
                    usd(t["spent_usd_micros"]),
                    esc(t["attention_counts"]),
                    esc(t["last_at"]),
                ]
                for t in found
            ],
        ),
    )


async def task_page(conn, task_id: str) -> web.Response:
    try:
        await tasks.brief(conn, task_id)
        state = await tasks.status(conn, task_id)
    except KeyError:
        raise web.HTTPNotFound(text=f"no task {task_id}") from None
    rows = await ledger.read(conn, task_id)
    body = (
        f"<pre>{esc(json.dumps(state, indent=2, default=str, sort_keys=True))}</pre><h2>Ledger</h2>"
        + table(
            ["id", "at", "type", "payload"],
            [
                [esc(r["id"]), esc(r.get("at")), esc(r["type"]), f"<pre>{esc(r['payload'])}</pre>"]
                for r in rows
            ],
        )
    )
    return layout(f"Task {task_id}", body)


async def pending_page(conn) -> web.Response:
    held = await broker.pending(conn)
    return layout(
        "Pending approvals",
        table(
            ["task", "effect", "action", "target", "class"],
            [
                [
                    link(h["task_id"]),
                    esc(h.get("effect_id")),
                    esc(h.get("action_type")),
                    esc(h.get("target")),
                    esc(h.get("effect_class")),
                ]
                for h in held
            ],
        ),
    )


async def attention_page(conn) -> web.Response:
    entries = await tasks.attention_log(conn)
    return layout(
        "Attention log",
        table(
            ["at", "task", "entry"],
            [
                [
                    esc(e["at"]),
                    link(e["task_id"]),
                    f"<pre>{esc({k: v for k, v in e.items() if k not in ('at', 'task_id')})}</pre>",
                ]
                for e in entries
            ],
        ),
    )


async def routines_page(conn) -> web.Response:
    body = ""
    for rep in await routines.reports(conn):
        last = rep["last"]
        body += (
            f"<h2>{esc(rep['name'])}</h2><p>{esc(routines.listing(rep))}</p>"
            f"<p>last result: {esc(last['summary'] if last else '')}; need: {esc(rep['need'])}; "
            f"mission item: {esc(rep['mission_item'])}</p>"
            + table(
                ["run", "outcome", "state", "metered spending", "at"],
                [
                    [
                        link(r["run"]) if r["run"] else "",
                        esc(r["outcome"]),
                        esc(r["state"]),
                        usd(r["spent_usd_micros"]),
                        esc(r["at"]),
                    ]
                    for r in rep["runs"]
                ],
            )
        )
    return layout("Routines", body or "<p>No routines.</p>")


def make_app(dsn: str | None = None) -> web.Application:
    """The application. Each request opens its own read-only session."""
    dsn = dsn or settings.dsn()

    def view(page: Callable[..., Awaitable[web.Response]]):
        async def handler(request: web.Request) -> web.Response:
            async with await psycopg.AsyncConnection.connect(
                dsn, autocommit=True, options="-c default_transaction_read_only=on"
            ) as conn:
                return await page(conn, *request.match_info.values())

        return handler

    app = web.Application()
    for path, page in (
        ("/", tasks_page),
        ("/task/{task_id}", task_page),
        ("/pending", pending_page),
        ("/attention", attention_page),
        ("/routines", routines_page),
    ):
        app.router.add_get(path, view(page), allow_head=False)
    return app
