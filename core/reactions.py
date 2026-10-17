"""Reactions: 👀 on a message Valor reads, then, once work is done for it,
an emoji for that work (docs/plans/reactions-activity.md).

Binding (`core/intake.py`) calls `on_bound` in the binding's transaction.
A Telegram or Slack message bound to a task as `start`, `answer`,
`feedback`, `steer`, or `none` with a task (Tom's reply to merged or
stopped work) gets a `seen` reaction, 👀, at once. A `start` started a
task, so its `shape` reaction 🫡 follows at once; a `stop` gets 👌 at
once instead of 👀. A message bound `none` with no task, or `heard`, and
any message on a channel that does not react (email, local), gets
nothing; so in a team chat whose spec lists it under `mentions`, only a
message that names Valor or replies to him is marked.

Nothing is guessed on arrival. When a turn of the task is collected
(`core/session.py`, `record`), `on_turn` reads what the turn actually did:
the effects it requested and the tools its transcript shows it called
(`did`). The most specific activity (`ORDER`) becomes the `shape` reaction
of one message still showing 👀 that the turn read (its prompt's window,
since the last turn that spent the steering; a fresh, compaction, or errored
turn sets nothing): the one a send of the turn replies to, else the newest. A turn that only replied in the chat
did nothing, so 👀 stays. A `none`-bound message's 👀 is `final` and never
changes.

`REACTIONS` is the one mapping, per channel, from a label (`seen`, `stop`,
and each activity) to what the bridge places: on Telegram the emoji's exact
code points, which the bridge sends as a standard reaction when Telegram's
list has it and as a Premium custom emoji for that glyph when not
(`bridges/telegram/wire.py`); on Slack the emoji's name. Each
`reaction.requested` row carries the `label` and that `emoji`.

The bridge places each `reaction.requested` row through its outbox
(`core/bridge.py`, `ReactionDue`) with its own account, as it sends
notices, and writes `reaction.placed` or `reaction.failed`. There is no
broker effect, approval, or report: a reaction is a reversible mark in a
chat Tom owns and carries no text. Every reaction row is quiet
(`ledger.quiet`), so none moves the task it sits on.

The session reads the reaction on each message beside it (`shown`, through
`intake.chat_line`), and what it was set for, so asked about an emoji
Valor says what he did.
"""

import json
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

import psycopg

from core import channels, ledger, machine, transcripts

# Telegram: exact code points, none with the U+FE0F variation selector,
# which Telegram can refuse. Slack: the emoji's name.
REACTIONS = {
    "telegram": {
        "seen": "\U0001f440",  # 👀
        "stop": "\U0001f44c",  # 👌
        "task": "\U0001fae1",  # 🫡
        "code": "\U0001f468‍\U0001f4bb",  # 👨‍💻
        "web_search": "\U0001f50d",  # 🔍
        "lookup": "\U0001f9d0",  # 🧐
        "document": "✍",  # ✍
        "present": "\U0001f4ca",  # 📊
        "issue": "\U0001f3ab",  # 🎫
        "schedule": "\U0001f4c5",  # 📅
        "send": "\U0001f4e4",  # 📤
    },
    "slack": {
        "seen": "eyes",
        "stop": "ok_hand",
        "task": "saluting_face",
        "code": "technologist",
        "web_search": "mag",
        "lookup": "face_with_monocle",
        "document": "writing_hand",
        "present": "bar_chart",
        "issue": "ticket",
        "schedule": "date",
        "send": "outbox_tray",
    },
}
# The activities, the one a turn shows first: what the sender would see.
ORDER = ("code", "present", "document", "issue", "schedule", "send", "task", "web_search", "lookup")
# What a message is bound as for it to get 👀.
SEEN = ("start", "answer", "feedback", "steer", "none")

# An effect's activity by its action type; a chat send is a reply unless it
# goes to another chat or carries a file other than a voice note.
EFFECTS = {"task.start": "task", "task.steer": "task", "task.answer": "task", "push_branch": "code",
           "email.send": "send"}  # fmt: skip
