"""A Google Meet call (docs/plans/meet.md): its session, its end, and the
process that is Valor in it.

**The call session.** `meet.join` (`tools/meet.py`), requested by a group
chat's session, opens one (`begin`): a root chat session on channel `meet`,
its chat id the meeting code, under the asking chat's project and at its
ceiling, so its worker has that chat's access (`tasks.dispatch` gives it
the asking chat's room). Its `task.started` carries `call`: the link,
this Mac, the chat and thread it was asked in (`asked_by`), and `prep`,
the asking turn's notes on the topic, which a call asked for in a chat
writes from what it holds and its worker's first turn adds to while
Valor joins (`Call._read_up`, P-033). It runs no turn (`router.has_step`)
and owes no report of its own (`notices.owe`); everything it says goes to the asking
chat's session as a `child.reported` row of kind `call` (`report`): first
that Valor is joining, which names the call session, so the chat can stop
or steer it, and carries no link; then, once Valor is in (`entered`,
`call.joined`), the link, which the call itself posts in the thread a chat
asked it in (an invited call's own post has it already); and at
the end what the call settled, or, for a call Valor never got into, why,
with no link to post (Tom, 2026-11-13: "don't share a meet link if it
doesn't work!"). A steer of it is posted in the call's chat while it is
open; after `call.ended` it is refused (`tools/chat_tasks.py`).

**The end** (`end`), written once, in one transaction: `call.ended` (with
the worker answers that came after leaving and the questions left
unanswered), `task.ended` unless the session was stopped, the decisions
the call settled, `effect.refused` for each `meet.say` or
`meet.send_message` never delivered (a thread copy still goes), and the
report. Tom's read-backs (a post's `settles`) count as said in the call, 1:1 or group, with no 👍 in chat (Tom,
2026-11-25: "Meets display name is trustworthy enough for our purposes"): the report has the asking chat's
session answer each with its question id (`settles`), refused unless it is still the task's open question, act
on it, and post the read-backs in the thread as a notice. The call's own process writes the end; a kernel
wake writes it for a call whose process died (`sweep`).

**The call's process** (`python -m core call SESSION`, `run`) holds
`meet:call:<session>` while it lives, and `run:<session>`, so the kernel
charges none of its model calls twice (`db.Held`: a restart of the
database, which drops the session holding them, takes them again, and the
call stays). It joins through the page
(`bridges/meet`), mixes what everyone says into Gemini's live
transcriber, which ends each turn; for each turn that is Valor's, one text
model writes every word he says (P-042), from the meeting's live state
(P-041) and the call so far, and posts, asks its worker, records decisions
and reads the project's backlog Sheet (`backlog`, where its spec names one)
through tools; Gemini's speech model says it, sentence by sentence, and the
call speaks and posts through the broker on its own session. After the call Gemini summarises
its recording, kept in Yudame Tools' meeting store.
"""

import asyncio
import contextlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import psycopg

from bridges.meet.google import Account
from core import broker, db, intake, ledger, persona, tasks, workspace
from core.settings import CALL_CONTEXT, resolve_seat, settings

CHANNEL = "meet"
# A call whose process never wrote `call.started` this long after it was
# opened, with its lock free, never started.
NEVER_STARTED_S = 90
# A call's process waits this long for a database that refuses connections (`Call._connect`).
DB_WAIT_S = 180.0
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# The product Sheet's tool (skills/valor/skills/product-sheet/SKILL.md), and
# the Sheet a spec's `rooms` name; each names one (2026-11-13).
SHEET_PY = Path(__file__).resolve().parent.parent / "skills/valor/skills/product-sheet/sheet.py"
SHEET_URL = re.compile(r"docs\.google\.com/spreadsheets/d/([\w-]+)")
# What `backlog` says of each Queue row when it reads the whole queue.
OVERVIEW = ("id", "lane", "rank", "item", "value", "appetite", "due", "status", "blocked by")


def is_call(brief: tasks.Brief) -> bool:
    return bool(brief.chat) and brief.chat["channel"] == CHANNEL


async def marker(conn, session_id: str) -> dict[str, Any] | None:
    """The `call` its `task.started` carries; None for any other task."""
    row = await (
        await conn.execute(
            "SELECT payload->'call' FROM events WHERE task_id = %s AND type = 'task.started' LIMIT 1",
            (session_id,),
        )
    ).fetchone()
    return row[0] if row else None


def account(profile: str | None = None) -> str | None:
    """The Google account this Mac's call profile is signed in as: the one
    its preferences name, with Google's session cookie (`SID`) in its
    cookies. Preferences alone outlive a lost sign-in (the Captain,
    2026-11-03). The cookies are read from a copy, with their side files,
    since a running Chrome locks them and holds writes there."""
    default = Path(profile or settings.meet_profile, "Default")
    try:
        prefs = json.loads((default / "Preferences").read_text())
    except OSError, ValueError:
        return None
    email = next((a["email"] for a in prefs.get("account_info") or [] if a.get("email")), None)
    if email is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        for side in ("", "-journal", "-wal"):
            with contextlib.suppress(FileNotFoundError):
                shutil.copyfile(default / f"Cookies{side}", Path(tmp, f"Cookies{side}"))
        if not Path(tmp, "Cookies").exists():
            return None
        try:
            with contextlib.closing(sqlite3.connect(Path(tmp, "Cookies"))) as db_:
                row = db_.execute(
                    "SELECT 1 FROM cookies WHERE host_key = '.google.com' AND name = 'SID' LIMIT 1"
                ).fetchone()
        except sqlite3.Error:
            return None
    return email if row else None


def signed_in_as(profile: str, email: str) -> bool:
    """Whether the call profile is signed in as `email`, not another
    account: a profile signed in as Valor's other address would join as
    the wrong one."""
    return (account(profile) or "").lower() == email.lower()


def call_account(info: dict[str, Any]) -> Account:
    """The account a call joins as: the one its marker names, else own."""
    return Account(info["account"]) if info.get("account") else Account.own()


def sign_in(
    who=None, *, profile: str | None = None, chrome: str = CHROME, poll_s: float = 2.0, say=print
) -> int:
    """`python -m core meet-sign-in [--account EMAIL]`, at the Mac, for
    `who` (`google.Account`, own by default) on its call profile
    (`profile` overrides it, for a test): where Meet settles for a
    call's Chrome (`bridges.meet.settled`), signed in first by Valor itself
    when its login is in 1Password (`bridges/meet/google.py`); when Meet
    still does not open signed in, plain Chrome on the call profile at Meet
    for Tom to sign in or clear "Verify it's you" (the Cowboy, 2026-11-13),
    and the answer from where Meet settles once he quits it. Chrome is never
    ended at the first sign of a sign-in: Google writes its cookies before
    its passkey step is done (the Captain, 2026-11-13). A Chrome already
    holding the profile is left alone: a call's is named, any other is the
    window to sign in from, which may be left open, since the next call
    quits it. 0 when Meet opens signed in as that account."""
    from bridges.meet import MEET_HOME, a_call, holder, on_meet, settled

    who = who or Account.own()
    profile = profile or who.profile
    if (pid := holder(profile)) is not None:
        if a_call(pid):
            say(f"the call profile is in use by a call's Chrome (pid {pid}); run this again after the call")
        else:
            say(f"the call profile is already open in Chrome (pid {pid}): sign in as {who.email} there; "
                "the window may be left open, the next call quits it")  # fmt: skip
        return 1
    if on_meet(asyncio.run(settled(profile, renew=True, account=who))) and signed_in_as(profile, who.email):
        say(f"signed in as {who.email}; {settings.machine} can join calls")
        return 0
    if (email := account(profile)) and email.lower() != who.email:
        say(
            f"the call profile is signed in as {email}, not {who.email}: in the Chrome window, sign out and sign "
            f"in as {who.email}, then quit Chrome with ⌘Q once Meet's home page shows (Ctrl-C here to give up)"
        )
    elif email:
        say(f"Google wants the sign-in as {email} verified again: in the Chrome window, click Next and verify, "
            "then quit Chrome with ⌘Q once Meet's home page shows (Ctrl-C here to give up)")  # fmt: skip
    else:
        say(f"sign in as {who.email} in the Chrome window, then quit Chrome with ⌘Q once Meet's home page "
            "shows (Ctrl-C here to give up)")  # fmt: skip
    Path(profile).mkdir(parents=True, exist_ok=True)
    child = subprocess.Popen(
        [chrome, f"--user-data-dir={profile}", "--no-first-run", MEET_HOME],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        while child.poll() is None:
            time.sleep(poll_s)
    except KeyboardInterrupt:
        pass
    finally:
        if child.poll() is None:
            child.terminate()  # a clean exit, which saves the cookies
        child.wait()
    email = account(profile)
    if email and on_meet(url := asyncio.run(settled(profile, account=who))):
        if signed_in_as(profile, who.email):
            say(f"signed in as {who.email}; {settings.machine} can join calls")
            return 0
        say(
            f"signed in as {email}, not {who.email}; sign out of {email} there and run this again: "
            f"{who.sign_in}"
        )
        return 1
    say(
        f"not signed in{f' (Meet stayed on {url})' if email else ''}; run this again to sign in: {who.sign_in}"
    )
    return 1


async def in_progress(conn) -> dict[str, Any] | None:
    """The call this Mac is in: an open call session's `call`."""
    for session_id, _, _ in await _open_calls(conn):
        call = await marker(conn, session_id) or {}
        if call.get("machine") == settings.machine and not await tasks.is_stopped(conn, session_id):
            return {"session_id": session_id, **call}
    return None


async def _open_calls(conn) -> list[tuple[str, datetime, bool]]:
    """Each call session with no `call.ended`, oldest first: its id, when
    it opened, and whether its process wrote `call.started`."""
    return await (
        await conn.execute(
            "SELECT d.id, d.created_at, EXISTS (SELECT 1 FROM events c WHERE c.task_id = d.id "
            "AND c.type = 'call.started') FROM documents d WHERE d.kind = 'task' "
            "AND d.body->'chat'->>'channel' = %s AND NOT EXISTS (SELECT 1 FROM events e "
            "WHERE e.task_id = d.id AND e.type = 'call.ended') ORDER BY d.created_at",
            (CHANNEL,),
        )
    ).fetchall()


async def begin(
    conn,
    asking: tasks.Brief,
    *,
    url: str,
    code: str,
    reply_to: str | None,
    topic_id: str | None,
    prep: str,
    effect_key: str,
    starts: str | None = None,
    account: str | None = None,
) -> str:
    """Start the call session and write its first report, that Valor is
    joining, with no link, inside the caller's transaction; the call joins
    as `account` (an address), else own. Returns the session's id."""
    chat = asking.chat or {}
    harness_name, model = resolve_seat("frontier")
    brief = tasks.Brief(
        instruction=(
            f"A Google Meet call, {url}, asked for in {chat.get('channel')}:{chat.get('chat_id')}. Valor is in "
            "it; this session runs no turn of its own, and its worker answers what the call asks."
        ),
        model=model,
        harness_name=harness_name,
        project={"name": (asking.project or {}).get("name") or "valor"},
        max_effect_class=asking.max_effect_class,
        chat={"channel": CHANNEL, "chat_id": code},
    )
    asked_by = {**chat, "reply_to": reply_to, "topic_id": topic_id, "session": asking.id}
    call = {"url": url, "code": code, "machine": settings.machine, "asked_by": asked_by, "prep": prep}
    if starts:
        call["starts"] = starts
    if account:
        call["account"] = account
    await tasks.start(conn, brief, marker={"sdlc": 1, "call": call, "effect_key": effect_key}, by="valor",
                      via="meet.join")  # fmt: skip
    await report(
        conn,
        brief.id,
        "joining",
        f"Valor is joining the call (call session {brief.id}); its link comes in a `link` report once Valor "
        "is in. Post nothing more yet: the thread already has your line that the call is starting.",
    )
    return brief.id


async def entered(conn, session_id: str) -> None:
    """Valor is in the call: `call.joined` and the `link` report, once each,
    inside the caller's transaction."""
    found = await (
        await conn.execute("SELECT 1 FROM events WHERE task_id = %s AND type = 'call.joined'", (session_id,))
    ).fetchone()
    if found is not None:
        return
    await ledger.append(conn, session_id, "call.joined", {"machine": settings.machine})
    call = await marker(conn, session_id) or {}
    asked = call.get("asked_by") or {}
    where = f"reply_to {asked.get('reply_to')}" + (
        f", topic_id {asked['topic_id']}" if asked.get("topic_id") else ""
    )
    await report(
        conn,
        session_id,
        "link",
        f"Valor is in the Meet: {call.get('url')}\nThe thread it was asked in ({where}) already has the link, "
        "from the call or your own post: post nothing; what the call writes is copied there.",
    )


async def report(conn, session_id: str, what: str, text: str, **more: Any) -> str:
    """A `call` report on the asking chat's open session, opened when it
    has none, inside the caller's transaction. Returns that session."""
    call = await marker(conn, session_id) or {}
    asked = call.get("asked_by") or {}
    project = (await tasks.brief(conn, session_id)).project
    return await to_session(conn, asked["channel"], asked["chat_id"], f"call:{session_id}:{what}", text,
                            child_id=session_id, project=project, **more)  # fmt: skip


async def to_session(
    conn,
    channel: str,
    chat_id: str,
    about_key: str,
    text: str,
    *,
    child_id: str | None = None,
    project: dict[str, Any] | None = None,
    **more: Any,
) -> str:
    """`text` as a `call` report on the chat's open session, opened under
    `project` when it has none; once per `about_key`. The call's session is
    `child_id`, which the report puts in the asking session's
    `tasks.reporting`, so it can stop or steer the call."""
    target = await intake.chat_session(conn, channel, chat_id)
    if target is None:
        spec = workspace.Spec.load((project or {}).get("name") or "valor")
        own = intake._toms_own({"channel": channel, "chat_id": chat_id})
        target, brief = await intake.open_session(conn, channel, chat_id, spec, own=own)
        if brief is not None:
            await tasks.start(conn, brief, by="valor", via="kernel")
    found = await (
        await conn.execute(
            "SELECT 1 FROM events WHERE task_id = %s AND type = 'child.reported' AND payload->>'about_key' = %s",
            (target, about_key),
        )
    ).fetchone()
    if found is None:
        payload = {"child_id": child_id, "kind": "call", "about_key": about_key, "text": text, **more}
        await ledger.append(conn, target, "child.reported", payload)
    return target


# -- the end -----------------------------------------------------------------


async def end(
    conn,
    session_id: str,
    reason: str,
    *,
    late: list[dict[str, str]] | None = None,
    unanswered: list[str] | None = None,
    summary: dict[str, Any] | None = None,
) -> bool:
    """Write the call's end once (module docstring). False when it had
    already ended. `summary` is Gemini's summary document (`path`) and its
    `actions`, each listed as a task from the call, the call's `title` and
    `notes` (kept on `call.ended`, which `valor-record call --search`
    reads) and the lines on what it `decided`; or why it failed (`error`);
    None when none was made."""
    async with conn.transaction():
        await ledger.lock(conn, f"task:{session_id}")
        rows = await ledger.read(conn, session_id)
        if any(r["type"] == "call.ended" for r in rows):
            return False
        stopped = await tasks.is_stopped(conn, session_id)
        late, unanswered = list(late or []), list(unanswered or [])
        kept = {k: summary[k] for k in ("title", "notes") if (summary or {}).get(k)}
        await ledger.append(conn, session_id, "call.ended", {"reason": reason, "stopped": stopped, "late": late,
                            "unanswered": unanswered, **kept})  # fmt: skip
        if not stopped:
            await ledger.append(
                conn, session_id, "task.ended", {"kind": "call", "by": "valor", "reason": reason}
            )
        settled, answerable = await _settle(conn, session_id, rows)
        dropped = await _leftovers(conn, session_id, rows, stopped)
        call = await marker(conn, session_id) or {}
        code, thread = call.get("code"), f"reply_to {(call.get('asked_by') or {}).get('reply_to')}"
        if not any(r["type"] == "call.joined" for r in rows):
            lines = [f"Valor could not join the Meet {call.get('url')}: {reason}. Do not post its link."]
        elif summary and summary.get("path"):
            lines = [
                f"The call {call.get('url')} ended: {reason}.",
                (
                    f"Gemini's summary of the call is {summary['path']}. Copy it into your workspace under "
                    f"its own name and send it, unchanged, in the thread it was asked in ({thread}) as the "
                    "send's `files`, with at most two lines of text beside it. The recording, transcript "
                    "and notes are in Yudame Tools' meetings on this Mac, which back them up to Drive."
                ),
            ]
        else:
            why = (
                f"Gemini could not summarise it ({summary['error']})"
                if (summary or {}).get("error")
                else "no summary was made"
            )
            lines = [
                f"The call {call.get('url')} ended: {reason}.",
                (
                    f"{why}: say so in the thread it was asked in ({thread}), in one line; write no summary "
                    f"of your own. Its transcript, where any task given in the call is: `valor-record call {code}`."
                ),
            ]
        if actions := (summary or {}).get("actions"):
            lines.append("Tasks from the call: take, hand on or decline each in the thread it was asked in:")
            lines += [f"- {a['task']}" + (f" ({a['owner']})" if a.get("owner") else "") for a in actions]
        lines += (summary or {}).get("decided") or []
        lines += settled
        lines += [
            f'Answered by your worker after the call: on "{a["question"]}": {a["answer"]}' for a in late
        ]
        lines += [f"Asked of your worker and never answered: {q}" for q in unanswered]
        lines += dropped
        await report(conn, session_id, "ended", "\n".join(lines), settles=answerable)
    return True


async def _settle(
    conn, session_id: str, rows: list[dict[str, Any]]
) -> tuple[list[str], list[dict[str, str]]]:
    """Tom's read-backs (module docstring), the latest `done` one per
    question: the report's lines telling the asking chat's session to
    answer each and act, and the list it may answer (`settles`). Only Tom
    settles in a call (INSTRUCTIONS), so each is answered `--by tom`."""
    held = {r["payload"]["effect_id"]: r["payload"] for r in rows if r["type"] == "effect.held"}
    readbacks: dict[tuple[str, str], str] = {}
    for r in rows:
        p = r["payload"]
        action = held.get(p.get("effect_id")) or {}
        if (
            r["type"] != "effect.outcome"
            or p.get("kind") != "done"
            or action.get("action_type") != "meet.send_message"
        ):
            continue
        for q in (action.get("payload") or {}).get("settles") or []:
            readbacks[(q["task_id"], q["question_id"])] = action["payload"]["text"]
    if not readbacks:
        return [], []
    settles = [{"task_id": t, "question_id": q, "text": text} for (t, q), text in readbacks.items()]
    head = (
        "Tom settled these in the call; each counts as said, with no 👍 asked. Answer each now with "
        '`valor-record act answer TASK --text "<its read-back>" --by tom --via "meet call" --question-id Q`, '
        "carry it out, and post the read-backs in the thread the call was asked in as one notice of what is "
        "now being done, asking nothing:"
    )
    return [
        head,
        *(f"- task {c['task_id']}, question {c['question_id']}: {c['text']}" for c in settles),
    ], settles


async def settles(conn, session_id: str, task_id: str) -> bool:
    """Whether a call's report gave this session `task_id`'s read-back to answer."""
    row = await (
        await conn.execute(
            "SELECT 1 FROM events WHERE task_id = %s AND type = 'child.reported' AND payload->>'kind' = 'call' "
            "AND payload->'settles' @> %s::jsonb",
            (session_id, json.dumps([{"task_id": task_id}])),
        )
    ).fetchone()
    return row is not None


MEET_TYPES = ("meet.say", "meet.send_message")


async def _leftovers(conn, session_id: str, rows: list[dict[str, Any]], stopped: bool) -> list[str]:
    """Close what the call left: each `meet.say` and `meet.send_message`
    never released is refused, and one whose perform died is failed (the
    call that would take it is gone). A thread copy is left to its bridge,
    which delivers it, or, when the session was stopped, refuses it: those
    are named."""
    settled: dict[str, set[str]] = {}
    held: dict[str, dict[str, Any]] = {}
    for r in rows:
        p = r["payload"]
        if r["type"] == "effect.held":
            held[p["effect_id"]] = p
        elif r["type"] in ("effect.intent", "effect.outcome", "effect.refused"):
            settled.setdefault(p.get("effect_id"), set()).add(r["type"])
    lines = []
    for effect_id, p in held.items():
        done = settled.get(effect_id, set())
        if done & {"effect.outcome", "effect.refused"}:
            continue
        if p["action_type"] not in MEET_TYPES:
            if stopped:
                lines.append(
                    f"Dropped when the call was stopped, never copied to the thread: {p['payload'].get('text')}"
                )
            continue
        if "effect.intent" in done:
            await ledger.append(conn, session_id, "effect.outcome", {
                "effect_id": effect_id, "idempotency_key": p["idempotency_key"], "kind": "failed", "result": {},
                "error": "the call ended while it was performed", "reconciled": True,
            })  # fmt: skip
        else:
            await ledger.append(conn, session_id, "effect.refused", {
                **p, "reason": "the call ended before it was delivered", "at": "release",
            })  # fmt: skip
    return lines


async def sweep(conn) -> list[str]:
    """End each open call whose process is gone: its lock free after
    `call.started`, or no `call.started` `NEVER_STARTED_S` after it opened.
    Returns the sessions ended."""
    ended = []
    for session_id, at, started in await _open_calls(conn):
        if not started and datetime.now(UTC) - at < timedelta(seconds=NEVER_STARTED_S):
            continue
        key = f"meet:call:{session_id}"
        got = await (
            await conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,))
        ).fetchone()
        if not got[0]:
            continue
        try:
            why = "Valor's call process stopped" if started else "Valor's call process never started"
            if await end(conn, session_id, why):
                ended.append(session_id)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))
    return ended


