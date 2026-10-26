"""Reactions: 👀 on a message Valor reads, then a `handling` reaction that
shows he understood it, then a `done` reaction that shows what came of it,
both picked by the reaction classifier (docs/plans/reactions-fun.md).

Binding (`core/intake.py`) calls `on_bound` in the binding's transaction.
A Telegram or Slack message bound to a task as `start`, `answer`,
`feedback`, `steer`, `stop`, or `none` with a task (Tom's reply to merged
or stopped work) gets a `seen` reaction, 👀, at once. A `start` (handed to
a new task) and a `none` owe their `done` at once (`reaction.owed`); a
`stop` gets 👌 as its done at once. A message bound `none` with no task, or
`heard`, and any message on a channel that does not react (email, local),
gets nothing; so in a team chat whose spec lists it under `mentions`, only
a message that names Valor or replies to him is marked.

When a turn of the task is collected (`core/session.py`, `record`),
`owe` writes, in the collection's transaction, a `reaction.owed` row for
every message with 👀 the turn read (its prompt's window, since the last
turn that spent the steering, and the steers it took mid-turn; a fresh or
compaction turn reads none): `done`, or `stalled` for a turn that ended in
an error, which the next turn's `done` replaces. It carries what the turn
did (`did`: the effects it requested and the tools its transcript shows,
`effect_did`/`tool_did`, in `ORDER`) and whether it sent text in reply.

The resident kernel (`core/serve.py`) asks the classifier for what is due
(`pending`, `react`): a `handling` for a message with 👀 and nothing owed
(`reaction.handling`), and each owed reaction (`reaction.completed`). Both
pick from one palette (`PALETTE`); the activity emoji fit only work the
message asks for or the turn did. An abstain, no port, or both legs
failing `judgement.UNANSWERED_RUNS` times gives `noted` 📝. A message on a
stopped task, which cannot be billed, gets its done unjudged: 📝. A done
the platform refuses is replaced by 📝 (stage `noted`). A message bound
before 2026-10-26 (a 👀 row with no `completes`) is left as it is.

`REACTIONS` is the one mapping, per channel, from a label to what the
bridge places: on Telegram the emoji's exact code points, which the bridge
sends as a standard reaction when Telegram's list has it and as a Premium
custom emoji for that glyph when not (`bridges/telegram/wire.py`); on Slack
the emoji's name. Each `reaction.requested` row carries the `label`, that
`emoji`, its `stage`, and `why` it was set.

The bridge places each `reaction.requested` row through its outbox
(`core/bridge.py`, `ReactionDue`) with its own account, as it sends
notices, and writes `reaction.placed` or `reaction.failed`; a row below a
later stage of the same message is closed `superseded` (`RANK`). There is
no broker effect, approval, or report: a reaction is a reversible mark in a
chat Tom owns and carries no text. Every reaction row is quiet
(`ledger.quiet`), so none moves the task it sits on.

The session reads the reaction on each message beside it (`shown`, through
`intake.chat_line`), and what set it, so asked about an emoji Valor says
why.
"""

import json
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

import psycopg

from core import channels, judgement, ledger, machine, tasks, transcripts

