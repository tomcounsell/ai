"""A Google Meet call (docs/plans/meet.md): its session, its end, and the
process that is Valor in it.

**The call session.** `meet.join` (`tools/meet.py`), requested by a group
chat's session, opens one (`begin`): a root chat session on channel `meet`,
its chat id the meeting code, under the asking chat's project and at its
ceiling, so its worker has that chat's access (`tasks.dispatch` gives it
the asking chat's room). Its `task.started` carries `call`: the link,
this Mac, the chat and thread it was asked in (`asked_by`), and `prep`,
the asking turn's notes on the topic. It runs no turn (`router.has_step`)
and owes no notice (`notices.owe`); everything it says goes to the asking
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
(`bridges/meet`), hears each person apart (`Speaker`), decides whether to
answer (`meet.speak`), drafts with the front model, asks its worker, and
speaks and posts through the broker on its own session.
"""

import asyncio
import contextlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import psycopg

from core import broker, db, intake, ledger, tasks, workspace
from core.settings import resolve_seat, settings

CHANNEL = "meet"
# A call whose process never wrote `call.started` this long after it was
# opened, with its lock free, never started.
NEVER_STARTED_S = 90
SIGN_IN = (
    'open -na "Google Chrome" --args --user-data-dir="$HOME/Library/Application Support/Valor/meet-chrome" '
    "https://accounts.google.com/"
)


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