# -- hearing: everyone's audio, mixed for the transcriber ---------------------------

RATE = 16_000  # what the transcriber hears
OUT_RATE = 24_000  # what Valor's voice speaks
MIX_S = 0.04  # the mix is sent this often
BACKLOG_S = 1.0  # a person's audio kept ahead of the mix at most
SPEAKING = 0.01  # a person's RMS above this is speech, for who is speaking
# A person's sound reaches the transcriber only from their first speech until this long
# after their last: faint speech under `SPEAKING` from the others (someone
# off-mic, a room behind a microphone) kept Live's own end of turn from ever
# coming, so it answered no one, silent 8 of 8 under it and answering 3 of 3
# once it stopped (tests/test_live_call_voice.py, 2026-11-18; the likely
# cause, inferred, of the group call's two requests by name unanswered that
# day). The recording keeps everyone's sound. A person who never reaches
# `SPEAKING` is not heard, as they were never counted as speaking (the transcriber's end of turn
# works as Live's did: on the quiet).
HOLD_S = 0.6
# Meet sends 48 kHz: a low-pass at 7.2 kHz, then every third sample.
LOWPASS = (np.hamming(49) * np.sinc((np.arange(49) - 24) * 2 * 7200 / 48_000)).astype(np.float32)
LOWPASS /= LOWPASS.sum()


def mixed(
    takes: dict[str, np.ndarray], due: int, spoke_at: dict[str, float], now: float
) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    """`due` samples of everyone's sound summed for the transcriber, each person's only
    while they speak (`HOLD_S`, `spoke_at` kept across frames); all of it
    summed, for the recording; and how loud each was."""
    heard, whole = np.zeros(due, np.float32), np.zeros(due, np.float32)
    loud: dict[str, float] = {}
    for who, take in takes.items():
        if len(take):
            loud[who] = float(np.sqrt(np.mean(take**2)))
            if loud[who] >= SPEAKING:
                spoke_at[who] = now
        whole[: len(take)] += take
        if now - spoke_at.get(who, -HOLD_S - 1) <= HOLD_S:
            heard[: len(take)] += take
    return heard, whole, loud


class Down:
    """One person's audio at 16 kHz, kept continuous across frames."""

    def __init__(self):
        self.tail = np.zeros(len(LOWPASS) - 1, dtype=np.float32)
        self.phase = 0

    def __call__(self, x: np.ndarray, rate: int) -> np.ndarray:
        if rate == RATE:
            return x.astype(np.float32)
        if rate != 3 * RATE:
            n = round(len(x) * RATE / rate)
            return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
        joined = np.concatenate([self.tail, x.astype(np.float32)])
        self.tail = joined[-(len(LOWPASS) - 1) :]
        y = np.convolve(joined, LOWPASS, "valid")
        out = y[self.phase :: 3]
        self.phase = (self.phase - len(y)) % 3
        return out


def pcm16(x: np.ndarray) -> bytes:
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


class Recording:
    """The call as Yudame Tools keeps a call: two channels, the left Valor
    (its microphone) and the right everyone else, 16-bit at 16 kHz, written
    as it goes to a WAV file."""

    def __init__(self, path: Path):
        import wave

        self.path = path
        self.file = wave.open(str(path), "wb")  # noqa: SIM115  open for the whole call, closed by `close`
        self.file.setnchannels(2)
        self.file.setsampwidth(2)
        self.file.setframerate(RATE)
        self.seconds = 0.0

    def write(self, mine: np.ndarray, theirs: np.ndarray) -> None:
        both = np.stack([mine, theirs], axis=1)
        self.file.writeframes((np.clip(both, -1, 1) * 32767).astype("<i2").tobytes())
        self.seconds += len(theirs) / RATE

    def close(self) -> None:
        self.file.close()


# -- answering: one mind writes every word (P-042) ------------------------------------
#
# Gemini's live transcriber hears the mix and ends each turn itself (fast end of
# speech: it never ended one inside a pause of 1.5 s or less that the person went on
# from, in Tom's recorded calls and a scripted Meet, 2026-11-25; the turn's end came
# 1.3-1.4 s after the last word). One text model writes every word, reading the
# meeting's state each turn, and Gemini's speech model says it, sentence by sentence.