# The palette both classifier questions pick from: per label, the Telegram
# glyph (exact code points, none with the U+FE0F variation selector, which
# Telegram can refuse), the Slack name, and when it fits. Telegram's
# standard reaction list (logged by the bridge, 2026-10-25) has all but
# 🔍 🧐 📤 🎫 📊 📅 ✅ 🚀 ⏳ 📝, which go as Premium custom emoji.
_DID = "only when `did` names it, or for a handling, when the message plainly asks for it"
PALETTE = {
    "seen": ("\U0001f440", "eyes", "read"),  # 👀, placed at binding, never picked
    "noted": ("\U0001f4dd", "memo", "noted: nothing else fits"),
    "code": ("\U0001f468\u200d\U0001f4bb", "technologist", f"a change to code or a running system, {_DID}"),
    "web_search": ("\U0001f50d", "mag", f"searching or reading the web, {_DID}"),
    "lookup": ("\U0001f9d0", "face_with_monocle", f"looking up the code, chat history or records, {_DID}"),
    "document": ("\u270d", "writing_hand", f"writing a document or report, {_DID}"),
    "present": ("\U0001f4ca", "bar_chart", f"making a deck, explainer or video, {_DID}"),
    "issue": ("\U0001f3ab", "ticket", f"filing or updating an issue, {_DID}"),
    "schedule": ("\U0001f4c5", "date", f"scheduling a meeting, reminder or routine, {_DID}"),
    "send": ("\U0001f4e4", "outbox_tray", f"sending an email, a file, or a message elsewhere, {_DID}"),
    "task": ("\U0001fae1", "saluting_face", f"on it: the work handed to a task, {_DID}"),
    "working": ("\u23f3", "hourglass_flowing_sand", "bigger work under way (a handling only)"),
    "thumbs_up": ("\U0001f44d", "+1", "yes, agreed, sounds good, will do"),
    "ok": ("\U0001f44c", "ok_hand", "understood, okay, as you say"),
    "check": ("\u2705", "white_check_mark", "confirmed, done, checked off"),
    "heart": ("\u2764", "heart", "thanks, kindness, something lovely"),
    "love": ("\U0001f60d", "heart_eyes", "love it"),
    "fire": ("\U0001f525", "fire", "impressive, exciting, hot news"),
    "party": ("\U0001f389", "tada", "a celebration: shipped, launched, a win"),
    "champagne": ("\U0001f37e", "champagne", "a big win worth popping a bottle"),
    "trophy": ("\U0001f3c6", "trophy", "an achievement or milestone"),
    "rocket": ("\U0001f680", "rocket", "launch, deploy, ship it"),
    "hundred": ("\U0001f4af", "100", "exactly right, full agreement"),
    "clap": ("\U0001f44f", "clap", "applause for someone's work"),
    "handshake": ("\U0001f91d", "handshake", "a deal, an agreement, teamwork"),
    "pray": ("\U0001f64f", "pray", "gratitude for a favour, a please, a hope"),
    "hug": ("\U0001f917", "hugging_face", "warm support, a welcome, comfort"),
    "grin": ("\U0001f601", "grin", "a cheerful message or light joke"),
    "rofl": ("\U0001f923", "rolling_on_the_floor_laughing", "something really funny"),
    "star_struck": ("\U0001f929", "star-struck", "delighted, amazing"),
    "cool": ("\U0001f60e", "sunglasses", "smooth, nailed it"),
    "zap": ("\u26a1", "zap", "fast, urgent, done in a flash"),
    "nerd": ("\U0001f913", "nerd_face", "a technical deep cut"),
    "mind_blown": ("\U0001f92f", "exploding_head", "astonishing news"),
    "thinking": ("\U0001f914", "thinking_face", "a question or idea to think over (never with no text sent)"),
    "raised_eyebrow": ("\U0001f928", "face_with_raised_eyebrow", "something odd or suspicious"),
    "sad": ("\U0001f622", "cry", "bad news, a loss, a disappointment"),
    "oops": ("\U0001f648", "see_no_evil", "an embarrassing slip, a mistake owned up to"),
    "angel": ("\U0001f607", "innocent", "a good deed, a sorry, an innocent ask"),
    "shrug": ("\U0001f937", "shrug", "nobody can know (never with no text sent)"),
    "yawn": ("\U0001f971", "yawning_face", "late at night, tired, or dull"),
    "moai": ("\U0001f5ff", "moyai", "deadpan, unbothered acknowledgement"),
    "unicorn": ("\U0001f984", "unicorn_face", "something rare, magical or whimsical"),
    "dove": ("\U0001f54a", "dove_of_peace", "calm, peace, a conflict settled"),
}
REACTIONS = {
    "telegram": {label: glyph for label, (glyph, _, _) in PALETTE.items()},
    "slack": {label: name for label, (_, name, _) in PALETTE.items()},
}
# What the classifier picks from: all but 👀.
PICKS = {label: f"{glyph} {fits}" for label, (glyph, _, fits) in PALETTE.items() if label != "seen"}
NOTED = "noted"
# A message's stages, lowest first: a row is closed `superseded` below a
# later one (`core/bridge.py`). `shape` is a row's of 2026-10-17 to 26.
RANK = {"seen": 0, "shape": 1, "handling": 1, "stalled": 2, "done": 3, "noted": 4}
# The activities, the one a turn shows first: what the sender would see.
ORDER = ("code", "present", "document", "issue", "schedule", "send", "task", "web_search", "lookup")
# What a message is bound as for it to get 👀; of those, what owes its
# done at binding, and what that done is told.
SEEN = ("start", "answer", "feedback", "steer", "none", "stop")
BOUND = {"start": "handed to a new task, which does the work",
         "none": "a reply to finished work: the kernel answers it with a notice"}  # fmt: skip
