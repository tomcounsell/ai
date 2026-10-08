"""The status page: server-rendered HTML over `core/` read models, no
JavaScript and no forms. Every route answers GET only (any other method is
a 405). Every value that came from the ledger is escaped. The database
session is read-only (`default_transaction_read_only`), so nothing here can
write a row.

    /                  every task: state, metered spending, attention counts,
                       and what came after its merges
    /task/ID           one task: what came after each merge, its status,
                       and its ledger
    /pending           the effects held for Tom
    /attention         the attention log across tasks
    /routines          each routine: period spending, last run, runs
    /audit             the audit list (blind: no verdict, no count by
                       verdict, no link to the task page, which shows it)
    /audit/scores      the verifier's scores
"""

import html
import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import psycopg
from aiohttp import web

from core import audit_sample, broker, ledger, outcomes, routines, tasks
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
            ("/audit", "audit"),
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
            ["task", "instruction", "state", "metered spending", "attention", "after merge", "last row"],
            [
                [
                    link(t["task_id"]),
                    esc(t["instruction"] or ""),
                    esc(t["state"]),
                    usd(t["spent_usd_micros"]),
                    esc(t["attention_counts"]),
                    after_counts(t),
                    esc(t["last_at"]),
                ]
                for t in found
            ],
        ),
    )


def after_counts(t: dict[str, Any]) -> str:
    """A task's after-merge counts in the index, empty with no merge."""
    if not t["merges"] and not t["used"]:
        return ""
    return esc(
        f"merges {t['merges']}, feedback after {t['feedback_after']}, used {t['used']}, "
        f"reworked by {t['reworked']}"
    )


def _people(entries: list[dict[str, Any]], text: str) -> str:
    return "<br>".join(
        esc(
            f"{e['provenance']['by']}{' (role-played)' if e['provenance']['role_played'] else ''}: {e[text] or ''}"
        )
        for e in entries
    )


def _later(entries: list[dict[str, Any]] | None, key: str) -> str:
    if entries is None:
        return ""
    return "<br>".join(
        esc(f"{e['task_id']} after {e['days_after']} days: {', '.join(e[key])}") for e in entries
    )


def _later_cell(m: dict[str, Any]) -> str:
    cell = esc(m["rework_why"]) if m["later"] is None else _later(m["later"], "shared_code")
    if m["later_unknown"]:
        cell += ("<br>" if cell else "") + esc(f"paths not recorded: {', '.join(m['later_unknown'])}")
    return cell


def _revert(m: dict[str, Any]) -> str:
    r = m["revert"]
    if r is None:
        return esc(m["revert_why"])
    by = r["reverted_by"]
    reverted = "not read" if by is None else ", ".join(e["sha"] for e in by) or "no"
    return esc(f"on branch: {r['on_branch']}; reverted by: {reverted}; cache as of {r['as_of']}")


def after_merge_html(after: dict[str, Any]) -> str:
    """The After merge table, one row per merge, and the Used list of
    deliveries marked with no merge; every value escaped."""
    body = ""
    if after["after_merge"]:
        body += "<h2>After merge</h2>" + table(
            ["head", "merged at", "feedback", "used", "paths", "later, shared code", "later, docs only",
             "revert"],
            [
                [
                    esc(m["head_sha"]),
                    esc(m["merged_at"]),
                    esc(len(m["feedback"])) + ("<br>" if m["feedback"] else "") + _people(m["feedback"], "text"),
                    esc(m["used_count"]) + ("<br>" if m["used"] else "") + _people(m["used"], "note"),
                    esc(m["rework_why"] if m["paths"] is None else ", ".join(m["paths"])),
                    _later_cell(m),
                    "" if m["later_docs"] is None else _later(m["later_docs"], "shared_docs"),
                    _revert(m),
                ]
                for m in after["after_merge"]
            ],
        )  # fmt: skip
    if after["deliveries_used"]:
        body += (
            "<h2>Used</h2><ul>"
            + "".join(
                f"<li>{esc(f'delivery {d["event_id"]} ({d["delivered_at"]}), used {d["used_count"]}')}"
                f"<br>{_people(d['used'], 'note')}</li>"
                for d in after["deliveries_used"]
            )
            + "</ul>"
        )
    return body


async def task_page(conn, task_id: str) -> web.Response:
    try:
        b = await tasks.brief(conn, task_id)
        state = await tasks.status(conn, task_id)
    except KeyError:
        raise web.HTTPNotFound(text=f"no task {task_id}") from None
    rows = await ledger.read(conn, task_id)
    body = (
        after_merge_html(await outcomes.after_merge(conn, b, rows))
        + f"<pre>{esc(json.dumps(state, indent=2, default=str, sort_keys=True))}</pre><h2>Ledger</h2>"
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


async def audit_page(conn) -> web.Response:
    """The list as `python -m core audit` gives it, and nothing else. A task
    id here is text, not a link: the task page shows the verdict. The scores
    are on their own page, as `audit scores` is its own command: a stratum's
    counts beside the list can show how many listed candidates hold each
    verdict, and with one listed candidate, which verdict it holds."""
    found = await audit_sample.sample(conn)
    listing = (
        table(
            ["task", "instruction", "base", "candidate", "read the work"],
            [
                [
                    esc(c["task_id"]),
                    esc(c["instruction"]),
                    esc(c["base_sha"]),
                    esc(c["sha"]),
                    f"<pre>{esc(c['read'])}</pre>",
                ]
                for c in found
            ],
        )
        if found
        else f"<p>{esc(audit_sample.render_list(found))}</p>"
    )
    body = (
        "<p>Label with <code>python -m core audit label TASK SHA pass|changes</code>. "
        'The verifier\'s scores are on <a href="/audit/scores">their own page</a>.</p>'
        f"{listing}"
    )
    return layout("Audit", body)


async def audit_scores_page(conn) -> web.Response:
    """The scores as `python -m core audit scores` prints them."""
    scored = audit_sample.render_scores(await audit_sample.scores(conn))
    return layout("Audit scores", f"<pre>{esc(scored)}</pre>")


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
        ("/audit", audit_page),
        ("/audit/scores", audit_scores_page),
    ):
        app.router.add_get(path, view(page), allow_head=False)
    return app