SENDS = ("telegram.send_message", "slack.send_message", "local.send_message")
VOICE = (".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav")
PRESENT = (".pptx", ".key", ".mp4", ".mov", ".webm")
DOCUMENT = (".pdf", ".docx", ".doc", ".md", ".txt", ".xlsx", ".csv")
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
    """The reaction a binding owes, in the binding's transaction: 👀 for a
    message read (final for one bound `none`), then 🫡 for one that started
    a task; 👌 for a `stop`."""
    row = channels.get(p["channel"])
    if task_id is None or row is None or not row.reacts:
        return
    if as_ in SEEN:
        await _request(conn, task_id, p, "seen", "seen", **({"final": True} if as_ == "none" else {}))
    if as_ == "start":
        await _request(conn, task_id, p, "task", "shape", reason="a task started")
    elif as_ == "stop":
        await _request(conn, task_id, p, "stop", "shape", reason="stop")


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
                    "received_id": p["received_id"],
                    "channel": p["channel"],
                    "chat_id": p["chat_id"],
                    "message_id": p["message_id"],
                    "emoji": REACTIONS[p["channel"]][label],
                    "label": label,
                    "stage": stage,
                    **extra,
                },
            )
    except psycopg.errors.UniqueViolation:
        return None
    return reaction_id


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
    `.valor/` files)."""
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
        return None if "/.valor/" in path or path.startswith(".valor/") else _of_file(path) or "code"
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


async def on_turn(conn, task_id: str, turn_id: str, effects: list[dict[str, Any]]) -> str | None:
    """The activity reaction a collected turn owes: what it did (`effects`,
    the entries `record` made, and its transcript's tool calls), on one
    message still showing 👀 that it read. The label written, or None."""
    rows = await ledger.read(conn, task_id)
    start = next(
        (r for r in rows if r["type"] == "turn.started" and r["payload"].get("turn_id") == turn_id), None
    )
    if start is None:
        return None
    # The messages this turn read: its prompt's steering window (`steer_since`
    # before it began), and only when the turn spent it (a fresh, compaction,
    # or errored turn leaves it to the next).
    if machine.fold(rows).steer_since != start["id"]:
        return None
    since = machine.fold([r for r in rows if r["id"] < start["id"]]).steer_since
    shaped = {r["payload"]["received_id"] for r in rows if r["type"] == "reaction.requested"
              and r["payload"].get("stage") == "shape"}  # fmt: skip
    waiting = [r["payload"] for r in rows if r["type"] == "reaction.requested" and since < r["id"] < start["id"]
               and r["payload"].get("stage") == "seen" and not r["payload"].get("final")
               and r["payload"]["received_id"] not in shaped]  # fmt: skip
    if not waiting:
        return None
    chats = {str(p["chat_id"]) for p in waiting}
    requested = [
        e["request"] for e in effects if "request" in e and not e.get("error") and e.get("kind") != "refused"
    ]
    did = {a for r in requested if (a := effect_did(r, chats))}
    ended = next(
        (r["payload"] for r in rows if r["type"] == "turn.ended" and r["payload"].get("turn_id") == turn_id),
        {},
    )
    for f in (ended.get("transcript") or {}).get("files", []):
        for name, given in tool_calls(await transcripts.joined(conn, turn_id, f["name"]), start["at"]):
            if a := tool_did(name, given):
                did.add(a)
    label = next((a for a in ORDER if a in did), None)
    if label is None:
        return None
    replied = {
        str((r.get("payload") or {}).get("reply_to")) for r in requested if r.get("action_type") in SENDS
    }
    target = next((p for p in reversed(waiting) if str(p["message_id"]) in replied), waiting[-1])
    written = await _request(conn, task_id, target, label, "shape", turn_id=turn_id,
                             did=[a for a in ORDER if a in did])  # fmt: skip
    return label if written else None


def emoji_of(p: dict[str, Any]) -> str:
    """A `reaction.requested` row's emoji as a reader sees it: Slack's by
    name, `:mag:`."""
    e = str(p.get("emoji") or "")
    return f":{e}:" if p.get("channel") == "slack" and p.get("label") else e


def why(p: dict[str, Any]) -> str:
    """What a `shape` reaction was set for."""
    if p.get("turn_id"):
        return f"your turn did {p.get('label')}"
    if p.get("reason"):
        return str(p["reason"])
    return f"the reaction classifier read it as {p.get('label')}" if p.get("judged") else str(p.get("label"))


def shown(rows: list[dict]) -> dict[str, str]:
    """By `received_id`, what a session reads of the reaction on the
    message now: the newest requested reaction that did not fail, and what
    set it."""
    failed = {r["payload"].get("reaction_id") for r in rows if r["type"] == "reaction.failed"}
    out: dict[str, str] = {}
    for r in rows:
        p = r["payload"]
        if r["type"] != "reaction.requested" or p.get("reaction_id") in failed:
            continue
        e = emoji_of(p)
        out[p.get("received_id")] = (
            f"reacted {e} (read)" if p.get("stage") == "seen" else f"reacted {e}: {why(p)}"
        )
    return out


async def shown_for(conn, received_ids: list[str]) -> dict[str, str]:
    """`shown` for these messages, read from the ledger wherever their
    rows sit."""
    if not received_ids:
        return {}
    found = await (
        await conn.execute(
            "SELECT type, payload FROM events WHERE type IN ('reaction.requested', 'reaction.failed') "
            "AND payload->>'received_id' = ANY(%s) ORDER BY id",
            (received_ids,),
        )
    ).fetchall()
    return shown([{"type": t, "payload": p} for t, p in found])
