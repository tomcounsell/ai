"""The status page: server-rendered HTML over `core/` read models, with
one script (`static/app.js`: the task modal) and no forms. Every route
answers GET only. Every value that came from the ledger is escaped. The
database session is read-only (`default_transaction_read_only`), so
nothing here can write a row.

    /                  every task as a tree under its root, children
                       collapsed: Needs you, Active, then the newest
                       settled (`?all=1`: every one)
    /live              what is alive: the kernel's and bridges' connections,
                       each running turn, its processes and subagents
    /routines          each routine: last run, period spending, runs
    /attention         the attention log, newest first
    /task/ID           one task: what came after each merge, its status,
                       and its ledger
    /task/ID/modal     the task's metadata, for the modal a row click opens

Every page carries the old dashboard's header (the pages, and the kernel
and each bridge, green with its uptime or red when not connected) and its
theme (`static/style.css`). A task's id is never printed: a row carries it
as `data-task`, a link in its href.
"""

import html
import json
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from aiohttp import web

from core import ledger, outcomes, rollout, routines, runs, tasks
from core.settings import settings

# A task in one of these states has nothing left to run.
SETTLED = {"merged", "ended", "stopped", "calibration", "node"}
RECENT = 15


def esc(value: Any) -> str:
    """A ledger value as safe text."""
    if isinstance(value, datetime):
        value = value.isoformat()
    elif value is None:
        value = ""
    elif not isinstance(value, str):
        value = json.dumps(value, default=str, sort_keys=True)
    return html.escape(value, quote=True)


def table(
    head: list[str], body: list[list[str]], *, tasks: list[str | None] | None = None, shown: int = 0
) -> str:
    """A table; each cell is HTML already (escaped or built here). A head
    starting with `#` is a number column. `tasks`, one per row, makes a
    row a task's: a click on it opens the task's modal. `shown` rows show;
    the rest collapse under a toggle."""
    num = [h.startswith("#") for h in head]
    heads = "".join(f"<th{' class=n' if n else ''}>{h.lstrip('#')}</th>" for h, n in zip(head, num))
    rows = "".join(
        f"<tr{task_attr(task)}{' class=child' if shown and i >= shown else ''}>"
        + "".join(f"<td{' class=n' if n else ''}>{c}</td>" for c, n in zip(r, num))
        + "</tr>"
        for i, (r, task) in enumerate(zip(body, tasks or [None] * len(body)))
    )
    if shown and len(body) > shown:
        rows += (
            f"<tr><td colspan={len(head)}><label class=toggle><input type=checkbox hidden>"
            f"<span>{len(body) - shown} older</span></label></td></tr>"
        )
    return f"<div class=wrap><table><thead><tr>{heads}</tr></thead><tbody>{rows}</tbody></table></div>"


def task_attr(task_id: str | None) -> str:
    return f' data-task="{esc(task_id)}"' if task_id else ""


def task_link(task_id: str, text: str) -> str:
    """The task's page, labelled with `text` (its instruction, a time), never its id."""
    return f'<a class=what href="/task/{esc(task_id)}">{esc(text)}</a>'


NAV = (("/", "Tasks"), ("/live", "Live"), ("/routines", "Routines"), ("/attention", "Attention"))


def health(services: list[dict[str, Any]]) -> str:
    """The old header's Services group: each service green with its uptime, or red."""
    items = "".join(
        f"<span data-up=yes title='{esc(s['commit'] or '')}'>{esc(s['name'])}<i>{ago(s['since'])}</i></span>"
        if s["since"]
        else f"<span data-up=no>{esc(s['name'])}<i>down</i></span>"
        for s in services
    )
    return f"<div class=health><span>Services</span>{items}</div>"


