"""Valor in a Meet call, its own process (`core/call.py`), driven without
Meet or Google: a page that records what it streams and posts, and a fake
Gemini whose Live sessions record every call and flag sent to them and
answer with scripted server messages (google-genai's own types).

What is said aloud is one `meet.say` per Live turn, requested at its first
audio chunk, its audio held until it is released and dropped when it is
refused, settled with what was said (cut where it was cut); a written
answer is a `meet.send_message`, copied to the thread once done. Notes
(who is speaking, who is here, the worker's answers) ask for no reply; a
typed line does, and is answered in the chat, never aloud. Each turn's
usage is charged by modality. A Live session that goes away is resumed
with its handle; one that cannot be is one line in the chat and a leave.
After the call Gemini summarises the recording, kept whole in Yudame
Tools' meeting store."""

import asyncio
import base64
import contextlib
import dataclasses
import inspect
import json
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import psycopg
import pytest
from google.genai import types

from core import bridge, broker, call, db, ledger, persona, spending, tasks
from core.settings import settings
from tests import bridges, test_meet
from tests.test_kernel import at
from tests.test_meet import CODE, TOM, a_call

pytestmark = pytest.mark.spend(usd=0)
team = test_meet.team  # the project and its signed-in call profile, a fixture
run = asyncio.run
LOGAN, TOMS = "devices/2", "devices/1"
FIXTURES = Path(__file__).parent / "fixtures" / "meetings"


class Page:
    """Meet's page as the call drives it: what it streamed (each chunk's
    bytes and whether it began a turn) and posted. `ahead` is the seconds
    a stream reports still queued; a stop reports `played` seconds."""

    profile = None  # this Mac's, signed in (`test_meet.team`)

    def __init__(self):
        self.status = "in"
        self.people = [{"id": "devices/9", "name": "Valor Engels", "me": True},
                       {"id": LOGAN, "name": "Logan", "me": False}, {"id": TOMS, "name": TOM, "me": False}]  # fmt: skip
        self.posts: list[str] = []
        self.streams: list[tuple[int, bool]] = []
        self.stops = 0
        self.ahead, self.played = 0.0, 0.0
        self.post_ok = True
        self.left = False

    async def open(self, url, port):
        self.url = url

    async def join(self):
        pass

    async def renew(self, back):
        return False

    async def ready(self):
        pass

    async def state(self):
        return {"status": self.status, "people": self.people}

    async def post(self, text):
        self.posts.append(text)
        return self.post_ok

    async def stream(self, pcm, rate, fresh):
        assert rate == call.OUT_RATE
        self.streams.append((len(pcm), fresh))
        return self.ahead

    async def stop(self):
        self.stops += 1
        return self.played

    async def leave(self):
        self.left = True

    async def close(self):
        pass


class Session:
    """A Live session: what was sent to it, and an inbox of server
    messages `receive` yields a turn at a time, as the SDK's does."""

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []
        self.inbox: asyncio.Queue = asyncio.Queue()

    async def send_realtime_input(self, **kw):
        self.sent.append(("realtime", kw))

    async def send_client_content(self, **kw):
        self.sent.append(("content", kw))

    async def send_tool_response(self, **kw):
        self.sent.append(("tool", kw))

    async def receive(self):
        while True:
            m = await self.inbox.get()
            if isinstance(m, Exception):
                raise m
            yield m
            if m.server_content and m.server_content.turn_complete:
                return

    def contents(self) -> list[tuple[str, bool]]:
        """Each text sent as content, with its `turn_complete`."""
        return [
            (kw["turns"]["parts"][0]["text"], kw["turn_complete"]) for k, kw in self.sent if k == "content"
        ]

    def audio_bytes(self) -> int:
        return sum(len(kw["audio"].data) for k, kw in self.sent if k == "realtime")


class Gemini:
    """Gemini as the call uses it: each `connect` a new `Session` (or, while
    `fail` is above 0, a refusal); `summarise` records what it was given."""

    def __init__(self, notes=None):
        self.sessions: list[Session] = []
        self.configs: list = []
        self.fail = 0
        self.notes = notes or {"topic": "Launch plan", "summary": "Shipping is Monday, after PR 12.",
                               "decisions": ["Ship Monday"], "actions": [{"task": "Book QA", "owner": "Logan"}],
                               "markdown": "# The call\n\nShip PR #12 and ENG-633."}  # fmt: skip
        self.summarised: list[tuple[Path, str, bytes]] = []
        self.preps: list[str] = []

    @contextlib.asynccontextmanager
    async def connect(self, config):
        self.configs.append(config)
        if self.fail:
            self.fail -= 1
            raise ConnectionError("refused")
        session = Session()
        self.sessions.append(session)
        yield session

    async def summarise(self, audio, transcript, prep):
        self.summarised.append((audio, transcript, audio.read_bytes()[:12]))
        self.preps.append(prep)
        if isinstance(self.notes, Exception):
            raise self.notes
        return self.notes, {"prompt_token_count": 1000, "response_token_count": 200,
                            "prompt_tokens_details": [{"modality": "AUDIO", "token_count": 800},
                                                      {"modality": "TEXT", "token_count": 200}]}  # fmt: skip


def sc(**kw) -> types.LiveServerMessage:
    return types.LiveServerMessage(server_content=types.LiveServerContent(**kw))


def audio(seconds=0.5) -> types.LiveServerMessage:
    data = b"\x01\x00" * int(seconds * call.OUT_RATE)
    return sc(
        model_turn=types.Content(parts=[types.Part(inline_data=types.Blob(data=data, mime_type="audio/pcm"))])
    )


def said(text):
    return sc(output_transcription=types.Transcription(text=text))


def heard(text):
    return sc(input_transcription=types.Transcription(text=text))


USAGE = {"prompt_token_count": 600, "response_token_count": 60, "thoughts_token_count": 40,
         "prompt_tokens_details": [{"modality": "TEXT", "token_count": 350}, {"modality": "AUDIO", "token_count": 220}],
         "response_tokens_details": [{"modality": "AUDIO", "token_count": 60}]}  # fmt: skip


def done(usage=USAGE):
    m = sc(turn_complete=True)
    if usage is not None:
        m.usage_metadata = types.UsageMetadata.model_validate(usage)
    return m


def tool(name, **args):
    return types.LiveServerMessage(tool_call=types.LiveServerToolCall(
        function_calls=[types.FunctionCall(id=f"fc-{name}", name=name, args=args)]))  # fmt: skip


def resume(handle):
    return types.LiveServerMessage(
        session_resumption_update=types.LiveServerSessionResumptionUpdate(new_handle=handle, resumable=True)
    )


def go_away():
    return types.LiveServerMessage(go_away=types.LiveServerGoAway(time_left="5s"))


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(call, "STATE_S", 0.05)
    monkeypatch.setattr(call, "MIX_S", 0.02)


async def until(check, s=5.0):
    for _ in range(int(s / 0.02)):
        if check():
            return True
        await asyncio.sleep(0.02)
    return False


async def a_call_of(
    dsn, team, page, gemini, *, worker=None, ready=True, reader=None, preparer=None, starts=None, target="new"
):
    async def ready_now():
        return ready

    async def no_worker(question):
        raise AssertionError("no worker in this test")

    if target == "new":
        brief, session_id = await a_call(dsn, team)
    else:
        brief = await test_meet.asking_session(dsn, team)
        session_id = (await test_meet.join(dsn, brief, [], target=target)).result["session_id"]
    async with await db.connect(dsn) as conn:
        info = await call.marker(conn, session_id)
    if starts:
        info["starts"] = starts
    the = call.Call(session_id, info, page, dsn=dsn, gemini=gemini, worker=worker or no_worker,
                    worker_ready=ready_now, reader=reader, preparer=preparer, project=team.project, store=str(team.profile.parent / "store" / "Meetings"))  # fmt: skip
    return brief, the


async def in_call(dsn, team, page=None, gemini=None, **kw):
    """A call joined, with its Live session open."""
    page, gemini = page or Page(), gemini or Gemini()
    brief, the = await a_call_of(dsn, team, page, gemini, **kw)
    running = asyncio.create_task(the.run())
    assert await until(lambda: gemini.sessions and the.session is not None)
    assert await until(lambda: call.JOIN_LINE in page.posts)
    return SimpleNamespace(brief=brief, the=the, page=page, gemini=gemini, running=running,
                           session=gemini.sessions[-1])  # fmt: skip


async def settle(the, s=3.0):
    """Until every post and job is idle."""
    for _ in range(int(s / 0.02)):
        await asyncio.sleep(0.02)
        if the.posts.empty() and not [j for j in the.jobs if not j.done()] and not the.asking:
            await asyncio.sleep(0.05)
            if the.posts.empty() and not the.jobs:
                return
    raise AssertionError("the call never settled")


async def push(session, *messages):
    for m in messages:
        session.inbox.put_nowait(m)
    await asyncio.sleep(0.1)


async def effects(dsn, session_id, *, link=False) -> list[dict]:
    """The session's effects in order: type, payload, request id, and how
    each stands; the link's post in the asking thread only with `link`."""
    rows = await bridges.rows(dsn, session_id)
    held = [r["payload"] for r in rows if r["type"] == "effect.held"
            and (link or r["payload"].get("request_id") != "meet/link/copy")]  # fmt: skip
    out = {r["payload"]["effect_id"]: r["payload"] for r in rows if r["type"] == "effect.outcome"}
    refused = {r["payload"]["effect_id"]: r["payload"] for r in rows if r["type"] == "effect.refused"}
    return [
        {"type": h["action_type"], "payload": h["payload"], "request_id": h.get("request_id"), "target": h["target"],
         "kind": out[h["effect_id"]]["kind"] if h["effect_id"] in out else "refused" if h["effect_id"] in refused
         else "released", "result": (out.get(h["effect_id"]) or {}).get("result")}
        for h in held
    ]  # fmt: skip


async def finish(c, reason="done"):
    c.the.leave(reason)
    return await asyncio.wait_for(c.running, 15)


def loud(seconds=0.5):
    return np.full(int(seconds * 48_000), 0.2, np.float32)


# -- speaking -------------------------------------------------------------------


