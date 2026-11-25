"""Valor in a Meet call, its own process (`core/call.py`), driven without
Meet or Google: a page that records what it streams and posts, and a fake
Gemini whose transcriber sessions record the audio sent them and answer
with scripted server messages (google-genai's own types), whose mind
writes scripted rounds of text and tool calls, and whose voice returns
half a second of sound a sentence.

What is said aloud is one `meet.say` per turn of the mind, requested at
its first sound, its audio held until it is released and dropped when it
is refused, settled with what was said (cut where it was cut); a written
answer is a `meet.send_message`, copied to the thread once done. What a
turn does in the world (`settle`, `post`, `ask`, `leave`) waits for its
first sound, or for the mind's end when it says nothing; the person it
answers speaking before then drops it. A typed line is answered in the
chat, never aloud. Each model call is charged by modality. A transcriber
that drops is reopened; one that cannot be is one line in the chat and a
leave. After the call Gemini summarises the recording, kept whole in
Yudame Tools' meeting store."""

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

from bridges.meet import google
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
        self.on_stream = None  # called at each stream, for what the call holds then

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
        if self.on_stream is not None:
            self.on_stream()
        self.streams.append((len(pcm), fresh))
        return self.ahead

    async def stop(self):
        self.stops += 1
        return self.played

    async def leave(self):
        self.left = True

    async def close(self):
        pass


class Ears:
    """A transcriber session: the audio sent to it, and an inbox of server
    messages `receive` yields."""

    def __init__(self):
        self.sent: list[types.Blob] = []
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.handling = False  # a message yielded, its handling not yet back for the next

    async def send_realtime_input(self, **kw):
        self.sent.append(kw["audio"])

    async def receive(self):
        while True:
            m = await self.inbox.get()
            if isinstance(m, Exception):
                raise m
            self.handling = True
            try:
                yield m
            finally:
                self.handling = False

    def audio_bytes(self) -> int:
        return sum(len(b.data) for b in self.sent)


MIND_USAGE = {"prompt_token_count": 3000, "candidates_token_count": 40,
              "prompt_tokens_details": [{"modality": "TEXT", "token_count": 3000}],
              "candidates_tokens_details": [{"modality": "TEXT", "token_count": 40}]}  # fmt: skip
VOICE_USAGE = {"prompt_token_count": 7, "candidates_token_count": 46,
               "prompt_tokens_details": [{"modality": "TEXT", "token_count": 7}],
               "candidates_tokens_details": [{"modality": "AUDIO", "token_count": 46}]}  # fmt: skip
EARS_USAGE = {"prompt_token_count": 250, "response_token_count": 20,
              "prompt_tokens_details": [{"modality": "AUDIO", "token_count": 250}],
              "response_tokens_details": [{"modality": "TEXT", "token_count": 20}]}  # fmt: skip


class Gemini:
    """Gemini as the call uses it: each `hear` a new `Ears` (or, while
    `fail` is above 0, a refusal); `think` plays the next round of `minds`
    (text, or `(tool, args)`), recording each prompt and tool answer it was
    given; `speak` gives `voice_s` of sound a sentence; `summarise` records
    what it was given. `think_gate`, `speak_gate` and `hear_gate` hold them."""

    def __init__(self, notes=None):
        self.ears: list[Ears] = []
        self.fail = 0
        self.minds: list[list] = []
        self.prompts: list[str] = []  # each turn's prompt
        self.instructions: list[str] = []
        self.tools: list[list[str]] = []
        self.results: list[tuple[str, str]] = []  # each tool's answer, as the mind was given it
        self.spoken: list[str] = []
        self.words_only: list[bool] = []  # each round's
        self.voice_s = 0.5
        self.think_gate = self.speak_gate = self.hear_gate = None
        self.held_sentences: set[str] | None = None  # only these wait on `speak_gate`, when set
        self.notes = notes or {"topic": "Launch plan", "summary": "Shipping is Monday, after PR 12.",
                               "decisions": [{"decision": "Ship Monday", "by": ["Logan Reyes"]}], "actions": [{"task": "Book QA", "owner": "Logan"}],
                               "markdown": "# The call\n\nShip PR #12 and ENG-633."}  # fmt: skip
        self.summarised: list[tuple[Path, str, bytes]] = []
        self.preps: list[str] = []
        self.recorded: list[str] = []

    @contextlib.asynccontextmanager
    async def hear(self):
        if self.hear_gate is not None:
            await self.hear_gate.wait()
        if self.fail:
            self.fail -= 1
            raise ConnectionError("refused")
        ears = Ears()
        self.ears.append(ears)
        yield ears

    async def think(self, instruction, contents, tools, words_only=False):
        if len(contents) == 1:
            self.prompts.append(contents[0]["parts"][0]["text"])
            self.instructions.append(instruction)
            self.tools.append([t["name"] for t in tools])
        else:
            self.results += [(p["function_response"]["name"], p["function_response"]["response"]["result"])
                             for p in contents[-1]["parts"]]  # fmt: skip
        if self.think_gate is not None:
            await self.think_gate.wait()
        self.words_only.append(words_only)
        for item in self.minds.pop(0) if self.minds else []:
            if words_only and not isinstance(item, str):
                continue
            if isinstance(item, str):
                yield {"part": {"text": item}, "text": item}
            else:
                yield {"part": {"function_call": {"name": item[0], "args": item[1]}}, "call": item}
            await asyncio.sleep(0)
        yield {"usage": MIND_USAGE}

    async def speak(self, text):
        self.spoken.append(text)
        if self.speak_gate is not None and (self.held_sentences is None or text in self.held_sentences):
            await self.speak_gate.wait()
        yield b"\x01\x00" * int(self.voice_s * call.OUT_RATE)
        yield {"usage": VOICE_USAGE}

    async def summarise(self, audio, transcript, prep, recorded=""):
        self.summarised.append((audio, transcript, audio.read_bytes()[:12]))
        self.preps.append(prep)
        self.recorded.append(recorded)
        if isinstance(self.notes, Exception):
            raise self.notes
        return self.notes, {"prompt_token_count": 1000, "response_token_count": 200,
                            "prompt_tokens_details": [{"modality": "AUDIO", "token_count": 800},
                                                      {"modality": "TEXT", "token_count": 200}]}  # fmt: skip


def heard(text) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(input_transcription=types.Transcription(text=text))
    )


def activity(kind) -> types.LiveServerMessage:
    return types.LiveServerMessage(voice_activity=types.VoiceActivity(voice_activity_type=kind))


def ended(text="") -> list[types.LiveServerMessage]:
    """A turn the transcriber heard: its words, then its end."""
    return [*([heard(text)] if text else []), activity("ACTIVITY_END")]


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
    """A call joined, its transcriber open and who is here known."""
    page, gemini = page or Page(), gemini or Gemini()
    brief, the = await a_call_of(dsn, team, page, gemini, **kw)
    running = asyncio.create_task(the.run())
    assert await until(lambda: gemini.ears and the.ears is not None)
    assert await until(lambda: call.JOIN_LINE in page.posts)
    assert await until(lambda: len(the.present) == len([p for p in page.people if not p["me"]]))
    return SimpleNamespace(brief=brief, the=the, page=page, gemini=gemini, running=running,
                           ears=gemini.ears[-1])  # fmt: skip


async def settle(the, s=3.0):
    """Until every message pushed to the transcriber is handled, every turn
    and job is done, and every post is sent."""

    def ears_idle():
        return the.ears is None or (the.ears.inbox.empty() and not the.ears.handling)

    for _ in range(int(s / 0.02)):
        await asyncio.sleep(0.02)
        if ears_idle() and the.posts.empty() and not [j for j in the.jobs if not j.done()] and not the.asking:
            await asyncio.sleep(0.05)
            if the.posts.empty() and not the.jobs:
                return
    raise AssertionError("the call never settled")


async def push(ears, *messages):
    for m in messages:
        ears.inbox.put_nowait(m)
    await asyncio.sleep(0.1)


async def says(c, who, text, seconds=0.4):
    """`who` speaks `text` and the transcriber ends their turn."""
    await talk(c.the, who, seconds)
    await push(c.ears, *ended(text))


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


async def talk(the, who, seconds):
    """`who` speaking for `seconds`, as Meet sends it."""
    for _ in range(round(seconds / 0.05)):
        the.hear(who, loud(0.05), 48_000)
        await asyncio.sleep(0.05)


def alone() -> Page:
    page = Page()
    page.people = page.people[:1] + page.people[2:]  # only Tom
    return page


def _answers(text):
    async def worker(question):
        return text

    return worker


def now_of(prompt: str) -> str:
    return prompt.rpartition("\n\nNow: ")[2]


def said_texts(done_) -> list[str]:
    return [e["result"]["spoken_text"] for e in done_ if e["type"] == "meet.say"]


# -- speaking -------------------------------------------------------------------