async def layout(conn, title: str, body: str, *, reload: int | None = None) -> web.Response:
    """A whole page: the header, the body, and the task modal it can open.
    `reload` reloads the page every that many seconds while no modal is open."""
    nav = "".join(
        f'<a href="{href}"{" aria-current=page" if name == title else ""}>{name}</a>' for href, name in NAV
    )
    own = "" if title in dict(NAV).values() else f"<h2>{esc(title)}</h2>"
    text = f"""<!doctype html>
<html lang=en>
<meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{esc(title)} — Valor System</title>
<link rel=icon href=/static/favicon-32.png>
<link rel=stylesheet href=/static/style.css>
<script src=/static/app.js defer></script>
<body{f" data-reload={reload}" if reload else ""}>
<header><nav><h1><a href=/>Valor System</a></h1>{nav}</nav>{health(await rollout.services(conn))}</header>
{own}{body}
<dialog><button aria-label=Close>✕</button><div></div></dialog>
"""
    return web.Response(text=text, content_type="text/html")


def usd(micros: int) -> str:
    return esc(f"${micros / 1_000_000:,.2f}")


def _dt(value: Any) -> datetime | None:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if isinstance(value, datetime) else None


def when(value: Any) -> str:
    """A time as the machine's local `MM-DD HH:MM`."""
    dt = _dt(value)
    return esc(dt.astimezone().strftime("%m-%d %H:%M")) if dt else ""


