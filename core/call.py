"""A Google Meet call (docs/plans/meet.md): its session, its end, and the
process that is Valor in it.

**The call session.** `meet.join` (`tools/meet.py`), requested by a group
chat's session, opens one (`begin`): a root chat session on channel `meet`,
its chat id the meeting code, under the asking chat's project and at its
ceiling, so its worker has that chat's access (`tasks.dispatch` gives it
the asking chat's room). Its `task.started` carries `call`: the link,
this Mac, the chat and thread it was asked in (`asked_by`), and `prep`,
the asking turn's notes on the topic. It runs no turn (`router.has_step`)
and owes no report of its own (`notices.owe`); everything it says goes to the asking
chat's session as a `child.reported` row of kind `call` (`report`): first
the link, which that session posts in the thread, and at the end what the
call settled. A steer of it is posted in the call's chat while it is
open; after `call.ended` it is refused (`tools/chat_tasks.py`).

**The end** (`end`), written once, in one transaction: `call.ended` (with
the worker answers that came after leaving and the questions left
unanswered), `task.ended` unless the session was stopped, the decisions
the call settled, `effect.refused` for each `meet.say` or
`meet.send_message` never delivered (a thread copy still goes), and the
report. Meet names people by display name only, which anyone with an open Meet's link can set, so Tom's read-backs (a
post's `settles`) go to his Slack DM's session,
which posts them and answers each on his 👍 with its question id
(`confirms`), refused unless it is still the task's open question. The call's own process writes the end; a kernel
wake writes it for a call whose process died (`sweep`).

**The call's process** (`python -m core call SESSION`, `run`) holds
`meet:call:<session>` while it lives, and `run:<session>`, so the kernel
charges none of its model calls twice. It joins through the page
(`bridges/meet`), mixes what everyone says into one Gemini Live session
(docs/plans/gemini-live.md), which decides when Valor is addressed and
speaks, posts and asks its worker through tools, and speaks and posts
through the broker on its own session. After the call Gemini summarises
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

from core import broker, db, intake, ledger, persona, tasks, workspace
from core.settings import CALL_CONTEXT, CALL_OUTPUT_MOST, resolve_seat, settings

CHANNEL = "meet"
# A call whose process never wrote `call.started` this long after it was
# opened, with its lock free, never started.
NEVER_STARTED_S = 90
SIGN_IN = "cd ~/src/ai && .venv/bin/python -m core meet-sign-in"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


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


def profile_ready(profile: str | None = None) -> bool:
    """Whether this Mac's call profile is signed in to a Google account."""
    return account(profile) is not None


def sign_in(profile: str | None = None, *, chrome: str = CHROME, poll_s: float = 2.0, say=print) -> int:
    """`python -m core meet-sign-in`, by Tom at the Mac: Chrome on the call
    profile at Google's sign-in, ended by this once the profile is signed
    in (the Captain, 2026-11-03). A Chrome already holding it is left
    alone: a call's is named, any other is the window to sign in from,
    which may be left open, since the next call quits it. The answer is
    read from the profile whenever Chrome exits; 0 when signed in."""
    from bridges.meet import a_call, holder

    profile = profile or settings.meet_profile
    if email := account(profile):
        say(f"already signed in as {email}; {settings.machine} can join calls")
        return 0
    if (pid := holder(profile)) is not None:
        if a_call(pid):
            say(f"the call profile is in use by a call's Chrome (pid {pid}); run this again after the call")
        else:
            say(f"the call profile is already open in Chrome (pid {pid}): sign in as valor@yuda.me there; "
                "the window may be left open, the next call quits it")  # fmt: skip
        return 1
    Path(profile).mkdir(parents=True, exist_ok=True)
    child = subprocess.Popen(
        [chrome, f"--user-data-dir={profile}", "--no-first-run", "https://accounts.google.com/"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    say(
        "sign in as valor@yuda.me in the Chrome window; it closes itself once signed in, and if it is left "
        "open the next call quits it (⌘Q or Ctrl-C to give up)"
    )
    try:
        while child.poll() is None and not profile_ready(profile):
            time.sleep(poll_s)
    except KeyboardInterrupt:
        pass
    finally:
        if child.poll() is None:
            child.terminate()  # a clean exit, which saves the cookies
        child.wait()
    if email := account(profile):
        say(f"signed in as {email}; {settings.machine} can join calls")
        return 0
    say(f"not signed in; run this again to sign in: {SIGN_IN}")
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
) -> str:
    """Start the call session and write its first report, the link, inside
    the caller's transaction. Returns the session's id."""
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
    await tasks.start(conn, brief, marker={"sdlc": 1, "call": call, "effect_key": effect_key}, by="valor",
                      via="meet.join")  # fmt: skip
    where = f"reply_to {reply_to}" + (f", topic_id {topic_id}" if topic_id else "")
    await report(
        conn,
        brief.id,
        "link",
        f"The Meet you asked for is open: {url}\nI am joining it now (call session {brief.id}). Post the "
        f"link in the thread it was asked in ({where}), one line; what the call writes is copied there.",
    )
    return brief.id


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
    summary: dict[str, str] | None = None,
) -> bool:
    """Write the call's end once (module docstring). False when it had
    already ended. `summary` is Gemini's summary document (`path`) or why
    it failed (`error`); None when none was made."""
    async with conn.transaction():
        await ledger.lock(conn, f"task:{session_id}")
        rows = await ledger.read(conn, session_id)
        if any(r["type"] == "call.ended" for r in rows):
            return False
        stopped = await tasks.is_stopped(conn, session_id)
        late, unanswered = list(late or []), list(unanswered or [])
        await ledger.append(conn, session_id, "call.ended", {"reason": reason, "stopped": stopped, "late": late,
                            "unanswered": unanswered})  # fmt: skip
        if not stopped:
            await ledger.append(
                conn, session_id, "task.ended", {"kind": "call", "by": "valor", "reason": reason}
            )
        settled = await _settle(conn, session_id, rows)
        dropped = await _leftovers(conn, session_id, rows, stopped)
        call = await marker(conn, session_id) or {}
        lines = [f"The call {call.get('url')} ended: {reason}."]
        code, thread = call.get("code"), f"reply_to {(call.get('asked_by') or {}).get('reply_to')}"
        if summary and summary.get("path"):
            lines.append(
                f"Gemini's summary of the call is {summary['path']}. Copy it into your workspace under its "
                f"own name and send it, unchanged, in the thread it was asked in ({thread}) as the "
                "send's `files`, with at most two lines of text beside it. The recording, transcript and "
                "notes are in Yudame Tools' meetings on this Mac, which back them up to Drive."
            )
        else:
            why = f"Gemini could not summarise it ({summary['error']})" if summary else "no summary was made"
            lines.append(
                f"{why}: say so in the thread it was asked in ({thread}), in one line; write no summary of "
                f"your own. Its transcript: `valor-record call {code}`."
            )
        lines += settled
        lines += [
            f'Answered by your worker after the call: on "{a["question"]}": {a["answer"]}' for a in late
        ]
        lines += [f"Asked of your worker and never answered: {q}" for q in unanswered]
        lines += dropped
        await report(conn, session_id, "ended", "\n".join(lines))
    return True