def test_a_reply_is_one_say_its_audio_held_until_released_and_settled_with_what_was_said(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        c.gemini.minds.append(["We ship ", "Monday. QA", " is done."])
        await says(c, LOGAN, "Valor, when do we ship?")
        assert await until(lambda: c.page.streams and c.the.out is None and not c.the.replies)
        await settle(c.the)
        await finish(c)
        return c, await effects(dsn, c.the.id), await bridges.rows(dsn, c.the.id)

    c, done_, rows = run(go())
    (say,) = [e for e in done_ if e["type"] == "meet.say"]
    assert say["kind"] == "done" and say["result"]["spoken_text"] == "We ship Monday. QA is done."
    assert not say["result"]["cut"]
    assert say["payload"] == {"turn": say["request_id"].split("/")[1]} and say["request_id"].endswith("/say")
    # Sentence by sentence, each to the voice as it is whole; held until released, then one fresh
    # chunk, the rest after it, never fresh.
    assert c.gemini.spoken == ["We ship Monday.", "QA is done."]
    assert c.page.streams[0][1] is True and all(not fresh for _, fresh in c.page.streams[1:])
    assert sum(n for n, _ in c.page.streams) == 2 * 2 * int(0.5 * call.OUT_RATE)
    (heard_,) = [r["payload"] for r in rows if r["type"] == "call.heard"]
    assert (
        heard_["name"] == "Logan" and heard_["text"] == "Valor, when do we ship?" and heard_["via"] == "spoke"
    )
    assert not [e for e in done_ if e["type"] == "slack.send_message"]  # speech is not copied
    assert any(x.endswith("Valor (spoke): We ship Monday. QA is done.") for x in c.the.lines)


def test_a_turn_with_nothing_to_say_is_silence(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team, alone())
        await says(c, TOMS, "Valor, hang on a second.")  # the mind writes nothing
        await settle(c.the)
        await finish(c)
        return c, await effects(dsn, c.the.id)

    c, done_ = run(go())
    assert len(c.gemini.prompts) == 1 and c.page.streams == [] and not said_texts(done_)


def test_a_refused_say_drops_its_audio(dsn, team, quick, monkeypatch):
    async def refuse(conn, action):
        return "not now"

    monkeypatch.setitem(
        bridge.DECLARED, "meet.say", dataclasses.replace(bridge.DECLARED["meet.say"], check=refuse)
    )

    async def go():
        c = await in_call(dsn, team, alone())
        c.gemini.minds.append(["Something."])
        await says(c, TOMS, "Say something.")
        await settle(c.the)
        await finish(c)
        return c, await bridges.rows(dsn, c.the.id)

    c, rows = run(go())
    assert c.page.streams == []
    refused = [r["payload"] for r in rows
               if r["type"] == "effect.refused" and r["payload"]["action_type"] == "meet.say"]  # fmt: skip
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
        c.gemini.voice_s = 1.0
        c.gemini.minds.append(["one two three. four five six."])
        await says(c, LOGAN, "Valor, tell me about the launch.")
        assert await until(lambda: c.the.out is not None and len(c.gemini.spoken) == 2)
        assert await until(lambda: c.the.turn.task.done())  # the turn is written; its reply plays on
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
    assert say["result"] == {"spoken_text": "one two three.", "cut": True, "played_s": 1.0}
    assert any(line.endswith("Valor (spoke, cut off): one two three.") for line in c.the.lines)


def test_a_cut_stops_the_turn_writing_and_saying_the_rest(dsn, team, quick):
    async def go():
        page = Page()
        page.ahead, page.played = 30.0, 0.5
        c = await in_call(dsn, team, page)
        c.gemini.minds.append(["First. ", "Second. ", "Third."])
        c.gemini.speak_gate, c.gemini.held_sentences = asyncio.Event(), {"Second."}
        await says(c, LOGAN, "Valor, tell me everything.")
        assert await until(lambda: c.the.out is not None and "Second." in c.gemini.spoken)
        await talk(c.the, LOGAN, 0.6)
        c.gemini.speak_gate.set()
        await settle(c.the)
        await finish(c)
        return c

    c = run(go())
    assert c.page.stops == 1 and "Third." not in c.gemini.spoken


@pytest.mark.parametrize("quiet_between", [True, False])
def test_valor_answers_who_spoke_his_name_and_their_words_over_him_cut_him_and_side_talk_does_not(
    dsn, team, quick, quiet_between
):
    """Live took the side-talker before a question as the person answered,
    and their words over the reply cut Valor off (the live test,
    2026-11-06). Each turn is credited to whoever spoke most in it."""

    async def go():
        page = Page()
        page.ahead, page.played = 30.0, 1.0
        c = await in_call(dsn, team, page)
        await says(c, TOMS, "Sam, did the invoice come?", 0.6)
        if quiet_between:
            await asyncio.sleep(0.5)
        c.gemini.minds.append(["one two three four five six."])
        await says(c, LOGAN, "Valor, tell me about the launch.", 0.6)
        assert await until(lambda: c.the.out is not None and c.the.turn.task.done())
        await talk(c.the, TOMS, 0.6)  # side talk over the reply
        side = c.page.stops
        await talk(c.the, LOGAN, 0.6)  # the person answered
        await settle(c.the)
        await finish(c)
        return c, side

    c, side = run(go())
    assert len(c.gemini.prompts) == 1  # Tom's words to Sam asked nothing of Valor
    assert side == 0 and c.page.stops == 1 and c.the.answering == LOGAN


def test_valors_own_audio_is_not_sent_and_silence_is(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        c.the.hear("devices/9", loud(), 48_000)  # Valor's own track
        await asyncio.sleep(0.3)
        await finish(c)
        return c.ears

    ears = run(go())
    assert ears.audio_bytes() > 0 and all(b.mime_type == "audio/pcm;rate=16000" for b in ears.sent)
    assert all(not any(b.data) for b in ears.sent)


def test_each_turn_heard_is_credited_to_whoever_was_loudest_in_it(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        await talk(c.the, LOGAN, 0.5)
        await talk(c.the, TOMS, 0.1)
        await push(c.ears, *ended("I think Monday."))
        await talk(c.the, TOMS, 0.6)
        await push(c.ears, activity("ACTIVITY_START"), *ended("Tuesday is better."))
        await settle(c.the)
        await finish(c)
        return c, await bridges.rows(dsn, c.the.id)

    c, rows = run(go())
    turns = [(r["payload"]["name"], r["payload"]["text"]) for r in rows if r["type"] == "call.heard"]
    assert turns == [("Logan", "I think Monday."), (TOM, "Tuesday is better.")]
    assert c.the.segments[-1]["start"] <= c.the.segments[-1]["end"]


def test_with_one_other_person_all_they_say_is_answered_and_words_not_caught_are_asked_for(dsn, team, quick):
    """Tom's "Hello" in a call with only him got no answer (2026-11-05)."""

    async def go():
        c = await in_call(dsn, team, alone())
        await says(c, TOMS, "Hello.")
        await settle(c.the)
        await says(c, TOMS, "")  # speech the transcriber gave no words for
        await settle(c.the)
        await finish(c)
        return c

    c = run(go())
    assert [now_of(p) for p in c.gemini.prompts] == [
        call.NOW_SPOKEN.format(name=TOM),
        call.NOW_UNCAUGHT.format(name=TOM),
    ]
    assert c.gemini.prompts[0].startswith(f"Only {TOM} is here with you")


def test_in_a_group_valor_answers_what_names_him_or_follows_his_answer_and_nothing_else(dsn, team, quick):
    """2026-11-18: asked by name ("Val", misheard) in a group call, Valor
    said nothing; side talk with Sam was answered once Sam had left
    (2026-11-05). Side talk asks the mind nothing; what names Valor, or
    follows straight on from what he said, is put to it to answer or not."""

    async def go():
        c = await in_call(dsn, team)
        await says(c, LOGAN, "The landing page converts better than in October.")
        await settle(c.the)
        side = len(c.gemini.prompts)
        c.gemini.minds.append(["Signups were 212."])
        await says(c, LOGAN, "Val, can you pull up the signups from last week?")
        assert await until(lambda: any("Valor (spoke)" in x for x in c.the.lines))
        await says(c, TOMS, "And the paid ones?")  # straight on from his answer
        await settle(c.the)
        await says(c, TOMS, "Logan, lunch after this?")
        await settle(c.the)
        await finish(c)
        return c, side

    c, side = run(go())
    assert side == 0
    assert [now_of(p) for p in c.gemini.prompts] == [
        call.NOW_GROUP.format(name="Logan"),
        call.NOW_GROUP.format(name=TOM),
    ]
    assert c.gemini.prompts[0].startswith(f"In the call with you: Logan, {TOM}. Open the next item only")


def test_a_turn_is_given_who_is_here_the_meeting_now_the_kept_notes_and_the_call_so_far(
    dsn, team, quick, monkeypatch
):
    """The one mind reads the state (P-041) fresh each turn: the decisions
    on top, a stale reading never beside it; the kept notes take at most a
    quarter of `CALL_SO_FAR`, the newest first kept, and the call so far
    the rest, cut from its start."""

    async def go():
        c = await in_call(dsn, team, alone())
        c.the.decided.append({"decision": "Ship Monday.", "by": [TOM], "at": "01:00"})
        c.the.topics = "1. Shipping: decided.\n2. Emails: open."
        c.the.told = ["an old answer", "x" * 900, "the newest answer"]
        c.the.lines = [f"[00:{i:02d}] {TOM} (spoke): line {i:02d} of the call" for i in range(40)]
        monkeypatch.setattr(call, "CALL_SO_FAR", 1600)
        await says(c, TOMS, "What next?")
        await settle(c.the)
        await finish(c)
        return c

    c = run(go())
    (prompt,) = c.gemini.prompts
    here, state, _ = prompt.split("\n\n", 2)
    assert here == call.Call._here([TOM]) and state.startswith("The meeting now")
    assert f"- Ship Monday. ({TOM}, 01:00)" in prompt and "2. Emails: open." in prompt
    assert "Kept for you:\n- the newest answer\n" in prompt and "x" * 900 not in prompt
    so_far = prompt.partition("The call so far:\n")[2].partition("\n\nNow:")[0].split("\n")
    assert so_far[-1].endswith(f"{TOM} (spoke): What next?") and "line 00 " not in prompt
    assert len(prompt) < 1600 + 400


def test_live_hears_each_person_only_while_they_speak():
    """Faint talk under `SPEAKING` from someone not speaking kept Live's
    end of turn from coming (tests/test_live_call_voice.py, 2026-11-18): it
    is left out of what the transcriber hears; a speaker's quiet tail within
    `HOLD_S` is kept; the recording keeps everyone's sound."""
    n, spoke_at = call.RATE // 25, {}
    faint, voiced = np.full(n, 0.004, np.float32), np.full(n, 0.2, np.float32)
    heard_, whole, loud_ = call.mixed({"a": voiced, "b": faint}, n, spoke_at, 0.0)
    assert np.allclose(heard_, 0.2) and np.allclose(whole, 0.204) and loud_["b"] < call.SPEAKING <= loud_["a"]
    heard_, _, _ = call.mixed({"a": faint, "b": faint}, n, spoke_at, call.HOLD_S / 2)
    assert np.allclose(heard_, 0.004)  # a's tail
    heard_, whole, _ = call.mixed({"a": faint, "b": faint}, n, spoke_at, call.HOLD_S + 0.1)
    assert not heard_.any() and np.allclose(whole, 0.008)  # the recording keeps everyone


def test_sentences_end_at_a_mark_before_a_space():
    assert call.sentences("Yes. We ship Monday! Then") == (["Yes.", "We ship Monday!"], " Then")
    assert call.sentences("It is 2.5 percent") == ([], "It is 2.5 percent")


# -- a turn's effects wait for its first sound ------------------------------------------


def test_a_decision_is_in_the_state_before_valor_speaks_and_nothing_held_is_done_before_his_first_sound(
    dsn, team, quick
):
    async def go():
        page = Page()
        c = await in_call(dsn, team, page)
        seen = {}
        page.on_stream = lambda: seen.setdefault("decided at first sound", len(c.the.decided))
        c.gemini.minds += [[("person", {"about": "Logan"}), ("settle", {"readback": "Ship Monday.", "ids": ["t1/q1"]}),
                            ("post", {"text": "The plan: ship Monday."})], ["Ship Monday, then."]]  # fmt: skip
        c.gemini.speak_gate = asyncio.Event()
        await says(c, LOGAN, "Valor, let's ship Monday.")
        assert await until(lambda: c.gemini.spoken)
        before = (list(c.the.decided), list(c.page.posts[1:]), [r for r, _ in c.gemini.results])
        c.gemini.speak_gate.set()
        await settle(c.the)
        await finish(c)
        return c, seen, before

    c, seen, (decided, posts, results) = run(go())
    assert decided == [] and posts == [] and results == ["person", "settle", "post"]  # looked up, held
    assert seen["decided at first sound"] == 1
    assert c.page.posts[1:] == ["Ship Monday.", "The plan: ship Monday."]


def test_the_person_answered_going_on_before_valors_first_sound_drops_the_turn_and_what_it_held(
    dsn, team, quick
):
    """The transcriber ends some turns in a pause (2 or 3 of 82 under 1.5 s
    in Tom's calls, 2026-11-25): "let's go with A... actually, B" leaves
    one decision, B, one read-back posted, and the dropped turn's post and
    ask never done."""

    async def go():
        c = await in_call(dsn, team, worker=_answers("Done."))
        c.gemini.minds += [[("settle", {"readback": "Going with A."}), ("post", {"text": "A's link."}),
                            ("ask", {"question": "Is A ready?"})], ["Going with A."]]  # fmt: skip
        c.gemini.speak_gate = asyncio.Event()
        await says(c, LOGAN, "Valor, let's go with A")
        assert await until(lambda: c.gemini.spoken)
        await talk(c.the, LOGAN, 0.6)  # they go on
        assert await until(lambda: c.the.turn.dropped)
        c.gemini.speak_gate.set()
        c.gemini.minds += [[("settle", {"readback": "Going with B."})], ["B it is."]]
        await push(c.ears, *ended("actually, B."))
        await settle(c.the)
        await finish(c)
        return c, await effects(dsn, c.the.id)

    c, done_ = run(go())
    assert [d["decision"] for d in c.the.decided] == ["Going with B."]
    assert c.page.posts[1:] == ["Going with B."] and not c.the.asked
    assert said_texts(done_) == ["B it is."] and len(c.gemini.prompts) == 2


def test_a_turn_that_says_nothing_does_what_it_held_when_the_mind_is_done(dsn, team, quick):
    """In a group, Tom says "go with B" to Logan and the mind settles it
    without a word: the decision is recorded once."""

    async def go():
        c = await in_call(dsn, team)
        c.gemini.minds += [[("settle", {"readback": "Going with B.", "by": [TOM]})], []]
        await says(c, TOMS, "Logan, Valor, we go with B.")
        await settle(c.the)
        await finish(c)
        return c, await effects(dsn, c.the.id)

    c, done_ = run(go())
    assert [(d["decision"], d["by"]) for d in c.the.decided] == [("Going with B.", [TOM])]
    assert c.page.posts[1:] == ["Going with B."] and not said_texts(done_)


def test_someone_else_talking_before_valors_first_sound_drops_nothing(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        c.gemini.minds.append(["Monday."])
        c.gemini.speak_gate = asyncio.Event()
        await says(c, LOGAN, "Valor, when do we ship?")
        assert await until(lambda: c.gemini.spoken)
        await talk(c.the, TOMS, 0.6)
        c.gemini.speak_gate.set()
        await settle(c.the)
        await finish(c)
        return await effects(dsn, c.the.id)

    assert said_texts(run(go())) == ["Monday."]


def test_a_call_repeated_in_a_turn_is_done_once_and_its_last_round_writes_words(dsn, team, quick):
    """Flash-Lite called `settle` round after round, recorded it each time
    and said nothing (the live tests, 2026-11-25): one call twice in a turn
    is done once, and the turn's last round is for words only."""
    settle_ = ("settle", {"readback": "Ship Monday."})

    async def go():
        c = await in_call(dsn, team, alone())
        c.gemini.minds += [[settle_]] * (call.MIND_ROUNDS - 1) + [[settle_, "Ship Monday, then."]]
        await says(c, TOMS, "Ship it Monday.")
        await settle(c.the)
        await finish(c)
        return c, await effects(dsn, c.the.id)

    c, done_ = run(go())
    assert [d["decision"] for d in c.the.decided] == ["Ship Monday."] and c.page.posts[1:] == ["Ship Monday."]
    assert [r for _, r in c.gemini.results][1:] == ["done already: now write your reply"] * (
        call.MIND_ROUNDS - 2
    )
    assert c.gemini.words_only == [False] * (call.MIND_ROUNDS - 1) + [True]
    assert said_texts(done_) == ["Ship Monday, then."]


def test_words_written_with_only_acts_called_end_the_turn_and_a_lookup_comes_back_first(dsn, team, quick):
    """Each round is a model call of 2-4 s (the live tests, 2026-11-25): a
    reply written beside `settle` needs no round more; a lookup's answer is
    given back first."""

    async def go():
        c = await in_call(dsn, team, alone())
        c.gemini.minds += [[("settle", {"readback": "Ship Monday."}), "Monday it is."], ["Never written."]]
        await says(c, TOMS, "Ship it Monday.")
        await settle(c.the)
        unread, c.gemini.minds = (
            list(c.gemini.minds),
            [[("person", {"about": "Logan"}), "One moment."], ["Logan owns QA."]],
        )
        await says(c, TOMS, "Who is Logan?")
        await settle(c.the)
        await finish(c)
        return unread, await effects(dsn, c.the.id)

    unread, done_ = run(go())
    assert unread == [["Never written."]]  # no round after the words
    assert said_texts(done_) == ["Monday it is.", "One moment. Logan owns QA."]


def test_a_newer_turn_drops_one_not_yet_said_and_waits_out_one_that_is(dsn, team, quick):
    async def go():
        page = Page()
        c = await in_call(dsn, team, page)
        c.gemini.think_gate = asyncio.Event()
        await says(c, LOGAN, "Valor, one thing.")
        assert await until(lambda: c.gemini.prompts)
        first = c.the.turn
        c.gemini.minds += [["Both things."]]
        await says(c, TOMS, "Valor, and another.")
        c.gemini.think_gate.set()
        await settle(c.the)
        page.ahead = 1.0  # the next reply plays a second
        c.gemini.minds += [["Third."], ["Fourth."]]
        await says(c, LOGAN, "Valor, a third.")
        assert await until(lambda: c.the.out is not None)
        playing = c.the.out
        await says(c, TOMS, "Valor, and a fourth.")
        assert await until(lambda: len(c.gemini.spoken) == 3)
        overlap = c.the.out is playing and len(page.streams)
        await settle(c.the)
        await finish(c)
        return first, overlap, await effects(dsn, c.the.id)

    first, overlap, done_ = run(go())
    assert first.dropped and said_texts(done_) == ["Both things.", "Third.", "Fourth."]
    assert overlap  # the fourth waited for the third to play out


# -- the chat, the worker, decisions -------------------------------------------------


def test_a_typed_line_is_answered_in_the_chat_never_aloud(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team, worker=_answers("Monday."))
        c.gemini.minds.append(["It is in the ledger."])
        c.the.chat(LOGAN, "Valor, where is the invoice?")
        await settle(c.the)
        c.gemini.minds += [[("ask", {"question": "When is the launch?"})], ["I will check."]]
        c.the.chat(TOMS, "And the launch?")
        await settle(c.the)
        await finish(c)
        return c, await effects(dsn, c.the.id)

    c, done_ = run(go())
    assert now_of(c.gemini.prompts[0]) == call.NOW_TYPED.format(name="Logan")
    assert "Logan (typed): Valor, where is the invoice?" in c.gemini.prompts[0]
    assert c.page.streams == [] and not c.gemini.spoken and not said_texts(done_)
    assert c.page.posts[1:4] == ["It is in the ledger.", "I will check.", 'On "When is the launch?": Monday.']
    post = next(
        e
        for e in done_
        if e["type"] == "meet.send_message" and e["payload"]["text"] == "It is in the ledger."
    )
    copy = next(e for e in done_ if e["type"] == "slack.send_message")
    assert post["kind"] == "done" and copy["target"] == team.chat
    assert copy["payload"] == {"text": "It is in the ledger.", "reply_to": at(1)}
    assert not c.the.aloud and not c.the.say_next  # asked in the chat, answered in the chat


def test_a_typed_answer_the_mind_posted_itself_is_not_posted_again(dsn, team, quick):
    """Posted twice in the live Meet test (2026-11-25)."""

    async def go():
        c = await in_call(dsn, team)
        c.gemini.minds.append(
            [("post", {"text": "Launch: Thursday the 4th."}), "The launch is Thursday the 4th."]
        )
        c.the.chat(LOGAN, "Valor, can you post the launch date here?")
        await settle(c.the)
        await finish(c)
        return c

    assert run(go()).page.posts[1:] == ["Launch: Thursday the 4th."]


def test_a_failed_post_gets_no_copy_and_is_said_aloud_once_valor_is_quiet(dsn, team, quick):
    """Every post of a call failed and Live was told each went, so it said
    nothing (the Captain, 2026-11-18): the join line among them."""

    async def go():
        page = Page()
        page.post_ok = False
        gemini = Gemini()
        gemini.minds += [["Valor here, recording for notes."]]
        c = await in_call(dsn, team, page, gemini)
        assert await until(lambda: c.gemini.prompts)
        await settle(c.the)
        await finish(c)
        return c, await effects(dsn, c.the.id)

    c, done_ = run(go())
    assert [e["kind"] for e in done_ if e["payload"].get("text") == call.JOIN_LINE] == ["failed"]
    assert not [e for e in done_ if e["type"] == "slack.send_message"]
    assert [now_of(p) for p in c.gemini.prompts] == [call.UNPOSTED.format(text=call.JOIN_LINE)]
    assert said_texts(done_) == ["Valor here, recording for notes."]


def test_a_post_failing_while_valor_speaks_is_said_once_the_reply_is_over_in_one_turn(dsn, team, quick):
    """Said mid-reply, it would cut his own answer off; a post the chat
    split in two, both parts failed, is one turn."""

    async def go():
        page = Page()
        c = await in_call(dsn, team, page)
        page.ahead, page.post_ok = 1.5, False
        c.gemini.minds.append(["Monday, after QA."])
        await says(c, LOGAN, "Valor, when?")
        assert await until(lambda: c.the.out is not None)
        c.the.posts.put_nowait(call.Post(None, "a" * 400 + "\n" + "b" * 400, True))
        assert await until(lambda: c.the.unposted)
        during = (c.the.out is not None, len(c.gemini.prompts))
        assert await until(lambda: len(c.gemini.prompts) == 2)
        after = c.the.out
        await finish(c)
        return c, during, after

    c, during, after = run(go())
    assert during == (True, 1) and after is None
    assert "a" * 400 in c.gemini.prompts[1] and "b" * 400 in c.gemini.prompts[1]


def test_a_worker_answer_is_kept_for_every_turn_and_never_a_turn_of_its_own(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team, worker=_answers("Tuesday."))
        c.gemini.minds += [[("ask", {"question": "When did the import ship?"})], []]
        c.the.chat(LOGAN, "When did the import ship?")
        assert await until(lambda: 'On "When did the import ship?": Tuesday.' in c.page.posts)
        await settle(c.the)
        await asyncio.sleep(0.5)
        quiet = len(c.gemini.prompts)
        await says(c, LOGAN, "Valor, anything else?")
        await settle(c.the)
        await finish(c)
        return c, quiet

    c, quiet = run(go())
    kept = call.ANSWERED.format(question="When did the import ship?", answer="Tuesday.")
    assert quiet == 1 and c.the.told == [kept] and f"- {kept}" in c.gemini.prompts[1]


@pytest.mark.parametrize("when", ["valor speaking", "the other speaking", "a pause"])
def test_an_answer_asked_aloud_opens_the_next_reply_and_is_never_a_turn_of_its_own(dsn, team, quick, when):
    """Tom's two calls, 2026-11-19: the answer's turn began 0.45 s after
    Valor's own question and Tom, answering it, cut it off. Arriving while
    Valor's reply plays, while the other person talks, or in a quiet pause,
    it starts no turn; the next reply opens with it, once."""

    async def go():
        gate = asyncio.Event()

        async def worker(question):
            await gate.wait()
            return "The import, on Tuesday."

        page = Page()
        c = await in_call(dsn, team, page, worker=worker)
        c.gemini.minds += [
            [("ask", {"question": "What did we ship?"})],
            ["I'll check. Meanwhile, the next item."],
        ]
        if when == "valor speaking":
            page.ahead = 2.0
        await says(c, LOGAN, "Valor, what did we ship?")
        assert await until(lambda: c.the.asked)
        gate.set()
        if when == "the other speaking":
            await talk(c.the, TOMS, 0.6)
        assert await until(lambda: c.the.say_next)
        await asyncio.sleep(0.5)
        quiet = len(c.gemini.prompts)
        c.gemini.minds += [["It was the import, on Tuesday; details in the chat."], ["Yes."]]
        await settle(c.the)
        await says(c, LOGAN, "Valor, and?")
        await settle(c.the)
        await says(c, LOGAN, "Valor, thanks.")
        await settle(c.the)
        await finish(c)
        return c, quiet

    c, quiet = run(go())
    say = call.SAY_ANSWER.format(question="What did we ship?", answer="The import, on Tuesday.")
    assert quiet == 1 and say in c.gemini.prompts[1] and say not in c.gemini.prompts[2]
    assert c.the.told == [call.SAID.format(question="What did we ship?", answer="The import, on Tuesday.")]
    assert not c.the.say_next


def test_an_answer_whose_post_fails_is_said_as_unposted_and_never_kept_as_posted(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team, worker=_answers("The import, on Tuesday."))
        await settle(c.the)
        c.page.post_ok = False
        c.gemini.minds += [[("ask", {"question": "What did we ship?"})], ["I'll check."]]
        await says(c, LOGAN, "Valor, what did we ship?")
        assert await until(lambda: len(c.gemini.prompts) == 2)
        await settle(c.the)
        await finish(c)
        return c

    c = run(go())
    assert now_of(c.gemini.prompts[1]).endswith('On "What did we ship?": The import, on Tuesday.')
    assert not c.the.told and not c.the.say_next


def test_a_settle_posts_the_readback_with_its_settles_and_copies_it(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        c.gemini.minds += [[("settle", {"ids": ["t1/q1", "t2/q2"], "readback": "Ship Monday; drop IE."})], []]
        await says(c, TOMS, "Valor, ship Monday and drop IE.")
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


def test_a_settle_resolves_bare_ids_against_the_open_decisions_and_says_what_matched_none(
    dsn, team, quick, monkeypatch
):
    """The 2026-11-25 call's one settle carried no `settles`: its ids were
    not `task/question`. A bare question id, or a task id with exactly one
    open question, is that decision; a task with two open questions, or an
    id that is no open decision, records nothing and the tool's answer says
    so."""
    from core import board

    open_ = [{"task_id": t, "question_id": q} for t, q in
             [("t1", "q1"), ("t2", "q2"), ("t3", "q3"), ("t4", "q4a"), ("t4", "q4b")]]  # fmt: skip

    async def snapshot(conn, spec):
        return {"open": open_}

    monkeypatch.setattr(board, "board_spec", lambda name: object())
    monkeypatch.setattr(board, "snapshot", snapshot)

    async def go():
        c = await in_call(dsn, team, alone())
        ids = ["t1/q1", "q2", "t3", "t4", "zz"]
        c.gemini.minds += [[("settle", {"ids": ids, "readback": "Rename it."})], ["Rename it."]]
        await says(c, TOMS, "Rename it.")
        await settle(c.the)
        await finish(c)
        return c, await effects(dsn, c.the.id)

    c, done_ = run(go())
    (post,) = [e for e in done_ if e["type"] == "meet.send_message" and e["payload"].get("settles")]
    assert post["payload"]["settles"] == [{"task_id": "t1", "question_id": "q1"},
        {"task_id": "t2", "question_id": "q2"}, {"task_id": "t3", "question_id": "q3"}]  # fmt: skip
    ((name, result),) = c.gemini.results
    assert name == "settle" and "no open decision is t4, zz" in result and "q4a" not in str(post)
    assert [(d["decision"], d["by"]) for d in c.the.decided] == [("Rename it.", [TOM])]  # a 1:1: Tom decided


def test_a_question_settled_in_the_call_is_never_offered_as_open_again(dsn, team, quick, monkeypatch):
    """Settled questions are answered only once the call ends, and the list
    still had one Tom settled minutes before (the live tests, 2026-11-25)."""

    async def decisions(conn, project):
        return "Open for Tom:\n- t1/q1: Ship Monday?\n- t2/q2: Drop IE?"

    monkeypatch.setattr(call, "decisions", decisions)

    async def go():
        c = await in_call(dsn, team, alone())
        c.gemini.minds += [[("settle", {"ids": ["t1/q1"], "readback": "Ship Monday."})], ["Shipping Monday."],
                           [("open_decisions", {})], ["Only IE is open."]]  # fmt: skip
        await says(c, TOMS, "Ship it Monday.")
        await settle(c.the)
        await says(c, TOMS, "What's open?")
        await settle(c.the)
        await finish(c)
        return c

    c = run(go())
    assert c.gemini.results[-1] == ("open_decisions", "Open for Tom:\n- t2/q2: Drop IE?")


def test_a_settle_with_no_ids_or_no_board_still_posts_its_readback_and_records_none(dsn, team, quick):
    """With no ids, or bare ids on a project with no board, the read-back
    is still posted and in the state as decided, with no `settles`, never
    an error."""

    async def go():
        c = await in_call(dsn, team, alone())
        c.gemini.minds += [[("settle", {"readback": "Keep it weekly."})], ["Noted."],
                           [("settle", {"ids": ["q9"], "readback": "No to IE."})], ["Noted."]]  # fmt: skip
        await says(c, TOMS, "Keep it weekly.")
        await settle(c.the)
        await says(c, TOMS, "And no to IE.")
        await settle(c.the)
        await finish(c)
        return c, await effects(dsn, c.the.id)

    c, done_ = run(go())
    posts = [e["payload"] for e in done_ if e["type"] == "meet.send_message"]
    assert {"text": "Keep it weekly."} in posts and {"text": "No to IE."} in posts
    assert [(d["decision"], d["settles"]) for d in c.the.decided] == [
        ("Keep it weekly.", []),
        ("No to IE.", []),
    ]
    assert c.gemini.results[0][1] == "recorded" and "no open decision is q9" in c.gemini.results[1][1]


def test_each_tool_answers_what_the_mind_needs_to_say(dsn, team, quick):
    """An ask the worker is not ready for says so, one already asked gives
    its answer or that it is coming, a call missing its arguments says so,
    and a lookup's result is the answer."""

    async def go():
        c = await in_call(dsn, team, alone(), worker=_answers("Tuesday."), ready=False)
        c.gemini.minds += [[("ask", {"question": "When did we ship?"}), ("post", {}), ("open_decisions", {})],
                           ["I'll check."]]  # fmt: skip
        await says(c, TOMS, "When did we ship?")
        await settle(c.the)
        c.gemini.minds += [
            [("ask", {"question": "when did we ship"}), ("backlog", {"about": "x"})],
            ["Tuesday."],
        ]
        await says(c, TOMS, "So when?")
        await settle(c.the)
        await finish(c)
        return c

    c = run(go())
    results = dict(c.gemini.results[:3])
    assert "needs a moment" in results["ask"] and results["post"] == "not done: post needs its arguments"
    assert results["open_decisions"].startswith("Open for Tom:")
    assert c.gemini.results[3] == ("ask", "already asked; the answer: Tuesday.")
    assert c.gemini.results[4] == ("backlog", "not done: backlog needs its arguments")  # no Sheet
    assert c.page.posts.count('On "When did we ship?": Tuesday.') == 1


def test_the_mind_is_told_valors_tools_how_to_answer_and_when_to_leave():
    text = " ".join(call.system("acme").split())
    assert [t["name"] for t in call.TOOLS] == ["ask", "post", "open_decisions", "settle", "person", "leave"]
    assert text.startswith(
        "You are Valor Engels, in a live Google Meet call as yourself, on the acme project."
    )
    assert "write nothing at all: you stay silent" in text and "never lists, markdown, ids or symbols" in text
    assert (
        "call `settle` once, before you write" in text
        and "Asked what was decided, answer from the meeting now, with no tool" in text
    )
    # Tom, 2026-11-12: anyone may ask Valor to leave, goodbyes all round end it, a scheduled end does not.
    assert "Call `leave` when anyone in the call asks you to leave" in text and "said goodbye" in text
    assert "Never leave because the meeting's scheduled time has passed" in text
    assert "Tom" not in call.TOOLS[-1]["description"]


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
    told = call.system("acme")
    assert persona.section(tmp_path / "persona", "In a call") in told and "Say {this} first." in told
    # The mechanics defer to it: no second copy of the conduct in the template.
    for gone in ("with your recommendation, and let them choose", "Speak in short, plain",
                 "do not steer the call", "take it offline with Tom"):  # fmt: skip
        assert gone not in call.INSTRUCTIONS


def test_what_valor_hears_is_transcribed_as_english_at_the_transcribers_quick_end_of_turn(monkeypatch):
    """The step-1 test (2026-11-25): the transcriber's own end of turn at
    high end-of-speech sensitivity cut no one off in a pause of 1.5 s or
    less, and ended a turn 1.3 s after the last word."""
    got = {}

    class Live:
        def connect(self, model, config):
            got.update(model=model, config=config)

    the = call.Gemini.__new__(call.Gemini)
    the.client = SimpleNamespace(aio=SimpleNamespace(live=Live()))
    the.hear()
    config = got["config"]
    assert got["model"] == "gemini-3.5-transcribe-live" and config.response_modalities == ["TEXT"]
    assert config.input_audio_transcription.language_codes == ["en-US"]
    detect = config.realtime_input_config.automatic_activity_detection
    assert detect.end_of_speech_sensitivity == types.EndSensitivity.END_SENSITIVITY_HIGH


def test_a_missing_persona_section_is_unreadable(tmp_path):
    from core import persona

    shutil.copytree(settings.persona_dir, tmp_path / "persona")
    with pytest.raises(persona.PersonaUnreadable, match="In a meeting"):
        persona.section(tmp_path / "persona", "In a meeting")


# -- the backlog ------------------------------------------------------------------


def test_a_call_whose_project_has_a_sheet_is_offered_backlog_and_told_when_to_use_it():
    """Tom, 2026-11-13: a fast lookup of the Sheet's items, answered in the person's terms."""
    text = " ".join(call.system("acme", sheet=True).split())
    assert (
        "call `backlog` at once: with `about` set to how they described the item, in their own words" in text
    )
    assert "never its id or its title as the Sheet words it" in text and "ask which they mean" in text
    assert "`backlog`" not in call.system("acme")


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
        c = await in_call(dsn, team, alone())
        c.gemini.minds += [[("backlog", {"about": "what's the appetite on the memory item?"}), ("backlog", {}),
                            ("backlog", {"about": "fail"})], ["Large."]]  # fmt: skip
        await says(c, TOMS, "What's the appetite on the memory item?")
        await settle(c.the)
        await finish(c)
        return c

    c = run(go())
    assert "call `backlog`" in c.gemini.instructions[0] and c.gemini.tools[0][-1] == "backlog"
    one, whole, failed = [r for _, r in c.gemini.results]
    assert json.loads(one)["matches"][0]["appetite"] == "large"
    # The whole queue in its order, with only what an overview needs.
    assert json.loads(whole) == [{"id": "P-005", "lane": "bet", "rank": "", "item": "Entity memory",
                                  "value": "medium", "appetite": "large", "due": "", "status": "shaped",
                                  "blocked by": ""}]  # fmt: skip
    assert failed.startswith("the Sheet could not be read: ") and "403" in failed
    argv = [json.loads(line) for line in (tmp_path / "sheet.py.argv").read_text().splitlines()]
    assert argv == [["lookup", "SHEET-1_x", "what's the appetite on the memory item?"], ["read", "SHEET-1_x"],
                    ["lookup", "SHEET-1_x", "fail"]]  # fmt: skip


def test_a_call_as_a_projects_own_account_reads_its_sheet_as_that_account(
    dsn, team, quick, tmp_path, monkeypatch
):
    """docs/plans/9f4967d9-google-per-project.md: the call's Sheet read runs
    with the account's gws folder; own's with none."""
    fake = tmp_path / "sheet.py"
    fake.write_text(
        'import json, os\nprint(json.dumps([{"id": os.environ.get("GOOGLE_WORKSPACE_CLI_CONFIG_DIR")}]))\n'
    )
    monkeypatch.setattr(call, "SHEET_PY", fake)

    async def go(account):
        _, the = await a_call_of(dsn, team, Page(), Gemini())
        if account:
            the.info["account"] = account
        the.sheet = "SHEET-1"
        return json.loads(await the._backlog(""))[0]["id"]

    assert run(go("valor@client.test")) == str(Path.home() / ".config" / "gws-valor@client.test")
    assert run(go(None)) is None


def test_a_call_as_a_projects_own_account_names_that_accounts_sign_in(dsn, team, quick):
    page = AtSignIn()

    async def go():
        _, the = await a_call_of(dsn, team, page, Gemini())
        the.info["account"] = "valor@client.test"
        return await asyncio.wait_for(the.run(), 10)

    reason = run(go())
    assert "as valor@client.test" in reason and "meet-sign-in --account valor@client.test" in reason


# -- metering ---------------------------------------------------------------------


def test_each_turn_ear_and_voice_is_opened_then_charged_by_modality(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team, alone())
        c.gemini.minds.append(["Hi."])
        await says(c, TOMS, "Hello.")
        await push(
            c.ears, types.LiveServerMessage(usage_metadata=types.UsageMetadata.model_validate(EARS_USAGE))
        )
        await says(c, TOMS, "Hm.")  # a turn that says nothing is billed too
        await settle(c.the)
        await finish(c)
        return await bridges.rows(dsn, c.the.id)

    rows = run(go())
    opened = {r["payload"]["call_id"]: r["payload"] for r in rows if r["type"] == "gateway.opened"
              and r["payload"]["route"] == "gemini"}  # fmt: skip
    charged = [
        r["payload"] for r in rows if r["type"] == "gateway.charged" and r["payload"]["call_id"] in opened
    ]
    by = {}
    for c in charged:
        by.setdefault(c["model"], []).append(c["usd_micros"])
    mind, voice, ears = (spending.gemini_price(m) for m in ("gemini-3.8-flash", "gemini-3.8-flash-tts",
                                                            "gemini-3.5-transcribe-live"))  # fmt: skip
    assert by["gemini-3.8-flash"] == [spending.gemini_cost(MIND_USAGE, mind)] * 2
    assert by["gemini-3.8-flash-tts"] == [spending.gemini_cost(VOICE_USAGE, voice)]
    assert by["gemini-3.5-transcribe-live"] == [spending.gemini_cost(EARS_USAGE, ears)]
    assert all(not o["unpriced"] for o in opened.values())
    first = next(o for o in opened.values() if o["model"] == "gemini-3.8-flash")
    assert first["holder"] is None or first["holder"].startswith("run:")
    assert first["estimate_usd_micros"] == spending.gemini_worst_case(
        mind, call.CALL_CONTEXT, call.MIND_OUT_MOST
    )


def test_gemini_prices_each_modality_its_output_by_either_name_and_an_unknown_model_at_the_top():
    price = spending.gemini_price("gemini-3.5-flash-lite")
    assert spending.gemini_cost(MIND_USAGE, price) == -(-(3000 * 300_000 + 40 * 2_500_000) // 1_000_000)
    live = {**EARS_USAGE}  # Live's names for the output
    assert spending.gemini_cost(live, spending.gemini_price("gemini-3.5-transcribe-live")) == -(
        -(250 * 3_500_000 + 20 * 21_000_000) // 1_000_000
    )
    assert spending.gemini_price("gemini-9-unknown")["unpriced"] is True


def test_a_stopped_task_refuses_a_turn_and_the_call_leaves(dsn, team):
    async def go():
        _, the = await a_call_of(dsn, team, Page(), Gemini())
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, the.id, reason="enough", by="tom", via="chat")
        return the, await the._open("turn")

    the, opened = run(go())
    assert the.leaving.is_set() and the.hard and opened is None


# -- the ears ----------------------------------------------------------------


def test_the_transcriber_is_reopened_before_its_ten_minutes_and_nothing_said_meanwhile_is_lost(
    dsn, team, quick, monkeypatch
):
    monkeypatch.setattr(call, "EARS_S", 1.0)
    monkeypatch.setattr(call, "EARS_EARLY_S", 0.6)
    monkeypatch.setattr(call, "EARS_QUIET_S", 0.1)

    async def go():
        c = await in_call(dsn, team)
        c.gemini.hear_gate = asyncio.Event()
        assert await until(lambda: c.the.ears is None, s=3)
        await talk(c.the, LOGAN, 0.6)  # said while no transcriber is open
        c.gemini.hear_gate.set()
        assert await until(lambda: len(c.gemini.ears) == 2)
        await asyncio.sleep(0.1)
        await finish(c)
        return c

    c = run(go())
    loud_bytes = sum(len(b.data) for b in c.gemini.ears[1].sent if any(b.data))
    assert loud_bytes >= int(0.5 * call.RATE) * 2


def test_a_dropped_transcriber_reopens_and_the_call_carries_on(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team, alone())
        await push(c.ears, ConnectionError("dropped"))
        assert await until(lambda: len(c.gemini.ears) == 2 and c.the.ears is c.gemini.ears[1])
        c.ears = c.gemini.ears[1]
        c.gemini.minds.append(["Still here."])
        await says(c, TOMS, "Valor?")
        await settle(c.the)
        await finish(c)
        return c, await effects(dsn, c.the.id)

    c, done_ = run(go())
    assert call.LOST_LINE not in c.page.posts and said_texts(done_) == ["Still here."]


def test_two_failed_opens_of_the_transcriber_post_one_line_and_leave(dsn, team, quick):
    async def go():
        c = await in_call(dsn, team)
        c.gemini.fail = 5
        await push(c.ears, ConnectionError("dropped"))
        return c, await asyncio.wait_for(c.running, 15)

    c, reason = run(go())
    assert reason == "Valor lost its hearing connection"
    assert c.page.posts.count(call.LOST_LINE) == 1 and len(c.gemini.ears) == 1


@pytest.mark.parametrize(
    "why,reason",
    [("wrapped up", "everyone wrapped up and said goodbye"), ("asked", "Valor was asked to leave"),
     (None, "Valor left the call")],
)  # fmt: skip
def test_valor_leaves_once_his_goodbye_is_said_and_the_reason_says_why(dsn, team, quick, why, reason):
    """Tom, 2026-11-12: leave when someone asks, or everyone wraps up. The
    model decides from the conversation; Logan is not Tom. The reason tells
    the two apart (2026-11-18: goodbyes were recorded as "asked to leave");
    a leave with no reason still leaves."""

    async def go():
        page = Page()
        page.ahead = 0.5
        c = await in_call(dsn, team, page)
        c.gemini.minds += [[("leave", {"why": why} if why else {})], ["Bye, everyone!"]]
        await says(c, LOGAN, "Thanks everyone, that's all for today. Bye, Valor!")
        reason_ = await asyncio.wait_for(c.running, 10)
        return reason_, await effects(dsn, c.the.id)

    got, done_ = run(go())
    assert got == reason and said_texts(done_) == ["Bye, everyone!"]


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
        c.gemini.minds.append(["Monday."])
        await says(c, LOGAN, "Valor, when do we ship?")
        await settle(c.the)
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
        await says(c, LOGAN, "Hello?")
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
        await says(c, LOGAN, "Hello?")
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


def test_what_a_call_decided_becomes_dated_facts_of_its_project_and_the_report_says_to_carry_them(
    dsn, team, quick
):
    """Tom, 2026-11-19: what a call settled lives with the project's other
    PM data. Each decision in Gemini's summary is a `decision` fact of the
    call's project, dated the call and citing it, about the deciders the
    project knows as people; the end report lists them and says to carry
    them; `call.ended` keeps the title and the notes the search reads.
    With memory off, the report lists them as not recorded."""
    from core import memory, workspace

    notes = {"topic": "Launch plan", "summary": "Shipping is Monday.", "markdown": "m", "actions": [],
             "decisions": [{"decision": "Ship Monday", "by": ["Rowan Vale", "Mara Ode"]},
                           {"decision": "Drop the beta", "by": []}, {"decision": " ", "by": []}]}  # fmt: skip

    async def a_call():
        c = await in_call(dsn, team, gemini=Gemini(notes=notes))
        await says(c, LOGAN, "Valor, when do we ship?")
        await settle(c.the)
        await finish(c)
        rows = await bridges.rows(dsn, c.brief.id)
        (ended,) = [r["payload"] for r in rows if r["type"] == "child.reported"
                    and r["payload"]["about_key"].endswith(":ended")]  # fmt: skip
        (kept,) = [r["payload"] for r in await bridges.rows(dsn, c.the.id) if r["type"] == "call.ended"]
        return c, ended["text"], kept

    async def go():
        spec = workspace.Spec.load(team.project)
        async with await db.connect(dsn) as conn:
            await memory.fact_write(conn, spec, [{"op": "entity", "kind": "person", "name": "Rowan Vale"}])
        c, text, kept = await a_call()
        async with await db.connect(dsn) as conn:
            facts = (await memory.facts_read(conn, spec, {"kind": "decision"}))["facts"]
        return c, text, kept, facts

    memory.reset()
    try:
        c, text, kept, facts = run(go())
    finally:
        memory.reset()
    day = c.the.started_at.strftime("%Y-%m-%d")
    by_claim = {f["claim"]: f for f in facts}
    # Every test project's repo is "unused", so other calls' decisions share the store.
    assert {"Ship Monday (decided by Rowan Vale, Mara Ode)", "Drop the beta"} <= set(by_claim)
    assert not [c for c in by_claim if not c.strip()]
    ship = by_claim["Ship Monday (decided by Rowan Vale, Mara Ode)"]
    assert (
        ship["since"].startswith(day)
        and ship["about"] == ["Rowan Vale"]
        and by_claim["Drop the beta"]["about"] == []
    )
    assert (
        ship["source"] == f"Meet call {CODE} {c.the.started_at.astimezone().strftime('%Y-%m-%d')}, "
        "Launch plan - Logan, Tom, Valor"
    )
    assert "now facts of the project (kind decision): carry each that changes a backlog item" in text
    assert f"- Ship Monday (fact {ship['fact']})" in text and "- Drop the beta (fact " in text
    assert kept["title"] == "Launch plan - Logan, Tom, Valor"
    assert kept["notes"] == "Shipping is Monday.\nShip Monday\nDrop the beta"


def test_a_decision_settled_in_the_call_is_a_fact_before_it_ends_and_its_record_lists_it_once(
    dsn, team, quick
):
    """P-041: a decision is recorded the moment it is settled, a fact of the
    project citing the open decisions it settles, about whoever decided (in
    a 1:1, with no `by`, the one other person). The summary is told what was
    recorded; the end lists it in notes.md, summary.json and the report,
    and one the summary restates in other words (`restates`) is not written
    twice (the 2026-11-25 case: one decision under two labels)."""
    from core import memory, workspace

    notes = {"topic": "Shed", "summary": "The shed.", "markdown": "m", "actions": [],
             "decisions": [{"decision": "The shed goes teal, not green", "by": ["Logan"], "restates": 1},
                           {"decision": "Order the teal paint by Friday", "by": []}]}  # fmt: skip

    async def go():
        spec = workspace.Spec.load(team.project)
        async with await db.connect(dsn) as conn:
            await memory.fact_write(conn, spec, [{"op": "entity", "kind": "person", "name": "Logan"}])
        page = Page()
        page.people = page.people[:2]  # a 1:1 with Logan
        c = await in_call(dsn, team, page, gemini=Gemini(notes=notes))
        c.gemini.minds += [
            [("settle", {"ids": ["t1/q1"], "readback": "Paint the shed teal."})],
            ["Teal it is."],
        ]
        await says(c, LOGAN, "Let's paint the shed teal.")
        assert await until(lambda: c.the.decided and c.the.decided[0].get("fact"))
        async with await db.connect(dsn) as conn:
            during = (await memory.facts_read(conn, spec, {"kind": "decision"}))["facts"]
        await settle(c.the)
        store = c.the.store
        await finish(c)
        rows = await bridges.rows(dsn, c.brief.id)
        (ended,) = [r["payload"] for r in rows if r["type"] == "child.reported"
                    and r["payload"]["about_key"].endswith(":ended")]  # fmt: skip
        async with await db.connect(dsn) as conn:
            after = (await memory.facts_read(conn, spec, {"kind": "decision"}))["facts"]
        return c, during, after, ended["text"], store

    memory.reset()
    try:
        c, during, after, text, store = run(go())
    finally:
        memory.reset()
    claim = "Paint the shed teal. (decided by Logan) (settles t1/q1)"
    (fact,) = [f for f in during if f["claim"] == claim]
    assert fact["about"] == ["Logan"]
    assert [f["claim"] for f in after].count(claim) == 1 and not [
        f for f in after if "goes teal" in f["claim"]
    ]
    assert "Order the teal paint by Friday" in {f["claim"] for f in after}
    assert c.gemini.recorded == ["1. Paint the shed teal."]
    (folder,) = store.iterdir()
    assert json.loads((folder / "summary.json").read_text())["decisions"] == [
        "Paint the shed teal.", "Order the teal paint by Friday"]  # fmt: skip
    assert "Paint the shed teal." in (folder / "notes.md").read_text()
    assert (
        f"- Paint the shed teal. (fact {fact['fact']})" in text
        and "- Order the teal paint by Friday (fact " in text
    )
    assert "The shed goes teal" not in text


def test_a_decision_whose_fact_failed_in_the_call_is_written_at_its_end(dsn, team, quick, monkeypatch):
    """A fact write that fails mid-call leaves the decision in the state
    with no fact; the end writes it with the summary's others."""
    from core import memory

    real, calls = memory.fact_write, []

    async def flaky(conn, spec, writes, **kw):
        calls.append([w["claim"] for w in writes])
        if len(calls) == 1:
            raise memory.Unavailable("the memory store could not be reached (down)")
        return await real(conn, spec, writes, **kw)

    monkeypatch.setattr(memory, "fact_write", flaky)
    notes = {"topic": "Gate", "summary": "s", "markdown": "m", "actions": [], "decisions": []}

    async def go():
        c = await in_call(dsn, team, gemini=Gemini(notes=notes))
        c.gemini.minds += [
            [("settle", {"readback": "Fix the gate hinge first.", "by": ["Tom Counsell"]})],
            ["Noted."],
        ]
        await says(c, TOMS, "Valor, fix the gate hinge first.")
        assert await until(lambda: len(calls) == 1)
        await settle(c.the)
        await finish(c)
        rows = await bridges.rows(dsn, c.brief.id)
        (ended,) = [r["payload"] for r in rows if r["type"] == "child.reported"
                    and r["payload"]["about_key"].endswith(":ended")]  # fmt: skip
        return c, ended["text"]

    memory.reset()
    try:
        c, text = run(go())
    finally:
        memory.reset()
    assert calls == [["Fix the gate hinge first. (decided by Tom Counsell)"]] * 2
    assert (
        c.the.decided[0]["fact"] and f"- Fix the gate hinge first. (fact {c.the.decided[0]['fact']})" in text
    )


def test_a_turn_valor_spoke_asks_the_worker_for_an_update_and_one_that_said_nothing_does_not(
    dsn, team, quick
):
    """P-041: the state is updated after every exchange; a silent turn is none."""
    updates = []

    async def preparer(prompt):
        updates.append(prompt)
        return "Topics: the shed, then the gate."

    async def go():
        starts = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        c = await in_call(dsn, team, alone(), preparer=preparer, starts=starts)
        assert await until(lambda: c.the.topics)
        await settle(c.the)
        first = len(updates)
        await says(c, TOMS, "Hm.")  # the mind writes nothing
        await settle(c.the)
        silent = len(updates)
        c.gemini.minds.append(["The shed is next."])
        await says(c, TOMS, "What's next?")
        await settle(c.the)
        await finish(c)
        return first, silent, len(updates)

    first, silent, spoke = run(go())
    assert first == silent == 1 and spoke == 2


def test_a_long_state_reaches_every_turn_whole_and_the_call_so_far_is_cut_instead(dsn, team):
    """A state over a quarter of `CALL_SO_FAR` is in each turn whole, beside
    the newest of the call so far."""
    topics = (
        "\n".join(f"- Topic {i}: open; " + "detail " * 40 for i in range(60)) + "\n- The last topic: open."
    )
    assert len(topics) > call.CALL_SO_FAR // 4

    async def go():
        _, the = await a_call_of(dsn, team, Page(), Gemini())
        the.topics = topics
        the.lines = [f"[00:{i % 60:02d}] Logan (spoke): line {i} " + "word " * 30 for i in range(400)]
        return the.prompt(call.Turn("t", "answer", False, None))

    prompt = run(go())
    assert topics in prompt and the_last(prompt) and len(prompt) <= call.CALL_SO_FAR + 1000


def the_last(prompt):
    return "line 399 " in prompt and "line 0 " not in prompt


def test_with_memory_off_the_report_lists_what_the_call_decided_as_not_recorded(
    dsn, team, quick, monkeypatch
):
    from core import memory

    notes = {"topic": "Launch plan", "summary": "s", "markdown": "m", "actions": [],
             "decisions": [{"decision": "Ship Monday", "by": []}, {"decision": "Drop the beta"}]}  # fmt: skip
    monkeypatch.setattr(memory, "on", lambda: False)

    async def go():
        c = await in_call(dsn, team, gemini=Gemini(notes=notes))
        await says(c, LOGAN, "Valor, when do we ship?")
        await settle(c.the)
        await finish(c)
        rows = await bridges.rows(dsn, c.brief.id)
        (ended,) = [r["payload"] for r in rows if r["type"] == "child.reported"
                    and r["payload"]["about_key"].endswith(":ended")]  # fmt: skip
        return ended["text"]

    off = run(go())
    assert "Decided in the call, not recorded as facts (memory is off on this Mac); record each with" in off
    assert "- Ship Monday\n- Drop the beta" in off


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
        "before the call:\nThe launch.\n\nDecided and recorded during the call, numbered:\n(none)\n\n"
        "The transcript:\n[0:01] Logan (spoke): hi"
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
        await says(c, LOGAN, "Valor, leave.")
        await settle(c.the)
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
        await says(c, TOMS, "Okay Valor, please leave the call.")
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
        f"Google wants Valor's sign-in as valor@yuda.me on {settings.machine} verified again; at that Mac run: "
        f"{google.SIGN_IN}"
    )
    assert page.posts == [] and "confirmidentifier" in capsys.readouterr().out
    assert page.done == ["join", ("renew", page.url)]
    ended = never_in(rows, got)
    assert settings.machine in ended["text"] and google.SIGN_IN in ended["text"]


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


def test_a_steer_of_the_call_is_posted_in_its_chat_not_copied_and_kept_for_every_turn(dsn, team, quick):
    """2026-11-18: a corrected answer from the chat session was only posted,
    and never reached the voice. A steer from before the call is neither."""

    async def go():
        page, gemini = Page(), Gemini()
        _, the = await a_call_of(dsn, team, page, gemini)
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, the.id, "message.steered", {"text": "An old steer."})
        running = asyncio.create_task(the.run())
        assert await until(lambda: gemini.ears and the.ears is not None)
        assert await until(lambda: call.JOIN_LINE in page.posts)
        await asyncio.sleep(0.3)
        async with await db.connect(dsn) as conn:
            await ledger.append(
                conn, the.id, "message.steered", {"text": "Logan is running five minutes late."}
            )
        assert await until(lambda: len(page.posts) == 2)
        c = SimpleNamespace(the=the, page=page, gemini=gemini, running=running, ears=gemini.ears[-1])
        await says(c, TOMS, "Valor, where's Logan?")
        await settle(the)
        await finish(c)
        return c, await effects(dsn, the.id)

    c, done_ = run(go())
    assert c.page.posts[1] == "Logan is running five minutes late." and "An old steer." not in c.page.posts
    assert not any(e["type"] == "slack.send_message" for e in done_)
    steer = call.STEERED.format(text="Logan is running five minutes late.")
    assert (
        c.the.told == [steer]
        and c.gemini.prompts[0].count(steer) == 1
        and "An old steer." not in c.gemini.prompts[0]
    )


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
        assert await until(lambda: 'On "How many signups last week?": 212 signups.' in c.page.posts)
        c.gemini.minds += [[("ask", {"question": "How many signups last week?"})], ["212 last week."]]
        await says(c, LOGAN, "Valor, how many signups last week?")
        await settle(c.the)
        await finish(c)
        return c

    c = run(go())
    assert asked == ["How many signups last week?"]
    assert not any("Look up" in p or "look up" in p for p in c.page.posts)
    assert 'On "How many signups last week?": 212 signups.' in c.page.posts
    # Given as one asked aloud: the next reply opens with it, then it is kept as answered.
    assert (
        call.SAY_ANSWER.format(question="How many signups last week?", answer="212 signups.")
        in c.gemini.prompts[0]
    )
    assert c.the.told == [call.SAID.format(question="How many signups last week?", answer="212 signups.")]


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
        await says(c, LOGAN, "Hello?")
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


def test_a_question_asked_in_turns_a_moment_apart_is_one_worker_turn_and_one_post(dsn, team, quick):
    """Live asked "When is the launch?" in two turns a moment apart, and
    the answer was posted twice (the live test, 2026-11-05)."""

    async def go():
        asked = []

        async def worker(question):
            asked.append(question)
            return "Monday."

        c = await in_call(dsn, team, alone(), worker=worker)
        for words in ("When is the launch?", "when is the launch", "When is the launch?"):
            c.gemini.minds += [[("ask", {"question": words})], ["Checking."]]
            await says(c, TOMS, "The launch?")
            await settle(c.the)
        await finish(c)
        return asked, c.page.posts, [r for _, r in c.gemini.results]

    asked, posts, results = run(go())
    assert asked == ["When is the launch?"]
    assert posts.count('On "When is the launch?": Monday.') == 1
    assert results[0] == call.ASKED and results[1:] == ["already asked; the answer: Monday."] * 2


def test_side_talk_said_with_others_there_is_not_answered_once_they_leave(dsn, team, quick):
    """Sam's "could you grab me a coffee?", said with Tom there, was
    answered once Tom left and Sam was alone with Valor (the live test,
    2026-11-05)."""

    async def go():
        page = Page()
        c = await in_call(dsn, team, page)
        await says(c, LOGAN, "Tom, could you grab me a coffee?")
        await settle(c.the)
        page.people = page.people[:2]  # Tom leaves
        await until(lambda: c.the.present == {LOGAN})
        await asyncio.sleep(1.0)
        await finish(c)
        return c.gemini.prompts

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
    text = call.system("acme")
    assert "as the project's manager" in text and "priority" in text


# -- the database restarting under a call ---------------------------------------


@pytest.fixture
def restart(dsn, monkeypatch):
    """What a restart of the server does to a call: every session dropped
    (`terminate_all`), and connections refused while `down` is set. The
    test's own connections go through `real`. `heard` is set once the call's
    orders lane reads its listener, past the row it starts after: a drop
    before then is no restart under a call but one before it listens."""
    from tests.test_health import terminate_all

    real, down, heard = db.connect, asyncio.Event(), asyncio.Event()
    listen = db.listen

    async def listening(*a, **kw):
        conn = await listen(*a, **kw)
        notifies = conn.notifies

        def told(*a, **kw):
            heard.set()
            return notifies(*a, **kw)

        conn.notifies = told
        return conn

    async def refusing(*a, **kw):
        if down.is_set():
            raise psycopg.OperationalError("connection is bad: No such file or directory")
        return await real(*a, **kw)

    monkeypatch.setattr(db, "connect", refusing)
    monkeypatch.setattr(db, "listen", listening)
    monkeypatch.setattr(db, "RELISTEN_S", 0.05)

    def drop():
        down.set()
        terminate_all(dsn)

    return SimpleNamespace(real=real, down=down, drop=drop, heard=heard)


async def held_call(dsn, team, restart):
    """A call under way as `python -m core call` runs one: started, its locks
    held, its orders lane listening."""
    c = await in_call(dsn, team)
    await asyncio.wait_for(restart.heard.wait(), 5)
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
        c = await held_call(dsn, team, restart)
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
        return c.the.id, posted, lock[0], swept, still, reason, await bridges.rows(dsn, c.the.id)

    c_id, posted, free, swept, still, reason, rows = run(go())
    # Other tests' calls share the database: only this one is looked at.
    assert posted and not free and c_id not in swept and still
    assert reason == "the call was stopped"
    (ended,) = [r["payload"] for r in rows if r["type"] == "call.ended"]
    assert ended["reason"] == "the call was stopped"


def test_a_call_the_sweep_ended_while_its_database_was_down_leaves_and_is_ended_once(
    dsn, team, quick, restart
):
    async def go():
        c = await held_call(dsn, team, restart)
        restart.drop()
        async with await restart.real(dsn) as conn:
            # Its lock is free once the session holding it has exited; other
            # tests' calls share the database, so only this one is looked for.
            for _ in range(50):
                if swept := c.the.id in await call.sweep(conn):
                    break
                await asyncio.sleep(0.05)
        restart.down.clear()
        reason = await asyncio.wait_for(c.running, 15)
        await c.the.held.close()
        return swept, reason, await bridges.rows(dsn, c.the.id)

    swept, reason, rows = run(go())
    assert swept and reason == "the call was ended while the database was down"
    assert [r["payload"]["reason"] for r in rows if r["type"] == "call.ended"] == [
        "Valor's call process stopped"
    ]


def test_a_spoken_answer_answers_what_was_asked_with_the_specific_or_says_it_does_not_know():
    """Tom, 2026-11-13: asked for an example and for the ranking's criteria,
    Valor restated the goal and the order."""
    text = " ".join(call.system("acme").split())
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
    text = " ".join(call.system("acme").split())
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
        c = await in_call(dsn, team, alone())
        c.gemini.minds += [
            [("person", {"about": "the finance lead"}), ("person", {"about": "the plumber"})],
            ["Rowan."],
        ]
        await says(c, TOMS, "Who is the finance lead, and the plumber?")
        await settle(c.the)
        await finish(c)
        return c

    memory.reset()
    try:
        c = run(go())
    finally:
        memory.reset()
    told = " ".join(c.gemini.instructions[0].split())
    assert "Call `person` only when someone asks about a person, or before you say what a person" in told
    assert "a person hears only their name, role and organisation" in told
    found, none = [r for _, r in c.gemini.results]
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
        assert await until(lambda: the.gemini.ears and the.ears is not None)
        the.gemini.minds.append(["Hi."])
        await says(SimpleNamespace(the=the, ears=the.gemini.ears[-1]), LOGAN, "Valor, hi.")
        await settle(the)
        the.leave("done")
        await asyncio.wait_for(running, 15)
        return the, await effects(dsn, the.id, link=True)

    the, done_ = run(go())
    assert not _links(done_) and not the.reading
    assert "still reading up" not in the.gemini.prompts[0]


def test_the_reading_is_the_states_first_topics_never_posted_and_a_question_waits_for_it(dsn, team, quick):
    """P-033: while the worker reads up, each turn is told it is still
    reading; the reading is the state's first topics, read by the next turn
    with the line gone, never posted. It is not added to the notes before
    the call, where it would sit stale beside the state (P-041). A question
    asked meanwhile waits for it on the one worker lane."""
    gate, order = asyncio.Event(), []

    async def reader():
        await gate.wait()
        order.append("read")
        return "The launch moved to Friday (Logan, 2026-11-15)."

    async def worker(question):
        order.append(question)
        return "Friday."

    async def go():
        c = await in_call(dsn, team, alone(), worker=worker, reader=reader)
        c.gemini.minds += [[("ask", {"question": "When is the launch?"})], ["Still reading up; I'll check."]]
        await says(c, TOMS, "When is the launch?")
        await settle(c.the)
        waited = list(order)
        gate.set()
        assert await until(lambda: len(order) == 2)
        await settle(c.the)
        await says(c, TOMS, "Where are we?")
        await settle(c.the)
        await finish(c)
        return c, waited

    c, waited = run(go())
    assert waited == [] and order == ["read", "When is the launch?"]
    first, second = c.gemini.prompts
    assert call.READING_LINE in first and "moved to Friday" not in first
    assert call.READING_LINE not in second and "moved to Friday" in second
    assert (
        second.count("The meeting now") == 1 and "two decisions open" not in second
    )  # the state, not the old notes
    assert not [p for p in c.page.posts if "moved to Friday" in p]
    assert "moved to Friday" not in c.the.info["prep"]


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
    assert call.READ_NONE in c.the.state() and c.the.state().startswith("The meeting now")
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
        c = await in_call(dsn, team, alone())
        c.gemini.minds += [
            [("person", {"about": "everyone"}), ("person", {"about": "Who's on this team?"})],
            ["Tom."],
        ]
        await says(c, TOMS, "Who's on this team?")
        await settle(c.the)
        await finish(c)
        return c

    memory.reset()
    try:
        c = run(go())
    finally:
        memory.reset()
    told = " ".join(c.gemini.instructions[0].split())
    assert 'Asked who is on the team or who you have records of, call `person` with "everyone"' in told
    (person,) = [t for t in call.TOOLS if t["name"] == "person"]
    assert 'With "everyone", the project\'s people, its own team first' in person["description"]
    roster, again = [r for _, r in c.gemini.results]
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
    told = " ".join((call.system("acme") + call.READING_LINE).split())
    assert "(pricing, terms, its people)" not in told and "(pricing, terms, hiring or pay)" in told
    assert "is ordinary work: say in a sentence that it is noted" in told
    # P-033's reading line no longer narrows the call to three tools.
    assert "everything else goes on as usual" in told
    worker = " ".join((Path(__file__).parent.parent / "skills/sdlc/call.md").read_text().split())
    assert "people matters" not in worker and "hiring, pay, performance, disputes" in worker


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
        c.gemini.minds.append(["Noted."])
        await says(c, LOGAN, "Valor, fill the dossiers.")
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
        assert line in " ".join(call.system("acme").split())
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
    told = " ".join(call.system("acme").split())
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
    assert "let them decide each one" not in told and "let them choose" not in told
    assert (
        "With only one person here, that read-back ends by opening the next open topic in the meeting now, "
        "explained, asking a yes only when it needs one; with none open there, ask what they want to take next"
        in told
    )


def test_a_stakeholder_is_asked_only_what_is_theirs_once_and_after_it_is_explained():
    """P-040 (Tom, 2026-11-25): a call asked for a yes before explaining
    anything, asked about steps that only carried out an approved plan, and
    asked a decided item again; a task's instruction had widened a rule
    about new names to every migration. The test for status or question is
    in "How product decisions are made", which every turn and the call
    read; explain-first and ask-once are in "In a call"."""
    process = " ".join(persona.section(settings.persona_dir, "How product decisions are made").split())
    flat = " ".join(persona.section(settings.persona_dir, "In a call").split())
    told = " ".join(call.system("acme").split())
    test = [
        (
            "Work that only carries out what was already decided (a stated requirement, a name or plan already "
            "approved) is status: say it is following that decision, and ask nothing."
        ),
        "A question is only a departure from it, a real choice, or a risk",
        "A project rule is carried at the reach it was given, never widened",
    ]
    for line in test:
        assert line in process and line in told
    for line in [
        (
            "Never ask for a yes on something you have not yet explained in their terms; when the detail is not in "
            "yet, say what the item is and ask nothing."
        ),
        "What only carries out a decision is said as following it, with no ask",
        "Ask once for each item that needs their answer; a no changes course and is never asked again.",
        "an explanation may run longer when it catches someone up on an item they have not seen",
        "Opening it means explaining it; ask a yes only when that item needs one.",
    ]:
        assert line in flat and line in told
    assert (
        "say which only carry out what was already decided (status, no ask), and ask once for each that needs their answer, after explaining it"
        in told
    )
    for prompt in (call.READ_UP, call.UPDATE):
        flat_prompt = " ".join(prompt.split())
        assert (
            "whether it only carries out what was already decided (status, no question) or needs their answer"
            in flat_prompt
        )
        assert "what it is and what it changes, in the person's terms" in flat_prompt
    assert "Name each decision once, under the label the call uses." in call.UPDATE
    assert "Decided in this call (each stays decided, under any name):\n{decided}" in call.UPDATE
    converse = " ".join((Path(__file__).parents[1] / "skills/sdlc/converse.md").read_text().split())
    assert "never as a bare yes or no with the detail still to come" in converse
    assert (
        "listed as status, following that decision, never as a question (**Status or question**)" in converse
    )
    assert "a project rule goes into its instruction at the reach it was given, never widened" in converse
    assert "The meeting now is the call's one record of what is open and decided" in told
    assert call.STATE.startswith("The meeting now")
    from core import __main__ as kernel

    source = " ".join(inspect.getsource(kernel._call).replace('"\n', "").replace('"', "").split())
    assert (
        "When the call so far already answered it, give what that answer lacked (an example, another"
        in source
    )
    assert "never the same answer" in source and "preparer=turn" in source
    worker = " ".join((Path(__file__).parent.parent / "skills/sdlc/call.md").read_text().split())
    assert (
        "A turn that asks you to update the state of the call is not a question" in worker
        and "never posted" in worker
    )


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
        assert "next two open topics" in prompt and "The call so far:" in prompt
        return "Dossiers: 14 people recorded, 3 internal (ledger, 2026-11-16)."

    async def go():
        c = await in_call(dsn, team, reader=reader, preparer=preparer)
        assert await until(lambda: "14 people" in c.the.topics)
        await settle(c.the)
        await finish(c)
        return c

    c = run(go())
    assert order == ["read", "prep"]
    assert "14 people recorded" in c.the.state() and c.the.state().startswith("The meeting now (replaces")
    assert not [p for p in c.page.posts if "14 people" in p]


def test_the_state_updates_on_the_queue_moving_and_a_settle_overtakes_one_under_way(dsn, team, quick):
    """Updates come on events (an invited call's start, `settle`, `backlog`,
    `open_decisions`, a turn Valor spoke), in a group call too, never
    because others talked on; events during one are one more. "no change"
    changes nothing. A decision is in the state the moment it is settled,
    before any update; an update under way when Tom settles is dropped, and
    the next lists each decision of the call, whatever has scrolled out of
    its window (2026-11-25: notes written before Tom's no relisted the item,
    and it was asked again)."""
    gate, order, prompts = asyncio.Event(), [], []
    replies = iter(["no change", "Item two: 0.4 percent of imports fail.", "Item three: blocked on QA."])

    async def preparer(prompt):
        order.append("prep")
        prompts.append(prompt)
        if len(order) == 2:
            await gate.wait()
        return next(replies)

    async def go():
        starts = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        c = await in_call(dsn, team, preparer=preparer, starts=starts)
        assert await until(lambda: order == ["prep"])
        await settle(c.the)
        for i in range(5):  # a group call, talking on
            await c.the._heard("d1", "Tom", f"talking on {i}", "spoke")
        await asyncio.sleep(0.3)
        talked = list(order)
        c.gemini.minds += [[("settle", {"ids": ["t1/q1"], "readback": "Ship Monday."})], []]
        await says(c, TOMS, "Valor, we ship Monday.")
        assert await until(lambda: len(order) == 2)  # the second update, held at the gate
        assert "- Ship Monday." in c.the.state()  # in the state the moment it is settled
        c.gemini.minds += [
            [("open_decisions", {}), ("settle", {"ids": ["t2/q2"], "readback": "Drop IE."})],
            [],
        ]
        await says(c, TOMS, "Valor, and we drop IE.")
        assert await until(lambda: len(c.the.decided) == 2)
        gate.set()
        assert await until(lambda: len(order) == 3 and "QA" in c.the.topics)
        await settle(c.the)
        await finish(c)
        return c, talked

    c, talked = run(go())
    assert talked == ["prep"]  # talk alone starts no update, and "no change" was noted as nothing
    assert order == ["prep", "prep", "prep"]
    state = c.the.state()
    assert "0.4 percent" not in state  # the update a settle overtook is never taken
    assert "blocked on QA" in state and "- Ship Monday." in state and "- Drop IE." in state
    assert "Decided in this call (each stays decided, under any name):\n(nothing yet)" in prompts[0]
    assert "- Ship Monday.\n" in prompts[1] and "Drop IE." not in prompts[1]
    assert "- Ship Monday.\n- Drop IE.\n" in prompts[2]
    assert not [p for p in c.page.posts if "0.4 percent" in p or "no change" in p]


def test_an_update_asked_while_a_stopped_one_winds_down_is_one_update_and_the_call_runs_on(dsn, team, quick):
    """Review, 2026-11-25: a worker turn takes a while to stop (its process
    killed and reaped). A `prepare()` while it does, after a question
    stopped it, queued a second update beside the first, and `work` spun on
    the two forever, freezing the call. It is one update, run after the
    answer, and the call goes on."""
    order, first = [], asyncio.Event()

    async def preparer(prompt):
        order.append("prep")
        if len(order) == 1:
            first.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0.2)  # a slow stop
                order.append("stopped")
                raise
        return f"Topics {len(order)}."

    async def worker(question):
        order.append(question)
        return "Tuesday."

    async def go():
        starts = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        c = await in_call(dsn, team, worker=worker, preparer=preparer, starts=starts)
        assert await until(lambda: first.is_set())
        c.gemini.minds += [[("ask", {"question": "When did the import ship?"})], []]
        await says(c, LOGAN, "Valor, when did the import ship?")
        await asyncio.sleep(0.05)
        c.the.prepare()  # Valor's "I'll check" turn ends while the update stops
        assert await until(lambda: "Topics" in c.the.topics)
        await asyncio.sleep(0.3)
        queued = [q for q in list(c.the.questions._queue) if q is call.PREP]
        await settle(c.the)
        await finish(c)
        return queued

    queued = run(go())
    assert order == ["prep", "stopped", "When did the import ship?", "prep"] and queued == []


def test_a_decision_before_any_topics_keeps_the_notes_before_the_call_in_the_state(dsn, team, quick):
    """Review, 2026-11-25: a settle before the first update rendered a state
    with no topics, which replaces the notes before the call, so the voice
    lost them. Until there are topics, the state carries those notes."""

    async def go():
        c = await in_call(dsn, team)
        c.gemini.minds += [[("settle", {"readback": "Ship Monday."})], ["Monday."]]
        await says(c, TOMS, "Valor, ship Monday.")
        await settle(c.the)
        await finish(c)
        return c

    c = run(go())
    text = c.the.state()
    assert "- Ship Monday." in text and c.the.info["prep"] in text and "(nothing yet)" not in text


def test_a_question_stops_an_update_under_way_and_the_update_runs_after_its_answer(dsn, team, quick):
    """A lookup never waits on an update (P-041): asked while one runs, the
    update is stopped, the question answered, posted and noted, and the
    update asked again after it."""
    order, first = [], asyncio.Event()

    async def preparer(prompt):
        order.append("prep")
        if len(order) == 1:
            first.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                order.append("stopped")
                raise
        return "Topics after the answer."

    async def worker(question):
        order.append(question)
        return "Tuesday."

    async def go():
        starts = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        c = await in_call(dsn, team, worker=worker, preparer=preparer, starts=starts)
        assert await until(lambda: first.is_set())
        c.gemini.minds += [[("ask", {"question": "When did the import ship?"})], []]
        await says(c, LOGAN, "Valor, when did the import ship?")
        assert await until(lambda: "after the answer" in c.the.topics)
        await settle(c.the)
        await finish(c)
        return c

    c = run(go())
    assert order == ["prep", "stopped", "When did the import ship?", "prep"]
    assert any("Tuesday." in p for p in c.page.posts)
    assert c.the.say_next == [("When did the import ship?", "Tuesday.")]  # asked aloud: for the next reply


# -- the mind's rounds, hedged and asked again -----------------------------------------


def test_the_first_copy_to_answer_is_taken_a_failed_one_left_and_all_failing_raise():
    async def copy(delay, items, fail=None):
        await asyncio.sleep(delay)
        if fail:
            raise fail
        for item in items:
            yield item

    async def read(*starts):
        return [x async for x in call.first_of(list(starts))]

    stopped = []

    async def slow():
        try:
            await asyncio.sleep(5)
            yield "late"
        finally:
            stopped.append(True)

    assert run(read(lambda: copy(0.05, ["a", "b"]), slow)) == ["a", "b"] and stopped == [True]
    assert run(read(lambda: copy(0, [], RuntimeError("503")), lambda: copy(0.05, ["ok"]))) == ["ok"]
    with pytest.raises(RuntimeError, match="second"):
        run(read(lambda: copy(0, [], RuntimeError("first")), lambda: copy(0.02, [], RuntimeError("second"))))


def _chunk(text):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(parts=[types.Part(text=text)]))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=100, candidates_token_count=5
        ),
    )