def span(seconds: float) -> str:
    """A length of time as `40s`, `3m`, `2h` or `4d`."""
    s = max(0, int(seconds))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{s // size}{unit}"
    return f"{s}s"


def ago(value: Any) -> str:
    """How long ago a time (or a Unix timestamp) was, as `span` gives it."""
    if isinstance(value, float):
        return span(time.time() - value)
    dt = _dt(value)
    return span(time.time() - dt.timestamp()) if dt else ""


def first_line(text: str | None, width: int = 110) -> str:
    line = (text or "").strip().split("\n", 1)[0]
    return line if len(line) <= width else line[: width - 1].rstrip() + "…"


def kind(chat: dict[str, Any] | None, routine: str | None) -> str:
    if chat:
        return f"chat · {chat.get('channel')}"
    if routine:
        return f"routine · {routine}"
    return ""


def where(t: dict[str, Any]) -> str:
    """The muted line under a task: project · kind · harness and model."""
    harness = f"{t['harness_name'] or ''} {t['model'] or ''}".strip()
    return " · ".join(p for p in (t["project"], kind(t["chat"], t["routine"]), harness) if p)


def badge(state: str) -> str:
    return f"<span class=state data-state='{esc(state)}'>{esc(state)}</span>"


def attention_cell(t: dict[str, Any]) -> str:
    """Only what is non-zero: the open questions, then each kind's count."""
    parts = [f"<span class=ask title='{esc(q)}'>open question</span>" for q in t["open_questions"]] + [
        esc(f"{c['total']} {k}") for k, c in t["attention_counts"].items() if c["total"]
    ]
    return "<br>".join(parts + ([after_counts(t)] if after_counts(t) else []))


HEAD = ["task", "state", "started", "#last", "#spent", "attention"]


def task_tr(t: dict[str, Any], spent: int, *, lead: str = "", child: bool = False) -> str:
    """One task's row; `lead` goes before its instruction (a tree's toggle)."""
    state = badge(t["state"])
    if t["failed_step"]:
        state += " " + badge("failed")
    if t["turn_open"]:
        state += " <span class=running>● turn running</span>"
    return (
        f"<tr{task_attr(t['task_id'])}{' class=child' if child else ''}>"
        f"<td>{lead}{task_link(t['task_id'], first_line(t['instruction']))}<div class=where>{esc(where(t))}</div></td>"
        f"<td>{state}</td>"
        f"<td class=t>{when(t['created_at'])}</td>"
        f"<td class=n>{esc(ago(t['last_at']))}</td>"
        f"<td class=n>{usd(spent)}</td>"
        f"<td>{attention_cell(t)}</td>"
        "</tr>"
    )


def task_table(bodies: list[str]) -> str:
    """Task rows under the task head; each tree is its own <tbody>, so its toggle opens only its children."""
    if not bodies:
        return "<p class=empty>None.</p>"
    heads = "".join(f"<th{' class=n' if h.startswith('#') else ''}>{h.lstrip('#')}</th>" for h in HEAD)
    return f"<div class=wrap><table><thead><tr>{heads}</tr></thead>{''.join(bodies)}</table></div>"


def _needs_you(t: dict[str, Any]) -> bool:
    return bool(t["open_questions"] or t["failed_step"]) and t["state"] not in SETTLED


async def tasks_page(conn, request: web.Request) -> web.Response:
    found = await tasks.index(conn)
    by_id = {t["task_id"]: t for t in found}

    def root(t: dict[str, Any]) -> str:
        seen = set()
        while t["parent_id"] in by_id and t["task_id"] not in seen:
            seen.add(t["task_id"])
            t = by_id[t["parent_id"]]
        return t["task_id"]

    trees: dict[str, list[dict[str, Any]]] = {}
    for t in found:  # newest first, so each tree's members stay newest first
        trees.setdefault(root(t), []).append(t)

    active, recent = [], []
    for r, members in trees.items():
        (active if any(m["state"] not in SETTLED for m in members) else recent).append(r)
    newest = lambda r: max(m["last_at"] for m in trees[r])
    active.sort(key=newest, reverse=True)
    recent.sort(key=newest, reverse=True)
    settled = len(recent)
    if "all" not in request.query:
        recent = recent[:RECENT]

    def tree(r: str) -> str:
        """The root's row, then its children's, collapsed until its toggle is checked."""
        members = trees[r]
        children = [m for m in members if m["task_id"] != r]
        spent = sum(m["spent_usd_micros"] for m in members)
        toggle = ""
        if children:
            live = sum(m["state"] not in SETTLED or m["turn_open"] for m in children)
            count = f"{len(children)}{f' · {live} open' if live else ''}"
            toggle = f"<label class=toggle title='child tasks'><input type=checkbox hidden><span>{count}</span></label>"
        return (
            "<tbody>"
            + task_tr(by_id[r], spent, lead=toggle)
            + "".join(task_tr(c, c["spent_usd_micros"], child=True) for c in children)
            + "</tbody>"
        )

    needs = [t for t in found if _needs_you(t)]
    body = "<h2>Needs you</h2>" + task_table(
        [f"<tbody>{''.join(task_tr(t, t['spent_usd_micros']) for t in needs)}</tbody>"] if needs else []
    )
    body += "<h2>Active</h2>" + task_table([tree(r) for r in active])
    note = (
        f" <small>newest {len(recent)} of {settled} · <a href='/?all=1'>all</a></small>"
        if settled > len(recent)
        else ""
    )
    body += f"<h2>Recent{note}</h2>" + task_table([tree(r) for r in recent])
    return await layout(conn, "Tasks", body)


async def live_page(conn, request: web.Request) -> web.Response:
    alive = await runs.live(conn)
    services = table(
        ["service", "commit", "connected", "#up"],
        [
            [
                f"<span data-up={'yes' if c['since'] else 'no'}>{esc(c['name'])}</span>",
                f"<span class=mono>{esc((c['commit'] or '')[:9])}</span>",
                when(c["since"]) if c["since"] else "<span data-up=no>not connected</span>",
                esc(ago(c["since"])),
            ]
            for c in await rollout.services(conn)
        ],
    )
    body = f"<h2>Services</h2>{services}<h2>Turns <small>{len(alive['turns'])} running</small></h2>"
    if not alive["turns"]:
        body += "<p class=empty>No turn is running.</p>"
    rows = []
    for t in alive["turns"]:
        started = t["at"].timestamp()
        subs = [s for s in t["subagents"] if not s["finished"] or s["at"] >= started]
        agents = "".join(
            f"<li style='--depth:{int(s['depth'])}'>"
            + (
                "<span class=running>● running</span>"
                if not s["finished"]
                else "<span class=muted>done</span>"
            )
            + f" {esc(s['type'])} <span class=muted>{esc(s['description'])} · {esc(ago(s['at']))}</span></li>"
            for s in subs
        )
        rows.append(
            [
                task_link(t["task_id"], first_line(t["instruction"]))
                + (f"<ul class=subagents>{agents}</ul>" if agents else ""),
                badge(t["state"] or ""),
                esc(f"{t['harness'] or ''} {t['model'] or ''}".strip()),
                esc(ago(t["at"])),
                esc(len(t["pids"])) if t["pids"] else "<span class=ask>none</span>",
            ]
        )
    if rows:
        body += table(
            ["task and subagents", "state", "harness", "#running for", "#processes"],
            rows,
            tasks=[t["task_id"] for t in alive["turns"]],
        )
    body += "<h2>Recently ended</h2>" + table(
        ["task", "state", "outcome", "#took", "#ended"],
        [
            [
                task_link(t["task_id"], first_line(t["instruction"])),
                badge(t["state"] or ""),
                esc(t["outcome"]),
                esc(span((t["at"] - t["started"]).total_seconds())),
                esc(ago(t["at"])) + " ago",
            ]
            for t in alive["ended"]
        ],
        tasks=[t["task_id"] for t in alive["ended"]],
    )
    body += "<p class=muted>Reloads every 5 seconds while no task is open.</p>"
    return await layout(conn, "Live", body, reload=5)


def after_counts(t: dict[str, Any]) -> str:
    """A task's after-merge counts in the index, empty with no merge."""
    counts = (
        ("merged", "merges"),
        ("feedback after", "feedback_after"),
        ("used", "used"),
        ("reworked by", "reworked"),
    )
    return esc(", ".join(f"{name} {t[key]}" for name, key in counts if t[key]))


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
        task_link(e["task_id"], f"{e['days_after']} days later") + esc(f": {', '.join(e[key])}")
        for e in entries
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


async def _task(conn, request: web.Request) -> tuple[tasks.Brief, dict[str, Any], list[dict[str, Any]]]:
    task_id = request.match_info["task_id"]
    try:
        return (
            await tasks.brief(conn, task_id),
            await tasks.status(conn, task_id),
            await ledger.read(conn, task_id),
        )
    except KeyError:
        raise web.HTTPNotFound(text="no such task") from None


async def task_page(conn, request: web.Request) -> web.Response:
    b, state, rows = await _task(conn, request)
    body = (
        f"<p>{esc(b.instruction)}</p>"
        + after_merge_html(await outcomes.after_merge(conn, b, rows))
        + f"<details><summary>status</summary><pre>{esc(json.dumps(state, indent=2, default=str, sort_keys=True))}</pre></details>"
        + "<h2>Ledger</h2>"
        + table(
            ["#row", "at", "type", "payload"],
            [[esc(r["id"]), when(r["at"]), esc(r["type"]), f"<pre>{esc(r['payload'])}</pre>"] for r in rows],
        )
    )
    return await layout(conn, first_line(b.instruction, 80), body)


def facts(pairs: list[tuple[str, str]]) -> str:
    """A definition list of the pairs whose value (HTML already) is not empty."""
    return "<dl>" + "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in pairs if v) + "</dl>"


async def task_modal(conn, request: web.Request) -> web.Response:
    """Everything known about a task, as the old session modal showed a session's."""
    b, state, rows = await _task(conn, request)
    turns = runs.turns(rows)
    session = next((t["session"] for t in reversed(turns) if t["session"]), None)
    open_turns = sum(t["ended"] is None for t in turns)
    asked = [a["question"] for a in state["attention"] if a["kind"] == "question" and a["answer"] is None]
    counts = ", ".join(f"{c['total']} {k}" for k, c in state["attention_counts"].items() if c["total"])
    failed = state["failed_step"]
    candidate = state["candidate"]
    chat = f"{b.chat.get('channel')} {b.chat.get('chat_id')}" if b.chat else ""
    details = facts([
        ("project", esc((b.project or {}).get("name"))),
        ("repository", esc(tasks.project_name(b)) if b.origin_url or b.workspace else ""),
        ("kind", esc(kind(b.chat, b.routine) or "task")),
        ("chat", esc(chat)),
        ("parent", task_link(b.parent_id, "open parent") if b.parent_id else ""),
        ("harness", esc(f"{b.harness_name} {b.model}")),
        ("effect ceiling", esc(b.max_effect_class)),
        ("workspace", f"<span class=mono>{esc(b.workspace)}</span>"),
        ("branch", f"<span class=mono>{esc(b.target_branch)}</span>"),
        ("base", f"<span class=mono>{esc((b.base_sha or '')[:9])}</span>"),
        ("origin", f"<span class=mono>{esc(b.origin_url)}</span>"),
        ("session", f"<span class=chip>claude --resume {esc(session)}</span>" if session else ""),
    ])  # fmt: skip
    timing = facts([
        ("started", f"{when(b.created_at)} · {ago(b.created_at)} ago"),
        ("last row", f"{when(rows[-1]['at'])} · {ago(rows[-1]['at'])} ago" if rows else ""),
        ("turns", esc(f"{len(turns)}{f', {open_turns} running' if open_turns else ''}")),
        ("spent", usd(state["spent_usd_micros"])
            + (f" · tree {usd(state['tree_spent_usd_micros'])}" if state["children"] else "")),
        ("open calls", esc(len(state["open_calls"]) or "")),
        ("plan", f"<span class=mono>{esc(state['plan'])}</span>" if state["plan"] else ""),
        ("rounds", esc(", ".join(f"{v} {k.replace('_', ' ')}" for k, v in state["loops"].items() if v))),
        ("candidate", f"<span class=mono>{esc(candidate['sha'][:9])}</span>" if candidate else ""),
        ("failed step", f"<span class=ask>{esc(first_line(json.dumps(failed['payload'], default=str), 160))}</span>"
            if failed else ""),
        ("delivered", esc(state["delivered"])),
        ("attention", esc(counts)),
    ])  # fmt: skip
    children = ""
    if state["children"]:
        children = "<h3>Children</h3>" + table(
            ["task", "state", "#spent"],
            [
                [task_link(c["task_id"], first_line(c["instruction"], 80)), badge(c["state"]),
                 usd(c["tree_spent_usd_micros"])]
                for c in state["children"]
            ],
            tasks=[c["task_id"] for c in state["children"]],
        )  # fmt: skip
    recent = turns[-8:][::-1]
    turn_rows = ""
    if recent:
        turn_rows = f"<h3>Turns <small class=muted>newest {len(recent)} of {len(turns)}</small></h3>" + table(
            ["started", "state", "model", "outcome", "#took"],
            [
                [
                    when(t["started"]),
                    badge(t["state"] or ""),
                    esc(t["model"]),
                    esc(t["outcome"]) if t["ended"] else "<span class=running>● running</span>",
                    esc(
                        span(
                            ((t["ended"] or datetime.now(t["started"].tzinfo)) - t["started"]).total_seconds()
                        )
                    ),
                ]
                for t in recent
            ],
        )
    text = (
        f"<article class=modal data-task='{esc(b.id)}'>"
        f"<header><h2 title='{esc(first_line(b.instruction, 300))}'>{esc(first_line(b.instruction, 90))}</h2>"
        f"{badge(state['state'])}<a href='/task/{esc(b.id)}'>full page →</a></header>"
        f"<section><div class=columns><div><h3>Details</h3>{details}</div>"
        f"<div><h3>Timing &amp; liveness</h3>{timing}</div></div>"
        + "".join(f"<h3>Asked</h3><pre class=ask>{esc(q)}</pre>" for q in asked)
        + f"<h3>Instruction</h3><pre>{esc(b.instruction)}</pre>{children}{turn_rows}</section></article>"
    )
    return web.Response(text=text, content_type="text/html")


def _entry(e: dict[str, Any]) -> tuple[str, str, str]:
    """An attention entry as what was said, the answer or note, and who said it."""
    said = e.get("question") or e.get("feedback") or e.get("note") or e.get("verdict") or ""
    answer = e.get("answer") if e["kind"] == "question" else ""
    who = (e.get("provenance") or {}).get("by") or ""
    if (e.get("provenance") or {}).get("role_played"):
        who += " (role-played)"
    if e["kind"] == "question" and e.get("answer") is None:
        answer = "<span class=ask>unanswered</span>"
        return esc(said), answer, who
    return esc(said), esc(answer), who


async def attention_page(conn, request: web.Request) -> web.Response:
    entries = (await tasks.attention_log(conn))[::-1]
    body = table(
        ["when", "kind", "said", "answer", "by"],
        [
            [
                when(e["at"]),
                esc(e["kind"]),
                f"<div class=clamp>{said}</div>",
                f"<div class=clamp>{answer}</div>",
                esc(who),
            ]
            for e in entries
            for said, answer, who in [_entry(e)]
        ],
        tasks=[e["task_id"] for e in entries],
    )
    return await layout(conn, "Attention", body)


async def routines_page(conn, request: web.Request) -> web.Response:
    reps = await routines.reports(conn)
    if not reps:
        return await layout(conn, "Routines", "<p class=empty>No routines.</p>")
    days = reps[0]["period_days"]
    summary = table(
        ["routine", "outcome", "#last run", "last result", f"#runs, {days}d", f"#spent, {days}d"],
        [
            [
                f"<a href='#{esc(rep['name'])}'>{esc(rep['name'])}</a>",
                esc(last["outcome"]) if last else "<span class=muted>never run</span>",
                esc(ago(last["at"])) + " ago" if last else "",
                esc(last["summary"]) if last else "",
                esc(rep["period_runs"]),
                usd(rep["period_spent_usd_micros"]),
            ]
            for rep in reps
            for last in [rep["last"]]
        ],
        tasks=[(rep["last"] or {}).get("run") for rep in reps],
    )
    body = summary
    for rep in reps:
        runs_ = list(reversed(rep["runs"]))
        body += (
            f"<h2 id='{esc(rep['name'])}'>{esc(rep['name'])}</h2>"
            f"<p class=muted>mission item: {esc(rep['mission_item'])}<br>need: {esc('; '.join(rep['need']))}</p>"
            + table(
                ["at", "outcome", "state", "#spent"],
                [
                    [
                        when(r["at"]),
                        esc(r["outcome"]),
                        badge(r["state"]) if r["state"] else "",
                        usd(r["spent_usd_micros"]),
                    ]
                    for r in runs_
                ],
                tasks=[r["run"] for r in runs_],
                shown=5,
            )
        )
    return await layout(conn, "Routines", body)


def make_app(dsn: str | None = None) -> web.Application:
    """The application. Each request opens its own read-only session."""
    dsn = dsn or settings.dsn()

    def view(page: Callable[..., Awaitable[web.Response]]):
        async def handler(request: web.Request) -> web.Response:
            async with await psycopg.AsyncConnection.connect(
                dsn, autocommit=True, options="-c default_transaction_read_only=on"
            ) as conn:
                return await page(conn, request)

        return handler

    app = web.Application()
    for path, page in (
        ("/", tasks_page),
        ("/live", live_page),
        ("/routines", routines_page),
        ("/task/{task_id}", task_page),
        ("/task/{task_id}/modal", task_modal),
        ("/attention", attention_page),
    ):
        app.router.add_get(path, view(page), allow_head=False)
    app.router.add_static("/static", Path(__file__).parent / "static")
    return app