async def _settle(conn, session_id: str, rows: list[dict[str, Any]]) -> list[str]:
    """Answer the questions Tom's read-backs settle (module docstring);
    each answer and refusal as a line of the report."""
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
        return []
    return await _confirm(conn, session_id, readbacks)


async def _confirm(conn, session_id: str, readbacks: dict[tuple[str, str], str]) -> list[str]:
    """Meet gives display names only, which anyone with an open Meet's link
    can set, so no word in a call is proven Tom's: the read-backs go to his
    Slack DM's session, which asks his 👍 on each (Tom, 2026-10-27: by
    account, else by 👍 in his DM)."""
    dm = await intake.operator_session(conn) if settings.operator_channel == "slack" else None
    if dm is None:
        return [
            f"Not answered: Tom has no Slack DM here to confirm by 👍: {t} ({text})"
            for (t, _), text in readbacks.items()
        ]
    confirm = [{"task_id": t, "question_id": q, "text": text} for (t, q), text in readbacks.items()]
    head = (
        "Tom settled these in a Meet call, which shows display names only. Post each to him here and, on his 👍 on its post, "
        "answer it with `task.answer` (via `meet call`, its `question_id`):"
    )
    text = "\n".join(
        [head, *(f"- task {c['task_id']}, question {c['question_id']}: {c['text']}" for c in confirm)]
    )
    channel, chat_id = settings.operator_channel, settings.operator_chat
    project = (await tasks.brief(conn, session_id)).project
    await to_session(
        conn, channel, chat_id, f"call:{session_id}:confirm", text, project=project, confirm=confirm
    )
    return [f"Sent to Tom's Slack DM to confirm by 👍: task {c['task_id']}: {c['text']}" for c in confirm]