def test_a_reply_is_one_say_its_audio_held_until_released_and_settled_with_what_was_said(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        c.the.hear(LOGAN, loud(), 48_000)
        await until(lambda: c.the.energy.get(LOGAN))  # mixed, as Live's transcript lags the audio
        await push(c.session, heard("Valor, when do we ship?"))
        await push(c.session, audio(), audio(), said("We ship Monday."), audio(), done())
        assert await until(lambda: c.page.streams and c.the.out is None and not c.the.replies)
        await settle(c.the)
        await finish(c)
        return c, await effects(dsn, c.the.id), await bridges.rows(dsn, c.the.id)

    c, done_, rows = run(go())
    (say,) = [e for e in done_ if e["type"] == "meet.say"]
    assert (
        say["kind"] == "done"
        and say["result"]["spoken_text"] == "We ship Monday."
        and not say["result"]["cut"]
    )
    assert say["payload"] == {"turn": say["request_id"].split("/")[1]} and say["request_id"].endswith("/say")
    # Held until released, then played as one fresh chunk; the rest after it, never fresh.
    assert c.page.streams[0][1] is True and all(not fresh for _, fresh in c.page.streams[1:])
    assert sum(n for n, _ in c.page.streams) == 3 * 2 * int(0.5 * call.OUT_RATE)
    (heard_,) = [r["payload"] for r in rows if r["type"] == "call.heard"]
    assert (
        heard_["name"] == "Logan" and heard_["text"] == "Valor, when do we ship?" and heard_["via"] == "spoke"
    )
    assert not [e for e in done_ if e["type"] == "slack.send_message"]  # speech is not copied


def test_lives_no_speech_turn_is_silence_and_its_markup_is_never_in_what_was_said(dsn, team, quick):
    """Live answers side talk it keeps out of with a turn whose transcript
    is "<no speech>{pause}" and some audio (the live test, 2026-11-05)."""

    async def go():
        c = await in_call(dsn, team)
        await push(c.session, sc(model_turn=audio(0.3).server_content.model_turn,
                                 output_transcription=types.Transcription(text="<no speech>{pause}")),
                   audio(0.5), done())  # fmt: skip
        await settle(c.the)
        quiet = list(c.page.streams)
        await push(c.session, said("..."), audio(0.7), done())
        await push(c.session, audio(0.9), done())  # near silence, no transcript at all
        await settle(c.the)
        quiet += list(c.page.streams)
        await push(c.session, said("Sure,"), audio(0.3), said(" {pause} Monday."), done())
        assert await until(lambda: len(c.page.streams) > len(quiet) and not c.the.replies)
        await settle(c.the)
        await finish(c)
        return quiet, await effects(dsn, c.the.id)

    quiet, done_ = run(go())
    assert quiet == []
    assert [e["result"]["spoken_text"] for e in done_ if e["type"] == "meet.say"] == ["Sure, Monday."]


def test_a_refused_say_drops_its_audio(dsn, team, quick, monkeypatch):
    async def refuse(conn, action):
        return "not now"

    monkeypatch.setitem(
        bridge.DECLARED, "meet.say", dataclasses.replace(bridge.DECLARED["meet.say"], check=refuse)
    )

    async def go():
        c = await in_call(dsn, team)
        await push(c.session, audio(), said("Something."), audio(), done())
        await settle(c.the)
        await finish(c)
        return c, await bridges.rows(dsn, c.the.id)

    c, rows = run(go())
    assert c.page.streams == []
    refused = [
        r["payload"]
        for r in rows
        if r["type"] == "effect.refused" and r["payload"]["action_type"] == "meet.say"
    ]
    assert [r["reason"] for r in refused] == ["not now"]


def test_the_person_answered_speaking_over_valor_cuts_him_off_and_side_talk_does_not(dsn, team, quick):
    """Live's own barge-in cut Valor off for side talk (the live test,
    2026-11-05); the call cuts him off only for the person he answers, and
    not for a quarter second of sound they don't go on from (three cuts in
    Tom's call, 2026-11-06)."""

    async def go():
        page = Page()
        page.ahead, page.played = 30.0, 1.0
        c = await in_call(dsn, team, page)
        await talk(c.the, LOGAN, 0.5)
        await push(c.session, heard("Valor, tell me about the launch."))
        await push(c.session, audio(1.0), said("one two three four five six"), audio(1.0), done())
        assert await until(lambda: c.the.out is not None)  # the say released: the reply is playing
        await talk(c.the, TOMS, 0.6)  # side talk
        side = c.page.stops
        await talk(c.the, LOGAN, 0.25)  # a sound from the person answered, not taken further
        await talk(c.the, TOMS, 0.1)
        brief = c.page.stops
        await talk(c.the, LOGAN, 0.6)  # the person answered
        await settle(c.the)
        await finish(c)
        return c, side, brief, await effects(dsn, c.the.id)

    c, side, brief, done_ = run(go())
    assert side == 0 and brief == 0 and c.page.stops == 1
    (say,) = [e for e in done_ if e["type"] == "meet.say"]
    assert say["result"] == {"spoken_text": "one two three", "cut": True, "played_s": 1.0}
    assert ("[note] Logan cut you off; they heard only: one two three", False) in c.session.contents()


@pytest.mark.parametrize("case", ["cut in the turn", "cut in the tail", "a short sound", "session lost"])
def test_what_cuts_valor_off_while_lives_turn_runs_is_sent_again_once_it_is_over(dsn, team, quick, case):
    """Live hears nothing sent while its turn runs, and runs on up to 30 s
    after the call cut it off (Tom's calls, 2026-11-19: what settled an item
    had no transcript). What the person said from the cut, with the
    `BARGE_S` that cut him, is sent once the turn is over, before the next
    frame; a cut in the tail, a sound too short to cut, and a session lost
    meanwhile send nothing again."""

    async def go():
        page = Page()
        page.ahead, page.played = 30.0, 1.0
        c = await in_call(dsn, team, page)
        await talk(c.the, LOGAN, 0.5)
        await push(c.session, heard("Valor, tell me about the launch."))
        await push(c.session, audio(1.0), said("one two three four five six"), audio(1.0))
        if case == "cut in the tail":
            await push(c.session, done())
        assert await until(lambda: c.the.out is not None)
        await talk(c.the, LOGAN, 0.25 if case == "a short sound" else 0.6)
        await talk(c.the, LOGAN, 0.4 if case != "a short sound" else 0.0)
        kept = None if c.the.kept is None else len(c.the.kept)
        before = c.session.audio_bytes()
        await asyncio.sleep(0.2)
        early = c.session.audio_bytes() - before
        if case == "session lost":
            c.gemini.fail = 0
            await push(c.session, ConnectionError("1006 lost"))
            assert await until(lambda: len(c.gemini.sessions) == 2 and c.the.session is c.gemini.sessions[1])
        elif case != "cut in the tail":
            await push(c.session, done())
            c.the.playing_until = 0.0  # the uncut reply's audio played out
        await asyncio.sleep(0.3)
        after = c.gemini.sessions[-1].audio_bytes() - (before + early if len(c.gemini.sessions) == 1 else 0)
        await settle(c.the)
        await finish(c)
        return c, kept, early, after

    c, kept, early, after = run(go())
    normal = int(0.5 * call.RATE * 2)  # 0.3 s of frames, and some
    if case == "cut in the turn":
        assert c.page.stops == 1 and kept >= int(0.8 * call.RATE)  # the barge and what followed it
        assert early < normal and after >= kept * 2  # held while the turn ran, then all of it
    elif case == "session lost":
        assert kept and after < normal  # the next session is not sent what the lost one was owed
    else:
        assert kept is None and after < normal
    assert c.the.kept is None and c.page.stops == (0 if case == "a short sound" else 1)


@pytest.mark.parametrize("quiet_between", [True, False])
def test_valor_answers_who_spoke_last_when_his_reply_begins_before_their_words_are_heard(
    dsn, team, quick, quiet_between
):
    """Live began a reply before its transcript of the question, so the
    call took the side-talker before it as the person answered, and their
    words over the reply cut Valor off (the live test, 2026-11-06); and a
    reply begun while the side-talker's stretch was still closing went to
    them too."""

    async def go():
        page = Page()
        page.ahead, page.played = 30.0, 1.0
        c = await in_call(dsn, team, page)
        await talk(c.the, TOMS, 0.6)
        await push(c.session, heard("Sam, did the invoice come?"))
        if quiet_between:
            await asyncio.sleep(call.QUIET_S + 0.2)
        await talk(c.the, LOGAN, 0.6)
        await push(c.session, audio(1.0))  # the reply, ahead of the question's transcript
        await push(c.session, heard("Valor, tell me about the launch."))
        await push(c.session, said("one two three four five six"), audio(1.0), done())
        assert await until(lambda: c.the.out is not None)  # the say released: the reply is playing
        await talk(c.the, TOMS, 0.6)  # side talk over the reply
        side = c.page.stops
        await talk(c.the, LOGAN, 0.6)  # the person answered
        await settle(c.the)
        await finish(c)
        return c, side

    c, side = run(go())
    assert side == 0 and c.page.stops == 1
    assert ("[note] Logan cut you off; they heard only: one two three", False) in c.session.contents()


async def talk(the, who, seconds):
    """`who` speaking for `seconds`, as Meet sends it."""
    for _ in range(round(seconds / 0.05)):
        the.hear(who, loud(0.05), 48_000)
        await asyncio.sleep(0.05)


def test_valors_own_audio_is_not_sent_and_silence_is(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        c.the.hear("devices/9", loud(), 48_000)  # Valor's own track
        await asyncio.sleep(0.3)
        quiet = c.session.audio_bytes()
        mixed = [kw["audio"] for k, kw in c.session.sent if k == "realtime"]
        await finish(c)
        return quiet, mixed

    quiet, mixed = run(go())
    assert quiet > 0 and all(b.mime_type == "audio/pcm;rate=16000" for b in mixed)
    assert all(not any(b.data) for b in mixed)


def test_each_stretch_heard_is_credited_to_whoever_was_loudest_and_a_change_of_speaker_is_a_note(
    dsn, team, quick
):
    async def go():
        c = await in_call(dsn, team)
        for _ in range(10):
            c.the.hear(LOGAN, loud(0.05), 48_000)
            await asyncio.sleep(0.05)
        await push(c.session, heard("I think Monday."))
        for _ in range(12):
            c.the.hear(TOMS, loud(0.05), 48_000)
            await asyncio.sleep(0.05)
        await asyncio.sleep(call.LAG_S + 0.2)
        await push(c.session, heard("Tuesday is better."))
        await asyncio.sleep(call.QUIET_S + 0.3)
        await finish(c)
        return c, await bridges.rows(dsn, c.the.id)

    c, rows = run(go())
    stretches = [(r["payload"]["name"], r["payload"]["text"]) for r in rows if r["type"] == "call.heard"]
    assert stretches == [("Logan", "I think Monday."), (TOM, "Tuesday is better.")]
    notes = c.session.contents()
    assert ("[note] Logan is speaking.", False) in notes and (f"[note] {TOM} is speaking.", False) in notes


def test_who_is_here_is_a_note_and_with_one_other_person_all_they_say_is_to_valor(dsn, team, quick):
    """Tom's "Hello" in a call with only him got no answer (2026-11-05)."""

    async def go():
        page = Page()
        page.people = page.people[:1] + page.people[2:]  # only Tom
        c = await in_call(dsn, team, page)
        await until(lambda: any("Only" in t for t, _ in c.session.contents()))
        page.people = [*Page().people, {"id": "devices/7", "name": "Valor Engels", "me": False}]
        await until(lambda: any("In the call with you" in t for t, _ in c.session.contents()))
        await finish(c)
        return c.session.contents()

    notes = run(go())
    assert (
        f"[note] Only {TOM} is here with you: everything they say is to you, so answer it.",
        False,
    ) in notes
    assert (
        (
            f"[note] In the call with you: Logan, {TOM}, Valor Engels (another device). Open the next item only "
            "when someone asks for it. Answer only what names you or follows straight on from your answer; a "
            "question without your name is to the person they were talking with."
        ),
        False,
    ) in notes
    assert all(not complete for text, complete in notes if text.startswith("[note]"))


@pytest.mark.parametrize("alone", [True, False])
def test_with_one_other_person_a_silence_after_their_words_asks_for_the_answer(dsn, team, quick, alone):
    """Live kept silent on "Hello." with only one person in the call (the
    live test, 2026-11-05), as Tom's "Hello" went unanswered that day."""

    async def go():
        page = Page()
        if alone:
            page.people = page.people[:1] + page.people[2:]  # only Tom
        c = await in_call(dsn, team, page)
        await until(lambda: c.the.present)
        await talk(c.the, TOMS, 0.4)
        await push(c.session, heard("Hello."))
        await asyncio.sleep(call.QUIET_S + 0.2)
        await push(c.session, said("..."), audio(0.5), done())
        await push(c.session, said("..."), audio(0.5), done())  # asked once only
        await settle(c.the)
        await finish(c)
        return [(t, done_) for t, done_ in c.session.contents() if "said to you" in t]

    asked = run(go())
    assert asked == (
        [(f'[note] {TOM}, the only one here, said to you: "Hello.". Answer it.', True)] if alone else []
    )


def test_a_turn_of_silence_during_the_heard_rows_write_still_asks_for_the_answer(
    dsn, team, quick, monkeypatch
):
    """The answer was owed only once the heard row was written, so Live's
    turn of silence finishing during that write asked nothing (a loaded
    suite, 2026-11-06; in a call, two seconds late)."""
    slow = call.Call._heard

    async def heard_slowly(self, *args):
        await asyncio.sleep(0.5)
        await slow(self, *args)

    monkeypatch.setattr(call.Call, "_heard", heard_slowly)

    async def go():
        page = Page()
        page.people = page.people[:1] + page.people[2:]  # only Tom
        c = await in_call(dsn, team, page)
        await until(lambda: c.the.present)
        await talk(c.the, TOMS, 0.4)
        await push(c.session, heard("Hello."))
        await asyncio.sleep(call.QUIET_S + 0.1)  # the quiet has closed the stretch; its row is being written
        await push(c.session, said("..."), audio(0.5), done())
        await asyncio.sleep(0.6)
        await finish(c)
        return [t for t, _ in c.session.contents() if "said to you" in t]

    assert run(go()) == [f'[note] {TOM}, the only one here, said to you: "Hello.". Answer it.']


def test_with_one_other_person_no_turn_at_all_after_their_words_asks_for_the_answer(
    dsn, team, quick, monkeypatch
):
    monkeypatch.setattr(call, "UNANSWERED_S", 0.3)

    async def go():
        page = Page()
        page.people = page.people[:1] + page.people[2:]  # only Tom
        c = await in_call(dsn, team, page)
        await until(lambda: c.the.present)
        await talk(c.the, TOMS, 0.4)
        await push(c.session, heard("Hello."))
        await until(lambda: any("said to you" in t for t, _ in c.session.contents()), s=call.QUIET_S + 2)
        await finish(c)
        return [t for t, _ in c.session.contents() if "said to you" in t]

    assert len(run(go())) == 1


def test_with_one_other_person_speech_live_gave_no_transcript_of_still_asks_for_the_answer(
    dsn, team, quick, monkeypatch
):
    """Live gave no transcript and no turn for a short "Hello." in three
    live tests, 2026-11-05."""
    monkeypatch.setattr(call, "UNANSWERED_S", 0.3)

    async def go():
        page = Page()
        page.people = page.people[:1] + page.people[2:]  # only Tom
        c = await in_call(dsn, team, page)
        await until(lambda: c.the.present)
        await talk(c.the, TOMS, 0.5)
        await until(
            lambda: any("spoke to you" in t for t, _ in c.session.contents()), s=call.QUIET_S + call.LAG_S + 2
        )
        await finish(c)
        return [(t, d) for t, d in c.session.contents() if "spoke to you" in t]

    assert run(go()) == [
        (f"[note] {TOM}, the only one here, spoke to you, words you did not catch: answer them.", True)
    ]


def test_in_a_group_a_stretch_naming_valor_with_no_turn_asks_whether_it_was_to_him_once(
    dsn, team, quick, monkeypatch
):
    """2026-11-18: asked by name twice in a group call, Valor
    said nothing. With others talking on after it, a stretch naming Valor
    (misheard as "Val" too) with no turn `NAMED_S` later asks Live, once;
    side talk naming no one asks nothing."""
    monkeypatch.setattr(call, "NAMED_S", 0.3)
    monkeypatch.setattr(call, "QUIET_S", 0.5)

    async def go():
        c = await in_call(dsn, team)
        await until(lambda: len(c.the.present) == 2)
        await talk(c.the, LOGAN, 0.4)
        await push(c.session, heard("The landing page converts better than in October."))
        await asyncio.sleep(call.QUIET_S + 0.6)
        side = [t for t, _ in c.session.contents() if "said your name" in t]
        await talk(c.the, LOGAN, 0.4)
        await push(c.session, heard("Val, can you pull up the signups from last week?"))
        await asyncio.sleep(call.QUIET_S + 0.2)
        await talk(c.the, TOMS, 1.0)  # someone talks on: it still asks
        await until(lambda: any("said your name" in t for t, _ in c.session.contents()))
        await asyncio.sleep(0.5)
        await finish(c)
        return side, [(t, d) for t, d in c.session.contents() if "said your name" in t]

    side, asked = run(go())
    assert side == []
    assert asked == [
        (('[note] Logan said your name: "Val, can you pull up the signups from last week?". If they asked you '
          "something, answer it now; if they only spoke about you, stay silent."), True)
    ]  # fmt: skip


def test_in_a_group_another_persons_stretch_after_a_named_one_leaves_it_waiting(
    dsn, team, quick, monkeypatch
):
    """The review, 2026-11-18: Logan asks Valor by name, then Tom's own
    words close as a stretch before `NAMED_S` (both at their real lengths); the named request still
    gets its note, once, and Tom's (not naming Valor) gets none."""

    async def go():
        c = await in_call(dsn, team)
        await until(lambda: len(c.the.present) == 2)
        await talk(c.the, LOGAN, 1.0)
        await push(c.session, heard("Valor, can you pull up the signups from last week?"))
        await talk(c.the, TOMS, 0.4)  # a change of speaker: Logan's stretch closes after LAG_S
        assert await until(lambda: any("signups from last week" in line for line in c.the.lines))
        await push(c.session, heard("And the paid ones too, I think."))
        await talk(c.the, TOMS, 0.6)
        assert await until(lambda: any("paid ones" in line for line in c.the.lines))
        early = [t for t, _ in c.session.contents() if "said your name" in t]
        await until(lambda: any("said your name" in t for t, _ in c.session.contents()))
        await asyncio.sleep(call.NAMED_S)
        await finish(c)
        return early, [t for t, _ in c.session.contents() if "said your name" in t]

    early, asked = run(go())
    assert early == []
    assert asked == [
        f"[note] {call.NAMED_NOTE.format(name='Logan', text='Valor, can you pull up the signups from last week?')}"
    ]


def test_in_a_group_a_named_stretch_live_answered_asks_nothing_and_the_log_has_each_note_and_turn(
    dsn, team, quick, monkeypatch, capsys
):
    """Nothing recorded why Live stayed silent (2026-11-18): the call's log
    now has each note told to Live and each turn of Live's, begun and complete."""
    monkeypatch.setattr(call, "NAMED_S", 0.3)

    async def go():
        c = await in_call(dsn, team)
        await until(lambda: len(c.the.present) == 2)
        await talk(c.the, LOGAN, 0.4)
        await push(c.session, heard("Valor, what ships first?"))
        await asyncio.sleep(call.QUIET_S + 0.1)
        await push(c.session, audio(), said("The import ships first."), done())
        await settle(c.the)
        await asyncio.sleep(0.6)
        await finish(c)
        return [t for t, _ in c.session.contents() if "said your name" in t]

    assert run(go()) == []
    log = capsys.readouterr().out
    assert (
        "told Live: '[note] Logan is speaking.'" in log and "told Live: '[note] In the call with you" in log
    )
    assert "Live began turn" in log and "answering Logan" in log
    assert "complete: spoke, 'The import ships first.'" in log or "complete: unsaid" in log


def test_a_turn_that_only_calls_a_tool_asks_for_no_second_answer(dsn, team, quick, monkeypatch):
    """The live Meet test, 2026-11-18: with one person there, a turn that
    only called `ask` was followed at once by a note asking for the answer."""
    monkeypatch.setattr(call, "QUIET_S", 0.5)
    monkeypatch.setattr(call, "UNANSWERED_S", 0.3)

    async def go():
        page = Page()
        page.people = page.people[:1] + page.people[2:]  # only Tom
        c = await in_call(dsn, team, page)
        await until(lambda: c.the.present)
        await talk(c.the, TOMS, 0.4)
        await push(c.session, heard("Valor, look up the error rate."))
        await push(c.session, tool("ask", question="What is the error rate?"), done())
        await asyncio.sleep(call.QUIET_S + call.UNANSWERED_S + 0.5)
        await finish(c)
        return [t for t, _ in c.session.contents() if "said to you" in t]

    assert run(go()) == []


def test_live_hears_each_person_only_while_they_speak():
    """Faint talk under `SPEAKING` from someone not speaking kept Live's
    end of turn from coming (tests/test_live_call_voice.py, 2026-11-18): it
    is left out of what Live hears; a speaker's quiet tail within `HOLD_S` is
    kept; the recording keeps everyone's sound."""
    n, spoke_at = call.RATE // 25, {}
    faint, voiced = np.full(n, 0.004, np.float32), np.full(n, 0.2, np.float32)
    heard, whole, loud = call.mixed({"a": voiced, "b": faint}, n, spoke_at, 0.0)
    assert np.allclose(heard, 0.2) and np.allclose(whole, 0.204) and loud["b"] < call.SPEAKING <= loud["a"]
    heard, _, _ = call.mixed({"a": faint, "b": faint}, n, spoke_at, call.HOLD_S / 2)
    assert np.allclose(heard, 0.004)  # a's tail
    heard, whole, _ = call.mixed({"a": faint, "b": faint}, n, spoke_at, call.HOLD_S + 0.1)
    assert not heard.any() and np.allclose(whole, 0.008)  # the recording keeps everyone


# -- the chat, the worker, decisions -------------------------------------------------


def test_a_typed_line_asks_for_a_reply_answered_in_the_chat_never_aloud(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        c.the.chat(LOGAN, "Valor, where is the invoice?")
        assert await until(
            lambda: ("[typed by Logan] Valor, where is the invoice?", True) in c.session.contents()
        )
        await push(
            c.session,
            audio(),
            tool("post", text="It is in the ledger."),
            said("It is in the ledger."),
            done(),
        )
        await push(
            c.session, audio(), said("I posted it."), done()
        )  # after its post: neither said nor posted
        await settle(c.the)
        c.the.chat(TOMS, "And the deck?")
        assert await until(lambda: (f"[typed by {TOM}] And the deck?", True) in c.session.contents())
        await push(c.session, audio(), said("In Drive."), done())  # answered aloud: posted instead
        await settle(c.the)
        await finish(c)
        return c, await effects(dsn, c.the.id)

    c, done_ = run(go())
    assert ("[typed by Logan] Valor, where is the invoice?", True) in c.session.contents()
    assert c.page.streams == [] and not [e for e in done_ if e["type"] == "meet.say"]
    assert c.page.posts[1:] == ["It is in the ledger.", "In Drive."]
    post = next(
        e
        for e in done_
        if e["type"] == "meet.send_message" and e["payload"]["text"] == "It is in the ledger."
    )
    copy = next(e for e in done_ if e["type"] == "slack.send_message")
    assert post["kind"] == "done" and copy["target"] == team.chat
    assert copy["payload"] == {"text": "It is in the ledger.", "reply_to": at(1)}
    (response,) = [kw for k, kw in c.session.sent if k == "tool"]
    assert response["function_responses"][0].response == {"result": call.POSTING}


def test_a_typed_line_is_answered_in_the_chat_past_a_tool_call_until_someone_speaks(dsn, team, quick):
    """Live asked the worker for a typed question, then said the rest of
    its answer aloud, in a turn of its own (the live test, 2026-11-05)."""

    async def go():
        c = await in_call(dsn, team, worker=_answers("Monday."))
        c.the.chat(LOGAN, "Valor, when is the launch?")
        assert await until(
            lambda: ("[typed by Logan] Valor, when is the launch?", True) in c.session.contents()
        )
        await push(c.session, tool("ask", question="When is the launch?"), done())
        await push(c.session, audio(), said("I will check."), done())
        await settle(c.the)
        typed_streams = list(c.page.streams)
        await talk(c.the, LOGAN, 0.4)
        await push(c.session, heard("Thanks, Valor."))
        await asyncio.sleep(call.QUIET_S + 0.2)
        await push(c.session, audio(), said("You're welcome."), done())
        await settle(c.the)
        await finish(c)
        return c, typed_streams, await effects(dsn, c.the.id)

    c, typed_streams, done_ = run(go())
    assert typed_streams == []
    assert "I will check." in c.page.posts and 'On "When is the launch?": Monday.' in c.page.posts
    assert [e["result"]["spoken_text"] for e in done_ if e["type"] == "meet.say"] == ["You're welcome."]


def _answers(text):
    async def worker(question):
        return text

    return worker


def unposted(session) -> list[str]:
    """The turns telling Live the chat failed, each completed."""
    return [t for t, complete in session.contents() if t.startswith("[not posted]") and complete]


def test_a_failed_post_gets_no_copy_and_live_is_told_to_say_it_aloud(dsn, team, quick):
    """Every post of a call failed and Live was told each went, so it said
    nothing (the Captain, 2026-11-18)."""

    async def go():
        c = await in_call(dsn, team)
        c.page.post_ok = False
        await push(c.session, tool("post", text="First."), done())
        await settle(c.the)
        assert await until(lambda: unposted(c.session))
        await finish(c)
        return c, await effects(dsn, c.the.id)

    c, done_ = run(go())
    assert [e["kind"] for e in done_ if e["payload"].get("text") == "First."] == ["failed"]
    assert not [e for e in done_ if e["type"] == "slack.send_message"]
    assert unposted(c.session) == [call.UNPOSTED.format(text="First.")]


def test_a_worker_answer_the_chat_failed_is_said_aloud_never_noted_as_posted(dsn, team, quick):
    async def go():
        page = Page()
        c = await in_call(dsn, team, page, worker=_answers("Tuesday."))
        page.post_ok = False
        await push(c.session, tool("ask", question="When did the import ship?"), done())
        assert await until(lambda: unposted(c.session))
        await settle(c.the)
        await finish(c)
        return c

    c = run(go())
    assert unposted(c.session) == [call.UNPOSTED.format(text='On "When did the import ship?": Tuesday.')]
    assert not [t for t, _ in c.session.contents() if "(posted in the chat)" in t]


def test_the_join_line_failing_before_live_opens_reaches_the_session_once_it_opens(dsn, team, quick):
    """The join line is how the room hears it is recorded; it is queued
    before the Live session opens."""

    class Gated(Gemini):
        def __init__(self):
            super().__init__()
            self.gate = asyncio.Event()

        @contextlib.asynccontextmanager
        async def connect(self, config):
            await self.gate.wait()
            async with super().connect(config) as session:
                yield session

    async def go():
        page, gemini = Page(), Gated()
        page.post_ok = False
        _, the = await a_call_of(dsn, team, page, gemini)
        running = asyncio.create_task(the.run())
        assert await until(lambda: the.unposted)  # failed, with no session yet
        gemini.gate.set()
        assert await until(lambda: gemini.sessions and unposted(gemini.sessions[0]))
        c = SimpleNamespace(the=the, running=running)
        await finish(c)
        return gemini.sessions[0], await effects(dsn, the.id)

    session, done_ = run(go())
    assert unposted(session) == [call.UNPOSTED.format(text=call.JOIN_LINE)]
    assert [e["kind"] for e in done_ if e["payload"].get("text") == call.JOIN_LINE] == ["failed"]


def test_a_post_failing_while_valor_speaks_is_told_once_the_reply_is_over_in_one_turn(dsn, team, quick):
    """Told mid-reply, Live would cut its own answer off; a post the chat
    split in two, both parts failed, is one turn."""

    async def go():
        page = Page()
        c = await in_call(dsn, team, page)
        page.ahead, page.post_ok = 1.5, False
        await push(c.session, audio(), said("Monday, after QA."), done())
        assert await until(lambda: c.the.out is not None)
        c.the.posts.put_nowait(call.Post(None, "a" * 400 + "\n" + "b" * 400, True))
        assert await until(lambda: c.the.unposted)
        during = (c.the.out is not None, unposted(c.session))
        assert await until(lambda: unposted(c.session))
        after = c.the.out
        await finish(c)
        return c, during, after

    c, during, after = run(go())
    assert during == (True, []) and after is None
    (turn,) = unposted(c.session)
    assert "a" * 400 in turn and "b" * 400 in turn


def test_after_a_typed_answer_the_chat_failed_the_next_reply_is_spoken(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        c.page.post_ok = False
        c.the.chat(LOGAN, "Valor, where is the invoice?")
        typed = ("[typed by Logan] Valor, where is the invoice?", True)
        assert await until(lambda: typed in c.session.contents())
        await push(c.session, audio(), said("It is in the ledger."), done())  # a typed line's reply: posted
        assert await until(lambda: unposted(c.session))
        await push(c.session, audio(), said("It is in the ledger."), done())  # the turn saying it aloud
        assert await until(lambda: c.page.streams)
        await finish(c)
        return c

    c = run(go())
    assert unposted(c.session) == [call.UNPOSTED.format(text="It is in the ledger.")]
    assert c.page.streams


def test_an_ask_returns_at_once_and_its_answer_after_a_resumption_is_posted_and_noted(dsn, team, quick):
    """Asked aloud, the worker's answer is posted and noted, kept, for Valor
    to give when he next speaks: never a turn of its own (2026-11-19: said
    at a pause, it spoke over Tom answering Valor's own question)."""

    async def go():
        gate = asyncio.Event()

        async def worker(question):
            await gate.wait()
            return "The import, on Tuesday."

        c = await in_call(dsn, team, worker=worker)
        await push(c.session, resume("h1"), tool("ask", question="What did we ship?"), done())
        # Under a loaded parallel suite the reply can take longer than push's 0.1 s.
        assert await until(lambda: [k for k, _ in c.session.sent if k == "tool"])
        (response,) = [kw for k, kw in c.session.sent if k == "tool"]
        await push(c.session, go_away())
        assert await until(lambda: len(c.gemini.sessions) == 2 and c.the.session is c.gemini.sessions[1])
        gate.set()
        assert await until(lambda: any("asked aloud" in t for t, _ in c.the.session.contents()))
        await asyncio.sleep(call.QUIET_S + 0.3)  # a quiet pause asks nothing more
        await settle(c.the)
        await finish(c)
        return c, response, await effects(dsn, c.the.id)

    c, response, done_ = run(go())
    assert response["function_responses"][0].response["result"].startswith("asked")
    assert c.gemini.configs[1].session_resumption.handle == "h1"
    assert 'On "What did we ship?": The import, on Tuesday.' in c.page.posts
    noted = c.gemini.sessions[1].contents()
    say = call.SAY_ANSWER.format(question="What did we ship?", answer="The import, on Tuesday.")
    assert [x for x in noted if "Tuesday" in x[0]] == [(f"[note] {say}", False)]
    assert c.the.told == [say]  # kept for a fresh session
    assert [e["type"] for e in done_ if e["type"] != "meet.send_message"] == ["slack.send_message"]


@pytest.mark.parametrize("when", ["valor speaking", "the other speaking", "a pause"])
def test_an_answer_asked_aloud_is_never_a_turn_of_its_own(dsn, team, quick, when):
    """Tom's two calls, 2026-11-19: the answer's turn began 0.45 s after
    Valor's own question and Tom, answering it, cut it off. Arriving while
    Valor's reply plays, while the other person talks, or in a quiet pause,
    it is one note asking no reply, and no turn is asked for it."""

    async def go():
        gate = asyncio.Event()

        async def worker(question):
            await gate.wait()
            return "The import, on Tuesday."

        page = Page()
        c = await in_call(dsn, team, page, worker=worker)
        await push(c.session, tool("ask", question="What did we ship?"), done())
        assert await until(lambda: [k for k, _ in c.session.sent if k == "tool"])
        if when == "valor speaking":
            page.ahead = 2.0
            await push(c.session, audio(), said("While that runs, the next item."), done())
            assert await until(lambda: c.the.out is not None)
        gate.set()
        for _ in range(15 if when == "the other speaking" else 0):
            c.the.hear(LOGAN, loud(0.05), 48_000)
            await asyncio.sleep(0.04)
        assert await until(lambda: any("asked aloud" in t for t, _ in c.session.contents()))
        await asyncio.sleep(call.QUIET_S + 0.5)
        await settle(c.the)
        await finish(c)
        return c

    c = run(go())
    say = call.SAY_ANSWER.format(question="What did we ship?", answer="The import, on Tuesday.")
    assert [x for x in c.session.contents() if "Tuesday" in x[0]] == [(f"[note] {say}", False)]


def test_an_answer_asked_aloud_whose_post_fails_is_said_once_as_unposted(dsn, team, quick):
    """An answer is noted only once it is in the chat; when the chat fails
    it, the unposted turn says it, and nothing says it twice."""

    async def worker(question):
        return "The import, on Tuesday."

    async def go():
        c = await in_call(dsn, team, worker=worker)
        c.page.post_ok = False
        await push(c.session, tool("ask", question="What did we ship?"), done())
        assert await until(lambda: any(t.startswith("[not posted]") for t, _ in c.session.contents()))
        await asyncio.sleep(call.QUIET_S + 0.3)
        await finish(c)
        return c.session.contents()

    told = run(go())
    assert [t for t, _ in told if "Tuesday" in t and t.startswith("[not posted]")]
    assert not [t for t, _ in told if "asked aloud" in t or t.startswith("[note] Your worker answered")]


def test_a_settle_posts_the_readback_with_its_settles_and_copies_it(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        await push(
            c.session, tool("settle", ids=["t1/q1", "t2/q2"], readback="Ship Monday; drop IE."), done()
        )
        await settle(c.the)
        await finish(c)
        return await effects(dsn, c.the.id)

    done_ = run(go())
    (post,) = [e for e in done_ if e["type"] == "meet.send_message" and e["payload"].get("settles")]
    assert post["payload"] == {"text": "Ship Monday; drop IE.", "settles": [
        {"task_id": "t1", "question_id": "q1"}, {"task_id": "t2", "question_id": "q2"}]}  # fmt: skip
    assert any(
        e["type"] == "slack.send_message" and e["payload"]["text"] == "Ship Monday; drop IE." for e in done_
    )


def _schedules(session) -> list[tuple[str, object]]:
    return [(r.name, r.scheduling) for k, kw in session.sent if k == "tool" for r in kw["function_responses"]]


SILENT, WHEN_IDLE = types.FunctionResponseScheduling.SILENT, types.FunctionResponseScheduling.WHEN_IDLE


@pytest.mark.parametrize("complete_first", [False, True])
def test_a_tool_called_after_valor_spoke_is_answered_silently_so_he_says_one_reply(
    dsn, team, quick, complete_first
):
    """Valor read Tom's answer back, posted it, and Live said a second reply
    to the tool's response (#118690 then #118695, 2026-11-16). The post came
    while the turn's sound played, before Live's transcript of it (#118659,
    4.76 s with no words); and the turn may be complete before its tool call."""

    async def go():
        page = Page()
        page.ahead = 30.0  # still playing
        c = await in_call(dsn, team, page)
        await talk(c.the, TOMS, 0.4)
        await push(c.session, heard("It's only for your use."))
        if complete_first:
            await push(c.session, audio(), said("Got it, for my use only."), done())
            assert await until(lambda: c.the.out is not None)
            await push(c.session, tool("post", text="For Valor's use only."), done(None))
        else:
            voiced = (np.full(int(0.5 * call.OUT_RATE), 3000, "<i2")).tobytes()  # sound, no transcript yet
            await push(c.session, sc(model_turn=types.Content(parts=[types.Part(inline_data=types.Blob(
                data=voiced, mime_type="audio/pcm"))])))  # fmt: skip
            assert await until(lambda: c.the.out is not None)
            await push(c.session, tool("post", text="For Valor's use only."), said("Got it, for my use only."),
                       audio(), done())  # fmt: skip
        assert await until(lambda: _schedules(c.session))
        assert await until(lambda: "For Valor's use only." in c.page.posts)
        await finish(c)  # cuts the reply still playing
        return c, await effects(dsn, c.the.id)

    c, done_ = run(go())
    assert _schedules(c.session) == [("post", SILENT)]
    assert len([e for e in done_ if e["type"] == "meet.say"]) == 1
    assert "For Valor's use only." in c.page.posts


def test_a_settle_read_back_aloud_is_answered_silently(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        await push(c.session, audio(), said("Ship Monday, then."), tool("settle", ids=["t1/q1"], readback="Ship Monday."),
                   done())  # fmt: skip
        await settle(c.the)
        await finish(c)
        return c

    assert _schedules(run(go()).session) == [("settle", SILENT)]


def test_a_tool_result_with_something_to_say_is_said(dsn, team, quick):
    """Only an acknowledgement in a turn that has spoken is silent: a tool
    called before any words, an ask the worker is not ready for, an answer
    already in, and a call missing its arguments are each said; a lookup's
    result is the answer, and stays blocking."""

    async def go():
        c = await in_call(dsn, team, worker=_answers("Tuesday."), ready=False)
        await push(c.session, tool("ask", question="When did we ship?"), audio(), said("I'll check."), done())
        await settle(c.the)
        await push(c.session, audio(), said("Checking."), tool("ask", question="When did we ship?"), done())
        await settle(c.the)
        await push(c.session, audio(), said("Posting."), tool("post"), done())
        await push(c.session, audio(), said("One moment."), tool("open_decisions"), done())
        await settle(c.the)
        await finish(c)
        return c

    c = run(go())
    assert _schedules(c.session) == [("ask", WHEN_IDLE), ("ask", WHEN_IDLE), ("post", WHEN_IDLE),
                                     ("open_decisions", None)]  # fmt: skip
    responses = [
        r.response["result"] for k, kw in c.session.sent if k == "tool" for r in kw["function_responses"]
    ]
    assert "needs a moment" in responses[0] and responses[1].startswith("already asked")
    assert responses[2] == "not done: post needs its arguments"


def test_only_the_tools_that_acknowledge_are_declared_non_blocking():
    config = call.live_config("acme", "", None, sheet=True)
    behaviours = {f.name: f.behavior for f in config.tools[0].function_declarations}
    assert {n for n, b in behaviours.items() if b == types.Behavior.NON_BLOCKING} == {
        "ask",
        "post",
        "settle",
        "leave",
    }
    assert {n for n, b in behaviours.items() if b is None} == {"open_decisions", "person", "backlog"}


def test_a_short_sound_valor_answers_asks_for_no_answer_after_his_reply(dsn, team, quick, monkeypatch):
    """A short sound Live gave no transcript of, answered by Valor, then
    asked him for an answer to "words you did not catch" once his reply
    was over: "Could you please say that again?" after his own answer
    (#117782 then #117788, 2026-11-16)."""
    monkeypatch.setattr(call, "UNANSWERED_S", 0.3)

    async def go():
        page = Page()
        page.people = page.people[:1] + page.people[2:]  # only Tom
        c = await in_call(dsn, team, page)
        await until(lambda: c.the.present)
        await talk(c.the, TOMS, 0.5)
        await push(c.session, audio(), said("The next item is the summary."), done())
        await settle(c.the)
        await asyncio.sleep(call.QUIET_S + call.LAG_S + 1)
        await finish(c)
        return c

    assert not [t for t, _ in run(go()).session.contents() if "spoke to you" in t]


def test_the_live_session_is_told_valors_voice_tools_and_how_to_wait_its_turn():
    config = call.live_config("acme", "Logan wants the launch date.", None)
    assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Alnilam"
    assert config.proactivity.proactive_audio is True
    assert config.realtime_input_config.activity_handling == types.ActivityHandling.NO_INTERRUPTION
    assert config.context_window_compression.trigger_tokens == call.CALL_CONTEXT
    assert config.realtime_input_config.automatic_activity_detection is None  # Live's own
    assert [f.name for f in config.tools[0].function_declarations] == [
        "ask",
        "post",
        "open_decisions",
        "settle",
        "person",
        "leave",
    ]
    assert (
        "Logan wants the launch date." in config.system_instruction
        and "[typed by NAME]" in config.system_instruction
    )
    # Tom, 2026-11-12: anyone may ask Valor to leave, goodbyes all round end it, a scheduled end does not.
    text = " ".join(config.system_instruction.split())
    assert "Call `leave` when anyone in the call asks you to leave" in text and "said goodbye" in text
    assert "Never leave because the meeting's scheduled time has passed" in text
    assert "Tom" not in config.tools[0].function_declarations[-1].description


def test_the_call_speaks_by_the_personas_in_a_call_section_and_in_english(tmp_path, monkeypatch):
    """Tom's first full Live call (2026-11-06): bare recommendations, a challenged
    question asked again, "offline" with no where, items that were not his, and a
    switch of language. The call's instruction carries the persona's own section,
    byte for byte, so the call and every turn read one text."""
    from core import persona

    real = persona.section(settings.persona_dir, "In a call")
    flat = " ".join(real.split())
    assert "say what is being decided, why now and what it changes, then your recommendation" in flat
    assert "Never open with a bare recommendation" in flat
    assert "Put to the person only what only they can decide" in flat
    assert "Your own work is done before the call and said as done" in flat
    assert "a blocker on someone else is status in a line (who, since when) and asks nothing" in flat
    assert "ask your worker to check it, or drop the question" in flat and "Never ask it again" in flat
    assert "Taking something offline says where it goes" in flat
    # English always: a short phrase is no switch (¿Qué tal?, answered in Spanish, 2026-11-16), nor are whole
    # sentences in another language; only an explicit ask is (Tom, 2026-11-16).
    assert "Answer in English even when someone speaks another language, a phrase or whole sentences" in flat
    assert "Switch only when someone explicitly asks you to speak another language" in flat
    voice = " ".join(persona.render(settings.persona_dir).split())
    assert (
        "Every conversation is in English" in voice and "explicitly asks you to use another language" in voice
    )
    assert real in persona.render(settings.persona_dir)  # every turn reads the same text

    # One source: a changed section is what the call is told, braces and all.
    shutil.copytree(settings.persona_dir, tmp_path / "persona")
    conduct = tmp_path / "persona" / "conduct.md"
    conduct.write_text(conduct.read_text().replace("### In a call\n", "### In a call\n\nSay {this} first.\n"))
    monkeypatch.setattr(
        call, "settings", dataclasses.replace(settings, persona_dir=str(tmp_path / "persona"))
    )
    told = call.live_config("acme", "notes", None).system_instruction
    assert persona.section(tmp_path / "persona", "In a call") in told and "Say {this} first." in told
    assert told.startswith("You are Valor Engels, in a live Google Meet call")
    # The mechanics defer to it: no second copy of the conduct in the template.
    for gone in ("with your recommendation, and let them choose", "Speak in short, plain",
                 "do not steer the call", "take it offline with Tom"):  # fmt: skip
        assert gone not in call.INSTRUCTIONS


def test_what_valor_hears_is_transcribed_as_english_and_the_reply_language_is_left_to_the_persona():
    config = call.live_config("acme", "", None)
    assert config.input_audio_transcription.language_codes == ["en-US"]
    assert config.speech_config.language_code is None  # a hard pin would forbid "unless clearly another"


def test_a_missing_persona_section_is_unreadable(tmp_path):
    from core import persona

    shutil.copytree(settings.persona_dir, tmp_path / "persona")
    with pytest.raises(persona.PersonaUnreadable, match="In a meeting"):
        persona.section(tmp_path / "persona", "In a meeting")


# -- the backlog ------------------------------------------------------------------


def test_a_call_whose_project_has_a_sheet_is_offered_backlog_and_told_when_to_use_it():
    """Tom, 2026-11-13: a fast lookup of the Sheet's items, answered in the person's terms."""
    config = call.live_config("acme", "", None, sheet=True)
    assert [f.name for f in config.tools[0].function_declarations][-1] == "backlog"
    text = " ".join(config.system_instruction.split())
    assert (
        "call `backlog` at once: with `about` set to how they described the item, in their own words" in text
    )
    assert "never its id or its title as the Sheet words it" in text and "ask which they mean" in text
    assert "`backlog`" not in call.live_config("acme", "", None).system_instruction


FAKE_SHEET = """import json, sys
with open(sys.argv[0] + ".argv", "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[-1] == "fail":
    sys.exit("gws sheets spreadsheets values batchGet failed: 403")
if sys.argv[1] == "read":
    print(json.dumps([{"id": "P-005", "lane": "bet", "rank": "", "item": "Entity memory", "value": "medium",
                       "appetite": "large", "due": "", "status": "shaped", "blocked by": "", "issue": "x",
                       "_rest": []}]))
else:
    print(json.dumps({"matches": [{"id": "P-005", "appetite": "large", "pitch": {"job": "remember people"},
                                   "blocked": [], "score": 2}]}))
"""


def test_backlog_reads_the_sheet_the_projects_spec_names(dsn, team, quick, tmp_path, monkeypatch):
    fake = tmp_path / "sheet.py"
    fake.write_text(FAKE_SHEET)
    monkeypatch.setattr(call, "SHEET_PY", fake)
    spec = Path(settings.projects_dir) / f"{team.project}.toml"
    spec.write_text(spec.read_text() + f'rooms."slack:{team.chat}" = "Product. The backlog Sheet: '
                    'https://docs.google.com/spreadsheets/d/SHEET-1_x/edit, kept here."\n')  # fmt: skip

    async def go():
        c = await in_call(dsn, team)
        await push(c.session, tool("backlog", about="what's the appetite on the memory item?"), done())
        await push(c.session, tool("backlog"), done())
        await push(c.session, tool("backlog", about="fail"), done())
        assert await until(lambda: len([k for k, _ in c.session.sent if k == "tool"]) == 3)
        await finish(c)
        return c

    c = run(go())
    assert "call `backlog`" in c.gemini.configs[0].system_instruction
    one, whole, failed = [
        kw["function_responses"][0].response["result"] for k, kw in c.session.sent if k == "tool"
    ]
    assert json.loads(one)["matches"][0]["appetite"] == "large"
    # The whole queue in its order, with only what an overview needs.
    assert json.loads(whole) == [{"id": "P-005", "lane": "bet", "rank": "", "item": "Entity memory",
                                  "value": "medium", "appetite": "large", "due": "", "status": "shaped",
                                  "blocked by": ""}]  # fmt: skip
    assert failed.startswith("the Sheet could not be read: ") and "403" in failed
    argv = [json.loads(line) for line in (tmp_path / "sheet.py.argv").read_text().splitlines()]
    assert argv == [["lookup", "SHEET-1_x", "what's the appetite on the memory item?"], ["read", "SHEET-1_x"],
                    ["lookup", "SHEET-1_x", "fail"]]  # fmt: skip


def test_a_call_whose_project_has_no_sheet_has_no_backlog(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        await push(c.session, tool("backlog", about="the memory item"), done())
        assert await until(lambda: [k for k, _ in c.session.sent if k == "tool"])
        await finish(c)
        return c

    c = run(go())
    assert "`backlog`" not in c.gemini.configs[0].system_instruction
    (response,) = [kw for k, kw in c.session.sent if k == "tool"]
    assert response["function_responses"][0].response["result"] == "not done: backlog needs its arguments"


# -- metering ---------------------------------------------------------------------


def test_each_turn_is_opened_then_charged_by_modality(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        await push(c.session, audio(), said("Hi."), done())
        await push(c.session, done())  # a turn of silence is billed too
        await settle(c.the)
        await finish(c)
        return await bridges.rows(dsn, c.the.id)

    rows = run(go())
    opened = [
        r["payload"] for r in rows if r["type"] == "gateway.opened" and r["payload"]["route"] == "gemini-live"
    ]
    charged = [
        r["payload"]
        for r in rows
        if r["type"] == "gateway.charged" and r["payload"].get("model") == "gemini-3.8-live"
    ]
    price = spending.gemini_price("gemini-3.8-live")
    assert len(opened) == len(charged) == 2 and {o["call_id"] for o in opened} == {
        c["call_id"] for c in charged
    }
    assert all(c["usd_micros"] == spending.gemini_cost(USAGE, price) for c in charged)
    assert opened[0]["holder"] is None or opened[0]["holder"].startswith("run:")
    assert opened[0]["estimate_usd_micros"] == spending.gemini_worst_case(price)


def test_gemini_prices_each_modality_and_the_worst_case_covers_a_full_context():
    price = spending.gemini_price("gemini-3.8-live")
    # 350 text and 220 audio in, 30 unlisted (at the audio rate); 60 audio out; 40 thinking, as text.
    assert spending.gemini_cost(USAGE, price) == -(
        -(350 * 750_000 + 250 * 3_000_000 + 60 * 12_000_000 + 40 * 4_500_000) // 1_000_000
    )
    full = {"prompt_token_count": call.CALL_CONTEXT, "response_token_count": call.CALL_OUTPUT_MOST,
            "prompt_tokens_details": [{"modality": "AUDIO", "token_count": call.CALL_CONTEXT}],
            "response_tokens_details": [{"modality": "AUDIO", "token_count": call.CALL_OUTPUT_MOST}]}  # fmt: skip
    assert spending.gemini_cost(full, price) <= spending.gemini_worst_case(price)
    assert spending.gemini_price("gemini-9-unknown")["unpriced"] is True


def test_a_stopped_task_refuses_a_turn_and_the_call_leaves(dsn, team):
    async def go():
        _, the = await a_call_of(dsn, team, Page(), Gemini())
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, the.id, reason="enough", by="tom", via="chat")
        await the._open("turn")
        return the

    the = run(go())
    assert the.leaving.is_set() and the.hard and the.metered is None


# -- when Live fails ----------------------------------------------------------------


def test_two_failed_reconnects_post_one_line_and_leave(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        c.gemini.fail = 5
        await push(c.session, ConnectionError("dropped"))
        reason = await asyncio.wait_for(c.running, 15)
        return c, reason

    c, reason = run(go())
    assert reason == "Valor lost its voice connection"
    assert c.page.posts.count(call.LOST_LINE) == 1 and len(c.gemini.configs) == 3


def test_a_dropped_session_that_reconnects_carries_on(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        await push(c.session, resume("h7"), ConnectionError("dropped"))
        assert await until(lambda: len(c.gemini.sessions) == 2 and c.the.session is c.gemini.sessions[1])
        await push(c.gemini.sessions[1], audio(), said("Still here."), done())
        await settle(c.the)
        await finish(c)
        return c, await effects(dsn, c.the.id)

    c, done_ = run(go())
    assert c.gemini.configs[1].session_resumption.handle == "h7" and call.LOST_LINE not in c.page.posts
    assert [e["result"]["spoken_text"] for e in done_ if e["type"] == "meet.say"] == ["Still here."]


def test_a_refused_handle_opens_a_fresh_session_told_the_call_so_far(dsn, team, quick):
    """Tom's invited call (2026-11-12): an hour in, a resumed session dropped
    ("Requested entity already exists") and its handle was refused twice
    ("session not found or expired"), so the call left. A refused handle
    is now a fresh session, told who is here and what was said."""

    async def go():
        c = await in_call(dsn, team)
        c.page.people = c.page.people[:2]  # Logan alone with Valor
        for _ in range(8):
            c.the.hear(LOGAN, loud(0.05), 48_000)
            await asyncio.sleep(0.05)
        await push(c.session, heard("Valor, what ships first?"))
        await push(c.session, audio(), said("The import ships first."), done())
        await settle(c.the)
        assert await until(lambda: any("The import ships first." in line for line in c.the.lines))
        c.gemini.fail = 1  # the handle is refused once
        await push(c.session, resume("h7"), ConnectionError("1008 Requested entity already exists"))
        assert await until(lambda: len(c.gemini.sessions) == 2 and c.the.session is c.gemini.sessions[1])
        await push(c.gemini.sessions[1], audio(), said("Still here."), done())
        await settle(c.the)
        await finish(c)
        return c, await effects(dsn, c.the.id)

    c, done_ = run(go())
    first, refused, fresh = c.gemini.configs
    assert refused.session_resumption.handle == "h7" and fresh.session_resumption.handle is None
    told = fresh.system_instruction
    assert "Valor, what ships first?" in told and "The import ships first." in told
    assert "Only Logan is here with you" in told and "do not greet them again" in told
    assert "The call so far" not in first.system_instruction
    assert call.LOST_LINE not in c.page.posts
    assert [e["result"]["spoken_text"] for e in done_ if e["type"] == "meet.say"][-1] == "Still here."


def test_a_fresh_session_is_told_the_workers_answers_the_steers_and_the_newest_prep(dsn, team, quick):
    """2026-11-18: a session opened fresh at 14:04 was told only what was
    heard and said, not what the worker had answered or prepared."""

    async def go():
        async def worker(question):
            return "Tuesday."

        replies = iter(["Item two: old prep.", "Item two: 0.4 percent of imports fail."])

        async def preparer(prompt):
            return next(replies)

        starts = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        c = await in_call(dsn, team, worker=worker, preparer=preparer, starts=starts)
        c.the.chat(LOGAN, "When did the import ship?")  # typed: its answer is noted, not said
        assert await until(lambda: any(t.startswith("[typed by") for t, _ in c.session.contents()))
        await push(c.session, tool("ask", question="When did the import ship?"), done())
        await push(c.session, tool("settle", ids=["t1/q1"], readback="Ship Monday."), done())
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, c.the.id, "message.steered", {"text": "The figure is 212."})
        assert await until(lambda: len(c.the.told) == 3)
        await settle(c.the)
        c.gemini.fail = 1
        await push(c.session, resume("h7"), ConnectionError("1008 Requested entity already exists"))
        assert await until(lambda: len(c.gemini.sessions) == 2 and c.the.session is c.gemini.sessions[1])
        await settle(c.the)
        await finish(c)
        return c

    c = run(go())
    told = c.gemini.configs[-1].system_instruction
    assert 'Your worker answered "When did the import ship?" (posted in the chat): Tuesday.' in told
    assert call.STEERED.format(text="The figure is 212.") in told
    assert "0.4 percent" in told and "old prep" not in told
    assert not c.gemini.sessions[1].contents() or not any(
        "0.4 percent" in t or "chat session sent" in t for t, _ in c.gemini.sessions[1].contents()
    )  # told once, in the config


def test_a_refused_handle_then_two_failed_fresh_opens_post_one_line_and_leave(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        c.gemini.fail = 5
        await push(c.session, resume("h7"), ConnectionError("dropped"))
        return c, await asyncio.wait_for(c.running, 15)

    c, reason = run(go())
    assert reason == "Valor lost its voice connection" and c.page.posts.count(call.LOST_LINE) == 1
    assert [x.session_resumption.handle for x in c.gemini.configs] == [None, "h7", None, None]


def test_the_call_so_far_is_its_newest_lines_within_its_bound(dsn, team, monkeypatch):
    async def go():
        _, the = await a_call_of(dsn, team, Page(), Gemini())
        return the

    the = run(go())
    assert the.so_far() == ""  # nothing said yet: a plain fresh session
    monkeypatch.setattr(call, "CALL_SO_FAR", 100)
    the.lines = [f"[00:{i:02d}] Logan (spoke): line {i:02d} of the call" for i in range(20)]
    tail = the.so_far().split("\n")[2:]
    assert tail[-1] == the.lines[-1] and len("\n".join(tail)) <= 100 and tail[0] in the.lines
    # The kept notes take at most a quarter, newest first; the transcript has the rest.
    monkeypatch.setattr(call, "CALL_SO_FAR", 200)
    the.told = ["an old answer", "x" * 500, "the newest answer"]
    told = the.so_far().split("\n")[2:]
    assert told[0] == "[note] the newest answer" and "x" * 500 not in the.so_far()
    assert told[-1] == the.lines[-1] and len("\n".join(told)) <= 200 and len(told) > 4


@pytest.mark.parametrize(
    "why,reason",
    [("wrapped up", "everyone wrapped up and said goodbye"), ("asked", "Valor was asked to leave"),
     (None, "Valor left the call")],
)  # fmt: skip
def test_valor_leaves_when_live_calls_leave_for_anyone_and_the_reason_says_why(dsn, team, quick, why, reason):
    """Tom, 2026-11-12: leave when someone asks, or everyone wraps up. The
    model decides from the conversation; Logan is not Tom. The reason tells
    the two apart (2026-11-18: goodbyes were recorded as "asked to leave");
    a leave with no reason still leaves."""

    async def go():
        c = await in_call(dsn, team)
        for _ in range(8):
            c.the.hear(LOGAN, loud(0.05), 48_000)
            await asyncio.sleep(0.05)
        args = {"why": why} if why else {}
        await push(c.session, heard("Thanks everyone, that's all for today. Bye!"), tool("leave", **args))
        return await asyncio.wait_for(c.running, 10)

    assert run(go()) == reason


# -- the say and the post, as the bridge checks them ------------------------------------


def test_a_say_names_its_turn_and_a_post_keeps_its_text_limits():
    async def check(action_type, payload):
        return await bridge.DECLARED[action_type].check(None, broker.Action(action_type, CODE, payload))

    assert run(check("meet.say", {"turn": "t1"})) is None
    assert run(check("meet.say", {"text": "Hello."})) is not None
    assert run(check("meet.send_message", {"text": "Hello."})) is None
    assert "empty" in run(check("meet.send_message", {"text": "  "}))
    assert "over a call's" in run(check("meet.send_message", {"text": "x" * 501}))


# -- after the call ------------------------------------------------------------------


def test_the_store_files_are_what_yudame_tools_writes():
    """Fixtures written by Yudame Tools' own encoders (MeetingStore.save,
    the transcript and summary encoders, NoteFormat and TranscriptMerge)."""
    title = "Launch plan - Logan, Valor"
    segments = [
        {"speaker": "Logan", "start": 0.5, "end": 2.0, "text": "Valor, when do we ship?", "channel": "call"},
        {"speaker": "Valor Engels", "start": 3.1, "end": 4.2, "text": "We ship Monday.", "channel": "mic"},
        {"speaker": "Valor Engels", "start": 4.3, "end": 5.0, "text": "After QA.", "channel": "mic"},
        {"speaker": "Logan", "start": 3605.0, "end": 3610.5, "text": "Great *thanks*", "channel": "call"},
    ]
    summary = {"summary": "Shipping is Monday.", "decisions": ["Ship *Monday*"],
               "actions": [{"task": "Send the [notes]", "owner": "Logan"}, {"task": "Book QA"}]}  # fmt: skip
    start, end = datetime(2026, 11, 5, 10, tzinfo=UTC), datetime(2026, 11, 5, 10, 12, 30, tzinfo=UTC)
    meta = json.loads(call.meta_json(title, start, end, "GMT", segments))
    want = json.loads((FIXTURES / "meta.json").read_text())
    pairs = lambda u: {(u[i], json.dumps(u[i + 1])) for i in range(0, len(u), 2)}
    assert pairs(meta.pop("uploads")) == pairs(want.pop("uploads")) and meta == want
    assert json.loads(json.dumps(segments)) == json.loads((FIXTURES / "transcript.json").read_text())
    assert call.transcript_md(title, segments) == (FIXTURES / "transcript.md").read_text()
    assert call.notes_md(title, summary) == (FIXTURES / "notes.md").read_text()
    assert summary == json.loads((FIXTURES / "summary.json").read_text())


def test_after_the_call_gemini_summarises_the_recording_and_the_folder_appears_in_the_store_whole(
    dsn, team, quick
):
    async def go():
        c = await in_call(dsn, team)
        c.the.hear(LOGAN, loud(), 48_000)
        await until(lambda: c.the.energy.get(LOGAN))  # mixed, as Live's transcript lags the audio
        await push(c.session, heard("Valor, when do we ship?"), audio(), said("Monday."), done())
        await settle(c.the)
        await asyncio.sleep(call.QUIET_S)
        store = c.the.store
        during = list(store.iterdir()) if store.exists() else []
        # Read at the save: the spec's repository and links, edited during the call.
        spec = Path(settings.projects_dir) / f"{team.project}.toml"
        spec.write_text(
            spec.read_text().replace('repo = "unused"', 'repo = "https://github.com/acme/app.git"')
            + 'links = { "ENG-" = "https://linear.app/acme/issue/ENG-" }\n'
        )
        await finish(c)
        rows = await bridges.rows(dsn, c.brief.id)
        return c, during, store, rows, await bridges.rows(dsn, c.the.id)

    c, during, store, rows, call_rows = run(go())
    assert during == []
    (folder,) = store.iterdir()
    title = "Launch plan - Logan, Tom, Valor"
    day = c.the.started_at.astimezone().strftime("%Y-%m-%d")
    assert folder.name == f"{c.the.started_at.astimezone().strftime('%Y-%m-%d %H%M')} {title}"
    doc = folder / f"{day} {title}.md"
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        [
            "audio.m4a",
            "meta.json",
            "transcript.json",
            "transcript.md",
            "summary.json",
            "notes.md",
            doc.name,
        ]
    )
    assert not [p for p in store.parent.iterdir() if p.name.startswith(".valor-call-")]
    meta = json.loads((folder / "meta.json").read_text())
    assert meta["recording"] is False and meta["soundChecked"] and not meta["summaryPending"]
    assert meta["speakers"] == ["Logan", "Valor Engels"] and meta["title"] == title
    assert (folder / "transcript.md").read_text().startswith(f"# {title} - Transcript")
    pr = "[PR 12](https://github.com/acme/app/pull/12)"
    assert json.loads((folder / "summary.json").read_text())["summary"] == f"Shipping is Monday, after {pr}."
    notes = (folder / "notes.md").read_text()
    assert notes.startswith(f"# {title} - Notes") and pr in notes
    assert c.gemini.preps == ["The launch: two decisions open."]
    (got,) = c.gemini.summarised
    _, transcript, head = got
    assert head[4:8] == b"ftyp"  # an m4a
    assert "Logan (spoke): Valor, when do we ship?" in transcript and "Valor (spoke): Monday." in transcript
    assert doc.read_text() == (
        f"# {day} {title}\n\nGoogle Meet {CODE}\n\nShip [PR #12](https://github.com/acme/app/pull/12) and "
        "[ENG-633](https://linear.app/acme/issue/ENG-633).\n"
    )
    (ended,) = [
        r["payload"]
        for r in rows
        if r["type"] == "child.reported" and r["payload"]["about_key"].endswith(":ended")
    ]
    assert str(doc) in ended["text"] and "unchanged" in ended["text"]
    assert any(
        r["type"] == "gateway.charged" and r["payload"].get("model") == "gemini-3.1-pro-preview"
        for r in call_rows
    )


def test_a_call_is_titled_by_its_topic_and_the_first_names_of_those_in_it(dsn, team, quick):
    async def go():
        page = Page()
        page.people += [{"id": "devices/3", "name": "", "me": False},
                        {"id": "devices/4", "name": "Valor Engels", "me": False},
                        {"id": "devices/5", "name": "Logan Reyes", "me": False},
                        {"id": "devices/6", "name": "Mara Ode", "me": False}]  # fmt: skip
        c = await in_call(dsn, team, page=page, gemini=Gemini(notes={
            "topic": "Q4: launch/rollout\nplan", "summary": "s", "decisions": [], "actions": [], "markdown": "m"}))  # fmt: skip
        await until(lambda: "Mara" in c.the.first_names)
        await push(c.session, heard("Hello?"), done())
        await settle(c.the)
        await finish(c)
        return c.the.store

    (folder,) = run(go()).iterdir()
    assert folder.name.endswith(" Q4 launch rollout plan - Logan, Tom, Mara, Valor")
    assert call.title(None, ["Valor"]) == "Valor call - Valor"
    assert call.title("x" * 80, ["Valor"]) == "x" * 60 + " - Valor"


def test_references_become_links_and_nothing_else_does():
    repo, links = "https://github.com/acme/app.git", {"ENG-": "https://linear.app/acme/issue/ENG-"}
    pull, issue = "https://github.com/acme/app/pull/", "https://github.com/acme/app/issues/"
    got = call.linked("PR 12, PR #13, pull request 14, issue 7, issue #8 and ENG-633.", repo, links)
    assert got == (f"[PR 12]({pull}12), [PR #13]({pull}13), [pull request 14]({pull}14), [issue 7]({issue}7), "
                   f"[issue #8]({issue}8) and [ENG-633](https://linear.app/acme/issue/ENG-633).")  # fmt: skip
    kept = (
        "#1 priority, [PR 12](https://x.test/12), <https://x.test/PR3>, https://x.test/a#12, `PR 5`, XENG-1"
    )
    assert call.linked(kept, repo, links) == kept
    assert call.linked("PR 12 and ENG-1", "https://gitlab.com/acme/app", {}) == "PR 12 and ENG-1"
    assert call.linked("PR 12", None, {}) == "PR 12"


def test_the_spec_loads_links_and_refuses_a_malformed_one():
    from core import workspace

    base = {"name": "x", "repo": "r", "kind": "plain", "suite": "true"}
    links = {"ENG-": "https://linear.app/acme/issue/ENG-"}
    assert workspace.Spec.from_dict({**base, "links": links}).links == links
    assert workspace.Spec.from_dict(base).links == {}
    for bad in ({"ENG-": 3}, {"ENG-": "linear.app/x"}, ["ENG-"]):
        with pytest.raises(workspace.Refused, match="links"):
            workspace.Spec.from_dict({**base, "links": bad})


def test_a_call_whose_spec_does_not_load_links_nothing(dsn, team):
    async def go():
        _, the = await a_call_of(dsn, team, Page(), Gemini())
        (Path(settings.projects_dir) / f"{team.project}.toml").write_text("not = [toml")
        broken = the._links()
        the.project = None
        return broken, the._links()

    assert run(go()) == ((None, {}), (None, {}))


def test_a_failed_summary_is_named_in_the_report_and_no_other_is_written(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team, gemini=Gemini(notes=RuntimeError("quota")))
        await push(c.session, heard("Hello?"), done())
        await settle(c.the)
        await finish(c)
        return await bridges.rows(dsn, c.brief.id)

    rows = run(go())
    (ended,) = [
        r["payload"]
        for r in rows
        if r["type"] == "child.reported" and r["payload"]["about_key"].endswith(":ended")
    ]
    assert "Gemini could not summarise it (RuntimeError: quota; the call's files are kept in" in ended["text"]
    assert "write no summary of your own" in ended["text"]
    assert "Its transcript, where any task given in the call is: `valor-record call" in ended["text"]


def test_the_summary_uploads_the_recording_and_sends_it_with_the_transcript(tmp_path):
    class Files:
        def __init__(self):
            self.deleted = []

        async def upload(self, file, config):
            self.file = file
            return SimpleNamespace(name="files/1", state=SimpleNamespace(name="ACTIVE"))

        async def get(self, name):
            raise AssertionError("not processing")

        async def delete(self, name):
            self.deleted.append(name)

    class Models:
        async def generate_content(self, model, contents, config):
            self.model, self.contents = model, contents
            return SimpleNamespace(text=json.dumps({"summary": "s", "decisions": [], "actions": [], "markdown": "m"}),
                                   usage_metadata=types.UsageMetadata(prompt_token_count=10))  # fmt: skip

    files, models = Files(), Models()
    gemini = call.Gemini.__new__(call.Gemini)
    gemini.client = SimpleNamespace(aio=SimpleNamespace(files=files, models=models))
    audio_file = tmp_path / "audio.m4a"
    audio_file.write_bytes(b"x")
    notes, usage = run(gemini.summarise(audio_file, "[0:01] Logan (spoke): hi", "The launch."))
    assert files.file == str(audio_file) and files.deleted == ["files/1"]
    assert models.model == "gemini-3.1-pro-preview" and models.contents[0].name == "files/1"
    assert models.contents[1].endswith(
        "before the call:\nThe launch.\n\nThe transcript:\n[0:01] Logan (spoke): hi"
    )
    assert notes["markdown"] == "m" and usage == {"prompt_token_count": 10}


def test_a_missing_google_key_is_named_and_its_value_never_printed(tmp_path):
    from core import credentials

    with pytest.raises(credentials.MissingKey, match="GOOGLE_API_KEY") as got:
        credentials.read_key(tmp_path / "google-key", call.GOOGLE_KEY, "google-key")
    assert "google-key" in str(got.value)


def test_google_key_reads_the_key_from_1password_into_the_key_file(tmp_path, monkeypatch, capsys):
    """`python -m core google-key` reads `settings.google_key_ref` with
    `op`, on the vault `.env`'s service-account token when the environment
    has none, and prints only whether it wrote it."""
    from core import __main__ as kernel
    from tests import bridges as b

    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "op").write_text(
        '#!/bin/sh\n[ "$1 $2" = "read op://Yudame Secrets/k1/credential" ] && [ "$OP_SERVICE_ACCOUNT_TOKEN" = ops_t ] '
        "&& echo AQ.secret-value && exit 0\nexit 1\n"
    )
    (bin_ / "op").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:/usr/bin:/bin")
    monkeypatch.delenv("OP_SERVICE_ACCOUNT_TOKEN", raising=False)
    (tmp_path / ".env").write_text("OP_SERVICE_ACCOUNT_TOKEN=ops_t\n")
    keyfile = tmp_path / "keys" / "google-key"
    monkeypatch.setattr(type(kernel.settings), "google_keyfile", property(lambda _: str(keyfile)))
    with b.configure(vault_env=str(tmp_path / ".env"), google_key_ref="op://Yudame Secrets/k1/credential"):
        for argv in (["google-key"], ["google-key", "--ref", "op://Yudame Secrets/gone/credential"]):
            monkeypatch.setattr(sys, "argv", ["python -m core", *argv])
            kernel.main()
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "GOOGLE_API_KEY (from op://Yudame Secrets/k1/credential): written",
        "GOOGLE_API_KEY (from op://Yudame Secrets/gone/credential): missing",
    ]
    assert "secret" not in out and oct(keyfile.stat().st_mode & 0o777) == "0o600"
    from core import credentials

    assert credentials.read_key(keyfile, call.GOOGLE_KEY, "google-key") == "AQ.secret-value"


# -- the call's life -----------------------------------------------------------


def test_a_call_that_fails_logs_its_traceback_with_the_missing_files_path(dsn, team, capsys):
    class Missing(Page):
        async def open(self, url, port):
            raise FileNotFoundError(2, "No such file or directory", "/gone/front")

    async def go():
        _, the = await a_call_of(dsn, team, Missing(), Gemini())
        return await asyncio.wait_for(the.run(), 10)

    reason = run(go())
    out = capsys.readouterr().out
    assert reason.startswith("Valor's call process failed: FileNotFoundError")
    assert "Traceback" in out and "/gone/front" in out and "in run" in out


def test_a_stop_leaves_within_seconds_drops_the_workers_question_and_ends_the_call_once(dsn, team, quick):
    async def go():
        hold = asyncio.Event()

        async def worker(question):
            await hold.wait()
            raise tasks.TaskStopped(question)

        c = await in_call(dsn, team, worker=worker)
        c.the.questions.put_nowait("What broke?")
        await asyncio.sleep(0.1)
        start = asyncio.get_running_loop().time()
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, c.the.id, reason="enough", by="tom", via="chat")
        hold.set()
        reason = await asyncio.wait_for(c.running, 10)
        took = asyncio.get_running_loop().time() - start
        return c, reason, took, await bridges.rows(dsn, c.the.id)

    c, reason, took, rows = run(go())
    assert reason == "the call was stopped" and took < 5 and c.page.left
    (ended,) = [r["payload"] for r in rows if r["type"] == "call.ended"]
    assert ended["stopped"] and ended["unanswered"] == ["What broke?"]
    assert not c.gemini.summarised and not c.the.store.exists()  # a stop keeps no recording


def test_valor_leave_from_tom_leaves_and_from_anyone_else_does_not(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        for _ in range(8):
            c.the.hear(LOGAN, loud(0.05), 48_000)
            await asyncio.sleep(0.05)
        await push(c.session, heard("Valor, leave."))
        await asyncio.sleep(call.QUIET_S + 0.3)
        before = c.the.leaving.is_set()
        c.the.chat(TOMS, "Thanks Valor, you can leave now.")
        reason = await asyncio.wait_for(c.running, 10)
        return before, reason, await bridges.rows(dsn, c.the.id)

    before, reason, rows = run(go())
    assert before is False and reason == "Tom asked Valor to leave"
    kinds = [r["type"] for r in rows]
    assert kinds.count("call.ended") == 1 and "task.ended" in kinds


def test_valor_leave_spoken_by_tom_leaves(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        for _ in range(8):
            c.the.hear(TOMS, loud(0.05), 48_000)
            await asyncio.sleep(0.05)
        await push(c.session, heard("Okay Valor, please leave the call."))
        return await asyncio.wait_for(c.running, 10)

    assert run(go()) == "Tom asked Valor to leave"


def test_a_late_worker_answer_goes_in_the_report_once(dsn, team, quick):
    async def go():
        gate = asyncio.Event()

        async def worker(question):
            await gate.wait()
            return "The import."

        c = await in_call(dsn, team, worker=worker)
        c.the.questions.put_nowait("What shipped?")
        await asyncio.sleep(0.1)
        c.page.status = "ended"
        await asyncio.sleep(0.3)
        gate.set()
        await asyncio.wait_for(c.running, 10)
        return c.page, await bridges.rows(dsn, c.brief.id)

    page, rows = run(go())
    assert page.posts == [call.JOIN_LINE]
    (ended,) = [
        r["payload"]
        for r in rows
        if r["type"] == "child.reported" and r["payload"]["about_key"].endswith(":ended")
    ]
    assert ended["text"].count("The import.") == 1 and "the call ended" in ended["text"]


async def ended_alone(dsn, team, page) -> tuple[str, list[dict], list[dict]]:
    """A call on `page` run to its end: its reason, the call session's rows
    and the asking session's `call` reports."""
    brief, the = await a_call_of(dsn, team, page, Gemini())
    reason = await asyncio.wait_for(the.run(), 10)
    return reason, await bridges.rows(dsn, the.id), await test_meet.reports(dsn, brief.id)


def never_in(rows, got) -> dict:
    """A call Valor never got into: no `call.joined`, no `link` report; its
    ended report, which says not to post the link and names no summary."""
    assert not any(r["type"] == "call.joined" for r in rows)
    assert [r["about_key"].rsplit(":", 1)[1] for r in got if r["kind"] == "call"] == ["joining", "ended"]
    ended = got[-1]
    assert (
        ended["text"].startswith("Valor could not join the Meet ")
        and "Do not post its link." in ended["text"]
    )
    assert "summary" not in ended["text"] and "valor-record call" not in ended["text"]
    return ended


def test_a_call_not_let_in_ends_saying_so_and_reports_no_link(dsn, team, quick):
    page = Page()
    page.status = "denied"
    reason, rows, got = run(ended_alone(dsn, team, page))
    assert reason == "Valor was not let in" and page.posts == []
    assert "Valor was not let in" in never_in(rows, got)["text"]


def test_the_link_is_reported_only_after_meets_join(dsn, team, quick):
    """Tom, 2026-11-13: "don't share a meet link if it doesn't work!" """
    gate = asyncio.Event()

    class Slow(Page):
        async def join(self):
            await gate.wait()

    async def go():
        c_page = Slow()
        brief, the = await a_call_of(dsn, team, c_page, Gemini())
        running = asyncio.create_task(the.run())
        await asyncio.sleep(0.3)
        before = await test_meet.reports(dsn, brief.id)
        gate.set()
        assert await until(lambda: call.JOIN_LINE in c_page.posts)
        after = await test_meet.reports(dsn, brief.id)
        the.leave("done")
        await asyncio.wait_for(running, 15)
        return before, after, await bridges.rows(dsn, the.id), await test_meet.reports(dsn, brief.id)

    before, after, rows, got = run(go())
    assert [r["about_key"].rsplit(":", 1)[1] for r in before if r["kind"] == "call"] == ["joining"]
    (link,) = [r for r in after if r["about_key"].endswith(":link")]
    assert link["text"].startswith("Valor is in the Meet: https://meet.google.com/") and at(1) in link["text"]
    assert [r["type"] for r in rows].count("call.joined") == 1
    assert [r["about_key"].rsplit(":", 1)[1] for r in got if r["kind"] == "call"] == [
        "joining",
        "link",
        "ended",
    ]
    assert got[-1]["text"].startswith("The call https://meet.google.com/")


class AtSignIn(Page):
    """Meet at Google's sign-in until Valor signs itself in, when `renews`."""

    def __init__(self, renews=False):
        super().__init__()
        self.renews, self.signed, self.done = renews, False, []

    async def join(self):
        self.done.append("join")
        if not self.signed:
            from bridges import meet

            raise meet.SignInNeeded("https://accounts.google.com/v3/signin/confirmidentifier")

    async def renew(self, back):
        self.done.append(("renew", back))
        self.signed = self.renews
        return self.renews


def test_a_call_meet_keeps_on_googles_sign_in_ends_naming_the_mac_and_the_command(dsn, team, quick, capsys):
    """The Cowboy, 2026-11-13: "Verify it's you" where Meet's join screen
    was, and no login of Valor's in 1Password to pass it with."""
    page = AtSignIn()
    reason, rows, got = run(ended_alone(dsn, team, page))
    assert reason == (
        f"Google wants Valor's sign-in on {settings.machine} verified again; at that Mac run: {call.SIGN_IN}"
    )
    assert page.posts == [] and "confirmidentifier" in capsys.readouterr().out
    assert page.done == ["join", ("renew", page.url)]
    ended = never_in(rows, got)
    assert settings.machine in ended["text"] and call.SIGN_IN in ended["text"]


def test_a_call_valor_signs_itself_in_to_joins_and_reports_its_link(dsn, team, quick, capsys):
    async def go():
        c = await in_call(dsn, team, AtSignIn(renews=True))
        rows = await bridges.rows(dsn, c.the.id)
        await finish(c)
        return c.page, rows, await test_meet.reports(dsn, c.brief.id)

    page, rows, got = run(go())
    assert page.done == ["join", ("renew", page.url), "join"]
    assert any(r["type"] == "call.joined" for r in rows) and any(
        r["about_key"].endswith(":link") for r in got
    )
    assert "signed in to Google again from 1Password" in capsys.readouterr().out


def test_a_profile_google_signed_out_is_signed_in_before_the_join(dsn, team, quick, tmp_path):
    """The Pirate and the Bald: a profile with no account, which Meet
    shows its guest screen, never Google's sign-in."""
    page = AtSignIn(renews=True)
    page.profile = str(tmp_path / "never-signed-in")

    async def go():
        c = await in_call(dsn, team, page)
        await finish(c)

    run(go())
    assert page.done == [("renew", page.url), "join"]


def test_meet_refusing_a_signed_in_valor_says_so_and_reports_no_link(dsn, team, quick):
    from bridges import meet

    class Refusing(Page):
        async def join(self):
            raise meet.Refused("https://meet.google.com/abc-defg-hij")

    reason, rows, got = run(ended_alone(dsn, team, Refusing()))
    assert reason == "Meet would not let Valor in"
    never_in(rows, got)


def test_a_steer_of_the_call_is_posted_in_its_chat_not_copied_and_noted_to_live(dsn, team, quick):
    """2026-11-18: a corrected answer from the chat session was only posted,
    and never reached the voice. A steer from before the call is neither."""

    async def go():
        page, gemini = Page(), Gemini()
        _, the = await a_call_of(dsn, team, page, gemini)
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, the.id, "message.steered", {"text": "An old steer."})
        running = asyncio.create_task(the.run())
        assert await until(lambda: gemini.sessions and the.session is not None)
        assert await until(lambda: call.JOIN_LINE in page.posts)
        await asyncio.sleep(0.3)
        async with await db.connect(dsn) as conn:
            await ledger.append(
                conn, the.id, "message.steered", {"text": "Logan is running five minutes late."}
            )
        assert await until(lambda: len(page.posts) == 2)
        c = SimpleNamespace(the=the, page=page, gemini=gemini, running=running, session=gemini.sessions[-1])
        await settle(the)
        await finish(c)
        return page, c.session, await effects(dsn, the.id)

    page, session, done_ = run(go())
    assert page.posts[1] == "Logan is running five minutes late." and "An old steer." not in page.posts
    assert not any(e["type"] == "slack.send_message" for e in done_)
    steered = [(t, d) for t, d in session.contents() if "chat session sent" in t]
    assert steered == [(f"[note] {call.STEERED.format(text='Logan is running five minutes late.')}", False)]


@pytest.mark.parametrize("handle", ["h1", None])
def test_a_steer_while_live_reconnects_is_noted_to_the_next_session_resumed_or_fresh(
    dsn, team, quick, handle
):
    """A fresh session's call-so-far is written before it connects: a steer
    that comes while it connects is noted to it once it opens."""

    async def go():
        c = await in_call(dsn, team)
        gate = asyncio.Event()
        connect = c.gemini.connect

        @contextlib.asynccontextmanager
        async def slow(config):
            await gate.wait()
            async with connect(config) as session:
                yield session

        c.gemini.connect = slow
        await push(c.session, *([resume(handle)] if handle else []), go_away())
        assert await until(lambda: c.the.session is None)
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, c.the.id, "message.steered", {"text": "The figure is 212, not 214."})
        assert await until(lambda: "The figure is 212, not 214." in c.page.posts)
        gate.set()
        assert await until(lambda: len(c.gemini.sessions) == 2 and c.the.session is c.gemini.sessions[1])
        await settle(c.the)
        await finish(c)
        return c

    c = run(go())
    assert c.gemini.configs[1].session_resumption.handle == handle
    steer = f"[note] {call.STEERED.format(text='The figure is 212, not 214.')}"
    assert "212" not in c.gemini.configs[1].system_instruction
    assert [t for t, _ in c.gemini.sessions[0].contents() if "chat session sent" in t] == []
    assert [t for t, _ in c.gemini.sessions[1].contents() if "chat session sent" in t] == [steer]


def test_a_question_the_chat_session_passes_on_goes_to_the_worker_unposted_and_is_noted(dsn, team, quick):
    """2026-11-18: the chat session hands a question asked in
    the call to the call's worker. A `look up:` steer is a worker turn, asked
    once even when Live asks it too; it is not posted, and its answer is
    posted and noted as one asked aloud."""
    asked = []

    async def worker(question):
        asked.append(question)
        return "212 signups."

    async def go():
        c = await in_call(dsn, team, worker=worker)
        async with await db.connect(dsn) as conn:
            await ledger.append(
                conn, c.the.id, "message.steered", {"text": "Look up: How many signups last week?"}
            )
        assert await until(lambda: asked)
        await push(c.session, tool("ask", question="How many signups last week?"), done())
        await settle(c.the)
        assert await until(lambda: any("asked aloud" in t for t, _ in c.session.contents()))
        await finish(c)
        return c

    c = run(go())
    assert asked == ["How many signups last week?"]
    assert not any("Look up" in p or "look up" in p for p in c.page.posts)
    assert 'On "How many signups last week?": 212 signups.' in c.page.posts
    say = call.SAY_ANSWER.format(question="How many signups last week?", answer="212 signups.")
    assert (f"[note] {say}", False) in c.session.contents()


def test_a_stop_before_valor_is_let_in_leaves_within_seconds(dsn, team, quick):
    async def go():
        page = Page()
        page.status = "lobby"
        _, the = await a_call_of(dsn, team, page, Gemini())
        running = asyncio.create_task(the.run())
        await asyncio.sleep(0.5)
        start = asyncio.get_running_loop().time()
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, the.id, reason="never mind", by="tom", via="chat")
        reason = await asyncio.wait_for(running, 10)
        return reason, asyncio.get_running_loop().time() - start, page

    reason, took, page = run(go())
    assert reason == "the call was stopped" and took < 5 and page.posts == []


@pytest.mark.parametrize(
    ("status", "people", "why"),
    [
        ("removed", None, "Valor was removed from the call"),
        ("in", [{"id": "devices/9", "name": "Valor Engels", "me": True}], "no one else came"),
    ],
)
def test_removal_or_no_one_coming_ends_the_call_once(dsn, team, quick, monkeypatch, status, people, why):
    monkeypatch.setattr(call, "ALONE_S", 0.3)

    async def go():
        page = Page()
        if people is not None:
            page.people = people
        c = await in_call(dsn, team, page)
        page.status = status
        reason = await asyncio.wait_for(c.running, 10)
        return reason, await bridges.rows(dsn, c.the.id)

    reason, rows = run(go())
    assert reason == why and [r["type"] for r in rows].count("call.ended") == 1


def test_down_to_16k_keeps_a_tone_across_frames():
    down = call.Down()
    t = np.arange(48000) / 48000
    tone = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    out = np.concatenate([down(tone[i : i + 480], 48000) for i in range(0, 48000, 480)])
    assert abs(len(out) - 16000) <= 1
    want = np.sin(2 * np.pi * 440 * (np.arange(len(out)) * 3 - 24) / 48000)
    assert np.abs(out[100:] - want[100:]).max() < 0.05


def test_a_copy_goes_to_the_telegram_topic_it_was_asked_in():
    info = {
        "code": CODE,
        "asked_by": {"channel": "telegram", "chat_id": "-1009", "reply_to": "77", "topic_id": "5"},
    }
    the = SimpleNamespace(info=info)
    kind, payload, chat = call.Call._copy(the, "The answer.")
    assert (kind, chat) == ("telegram.send_message", "-1009")
    assert payload == {"text": "The answer.", "reply_to": "77", "topic_id": "5"}


def test_a_calls_worker_takes_no_turn_slot(dsn, tmp_path, monkeypatch):
    from core import runs, slot

    def taken(*a, **k):
        raise AssertionError("a call's worker waited on a turn slot")

    monkeypatch.setattr(slot, "held", taken)

    async def go():
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="a call", max_effect_class="act"))
        gateway = await bridges.gateway(dsn)
        build = lambda url, brief, turn_id, news="", subagent="": runs.TurnCommand(
            argv=[sys.executable, "-c", "print('{}')"], env={}, cwd=str(tmp_path), harness="answer"
        )
        try:
            return await runs.run_turn(gateway, task, build, dsn=dsn, fresh="call")
        finally:
            await gateway.close()

    assert run(go())["outcome"] == "done"


# -- the page's player ---------------------------------------------------------------

STUB = """
window.ws = { ws: { addEventListener() {} }, sendJson() {} };
const ctx = new AudioContext();
window.botOutputManager = {
    audioContext: ctx, gainNode: ctx.createGain(), _createSourceAudioTrack() {}, ensureMicOn() {},
};
"""


def test_the_page_plays_chunks_back_to_back_and_a_stop_clears_them_saying_what_played():
    """`page.js` in a real Chrome, the payload's output stubbed by a plain
    Web Audio graph."""
    from playwright.async_api import async_playwright

    from bridges.meet import CHROME_ARGS, HERE

    chunk = base64.b64encode(b"\x00\x00" * int(0.2 * call.OUT_RATE)).decode()

    async def go():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel="chrome", headless=True, args=list(CHROME_ARGS))
            page = await browser.new_page()
            await page.evaluate(STUB + (HERE / "page.js").read_text())
            await page.wait_for_function(
                "window.botOutputManager.audioContext.currentTime > 0.05"
            )  # in a call it runs
            stream = "([b, f]) => window.valorStream(b, 24000, f)"
            aheads = [await page.evaluate(stream, [chunk, i == 0]) for i in range(3)]
            await asyncio.sleep(0.3)
            played = await page.evaluate("window.valorStop()")
            fresh = await page.evaluate(stream, [chunk, True])
            await browser.close()
            return aheads, played, fresh

    aheads, played, fresh = run(go())
    assert aheads[1] - aheads[0] == pytest.approx(0.2, abs=0.03) and aheads[2] - aheads[1] == pytest.approx(
        0.2, abs=0.03
    )
    assert 0.2 <= played <= 0.5  # played up to the stop, not the 0.6 s queued
    assert fresh == pytest.approx(0.2, abs=0.05)  # the stop emptied the queue


def test_a_summary_sent_with_its_newlines_escaped_is_written_as_markdown():
    """Gemini Flash sent the document as one line of `\\n`s (the live test, 2026-11-05)."""
    assert call._unescaped("# Notes\\n\\n- Ship Monday.") == "# Notes\n\n- Ship Monday."
    assert call._unescaped("# Notes\n\nA path C:\\new") == "# Notes\n\nA path C:\\new"


def test_a_failed_conversion_keeps_the_calls_recording(dsn, team, quick, monkeypatch):
    real = asyncio.create_subprocess_exec

    async def failing(*argv, **kw):
        if argv[0] == "afconvert":
            return await real(sys.executable, "-c", "raise SystemExit(1)")
        return await real(*argv, **kw)

    async def go():
        c = await in_call(dsn, team)
        await push(c.session, heard("Hello?"), done())
        await settle(c.the)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", failing)
        await finish(c)
        return c, await bridges.rows(dsn, c.brief.id)

    c, rows = run(go())
    assert not c.the.store.exists() or not list(
        c.the.store.iterdir()
    )  # the app's store holds only what it reads
    (folder,) = [p for p in c.the.store.parent.iterdir() if p.name.startswith(".valor-call-")]
    assert (folder / "call.wav").stat().st_size > 44
    assert not c.gemini.summarised
    (ended,) = [
        r["payload"]
        for r in rows
        if r["type"] == "child.reported" and r["payload"]["about_key"].endswith(":ended")
    ]
    assert "afconvert exited 1" in ended["text"] and str(folder) in ended["text"]


def test_a_question_live_asks_twice_is_one_worker_turn_and_one_post(dsn, team, quick):
    """Live asked "When is the launch?" in two turns a moment apart, and
    the answer was posted twice (the live test, 2026-11-05)."""

    async def go():
        asked = []

        async def worker(question):
            asked.append(question)
            return "Monday."

        c = await in_call(dsn, team, worker=worker)
        await push(c.session, tool("ask", question="When is the launch?"), done())
        await push(c.session, tool("ask", question="when is the launch"), done())
        await settle(c.the)
        await push(c.session, tool("ask", question="When is the launch?"), done())
        await settle(c.the)
        await finish(c)
        responses = [
            kw["function_responses"][0].response["result"] for k, kw in c.session.sent if k == "tool"
        ]
        return asked, c.page.posts, responses

    asked, posts, responses = run(go())
    assert asked == ["When is the launch?"]
    assert posts.count('On "When is the launch?": Monday.') == 1
    assert responses[1].startswith("already asked") and responses[2] == "already asked; the answer: Monday."


def test_side_talk_said_with_others_there_is_not_answered_once_they_leave(dsn, team, quick, monkeypatch):
    """Sam's "could you grab me a coffee?", said with Tom there, was
    answered once Tom left and Sam was alone with Valor (the live test,
    2026-11-05)."""
    monkeypatch.setattr(call, "UNANSWERED_S", 0.3)

    async def go():
        page = Page()
        c = await in_call(dsn, team, page)
        await until(lambda: len(c.the.present) == 2)
        await talk(c.the, LOGAN, 0.4)
        await push(c.session, heard("Tom, could you grab me a coffee?"))
        await asyncio.sleep(call.QUIET_S + 0.2)
        page.people = page.people[:2]  # Tom leaves
        await until(lambda: c.the.present == {LOGAN})
        await asyncio.sleep(1.0)
        await finish(c)
        return [t for t, _ in c.session.contents() if "said to you" in t or "spoke to you" in t]

    assert run(go()) == []


def test_a_lane_that_fails_ends_the_call_saying_why(dsn, team, quick, monkeypatch, capsys):
    """The mixer stopped mid-call and Valor heard nothing more, with
    nothing in the log (the live test, 2026-11-05)."""

    async def broken(self, due):
        raise ValueError("the mix broke")

    monkeypatch.setattr(call.Call, "_tick", broken)

    async def go():
        c = await in_call(dsn, team)
        return await asyncio.wait_for(c.running, 15)

    reason = run(go())
    assert reason == "Valor's call process failed: ValueError('the mix broke')"
    assert "the mix broke" in capsys.readouterr().out


def test_a_live_session_that_hears_nothing_of_speech_is_resumed(dsn, team, quick, monkeypatch, capsys):
    """Live stopped sending anything, transcripts included, after a typed
    answer, and Valor was deaf for the rest of the call (the live tests on
    the paid key, 2026-11-05)."""
    monkeypatch.setattr(call, "STALL_S", 0.3)

    async def go():
        c = await in_call(dsn, team)
        await push(c.session, resume("h9"))
        await talk(c.the, LOGAN, 0.5)  # nothing comes back
        assert await until(lambda: len(c.gemini.sessions) == 2 and c.the.session is c.gemini.sessions[1])
        sent = c.session.audio_bytes()
        await asyncio.sleep(1.0)  # the resumed session is not resumed again in the quiet
        assert len(c.gemini.sessions) == 2 and c.session.audio_bytes() == sent
        await push(c.gemini.sessions[1], heard("Valor, are you there?"), audio(), said("Yes."), done())
        await settle(c.the)
        await finish(c)
        return c, await effects(dsn, c.the.id)

    c, done_ = run(go())
    assert capsys.readouterr().out.count("Live sent nothing back after speech; resuming") == 1
    assert c.gemini.configs[1].session_resumption.handle == "h9" and call.LOST_LINE not in c.page.posts
    assert [e["result"]["spoken_text"] for e in done_ if e["type"] == "meet.say"] == ["Yes."]


def test_a_long_turn_live_sends_little_back_during_resumes_nothing(dsn, team, quick, monkeypatch):
    """Inside a long turn a healthy Live sent nothing back for longer than
    the stall, and the call resumed it eleven times (Tom's call,
    2026-11-06): a turn ten times the stall, its transcript in bursts
    farther apart than the stall, or none until it ends, resumes nothing."""
    monkeypatch.setattr(call, "STALL_S", 0.3)

    async def go():
        c = await in_call(dsn, team)
        for _ in range(4):  # 3 s of speech, a transcript only every 0.75 s
            await talk(c.the, LOGAN, 0.75)
            await push(c.session, heard("and then the next part "))
        await talk(c.the, LOGAN, 3.0)  # 3 s of speech with nothing back at all
        await asyncio.sleep(0.15)  # Live answers the end of the turn within the stall
        await push(c.session, heard("and that is all."), audio(), said("Got it."), done())
        await asyncio.sleep(1.0)
        await finish(c)
        return c

    c = run(go())
    assert len(c.gemini.sessions) == 1


def test_a_short_sound_live_gives_nothing_for_resumes_nothing(dsn, team, quick, monkeypatch, capsys):
    """Live gives no transcript of a short utterance ("Hello.", the live
    tests, 2026-11-05), and with several people there nothing asks it to
    answer: a second of it and then quiet is not a stall."""
    monkeypatch.setattr(call, "STALL_S", 1.5)

    async def go():
        c = await in_call(dsn, team)
        await talk(c.the, LOGAN, 1.0)
        await asyncio.sleep(2.5)
        await finish(c)
        return c

    c = run(go())
    assert len(c.gemini.sessions) == 1
    # A call leaving cancels the session's receiver; that is no stall (every call's end logged one, 2026-11-06).
    assert "Live sent nothing back" not in capsys.readouterr().out


def test_google_key_over_ssh_without_the_vault_says_missing_rather_than_failing(tmp_path, monkeypatch):
    """Over ssh the Desktop's vault `.env` is not readable, and `google-key`
    failed with PermissionError (the Captain, Pirate and Bald, 2026-11-05)."""
    from core import credentials

    monkeypatch.delenv("OP_SERVICE_ACCOUNT_TOKEN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))  # no op either
    vault = tmp_path / ".env"
    vault.write_text("OP_SERVICE_ACCOUNT_TOKEN=x\n")
    vault.chmod(0)
    try:
        assert credentials.op_read("op://Yudame Secrets/k1/credential", vault) is None
    finally:
        vault.chmod(0o600)


def test_an_invited_call_joined_before_its_start_waits_for_the_others_from_the_start(
    dsn, team, quick, monkeypatch
):
    # docs/plans/invite-calls.md: ALONE_S and LOBBY_S count from `starts`.
    monkeypatch.setattr(call, "ALONE_S", 0.3)

    async def go():
        page = Page()
        page.people = [{"id": "devices/9", "name": "Valor Engels", "me": True}]
        _brief, session_id = await a_call(dsn, team)
        async with await db.connect(dsn) as conn:
            info = await call.marker(conn, session_id)
        info["starts"] = (datetime.now(UTC) + timedelta(seconds=1.5)).isoformat()

        async def ready():
            return True

        the = call.Call(session_id, info, page, dsn=dsn, gemini=Gemini(), worker=None,
                        worker_ready=ready, project=team.project, store=str(team.profile.parent / "store"))  # fmt: skip
        running = asyncio.create_task(the.run())
        await asyncio.sleep(1.0)
        still = not running.done()
        return still, await asyncio.wait_for(running, 10)

    still, reason = run(go())
    assert still and reason == "no one else came"


def test_every_call_is_joined_as_the_projects_manager():
    # Tom, 2026-11-11: the persona's "In a call" carries it into the call's instruction.
    text = call.live_config("acme", "", None).system_instruction
    assert "as the project's manager" in text and "priority" in text


# -- the database restarting under a call ---------------------------------------


@pytest.fixture
def restart(dsn, monkeypatch):
    """What a restart of the server does to a call: every session dropped
    (`terminate_all`), and connections refused while `down` is set. The
    test's own connections go through `real`."""
    from tests.test_health import terminate_all

    real, down = db.connect, asyncio.Event()

    async def refusing(*a, **kw):
        if down.is_set():
            raise psycopg.OperationalError("connection is bad: No such file or directory")
        return await real(*a, **kw)

    monkeypatch.setattr(db, "connect", refusing)
    monkeypatch.setattr(db, "RELISTEN_S", 0.05)

    def drop():
        down.set()
        terminate_all(dsn)

    return SimpleNamespace(real=real, down=down, drop=drop)


async def held_call(dsn, team):
    """A call under way as `python -m core call` runs one: started, its locks held."""
    c = await in_call(dsn, team)
    async with await db.connect(dsn) as conn:
        await ledger.append(conn, c.the.id, "call.started", {"machine": settings.machine})
    c.the.held = db.Held(dsn, [f"meet:call:{c.the.id}", f"run:{c.the.id}"])
    assert await c.the.held.take() is None
    return c


def test_a_call_stays_through_its_database_restarting_holds_its_locks_again_and_hears_a_stop_after(
    dsn, team, quick, restart
):
    """2026-11-13, the Cowboy's restart at 10:38: the call's listener raised
    on the dropped session, its end could not be written, and the sweep,
    finding its lock free, ended it as "Valor's call process stopped"."""

    async def go():
        c = await held_call(dsn, team)
        restart.drop()
        async with await restart.real(dsn) as conn:
            await ledger.append(conn, c.the.id, "message.steered", {"text": "Back in a minute."})
        await asyncio.sleep(1.0)
        restart.down.clear()
        posted = await until(lambda: "Back in a minute." in c.page.posts)
        async with await restart.real(dsn) as conn:
            lock = await (await conn.execute(db.TRY_LOCK, (f"meet:call:{c.the.id}",))).fetchone()
            swept = await call.sweep(conn)
            still = not c.running.done()
            await tasks.stop(conn, c.the.id, reason="enough", by="tom", via="chat")
        reason = await asyncio.wait_for(c.running, 10)
        await c.the.held.close()
        return posted, lock[0], swept, still, reason, await bridges.rows(dsn, c.the.id)

    posted, free, swept, still, reason, rows = run(go())
    assert posted and not free and swept == [] and still
    assert reason == "the call was stopped"
    (ended,) = [r["payload"] for r in rows if r["type"] == "call.ended"]
    assert ended["reason"] == "the call was stopped"


def test_a_call_the_sweep_ended_while_its_database_was_down_leaves_and_is_ended_once(
    dsn, team, quick, restart
):
    async def go():
        c = await held_call(dsn, team)
        restart.drop()
        async with await restart.real(dsn) as conn:
            for _ in range(50):  # its lock is free once the session holding it has exited
                if swept := await call.sweep(conn):
                    break
                await asyncio.sleep(0.05)
        restart.down.clear()
        reason = await asyncio.wait_for(c.running, 15)
        await c.the.held.close()
        return c, swept, reason, await bridges.rows(dsn, c.the.id)

    c, swept, reason, rows = run(go())
    assert swept == [c.the.id] and reason == "the call was ended while the database was down"
    assert [r["payload"]["reason"] for r in rows if r["type"] == "call.ended"] == [
        "Valor's call process stopped"
    ]


def test_a_spoken_answer_answers_what_was_asked_with_the_specific_or_says_it_does_not_know():
    """Tom, 2026-11-13: asked for an example and for the ranking's criteria,
    Valor restated the goal and the order."""
    text = " ".join(call.live_config("acme", "", None).system_instruction.split())
    assert "Answer what was asked." in text and "the example, the number, the rule" in text
    assert "say plainly you don't know" in text and "Never answer by restating the goal" in text
    converse = " ".join((Path(__file__).parent.parent / "skills/sdlc/converse.md").read_text().split())
    calls = converse[converse.index("**Calls.**") : converse.index("**An invited call.**")]
    assert "concrete case behind it" in calls and "How product decisions are made" in calls


def test_a_call_ask_is_answered_and_joined_in_one_turn_with_notes_from_what_it_holds():
    """Tom, 2026-11-16 (P-033): the link took 2-3 minutes, a wake and a
    reading turn before the join. The turn that sees the ask sends the line
    and requests `meet.join` together, its notes from its Brief's summary,
    the open decisions and the thread; the call's worker reads the rest and
    the call posts its own link. An invited call keeps its reading ahead
    and its own post."""
    converse = " ".join((Path(__file__).parent.parent / "skills/sdlc/converse.md").read_text().split())
    calls = converse[converse.index("**Calls.**") : converse.index("**An invited call.**")]
    ack = calls.index(
        '"Starting a call, link in a moment", with no link, and in that same turn requests `meet.join`'
    )
    assert calls.index("with no reading first") < ack
    assert "wake.md" not in calls and "Read first" not in calls
    assert "summary page in your Brief" in calls and "with no tool reads for them" in calls
    assert "worker reads the rest" in calls
    assert "the call posts its link in that thread itself" in calls and "post no link yourself" in calls
    invited = converse[converse.index("**An invited call.**") : converse.index("**Voice notes.**")]
    assert "Starting a call" not in invited and "the `link` report needs no second post" in invited
    assert "Prep it ahead" in invited and "its open decisions and its priority queue" in invited


def test_every_managers_call_carries_valors_own_product_decision_process():
    """Tom, 2026-11-13: the process that orders the queue is standing context
    in every call, from the persona's one copy, not from the prep."""
    text = " ".join(call.live_config("acme", "", None).system_instruction.split())
    process = " ".join(persona.section(settings.persona_dir, "How product decisions are made").split())
    assert process in text
    for rule in (
        "a bug that breaks a job, with a real recorded case; security; a dated commitment) before bets",
        "Higher value first, then the smaller appetite",
        "no guard for a theoretical failure",
        "goal, job, appetite, solution, no-gos and acceptance",
        "observed (you saw it",
        "reported (someone told you) or assumed",
    ):
        assert rule in text
    assert "give the rule and the item's case" in text
    assert len(text.split()) < 2000  # an instruction, not a document


def test_a_call_looks_a_person_up_and_says_when_nobody_fits(dsn, team, quick):
    """P-005 (Tom, 2026-11-16): a call looks a person up before saying
    anything about them, from their dated facts; nobody recorded is a result
    the call can speak, not an error."""
    from core import memory, workspace

    async def go():
        async with await db.connect(dsn) as conn:
            await memory.fact_write(conn, workspace.Spec.load(team.project), [
                {"op": "entity", "kind": "person", "name": "Rowan Vale"},
                {"op": "fact", "kind": "role", "claim": "finance lead at the client", "source": "telegram:-1/3",
                 "since": "2026-10-20", "about": ["Rowan Vale"]},
            ])  # fmt: skip
        c = await in_call(dsn, team)
        await push(c.session, tool("person", about="the finance lead"), done())
        await push(c.session, tool("person", about="the plumber"), done())
        assert await until(lambda: len([k for k, _ in c.session.sent if k == "tool"]) == 2)
        await finish(c)
        return c

    memory.reset()
    try:
        c = run(go())
    finally:
        memory.reset()
    told = " ".join(c.gemini.configs[0].system_instruction.split())
    assert "call `person` with their name or how they were described" in told
    assert "a person hears only their name, role and organisation" in told
    found, none = [kw["function_responses"][0].response["result"] for k, kw in c.session.sent if k == "tool"]
    assert found.startswith("## Rowan Vale (external; person ")
    assert "[role], since 2026-10-20, inferred; source telegram:-1/3" in found
    assert none == "nobody recorded fits that"


# -- join first, read up after (P-033) -------------------------------------------


def _links(done_):
    return [e for e in done_ if e["request_id"] == "meet/link/copy"]


@pytest.mark.parametrize("target", ["new", "https://meet.google.com/xyz-abcd-efg"])
def test_a_call_asked_in_a_chat_posts_its_link_in_the_thread_once_valor_is_in(dsn, team, quick, target):
    """P-033: the call posts "I'm in: <link>" itself, after `call.joined`,
    under one fixed request id, so a re-entered run sends it once; a Meet
    link the chat gave is posted the same way."""
    gate = asyncio.Event()

    class Slow(Page):
        async def join(self):
            await gate.wait()

    async def go():
        page = Slow()
        _brief, the = await a_call_of(dsn, team, page, Gemini(), target=target)
        running = asyncio.create_task(the.run())
        await asyncio.sleep(0.3)
        before = await effects(dsn, the.id, link=True)
        gate.set()
        assert await until(lambda: call.JOIN_LINE in page.posts)
        the.leave("done")
        await asyncio.wait_for(running, 15)
        return the, page, before, await effects(dsn, the.id, link=True), await bridges.rows(dsn, the.id)

    the, page, before, after, rows = run(go())
    assert not _links(before)
    (link,) = _links(after)
    assert link["type"] == "slack.send_message" and link["target"] == team.chat
    assert link["payload"] == {"text": f"I'm in: {the.info['url']}", "reply_to": at(1)}
    joined = next(r["id"] for r in rows if r["type"] == "call.joined")
    held = next(
        r["id"] for r in rows if r["type"] == "effect.held" and r["payload"]["request_id"] == "meet/link/copy"
    )
    assert joined < held
    assert not [p for p in page.posts if the.info["url"] in p]  # the thread's line, not the call's chat


def test_the_link_report_says_the_thread_has_the_link_and_to_post_nothing(dsn, team):
    async def go():
        brief, session_id = await a_call(dsn, team)
        async with await db.connect(dsn) as conn, conn.transaction():
            await call.entered(conn, session_id)
        return await test_meet.reports(dsn, brief.id)

    (link,) = [r for r in run(go()) if r["about_key"].endswith(":link")]
    assert "already has the link" in link["text"] and "post nothing" in link["text"]


def test_a_call_valor_never_got_into_posts_no_link(dsn, team, quick):
    page = Page()
    page.status = "denied"

    async def go():
        _brief, the = await a_call_of(dsn, team, page, Gemini())
        await asyncio.wait_for(the.run(), 10)
        return await effects(dsn, the.id, link=True)

    assert not [e for e in run(go()) if e["type"] == "slack.send_message"]


def test_an_invited_call_posts_no_link_and_reads_nothing(dsn, team, quick):
    """Invited calls prep ahead and their own post has the link: no copy,
    no reading, no "still reading" line."""

    async def reader():
        raise AssertionError("an invited call reads nothing")

    async def go():
        starts = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        _brief, the = await a_call_of(dsn, team, Page(), Gemini(), reader=reader, starts=starts)
        running = asyncio.create_task(the.run())
        assert await until(lambda: the.gemini.sessions and the.session is not None)
        await settle(the)
        the.leave("done")
        await asyncio.wait_for(running, 15)
        return the, await effects(dsn, the.id, link=True)

    the, done_ = run(go())
    assert not _links(done_) and not the.reading
    assert "still reading up" not in the.gemini.configs[0].system_instruction


def test_the_reading_reaches_the_open_call_once_as_a_note_never_posted(dsn, team, quick):
    """P-033: while the worker reads up, Live is told it is still reading;
    the reading is noted once with no reply asked, never posted, and added
    to the notes, so a fresh session starts with it and is not noted again.
    A question asked meanwhile waits for it on the one worker lane."""
    gate, order = asyncio.Event(), []

    async def reader():
        await gate.wait()
        order.append("read")
        return "The launch moved to Friday (Logan, 2026-11-15)."

    async def worker(question):
        order.append(question)
        return "Friday."

    async def go():
        c = await in_call(dsn, team, worker=worker, reader=reader)
        await push(c.session, tool("ask", question="When is the launch?"), done())
        await asyncio.sleep(0.2)
        waited = list(order)
        gate.set()
        await settle(c.the)
        await push(c.session, go_away())  # no handle: a fresh session
        assert await until(lambda: len(c.gemini.sessions) == 2 and c.the.session is c.gemini.sessions[1])
        await settle(c.the)
        await finish(c)
        return c, waited

    c, waited = run(go())
    assert waited == [] and order == ["read", "When is the launch?"]
    first, second = c.gemini.configs[0].system_instruction, c.gemini.configs[1].system_instruction
    assert "still reading up" in first and "moved to Friday" not in first
    assert "still reading up" not in second and "moved to Friday" in second
    noted = [t for t, complete in c.gemini.sessions[0].contents() if "reading for this call is in" in t]
    assert len(noted) == 1 and "moved to Friday" in noted[0]
    assert all(not complete for t, complete in c.gemini.sessions[0].contents() if t.startswith("[note]"))
    assert not [t for t, _ in c.gemini.sessions[1].contents() if "reading for this call" in t]
    assert not [p for p in c.page.posts if "moved to Friday" in p]
    assert "moved to Friday" in c.the.info["prep"] and c.the.info["prep"].startswith("The launch")


def test_a_reading_done_while_live_opens_is_noted_once(dsn, team, quick):
    """The reading lands after the session's config was built and before
    it is open: the session is noted once, on opening."""
    opening, asked = asyncio.Event(), asyncio.Event()

    class SlowGemini(Gemini):
        @contextlib.asynccontextmanager
        async def connect(self, config):
            asked.set()
            await opening.wait()
            async with super().connect(config) as session:
                yield session

    async def reader():
        await asked.wait()  # its config is built, still reading
        return "Logan owns QA."

    async def go():
        gemini = SlowGemini()
        _brief, the = await a_call_of(dsn, team, Page(), gemini, reader=reader)
        running = asyncio.create_task(the.run())
        assert await until(lambda: not the.reading)
        opening.set()
        assert await until(lambda: the.session is not None)
        await settle(the)
        the.leave("done")
        await asyncio.wait_for(running, 15)
        return gemini

    gemini = run(go())
    assert "still reading up" in gemini.configs[0].system_instruction
    noted = [t for t, _ in gemini.sessions[0].contents() if "reading for this call is in" in t]
    assert len(noted) == 1 and "Logan owns QA." in noted[0]


def test_a_reading_that_fails_says_so_and_leaves_no_question_unanswered(dsn, team, quick):
    gate = asyncio.Event()

    async def reader():
        await gate.wait()
        raise RuntimeError("the worker's turn ended failed")

    async def go():
        c = await in_call(dsn, team, reader=reader)
        gate.set()
        assert await until(lambda: not c.the.reading)
        await settle(c.the)
        await finish(c)
        return c

    c = run(go())
    assert (f"[note] {call.READ_NONE}", False) in c.session.contents()
    assert not c.the.dropped and c.the.info["prep"].startswith("The launch")


# -- the team, and a task given in a call (P-034) ----------------------------------


def test_a_call_asked_who_is_on_the_team_looks_up_everyone_and_gets_the_roster(dsn, team, quick):
    """Tom, call pxz-exmv-icy (2026-11-16), #122073: "Who's on this team?"
    had no answer. The call is told to look up "everyone"; its `person` tool
    on that, or on Tom's own words, returns the project's people, internal
    first, with who they are."""
    from core import memory, workspace

    async def go():
        async with await db.connect(dsn) as conn:
            await memory.fact_write(conn, workspace.Spec.load(team.project), [
                {"op": "entity", "kind": "person", "name": "Rowan Vale"},
                {"op": "fact", "kind": "role", "claim": "finance lead at the client", "source": "telegram:-1/3",
                 "since": "2026-10-20", "about": ["Rowan Vale"]},
                {"op": "entity", "kind": "person", "name": "Tom Counsell"},
            ])  # fmt: skip
        c = await in_call(dsn, team)
        await push(c.session, tool("person", about="everyone"), done())
        await push(c.session, tool("person", about="Who's on this team?"), done())
        assert await until(lambda: len([k for k, _ in c.session.sent if k == "tool"]) == 2)
        await finish(c)
        return c

    memory.reset()
    try:
        c = run(go())
    finally:
        memory.reset()
    told = " ".join(c.gemini.configs[0].system_instruction.split())
    assert 'Asked who is on the team or who you have records of, call `person` with "everyone"' in told
    (person,) = [t for t in call.TOOLS if t["name"] == "person"]
    assert 'With "everyone", the project\'s people, its own team first' in person["description"]
    roster, again = [
        kw["function_responses"][0].response["result"] for k, kw in c.session.sent if k == "tool"
    ]
    assert roster == again
    assert roster.index("## Tom Counsell (internal;") < roster.index("## Rowan Vale (external;")
    assert "finance lead at the client" in roster


def test_a_task_given_in_a_call_is_ordinary_work_and_only_people_decisions_go_to_tom():
    """Tom, call pxz-exmv-icy (2026-11-16), #122150-#122163: told to fill the
    dossiers, Valor refused three times as "people matters ... go to Tom".
    Recording who people are and a task given are ordinary work, said as
    noted; hiring, pay, performance and disputes still go to Tom."""
    flat = " ".join(persona.section(settings.persona_dir, "In a call").split())
    assert "people matters" not in flat
    assert "A task anyone gives you in a call (note something down" in flat
    assert "is ordinary work, and so is recording who people are: say in a sentence that it is noted" in flat
    assert "decisions about people: hiring, pay, performance and disputes. Say they go to Tom" in flat
    told = " ".join(call.live_config("acme", "", None, reading=True).system_instruction.split())
    assert "(pricing, terms, its people)" not in told and "(pricing, terms, hiring or pay)" in told
    assert "is ordinary work: say in a sentence that it is noted" in told
    # P-033's reading line no longer narrows the call to three tools.
    assert "everything else goes on as usual: `ask` your worker, and take the tasks you are given" in told
    worker = " ".join((Path(__file__).parent.parent / "skills/sdlc/call.md").read_text().split())
    assert "people matters" not in worker and "hiring, pay, performance, disputes" in worker


def test_the_reading_note_takes_back_the_still_reading_line():
    """Call pxz-exmv-icy, #122072: after its reading came in (#122065) the
    call still said "I am still reading up"; its instruction cannot change
    mid-session, so the note says it no longer applies."""
    note = call.READ_IN.format(notes="n")
    assert "you are no longer reading up, so never say you are" in note


def test_the_tasks_from_a_call_reach_the_chat_session_in_its_report(dsn, team, quick):
    """Tom, #valor-product 2026-11-16 14:44: "Valor surprisingly denied my
    request to note down tasks." Each task the summary lists, given by Tom or
    taken by Valor, is a line of the call's report to the chat session."""
    notes = {"topic": "Dossiers", "summary": "s", "decisions": [], "markdown": "m",
             "actions": [{"task": "Fill the dossiers for each project", "owner": "Valor"},
                         {"task": "Prepare the build of action results before replies", "owner": "Valor"},
                         {"task": "Mark team lookup as something to improve"}]}  # fmt: skip

    async def go():
        c = await in_call(dsn, team, gemini=Gemini(notes=notes))
        c.the.hear(LOGAN, loud(), 48_000)
        await until(lambda: c.the.energy.get(LOGAN))
        await push(c.session, heard("Valor, fill the dossiers."), audio(), said("Noted."), done())
        await settle(c.the)
        await finish(c)
        return await bridges.rows(dsn, c.brief.id)

    rows = run(go())
    (ended,) = [
        r["payload"]["text"]
        for r in rows
        if r["type"] == "child.reported" and r["payload"]["about_key"].endswith(":ended")
    ]
    assert "Tasks from the call: take, hand on or decline each in the thread it was asked in:" in ended
    assert "- Fill the dossiers for each project (Valor)" in ended
    assert "- Prepare the build of action results before replies (Valor)" in ended
    assert "- Mark team lookup as something to improve\n" in ended + "\n"
    assert "each task someone gave or asked Valor for, and each thing Valor said it would do" in call.SUMMARY


def test_the_persona_says_when_the_call_asks_its_worker_as_if_then_lines():
    """P-038: asked what an error meant, Valor explained it five times in
    other words before asking (2026-11-16); asked for the criteria behind an
    order, it restated the order (2026-11-13). The persona's own rule, as
    Google's Live API guide shapes one ("if x, do y"), reaches the call's
    instruction, and Google's English sentence with it; the call's template
    keeps no copy."""
    flat = " ".join(persona.section(settings.persona_dir, "In a call").split())
    for line in (
        (
            "If you are asked for a fact your notes and tools don't give (a figure, what an error means, why an "
            "item ranks where it does), say plainly you don't know and ask your worker in that same turn."
        ),
        (
            "If you are asked again, or told they are confused, and your answer would say what you already said, "
            "ask your worker. Never say it again in other words."
        ),
        "RESPOND IN ENGLISH. YOU MUST RESPOND UNMISTAKABLY IN ENGLISH.",
    ):
        assert line in flat
        assert line in " ".join(call.live_config("acme", "", None).system_instruction.split())
        assert line not in " ".join(call.INSTRUCTIONS.split())


def test_in_a_one_to_one_valor_opens_the_next_item_and_more_detail_is_taught_not_repeated():
    """Tom, #valor-product 2026-11-17 04:25 UTC: in a 1:1 he had to ask
    "what's next?" once an item was settled (2026-11-16, #117966, #118033,
    #118640), and Valor handed the pick back ("What priority should we review
    next?", #118046); asked for more, Valor repeated itself. The persona's
    one text says to lead a 1:1 and wait in a group, and to teach a second
    answer in the present skill's words; the call's mechanics point at it."""
    flat = " ".join(persona.section(settings.persona_dir, "In a call").split())
    assert "With one other person in the call you lead it as the project's manager (Tom, 2026-11-17)" in flat
    assert "a short question confirming it is settled is always fine" in flat
    assert (
        "the same reply that reads it back or answers their confirmation ends by opening the next item"
        in flat
    )
    assert "open it, never ask which to take next" in flat
    assert (
        "With two or more others, don't steer the call: a settled item's reply ends with its read-back"
        in flat
    )
    assert "the next item waits until someone asks for it" in flat
    assert flat.count("steer the call") == 1 and "Follow what they want to discuss." in flat
    # The present skill's own lines (yudame present, Page form), not a second version.
    for quoted in ('"a taught explanation, not the same prose in a nicer font"',
                   '"a concrete example before the abstraction"', '"one hard concept an analogy and then the mechanism"',
                   "Each answer adds what the last one lacked; when your notes hold nothing new, ask your worker"):  # fmt: skip
        assert quoted in flat
    told = " ".join(call.live_config("acme", "", None).system_instruction.split())
    assert 'everything they say is to you: answer it, and lead the call as "In a call" says' in told
    # When to speak is the persona's, one text (2026-11-18): read into the call, no copy of its own.
    lines = [
        "is talk between the others",
        'no comment and no "Yes?" on it',
        "a question or request is yours only when it names you or follows straight on from your own answer",
        "say you missed it and ask your worker for it in that same turn",
    ]
    for line in lines:
        assert line in flat and line in told and line not in " ".join(call.INSTRUCTIONS.split())
    assert "Speak only when someone addresses you" in flat
    assert "Speak only when someone addresses you" not in " ".join(call.INSTRUCTIONS.split())
    assert "With only one person here, that read-back ends by opening the next item" in told
    assert "and let them decide each one" in told and "let them choose" not in told
    assert 'A note opening "Your worker prepared" is depth on items still to come' in told
    assert call.PREPARED.startswith("Your worker prepared")
    from core import __main__ as kernel

    source = " ".join(inspect.getsource(kernel._call).replace('"\n', "").replace('"', "").split())
    assert (
        "When the call so far already answered it, give what that answer lacked (an example, another"
        in source
    )
    assert "never the same answer" in source and "preparer=turn" in source
    worker = " ".join((Path(__file__).parent.parent / "skills/sdlc/call.md").read_text().split())
    assert "A turn that asks you to prepare ahead is not a question" in worker and "never posted" in worker


def _prepared(session) -> list[tuple[str, bool]]:
    return [
        (t, complete) for t, complete in session.contents() if t.startswith("[note] Your worker prepared")
    ]


def test_the_worker_prepares_after_the_reading_as_a_note_never_posted(dsn, team, quick):
    """P-038: the depth is ready before the call reaches it: the first prep
    follows the reading on the one worker lane, reaches the voice as a note
    asking no reply, and is never posted."""
    order = []

    async def reader():
        order.append("read")
        return "Two items: the import error, then the dossiers."

    async def preparer(prompt):
        order.append("prep")
        assert "next two items or open questions" in prompt and "The call so far:" in prompt
        return "Dossiers: 14 people recorded, 3 internal (ledger, 2026-11-16)."

    async def go():
        c = await in_call(dsn, team, reader=reader, preparer=preparer)
        assert await until(lambda: _prepared(c.session))
        await settle(c.the)
        await finish(c)
        return c

    c = run(go())
    assert order == ["read", "prep"]
    ((text, complete),) = _prepared(c.session)
    assert "14 people recorded" in text and complete is False
    assert not [p for p in c.page.posts if "14 people" in p]


def test_prep_runs_on_the_queue_moving_not_on_talk_and_questions_go_first(dsn, team, quick):
    """Preps come on events (an invited call's start, `settle`, `backlog`,
    `open_decisions`), in a group call too, never because the call talked
    on; events during a prep are one more prep; a question asked meanwhile
    is answered first, posted and noted as ever. "nothing ahead" notes
    nothing."""
    gate, order = asyncio.Event(), []
    replies = iter(["nothing ahead", "Item two: 0.4 percent of imports fail.", "Item three: blocked on QA."])

    async def preparer(prompt):
        order.append("prep")
        if len(order) == 2:
            await gate.wait()
        return next(replies)

    async def worker(question):
        order.append(question)
        return "Tuesday."

    async def go():
        starts = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        c = await in_call(dsn, team, worker=worker, preparer=preparer, starts=starts)
        assert await until(lambda: order == ["prep"])
        await settle(c.the)
        c.the.present = {"d1", "d2"}  # a group call
        for i in range(5):
            await c.the._heard("d1", "Tom", f"talking on {i}", "spoke")
        await asyncio.sleep(0.3)
        talked = list(order)
        await push(c.session, tool("settle", ids=["t1/q1"], readback="Ship Monday."), done())
        assert await until(lambda: len(order) == 2)  # the second prep, held at the gate
        await push(c.session, tool("open_decisions"), done())
        await push(c.session, tool("settle", ids=["t2/q2"], readback="Drop IE."), done())
        await push(c.session, tool("ask", question="When did the import ship?"), done())
        # Tool calls are handled in order: once the question is taken, so are the two before it.
        assert await until(lambda: "when did the import ship" in c.the.asked)
        gate.set()
        assert await until(lambda: len(order) == 4)
        await settle(c.the)
        assert await until(lambda: any("asked aloud" in t for t, _ in c.session.contents()))
        await finish(c)
        return c, talked

    c, talked = run(go())
    assert talked == ["prep"]  # talk alone starts no prep, and "nothing ahead" was noted as nothing
    assert order == ["prep", "prep", "When did the import ship?", "prep"]
    notes = [t for t, _ in _prepared(c.session)]
    assert len(notes) == 2 and "0.4 percent" in notes[0] and "blocked on QA" in notes[1]
    assert not [p for p in c.page.posts if "0.4 percent" in p or "nothing ahead" in p]
    assert any("Tuesday." in p for p in c.page.posts)
    say = call.SAY_ANSWER.format(question="When did the import ship?", answer="Tuesday.")
    assert (f"[note] {say}", False) in c.session.contents()  # asked aloud: noted, asking no turn


def test_a_prep_done_while_no_session_is_open_is_noted_once_to_the_next(dsn, team, quick):
    """A prep that lands before Live is open (the call joining, or between
    sessions) waits for the next session to open, is noted to it once with
    no reply asked, and not again to a session after."""
    opening, asked = asyncio.Event(), asyncio.Event()

    class SlowGemini(Gemini):
        @contextlib.asynccontextmanager
        async def connect(self, config):
            asked.set()
            await opening.wait()
            async with super().connect(config) as session:
                yield session

    async def preparer(prompt):
        await asked.wait()  # no session open yet
        return "Item two: 0.4 percent of imports fail."

    async def go():
        gemini = SlowGemini()
        starts = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        _brief, the = await a_call_of(dsn, team, Page(), gemini, preparer=preparer, starts=starts)
        running = asyncio.create_task(the.run())
        assert await until(lambda: the.pending)
        opening.set()
        assert await until(lambda: the.session is not None and _prepared(the.session))
        await settle(the)
        await push(the.session, go_away())  # no handle: a fresh session
        assert await until(lambda: len(gemini.sessions) == 2 and the.session is gemini.sessions[1])
        await settle(the)
        the.leave("done")
        await asyncio.wait_for(running, 15)
        return the, gemini

    the, gemini = run(go())
    ((text, complete),) = _prepared(gemini.sessions[0])
    assert "0.4 percent" in text and complete is False
    assert not _prepared(gemini.sessions[1]) and not the.pending