INSTRUCTIONS = (
    "You are {name}, in a live Google Meet call as yourself, on the {project} project. You are an AI "
    "coworker; everyone in the call knows. What you write is spoken aloud in your voice, word for word "
    "(posted instead when answering a typed line): plain spoken sentences, one to three "
    "short ones, never lists, markdown, ids or symbols. When nothing is yours to answer, write nothing at "
    "all: you stay silent.\n\n"
    '- When to speak is "In a call" below. With only one other person here, everything they say is to '
    'you: answer it, and lead the call as "In a call" says.\n'
    "- The meeting now is the call's one record of what is open and decided: speak on a topic from it. "
    "Topics come in any order: follow the person. A decided or covered topic stays decided: never ask or "
    "raise it again, under any name, whatever an older line says. Asked what was decided, answer from the "
    "meeting now, with no tool.\n"
    "- When the person you answer decides something just now (picks an option, says yes to a choice, says it "
    "is decided), call `settle` once, before you write: the read-back, `by` who decided, and the ids of the "
    "open decisions it settles (`open_decisions`), if any. Choosing what to discuss, a proposal and your own "
    "recommendation are no decision. The ids are only those `open_decisions` returned; with none, give none. "
    "Then read the decision back in a sentence. Only Tom settles open decisions; a correction is a new `settle`. With only one person here, "
    "that read-back ends by opening the next open topic in the meeting now, explained, asking a yes only when "
    "it needs one; with none open there, ask what they want to take next. With more people here, it ends with "
    "the read-back: the next item waits until someone asks for it.\n"
    "- Never name a topic, figure or person that the meeting now, the call or a tool does not hold.\n"
    "- Write your reply with any `settle`, `ask`, `post` or `leave` call, in the same answer.\n"
    "- For anything you would have to look up (code, records, documents, figures), call `ask` and say you "
    "will check: your worker answers in the call's chat, and you are told. Never guess a fact.\n{backlog}"
    "- Call `person` only when someone asks about a person, or before you say what a person asked for, "
    "decided, is owed or was sent; answer from its dated facts, or say you have nothing recorded. Asked what "
    "you know about themselves, a person hears only their name, role and organisation. Asked who is on the "
    'team or who you have records of, call `person` with "everyone"; say their names.\n'
    "- A task anyone gives you in the call (to note something down, look into it, build it, fill records) is "
    "ordinary work: say in a sentence that it is noted.\n"
    '- When someone asks what is open, call `open_decisions` and walk through the items as "In a call" below '
    "says: explain each in their terms, say which only carry out what was already decided (status, no ask), and "
    "ask once for each that needs their answer, after explaining it. With anyone but Tom in the call, a "
    "decision about a client "
    "relationship (pricing, terms, hiring or pay) is one line: it is for Tom.\n"
    "- Call `post` only for what the room should read (a link, a figure, a list), never for a read-back.\n"
    '- Call `leave` only when anyone in the call asks you to leave (`why` "asked"), or everyone has wrapped up '
    'and said goodbye (`why` "wrapped up"); say goodbye. Never leave because the meeting\'s '
    "scheduled time has passed.\n\n"
    "How you work, from your persona:\n\n{conduct}\n\n{process}"
)
TURN = "{here}\n\n{state}\n\n{kept}{reading}The call so far:\n{so_far}\n\nNow: {now}"
NOW_SPOKEN = "answer what {name} said last, above."
NOW_GROUP = (
    "{name} spoke last, above. Answer only if it names you or follows straight on from your own answer; "
    "a question without your name is to the person they were talking with: then write nothing. Two people "
    "talking at once run together in one line: answer the part said to you."
)
NOW_UNCAUGHT = "{name}, the only one here, spoke to you, words you did not catch: say so in a few words."
NOW_TYPED = "answer what {name} typed, the last line above; your reply is posted in the call's chat."
UNPOSTED = "the call's chat failed, so the room has not seen this; say it aloud now, as it is: {text}"
TOOLS = [
    {"name": "ask", "description": "Ask your worker to look something up; the answer is posted in the call's "
     "chat and given to you.", "parameters": {"type": "object", "properties": {"question": {"type": "string"}},
     "required": ["question"]}},
    {"name": "post", "description": "Post text in the call's chat (copied to the thread the call was asked in).",
     "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "open_decisions", "description": "The decisions open for Tom on this project, with their ids.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "settle", "description": "Record a decision the moment it is made: the read-back, who decided "
     "(`by`, as the call names them), and each open decision it settles, as `task id/question id` or the "
     "question's id (none when it settles no open decision).", "parameters": {"type": "object", "properties": {
         "ids": {"type": "array", "items": {"type": "string"}}, "readback": {"type": "string"},
         "by": {"type": "array", "items": {"type": "string"}}},
         "required": ["readback"]}},
    {"name": "person", "description": "A person's dossier on this project, by their name or a loose description: "
     "internal or external, and their dated facts (role, organisation, asks, decisions, what is owed and "
     'delivered). With "everyone", the project\'s people, its own team first, with role and organisation.',
     "parameters": {"type": "object", "properties": {"about": {"type": "string"}},
     "required": ["about"]}},
    {"name": "leave", "description": "Leave the call once your reply is said: when someone asks you to, or "
     "everyone has said goodbye.", "parameters": {"type": "object", "properties": {
         "why": {"type": "string", "enum": ["asked", "wrapped up"]}}, "required": ["why"]}},
]  # fmt: skip
# What a turn does in the world (`settle`, `ask`, `post`, `leave`) waits until it is said (its first
# sound) or, saying nothing, until the mind is done: speech from the person answered before then drops
# the turn and what it held, and it is written again from what they went on to say (the transcriber
# ends some turns in a pause; P-042).
ACTS = ("settle", "ask", "post", "leave")  # the tools whose effects wait for the turn's first sound
ASKED = "asked: the answer will be posted in the chat and given to you"
POSTING = "posting in the chat"
MIND_ROUNDS = 4  # rounds of tool calls in one turn
MIND_OUT_MOST = 2_000  # tokens a turn is taken to write at most, for its estimate
# A call asked for in a chat joins first and reads up after (P-033, Tom, 2026-11-16).
READING_LINE = (
    "Your worker is still reading up for this call. Asked what that reading would hold, say you are still "
    "reading up, and answer from `backlog`, `person` and `open_decisions` meanwhile; everything else goes on "
    "as usual.\n\n"
)
READ_UP = (
    "Read up for this call while Valor joins it: the thread it was asked in ({where}), the project's record "
    "and memory on its topic, its open decisions and its priority queue (the product Sheet where it has one, "
    "else its open tasks). Write short notes for the call, covering the notes before it and what you found: "
    "what it is about, what is known and open, and what you would recommend, each recommendation with its concrete "
    "case (what happened, when, where it is recorded; observed, reported or assumed) or saying there is no "
    "case yet. For each item: what it is and what it changes, in the person's terms, and whether it only "
    "carries out what was already decided (status, no question) or needs their answer (a departure, a real "
    "choice or a risk). Name each decision once, under one label."
)
# The call's one live state, updated after every exchange (P-041, Tom, 2026-11-25: "I'd rather the
# interaction be slow than have to repeat myself"); the depth on what is coming (P-038, 2026-11-17).
UPDATE = (
    "Update the state of this call; nothing you write is posted: it reaches Valor's voice as its one note on "
    "the meeting, replacing the last. Write the topics: every topic of the call, from the notes before it, "
    "the state now and the call so far, each once under the label the call uses, in the order they matter "
    "now. For each, its state (open; decided, and what; parked) and what it touches; where a decision in this "
    "call changes it, `changed:` and how. Everything decided in this call is decided under any name, even "
    "where it adds work: never open again and no catch on it, since Valor raising it reopens it. "
    "Then, for the next two open topics the call has not reached: what it is and what it changes, in the "
    "person's terms; whether it only carries out what was already decided (status, no question) or needs "
    "their answer (a departure, a real choice or a risk); its case (what happened, when, where it is "
    "recorded; observed, reported or assumed), the numbers, and the two or three follow-ups likely asked, "
    "each answered a level below the last (an example, the mechanism, the step). Name each decision once, "
    "under the label the call uses. Answer only `no change` when the state needs none.\n\n"
    "Decided in this call (each stays decided, under any name):\n{decided}\n\n"
    "The state now:\n{topics}\n\nThe notes before the call:\n{prep}\n\nThe call so far:\n{so_far}"
)
STATE = (
    "The meeting now (replaces every earlier note on the meeting, the notes before the call included; say "
    "nothing about this note).\nDecided in this call (each is decided: never ask it again, under any name):\n"
    "{decided}\n\nTopics:\n{topics}"
)
# Kept for every turn (`Call.told`), the newest within a quarter of `CALL_SO_FAR`; one Valor has said is
# marked so, or it opened four replies in a row (the live Meet test, 2026-11-25).
ANSWERED = 'Your worker answered "{question}" (posted in the chat): {answer}'
SAID = 'Your worker answered "{question}", and you have said it (posted in the chat): {answer}\nSay it again only when asked.'
# Built on the chat session's stated default, no objection from Tom in five minutes, 2026-11-18; his to reverse.
# An answer asked for aloud is given when Valor next speaks (2026-11-18: it
# was posted only, and said 11 minutes later). Never as a turn of its own: at
# a pause it spoke over Tom answering Valor's own question (2026-11-19, Tom:
# hold it for the next opportunity to talk).
SAY_ANSWER = (
    'Your worker\'s answer to "{question}", asked aloud, is in and posted in the chat: {answer}\nOpen this '
    "reply with it, whatever the reply answers, in one short sentence saying the chat has the detail."
)
# A steer from the chat session reaches the voice as well as the chat (2026-11-18: a corrected
# answer reached only the call's chat).
STEERED = "Your chat session sent the call this, posted in the chat: {text}\nUse it from now on."
# A steer opening this passes a question asked in the call to its worker: the chat
# session's own lookup gave the room a second figure (2026-11-18). Built on
# the chat session's stated default, no objection from Tom in five minutes, 2026-11-18; his to reverse.
LOOK_UP = "look up:"
LEAVES = {"asked": "Valor was asked to leave", "wrapped up": "everyone wrapped up and said goodbye"}
PREP = object()  # a prep turn on the worker's lane (`Call.prepare`)
READ_NONE = "(Your reading for this call did not come back; `ask` your worker for what you need.)"
# Offered when the call's project has a backlog Sheet (Tom, 2026-11-13).
BACKLOG_LINE = (
    "- When someone asks about the backlog or an item on it (what is in it, the top priority, an item's size, "
    "value, due date, status, blocker, pitch or evidence), call `backlog` at once: with `about` set to how they "
    "described the item, in their own words, or with nothing for the whole queue in ranked order. Answer from "
    "what it returns, in their terms: what the item does for them, never its id or its title as the Sheet "
    "words it. When several items fit, say in a few words what each is and ask which they mean. `ask` your "
    "worker only for what the Sheet does not hold.\n"
)
BACKLOG = {"name": "backlog", "description": "Read the project's backlog Sheet. With `about`: the items that "
           "fit how someone described one (or an id), best first, each with all its values, its pitch and its "
           "blockers. Without: the whole queue in ranked order.", "parameters": {"type": "object",
           "properties": {"about": {"type": "string"}}}}  # fmt: skip
# The old call's front model's (ef5172ac1^:core/call.py): a sentence ends at its mark before a space.
SENTENCE = re.compile(r"[.!?…](?=\s)")


def system(project: str, sheet: bool = False) -> str:
    """The mind's standing instruction: Valor in the call, its tools, and
    the persona's "In a call" section and its "How product decisions are
    made" (Valor's own process as the project's manager, Tom, 2026-11-13),
    read as the call starts; with a `sheet`, when to use `backlog`. What
    changes from turn to turn (who is here, the meeting now, the call so
    far) is the turn's own (`Call.prompt`)."""
    return INSTRUCTIONS.format(
        name=persona.identity(settings.persona_dir)["name"],
        project=project or "Valor",
        conduct=persona.section(settings.persona_dir, "In a call"),
        process=persona.section(settings.persona_dir, "How product decisions are made"),
        backlog=BACKLOG_LINE if sheet else "",
    )


def sentences(text: str) -> tuple[list[str], str]:
    """The whole sentences at the start of `text`, and what is left of it."""
    out = []
    while (m := SENTENCE.search(text)) is not None:
        if said := text[: m.end()].strip():
            out.append(said)
        text = text[m.end() :]
    return out, text


SUMMARY = (
    "This is the recording of a Google Meet call Valor Engels (an AI coworker) was in, Valor's notes from "
    "before it, and its transcript (heard, said and typed lines, each with its minute). Write the notes on it, "
    "using only what was said:\n"
    "- topic: what the call was about, in two to five words, as its title;\n"
    "- summary: a short paragraph;\n- decisions: each decision made, and by: who made it, by their names as "
    "the transcript gives them; one of those recorded during the call (below) is never listed again, "
    "however it is worded: a decision that only restates one gives its number as `restates`;\n"
    "- actions: each task from the call: each thing someone said they would do, each task someone gave or "
    "asked Valor for, and each thing Valor said it would do, with its owner (the one to do it) or null;\n"
    "- markdown: the full summary as a Markdown document: who joined, what was discussed, what was settled, "
    "what Valor answered and promised, and what is still open.\n\nValor's notes from before the call:\n{prep}"
    "\n\nDecided and recorded during the call, numbered:\n{recorded}\n\nThe transcript:\n"
)
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "summary": {"type": "string"},
        "decisions": {"type": "array", "items": {"type": "object", "properties": {
            "decision": {"type": "string"}, "by": {"type": "array", "items": {"type": "string"}},
            "restates": {"type": "integer", "nullable": True}},
            "required": ["decision"]}},
        "actions": {"type": "array", "items": {"type": "object", "properties": {
            "task": {"type": "string"}, "owner": {"type": "string", "nullable": True}}, "required": ["task"]}},
        "markdown": {"type": "string"},
    },
    "required": ["summary", "decisions", "actions", "markdown"],
}  # fmt: skip


GOOGLE_KEY = "GOOGLE_API_KEY"


HEDGE = 2  # copies of each round of the mind asked at once (`Gemini.think`)
RETRY_S = (1.0, 3.0)  # waits before asking a failed round again


async def first_of(starts: list[Callable[[], Any]]):
    """The items of whichever of `starts` (each making an async iterator)
    yields first; the others are stopped then. One that fails before its
    first item leaves the rest; when all do, the last failure is raised."""
    got: asyncio.Queue = asyncio.Queue()
    over = object()

    async def pump(i, start):
        try:
            async for item in start():
                await got.put((i, item, None))
            await got.put((i, over, None))
        except Exception as exc:  # noqa: BLE001  handed to the reader
            await got.put((i, None, exc))

    runs = [asyncio.create_task(pump(i, start)) for i, start in enumerate(starts)]
    winner, failed = None, 0
    try:
        while True:
            i, item, exc = await got.get()
            if winner is None and exc is not None:
                failed += 1
                if failed == len(runs):
                    raise exc
                continue
            if winner is None:
                winner = i
                for j, run in enumerate(runs):
                    if j != i:
                        run.cancel()
            if i != winner:
                continue
            if exc is not None:
                raise exc
            if item is over:
                return
            yield item
    finally:
        for run in runs:
            run.cancel()
        await asyncio.gather(*runs, return_exceptions=True)