async def confirms(conn, dm_session: str, task_id: str) -> bool:
    """Whether a call sent `task_id`'s read-back to this session to confirm."""
    row = await (
        await conn.execute(
            "SELECT 1 FROM events WHERE task_id = %s AND type = 'child.reported' AND payload->>'kind' = 'call' "
            "AND payload->'confirm' @> %s::jsonb",
            (dm_session, json.dumps([{"task_id": task_id}])),
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


# -- hearing: everyone's audio, mixed for Gemini Live ---------------------------

RATE = 16_000  # what Live hears
OUT_RATE = 24_000  # what Live speaks
MIX_S = 0.04  # the mix is sent this often
BACKLOG_S = 1.0  # a person's audio kept ahead of the mix at most
SPEAKING = 0.01  # a person's RMS above this is speech, for who is speaking
CHANGE_S = 0.3  # someone else this long the loudest is a change of speaker
LAG_S = 0.8  # Live's transcript of a stretch lags its audio by up to this
QUIET_S = 1.5  # this long with no one speaking ends a stretch of the transcript
# Meet sends 48 kHz: a low-pass at 7.2 kHz, then every third sample.
LOWPASS = (np.hamming(49) * np.sinc((np.arange(49) - 24) * 2 * 7200 / 48_000)).astype(np.float32)
LOWPASS /= LOWPASS.sum()


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


# -- answering: one Gemini Live session per call ----------------------------------

INSTRUCTIONS = (
    "You are {name}, in a live Google Meet call as yourself, on the {project} project. You are an AI "
    "coworker; everyone in the call knows. You hear everyone in the call in one stream.\n\n"
    "- Speak only when someone addresses you (by name, or by asking you something directly), or asks what only "
    "you would know. Otherwise stay silent: talk between the others is not to you, even about the work, money "
    "or something you know, and you never answer or comment on it. When a note "
    "says only one person is here with you, everything they say is to you: answer it.\n"
    "- Lines that begin `[note]` tell you about the call (who is speaking, who is here, what your worker "
    "found). Never answer a note or read one out.\n"
    "- A line that begins `[typed by NAME]` was typed in the call's chat: answer it only by calling `post`, "
    "never aloud.\n"
    "- For anything you would have to look up (code, records, documents, figures), call `ask` and say you "
    "will check: your worker, who reads the project's code, records and chats, answers in the call's chat, "
    "and you get a note. Never guess a fact.\n"
    "- When someone asks what is open, call `open_decisions` and go through what is theirs to decide as "
    '"In a call" below says, and let them choose. With anyone but Tom in the call, a decision about a client '
    "relationship (pricing, terms, its people) is one line: it is for Tom. Never say an id aloud.\n"
    "- When Tom settles open decisions (one idea may settle several), read his answer back in a sentence and "
    "call `settle` with the ids it settles. Only Tom settles decisions; a correction is a new `settle`.\n"
    "- Call `leave` when anyone in the call asks you to leave, or when everyone has wrapped up and said "
    "goodbye. Never leave because the meeting's scheduled time has passed: stay while people are still "
    "talking.\n\n"
    "How you work, from your persona:\n\n{conduct}\n\n"
    "Why you are in this call, from your notes before it:\n{prep}{so_far}"
)
TOOLS = [
    {"name": "ask", "description": "Ask your worker to look something up; the answer is posted in the call's "
     "chat and noted to you.", "parameters": {"type": "object", "properties": {"question": {"type": "string"}},
     "required": ["question"]}},
    {"name": "post", "description": "Post text in the call's chat (copied to the thread the call was asked in).",
     "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "open_decisions", "description": "The decisions open for Tom on this project, with their ids.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "settle", "description": "Record Tom's answer to open decisions: the read-back, and each "
     "`task id/question id` it settles.", "parameters": {"type": "object", "properties": {
         "ids": {"type": "array", "items": {"type": "string"}}, "readback": {"type": "string"}},
         "required": ["ids", "readback"]}},
    {"name": "leave", "description": "Leave the call: when someone asks you to, or everyone has said goodbye.",
     "parameters": {"type": "object", "properties": {}}},
]  # fmt: skip


def live_config(project: str, prep: str, handle: str | None, so_far: str = "") -> Any:
    """The Live session's settings: Valor's voice, transcripts both ways,
    proactive audio (it may stay silent), no cutting Valor off (Live's own
    barge-in stopped him for side talk, 2026-11-05: the call cuts him off
    only for the person he answers), compression that bounds what each
    turn bills, and resumption from `handle`. Live's own sensitivity to the
    start and end of speech is kept: at its lowest it missed a short
    "Hello.", and 500 ms to end a turn gained nothing measurable in a live
    call (the live tests, 2026-11-05). The instruction carries the
    persona's "In a call" section, read as the call starts. What Valor
    hears is transcribed as English unless clearly another language (an
    unclear phrase came out as Portuguese, 2026-11-06); the reply's
    language is the model's, which the persona section keeps English.
    A fresh session in a call under way is told `so_far` (`Call.so_far`)."""
    from google.genai import types

    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=settings.call_voice)
            )
        ),
        system_instruction=INSTRUCTIONS.format(
            name=persona.identity(settings.persona_dir)["name"],
            project=project or "Valor",
            conduct=persona.section(settings.persona_dir, "In a call"),
            prep=prep.strip() or "(no notes)",
            so_far=f"\n\nThe call so far:\n{so_far}" if so_far else "",
        ),
        tools=[types.Tool(function_declarations=[types.FunctionDeclaration(**t) for t in TOOLS])],
        input_audio_transcription=types.AudioTranscriptionConfig(language_codes=["en-US"]),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        proactivity=types.ProactivityConfig(proactive_audio=True),
        realtime_input_config=types.RealtimeInputConfig(
            activity_handling=types.ActivityHandling.NO_INTERRUPTION
        ),
        context_window_compression=types.ContextWindowCompressionConfig(
            trigger_tokens=CALL_CONTEXT, sliding_window=types.SlidingWindow(target_tokens=CALL_CONTEXT // 2)
        ),
        session_resumption=types.SessionResumptionConfig(handle=handle),
    )


def note(text: str) -> dict[str, Any]:
    """Context for the session that asks for no reply."""
    return {"turns": {"role": "user", "parts": [{"text": f"[note] {text}"}]}, "turn_complete": False}


SUMMARY = (
    "This is the recording of a Google Meet call Valor Engels (an AI coworker) was in, Valor's notes from "
    "before it, and its transcript (heard, said and typed lines, each with its minute). Write the notes on it, "
    "using only what was said:\n"
    "- topic: what the call was about, in two to five words, as its title;\n"
    "- summary: a short paragraph;\n- decisions: each decision made;\n"
    "- actions: each thing someone said they would do, with its owner (the person who said it) or null;\n"
    "- markdown: the full summary as a Markdown document: who joined, what was discussed, what was settled, "
    "what Valor answered and promised, and what is still open.\n\nValor's notes from before the call:\n{prep}"
    "\n\nThe transcript:\n"
)
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "summary": {"type": "string"},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "actions": {"type": "array", "items": {"type": "object", "properties": {
            "task": {"type": "string"}, "owner": {"type": "string", "nullable": True}}, "required": ["task"]}},
        "markdown": {"type": "string"},
    },
    "required": ["summary", "decisions", "actions", "markdown"],
}  # fmt: skip


GOOGLE_KEY = "GOOGLE_API_KEY"