# A reply's text as the completed question reads it.
REPLY_CHARS = 400

# An effect's activity by its action type; a chat send is a reply unless it
# goes to another chat or carries a file other than a voice note.
EFFECTS = {"task.start": "task", "task.steer": "task", "task.answer": "task", "push_branch": "code",
           "email.send": "send"}  # fmt: skip
SENDS = ("telegram.send_message", "slack.send_message", "local.send_message")
VOICE = (".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav")
PRESENT = (".pptx", ".key", ".mp4", ".mov", ".webm")
DOCUMENT = (".pdf", ".docx", ".doc", ".md", ".txt", ".xlsx", ".csv")
# Files a turn writes for itself, not as work: its `.valor/` signals, and
# Claude Code's own (its auto-memory under the config dir's `projects/`).
OWN_FILES = ("/.valor/", "/claude/projects/", "/.claude/")
# A tool call's activity by the tool's name, then a Bash command's by its words.
TOOLS = {"WebSearch": "web_search", "WebFetch": "web_search", "Grep": "lookup", "Glob": "lookup",
         "CronCreate": "schedule"}  # fmt: skip
SKILLS = {"present": "present", "officecli": "document", "calendar": "schedule", "email": "send",
          "do-issue": "issue", "do-investigation-issue": "issue", "do-build": "code", "do-patch": "code"}  # fmt: skip
COMMANDS = (
    (re.compile(r"\bgh\s+issue\s+(create|edit|comment|close|reopen)\b"), "issue"),
    (re.compile(r"\bgit\s+(commit|push)\b"), "code"),
    (re.compile(r"\bgws\s+calendar\b.*\b(insert|patch|update|delete|quickAdd)\b"), "schedule"),
    (re.compile(r"\bgws\s+gmail\b.*\bsend\b"), "send"),
    (re.compile(r"\bvalor-(telegram|slack)\s+read\b|\bgh\s+(issue|pr|run)\s+(view|list)\b|\bgit\s+(log|show|blame)\b"
                r"|\bgws\b|(^|[|;&]\s*)(rg|grep)\s"), "lookup"),
)  # fmt: skip


async def on_bound(conn, p: dict[str, Any], task_id: str | None, as_: str) -> None:
    """The reactions a binding owes, in the binding's transaction: 👀 for a
    message read; then, for one handed to a new task or bound `none`, its
    done owed at once; for a `stop`, 👌 as its done."""
    row = channels.get(p["channel"])
    if task_id is None or row is None or not row.reacts or as_ not in SEEN:
        return
    await _request(conn, task_id, p, "seen", "seen", completes=True)
    if as_ == "stop":
        await _request(conn, task_id, p, "ok", "done", why="done: you stopped the work, as asked")
    elif as_ in BOUND:
        await _owe(conn, task_id, p, "done", outcome=BOUND[as_], did=[], replied="no text sent")


def _message(p: dict[str, Any]) -> dict[str, Any]:
    return {k: p[k] for k in ("received_id", "channel", "chat_id", "message_id")}


async def _request(conn, task_id: str, p: dict[str, Any], label: str, stage: str, **extra) -> str | None:
    """One `reaction.requested` row for `label`, its emoji the channel's;
    its id, or None when this message already has a reaction at this
    stage."""
    reaction_id = ledger.new_id()
    try:
        async with conn.transaction():
            await ledger.append(
                conn,
                task_id,
                "reaction.requested",
                {
                    "reaction_id": reaction_id,
                    **_message(p),
                    "emoji": REACTIONS[p["channel"]][label],
                    "label": label,
                    "stage": stage,
                    **extra,
                },
            )
    except psycopg.errors.UniqueViolation:
        return None
    return reaction_id


async def _owe(conn, task_id: str, p: dict[str, Any], stage: str, **what) -> None:
    """One `reaction.owed` row; a reply text the ledger refuses (in a
    savepoint, so a turn's collection stands) is left out."""
    row = {**_message(p), "stage": stage, **what}
    _, why = await ledger.try_append(conn, task_id, "reaction.owed", row)
    if why is not None:
        await ledger.append(
            conn, task_id, "reaction.owed", {**row, "replied": f"a reply the ledger refused: {why}"}
        )


