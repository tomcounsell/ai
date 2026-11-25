#!/usr/bin/env python3
"""valor-record chats
valor-record messages [CHAT] [--search TEXT] [--limit N] [--before RECORD] [--json]
valor-record tasks [--search TEXT] [--limit N] [--json]
valor-record task ID [--json]
valor-record memory WORDS [--limit N] [--offset N] [--json]
valor-record call CODE [--before RECORD] [--limit N] [--json]
valor-record call [CODE] --search WORDS [--limit N] [--json]
valor-record hours [--since DATE] [--until DATE] [--json]
valor-record waits [--json]
valor-record facts [--as-of DATE] [--kind K] | --history FACT | --related FACT_OR_ENTITY [--depth N] [--json]
valor-record sources [--json]
valor-record person NAME_OR_DESCRIPTION [--json]
valor-record fact add CLAIM --kind K --source CITE --since DATE [--source-at DATE] [--about ENTITY]...
             [--reported R --evidence E --why-differs W]
valor-record fact supersede FACT CLAIM --reason R (and add's options)
valor-record fact confirm FACT --answer LEDGER_ROW [--by WHO]
valor-record entity add NAME --kind K [--alias A]...
valor-record entity alias ENTITY ALIAS
valor-record link A B --kind K [--source CITE]
valor-record source CHANNEL [--failed] [--upto CURSOR]
valor-record act start --instruction TEXT --by WHO --context TEXT [--project P] [--in CHAT] [--key K]
valor-record act answer TASK --text TEXT --by WHO [--via HOW] [--question-id Q] [--key K]
valor-record act steer TASK --text TEXT --by WHO [--key K]
valor-record act steer --in CHAT --from RECORD --by WHO [--text NOTE] [--key K]
valor-record act stop TASK --why TEXT --by WHO [--via HOW] [--key K]

Reads the ledger of this turn's own project: its chats, the messages each
received and what they were bound to, its tasks and what became of them.
The kernel answers (`core/record.py`) at `$VALOR_RECORD_URL`, which carries
the turn's own gateway token, so the project is the kernel's to say. CHAT
is `<channel>:<id>` as `chats` prints it. Messages print oldest first. `memory` searches what Valor remembers for
this project (the records recall reads, and standards of working), best
match first, each record quoted as data. `call` reads a Meet call's
transcript by its meeting code, oldest first a page at a time: who said
or typed what, what Valor spoke and posted; `call --search WORDS` finds
the lines of every call of the project (or of CODE) holding all the
words, and the calls' summaries, newest first, each with its call's
date, title and code and how to read the call around it. `hours` prints the blocks of
time Valor worked on this project (Bangkok days, default this month), as
the `hours` routine writes them to the project's calendar. `waits`
prints what is waiting now, oldest first: each task's open question and
each post a chat session awaits a reply to, with who or what it waits on,
since when, how long, and how much of that was Tom's working hours.
`facts` prints what is true about the project now or at a date, a fact's
history, or what a fact or entity links to; `fact`, `entity`, `link` and
`source` write them (docs/memory.md, Facts), each printing the name of
what it wrote; the same write asked twice writes once. Everything starts
inferred; only `fact confirm`, naming a person's answer, confirms.
`person` prints the dossier of each of the project's people a name, an
alias or a loose description fits, best first: internal or external, and
their live facts (team, role, organisation, asks, decisions, what is owed
and delivered), each dated and sourced (docs/memory.md, People).
`act` is a chat session's hands on its tasks, done now, in the turn
(`core/gateway.py`, `tools/chat_tasks.py`): it starts, answers, steers or
stops a task and prints `done` and what it did (a start prints the new
task's id), or the error, exiting 1; so the turn writes its reply from
what happened. `act steer --in CHAT --from RECORD` hands the message
`messages` prints as #RECORD to CHAT's session, any project's on any Mac,
as said by whoever said it; `--text` is the session's note beside it.
The same command again in a turn is the same request
(`--key` defaults to a hash of it): it answers what the first did, a
failure included, and never starts a second task; to try a failed one
again, change it or pass a new `--key`. When the outcome is not known yet
it says so and exits 1.
Standard library only, and Python 3.9 (macOS's own `python3`), so it runs
in any workspace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


def _when(at: object) -> str:
    try:
        return datetime.fromisoformat(str(at)).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")  # noqa: UP017
    except ValueError:
        return "an unknown time"


def message_line(r: dict) -> str:
    m = r["message"]
    who = m.get("sender_name") or m.get("sender_id") or "someone"
    ids = f"message {m.get('message_id')}" + (f", replying to {m['reply_to']}" if m.get("reply_to") else "")
    if m.get("handed_on"):
        h = m["handed_on"]
        ids = f"handed on from {h.get('chat')} by the {h.get('project')} session on {h.get('machine')}; not said here"
    bound = r.get("bound") or {}
    to = f"{bound.get('as')} {bound.get('task_id') or ''}".strip() if bound else "not bound yet"
    said = (m.get("text") or "").strip() or "(no text)"
    files = "".join(f" [file: {a.get('name') or 'file'}]" for a in m.get("attachments") or [])
    reaction = f"; {r['reaction']}" if r.get("reaction") else ""
    return (
        f"#{r['record']} {r['chat']} [{_when(m.get('sent_at'))}] {who} ({ids}{reaction}; {to}): {said}{files}"
    )


def task_line(t: dict) -> str:
    where = (
        f"session of {t['chat']}" if t["chat"] else f"under {t['parent_id']}" if t["parent_id"] else "root"
    )
    out = f"{t['task_id']} {t['state']} ({where}, last {_when(t.get('last_at'))}): {(t['instruction'] or '')[:200]}"
    if t.get("open_question"):
        out += f"\n    asked: {t['open_question'][:300]}"
    if t.get("delivered"):
        out += f"\n    delivered: {t['delivered'][:300]}"
    return out


def _age(w: dict) -> str:
    return f"{w['hours']}h ({w['tom_working_hours']}h of Tom's working hours)"


def _due(w: dict) -> str:
    return "when the send has its outcome" if w.get("due") is None else _when(w["due"])


def wait_line(w: dict) -> str:
    if w["kind"] == "question":
        where = w["asked_in"] + (f" ({w['room']})" if w.get("room") else "")
        out = (
            f"question from task {w['task_id']}, asked {_when(w['since'])}, waiting {_age(w)}, via {where}"
            f"\n    asks: {(w['question'] or '')[:300]}\n    blocks: {(w['blocks'] or '')[:200]}"
        )
        if w.get("relayed"):
            r = w["relayed"]
            out += f"\n    relayed in post {r['post']} ({r['status']}), wake due {_due(r)}"
        return out
    where = w["chat"] + (f" ({w['room']})" if w.get("room") else "")
    return (
        f"post {w['post']} in {where} ({w['status']}), set {_when(w['since'])}, waiting {_age(w)}, "
        f"wake due {_due(w)}\n    note: {(w.get('note') or '(none: who it waits on is unknown)')[:300]}"
    )


def fact_write(args: argparse.Namespace) -> dict | None:
    """The write a `fact`, `entity`, `link` or `source` asks for, as
    `core/memory.py` `fact_write` takes it; None for a read."""
    if args.verb == "fact" and args.op == "confirm":
        return {"op": "confirm", "fact": args.fact, "answer": args.answer, "by": args.by}
    if args.verb == "fact":
        w = {"op": "fact" if args.op == "add" else "supersede", "claim": args.claim, "kind": args.kind,
             "source": args.source, "since": args.since, "source_at": args.source_at, "about": args.about,
             "reported": args.reported, "evidence": args.evidence, "why_differs": args.why_differs}  # fmt: skip
        if args.op == "supersede":
            w.update(old=args.old, reason=args.reason)
        return {k: v for k, v in w.items() if v is not None}
    if args.verb == "entity" and args.op == "add":
        return {"op": "entity", "name": args.name, "kind": args.kind, "aliases": args.alias or []}
    if args.verb == "entity":
        return {"op": "alias", "entity": args.entity, "alias": args.alias}
    if args.verb == "link":
        return {"op": "link", "a": args.a, "b": args.b, "kind": args.kind, "source": args.source or ""}
    if args.verb == "source":
        # Each read is its own: the time keeps a second read from being the first's repeat.
        when = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        return {
            "op": "source",
            "channel": args.channel,
            "ok": not args.failed,
            "upto": args.upto or "",
            "at": when,
        }
    return None


NOT_KNOWN = (
    "valor-record: the outcome is not known yet{effect}; read `valor-record task <your Brief's Task id>` "
    "before you report it, and say it is not known if it is still not there"
)


def act_request(args: argparse.Namespace) -> dict:
    """The `act` body for the gateway: `task.<verb>`'s target and payload,
    as the chat_tasks performers read them, and the request's key."""
    names = {"start": ("instruction", "by", "context", "project", "in"), "answer": ("text", "by", "via", "question_id"),
             "steer": ("text", "by", "in", "from"), "stop": ("why", "by", "via")}[args.op]  # fmt: skip
    attr = {"in": "into", "from": "record"}
    payload = {n: getattr(args, attr.get(n, n), None) for n in names}
    payload = {k: v.lstrip("#") if k == "from" else v for k, v in payload.items() if v is not None}
    target = "work" if args.op == "start" else payload["in"] if payload.get("in") else args.task
    key = (
        args.key
        or hashlib.sha256(json.dumps([args.op, target, payload], sort_keys=True).encode()).hexdigest()[:16]
    )
    return {"verb": args.op, "target": target, "payload": payload, "key": key}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="valor-record", description="Read this project's record.")
    sub = parser.add_subparsers(dest="verb", required=True)
    sub.add_parser("chats", help="the project's chats, rooms and sessions")
    msgs = sub.add_parser("messages", help="messages received, latest first or matching --search")
    msgs.add_argument("chat", nargs="?", help="one chat, as `chats` prints it; default every chat")
    msgs.add_argument("--before", help="only messages older than this record number (#N)")
    tasks = sub.add_parser("tasks", help="the project's tasks, newest first")
    for p in (msgs, tasks):
        p.add_argument("--search", "-s", help="text to search for")
        p.add_argument("--limit", "-n", type=int, default=50, help="how many (at most 200)")
    one = sub.add_parser("task", help="one task's ledger rows")
    one.add_argument("id")
    call = sub.add_parser(
        "call", help="a Meet call's transcript by its meeting code, or the calls' lines by --search"
    )
    call.add_argument("code", nargs="?", help="the meeting code; with --search, only that call")
    call.add_argument("--search", "-s", help="words said, typed or summarised in the project's calls")
    call.add_argument("--before", help="only lines older than this record number (#N)")
    call.add_argument("--limit", "-n", type=int, default=200, help="how many (at most 200)")
    hrs = sub.add_parser("hours", help="the time Valor worked on this project, block by block")
    hrs.add_argument("--since", help="first Bangkok day, YYYY-MM-DD (default the 1st of this month)")
    hrs.add_argument("--until", help="last Bangkok day, YYYY-MM-DD (default today)")
    sub.add_parser("waits", help="what is waiting now, on whom, since when")
    mem = sub.add_parser("memory", help="search this project's memory, best match first")
    mem.add_argument("words", nargs="+", help="what to search for")
    mem.add_argument("--limit", "-n", type=int, default=10, help="how many (at most 50)")
    mem.add_argument("--offset", type=int, help="skip this many matches, for the next page")
    facts = sub.add_parser("facts", help="what is true about the project, now or at a date")
    facts.add_argument("--as-of", help="a date (YYYY-MM-DD or an ISO time); default now")
    facts.add_argument("--kind", help="only facts of this kind")
    facts.add_argument("--history", help="a fact's supersession chain, oldest first")
    facts.add_argument("--related", help="what a fact or entity links to")
    facts.add_argument("--depth", type=int, help="how many hops --related walks (default 2, at most 6)")
    sub.add_parser("sources", help="each source of the project's facts and how far it was read")
    person = sub.add_parser("person", help="a person's dossier, by name or loose description")
    person.add_argument("about", nargs="+", help="their name, another name, or how you would describe them")
    leaves = list(sub.choices.values())
    fact = sub.add_parser("fact", help="add, supersede or confirm a fact").add_subparsers(
        dest="op", required=True
    )
    for op in ("add", "supersede"):
        f = fact.add_parser(op)
        if op == "supersede":
            f.add_argument("old", help="the fact it supersedes")
            f.add_argument("--reason", required=True)
        f.add_argument("claim")
        f.add_argument("--kind", required=True, help="free text: pricing, customer, release, decision...")
        f.add_argument(
            "--source", required=True, help="commit:<sha>, telegram:<chat>/<message>, a ledger row"
        )
        f.add_argument("--since", required=True, help="when it became true")
        f.add_argument("--source-at", help="the source's date, if not --since")
        f.add_argument("--about", action="append", help="an entity it is about (repeatable)")
        for k in ("--reported", "--evidence", "--why-differs"):
            f.add_argument(k)
        leaves.append(f)
    c = fact.add_parser("confirm")
    c.add_argument("fact")
    c.add_argument("--answer", required=True, help="the ledger row of the person's answer (<ledger>/<id>)")
    c.add_argument("--by", default="tom")
    entity = sub.add_parser("entity", help="add an entity or a name it has had").add_subparsers(
        dest="op", required=True
    )
    e = entity.add_parser("add")
    e.add_argument("name")
    e.add_argument("--kind", required=True)
    e.add_argument("--alias", action="append", help="another name it has had (repeatable)")
    a = entity.add_parser("alias")
    a.add_argument("entity")
    a.add_argument("alias")
    link = sub.add_parser("link", help="link two facts or entities")
    link.add_argument("a")
    link.add_argument("b")
    link.add_argument("--kind", required=True)
    link.add_argument("--source")
    src = sub.add_parser("source", help="record a read of a source")
    src.add_argument("channel")
    src.add_argument("--failed", action="store_true", help="the read failed")
    src.add_argument("--upto", help="how far it read: the last sha or message id")
    acts = sub.add_parser("act", help="start, answer, steer or stop a task, now").add_subparsers(
        dest="op", required=True
    )
    ops = []
    for op in ("start", "answer", "steer", "stop"):
        o = acts.add_parser(op)
        if op == "start":
            o.add_argument("--instruction", required=True, help="the request as written, with what it needs")
            o.add_argument(
                "--context", required=True, help="a few lines: where it came from, what the room allows"
            )
            o.add_argument("--project", help="one of the projects your Repos line names")
            o.add_argument("--in", dest="into", help="another chat of this project, slack:<team>/<channel>")
        elif op == "steer":
            o.add_argument("task", nargs="?", help="the task's id; none with --in")
            o.add_argument("--text", help="the words; with --in, your own note beside the message")
            o.add_argument("--in", dest="into", help="hand a message to this chat's session, any project's")
            o.add_argument(
                "--from", dest="record", help="with --in: the message's record number, as messages prints it"
            )
        else:
            o.add_argument("task", help="the task's id")
            o.add_argument("--why" if op == "stop" else "--text", required=True)
        if op in ("answer", "stop"):
            o.add_argument("--via", help="how it came: chat, slack reaction :+1:, meet call")
        if op == "answer":
            o.add_argument("--question-id", help="only this open question")
        o.add_argument("--by", required=True, help="who asked or said it")
        o.add_argument("--key", help="the request's key (default: a hash of the request)")
        ops.append(o)
    for p in [*leaves, c, e, a, link, src, *ops]:
        p.add_argument("--json", action="store_true", help="print the kernel's JSON")
    args = parser.parse_args(argv)
    if (
        args.verb == "act"
        and args.op == "steer"
        and not ((args.into and args.record and not args.task) or (args.task and args.text and not args.into))
    ):
        print("valor-record: act steer takes TASK --text, or --in CHAT --from RECORD", file=sys.stderr)
        return 2

    base = os.environ.get("VALOR_RECORD_URL")
    if not base:
        print("valor-record: VALOR_RECORD_URL is not set; it is set in a kernel turn", file=sys.stderr)
        return 2
    query = {
        "chat": getattr(args, "chat", None),
        "q": " ".join(args.words) if args.verb == "memory" else getattr(args, "search", None),
        "offset": getattr(args, "offset", None),
        "limit": getattr(args, "limit", None),
        "before": (getattr(args, "before", None) or "").lstrip("#") or None,
        "id": getattr(args, "id", None),
        "code": getattr(args, "code", None),
        "since": getattr(args, "since", None),
        "until": getattr(args, "until", None),
    }
    if args.verb == "facts":
        query.update(
            as_of=args.as_of, kind=args.kind, history=args.history, related=args.related, depth=args.depth
        )
    if args.verb == "sources":
        args.verb, query["sources"] = "facts", "1"
    if args.verb == "person":
        args.verb, query["about"] = "people", " ".join(args.about)
    if args.verb == "act":
        return act(base, act_request(args), args.json)
    write = fact_write(args)
    url = f"{base}/{'facts' if write else args.verb}?" + urllib.parse.urlencode(
        {k: v for k, v in query.items() if v is not None and not write}
    )
    data = json.dumps({"writes": [write]}).encode() if write else None
    request = urllib.request.Request(
        url, data=data, headers={"content-type": "application/json"} if write else {}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read()).get("error")
        except ValueError:
            said = None
        print(f"valor-record: {said or e}", file=sys.stderr)
        return 1
    except OSError:
        print("valor-record: the kernel is not answering", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    elif write:
        print(out["names"]["1"])
    elif args.verb == "facts" and "sources" in out:
        for x in out["sources"]:
            print(
                f"{x['channel']}: last read {x['last_ok']}, last tried {x['last_tried']}, up to {x['upto'] or '-'}"
            )
        if not out["sources"]:
            print("nothing found")
    elif args.verb == "facts" and "related" in out:
        for x in out["links"]:
            print(f"link {x['kind']}: {x['a']} - {x['b']}" + (f" ({x['source']})" if x["source"] else ""))
        for x in out["related"]:
            print(f"[{x['hops']} hop{'s' if x['hops'] > 1 else ''}] {x['entry']}")
    elif args.verb == "people":
        print("\n\n".join(x["entry"] for x in out["people"]) or "nobody fits")
    elif args.verb == "facts":
        for x in out["facts"]:
            print(x["entry"])
        if not out["facts"]:
            print("nothing found")
    elif args.verb == "memory":
        for r in out["records"]:
            print(r["entry"])
        if not out["records"]:
            print("nothing found")
        elif out["next"] is not None:
            print(f"(more: --offset {out['next']})")
    elif not out:
        print("nothing found")
    elif args.verb == "chats":
        for c in out:
            print(f"{c['chat']}: {c['room'] or '(no room)'}; sessions: {', '.join(c['sessions']) or 'none'}")
    elif args.verb == "messages":
        for r in reversed(out):
            print(message_line(r))
    elif args.verb == "tasks":
        for t in out:
            print(task_line(t))
    elif args.verb == "waits":
        for w in out:
            print(wait_line(w))
    elif args.verb == "hours":
        for b in out:
            print(
                f"{b['start'][:10]} {b['start'][11:16]}-{b['end'][11:16]} {b['hours']:>4}h  {', '.join(b['tasks'])}"
            )
        print(f"total {round(sum(b['hours'] for b in out), 1)}h")
    elif args.verb == "call" and args.search:
        for r in out:
            title = f"{r['title']} " if r["title"] else ""
            print(f"{r['started'][:10]} {title}(meet {r['code']}) #{r['record']} [{_when(r['at'])}] "
                  f"{r['who']} ({r['how']}): {r['text']}")  # fmt: skip
            print(f"  the call around it: valor-record call {r['code']} --before {r['record'] + 1}")
    elif args.verb == "call":
        for r in reversed(out):
            print(f"#{r['record']} [{_when(r['at'])}] {r['who']} ({r['how']}): {r['text']}".rstrip())
        if len(out) == args.limit:
            print(f"(earlier: --before {out[-1]['record']})")
    else:
        for r in out:
            print(
                f"{r['id']:>7} {_when(r['at'])} {r['type']:<18} {json.dumps(r['payload'], ensure_ascii=False)}"
            )
    return 0


def act(base: str, body: dict, as_json: bool) -> int:
    """Request one act and print what came of it."""
    request = urllib.request.Request(
        f"{base}/act", data=json.dumps(body).encode(), headers={"content-type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read()).get("error")
        except ValueError:
            said = None
        print(f"valor-record: {said or e}", file=sys.stderr)
        return 1
    except OSError:
        print(NOT_KNOWN.format(effect=""), file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    if out["kind"] == "unknown" or (out["kind"] == "refused" and out["error"] == "in flight"):
        print(NOT_KNOWN.format(effect=f" (effect {out['effect_id']})"), file=sys.stderr)
        return 1
    if out["kind"] != "done":
        print(f"valor-record: {out['kind']}: {out['error']}", file=sys.stderr)
        return 1
    if not as_json:
        print("done: " + ", ".join(f"{k} {v}" for k, v in out["result"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