class Gemini:
    """Valor's Gemini API (the key `python -m core google-key` copies from
    the vault): a call's Live sessions and its summary."""

    def __init__(self, key: str):
        from google import genai

        self.client = genai.Client(api_key=key)

    def connect(self, config):
        return self.client.aio.live.connect(model=settings.call_model, config=config)

    async def summarise(
        self, audio: Path, transcript: str, prep: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """The notes on a call from its recording, uploaded through the
        Files API (a request's own bytes stop near 20 MB) and deleted after,
        its prep (an invite's title, the asking chat's topic) and its
        transcript; with the response's usage."""
        from google.genai import types

        files = self.client.aio.files
        up = await files.upload(file=str(audio), config={"mime_type": "audio/mp4"})
        try:
            while up.state and up.state.name == "PROCESSING":
                await asyncio.sleep(2)
                up = await files.get(name=up.name)
            got = await self.client.aio.models.generate_content(
                model=settings.summary_model,
                contents=[up, SUMMARY.format(prep=prep.strip() or "(none)") + transcript],
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
LOST_LINE = "I've lost my voice connection; leaving, the summary will follow in the thread."
JOIN_S = 90  # to get through Meet's join screen
LOBBY_S = 600  # waiting to be let in
ALONE_S = 600  # with no one else ever there
EMPTY_S = 120  # after the last other person left
SOCKET_S = 30  # without the page's audio, or out of the call
WORKER_WAIT_S = 120  # for questions still with the worker after leaving
RECONNECTS = 2  # failed reconnects to Live before the call leaves
STATE_S = 2.0
# Live's transcript marks silence and pauses, "<no speech>{pause}" (2026-11-05).
MARKUP_PART = re.compile(r"<[^>]*>|\{[^}]*\}")
# This much unbroken sound from the person Valor answers cuts him off. At
# 0.2 s three of four cuts in Tom's call (2026-11-06) were 0.22-0.24 s of
# sound he did not go on from; the one he meant ran 0.5 s and on.
BARGE_S = 0.4
UNANSWERED_S = 2.0  # with one other person, this long with no answer to them asks for one
# Characters of the call so far told to a fresh session: at about four a
# token, a quarter of the window compression keeps.
CALL_SO_FAR = CALL_CONTEXT
STALL_S = 6.0  # this much speech, then this long quiet, with nothing back from Live resumes the session
CUT_S = 3.0  # the rest of what cut Valor off ("Valor, stop. Wait a moment.") asks nothing


def wordless(text: str) -> bool:
    """A transcript of no words: "<no speech>{pause}" or "..." (Live's
    turns of silence, 2026-11-05)."""
    return not re.search(r"\w", MARKUP_PART.sub("", text))


@dataclass
class Post:
    turn: str | None
    text: str
    copy: bool
    settles: list[dict[str, str]] = field(default_factory=list)


@dataclass
class Reply:
    """One turn of Live's: its audio, held until its `meet.say` is released,
    and what it said, from Live's transcript of it."""

    id: str
    typed: bool  # the answer to a typed line, which is posted, never spoken
    held: list[bytes] = field(default_factory=list)
    released: bool = False
    dropped: bool = False
    text: str = ""
    seconds: float = 0.0  # of audio received
    voiced: bool = False  # its audio has speech in it
    started: float | None = None  # on the recording's clock
    generated: bool = False
    over: asyncio.Future | None = None
    said: asyncio.Task | None = None


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
    streamed to a Gemini Live session, which speaks, posts and asks through
    its tools; the page, Gemini and the worker are injected, so a test
    drives it without Meet or Google."""

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
        project: str | None = None,
        socket=None,
        clock: Callable[[], float] = time.monotonic,
        store: str | None = None,
    ):
        from core import bridge

        self.id, self.info, self.page, self.dsn, self.gemini = session_id, info, page, dsn, gemini
        self.worker, self.worker_ready, self.project = worker, worker_ready, project
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
        self.session = None  # the Live session, while connected
        self.handle: str | None = None  # Live's resumption handle
        self.downs: dict[str, Down] = {}
        self.buffers: dict[str, np.ndarray] = {}
        self.mine = np.zeros(0, np.float32)  # Valor's own speech, for the recording
        self.names: dict[str, str] = {}
        self.first_names: list[str] = []  # who was in the call, for its title
        self.present: set[str] = set()  # who else is in the call now
        self.me: str | None = None
        self.speaking: str | None = None  # who the session was last told is speaking
        self.rival: tuple[str | None, float] = (None, 0.0)
        self.energy: dict[str, float] = {}  # each person's speech in the stretch being heard
        self.heard = ""  # Live's transcript of that stretch so far
        self.heard_at: float | None = None
        self.close_at: tuple[float, dict[str, float]] | None = None
        self.last_voice = 0.0
        self.lines: list[str] = []  # the call so far, for the worker and the summary
        self.segments: list[dict[str, Any]] = []  # the spoken transcript, for the store
        self.reply: Reply | None = None  # the turn Live is generating
        self.replies: dict[str, Reply] = {}  # each turn whose `meet.say` is not settled
        self.out: Reply | None = None  # the turn whose audio is playing
        self.answering: str | None = None  # whose stretch the current reply answers
        self.last_who: str | None = None
        self.unanswered: tuple[str, str, float] | None = None  # the last stretch heard, until Valor speaks
        self.barge = 0.0
        self.voiced_s = 0.0  # speech in the stretch being heard, less what cut Valor off
        self.unheard = 0.0  # speech sent since Live last sent anything
        self.stalled: asyncio.Event | None = None
        self.cut_at = -1e9
        self.typed = False  # a typed line is being answered: replies are posted, until someone speaks
        self.typed_posted = False  # and Live posted its answer
        self.metered: str | None = None  # the open `gateway.opened` call of the current turn
        self.playing_until = 0.0
        self.posts: asyncio.Queue = asyncio.Queue()
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
        """Every `MIX_S`, what everyone said since, summed, to the session
        and the recording (silence too, which tells Live a turn ended);
        who is loudest is who is speaking."""
        sent, start = 0, self.clock()
        while not self.leaving.is_set():
            await asyncio.sleep(MIX_S)
            due = int((self.clock() - start) * RATE) - sent
            if due <= 0:
                continue
            sent += due
            await self._tick(due)

    async def _tick(self, due: int) -> None:
        frame = np.zeros(due, np.float32)
        loud: dict[str, float] = {}
        for who, buf in list(self.buffers.items()):
            take, self.buffers[who] = buf[:due], buf[due:]
            frame[: len(take)] += take
            if len(take):
                loud[who] = float(np.sqrt(np.mean(take**2)))
        mine, self.mine = self.mine[:due], self.mine[due:]
        if self.recording is not None:
            self.recording.write(np.pad(mine, (0, due - len(mine))), frame)
        if self.session is not None:
            from google.genai import types

            with contextlib.suppress(Exception):  # a dropped session is the receiver's to resume
                await self.session.send_realtime_input(
                    audio=types.Blob(data=pcm16(frame), mime_type=f"audio/pcm;rate={RATE}")
                )
        await self._speakers(loud, due / RATE)

    async def _speakers(self, loud: dict[str, float], seconds: float) -> None:
        """Who is speaking, told to the session when it changes, and where
        a stretch of the transcript ends: at a change (once Live's
        transcript of it has caught up), or after a quiet."""
        voiced = {w: v for w, v in loud.items() if v >= SPEAKING}
        now = self.now()
        self.barge = self.barge + seconds if self.out is not None and self.answering in voiced else 0.0
        if self.barge >= BARGE_S:
            await self._cut_off(self.out)
        for who, v in voiced.items():
            self.energy[who] = self.energy.get(who, 0.0) + v * seconds
        self.voiced_s += seconds if voiced and self.out is None and now >= self.cut_at + CUT_S else 0.0
        # Live once stopped answering anything, transcripts included, for the
        # rest of a call (the live tests on the paid key, 2026-11-05). Inside
        # a long turn a healthy Live may send nothing for longer than that
        # (eleven resumes in Tom's call, 2026-11-06); it answers once the
        # room is quiet, so the stall is that quiet with nothing back.
        self.unheard += seconds if voiced and self.session is not None else 0.0
        if self.unheard >= STALL_S and now - self.last_voice >= STALL_S and self.stalled is not None:
            self.stalled.set()
        if voiced:
            self.last_voice = now
            top = max(voiced, key=voiced.get)
            if top == self.speaking:
                self.rival = (None, 0.0)
            elif self.rival[0] != top:
                self.rival = (top, seconds)
            else:
                self.rival = (top, self.rival[1] + seconds)
                if self.rival[1] >= CHANGE_S:
                    if self.speaking is not None and self.close_at is None:
                        self.close_at, self.energy = (now + LAG_S, self.energy), {}
                    self.speaking, self.rival = top, (None, 0.0)
                    await self._note(f"{self.names.get(top, 'someone')} is speaking.")
        if not voiced:
            await self._answer_alone(UNANSWERED_S)
        if self.close_at is not None and now >= self.close_at[0]:
            await self._close_stretch(self.close_at[1])
        elif self.heard and now - self.last_voice >= QUIET_S:
            await self._close_stretch(self.energy)
        elif not self.heard and self.voiced_s >= CHANGE_S and now - self.last_voice >= QUIET_S + LAG_S:
            # Speech Live gave no transcript of (a short "Hello." it missed, 2026-11-05).
            who = max(self.energy, key=self.energy.get) if self.energy else self.speaking
            if self.present == {who}:
                self.unanswered = (self.names.get(who, "someone"), "", now)
            self.energy, self.voiced_s = {}, 0.0

    async def _close_stretch(self, energy: dict[str, float]) -> None:
        """One `call.heard` row: what Live heard since the last, said by
        whoever spoke most in it. "Valor, leave" from Tom leaves."""
        text, at = self.heard.strip(), self.heard_at
        self.heard, self.heard_at, self.close_at, self.voiced_s = "", None, None, 0.0
        self.typed = self.typed and not text
        if energy is self.energy:
            self.energy = {}
        if not text:
            return
        who = max(energy, key=energy.get) if energy else self.speaking
        self.last_who = who
        name = self.names.get(who, "someone") if who else "someone"
        # Owed an answer only when said with no one else there: side talk
        # with Sam, answered once Sam had left (the live test, 2026-11-05), was not.
        # Set before the row's write, which a reply can finish during.
        self.unanswered = (name, text, self.now()) if who is not None and self.present == {who} else None
        await self._heard(who or "", name, text, "spoke")
        self.segments.append({"speaker": name, "start": round(at or 0.0, 2), "end": round(self.now(), 2),
                              "text": text, "channel": "call"})  # fmt: skip

    async def _heard(self, who: str, name: str, text: str, via: str) -> None:
        async with await db.connect(self.dsn) as conn:
            await ledger.append(conn, self.id, "call.heard", {"turn_id": ledger.new_id(), "who": who,
                                "name": name, "via": via, "text": text})  # fmt: skip
        self.lines.append(f"[{_clock(self.now())}] {name} ({via}): {text}")
        if name == settings.operator_name and LEAVE.search(text):
            self.leave("Tom asked Valor to leave")

    async def _typed(self, who: str, text: str) -> None:
        name = self.names.get(who, "someone")
        await self._heard(who, name, text, "typed")
        if self.leaving.is_set() or self.session is None:
            return
        self.typed, self.typed_posted, self.unanswered = True, False, None
        await self.session.send_client_content(
            turns={"role": "user", "parts": [{"text": f"[typed by {name}] {text}"}]}, turn_complete=True
        )

    async def _note(self, text: str) -> None:
        if self.session is not None:
            with contextlib.suppress(Exception):
                await self.session.send_client_content(**note(text))

    # -- the session --

    async def live(self) -> None:
        """The Live session for the whole call: resumed with its handle
        when Live says it is going away or the connection drops. A handle
        Live refuses (not found or expired, an hour into Tom's call,
        2026-11-12) opens a fresh session told the call so far; after
        `RECONNECTS` failed fresh tries, one line in the chat and the call leaves."""
        failed = 0
        while not self.leaving.is_set():
            so_far = "" if self.handle else self.so_far()
            config = live_config(self.project or "", self.info.get("prep") or "", self.handle, so_far)
            opened = False
            try:
                async with self.gemini.connect(config) as session:
                    self.session, failed, opened = session, 0, True
                    self.unheard, self.stalled = 0.0, asyncio.Event()
                    _log(f"call {self.id}: the Live session is open{' (resumed)' if self.handle else ''}")
                    receiving = asyncio.create_task(self._receive(session))
                    stall = asyncio.create_task(self.stalled.wait())
                    try:
                        await asyncio.wait({receiving, stall}, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        stall.cancel()
                        if not receiving.done():
                            receiving.cancel()
                        if self.stalled.is_set():  # not a call that is leaving
                            _log(f"call {self.id}: Live sent nothing back after speech; resuming")
                    if receiving.done() and not receiving.cancelled() and receiving.exception() is not None:
                        raise receiving.exception()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001  a lost session is resumed, or the call leaves
                refused = not opened and self.handle is not None
                failed = 0 if opened else failed + (not refused)
                _log(f"call {self.id}: the Live session {'dropped' if opened else 'failed to open'}: {exc!r}"
                     f"{'; opening a fresh one' if refused else ''}")  # fmt: skip
                if refused:
                    self.handle = None
            finally:
                self.session = None
                if self.reply is not None:
                    await self._finish(self.reply, cut=True)
                    self.reply = None
            if failed >= RECONNECTS:
                self.posts.put_nowait(Post(None, LOST_LINE, False))
                self.leave("Valor lost its voice connection")
            elif failed:
                await asyncio.sleep(1)

    def so_far(self) -> str:
        """What a fresh session in a call under way is told: that Valor is
        in it, who is here, and the newest of the transcript, at most
        `CALL_SO_FAR` characters; empty before anything was said."""
        if not self.lines:
            return ""
        tail = "\n".join(self.lines)
        if len(tail) > CALL_SO_FAR:
            tail = tail[-CALL_SO_FAR:]
            tail = tail.partition("\n")[2] or tail
        here = self._here(sorted(self.names.get(i, "someone") for i in self.present))
        return ("You are already in this call and have greeted everyone: do not greet them again; carry on "
                f"from here.\n{here}\n{tail}")  # fmt: skip

    async def _receive(self, session) -> None:
        while not self.leaving.is_set():
            async for m in session.receive():
                self.unheard = 0.0
                if m.session_resumption_update and m.session_resumption_update.resumable:
                    self.handle = m.session_resumption_update.new_handle or self.handle
                if m.go_away is not None:
                    _log(f"call {self.id}: Live is going away in {m.go_away.time_left}; resuming")
                    return
                if m.tool_call is not None:
                    await self._tools(session, m.tool_call.function_calls or [])
                if m.server_content is not None:
                    await self._content(m.server_content)
                if m.usage_metadata is not None:
                    await self._charge(m.usage_metadata.model_dump(mode="json", exclude_none=True))

    async def _begin(self) -> Reply:
        """The reply Live has started: what was heard before it is a whole
        stretch, and the turn is metered from here. It answers whoever spoke
        most since the last change of speaker: Live may begin a reply before
        its transcript of the question, and the stretch closed before it was
        another person's (a cut for side talk in the live test, 2026-11-06)."""
        if self.reply is None:
            spoke = self.energy
            self.answering = max(spoke, key=spoke.get) if spoke else self.speaking or self.last_who
            if self.close_at is not None:
                await self._close_stretch(self.close_at[1])
            if self.heard:
                await self._close_stretch(self.energy)
            self.reply = Reply(ledger.new_id(), self.typed)
            self.replies[self.reply.id] = self.reply
            await self._open(self.reply.id)
        return self.reply

    async def _content(self, sc) -> None:
        if sc.input_transcription and sc.input_transcription.text:
            if not self.heard:
                self.heard_at = self.now()
            self.heard += sc.input_transcription.text
        if sc.output_transcription and sc.output_transcription.text:
            reply = await self._begin()
            reply.text += sc.output_transcription.text
            if wordless(reply.text):
                reply.dropped, reply.held = True, []  # a turn of silence
            else:
                self.unanswered = None
                if reply.held and reply.said is None:
                    await self._audio(reply, b"")
        if sc.model_turn:
            for part in sc.model_turn.parts or []:
                if part.inline_data and part.inline_data.data:
                    await self._audio(await self._begin(), part.inline_data.data)
        if sc.turn_complete and self.reply is not None:
            reply, self.reply = self.reply, None
            reply.generated = True
            text = MARKUP_PART.sub("", reply.text).strip()
            if reply.typed and not self.typed_posted and not wordless(text):
                self.posts.put_nowait(Post(reply.id, text, True))
            if reply.said is None:
                await self._answer_alone(0.0)
            if reply.said is None:
                await self._finish(reply, cut=False)
            elif reply.released:
                self._spawn(self._played_out(reply))

    async def _answer_alone(self, after_s: float) -> None:
        """With one other person in the call, all they say is to Valor
        (Tom's "Hello", unanswered, 2026-11-05; Live kept silent on "Hello."
        with only one person there, or gave no transcript of it, in the live
        tests, 2026-11-05): a turn of silence after their words, or
        `after_s` with no turn, asks for the answer, once."""
        if (
            len(self.present) != 1 or self.unanswered is None or self.session is None
            or self.reply is not None or self.out is not None or self.now() - self.unanswered[2] < after_s
        ):  # fmt: skip
            return
        name, text, _ = self.unanswered
        self.unanswered = None
        said = (
            f'said to you: "{text}". Answer it.'
            if text
            else "spoke to you, words you did not catch: answer them."
        )
        await self.session.send_client_content(
            turns={"role": "user", "parts": [{"text": f"[note] {name}, the only one here, {said}"}]},
            turn_complete=True,
        )

    async def _audio(self, reply: Reply, data: bytes) -> None:
        """A chunk of Live's speech: once the turn shows speech, in Live's
        transcript of it or in its sound, it requests its `meet.say`. Live
        answers what it keeps out of with a turn of near-silent audio, its
        transcript "<no speech>{pause}", "..." or none (the live tests,
        2026-11-05), which is held and never said. Each chunk is held until
        the say is released, and dropped when it is refused or when the
        turn answers a typed line."""
        if reply.typed or reply.dropped:
            return
        reply.seconds += len(data) / 2 / OUT_RATE
        if data and not reply.voiced:
            pcm = np.frombuffer(data, "<i2").astype(np.float32) / 32768
            reply.voiced = float(np.sqrt(np.mean(pcm**2))) >= SPEAKING
        if reply.said is None and wordless(reply.text) and not reply.voiced:
            if data:
                reply.held.append(data)
            return
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
        over it, or the session was lost). Its `meet.say` settles with what
        was said."""
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
            spoken = _cut(MARKUP_PART.sub("", reply.text), played / reply.seconds if reply.seconds else 0.0)
        else:
            played, spoken = reply.seconds, " ".join(MARKUP_PART.sub("", reply.text).split())
        result = {"spoken_text": spoken, "cut": cut, "played_s": round(played, 2)}
        reply.over.set_result(result)
        if spoken:
            self.lines.append(f"[{_clock(self.now())}] Valor (spoke): {spoken}")
            self.segments.append({"speaker": "Valor Engels", "start": round(reply.started or 0.0, 2),
                                  "end": round(self.now(), 2), "text": spoken, "channel": "mic"})  # fmt: skip

    async def _cut_off(self, reply: Reply) -> None:
        """The person Valor answers spoke over him: he stops, the rest of
        the turn Live is still sending is dropped, and the session is told
        what was heard of it."""
        reply.dropped, reply.held, self.barge, self.cut_at = True, [], 0.0, self.now()
        await self._finish(reply, cut=True)
        name = self.names.get(self.answering, "someone") if self.answering else "someone"
        said = reply.over.result().get("spoken_text") if reply.over and reply.over.done() else ""
        await self._note(f"{name} cut you off; they heard only: {said or '(nothing)'}")

    async def _say(self, payload: dict[str, Any]) -> dict[str, Any]:
        """`meet.say`, released: the turn's held audio plays, and the say
        settles once the turn is over."""
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

    async def _tools(self, session, calls) -> None:
        from google.genai import types

        out = []
        for fc in calls:
            args = dict(fc.args or {})
            reply = await self._begin()
            if fc.name == "ask" and str(args.get("question") or "").strip():
                question = str(args["question"]).strip()
                key = " ".join(re.findall(r"\w+", question.lower()))
                if key in self.asked:
                    # Live asked again in the turn after its tool response
                    # (the live test, 2026-11-05): one worker turn, one post.
                    answer = self.asked[key]
                    result = f"already asked; {f'the answer, posted in the chat: {answer}' if answer else 'the answer is coming'}"
                else:
                    self.asked[key] = None
                    self.questions.put_nowait(question)
                    result = "asked: the answer will be posted in the chat and noted to you"
                if not await self.worker_ready():
                    result += (
                        "; your worker's copy of the project is still being set up, so say it needs a moment"
                    )
            elif fc.name == "post" and str(args.get("text") or "").strip():
                self.typed_posted = True
                self.posts.put_nowait(Post(reply.id, str(args["text"]).strip(), True))
                result = "posted"
            elif fc.name == "open_decisions":
                async with await db.connect(self.dsn) as conn:
                    result = await decisions(conn, self.project)
            elif fc.name == "settle" and args.get("ids") and str(args.get("readback") or "").strip():
                settles = [
                    {"task_id": t.strip(), "question_id": q.strip()}
                    for t, _, q in (str(i).partition("/") for i in args["ids"])
                    if t.strip() and q.strip()
                ]
                self.posts.put_nowait(Post(reply.id, str(args["readback"]).strip(), True, settles))
                result = "recorded"
            elif fc.name == "leave":
                self.leave("Valor was asked to leave")
                result = "leaving"
            else:
                result = f"not done: {fc.name} needs its arguments"
            out.append(types.FunctionResponse(id=fc.id, name=fc.name, response={"result": result}))
        if out:
            await session.send_tool_response(function_responses=out)

    # -- metering --

    async def _open(self, turn_id: str) -> None:
        """Open a turn's call before its usage comes (`spending.open_call`);
        a stopped task refuses it, and the call leaves."""
        from core import spending

        price = spending.gemini_price(settings.call_model)
        call = {"call_id": ledger.new_id(), "turn_id": turn_id, "model": settings.call_model,
                "route": "gemini-live", "estimated_input": CALL_CONTEXT, "max_tokens": CALL_OUTPUT_MOST,
                "estimate_usd_micros": spending.gemini_worst_case(price), "unpriced": price["unpriced"]}  # fmt: skip
        try:
            async with await db.connect(self.dsn) as conn:
                self.metered = await spending.open_call(conn, self.id, call)
        except tasks.TaskStopped:
            self.leave("the call was stopped", hard=True)

    async def _charge(self, usage: dict[str, Any]) -> None:
        """A turn's usage, which Live sends at its end (a turn of silence
        too), charged at the model's rates by modality."""
        from core import spending

        if self.metered is None:
            await self._open(self.reply.id if self.reply else ledger.new_id())
        if self.metered is None:
            return
        price = spending.gemini_price(settings.call_model)
        async with await db.connect(self.dsn) as conn:
            await spending.charge(conn, self.id, self.metered, spending.gemini_cost(usage, price),
                                  {"model": settings.call_model, "usage": usage})  # fmt: skip
        self.metered = None

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
        done = []
        for i, part in enumerate(bridge.split_text("meet", post.text)):
            payload = {"text": part, **({"settles": post.settles} if post.settles and i == 0 else {})}
            out = await self._effect("meet.send_message", payload, f"meet/{turn}/{i}/post")
            if out.kind == "done":
                done.append(part)
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
                async with await db.connect(self.dsn) as conn:
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
        in the call's report."""
        while True:
            question = await self.questions.get()
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
                self.asked.pop(" ".join(re.findall(r"\w+", question.lower())), None)  # may be asked again
            elif self.left:
                self.late.append({"question": question, "answer": answer})
            else:
                self.asked[" ".join(re.findall(r"\w+", question.lower()))] = answer
                self.posts.put_nowait(Post(None, f'On "{question}": {answer}', True))
                await self._note(f'Your worker answered "{question}" (posted in the chat): {answer}')

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
        """Who is in the call, told to the session when it changes, and the
        reasons to leave it."""
        start, alone, down, seen, inside = self.clock(), None, None, set(), self.clock()
        told = None
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
                    async with await db.connect(self.dsn) as conn:
                        await ledger.append(
                            conn, self.id, "call.participant", {"id": p["id"], "name": p["name"]}
                        )
            here = sorted(self.names.get(i, "someone") for i in self.present)
            if here != told and self.session is not None:
                told = here
                await self._note(self._here(here))
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
        to Valor (Tom's "Hello", unanswered, 2026-11-05)."""
        if len(names) == 1:
            return f"Only {names[0]} is here with you: everything they say is to you, so answer it."
        return f"In the call with you: {', '.join(names) or 'no one yet'}."

    async def orders(self) -> None:
        """A stop of the session leaves at once; a steer is posted in the
        call's chat."""
        listener = await db.connect(self.dsn)
        try:
            await listener.execute("LISTEN valor_events")
            await listener.execute(f"LISTEN {tasks.STOP_CHANNEL}")
            async with await db.connect(self.dsn) as conn:
                if await tasks.is_stopped(conn, self.id):
                    self.leave("the call was stopped", hard=True)
            async for got in listener.notifies():
                if got.channel == tasks.STOP_CHANNEL and got.payload == self.id:
                    self.leave("the call was stopped", hard=True)
                    return
                said = json.loads(got.payload) if got.channel == "valor_events" else {}
                if said.get("task_id") == self.id and said.get("type") == "message.steered":
                    async with await db.connect(self.dsn) as conn:
                        row = await (
                            await conn.execute(
                                "SELECT payload->>'text' FROM events WHERE id = %s", (said["id"],)
                            )
                        ).fetchone()
                    if row and row[0]:
                        self.posts.put_nowait(Post(None, row[0], False))
        finally:
            await listener.close()

    async def joined(self) -> str | None:
        """Through Meet's join screen and lobby; None once in, else why not."""
        await self.page.join()
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
            async with await db.connect(self.dsn) as conn:
                await spending.open_call(conn, self.id, {
                    "call_id": call_id, "turn_id": "summary", "model": settings.summary_model,
                    "route": "gemini", "estimated_input": est, "max_tokens": 16_000,
                    "estimate_usd_micros": spending.gemini_worst_case(price, est, 16_000),
                    "unpriced": price["unpriced"]})  # fmt: skip
            notes, usage = await self.gemini.summarise(audio, transcript, self.info.get("prep") or "")
            async with await db.connect(self.dsn) as conn:
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
        summary = {"summary": linked(notes["summary"], repo, links), "decisions": list(notes["decisions"]),
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
        return {"path": str(folder / f"{day} {title}.md")}

    def _title(self, topic: str | None) -> str:
        """The call's topic, then the first names of those in it, Valor's last."""
        mine = persona.identity(settings.persona_dir)["name"].split()[0]
        return title(topic, [n for n in self.first_names if n != mine] + [mine])

    def _links(self) -> tuple[str | None, dict[str, str]]:
        """The call's project's repository and reference links, read now; none
        when it has no project or its spec does not load."""
        try:
            spec = workspace.Spec.load(self.project) if self.project else None
        except workspace.Refused as exc:
            _log(f"call {self.id}: no links in the summary: {exc}")
            spec = None
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
                await self.page.ready()
                self.store.parent.mkdir(parents=True, exist_ok=True)
                self.staging = Path(tempfile.mkdtemp(prefix=".valor-call-", dir=self.store.parent))
                self.recording = Recording(self.staging / "call.wav")
                self.started_at, self.t0 = datetime.now(UTC), self.clock()
                self.posts.put_nowait(Post("join", JOIN_LINE, False))
                lanes += [self._lane(c) for c in (self.live(), self.mix(), self.watch())]
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
            unanswered.append(self.questions.get_nowait())
        jobs = [*lanes, *self.jobs]
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        summary = await self.summarise()
        async with await db.connect(self.dsn) as conn:
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