def _ext(path: str) -> str:
    return PurePosixPath(str(path)).suffix.lower()


def _of_file(path: str) -> str | None:
    ext = _ext(path)
    return "present" if ext in PRESENT else "document" if ext in DOCUMENT else None


def effect_did(request: dict[str, Any], chats: set[str]) -> str | None:
    """An effect request's activity; None for a reply in one of `chats`."""
    kind, payload = request.get("action_type"), request.get("payload") or {}
    if kind in EFFECTS:
        return EFFECTS[kind]
    if kind not in SENDS:
        return None
    files = [f.get("path") or "" for f in payload.get("files") or [] if isinstance(f, dict)]
    shown = [f for f in files if _ext(f) not in VOICE]
    if shown:
        return next((a for f in shown if (a := _of_file(f))), "send")
    return None if str(request.get("target")) in chats else "send"


def tool_did(name: str, given: dict[str, Any]) -> str | None:
    """A tool call's activity, from its name and input; None for a call
    that is part of replying (reading a file, writing the turn's own
    `.valor/` files or Claude Code's memory)."""
    if name in TOOLS:
        return TOOLS[name]
    if name.startswith("mcp__byob__"):
        return "web_search"
    if name == "Agent":
        return "web_search" if "web" in str(given.get("subagent_type") or "") else "lookup"
    if name == "Skill":
        return SKILLS.get(str(given.get("skill") or "").rpartition(":")[2])
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = str(given.get("file_path") or given.get("notebook_path") or "")
        return None if any(m in f"/{path}" for m in OWN_FILES) else _of_file(path) or "code"
    if name == "Bash":
        command = str(given.get("command") or "")
        return next((a for pattern, a in COMMANDS if pattern.search(command)), None)
    return None


