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
import json
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from google.genai import types

from core import bridge, broker, call, db, ledger, spending, tasks
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


async def a_call_of(dsn, team, page, gemini, *, worker=None, ready=True):
    async def ready_now():
        return ready

    async def no_worker(question):
        raise AssertionError("no worker in this test")

    brief, session_id = await a_call(dsn, team)
    async with await db.connect(dsn) as conn:
        info = await call.marker(conn, session_id)
    the = call.Call(session_id, info, page, dsn=dsn, gemini=gemini, worker=worker or no_worker,
                    worker_ready=ready_now, project=team.project, store=str(team.profile.parent / "store" / "Meetings"))  # fmt: skip
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


async def effects(dsn, session_id) -> list[dict]:
    """The session's effects in order: type, payload, request id, and how
    each stands."""
    rows = await bridges.rows(dsn, session_id)
    held = [r["payload"] for r in rows if r["type"] == "effect.held"]
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
    assert (f"[note] In the call with you: Logan, {TOM}, Valor Engels (another device).", False) in notes
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
    assert response["function_responses"][0].response == {"result": "posted"}


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


def test_a_failed_post_gets_no_copy(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        c.page.post_ok = False
        await push(c.session, tool("post", text="First."), done())
        await settle(c.the)
        await finish(c)
        return await effects(dsn, c.the.id)

    done_ = run(go())
    assert [e["kind"] for e in done_ if e["payload"].get("text") == "First."] == ["failed"]
    assert not [e for e in done_ if e["type"] == "slack.send_message"]


def test_an_ask_returns_at_once_and_its_answer_after_a_resumption_is_posted_and_noted(dsn, team, quick):
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
        await settle(c.the)
        await finish(c)
        return c, response, await effects(dsn, c.the.id)

    c, response, done_ = run(go())
    assert response["function_responses"][0].response["result"].startswith("asked")
    assert c.gemini.configs[1].session_resumption.handle == "h1"
    assert 'On "What did we ship?": The import, on Tuesday.' in c.page.posts
    noted = c.gemini.sessions[1].contents()
    assert (
        '[note] Your worker answered "What did we ship?" (posted in the chat): The import, on Tuesday.',
        False,
    ) in noted
    assert [e["type"] for e in done_ if e["type"] != "meet.send_message"] == ["slack.send_message"]


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


def test_the_live_session_is_told_valors_voice_tools_and_how_to_wait_its_turn():
    config = call.live_config("acme", "Logan wants the launch date.", None)
    assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Charon"
    assert config.proactivity.proactive_audio is True
    assert config.realtime_input_config.activity_handling == types.ActivityHandling.NO_INTERRUPTION
    assert config.context_window_compression.trigger_tokens == call.CALL_CONTEXT
    assert config.realtime_input_config.automatic_activity_detection is None  # Live's own
    assert [f.name for f in config.tools[0].function_declarations] == [
        "ask",
        "post",
        "open_decisions",
        "settle",
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
    assert "**English,** unless the person speaks another language clearly and on purpose" in flat
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


def test_valor_leaves_when_live_calls_leave_for_anyone(dsn, team, quick):
    """Tom, 2026-11-12: leave when someone asks, or everyone wraps up. The
    model decides from the conversation; Logan is not Tom."""

    async def go():
        c = await in_call(dsn, team)
        for _ in range(8):
            c.the.hear(LOGAN, loud(0.05), 48_000)
            await asyncio.sleep(0.05)
        await push(c.session, heard("Thanks everyone, that's all for today. Bye!"), tool("leave"))
        return await asyncio.wait_for(c.running, 10)

    assert run(go()) == "Valor was asked to leave"


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


def test_a_call_not_let_in_ends_saying_so(dsn, team, quick):
    async def go():
        page = Page()
        page.status = "denied"
        _, the = await a_call_of(dsn, team, page, Gemini())
        return await asyncio.wait_for(the.run(), 10), page

    reason, page = run(go())
    assert reason == "Valor was not let in" and page.posts == []


def test_a_steer_of_the_call_is_posted_in_its_chat_and_not_copied(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        async with await db.connect(dsn) as conn:
            await ledger.append(
                conn, c.the.id, "message.steered", {"text": "Logan is running five minutes late."}
            )
        assert await until(lambda: len(c.page.posts) == 2)
        await finish(c)
        return c.page, await effects(dsn, c.the.id)

    page, done_ = run(go())
    assert page.posts[1] == "Logan is running five minutes late."
    assert not any(e["type"] == "slack.send_message" for e in done_)


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
        build = lambda url, brief, turn_id: runs.TurnCommand(
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
window.botOutputManager = { audioContext: ctx, gainNode: ctx.createGain(), _createSourceAudioTrack() {} };
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
    assert (
        responses[1].startswith("already asked")
        and responses[2] == "already asked; the answer, posted in the chat: Monday."
    )


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