class Gemini:
    """Valor's Gemini API (the key `python -m core google-key` copies from
    the vault): a call's ears, mind and voice, and its summary."""

    def __init__(self, key: str):
        from google import genai

        self.client = genai.Client(api_key=key)

    def hear(self):
        """A transcriber session (`settings.call_ears_model`): English, its
        own end of turn at high end-of-speech sensitivity (the step-1 test,
        2026-11-25: it waits about a second of quiet either way, and cut no
        one off). It streams for ten minutes at most (`Call.listen`)."""
        from google.genai import types

        config = types.LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=types.AudioTranscriptionConfig(language_codes=["en-US"]),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH
                )
            ),
        )
        return self.client.aio.live.connect(model=settings.call_ears_model, config=config)

    async def think(self, instruction: str, contents: list, tools: list[dict], *, words_only: bool = False):
        """One round of the mind (`settings.call_mind_model`), streamed:
        each part as `{"part"}` with its `text` (not a thought) or its
        `call` (name, args), then `{"usage"}`; `words_only`, no tool calls.
        The parts go back into `contents` as they came, thought signatures
        and all. `HEDGE` copies are asked at once and the first to answer is
        taken (`first_of`): one copy's first sentence came in 5.7 s median,
        9.5 s p90, two's in 4.0 and 6.4; through Meet, the delay per reply
        went from 8.1 s median to 7.0, and three copies measured the same as
        two (2026-11-25); each other copy is
        charged its input. A round none of them answers, a 503 or 429 among
        them (four silent turns in one live run, 2026-11-25), is asked again
        after `RETRY_S`."""
        from google.genai import errors, types

        model = settings.call_mind_model
        mode = types.FunctionCallingConfigMode.NONE if words_only else types.FunctionCallingConfigMode.AUTO
        config = types.GenerateContentConfig(
            system_instruction=instruction,
            tools=[types.Tool(function_declarations=[types.FunctionDeclaration(**t) for t in tools])],
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode=mode)),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(thinking_level="minimal" if "lite" in model else "low"),
        )

        async def stream():
            async for got in await self.client.aio.models.generate_content_stream(
                model=model, contents=contents, config=config
            ):
                yield got

        for wait in (*RETRY_S, None):
            usage, began = None, False
            try:
                async for got in first_of([stream] * HEDGE):
                    usage, began = got.usage_metadata or usage, True
                    content = got.candidates[0].content if got.candidates else None
                    for part in (content.parts if content else None) or []:
                        if part.function_call:
                            yield {
                                "part": part,
                                "call": (part.function_call.name, dict(part.function_call.args or {})),
                            }
                        else:
                            yield {"part": part, "text": "" if part.thought else part.text or ""}
            except errors.APIError as exc:
                if began or wait is None or not (exc.code == 429 or exc.code >= 500):
                    raise
                _log(f"the mind's round failed ({exc.code}); asking again in {wait} s")
                await asyncio.sleep(wait)
                continue
            got = usage.model_dump(mode="json", exclude_none=True) if usage else {}
            asked = {"prompt_token_count": got.get("prompt_token_count", 0),
                     "prompt_tokens_details": got.get("prompt_tokens_details", [])}  # fmt: skip
            for extra in [got, *[asked] * (HEDGE - 1)]:
                yield {"usage": extra}
            return

    async def speak(self, text: str):
        """`text` in Valor's voice (`settings.call_voice` on
        `settings.call_voice_model`): 16-bit PCM at `OUT_RATE` as it comes,
        then `{"usage"}`."""
        from google.genai import types

        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=settings.call_voice)
                )
            ),
        )
        usage = None
        async for got in await self.client.aio.models.generate_content_stream(
            model=settings.call_voice_model, contents=text, config=config
        ):
            usage = got.usage_metadata or usage
            content = got.candidates[0].content if got.candidates else None
            for part in (content.parts if content else None) or []:
                if part.inline_data and part.inline_data.data:
                    yield part.inline_data.data
        yield {"usage": usage.model_dump(mode="json", exclude_none=True) if usage else {}}

    async def summarise(
        self, audio: Path, transcript: str, prep: str, recorded: str = ""
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """The notes on a call from its recording, uploaded through the
        Files API (a request's own bytes stop near 20 MB) and deleted after,
        its prep (an invite's title, the asking chat's topic), the
        decisions `recorded` during it and its transcript; with the
        response's usage."""
        from google.genai import types

        files = self.client.aio.files
        up = await files.upload(file=str(audio), config={"mime_type": "audio/mp4"})
        try:
            while up.state and up.state.name == "PROCESSING":
                await asyncio.sleep(2)
                up = await files.get(name=up.name)
            got = await self.client.aio.models.generate_content(
                model=settings.summary_model,
                contents=[
                    up,
                    SUMMARY.format(prep=prep.strip() or "(none)", recorded=recorded or "(none)") + transcript,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", response_schema=SUMMARY_SCHEMA
                ),
            )
        finally:
            with contextlib.suppress(Exception):
                await files.delete(name=up.name)
        usage = got.usage_metadata.model_dump(mode="json", exclude_none=True) if got.usage_metadata else {}
        return json.loads(got.text), usage


async def decisions(conn, project: str | None) -> str:
    """The open decisions as the call reads them: the project's board
    snapshot now, each with its task and question ids, and the board's
    canvas as last written (`board.canvas_markdown`)."""
    from core import board

    spec = board.board_spec(project)
    if spec is None:
        return "This project has no decisions open."
    snap = await board.snapshot(conn, spec)
    lines = [f"- {q['task_id']}/{q['question_id']}: {q['question']}" for q in snap["open"]]
    canvas = await board.canvas_markdown(conn, spec)
    out = ["Open for Tom:", *(lines or ["- nothing"])]
    if canvas:
        out += ["", "The decision board as last written:", canvas]
    return "\n".join(out)


# -- the store: a call kept as Yudame Tools keeps a meeting ------------------------


def _escape(text: str) -> str:
    return "".join("\\" + c if c in "\\`*_[]#<>" else c for c in text)


def _clock(seconds: float) -> str:
    total = int(seconds // 1)
    h, m, s = total // 3600, total // 60 % 60, total % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def notes_md(title: str, summary: dict[str, Any]) -> str:
    """notes.md as Yudame Tools' `NoteFormat.markdown` writes it."""
    lines = [f"# {title} - Notes", "", "## Summary", "", summary["summary"], "", "## Decisions", ""]
    lines += [f"- {_escape(d)}" for d in summary["decisions"]] or ["None recorded."]
    lines += ["", "## Action items", ""]
    lines += [
        "- [ ] " + _escape(a["task"]) + (f" ({_escape(a['owner'])})" if a.get("owner") else "")
        for a in summary["actions"]
    ] or ["None recorded."]
    return "\n".join([*lines, ""])


def transcript_md(title: str, segments: list[dict[str, Any]]) -> str:
    """transcript.md as Yudame Tools' `TranscriptMerge.markdown` writes it."""
    turns: list[list] = []
    for s in segments:
        if turns and turns[-1][0] == s["speaker"]:
            turns[-1][2].append(s["text"])
        else:
            turns.append([s["speaker"], s["start"], [s["text"]]])
    body = [f"**{who}** ({_clock(start)}): {' '.join(text)}" for who, start, text in turns]
    return "\n".join([f"# {title} - Transcript", "", *(x for b in body for x in (b, ""))])


def meta_json(title: str, start: datetime, end: datetime, zone: str, segments: list[dict[str, Any]]) -> str:
    """meta.json for a finished meeting the app has only to back up: its
    sound checked, transcribed and summarised, every file pending upload."""
    stamp = lambda t: t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    speakers = list(dict.fromkeys(s["speaker"] for s in segments))
    meta = {
        "title": title, "start": stamp(start), "end": stamp(end), "timeZone": zone, "app": "Google Meet",
        "mode": "call", "speakers": speakers, "transcriptPending": False, "speakersPending": False,
        "summaryPending": False, "links": [], "audioDeleted": False, "recordingLost": False, "typedNotes": "",
        "notesChanged": False, "recording": False, "soundChecked": True, "transcribeTried": True,
        "uploads": [x for f in ("recording", "transcript", "notes") for x in (f, {"pending": {}})],
    }  # fmt: skip
    return json.dumps(meta, indent=2, sort_keys=True)


def folder_name(title: str, start: datetime) -> str:
    """`MeetingStore.folderName`: "2026-11-05 1000 <title>" in the Mac's zone."""
    return f"{start.astimezone().strftime('%Y-%m-%d %H%M')} {title.replace('/', '-').replace(':', '-')}"


def title(topic: str | None, names: list[str]) -> str:
    """A call's title: "<topic> - <first names>", its topic at most 60
    characters (else "Valor call"), and no character a file name or the
    store's folder name cannot hold."""
    clean = lambda t: " ".join(re.sub(r"[/:\\]", " ", t).split())
    return f"{clean(topic or '')[:60].strip() or 'Valor call'} - {clean(', '.join(names))}"


GITHUB = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?")
# Left as they are: a Markdown link, an autolink, a bare URL, a code span.
LINKED = r"\[[^\]]*\]\([^)]*\)|<[^>\s]+>|https?://\S+|`[^`]*`"


def linked(text: str, repo: str | None, links: dict[str, str]) -> str:
    """References in `text` as Markdown links: "PR 12", "PR #12" and
    "pull request 12" to the GitHub `repo`'s pull, "issue 12" to its issue,
    and each of `links`' prefixes and its digits ("ENG-633") to its URL
    plus the digits. A bare "#12" stays as it is ("#1 priority")."""
    found = GITHUB.fullmatch(repo or "")
    parts = [f"(?P<keep>{LINKED})"]
    if found:
        parts += [
            r"(?P<pull>\b(?:PR|[Pp]ull [Rr]equest) ?#?(?P<pn>\d+)\b)",
            r"(?P<issue>\b[Ii]ssue ?#?(?P<in>\d+)\b)",
        ]
    order = list(links)
    parts += [rf"(?<![\w-])(?P<x{i}>{re.escape(p)}(?P<x{i}n>\d+)\b)" for i, p in enumerate(order)]
    base = f"https://github.com/{found[1]}" if found else ""

    def one(m: re.Match) -> str:
        if m["keep"]:
            return m[0]
        if found and m["pull"]:
            return f"[{m[0]}]({base}/pull/{m['pn']})"
        if found and m["issue"]:
            return f"[{m[0]}]({base}/issues/{m['in']})"
        i = next(i for i in range(len(order)) if m[f"x{i}"])
        return f"[{m[0]}]({links[order[i]]}{m[f'x{i}n']})"

    return re.sub("|".join(parts), one, text)


def keep(staging: Path, store: Path, name: str) -> Path:
    """Move the finished folder into the store in one rename, so the app
    never reads a half-written one; a second with the same name gets " 2"."""
    store.mkdir(parents=True, exist_ok=True)
    target, n = store / name, 2
    while target.exists():
        target, n = store / f"{name} {n}", n + 1
    staging.rename(target)
    return target


# -- the call ------------------------------------------------------------------

# "Valor, leave" from Tom ends the call as any leave does (Tom, 2026-10-27).
LEAVE = re.compile(
    r"\bvalor\b(?:(?!\b(?:don'?t|do not|not|never)\b).){0,30}?\b(?:leave|drop off|hang up)\b", re.IGNORECASE
)
JOIN_LINE = "Valor here, recording for notes."
LOST_LINE = "I've lost my hearing connection; leaving, the summary will follow in the thread."
JOIN_S = 90  # to get through Meet's join screen
LOBBY_S = 600  # waiting to be let in
ALONE_S = 600  # with no one else ever there
EMPTY_S = 120  # after the last other person left
SOCKET_S = 30  # without the page's audio, or out of the call
WORKER_WAIT_S = 120  # for questions still with the worker after leaving
RECONNECTS = 2  # failed opens of the transcriber in a row before the call leaves
STATE_S = 2.0
# The transcriber streams ten minutes at most: it is reopened by `EARS_S`, at
# a quiet moment (`EARS_QUIET_S`) in the last `EARS_EARLY_S` before it.
EARS_S = 540.0
EARS_EARLY_S = 60.0
EARS_QUIET_S = 1.0
EARS_HANDOVER_S = 2.0  # a transcriber let go hears out what it was hearing this long
# This much unbroken sound from the person Valor answers cuts him off, or,
# before his first sound, drops the turn. At 0.2 s three of four cuts in
# Tom's call (2026-11-06) were 0.22-0.24 s of sound he did not go on from;
# the one he meant ran 0.5 s and on.
BARGE_S = 0.4
# Valor's name as a transcript writes it down, misheard too ("Val", "Valer", 2026-11-18; "Valar",
# "Valour", 2026-11-25).
MISHEARD = ("valer", "val", "valar", "valour")
# Characters of the call so far in each turn: at about four a token, the
# context Live's compression held (`CALL_CONTEXT`).
CALL_SO_FAR = CALL_CONTEXT


@dataclass
class Post:
    turn: str | None
    text: str
    copy: bool
    settles: list[dict[str, str]] = field(default_factory=list)
    # A worker's answer, (question, answer), kept for the turns once the whole post is in the chat
    # (`Call.told`); asked aloud, given in the next reply first (`Call.say_next`).
    answer: tuple[str, str] | None = None
    aloud: bool = False


@dataclass
class Reply:
    """One turn's speech: its audio, held until its `meet.say` is
    released, and its words, as they were sent to the voice."""

    id: str
    held: list[bytes] = field(default_factory=list)
    released: bool = False
    dropped: bool = False
    text: str = ""
    seconds: float = 0.0  # of audio received
    started: float | None = None  # on the recording's clock
    generated: bool = False
    over: asyncio.Future | None = None
    said: asyncio.Task | None = None


@dataclass
class Turn:
    """One turn of the mind (`Call._turn`): what it answers, the effects
    it holds until it commits, and its reply."""

    id: str
    now: str
    typed: bool  # answers a typed line: posted, never spoken
    answering: str | None  # whose words: their speech before the first sound drops the turn
    after: Turn | None = None  # the turn before, said out before this one speaks
    reply: Reply | None = None
    held: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    say: list[tuple[str, str]] = field(default_factory=list)  # worker answers it was told to open with
    committed: bool = False
    dropped: bool = False
    task: asyncio.Task | None = None

    def done(self) -> bool:
        return (self.task is None or self.task.done()) and (
            self.reply is None or self.reply.over is None or self.reply.over.done()
        )


class _Performer:
    """A call's `meet.say` or `meet.send_message`, performed by this
    process, which alone is in the call. Nothing outside it can look one
    up, so one whose answer was lost settles `failed`."""

    effect_class = "act"
    usage = None

    def __init__(self, action_type: str, perform: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]):
        from core import bridge

        self.action_type, self._perform, self._check = (
            action_type,
            perform,
            bridge.DECLARED[action_type].check,
        )

    async def refuse(self, conn, action: broker.Action) -> str | None:
        return await self._check(conn, action)

    async def perform(self, action: broker.Action, key: str) -> dict[str, Any]:
        return await self._perform(action.payload)

    async def lookup(self, action: broker.Action, key: str) -> dict[str, Any] | None:
        return None


def _cut(text: str, played: float) -> str:
    """The words of `text` said in the `played` share of it."""
    words = text.split()
    return " ".join(words[: round(len(words) * max(0.0, min(1.0, played)))])


class Call:
    """Valor in one call (module docstring): what it hears is mixed and
    streamed to Gemini's transcriber; each turn it ends that is Valor's to
    answer, one mind writes the reply, with the meeting's state and its
    tools, and Gemini's voice says it; the page, Gemini and the worker are
    injected, so a test drives it without Meet or Google."""

    def __init__(
        self,
        session_id: str,
        info: dict[str, Any],
        page,
        *,
        dsn: str,
        gemini,
        worker: Callable[[str], Awaitable[str]],
        worker_ready: Callable[[], Awaitable[bool]],
        reader: Callable[[], Awaitable[str]] | None = None,
        preparer: Callable[[str], Awaitable[str]] | None = None,
        project: str | None = None,
        socket=None,
        clock: Callable[[], float] = time.monotonic,
        store: str | None = None,
        held: db.Held | None = None,
    ):
        from core import bridge

        self.id, self.info, self.page, self.dsn, self.gemini = session_id, info, page, dsn, gemini
        self.held = held  # the call's locks (`core/__main__.py` `_call`), taken again after a restart
        self.worker, self.worker_ready, self.project = worker, worker_ready, project
        # A call asked for in a chat joins first; its worker's first turn reads up (`_read_up`).
        self.reader = reader if not info.get("starts") else None
        self.reading = self.reader is not None
        self.preparer, self.prep_queued = preparer, False
        # The call's one live state (P-041): what it decided, each the moment it was (`settle`), and the
        # worker's topics (`_update`), read by every turn of the mind (`prompt`).
        self.decided: list[dict[str, Any]] = []
        self.topics = ""
        self.told: list[str] = []  # the notes every turn is given: the worker's answers, the steers
        self.say_next: list[tuple[str, str]] = []  # answers asked aloud, for the next reply to open with
        self.aloud: set[str] = set()  # the questions asked aloud, by their words
        first = persona.identity(settings.persona_dir)["name"].split()[0].lower()
        # Run into the next word too ("ValorStop", the transcriber, 2026-11-25).
        self.named = re.compile(rf"(?i:\b(?:{'|'.join(map(re.escape, (first, *MISHEARD)))}))(?:\b|(?=[A-Z]))")
        self.sheet: str | None = None  # the project's backlog Sheet, read as the call starts (`run`)
        self.instruction = ""  # the mind's (`system`), as the call starts
        self.socket, self.clock = socket, clock
        # An invited call joined before its start waits for the others, and
        # to be let in, from the start (docs/plans/invite-calls.md).
        starts = info.get("starts")
        self.early = (
            max(0.0, (datetime.fromisoformat(starts) - datetime.now(UTC)).total_seconds()) if starts else 0.0
        )
        self.store = Path(store or settings.meetings_store)
        self.declared = broker.Performers(*bridge.declared_performers())
        self.bound = broker.Performers(
            _Performer("meet.say", self._say), _Performer("meet.send_message", self._post)
        )
        self.ears = None  # the transcriber's session, while open
        self.ears_at = 0.0
        self.unsent = np.zeros(0, np.float32)  # heard while no transcriber is open, for the next
        self.downs: dict[str, Down] = {}
        self.buffers: dict[str, np.ndarray] = {}
        self.spoke_at: dict[str, float] = {}  # when each person last spoke, for what the transcriber hears
        self.mine = np.zeros(0, np.float32)  # Valor's own speech, for the recording
        self.names: dict[str, str] = {}
        self.first_names: list[str] = []  # who was in the call, for its title
        self.present: set[str] = set()  # who else is in the call now
        self.me: str | None = None
        self.energy: dict[str, float] = {}  # each person's speech in the turn being heard
        self.heard = ""  # the transcriber's words of that turn so far
        self.heard_at: float | None = None
        self.last_voice = 0.0
        self.lines: list[str] = []  # the call so far, for the mind, the worker and the summary
        self.segments: list[dict[str, Any]] = []  # the spoken transcript, for the store
        self.turn: Turn | None = None  # the mind's newest turn
        self.replies: dict[str, Reply] = {}  # each reply whose `meet.say` is not settled
        self.out: Reply | None = None  # the reply whose audio is playing
        self.answering: str | None = None  # whose words the reply playing answers
        self.went_on: str | None = None  # who went on speaking before Valor's first sound, dropping his turn
        self.barge = 0.0
        self.playing_until = 0.0
        self.posts: asyncio.Queue = asyncio.Queue()
        self.unposted: list[str] = []  # posts the chat failed, for Valor to say aloud
        self.questions: asyncio.Queue = asyncio.Queue()
        self.asking: list[str] = []
        self.asked: dict[
            str, str | None
        ] = {}  # each question asked of the worker, by its words, and its answer
        self.late: list[dict[str, str]] = []
        self.dropped: list[str] = []
        self.left = False
        self.leaving = asyncio.Event()
        self.reason = ""
        self.hard = False
        self.jobs: set[asyncio.Task] = set()
        self.recording: Recording | None = None
        self.staging: Path | None = None
        self.started_at = datetime.now(UTC)
        self.t0 = clock()

    def now(self) -> float:
        """Seconds into the recording."""
        return self.recording.seconds if self.recording else self.clock() - self.t0

    # -- hearing (the page's socket calls these) --

    def hear(self, who: str, samples: np.ndarray, rate: int) -> None:
        if not who or who == self.me:
            return
        x = self.downs.setdefault(who, Down())(samples, rate)
        buf = np.concatenate([self.buffers.get(who, np.zeros(0, np.float32)), x])
        self.buffers[who] = buf[-int(BACKLOG_S * RATE) :]

    def chat(self, who: str, text: str) -> None:
        if who == self.me or not text.strip():
            return
        self._spawn(self._typed(who, text.strip()))

    def people(self, update: dict[str, Any]) -> None:
        for p in [*(update.get("newUsers") or []), *(update.get("updatedUsers") or [])]:
            if p.get("isCurrentUser"):
                self.me = p.get("deviceId")
            elif p.get("deviceId"):
                self.names[p["deviceId"]] = p.get("fullName") or p.get("displayName") or "someone"

    async def mix(self) -> None:
        """Every `MIX_S`, what everyone said since, summed (`mixed`), to the
        transcriber and the recording (silence too, which ends a turn); who
        is loudest is who is speaking."""
        sent, start = 0, self.clock()
        while not self.leaving.is_set():
            await asyncio.sleep(MIX_S)
            due = int((self.clock() - start) * RATE) - sent
            if due <= 0:
                continue
            sent += due
            await self._tick(due)

    async def _tick(self, due: int) -> None:
        takes = {}
        for who, buf in list(self.buffers.items()):
            takes[who], self.buffers[who] = buf[:due], buf[due:]
        frame, whole, loud = mixed(takes, due, self.spoke_at, self.now())
        mine, self.mine = self.mine[:due], self.mine[due:]
        if self.recording is not None:
            self.recording.write(np.pad(mine, (0, due - len(mine))), whole)
        # What is heard while the transcriber reopens goes to the next, so nothing said is lost.
        sending, self.unsent = np.concatenate([self.unsent, frame]), np.zeros(0, np.float32)
        if self.ears is None:
            self.unsent = sending
        else:
            from google.genai import types

            with contextlib.suppress(Exception):  # a dropped session is `listen`'s to reopen
                for i in range(0, len(sending), RATE // 25):  # in 40 ms chunks, as the mix sends
                    await self.ears.send_realtime_input(audio=types.Blob(
                        data=pcm16(sending[i : i + RATE // 25]), mime_type=f"audio/pcm;rate={RATE}"))  # fmt: skip
        await self._speakers(loud, due / RATE)

    async def _speakers(self, loud: dict[str, float], seconds: float) -> None:
        """Who is speaking: each person's speech in the turn being heard,
        and the person Valor answers speaking over him (`BARGE_S`), which
        cuts him off once he is speaking and drops the turn before; then
        what the chat failed to post, said once he is quiet."""
        voiced = {w: v for w, v in loud.items() if v >= SPEAKING}
        t = self.turn
        over = self.out is not None and self.answering in voiced
        early = t is not None and not t.committed and not t.dropped and t.answering in voiced
        self.barge = self.barge + seconds if over or early else 0.0
        if self.barge >= BARGE_S:
            self.barge = 0.0
            if over:
                await self._cut_off(self.out)
            else:
                self._drop(t, "they went on")
                self.went_on = t.answering
        for who, v in voiced.items():
            self.energy[who] = self.energy.get(who, 0.0) + v * seconds
        if voiced:
            self.last_voice = self.now()
        await self._say_unposted()

    async def _turn_end(self) -> None:
        """A turn the transcriber heard end: one `call.heard` row, said by
        whoever spoke most in it, and an answer when it is Valor's: all of
        it with one other person here (Tom's "Hello", unanswered,
        2026-11-05); in a group, what names him or follows straight on
        from his answer (the persona's "In a call"; side talk with Sam was
        answered once Sam had left, 2026-11-05). Words it did not catch from
        the only person here are asked for again; a group's turn dropped
        because its person went on is answered when they finish. "Valor,
        leave" from Tom leaves."""
        text, energy, at = self.heard.strip(), self.energy, self.heard_at
        self.heard, self.energy, self.heard_at = "", {}, None
        alone = len(self.present) == 1
        who = max(energy, key=energy.get) if energy else next(iter(self.present)) if alone else None
        name = self.names.get(who, "someone") if who else "someone"
        if not text:
            if alone and energy and not self.leaving.is_set():
                self._answer(NOW_UNCAUGHT.format(name=name), who)
            return
        # Straight on from his answer, or going on from words whose turn they talked over.
        follows = (bool(self.lines) and "] Valor (spoke" in self.lines[-1]) or who == self.went_on
        self.went_on = None
        await self._heard(who or "", name, text, "spoke")
        self.segments.append({"speaker": name, "start": round(at if at is not None else self.now(), 2),
                              "end": round(self.now(), 2), "text": text, "channel": "call"})  # fmt: skip
        if self.leaving.is_set() or who is None:
            return
        if alone:
            self._answer(NOW_SPOKEN.format(name=name), who)
        elif self.named.search(text) or follows:
            self._answer(NOW_GROUP.format(name=name), who)

    async def _heard(self, who: str, name: str, text: str, via: str) -> None:
        async with await self._connect() as conn:
            await ledger.append(conn, self.id, "call.heard", {"turn_id": ledger.new_id(), "who": who,
                                "name": name, "via": via, "text": text})  # fmt: skip
        self.lines.append(f"[{_clock(self.now())}] {name} ({via}): {text}")
        if name == settings.operator_name and LEAVE.search(text):
            self.leave("Tom asked Valor to leave")

    async def _typed(self, who: str, text: str) -> None:
        name = self.names.get(who, "someone")
        await self._heard(who, name, text, "typed")
        if not self.leaving.is_set():
            self._answer(NOW_TYPED.format(name=name), None, typed=True)

    # -- the ears --

    async def listen(self) -> None:
        """The transcriber for the whole call, reopened before its stream's
        ten minutes end (`EARS_S`), the next one open before the last is let
        go, so nothing said meanwhile is lost (an utterance begun as one was
        closed was never heard, the live Meet test, 2026-11-25); the last
        hears out what it was hearing for `EARS_HANDOVER_S`. One that drops
        is opened again, what is said meanwhile kept for it (`_tick`). After
        `RECONNECTS` failed opens in a row (6 of 55 opens timed out on the
        handshake, 2026-11-25), one line in the chat and the call leaves."""
        failed, current = 0, None  # (the session's context, its receiver)
        try:
            while not self.leaving.is_set():
                opening = self.gemini.hear()
                try:
                    ears = await opening.__aenter__()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001  a refused open is tried again, or the call leaves
                    failed += 1
                    _log(f"call {self.id}: the transcriber failed to open: {exc!r}")
                    if failed >= RECONNECTS:
                        self.posts.put_nowait(Post(None, LOST_LINE, False))
                        self.leave("Valor lost its hearing connection")
                        return
                    await asyncio.sleep(1)
                    continue
                failed = 0
                if current is not None:
                    self._spawn(self._let_go(*current))
                self.ears, self.ears_at = ears, self.clock()
                current = (opening, asyncio.create_task(self._receive(ears)))
                _log(f"call {self.id}: the transcriber is open")
                due = asyncio.create_task(self._ears_due())
                await asyncio.wait({current[1], due}, return_when=asyncio.FIRST_COMPLETED)
                due.cancel()
                if current[1].done():  # it dropped: hear into `unsent` until the next is open
                    self.ears = None
                    exc = None if current[1].cancelled() else current[1].exception()
                    _log(f"call {self.id}: the transcriber dropped: {exc!r}")
                    with contextlib.suppress(Exception):
                        await opening.__aexit__(None, None, None)
                    current = None
        finally:
            self.ears = None
            if current is not None:
                await self._let_go(*current, wait=0.0)

    async def _let_go(self, opening, receiving: asyncio.Task, wait: float | None = None) -> None:
        """A transcriber no longer sent to, closed once it has heard out what
        it was hearing."""
        await asyncio.sleep(EARS_HANDOVER_S if wait is None else wait)
        receiving.cancel()
        await asyncio.gather(receiving, return_exceptions=True)
        with contextlib.suppress(Exception):
            await opening.__aexit__(None, None, None)

    async def _ears_due(self) -> None:
        """Until the transcriber is to be reopened: at a quiet moment in the
        last `EARS_EARLY_S` before `EARS_S`, between turns, or at `EARS_S`."""
        while True:
            await asyncio.sleep(min(0.2, STATE_S))
            age = self.clock() - self.ears_at
            quiet = self.now() - self.last_voice >= EARS_QUIET_S and not self.heard and self.heard_at is None
            if age >= EARS_S or (age >= EARS_S - EARS_EARLY_S and quiet):
                _log(f"call {self.id}: reopening the transcriber after {age:.0f} s")
                return

    async def _receive(self, ears) -> None:
        from google.genai import types

        while not self.leaving.is_set():
            async for m in ears.receive():
                if m.usage_metadata is not None:
                    usage = m.usage_metadata.model_dump(mode="json", exclude_none=True)
                    await self._meter(settings.call_ears_model, usage)
                sc = m.server_content
                if sc is not None and sc.input_transcription and sc.input_transcription.text:
                    self.heard += sc.input_transcription.text
                kind = m.voice_activity.voice_activity_type if m.voice_activity else None
                if kind == types.VoiceActivityType.ACTIVITY_START and self.heard_at is None:
                    self.heard_at = self.now()
                elif kind == types.VoiceActivityType.ACTIVITY_END:
                    await self._turn_end()

    # -- the mind --

    def _answer(self, now: str, who: str | None, *, typed: bool = False) -> None:
        """A turn of the mind answering `now`; a turn before it not yet said
        is dropped, what it held with it: the newer has all it heard."""
        if self.turn is not None and not self.turn.committed and not self.turn.dropped:
            self._drop(self.turn, "a newer turn")
        t = Turn(ledger.new_id(), now, typed, who, after=self.turn)
        t.reply = Reply(t.id)
        self.turn = t
        t.task = asyncio.create_task(self._turn(t))
        self.jobs.add(t.task)
        t.task.add_done_callback(self.jobs.discard)

    def _drop(self, t: Turn, why: str) -> None:
        """A turn not yet said, dropped with what it held; the person's next
        words start another (`_turn_end`)."""
        t.dropped = True
        if t.task is not None:
            t.task.cancel()
        _log(f"call {self.id}: turn {t.id} dropped before its first sound: {why}")

    def prompt(self, t: Turn) -> str:
        """What a turn is given: who is here, the meeting now (`state`),
        the notes kept for it (the newest within a quarter of
        `CALL_SO_FAR`) and an answer asked aloud to open with, the call so
        far in what is left, and what to answer."""
        kept: list[str] = []
        for text in reversed(self.told):
            if sum(len(k) + 3 for k in [*kept, text]) > CALL_SO_FAR // 4:
                break
            kept.insert(0, text)
        t.say = list(self.say_next)
        kept += [SAY_ANSWER.format(question=q, answer=a) for q, a in t.say]
        notes = "Kept for you:\n" + "".join(f"- {k}\n" for k in kept) + "\n" if kept else ""
        here = self._here(sorted(self.names.get(i, "someone") for i in self.present))
        state = self.state()
        tail = "\n".join(self.lines)
        room = max(0, CALL_SO_FAR - len(notes) - len(state))
        if len(tail) > room:
            tail = tail[len(tail) - room :].partition("\n")[2]
        return TURN.format(here=here, state=state, kept=notes, reading=READING_LINE if self.reading else "",
                           so_far=tail or "(nothing yet)", now=t.now)  # fmt: skip

    async def _turn(self, t: Turn) -> None:
        """One turn: the mind writes it, round by round of its tools, each
        whole sentence said as it comes (`_sentence`); what it does in the
        world is held until it commits (`_commit`). The answer to a typed
        line is posted whole."""
        reply, usage, began = t.reply, [], self.clock()
        self.replies[reply.id] = reply
        prompt = self.prompt(t)
        call_id = await self._open(t.id)
        try:
            if call_id is None:
                return
            contents: list = [{"role": "user", "parts": [{"text": prompt}]}]
            tools = [*TOOLS, *([BACKLOG] if self.sheet else [])]
            written, left, done = "", "", set()
            for n in range(MIND_ROUNDS):
                parts, calls, wrote = [], [], ""
                # The last round writes: Flash-Lite called `settle` round after round and said nothing (2026-11-25).
                last = n == MIND_ROUNDS - 1
                async for got in self.gemini.think(self.instruction, contents, tools, words_only=last):
                    if "usage" in got:
                        usage.append(got["usage"])
                        continue
                    parts.append(got["part"])
                    if "call" in got:
                        calls.append(got["call"])
                        continue
                    wrote += got["text"]
                    said, left = sentences(left + got["text"])
                    for sentence in said:
                        await self._sentence(t, sentence)
                written = f"{written} {wrote}" if written and wrote else written + wrote
                if left.strip():  # a round's end ends its sentence
                    await self._sentence(t, left.strip())
                left = ""
                results = []
                for (
                    name,
                    args,
                ) in calls:  # one call twice in a turn is done once (2026-11-25: `settle` 3 times)
                    key = (name, json.dumps(args, sort_keys=True))
                    results.append(
                        "done already: now write your reply"
                        if key in done
                        else await self._tool(t, name, args)
                    )
                    done.add(key)
                # Words written with only acts called need no round more: each one is a model call, 2-4 s
                # (the live tests, 2026-11-25).
                if not calls or (wrote.strip() and all(name in ACTS for name, _ in calls)):
                    break
                contents += [{"role": "model", "parts": parts}, {"role": "user", "parts": [
                    {"function_response": {"name": n, "response": {"result": r}}}
                    for (n, _), r in zip(calls, results, strict=True)]}]  # fmt: skip
            if left.strip():
                await self._sentence(t, left.strip())
            _log(
                f"call {self.id}: turn {t.id} written in {self.clock() - began:.1f} s: {written.strip()[:80]!r}"
            )
            await self._commit(t, spoken=False)  # a turn with no words commits as the mind ends
            # Posted by the mind's own `post`, its words are not posted again (twice in the live Meet test).
            if t.typed and written.strip() and not [n for n, _ in t.held if n == "post"]:
                self.posts.put_nowait(Post(t.id, written.strip(), True))
            if written.strip():
                self.prepare()  # an exchange: the worker updates the state (P-041)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001  a turn that fails says nothing, and the call goes on
            _log(f"call {self.id}: turn {t.id} failed: {''.join(traceback.format_exception(exc)).rstrip()}")
        finally:
            reply.generated = True
            if call_id is not None:
                await self._charge(
                    call_id, settings.call_mind_model, usage, len(self.instruction) + len(prompt)
                )
            if reply.said is None or t.dropped:
                await self._finish(reply, cut=t.dropped)
            elif reply.released and not reply.over.done():
                self._spawn(self._played_out(reply))

    async def _sentence(self, t: Turn, sentence: str) -> None:
        """One sentence in Valor's voice: its first sound commits the turn."""
        if t.typed:
            return
        t.reply.text = f"{t.reply.text} {sentence}".strip()
        async for got in self.gemini.speak(sentence):
            if isinstance(got, dict):
                await self._meter(settings.call_voice_model, got["usage"])
                continue
            if not t.committed:
                await self._commit(t, spoken=True)
            await self._audio(t.reply, got)

    async def _commit(self, t: Turn, *, spoken: bool) -> None:
        """The turn is said, or done with no words: once the reply before it
        is over, what it held is done, a decision first, so the state holds
        it before Valor speaks of it. An answer it opened with is given."""
        if t.committed:
            return
        while t.after is not None and not t.after.done():
            await asyncio.sleep(0.05)
        t.committed, self.answering = True, t.answering
        for name, args in sorted(t.held, key=lambda h: h[0] != "settle"):
            await self._do(t, name, args)
        if spoken and t.say:
            self.say_next = [a for a in self.say_next if a not in t.say]
            self.told += [SAID.format(question=q, answer=a) for q, a in t.say]

    async def _tool(self, t: Turn, name: str, args: dict[str, Any]) -> str:
        """A tool the mind called: a lookup now; `settle`, `ask`, `post` and
        `leave` acknowledged now and done when the turn commits (`_do`)."""
        _log(f"call {self.id}: turn {t.id} called {name}")
        text = {k: str(args.get(k) or "").strip() for k in ("question", "text", "readback", "about", "why")}
        if name == "ask" and text["question"]:
            key = self._key(text["question"])
            if key in self.asked:  # asked again (the live test, 2026-11-05): one worker turn, one post
                answer = self.asked[key]
                return f"already asked; {f'the answer: {answer}' if answer else 'the answer is coming'}"
            t.held.append((name, {"question": text["question"]}))
            result = ASKED
            if not await self.worker_ready():
                result += (
                    "; your worker's copy of the project is still being set up, so say it needs a moment"
                )
            return result
        if name == "post" and text["text"]:
            t.held.append((name, {"text": text["text"]}))
            return POSTING
        if name == "settle" and text["readback"]:
            ids = [str(i).strip() for i in args.get("ids") or [] if str(i).strip()]
            by = [str(n).strip() for n in args.get("by") or [] if str(n).strip()]
            if not by and len(self.present) == 1:  # a 1:1: the one other person decided
                by = [self.names.get(next(iter(self.present)), "someone")]
            settles, unmatched = await self._settles(ids)
            _log(f"call {self.id}: settle ids {ids!r} by {by!r}")
            t.held.append((name, {"readback": text["readback"], "by": by, "settles": settles}))
            if unmatched:
                return (f"recorded the read-back, but no open decision is {', '.join(unmatched)}: say in a "
                        "sentence that it is noted and not yet recorded against an open decision")  # fmt: skip
            return "recorded"
        if name == "leave":
            t.held.append((name, {"why": text["why"]}))
            return "leaving once your reply is said"
        if name == "backlog" and self.sheet:
            self.prepare()  # the call is moving through its queue
            return await self._backlog(text["about"])
        if name == "person" and text["about"]:
            return await self._person(text["about"])
        if name == "open_decisions":
            self.prepare()
            async with await self._connect() as conn:
                found = await decisions(conn, self.project)
            # Settled here, answered only once the call ends: never offered as open again (2026-11-25,
            # a question Tom had settled was raised again from this list).
            settled = {f"- {q['task_id']}/{q['question_id']}:" for d in self.decided for q in d["settles"]}
            return "\n".join(x for x in found.splitlines() if not x.startswith(tuple(settled)))
        return f"not done: {name} needs its arguments"

    async def _do(self, t: Turn, name: str, args: dict[str, Any]) -> None:
        """A held effect, as the turn commits."""
        if name == "ask":
            key = self._key(args["question"])
            if not t.typed:
                self.aloud.add(key)
            if key not in self.asked:
                self._ask(args["question"])
        elif name == "post":
            self.posts.put_nowait(Post(t.id, args["text"], True))
        elif name == "settle":
            day = self.started_at.astimezone().strftime("%Y-%m-%d")
            decision = {"decision": args["readback"], "by": args["by"], "at": _clock(self.now()),
                        "settles": args["settles"],
                        "source": f"Meet call {self.info['code']} {day}, read back at {_clock(self.now())}"}  # fmt: skip
            self.decided.append(decision)
            self._spawn(self._record(decision))
            self.posts.put_nowait(Post(t.id, args["readback"], True, args["settles"]))
            self.prepare()
        elif name == "leave":
            self._spawn(self._leave_after(t, LEAVES.get(args["why"], "Valor left the call")))

    async def _leave_after(self, t: Turn, reason: str) -> None:
        """Leave once the turn that said goodbye is over."""
        while not t.done():
            await asyncio.sleep(0.05)
        self.leave(reason)

    async def _say_unposted(self) -> None:
        """What the chat failed to post (every post of a call on the
        Captain, 2026-11-18, the recording notice among them), as one turn
        said aloud, once Valor is quiet, so it cuts no answer off."""
        if not self.unposted or self.out is not None or (self.turn is not None and not self.turn.done()):
            return
        text, self.unposted = " ".join(self.unposted), []
        self._answer(UNPOSTED.format(text=text), None)

    # -- speaking --

    async def _audio(self, reply: Reply, data: bytes) -> None:
        """A chunk of Valor's speech: the first requests the reply's
        `meet.say`; each is held until the say is released, and dropped when
        it is refused."""
        if reply.dropped:
            return
        reply.seconds += len(data) / 2 / OUT_RATE
        if reply.said is None:
            reply.over = asyncio.get_running_loop().create_future()
            reply.said = asyncio.create_task(
                self._effect("meet.say", {"turn": reply.id}, f"meet/{reply.id}/say")
            )
            reply.said.add_done_callback(lambda t: self._said(reply, t))
        if reply.released:
            await self._play(data, fresh=False)
        elif data:
            reply.held.append(data)

    def _said(self, reply: Reply, task: asyncio.Task) -> None:
        if task.cancelled() or task.exception() is not None or task.result().kind != "done":
            reply.dropped, reply.held = True, []
            if reply.over is not None and not reply.over.done():
                reply.over.set_result({})

    async def _play(self, data: bytes, *, fresh: bool) -> None:
        ahead = await self.page.stream(data, OUT_RATE, fresh)
        self.playing_until = self.clock() + float(ahead)
        pcm = np.frombuffer(data, "<i2").astype(np.float32) / 32768
        self.mine = np.concatenate([self.mine, Down()(pcm, OUT_RATE)])

    async def _played_out(self, reply: Reply) -> None:
        while self.clock() < self.playing_until and not reply.over.done():
            await asyncio.sleep(min(0.1, self.playing_until - self.clock()))
        await self._finish(reply, cut=False)

    async def _finish(self, reply: Reply, *, cut: bool) -> None:
        """The reply is over: played out, or cut (the person answered spoke
        over it). Its `meet.say` settles with what was said."""
        self.replies.pop(reply.id, None)
        if self.out is reply:
            self.out = None
        if reply.over is None or reply.over.done():
            return
        if not reply.released:
            played, spoken = 0.0, ""
        elif cut:
            played = float(await self.page.stop() or 0.0)
            self.mine = np.zeros(0, np.float32)
            spoken = _cut(reply.text, played / reply.seconds if reply.seconds else 0.0)
        else:
            played, spoken = reply.seconds, " ".join(reply.text.split())
        result = {"spoken_text": spoken, "cut": cut, "played_s": round(played, 2)}
        reply.over.set_result(result)
        if spoken:
            self.lines.append(f"[{_clock(self.now())}] Valor (spoke{', cut off' if cut else ''}): {spoken}")
            self.segments.append({"speaker": "Valor Engels", "start": round(reply.started or 0.0, 2),
                                  "end": round(self.now(), 2), "text": spoken, "channel": "mic"})  # fmt: skip

    async def _cut_off(self, reply: Reply) -> None:
        """The person Valor answers spoke over him: he stops, and the rest of
        the turn is not written or said."""
        reply.dropped, reply.held = True, []
        await self._finish(reply, cut=True)
        t = self.turn
        if t is not None and t.reply is reply and t.task is not None and not t.task.done():
            t.task.cancel()

    async def _say(self, payload: dict[str, Any]) -> dict[str, Any]:
        """`meet.say`, released: the reply's held audio plays, and the say
        settles once the reply is over."""
        reply = self.replies.get(payload.get("turn"))
        if reply is None or reply.over is None or reply.over.done():
            raise broker.Failed("stale: the turn was over before it was released")
        if self.out is not None:
            await self._finish(self.out, cut=True)
        reply.released, reply.started, self.out = True, self.now(), reply
        held, reply.held = reply.held, []
        await self._play(b"".join(held), fresh=True)
        if reply.generated:
            self._spawn(self._played_out(reply))
        result = await reply.over
        if not result:
            raise broker.Failed("the turn was dropped")
        return result

    async def _settles(self, ids: list[str]) -> tuple[list[dict[str, str]], list[str]]:
        """The open decisions `settle`'s ids name, and the ids that name
        none. `task/question` is taken as given (the session's answer refuses one no
        longer open); a bare id is the project's open question with that
        id, or the one open question of the task with that id (2026-11-25:
        the voice passed ids of another form and nothing was recorded)."""
        settles, bare = [], []
        for i in ids:
            t, slash, q = i.partition("/")
            if slash and t.strip() and q.strip():
                settles.append({"task_id": t.strip(), "question_id": q.strip()})
            else:
                bare.append(i)
        if not bare:
            return settles, []
        from core import board

        spec = board.board_spec(self.project)
        open_ = []
        if spec is not None:
            async with await self._connect() as conn:
                open_ = (await board.snapshot(conn, spec))["open"]
        unmatched = []
        for i in bare:
            hits = [q for q in open_ if q["question_id"] == i] or [q for q in open_ if q["task_id"] == i]
            if len(hits) == 1:
                settles.append({"task_id": hits[0]["task_id"], "question_id": hits[0]["question_id"]})
            else:
                unmatched.append(i)
        return settles, unmatched

    async def _person(self, about: str) -> str:
        """The dossiers of the call's project's people `about` fits
        (`memory.people`), as text; nobody, said so."""
        from core import memory

        spec = self._spec("no people to look up")
        if spec is None:
            return "this call has no project, so no one to look up"
        try:
            async with await self._connect() as conn:
                found = await memory.people(conn, spec, about)
        except memory.Unavailable as exc:
            return f"the people records could not be read: {exc}"
        return "\n\n".join(d["entry"] for d in found["people"]) or "nobody recorded fits that"

    async def _backlog(self, about: str) -> str:
        """`sheet.py lookup` on what was described, or the whole Queue in
        its order (`OVERVIEW`'s columns), as compact JSON; run apart from
        the call's loop, which keeps mixing and playing meanwhile."""
        argv = ["lookup", self.sheet, about] if about else ["read", self.sheet]
        run = await asyncio.create_subprocess_exec(
            sys.executable, str(SHEET_PY), *argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, **call_account(self.info).env},
        )  # fmt: skip
        out, err = await run.communicate()
        if run.returncode != 0:
            _log(f"call {self.id}: the backlog read failed: {err.decode().strip()}")
            return f"the Sheet could not be read: {err.decode().strip()[-300:]}"
        found = json.loads(out)
        return json.dumps(
            found if about else [{k: r.get(k, "") for k in OVERVIEW} for r in found], separators=(",", ":")
        )

    # -- metering --

    async def _open(self, turn_id: str, model: str | None = None, estimated_input: int = CALL_CONTEXT,
                    max_tokens: int = MIND_OUT_MOST) -> str | None:  # fmt: skip
        """Open a model call before its usage comes (`spending.open_call`);
        a stopped task refuses it, and the call leaves (None)."""
        from core import spending

        model = model or settings.call_mind_model
        price = spending.gemini_price(model)
        call = {"call_id": ledger.new_id(), "turn_id": turn_id, "model": model, "route": "gemini",
                "estimated_input": estimated_input, "max_tokens": max_tokens,
                "estimate_usd_micros": spending.gemini_worst_case(price, estimated_input, max_tokens),
                "unpriced": price["unpriced"]}  # fmt: skip
        try:
            async with await self._connect() as conn:
                return await spending.open_call(conn, self.id, call)
        except tasks.TaskStopped:
            self.leave("the call was stopped", hard=True)
            return None

    async def _charge(self, call_id: str, model: str, usage: list[dict[str, Any]], chars: int = 0) -> None:
        """A call's usage, each response's at the model's rates by modality;
        a turn dropped before any came back, its input (`chars`, about four
        a token) as text in."""
        from core import spending

        price = spending.gemini_price(model)
        cost = sum(spending.gemini_cost(u, price) for u in usage)
        if not usage:
            cost = spending.gemini_cost({"prompt_tokens_details": [{"modality": "TEXT", "token_count": chars // 4}],
                                         "prompt_token_count": chars // 4}, price)  # fmt: skip
        async with await self._connect() as conn:
            await spending.charge(conn, self.id, call_id, cost, {"model": model, "usage": usage})

    async def _meter(self, model: str, usage: dict[str, Any]) -> None:
        """The ears' or the voice's usage, as it comes."""
        if (call_id := await self._open(ledger.new_id(), model, 0, 0)) is not None:
            await self._charge(call_id, model, [usage])

    # -- posting --

    async def posts_lane(self) -> None:
        """Each post in the call's chat, then its copy to the thread it was
        asked in, which the thread's bridge sends while the next post goes."""
        while True:
            post = await self.posts.get()
            try:
                await self._send(post)
            finally:
                self.posts.task_done()

    async def _send(self, post: Post) -> None:
        from core import bridge

        turn = post.turn or ledger.new_id()
        done, failed = [], []
        for i, part in enumerate(bridge.split_text("meet", post.text)):
            payload = {"text": part, **({"settles": post.settles} if post.settles and i == 0 else {})}
            out = await self._effect("meet.send_message", payload, f"meet/{turn}/{i}/post")
            (done if out.kind == "done" else failed).append(part)
        if failed:
            self.unposted.append(" ".join(failed))
        elif post.answer and post.aloud:
            self.say_next.append(post.answer)
        elif post.answer:
            self.told.append(ANSWERED.format(question=post.answer[0], answer=post.answer[1]))
        if post.copy and done:
            kind, payload, chat = self._copy(" ".join(done))
            await self._effect(kind, payload, f"meet/{turn}/copy", chat)

    def _copy(self, text: str) -> tuple[str, dict[str, Any], str]:
        asked = self.info["asked_by"]
        payload = {"text": text, "reply_to": asked.get("reply_to")}
        if asked["channel"] == "telegram":
            payload["topic_id"] = asked.get("topic_id")
        return f"{asked['channel']}.send_message", payload, asked["chat_id"]

    async def _effect(
        self, action_type: str, payload: dict[str, Any], request_id: str, target: str | None = None
    ) -> broker.Outcome:
        """Request an effect on the call's session: a copy is handed to its
        bridge; a call's own is released here at once. A connection that
        drops is tried once more with the same request id, which the
        broker answers with the first effect."""
        action = broker.Action(action_type, target or self.info["code"], payload)
        for attempt in range(2):
            try:
                async with await self._connect() as conn:
                    out = await broker.request(conn, self.declared, self.id, action, request_id=request_id)
                    if out.kind == "released" and action_type in MEET_TYPES:
                        try:
                            out = await broker.release(conn, self.bound, out.effect_id)
                        except (broker.Refused, tasks.TaskStopped) as exc:
                            out = broker.Outcome(
                                out.effect_id, "refused", error=str(exc) or type(exc).__name__
                            )
                return out
            except psycopg.OperationalError:
                if attempt:
                    raise
        raise AssertionError("unreachable")

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not await self.page.post(payload["text"]):
            raise broker.Failed("the call's chat box was not there")
        self.lines.append(f"[{_clock(self.now())}] Valor (typed): {payload['text']}")
        return {"posted": payload["text"]}

    # -- the worker --

    async def work(self) -> None:
        """The worker's questions, one at a time; an answer is posted
        naming its question and noted to the session, or after leaving goes
        in the call's report. A call asked for in a chat reads up first.
        Between questions, the state updates `prepare` asks for."""
        if self.reader is not None:
            await self._read_up()
        self.prepare()
        while True:
            question = await self.questions.get()
            if question is PREP:
                if not self.questions.empty():  # a question asked meanwhile goes first
                    self.questions.put_nowait(PREP)
                    continue
                self.prep_queued = False
                if (question := await self._update_unless_asked()) is None:
                    continue
            self.asking.append(question)
            try:
                answer = await self.worker(question)
            except tasks.TaskStopped:
                answer = None
            except Exception as exc:  # noqa: BLE001  a worker that fails leaves its question unanswered
                _log(f"call {self.id}: the worker failed on {question!r}: {exc!r}")
                answer = None
            finally:
                self.asking.remove(question)
            if not answer:
                self.dropped.append(question)
                self.asked.pop(self._key(question), None)  # may be asked again
            elif self.left:
                self.late.append({"question": question, "answer": answer})
            else:
                key = self._key(question)
                self.asked[key] = answer
                # Kept for every turn once posted; asked aloud, given when Valor next speaks.
                self.posts.put_nowait(Post(None, f'On "{question}": {answer}', True, answer=(question, answer),
                                           aloud=key in self.aloud))  # fmt: skip

    async def _update_unless_asked(self) -> str | None:
        """An update of the state, unless a question comes while it runs:
        then the update is stopped and asked again after it, and the
        question is returned, so a lookup never waits on an update."""
        update, again, taken = asyncio.create_task(self._update()), False, False
        try:
            while not update.done():
                got = asyncio.create_task(self.questions.get())
                await asyncio.wait({update, got}, return_when=asyncio.FIRST_COMPLETED)
                if not got.done():
                    got.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await got
                elif (question := got.result()) is PREP:
                    taken = True  # an update asked meanwhile, taken off the queue: it runs after this one
                else:
                    again = again or not update.done()
                    return question
            return None
        finally:
            if not update.done():
                update.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await update
            # One PREP queued at most: `work` puts a PREP back while the queue is not empty, so two
            # would spin it forever (review, 2026-11-25). Only the one taken here is no longer queued.
            if taken:
                self.prep_queued = False
            if again or taken:
                self.prepare()

    async def _read_up(self) -> None:
        """The worker's reading for a call asked in a chat, done while Valor
        joins (P-033): the state's first topics, read by the next turn;
        never posted, and never added to the notes before the call, where it
        would sit stale beside the state."""
        try:
            notes = (await self.reader()).strip()
        except tasks.TaskStopped:
            notes = ""
        except Exception as exc:  # noqa: BLE001  a reading that fails leaves the chat's notes
            _log(f"call {self.id}: the worker's reading failed: {exc!r}")
            notes = ""
        self.topics = notes or f"{self.info.get('prep') or ''}\n{READ_NONE}".strip()
        self.reading = False

    def prepare(self) -> None:
        """Ask for an update of the state: after the reading (or as an
        invited call starts), after each of Valor's turns that spoke, and
        each time the voice moves through the queue (`settle`, `backlog`,
        `open_decisions`); never on a timer. Asks while one waits are one."""
        if self.preparer is not None and not self.prep_queued:
            self.prep_queued = True
            self.questions.put_nowait(PREP)

    def state(self) -> str:
        """The meeting now, as the voice is told it: what the call decided,
        then the worker's topics, or the notes before the call until there
        are any (a settle before the first update left the voice with none)."""
        decided = [
            f"- {d['decision']}" + (f" ({', '.join(d['by'])}, {d['at']})" if d["by"] else f" ({d['at']})")
            for d in self.decided
        ]
        return STATE.format(
            decided="\n".join(decided) or "(nothing yet)",
            topics=self.topics or self.info.get("prep") or "(nothing yet)",
        )

    async def _update(self) -> None:
        """The worker's state of the meeting (P-041): the topics rewritten
        from the decisions, the state now, the notes before the call and
        the call so far, then shown; never posted. "no change" changes
        nothing, and an update written before a settle is dropped: the
        settle asked for another (2026-11-25: notes written before Tom's no
        relisted the item, and it was asked again)."""
        if self.left:
            return
        decided = len(self.decided)
        prompt = UPDATE.format(
            decided="\n".join(f"- {d['decision']}" for d in self.decided) or "(nothing yet)",
            topics=self.topics or "(nothing yet)",
            prep=self.info.get("prep") or "(none)",
            so_far="\n".join(self.lines[-40:]) or "(nothing yet)",
        )
        began = time.monotonic()
        try:
            topics = (await self.preparer(prompt)).strip()
        except tasks.TaskStopped:
            return
        except Exception as exc:  # noqa: BLE001  an update that fails leaves the state as it was
            _log(f"call {self.id}: the worker's update failed: {exc!r}")
            return
        _log(f"call {self.id}: the worker's update took {time.monotonic() - began:.1f} s")
        if len(self.decided) != decided or self.left or not topics or "no change" in topics.lower()[:40]:
            return
        self.topics = topics

    async def _record(self, decision: dict[str, Any]) -> None:
        """A decision as a fact of the project the moment it is made, with
        the open decisions it settles; its name kept on it. One that fails
        is written again at the end (`_decisions`), with the same source, so
        a write cut short by the call's end is the same fact."""
        async with await self._connect() as conn:
            await self._decisions(conn, [decision], decision["source"])

    # -- the call's life --

    def leave(self, reason: str, *, hard: bool = False) -> None:
        if not self.leaving.is_set():
            self.reason, self.hard = reason, hard
            self.leaving.set()

    def _lane(self, coro) -> asyncio.Task:
        """One of the call's lanes. A lane that fails ends the call, saying
        why: the mixer stopping went unseen, and Valor heard nothing for the
        rest of a call (the live test, 2026-11-05)."""
        lane = asyncio.create_task(coro)

        def ended(task: asyncio.Task) -> None:
            if not task.cancelled() and (exc := task.exception()) is not None:
                _log(f"call {self.id}: {''.join(traceback.format_exception(exc)).rstrip()}")
                self.leave(f"Valor's call process failed: {exc!r}")

        lane.add_done_callback(ended)
        return lane

    def _spawn(self, coro) -> None:
        job = asyncio.create_task(coro)
        self.jobs.add(job)
        job.add_done_callback(self.jobs.discard)

    async def watch(self) -> None:
        """Who is in the call, and the reasons to leave it."""
        start, alone, down, seen, inside = self.clock(), None, None, set(), self.clock()
        while not self.leaving.is_set():
            state = await self.page.state()
            if state["status"] in ("removed", "denied", "ended"):
                self.leave({"removed": "Valor was removed from the call", "denied": "Valor was not let in",
                            "ended": "the call ended"}[state["status"]])  # fmt: skip
                break
            if state["status"] == "in":
                inside = self.clock()
            elif self.clock() - inside >= SOCKET_S:
                self.leave("Valor was no longer in the call")
                break
            others = [p for p in state["people"] if not p["me"]]
            self.present = {p["id"] for p in others}
            mine = next((p["name"] for p in state["people"] if p["me"]), None)
            for p in state["people"]:
                if p["me"]:
                    self.me = p["id"]
                    continue
                # Another device of Valor's own account (the live tests) is not Valor.
                self.names[p["id"]] = (p["name"] or "someone") + (
                    " (another device)" if p["name"] == mine else ""
                )
                if p["id"] not in seen:
                    seen.add(p["id"])
                    first = (p["name"] or "").split()[:1]
                    if first and p["name"] != mine and first[0] not in self.first_names:
                        self.first_names += first
                    async with await self._connect() as conn:
                        await ledger.append(
                            conn, self.id, "call.participant", {"id": p["id"], "name": p["name"]}
                        )
            now = self.clock()
            alone = None if others else alone or now
            if seen and alone is not None and now - alone >= EMPTY_S:
                self.leave("everyone else had left")
            elif not seen and now - start >= ALONE_S + self.early:
                self.leave("no one else came")
            up = self.socket is None or self.socket.up
            down = None if up else down or now
            if down is not None and now - down >= SOCKET_S:
                self.leave("the call's audio was lost")
            await asyncio.sleep(STATE_S)

    @staticmethod
    def _here(names: list[str]) -> str:
        """Who is in the call. With one other person, whatever they say is
        to Valor (Tom's "Hello", unanswered, 2026-11-05); with more, the next
        item waits to be asked for (a settled item's reply opened it, live
        test, 2026-11-17), and a question not naming Valor is not to him
        (the persona's line alone, it took one, 3 of 3, 2026-11-18)."""
        if len(names) == 1:
            return f"Only {names[0]} is here with you: everything they say is to you, so answer it."
        if len(names) > 1:  # a group call: Valor does not lead it (Tom, 2026-11-17)
            return (
                f"In the call with you: {', '.join(names)}. Open the next item only when someone asks for it. "
                "Answer only what names you or follows straight on from your answer; a question without your "
                "name is to the person they were talking with."
            )
        return f"In the call with you: {', '.join(names) or 'no one yet'}."

    async def orders(self) -> None:
        """A stop of the session leaves at once; a steer is posted in the
        call's chat and kept for the mind's turns. A listening session the database dropped (a restart of
        the server, `core/health.py`) is listened again on a new one, the
        call's locks taken again (`db.Held`), and a stop or steer written
        meanwhile caught up: the call stays (2026-11-13, the Cowboy's
        restart ended a call through this lane, and the kernel's sweep,
        finding its lock free, ended it with no reason)."""
        channels = ["valor_events", tasks.STOP_CHANNEL]
        listener = await db.listen(self.dsn, channels)
        try:
            async with await self._connect() as conn:
                if await tasks.is_stopped(conn, self.id):
                    self.leave("the call was stopped", hard=True)
                    return
                row = await (
                    await conn.execute("SELECT max(id) FROM events WHERE task_id = %s", (self.id,))
                ).fetchone()
            seen = row[0] or 0  # steers up to here came before the call
            while True:
                try:
                    async for got in listener.notifies():
                        if got.channel == tasks.STOP_CHANNEL and got.payload == self.id:
                            self.leave("the call was stopped", hard=True)
                            return
                        said = json.loads(got.payload) if got.channel == "valor_events" else {}
                        if said.get("task_id") == self.id and said.get("type") == "message.steered":
                            seen = await self._steers(seen, said["id"])
                except psycopg.OperationalError:
                    pass
                await listener.close()
                listener = await db.listen(self.dsn, channels)
                if self.held is not None and not await self.held.alive():
                    self.leave("another process took over the call")
                    return
                async with await self._connect() as conn:
                    if await tasks.is_stopped(conn, self.id):
                        self.leave("the call was stopped", hard=True)
                        return
                    ended = await (
                        await conn.execute(
                            "SELECT 1 FROM events WHERE task_id = %s AND type = 'call.ended'", (self.id,)
                        )
                    ).fetchone()
                if ended is not None:  # the kernel's sweep ended it while the database was down
                    self.leave("the call was ended while the database was down")
                    return
                seen = await self._steers(seen)
        finally:
            await listener.close()

    @staticmethod
    def _key(question: str) -> str:
        """A question by its words, so one asked twice is one worker turn."""
        return " ".join(re.findall(r"\w+", question.lower()))

    def _ask(self, question: str) -> None:
        self.asked[self._key(question)] = None
        self.questions.put_nowait(question)

    async def _steers(self, seen: int, upto: int | None = None) -> int:
        """Post each steer of the call after row `seen` (up to `upto`), and
        keep it for the mind's turns; one opening `LOOK_UP` goes to the worker instead,
        never posted. The last row read."""
        async with await self._connect() as conn:
            rows = await (
                await conn.execute(
                    "SELECT id, payload->>'text' FROM events WHERE task_id = %s AND type = 'message.steered' "
                    "AND id > %s AND id <= coalesce(%s, id) ORDER BY id",
                    (self.id, seen, upto),
                )
            ).fetchall()
        for row_id, text in rows:
            question = text[len(LOOK_UP) :].strip() if text and text.lower().startswith(LOOK_UP) else ""
            if question:  # the call's worker looks it up; its answer is posted and said
                self.aloud.add(self._key(question))
                if self._key(question) not in self.asked:
                    self._ask(question)
            elif text:
                self.posts.put_nowait(Post(None, text, False))
                self.told.append(STEERED.format(text=text))
            seen = max(seen, row_id)
        return max(seen, upto or 0)

    async def _connect(self) -> psycopg.AsyncConnection:
        """A connection, waiting out a database that refuses them for up to
        `DB_WAIT_S` (a restart of the server took 61 s, 2026-11-13). The
        lane that waits stalls meanwhile (the transcriber's receive loop: no line is
        recorded or spoken), by intent; a database down for longer ends the
        call, as before."""
        waited = 0.0
        while True:
            try:
                return await db.connect(self.dsn)
            except psycopg.OperationalError:
                if waited >= DB_WAIT_S:
                    raise
                await asyncio.sleep(db.RELISTEN_S)
                waited += db.RELISTEN_S

    async def joined(self) -> str | None:
        """Through Meet's join screen and lobby; None once in, else why not.
        A profile Google signed out is signed in first, and a join Google's
        sign-in stops is tried once more after it, both by Valor itself when
        its login is in 1Password (`Page.renew`)."""
        from bridges.meet import Refused, SignInNeeded

        url = self.info["url"]
        who = call_account(self.info)
        again = (
            f"Google wants Valor's sign-in as {who.email} on {settings.machine} verified again; at that Mac "
            f"run: {who.sign_in}"
        )
        if not signed_in_as(self.page.profile, who.email) and not await self.page.renew(url):
            return again
        for renewed in (False, True):
            try:
                await self.page.join()
                break
            except SignInNeeded as exc:
                _log(f"call {self.id}: Meet stayed on Google's sign-in at {exc}")
                if renewed or not await self.page.renew(url):
                    return again
                _log(f"call {self.id}: signed in to Google again from 1Password")
            except Refused as exc:
                _log(f"call {self.id}: Meet refused Valor, signed in, at {exc}")
                return "Meet would not let Valor in"
        start = self.clock()
        while True:
            status = (await self.page.state())["status"]
            if status == "in":
                return None
            if status in ("removed", "denied", "ended"):
                return "Valor was not let in" if status != "ended" else "the call had ended"
            waited = self.clock() - start
            if status == "lobby" and waited >= LOBBY_S + self.early:
                return "no one let Valor in"
            if status not in ("lobby",) and waited >= JOIN_S:
                return "Valor could not get through Meet's join screen"
            if self.leaving.is_set():
                return self.reason
            await asyncio.sleep(1)

    # -- after the call: the recording, its summary, the store --

    async def summarise(self) -> dict[str, str] | None:
        """The call kept as Yudame Tools keeps a meeting, with Gemini's
        notes on it: the recording, its transcript and the notes staged
        apart and moved into the store whole, and the summary document
        beside them. None when nothing was heard or the call was stopped;
        `{"error"}` when Gemini failed, and no other model writes one."""
        if self.recording is None:
            return None
        self.recording.close()
        staging, wav = self.staging, self.recording.path
        if self.hard or not self.lines:
            shutil.rmtree(staging, ignore_errors=True)
            return None
        from core import spending

        audio = staging / "audio.m4a"
        try:
            code = await (await asyncio.create_subprocess_exec(
                "afconvert", "-f", "m4af", "-d", "aac", str(wav), str(audio))).wait()  # fmt: skip
            if code != 0:
                raise RuntimeError(f"afconvert exited {code}; the recording is kept as {wav.name}")
            wav.unlink()
            transcript = "\n".join(self.lines)
            price = spending.gemini_price(settings.summary_model)
            est = int(self.recording.seconds * 32) + len(transcript) // 3 + 1000
            call_id = ledger.new_id()
            async with await self._connect() as conn:
                await spending.open_call(conn, self.id, {
                    "call_id": call_id, "turn_id": "summary", "model": settings.summary_model,
                    "route": "gemini", "estimated_input": est, "max_tokens": 16_000,
                    "estimate_usd_micros": spending.gemini_worst_case(price, est, 16_000),
                    "unpriced": price["unpriced"]})  # fmt: skip
            recorded = "\n".join(f"{n}. {d['decision']}" for n, d in enumerate(self.decided, 1))
            notes, usage = await self.gemini.summarise(
                audio, transcript, self.info.get("prep") or "", recorded
            )
            async with await self._connect() as conn:
                await spending.charge(conn, self.id, call_id, spending.gemini_cost(usage, price),
                                      {"model": settings.summary_model, "usage": usage})  # fmt: skip
        except Exception as exc:  # noqa: BLE001  the report says the summary failed
            _log(f"call {self.id}: the summary failed: {''.join(traceback.format_exception(exc)).rstrip()}")
            # Kept beside the store, not in it: the app reads only the folders it can.
            return {
                "error": f"{type(exc).__name__}: {str(exc)[:300]}; the call's files are kept in {staging}"
            }
        title, code = self._title(notes.get("topic")), self.info["code"]
        day = self.started_at.astimezone().strftime("%Y-%m-%d")
        repo, links = self._links()
        segments = sorted(self.segments, key=lambda s: s["start"])
        # Linked in summary.json, which the app rewrites notes.md from; its
        # decisions and actions it escapes, so they stay as they are.
        # What the call recorded as it went, then the summary's others: one restating a recorded one is left out.
        decided = [
            *self.decided,
            *(d for d in notes["decisions"] if d["decision"].strip() and not d.get("restates")),
        ]
        summary = {"summary": linked(notes["summary"], repo, links), "decisions": [d["decision"] for d in decided],
                   "actions": [{k: v for k, v in a.items() if k in ("task", "owner") and v} for a in notes["actions"]]}  # fmt: skip
        files = {
            "meta.json": meta_json(title, self.started_at, datetime.now(UTC), _zone(), segments),
            "transcript.json": json.dumps(segments, indent=2, sort_keys=True),
            "transcript.md": transcript_md(title, segments),
            "summary.json": json.dumps(summary),
            "notes.md": notes_md(title, summary),
        }
        doc = re.sub(r"\A\s*# [^\n]*\n?", "", _unescaped(notes["markdown"]))
        files[f"{day} {title}.md"] = (
            f"# {day} {title}\n\nGoogle Meet {code}\n\n{linked(doc.strip(), repo, links)}\n"
        )
        for name, text in files.items():
            (staging / name).write_text(text)
        folder = keep(staging, self.store, folder_name(title, self.started_at))
        async with await self._connect() as conn:
            facts = await self._decisions(conn, decided, f"Meet call {code} {day}, {title}")
        return {"path": str(folder / f"{day} {title}.md"), "actions": summary["actions"], "title": title,
                "notes": "\n".join([summary["summary"], *summary["decisions"]]), "decided": facts}  # fmt: skip

    async def _decisions(self, conn, decided: list[dict[str, Any]], source: str) -> list[str]:
        """Each decision the call made as a dated fact of its project (kind
        `decision`, citing the call and the open decisions it settles), about
        those who made it whom the project knows as people, its fact's name
        kept on it; one already written (`fact`) is not written again. The
        report's lines on them."""
        from core import memory

        if not decided:
            return []
        spec, todo = (
            self._spec("its decisions are not recorded as facts"),
            [d for d in decided if not d.get("fact")],
        )
        try:
            if spec is None:
                raise memory.Unavailable("the call has no project spec")
            writes = []
            for d in todo:
                by = [n.strip() for n in d.get("by") or [] if n.strip()]
                settles = ", ".join(f"{q['task_id']}/{q['question_id']}" for q in d.get("settles") or [])
                claim = d["decision"] + (f" (decided by {', '.join(by)})" if by else "")
                claim += f" (settles {settles})" if settles else ""
                writes.append({"op": "fact", "kind": "decision", "claim": claim, "source": d.get("source") or source,
                               "since": self.started_at.isoformat(), "about": await memory.known(conn, spec, by)})  # fmt: skip
            names = (
                await memory.fact_write(conn, spec, writes, via=f"Meet call, task {self.id}")
                if writes
                else {}
            )
        except (memory.Unavailable, memory.Refused) as exc:
            _log(f"call {self.id}: its decisions are not recorded as facts: {exc}")
            head = f"Decided in the call, not recorded as facts ({exc}); record each with `valor-record fact add --kind decision`:"
            done = [f"- {d['decision']} (fact {d['fact']})" for d in decided if d.get("fact")]
            return [head, *(f"- {d['decision']}" for d in todo), *done]
        for n, d in enumerate(todo, 1):
            d["fact"] = names[str(n)]
        head = (
            "Decided in the call, now facts of the project (kind decision): carry each that changes a backlog "
            "item or an open question to where it lives (its Sheet row, the task waiting on it); an open "
            "question Tom settled is answered as the lines on his read-backs say:"
        )
        return [head, *(f"- {d['decision']} (fact {d['fact']})" for d in decided)]

    def _title(self, topic: str | None) -> str:
        """The call's topic, then the first names of those in it, Valor's last."""
        mine = persona.identity(settings.persona_dir)["name"].split()[0]
        return title(topic, [n for n in self.first_names if n != mine] + [mine])

    def _spec(self, without: str) -> workspace.Spec | None:
        """The call's project's spec, read now; None, logging `without`,
        when it has no project or its spec does not load."""
        try:
            return workspace.Spec.load(self.project) if self.project else None
        except workspace.Refused as exc:
            _log(f"call {self.id}: {without}: {exc}")
            return None

    def _links(self) -> tuple[str | None, dict[str, str]]:
        """The call's project's repository and reference links, read now; none
        when it has no project or its spec does not load."""
        spec = self._spec("no links in the summary")
        return (spec.repo, spec.links) if spec else (None, {})

    async def run(self) -> str:
        """The call, from joining to its end; the reason it ended."""
        lanes = []
        try:
            lanes = [self._lane(c) for c in (self.orders(), self.posts_lane(), self.work())]
            await self.page.open(self.info["url"], self.socket.port if self.socket else 0)
            why = await self.joined()
            if why is not None:
                self.leave(why)
            else:
                async with await db.connect(self.dsn) as conn, conn.transaction():
                    await entered(conn, self.id)
                if not self.info.get("starts"):  # an invited call's own post has its link
                    kind, payload, chat = self._copy(f"I'm in: {self.info['url']}")
                    await self._effect(kind, payload, "meet/link/copy", chat)
                await self.page.ready()
                self.store.parent.mkdir(parents=True, exist_ok=True)
                self.staging = Path(tempfile.mkdtemp(prefix=".valor-call-", dir=self.store.parent))
                self.recording = Recording(self.staging / "call.wav")
                self.started_at, self.t0 = datetime.now(UTC), self.clock()
                self.posts.put_nowait(Post("join", JOIN_LINE, False))
                spec = self._spec("no backlog Sheet")
                self.sheet = next(
                    (m[1] for t in (spec.rooms.values() if spec else []) if (m := SHEET_URL.search(t))), None
                )
                self.instruction = system(self.project or "", bool(self.sheet))
                lanes += [self._lane(c) for c in (self.listen(), self.mix(), self.watch())]
                await self.leaving.wait()
        except Exception as exc:  # noqa: BLE001  the call ends, saying why
            _log(f"call {self.id}: {''.join(traceback.format_exception(exc)).rstrip()}")
            self.leave(f"Valor's call process failed: {exc!r}")
        if not self.hard:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.posts.join(), 10)  # the line saying why, before leaving
        self.left = True
        with contextlib.suppress(Exception):
            await self.page.leave()
        if not self.hard:
            waited = self.clock()
            while (self.asking or not self.questions.empty()) and self.clock() - waited < WORKER_WAIT_S:
                await asyncio.sleep(0.2)
        unanswered = [*self.asking, *self.dropped]
        while not self.questions.empty():
            if (q := self.questions.get_nowait()) is not PREP:
                unanswered.append(q)
        jobs = [*lanes, *self.jobs]
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        summary = await self.summarise()
        async with await self._connect() as conn:
            if self.decided and not (summary or {}).get(
                "path"
            ):  # no summary: what the call recorded still reports
                summary = {**(summary or {}), "decided": await self._decisions(conn, self.decided, "")}
            await end(conn, self.id, self.reason, late=self.late, unanswered=unanswered, summary=summary)
        return self.reason


def _unescaped(markdown: str) -> str:
    """The document as Markdown: Gemini sent one with its newlines escaped
    inside the JSON string, `\\n` throughout (the live test, 2026-11-05)."""
    return markdown.replace("\\n", "\n") if "\n" not in markdown else markdown


def _zone() -> str:
    """The Mac's time zone's name, as Foundation's `TimeZone.current` gives it."""
    with contextlib.suppress(OSError):
        return os.readlink("/etc/localtime").split("zoneinfo/", 1)[1]
    return "GMT"


def _log(text: str) -> None:
    print(f"{datetime.now(UTC).isoformat(timespec='seconds')} {text}", flush=True)