def tool_calls(text: bytes, since: datetime) -> list[tuple[str, dict[str, Any]]]:
    """Each tool call a Claude Code transcript records at or after `since`:
    its name and input. Lines it cannot read are skipped."""
    calls = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
            if datetime.fromisoformat(str(entry["timestamp"])) < since:
                continue
        except ValueError, TypeError, KeyError:
            continue
        message = entry.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            continue
        for block in message["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append((str(block.get("name")), block.get("input") or {}))
    return calls


async def did(conn, task_id: str, turn_id: str, effects: list[dict[str, Any]]) -> list[str]:
    """What a collected turn did, in `ORDER`: the activities of `effects`
    (the entries `record` made) and of its transcript's tool calls. A chat
    send is a reply in any chat that reacts, so only a send elsewhere or
    with a file counts."""
    rows = await ledger.read(conn, task_id)
    start, ended = _turn(rows, turn_id)
    if start is None:
        return []
    chats = {str(r["payload"]["chat_id"]) for r in rows if r["type"] == "reaction.requested"}
    found = {a for r in _requested(effects) if (a := effect_did(r, chats))}
    for f in (ended.get("transcript") or {}).get("files", []):
        for name, given in tool_calls(await transcripts.joined(conn, turn_id, f["name"]), start["at"]):
            if a := tool_did(name, given):
                found.add(a)
    return [a for a in ORDER if a in found]


def _turn(rows: list[dict], turn_id: str) -> tuple[dict | None, dict[str, Any]]:
    """A turn's `turn.started` row and its `turn.ended` payload ({} when none)."""
    mine = [
        r
        for r in rows
        if r["type"] in ("turn.started", "turn.ended") and r["payload"].get("turn_id") == turn_id
    ]
    start = next((r for r in mine if r["type"] == "turn.started"), None)
    return start, next((r["payload"] for r in mine if r["type"] == "turn.ended"), {})


def _requested(effects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        e["request"] for e in effects if "request" in e and not e.get("error") and e.get("kind") != "refused"
    ]


def _replied(p: dict[str, Any], requested: list[dict[str, Any]]) -> str:
    """What the turn sent for this message: a reply to it, a reply in its
    chat to another, or no text."""
    sends = [
        r for r in requested if r.get("action_type") in SENDS and str(r.get("target")) == str(p["chat_id"])
    ]
    for r in sends:
        payload = r.get("payload") or {}
        if str(payload.get("reply_to")) == str(p["message_id"]):
            return f"a reply to this message: {str(payload.get('text') or '')[:REPLY_CHARS]}"
    for r in sends:
        payload = r.get("payload") or {}
        to = payload.get("reply_to")
        return (
            f"replied in this chat to {f'message {to}' if to else 'no message'}: "
            + str(payload.get("text") or "")[:REPLY_CHARS]
        )
    return "no text sent"


async def owe(conn, task_id: str, turn_id: str, effects: list[dict[str, Any]], done: list[str]) -> int:
    """The reactions a collected turn owes, in `record`'s transaction after
    its `turn.collected`: one `reaction.owed` for each message with 👀 it
    read and no done owed, `done` or, for a turn that ended in an error,
    `stalled`. `done` is what it did (`did`). How many it wrote."""
    rows = await ledger.read(conn, task_id)
    f = machine.fold(rows)
    start, ended = _turn(rows, turn_id)
    if start is None or f.turn_states.get(turn_id) in (machine.FRESH, machine.COMPACTION):
        return 0
    ok = ended.get("outcome") == "done" and not (ended.get("result") or {}).get("is_error")
    stage = "done" if ok else "stalled"
    since = machine.fold([r for r in rows if r["id"] < start["id"]]).steer_since
    took = {p.get("received_id") for i, p in f.steered if i in f.taken.get(turn_id, set())}
    owed: dict[str, int] = {}
    for r in rows:
        p = r["payload"]
        if r["type"] in ("reaction.owed", "reaction.requested") and p.get("stage") in RANK:
            owed[p["received_id"]] = max(owed.get(p["received_id"], 0), RANK[p["stage"]])
    requested = _requested(effects)
    written = 0
    for r in rows:
        p = r["payload"]
        if r["type"] != "reaction.requested" or p.get("stage") != "seen" or p.get("final"):
            continue
        if not (since < r["id"] <= f.turn_rows[turn_id] or p["received_id"] in took):
            continue
        if owed.get(p["received_id"], 0) >= RANK[stage]:
            continue
        outcome = "done" if ok else "the turn ended in an error; it is tried again"
        await _owe(conn, task_id, p, stage, turn_id=turn_id, outcome=outcome, did=done,
                   replied=_replied(p, requested))  # fmt: skip
        owed[p["received_id"]] = RANK[stage]
        written += 1
    return written


# -- what is due, and the classifier's pick ------------------------------------

# Every message with reactions still to come: one with a `completes` 👀 or a
# reaction owed, and no done (placed or due) nor `noted`. Its task id (the
# 👀's or the owed row's), whether that task is stopped, and its reaction
# rows, oldest first.
OPEN = (
    "WITH open AS (SELECT DISTINCT ON (e.payload->>'received_id') e.payload->>'received_id' AS rid, e.task_id "
    "FROM events e WHERE ((e.type = 'reaction.requested' AND e.payload->>'stage' = 'seen' "
    "AND e.payload ? 'completes') OR e.type = 'reaction.owed') AND NOT EXISTS (SELECT 1 FROM events d "
    "WHERE d.type = 'reaction.requested' AND d.payload->>'received_id' = e.payload->>'received_id' "
    "AND (d.payload->>'stage' = 'noted' OR (d.payload->>'stage' = 'done' AND NOT EXISTS (SELECT 1 FROM events x "
    "WHERE x.type = 'reaction.failed' AND x.payload->>'reaction_id' = d.payload->>'reaction_id')))) "
    "ORDER BY e.payload->>'received_id', e.id) "
    "SELECT o.rid, o.task_id, EXISTS (SELECT 1 FROM events s WHERE s.task_id = o.task_id "
    "AND s.type = 'task.stopped'), r.type, r.payload FROM open o JOIN events r "
    "ON r.type IN ('reaction.requested', 'reaction.owed', 'reaction.failed') "
    "AND r.payload->>'received_id' = o.rid ORDER BY r.id"
)


def due(rows: list[dict[str, Any]], stopped: bool) -> tuple[str, dict[str, Any]] | None:
    """What one message's reaction rows (oldest first) are owed next: the
    stage, and the row it is read from (a `seen` or `reaction.owed`
    payload); None when nothing is. A refused done is replaced by `noted`;
    an owed done comes first, then a stopped task's done, then an owed
    `stalled`, then a `handling` for a message with nothing owed."""
    failed = {r["payload"].get("reaction_id") for r in rows if r["type"] == "reaction.failed"}
    asked = [r["payload"] for r in rows if r["type"] == "reaction.requested"]
    stages = {p.get("stage") for p in asked}
    owed = [r["payload"] for r in rows if r["type"] == "reaction.owed"]
    seen = next((p for p in asked if p.get("stage") == "seen"), None)
    done = [p for p in asked if p.get("stage") == "done"]
    if done and all(p.get("reaction_id") in failed for p in done):
        return "noted", done[-1]
    if done:
        return None
    if owing := [p for p in owed if p.get("stage") == "done"]:
        return "done", owing[-1]
    if stopped and (seen or owed):
        return "done", seen or owed[-1]
    if (owing := [p for p in owed if p.get("stage") == "stalled"]) and "stalled" not in stages:
        return "stalled", owing[-1]
    if not owed and seen is not None and seen.get("completes") and stages == {"seen"}:
        return "handling", seen
    return None


async def pending(conn) -> list[dict[str, Any]]:
    """Each message's next reaction (`due`): `stage`, `task_id`, whether its
    task is `stopped`, and the row it is read from, oldest first."""
    by: dict[str, tuple[str, bool, list[dict[str, Any]]]] = {}
    for rid, task_id, stopped, kind, payload in await (await conn.execute(OPEN)).fetchall():
        by.setdefault(rid, (task_id, stopped, []))[2].append({"type": kind, "payload": payload})
    out = []
    for task_id, stopped, rows in by.values():
        if (next_ := due(rows, stopped)) is not None:
            out.append({"stage": next_[0], "task_id": task_id, "stopped": stopped, "row": next_[1]})
    return out


async def inputs(conn, item: dict[str, Any]) -> dict[str, str]:
    """A classifier question's inputs for a due reaction: the message's
    text, its reply chain oldest first, what it was bound as, and for a
    done what the turn did and sent and the handling on it."""
    rid = item["row"]["received_id"]
    received = await (
        await conn.execute(
            "SELECT payload FROM events WHERE type = 'message.received' AND payload->>'received_id' = %s",
            (rid,),
        )
    ).fetchone()
    bound = await (
        await conn.execute(
            "SELECT payload->>'as' FROM events WHERE type = 'message.bound' AND payload->>'received_id' = %s",
            (rid,),
        )
    ).fetchone()
    p = received[0] if received else {}
    thread = "\n".join(str(m.get("text") or "") for m in p.get("thread") or [] if isinstance(m, dict))
    got = {"message": str(p.get("text") or ""), "thread": thread, "bound_as": bound[0] if bound else ""}
    if item["stage"] == "handling":
        return got
    shown = await current_for(conn, [rid])
    owed = item["row"]
    return {
        **got,
        "handling": emoji_of(shown[rid]) if rid in shown else "none",
        "did": ", ".join(owed.get("did") or []) or "nothing beyond reading and replying",
        "replied": str(owed.get("replied") or "no text sent"),
        "outcome": str(owed.get("outcome") or "done"),
    }


async def react(port: judgement.JudgementPort | None, conn, item: dict[str, Any], dsn: str) -> str | None:
    """Write the reaction `item` (`pending`) is owed; the label written, or
    None when none is (a failed judgement asked again later). `conn` writes
    it; the port opens its own connections on `dsn`."""
    from core.judgement_tasks import COMPLETED, HANDLING

    stage, task_id, p = item["stage"], item["task_id"], item["row"]
    if stage == "noted":
        why = f"noted: your reaction {emoji_of(p)} was refused by the chat"
        return NOTED if await _request(conn, task_id, p, NOTED, "noted", why=why) else None
    if item["stopped"] or port is None:
        why = f"{stage}: noted, " + (
            "its task was stopped" if item["stopped"] else "the classifier was not asked"
        )
        return NOTED if await _request(conn, task_id, p, NOTED, stage, why=why) else None
    task = HANDLING if stage == "handling" else COMPLETED
    ref = {"received_id": p["received_id"], "stage": stage}
    try:
        j = await port.judge(task, await inputs(conn, item), task_id=task_id, ref=ref, dsn=dsn)
    except tasks.TaskStopped:
        why = f"{stage}: noted, its task was stopped"
        return NOTED if await _request(conn, task_id, p, NOTED, stage, why=why) else None
    if not j.answered and await _failures(conn, task_id, ref) < judgement.UNANSWERED_RUNS:
        return None
    label = (j.payload.get("picked") or {}).get(task.questions[0].id) if j.answered else None
    if label not in PICKS:
        label = NOTED
    why = _why(stage, label, p)
    written = await _request(conn, task_id, p, label, stage, why=why, judgement_id=j.judgement_id)
    return label if written else None


async def _failures(conn, task_id: str, ref: dict[str, Any]) -> int:
    row = await (
        await conn.execute(
            "SELECT count(*) FROM events WHERE task_id = %s AND type = 'judgement.failed' AND payload->'ref' = %s::jsonb",
            (task_id, json.dumps(ref)),
        )
    ).fetchone()
    return row[0]


def _why(stage: str, label: str, p: dict[str, Any]) -> str:
    """What a judged reaction tells the session of why it was set."""
    fits = "no pick, so noted" if label == NOTED else f"read as {PICKS[label].split(' ', 1)[1]}"
    if stage == "handling":
        return f"handling, the classifier's pick: {fits}"
    if not p.get("turn_id"):
        return f"{stage}, the classifier's pick: {fits} ({p.get('outcome')})"
    replied = str(p.get("replied") or "no text sent")
    sent = (
        "no text sent, so the reaction is your answer" if replied == "no text sent"
        else "you replied to it" if replied.startswith("a reply to this") else "you " + replied.split(":", 1)[0]
    )  # fmt: skip
    did = ", ".join(p.get("did") or []) or "nothing beyond replying"
    return f"{stage}, the classifier's pick after your turn ({sent}; you did {did}): {fits}"


def emoji_of(p: dict[str, Any]) -> str:
    """A `reaction.requested` row's emoji as a reader sees it: Slack's by
    name, `:mag:`."""
    e = str(p.get("emoji") or "")
    return f":{e}:" if p.get("channel") == "slack" and p.get("label") else e


def why(p: dict[str, Any]) -> str:
    """What a reaction past 👀 was set for (rows of 2026-10-17 to 26 by
    their own fields)."""
    if p.get("why"):
        return str(p["why"])
    if p.get("turn_id"):
        return f"your turn did {p.get('label')}"
    if p.get("reason"):
        return str(p["reason"])
    return f"the reaction classifier read it as {p.get('label')}" if p.get("judged") else str(p.get("label"))


def current(rows: list[dict]) -> dict[str, dict[str, Any]]:
    """By `received_id`, the reaction on the message now: of its requested
    reactions that did not fail, the latest stage's (`RANK`)."""
    failed = {r["payload"].get("reaction_id") for r in rows if r["type"] == "reaction.failed"}
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        p = r["payload"]
        if r["type"] != "reaction.requested" or p.get("reaction_id") in failed:
            continue
        held = out.get(p.get("received_id"))
        if held is None or RANK.get(p.get("stage"), 0) >= RANK.get(held.get("stage"), 0):
            out[p.get("received_id")] = p
    return out


def shown(rows: list[dict]) -> dict[str, str]:
    """By `received_id`, what a session reads of the reaction on the
    message now (`current`), and what set it."""
    return {
        rid: f"reacted {emoji_of(p)} (read)"
        if p.get("stage") == "seen"
        else f"reacted {emoji_of(p)}: {why(p)}"
        for rid, p in current(rows).items()
    }


async def _rows_for(conn, received_ids: list[str]) -> list[dict]:
    if not received_ids:
        return []
    found = await (
        await conn.execute(
            "SELECT type, payload FROM events WHERE type IN ('reaction.requested', 'reaction.failed') "
            "AND payload->>'received_id' = ANY(%s) ORDER BY id",
            (received_ids,),
        )
    ).fetchall()
    return [{"type": t, "payload": p} for t, p in found]


async def current_for(conn, received_ids: list[str]) -> dict[str, dict[str, Any]]:
    """`current` for these messages, read from the ledger wherever their
    rows sit."""
    return current(await _rows_for(conn, received_ids))


async def shown_for(conn, received_ids: list[str]) -> dict[str, str]:
    """`shown` for these messages, read from the ledger wherever their
    rows sit."""
    return shown(await _rows_for(conn, received_ids))