def profile_ready(profile: str | None = None) -> bool:
    """Whether this Mac's call profile is signed in to a Google account."""
    try:
        prefs = json.loads(Path(profile or settings.meet_profile, "Default", "Preferences").read_text())
    except OSError, ValueError:
        return False
    return any(a.get("email") for a in prefs.get("account_info") or [])


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
) -> bool:
    """Write the call's end once (module docstring). False when it had
    already ended."""
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
        lines.append(
            f"Its whole transcript: `valor-record call {call.get('code')}`, every page. Write the full "
            f"summary as a file and send it in the thread it was asked in (reply_to "
            f"{(call.get('asked_by') or {}).get('reply_to')}), with at most two lines of text beside it."
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


# -- hearing -------------------------------------------------------------------

RATE = 16_000
FRAME, CONTEXT = 512, 64  # Silero's frame and context at 16 kHz
VOICED = 0.5
PRE_FRAMES = 8  # kept from before speech starts, so its first sound is heard
PAUSE_S = 0.3  # a pause long enough to ask Smart Turn whether the turn ended
GIVE_UP_S = 1.6  # a pause this long ends the turn whatever Smart Turn says
SHORT_S = 1.0  # a turn this short ends only by `GIVE_UP_S`: "Valor," then a pause is no turn
TURN_MOST_S = 30.0
BARGE_S = 0.12  # this much speech by the person Valor answers stops him
TURN_ENDED = 0.5
SMART_TURN = ("pipecat-ai/smart-turn-v3", "smart-turn-v3.2-cpu.onnx")
SMART_TURN_SAMPLES = 8 * RATE
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


class Ears:
    """What hears: Silero's voice activity (onnx-asr's model), Smart Turn
    v3 (Pipecat's, BSD-2) on Whisper's features, and Parakeet in the voice
    process (`tools/voice.py`), which holds the one copy of it on the Mac."""

    def __init__(self, vad, turn, transcribe: Callable[[np.ndarray], Awaitable[str]]):
        from onnx_asr.preprocessors.numpy_preprocessor import WhisperPreprocessorNumpy

        class Features(WhisperPreprocessorNumpy):
            _chunk_length = SMART_TURN_SAMPLES // RATE

        self._vad, self._turn, self.transcribe = vad, turn, transcribe
        self._features = Features("whisper80")

    @classmethod
    def load(cls, transcribe: Callable[[np.ndarray], Awaitable[str]]) -> Ears:
        import onnx_asr
        import onnxruntime
        from huggingface_hub import hf_hub_download

        vad = onnx_asr.load_vad("silero")._model
        return cls(vad, onnxruntime.InferenceSession(hf_hub_download(*SMART_TURN)), transcribe)

    def voiced(self, speaker: Speaker, frame: np.ndarray) -> float:
        """The chance a 32 ms frame is speech, carrying the speaker's state."""
        out, speaker.vad_state = self._vad.run(
            ["output", "stateN"],
            {"input": np.concatenate([speaker.context, frame])[None], "state": speaker.vad_state,
             "sr": np.array([RATE], dtype=np.int64)},
        )  # fmt: skip
        speaker.context = frame[-CONTEXT:]
        return float(out[0, 0])

    def ended(self, audio: np.ndarray) -> float:
        """Smart Turn's chance the turn in `audio` is finished: its last 8 s,
        padded at the start, normalised, as Pipecat's own inference does."""
        a = audio[-SMART_TURN_SAMPLES:]
        a = np.pad(a, (SMART_TURN_SAMPLES - len(a), 0))
        a = ((a - a.mean()) / np.sqrt(a.var() + 1e-7)).astype(np.float32)
        features, _ = self._features(a[None], np.array([len(a)], dtype=np.int64))
        return float(self._turn.run(None, {"input_features": features.astype(np.float32)})[0].ravel()[0])


async def words(audio: np.ndarray) -> str:
    """The words of a turn, from the voice process's `/hear`
    (`tools/voice.py`), which holds the Mac's one copy of Parakeet."""
    import aiohttp

    url = f"http://127.0.0.1:{settings.voice_port}/hear"
    async with (
        aiohttp.ClientSession() as http,
        http.post(url, data=audio.astype(np.float32).tobytes()) as got,
    ):
        got.raise_for_status()
        return (await got.json())["text"]


class Speaker:
    """One person's turn as it is heard: their voiced frames from the
    first (with the frames just before it) to a pause Smart Turn calls the
    end, or `GIVE_UP_S` of quiet. Meet sends a person's audio only while
    they are the loudest, so quiet is measured by the clock."""

    def __init__(self, ident: str):
        self.id = ident
        self.down = Down()
        self.vad_state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros(CONTEXT, dtype=np.float32)
        self.carry = np.zeros(0, dtype=np.float32)
        self.before: list[np.ndarray] = []
        self.heard: list[np.ndarray] = []
        self.voiced_s = 0.0
        self.last_voice = 0.0
        self.since = 0.0
        self.asked = -1.0

    def feed(self, ears: Ears, samples: np.ndarray, rate: int, now: float) -> bool:
        """Hear a frame; whether they are speaking now, past `BARGE_S`."""
        x = np.concatenate([self.carry, self.down(samples, rate)])
        while len(x) >= FRAME:
            frame, x = x[:FRAME], x[FRAME:]
            voiced = ears.voiced(self, frame) >= VOICED
            if voiced and not self.heard:
                self.heard, self.since = list(self.before), now
            if voiced:
                self.voiced_s += FRAME / RATE
                self.last_voice = now
            if self.heard:
                self.heard.append(frame)
            else:
                self.before = [*self.before[-(PRE_FRAMES - 1) :], frame]
        self.carry = x
        return bool(self.heard) and self.voiced_s >= BARGE_S and now - self.last_voice < PAUSE_S

    def pause(self, ears: Ears, now: float) -> np.ndarray | None:
        """The turn's audio once it has ended, else None."""
        if not self.heard or now - self.last_voice < PAUSE_S:
            return None
        audio = np.concatenate(self.heard)
        done = now - self.last_voice >= GIVE_UP_S or now - self.since >= TURN_MOST_S
        if not done and self.asked != self.last_voice and len(audio) >= SHORT_S * RATE:
            self.asked = self.last_voice
            done = ears.ended(audio) >= TURN_ENDED
        if not done:
            return None
        enough = self.voiced_s >= 2 * BARGE_S
        self.heard, self.before, self.voiced_s, self.asked = [], [], 0.0, -1.0
        return audio if enough else None


# -- answering -----------------------------------------------------------------

GUIDANCE = (
    "You are Valor Engels, in a live Google Meet call as yourself, on the {project} project. You are an AI "
    "coworker; everyone in the call knows. Your reply is spoken aloud, or posted in the call's chat when it "
    "was typed there: short, plain, natural sentences, one to three, no lists or markdown, no preamble.\n\n"
    "Each message brings what was said in the call since your last reply (your own words as Valor), the "
    "decisions open for Tom when they change, and then what was just said, which you answer. A reply of yours "
    "that was not said is marked so: you stayed silent then.\n\n"
    "- Answer whoever addresses you. Follow what the others want to discuss; do not steer the call.\n"
    "- Avoid legal and financial work, pricing, terms and people matters for now: say you will take it "
    "offline with Tom.\n"
    "- When someone asks what is open, take them through the open decisions listed, one at a time, with "
    "your recommendation on each, and let them choose. With anyone but Tom in the call, an open decision "
    "about a client relationship (pricing, terms, its people) is one line: it is for Tom. Mention no "
    "decision that is not listed, and never say an id aloud.\n"
    "- For anything you would have to look up (code, records, documents, figures), say you will check and "
    "end your reply with a line `ASK: <the question>`: your worker, who reads the project's code, records "
    "and chats, answers in writing in the call's chat. Never guess a fact.\n"
    "- When Tom settles open decisions (one idea may settle several), read his answer back in a sentence and "
    "end your reply with a line `DECIDED: <task id>/<question id>, ... | <the read-back>`, naming each "
    "question it settles by the ids listed. Only Tom settles decisions; a correction is a new DECIDED line.\n\n"
    "Why you are in this call, from your notes before it:\n{prep}"
)
SENTENCE = re.compile(r"[.!?…](?=\s)")
DIRECTIVES = ("ASK:", "DECIDED:")


def directive(line: str) -> tuple[str, dict[str, Any]] | None:
    """An `ASK:` or `DECIDED:` line of the front model's reply as the action
    it asks for; None for any other line."""
    line = line.strip()
    if line.startswith("ASK:") and line[4:].strip():
        return "ask_worker", {"question": line[4:].strip()}
    if line.startswith("DECIDED:") and "|" in line:
        ids, _, readback = line[8:].partition("|")
        settles = [
            {"task_id": t.strip(), "question_id": q.strip()}
            for t, _, q in (i.partition("/") for i in ids.split(","))
            if t.strip() and q.strip()
        ]
        if settles and readback.strip():
            return "record_decision", {"readback": readback.strip(), "settles": settles}
    return None


class Front:
    """The front model: one Claude Code session on the light seat for the
    whole call, streaming (`claude -p` reading and writing stream-json), on
    the call's own gateway, so it answers within a second or so of a turn
    while `meet.speak` decides whether Valor says it. It has no tools: it
    asks the worker and records a decision by a line of its reply
    (`directive`), which acts only once the draft is taken."""

    def __init__(self, argv: list[str], env: dict[str, str], cwd: str):
        self.argv, self.env, self.cwd = argv, env, cwd
        self.proc: asyncio.subprocess.Process | None = None
        self.lock = asyncio.Lock()

    @classmethod
    def claude(cls, base_url: str, *, model: str, project: str, prep: str, home: str) -> Front:
        from core.gateway import TURN_TOKEN

        system = GUIDANCE.format(project=project, prep=prep.strip() or "(no notes)")
        argv = [settings.claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                "--verbose", "--include-partial-messages", "--model", model, "--system-prompt", system,
                "--tools", "", "--setting-sources", "", "--strict-mcp-config"]  # fmt: skip
        env = {"PATH": "/usr/bin:/bin", "HOME": home, "ANTHROPIC_BASE_URL": base_url,
               "CLAUDE_CONFIG_DIR": home, "CLAUDE_CODE_OAUTH_TOKEN": TURN_TOKEN}  # fmt: skip
        return cls(argv, env, home)

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.argv, env=self.env, cwd=self.cwd, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=16 * 1024 * 1024,
        )  # fmt: skip

    async def close(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            self.proc.stdin.close()
            with contextlib.suppress(ProcessLookupError):
                self.proc.terminate()
            await self.proc.wait()

    async def draft(self, news: str, said: str, actions: list[tuple[str, dict[str, Any]]]):
        """The answer to `said`, sentence by sentence as it streams; its
        directive lines go to `actions`. Drafts take turns: a draft
        abandoned midway is read to its end before the next is asked."""
        await self.lock.acquire()
        ended = False
        try:
            message = (f"{news}\n\n" if news else "") + f"Now: {said}"
            line = {"type": "user", "message": {"role": "user", "content": message}}
            self.proc.stdin.write((json.dumps(line) + "\n").encode())
            await self.proc.stdin.drain()
            text = ""
            while True:
                event = await self._event()
                if event.get("type") == "result":
                    ended = True
                    if event.get("is_error"):
                        raise RuntimeError(f"the front model's turn failed: {str(event.get('result'))[:300]}")
                    break
                delta = (event.get("event") or {}).get("delta") or {}
                if event.get("type") != "stream_event" or delta.get("type") != "text_delta":
                    continue
                text += delta["text"]
                text, done = self._sentences(text, actions)
                for sentence in done:
                    yield sentence
            _, done = self._sentences(text + "\n", actions)
            for sentence in done:
                yield sentence
        finally:
            if ended:
                self.lock.release()
            else:
                asyncio.get_running_loop().create_task(self._drain())

    async def _event(self) -> dict[str, Any]:
        raw = await self.proc.stdout.readline()
        if not raw:
            raise RuntimeError("the front model's session ended")
        return json.loads(raw)

    async def _drain(self) -> None:
        try:
            while (await self._event()).get("type") != "result":
                pass
        except RuntimeError:
            pass
        finally:
            self.lock.release()

    @staticmethod
    def _sentences(text: str, actions: list) -> tuple[str, list[str]]:
        """The whole sentences of `text` to say, and what is left of it. A
        line that is (or may yet be) a directive is never said: once it is
        whole it goes to `actions`."""
        out = []
        while text:
            line, newline, rest = text.partition("\n")
            head = line.strip()
            if head and any(d.startswith(head) or head.startswith(d) for d in DIRECTIVES):
                if not newline:
                    break
                if (found := directive(head)) is not None:
                    actions.append(found)
                text = rest
                continue
            m = SENTENCE.search(line + (" " if newline else ""))
            if m is not None:
                if sentence := line[: m.end()].strip():
                    out.append(sentence)
                text = text[m.end() :]
            elif newline:
                if head:
                    out.append(head)
                text = rest
            else:
                break
        return text, out


async def decisions(conn, project: str | None) -> str:
    """The open decisions as the front model reads them: the project's
    board snapshot now, each with its task and question ids, and the board's
    canvas as last written (`board.canvas_markdown`)."""
    from core import board

    spec = board.board_spec(project)
    if spec is None:
        return "This project has no decisions open."
    snap = await board.snapshot(conn, spec)
    lines = [f"- task {q['task_id']}, question {q['question_id']}: {q['question']}" for q in snap["open"]]
    canvas = await board.canvas_markdown(conn, spec)
    out = ["Open for Tom:", *(lines or ["- nothing"])]
    if canvas:
        out += ["", "The decision board as last written:", canvas]
    return "\n".join(out)


# -- the call ------------------------------------------------------------------

# "Valor, leave" from Tom ends the call as any leave does (Tom, 2026-10-27).
LEAVE = re.compile(
    r"\bvalor\b(?:(?!\b(?:don'?t|do not|not|never)\b).){0,30}?\b(?:leave|drop off|hang up)\b", re.IGNORECASE
)
JOIN_LINE = "Valor here, keeping a transcript for notes."
JOIN_S = 90  # to get through Meet's join screen
LOBBY_S = 600  # waiting to be let in
ALONE_S = 600  # with no one else ever there
EMPTY_S = 120  # after the last other person left
SOCKET_S = 30  # without the page's audio, or out of the call
WORKER_WAIT_S = 120  # for questions still with the worker after leaving
TICK_S, STATE_S = 0.1, 2.0
VOICED_MOST = 32  # sentences voiced ahead and kept
RECENT = 20


@dataclass
class Turn:
    id: str
    who: str
    name: str
    text: str
    via: str  # "spoke" or "typed"


@dataclass
class Post:
    turn: str | None
    text: str
    copy: bool
    settles: list[dict[str, str]] = field(default_factory=list)


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
    """Valor in one call (module docstring), with what it hears, speaks,
    decides and asks injected, so a test drives it without Meet."""

    def __init__(
        self,
        session_id: str,
        info: dict[str, Any],
        page,
        ears: Ears,
        *,
        dsn: str,
        voice: Callable[[str], tuple[np.ndarray, int]],
        judge: Callable[[dict[str, str], str], Awaitable[Any]],
        front: Front,
        worker: Callable[[str], Awaitable[str]],
        worker_ready: Callable[[], Awaitable[bool]],
        project: str | None = None,
        socket=None,
        clock: Callable[[], float] = time.monotonic,
    ):
        from concurrent.futures import ThreadPoolExecutor

        from core import bridge

        self.id, self.info, self.page, self.ears, self.dsn = session_id, info, page, ears, dsn
        self.voice, self.judge, self.front, self.worker = voice, judge, front, worker
        self.worker_ready, self.project, self.socket, self.clock = worker_ready, project, socket, clock
        self.declared = broker.Performers(*bridge.declared_performers())
        self.bound = broker.Performers(
            _Performer("meet.say", self._say), _Performer("meet.send_message", self._post)
        )
        self.speakers: dict[str, Speaker] = {}
        self.names: dict[str, str] = {}
        self.me: str | None = None
        self.lines: list[str] = []  # the call so far, as the front model reads it
        self.told = 0  # how many of `lines` the front model has read
        self.open_told: str | None = None
        self.unsaid = False
        self.current: Turn | None = None  # the turn Valor is answering
        self.playing: Turn | None = None  # the turn whose answer is being spoken
        self.said: dict[str, int] = {}  # each turn's request numbers so far
        self.says: asyncio.Queue = asyncio.Queue()
        self.posts: asyncio.Queue = asyncio.Queue()
        self.questions: asyncio.Queue = asyncio.Queue()
        self.asking: list[str] = []
        self.late: list[dict[str, str]] = []
        self.dropped: list[str] = []
        self.left = False
        self.leaving = asyncio.Event()
        self.reason = ""
        self.hard = False
        self.jobs: set[asyncio.Task] = set()
        self._tts = ThreadPoolExecutor(max_workers=1)
        self._spoken: dict[str, asyncio.Future] = {}

    # -- hearing (the page's socket calls these) --

    def hear(self, who: str, samples: np.ndarray, rate: int) -> None:
        if not who or who == self.me:
            return
        speaker = self.speakers.setdefault(who, Speaker(who))
        talking = speaker.feed(self.ears, samples, rate, self.clock())
        if talking and self.playing is not None and self.playing.who == who:
            self.playing = None
            self._spawn(self.page.stop())

    def chat(self, who: str, text: str) -> None:
        if who == self.me or not text.strip():
            return
        self._spawn(
            self._turn(Turn(ledger.new_id(), who, self.names.get(who, "someone"), text.strip(), "typed"))
        )

    def people(self, update: dict[str, Any]) -> None:
        for p in [*(update.get("newUsers") or []), *(update.get("updatedUsers") or [])]:
            if p.get("isCurrentUser"):
                self.me = p.get("deviceId")
            elif p.get("deviceId"):
                self.names[p["deviceId"]] = p.get("fullName") or p.get("displayName") or "someone"

    # -- the turns --

    async def listen(self) -> None:
        """Every `TICK_S`, each person's pause asked whether their turn ended."""
        while not self.leaving.is_set():
            for speaker in list(self.speakers.values()):
                audio = speaker.pause(self.ears, self.clock())
                if audio is not None:
                    self._spawn(self._spoke(speaker.id, audio))
            await asyncio.sleep(TICK_S)

    async def _spoke(self, who: str, audio: np.ndarray) -> None:
        text = (await self.ears.transcribe(audio)).strip()
        if text:
            await self._turn(Turn(ledger.new_id(), who, self.names.get(who, "someone"), text, "spoke"))

    async def _turn(self, turn: Turn) -> None:
        async with await db.connect(self.dsn) as conn:
            await ledger.append(conn, self.id, "call.heard", {"turn_id": turn.id, "who": turn.who, "name": turn.name,
                                "via": turn.via, "text": turn.text})  # fmt: skip
        self.lines.append(f"{turn.name} ({turn.via}): {turn.text}")
        if turn.name == settings.operator_name and LEAVE.search(turn.text):
            self.leave("Tom asked Valor to leave")
            return
        if self.current is not None and self.current.who == turn.who:
            self.current = None  # they went on: what Valor was answering is stale
        actions: list[tuple[str, dict[str, Any]]] = []
        sentences: asyncio.Queue = asyncio.Queue()
        drafting = asyncio.create_task(self._draft(turn, actions, sentences))
        label = await self._decide(turn)
        if label not in ("asked_valor", "useful_post") or self.left:
            self.unsaid = True
            drafting.cancel()
            return
        self.current = turn
        spoken = turn.via == "spoke" and label == "asked_valor"
        written = []
        while (sentence := await sentences.get()) is not None:
            if self.current is not turn:
                self.unsaid = True
                drafting.cancel()
                return
            if spoken:
                self._prefetch(sentence)
                self.says.put_nowait((turn, self._n(turn.id), sentence))
            else:
                written.append(sentence)
        for name, args in actions:
            if name == "ask_worker":
                self.questions.put_nowait(str(args["question"]).strip())
            else:
                self.posts.put_nowait(
                    Post(turn.id, str(args["readback"]).strip(), True, list(args["settles"]))
                )
        if written:
            self.posts.put_nowait(Post(turn.id, " ".join(written), True))

    async def _news(self) -> str:
        """What the front model has not heard yet, up to the turn it is
        asked about: the call's lines since its last message, who is in it,
        the open decisions when they changed, and whether its last reply
        went unsaid."""
        parts = []
        if self.unsaid:
            parts.append("(Your last reply was not said: you stayed silent.)")
            self.unsaid = False
        since, self.told = self.lines[self.told : -1], len(self.lines)
        if since:
            parts.append("Said in the call since your last reply:\n" + "\n".join(since))
        open_now = await self._decisions()
        if open_now != self.open_told:
            parts.append(open_now)
            self.open_told = open_now
        parts.append(f"In the call: {', '.join(sorted(set(self.names.values()))) or 'only you'}.")
        if not await self.worker_ready():
            parts.append(
                "Your worker's copy of the project is still being set up: asked anything, say you need a moment."
            )
        return "\n\n".join(parts)

    async def _draft(self, turn: Turn, actions: list, sentences: asyncio.Queue) -> None:
        said = f"{turn.name} {'said' if turn.via == 'spoke' else 'typed in the chat'}: {turn.text}"
        try:
            async for sentence in self.front.draft(await self._news(), said, actions):
                if turn.via == "spoke":
                    self._prefetch(sentence)
                sentences.put_nowait(sentence)
        except Exception as exc:  # noqa: BLE001  a draft that fails is silence, and the call goes on
            _log(f"call {self.id}: the front model failed: {exc!r}")
        finally:
            sentences.put_nowait(None)

    async def _decide(self, turn: Turn) -> str | None:
        """`meet.speak`'s label; None (silence) on abstain, failure or stop."""
        inputs = {
            "said": f"{turn.name} {turn.via}: {turn.text}",
            "recent": "\n".join(self.lines[-RECENT:-1]) or "(nothing before it)",
            "people": ", ".join(self.names.values()) or "no one else",
        }
        try:
            judged = await self.judge(inputs, turn.id)
        except tasks.TaskStopped:
            return None
        return (judged.payload.get("picked") or {}).get("turn") if judged.answered else None

    async def _decisions(self) -> str:
        async with await db.connect(self.dsn) as conn:
            return await decisions(conn, self.project)

    def _n(self, turn_id: str) -> int:
        self.said[turn_id] = self.said.get(turn_id, -1) + 1
        return self.said[turn_id]

    # -- speaking and posting --

    def _prefetch(self, text: str) -> None:
        """Voice `text` ahead of saying it: a spoken turn's draft is voiced
        as it streams, before `meet.speak` decides, so a sentence Valor
        says is ready when its turn comes. The latest `VOICED_MOST` kept."""
        if text not in self._spoken:
            self._spoken[text] = asyncio.get_running_loop().run_in_executor(self._tts, self.voice, text)
            while len(self._spoken) > VOICED_MOST:
                self._spoken.pop(next(iter(self._spoken)))

    async def says_lane(self) -> None:
        """One sentence spoken at a time, never holding up a post."""
        while True:
            turn, n, text = await self.says.get()
            if self.current is turn:
                await self._effect("meet.say", {"text": text, "turn": turn.id}, f"meet/{turn.id}/{n}/say")
            self._spoken.pop(text, None)

    async def posts_lane(self) -> None:
        """Each post in the call's chat, then its copy to the thread it was
        asked in, which the thread's bridge sends while the next post goes."""
        from core import bridge

        while True:
            post = await self.posts.get()
            turn = post.turn or ledger.new_id()
            done = []
            for i, part in enumerate(bridge.split_text("meet", post.text)):
                payload = {"text": part, **({"settles": post.settles} if post.settles and i == 0 else {})}
                n = self._n(turn)
                out = await self._effect("meet.send_message", payload, f"meet/{turn}/{n}/post")
                if out.kind == "done":
                    done.append(part)
            if post.copy and done:
                kind, payload, chat = self._copy(" ".join(done))
                await self._effect(kind, payload, f"meet/{turn}/{n}/copy", chat)

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

    async def _say(self, payload: dict[str, Any]) -> dict[str, Any]:
        turn = self.current
        if turn is None or turn.id != payload.get("turn"):
            raise broker.Failed("stale: newer speech in the call came first")
        voiced = self._spoken.get(payload["text"])
        pcm, rate = (
            await voiced if voiced is not None else await asyncio.to_thread(self.voice, payload["text"])
        )
        if self.current is not turn:
            raise broker.Failed("stale: newer speech in the call came first")
        self.playing = turn
        try:
            got = await self.page.play(pcm, rate)
        finally:
            self.playing = None
        spoken = _cut(payload["text"], got["played"] / got["total"]) if got.get("cut") else payload["text"]
        if got.get("cut"):
            self.current = None
        self.lines.append(f"Valor (spoke): {spoken}")
        return {
            "spoken_text": spoken,
            "cut": bool(got.get("cut")),
            "played_s": round(float(got["played"]), 2),
        }

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not await self.page.post(payload["text"]):
            raise broker.Failed("the call's chat box was not there")
        self.lines.append(f"Valor (typed): {payload['text']}")
        return {"posted": payload["text"]}

    # -- the worker --

    async def work(self) -> None:
        """The worker's questions, one at a time; a late answer is posted
        naming its question, or after leaving goes in the call's report."""
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
            elif self.left:
                self.late.append({"question": question, "answer": answer})
            else:
                self.posts.put_nowait(Post(None, f'On "{question}": {answer}', True))

    # -- the call's life --

    def leave(self, reason: str, *, hard: bool = False) -> None:
        if not self.leaving.is_set():
            self.reason, self.hard = reason, hard
            self.leaving.set()

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
            for p in state["people"]:
                if p["me"]:
                    self.me = p["id"]
                    continue
                self.names[p["id"]] = p["name"] or "someone"
                if p["id"] not in seen:
                    seen.add(p["id"])
                    async with await db.connect(self.dsn) as conn:
                        await ledger.append(
                            conn, self.id, "call.participant", {"id": p["id"], "name": p["name"]}
                        )
            now = self.clock()
            alone = None if others else alone or now
            if seen and alone is not None and now - alone >= EMPTY_S:
                self.leave("everyone else had left")
            elif not seen and now - start >= ALONE_S:
                self.leave("no one else came")
            up = self.socket is None or self.socket.up
            down = None if up else down or now
            if down is not None and now - down >= SOCKET_S:
                self.leave("the call's audio was lost")
            await asyncio.sleep(STATE_S)

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
            async for note in listener.notifies():
                if note.channel == tasks.STOP_CHANNEL and note.payload == self.id:
                    self.leave("the call was stopped", hard=True)
                    return
                said = json.loads(note.payload) if note.channel == "valor_events" else {}
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
            if status == "lobby" and waited >= LOBBY_S:
                return "no one let Valor in"
            if status not in ("lobby",) and waited >= JOIN_S:
                return "Valor could not get through Meet's join screen"
            if self.leaving.is_set():
                return self.reason
            await asyncio.sleep(1)

    async def run(self) -> str:
        """The call, from joining to its end; the reason it ended."""
        lanes = []
        try:
            lanes = [
                asyncio.create_task(c)
                for c in (self.orders(), self.says_lane(), self.posts_lane(), self.work())
            ]
            await self.front.start()
            await self.page.open(self.info["url"], self.socket.port if self.socket else 0)
            why = await self.joined()
            if why is not None:
                self.leave(why)
            else:
                await self.page.ready()
                self.posts.put_nowait(Post("join", JOIN_LINE, False))
                lanes += [asyncio.create_task(self.listen()), asyncio.create_task(self.watch())]
                await self.leaving.wait()
        except Exception as exc:  # noqa: BLE001  the call ends, saying why
            _log(f"call {self.id}: {exc!r}")
            self.leave(f"Valor's call process failed: {exc!r}")
        self.left = True
        with contextlib.suppress(Exception):
            await self.page.leave()
        with contextlib.suppress(Exception):
            await self.front.close()
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
        async with await db.connect(self.dsn) as conn:
            await end(conn, self.id, self.reason, late=self.late, unanswered=unanswered)
        return self.reason


def _log(text: str) -> None:
    print(f"{datetime.now(UTC).isoformat(timespec='seconds')} {text}", flush=True)