def test_a_round_google_refuses_is_asked_again_and_each_copy_is_charged_its_input(monkeypatch):
    """Four turns of one live run ended in silence on "503 high demand"
    (2026-11-25): the round is asked again and answered."""
    from google.genai import errors

    monkeypatch.setattr(call, "RETRY_S", (0.01, 0.01))
    asked = []

    class Models:
        async def generate_content_stream(self, model, contents, config):
            asked.append(model)
            if len(asked) <= call.HEDGE:  # every copy of the first ask
                raise errors.ServerError(503, {"error": {"message": "high demand", "status": "UNAVAILABLE"}})

            async def chunks():
                yield _chunk("Monday.")

            return chunks()

    the = call.Gemini.__new__(call.Gemini)
    the.client = SimpleNamespace(aio=SimpleNamespace(models=Models()))

    async def go():
        return [
            x
            async for x in the.think(
                "instruction", [{"role": "user", "parts": [{"text": "When?"}]}], call.TOOLS
            )
        ]

    got = run(go())
    assert [x["text"] for x in got if "text" in x] == ["Monday."] and len(asked) == 2 * call.HEDGE
    usages = [x["usage"] for x in got if "usage" in x]
    assert len(usages) == call.HEDGE and usages[0]["candidates_token_count"] == 5
    assert all(u["prompt_token_count"] == 100 and "candidates_token_count" not in u for u in usages[1:])
