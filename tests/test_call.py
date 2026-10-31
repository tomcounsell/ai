"""Valor in a Meet call, its own process (`core/call.py`), driven without
Meet: a page that records what it was asked to play and post, ears that
hear loud frames as speech, and a scripted `meet.speak`, front model and
worker. What is said aloud is a `meet.say` and what is written a
`meet.send_message`, each one effect per request id; a written answer is
copied to the thread the call was asked in once its post is done; speech
never holds up a post; a newer turn of the person answered makes the
rest of the answer stale; that person talking over Valor stops him, others
do not; the worker's answers are posted, or after leaving go in the
report; a stop leaves at once and "Valor, leave" from Tom leaves too."""

import asyncio
import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from aiohttp import web

from core import call, db, ledger, tasks
from tests import bridges, test_meet
from tests.test_kernel import at
from tests.test_meet import CODE, TOM, a_call

pytestmark = pytest.mark.spend(usd=0)
team = test_meet.team  # the project and its signed-in call profile, a fixture
run = asyncio.run
LOGAN, TOMS = "devices/2", "devices/1"


class Page:
    """Meet's page as the call drives it. With `hold`, a play waits until
    `stop` (cut at half) or `finish`."""

    def __init__(self, *, hold=False):
        self.hold, self.status = hold, "in"
        self.people = [{"id": "devices/9", "name": "Valor Engels", "me": True},
                       {"id": LOGAN, "name": "Logan", "me": False}, {"id": TOMS, "name": TOM, "me": False}]  # fmt: skip
        self.posts: list[str] = []
        self.plays: list[float] = []
        self.stops = 0
        self.post_ok = True
        self.playing: asyncio.Future | None = None
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

    async def play(self, pcm, rate):
        total = len(pcm) / rate
        self.plays.append(total)
        if not self.hold:
            return {"played": total, "total": total, "cut": False}
        self.playing = asyncio.get_running_loop().create_future()
        cut = await self.playing
        return {"played": total / 2 if cut else total, "total": total, "cut": cut}

    async def stop(self):
        self.stops += 1
        if self.playing is not None and not self.playing.done():
            self.playing.set_result(True)

    def finish(self):
        self.playing.set_result(False)

    async def leave(self):
        self.left = True

    async def close(self):
        pass


class Ears(call.Ears):
    """A loud frame is speech; every pause ends the turn; the words are
    the next of `said`."""

    def __init__(self, said=()):
        self.said = list(said)

    def voiced(self, speaker, frame):
        return 1.0 if float(np.abs(frame).mean()) > 0.05 else 0.0

    def ended(self, audio):
        return 1.0

    async def transcribe(self, audio):
        return self.said.pop(0) if self.said else ""


class Front:
    """The front model: each draft says `answers[i]`, sentence by sentence,
    after calling each tool `tools[i]` names."""

    def __init__(self, answers, tools=None, *, gate=None):
        self.answers, self.tools, self.gate, self.drafts = list(answers), list(tools or []), gate, []

    async def start(self):
        pass

    async def close(self):
        pass

    async def draft(self, news, said, actions):
        self.drafts.append((news, said))
        actions += self.tools.pop(0) if self.tools else []
        for sentence in self.answers.pop(0) if self.answers else []:
            if self.gate is not None:
                await self.gate.wait()
            yield sentence


def judged(*labels):
    """`meet.speak` picking each label in turn; None is an abstain."""
    labels = list(labels)
    asked = []

    async def judge(inputs, turn):
        asked.append(inputs)
        label = labels.pop(0) if labels else None
        return SimpleNamespace(
            answered=label is not None, payload={"picked": {"turn": label}} if label else {}
        )

    judge.asked = asked
    return judge


def a_call_of(dsn, team, page, front, judge, *, worker=None, said=(), ready=True):
    async def ready_now():
        return ready

    async def no_worker(question):
        raise AssertionError("no worker in this test")

    async def make():
        brief, session_id = await a_call(dsn, team)
        async with await db.connect(dsn) as conn:
            info = await call.marker(conn, session_id)
        the = call.Call(session_id, info, page, Ears(said), dsn=dsn,
                        voice=lambda text: (np.zeros(240 * len(text.split()), np.float32), 24000),
                        judge=judge, front=front, worker=worker or no_worker, worker_ready=ready_now,
                        project=team.project)  # fmt: skip
        the.names.update({LOGAN: "Logan", TOMS: TOM})
        return brief, the

    return make()


def lanes(the):
    return [asyncio.create_task(c) for c in (the.says_lane(), the.posts_lane(), the.work())]


async def settle(the, *jobs, rounds=200):
    """Until every turn and lane is idle."""
    for _ in range(rounds):
        await asyncio.sleep(0.01)
        if not the.jobs and the.says.empty() and the.posts.empty() and not the.asking:
            await asyncio.sleep(0.05)
            if not the.jobs and the.says.empty() and the.posts.empty() and not the.asking:
                return
    raise AssertionError("the call never settled")


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


def turn(who, text, via="spoke"):
    return call.Turn(ledger.new_id(), who, {LOGAN: "Logan", TOMS: TOM}[who], text, via)


def test_a_spoken_question_is_answered_aloud_a_sentence_at_a_time_and_heard_in_the_transcript(dsn, team):
    async def go():
        page = Page()
        _, the = await a_call_of(dsn, team, page, Front([["We ship Monday.", "The import is done."]]),
                                     judged("asked_valor"))  # fmt: skip
        running = lanes(the)
        await the._turn(turn(LOGAN, "Valor, when do we ship?"))
        await settle(the)
        for job in running:
            job.cancel()
        return the, page, await effects(dsn, the.id), await bridges.rows(dsn, the.id)

    the, page, done, rows = run(go())
    assert [e["type"] for e in done] == ["meet.say", "meet.say"] and len(page.plays) == 2
    assert [e["result"]["spoken_text"] for e in done] == ["We ship Monday.", "The import is done."]
    assert [e["request_id"].split("/")[2:] for e in done] == [["0", "say"], ["1", "say"]]
    assert page.posts == []  # speech is not copied
    (heard,) = [r["payload"] for r in rows if r["type"] == "call.heard"]
    assert heard["name"] == "Logan" and heard["text"] == "Valor, when do we ship?" and heard["via"] == "spoke"
    assert the.lines[-1] == "Valor (spoke): The import is done."


def test_a_typed_question_is_answered_in_the_chat_and_copied_to_the_thread_after_its_post(dsn, team):
    """The copy is a Slack send to the chat that asked, in its thread, its
    request after the post's; the post's outcome is done first."""

    async def go():
        page = Page()
        _, the = await a_call_of(dsn, team, page, Front([["It is in the ledger.", "Row 12."]]),
                                     judged("asked_valor"))  # fmt: skip
        running = lanes(the)
        the.chat(LOGAN, "Valor, where is the invoice?")
        await settle(the)
        for job in running:
            job.cancel()
        return the, page, await effects(dsn, the.id)

    _the, page, done = run(go())
    assert page.posts == ["It is in the ledger. Row 12."]
    post, copy = done
    assert post["type"] == "meet.send_message" and post["kind"] == "done"
    assert copy["type"] == "slack.send_message" and copy["kind"] == "released" and copy["target"] == team.chat
    assert copy["payload"] == {"text": "It is in the ledger. Row 12.", "reply_to": at(1)}
    assert (
        copy["request_id"].endswith("/copy")
        and copy["request_id"].split("/")[1] == post["request_id"].split("/")[1]
    )


def test_a_failed_post_gets_no_copy_and_a_copy_never_holds_up_the_next_post(dsn, team):
    async def go():
        page = Page()
        page.post_ok = False
        _, the = await a_call_of(dsn, team, page, Front([["First."], ["Second."], ["Third."]]),
                                     judged("asked_valor", "asked_valor", "asked_valor"))  # fmt: skip
        running = lanes(the)
        await the._turn(turn(LOGAN, "Valor, one?", "typed"))
        await settle(the)
        page.post_ok = True
        await the._turn(turn(LOGAN, "Valor, two?", "typed"))
        await the._turn(turn(TOMS, "Valor, three?", "typed"))
        await settle(the)
        for job in running:
            job.cancel()
        return await effects(dsn, the.id)

    done = run(go())
    kinds = [(e["type"], e["kind"]) for e in done]
    assert kinds[0] == ("meet.send_message", "failed") and kinds[1][0] == "meet.send_message"
    # The second's copy is still with Slack's bridge when the third posts.
    assert [k for k in kinds if k[0] == "slack.send_message"] == [("slack.send_message", "released")] * 2
    assert sum(1 for k in kinds if k == ("meet.send_message", "done")) == 2


def test_a_post_lands_while_a_long_say_plays_and_one_request_id_is_one_effect(dsn, team):
    async def go():
        page = Page(hold=True)
        _, the = await a_call_of(dsn, team, page, Front([["A long answer."], ["Noted."]]),
                                     judged("asked_valor", "useful_post"))  # fmt: skip
        running = lanes(the)
        await the._turn(turn(LOGAN, "Valor, explain the import?"))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if page.playing is not None:
                break
        await the._turn(turn(TOMS, "The figure is in the deck.", "typed"))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if page.posts:
                break
        posted_while_playing = list(page.posts) and not page.playing.done()
        page.finish()
        await settle(the)
        (post,) = [e for e in await effects(dsn, the.id) if e["type"] == "meet.send_message"]
        again = await the._effect("meet.send_message", {"text": "Noted."}, post["request_id"])
        for job in running:
            job.cancel()
        return posted_while_playing, again, await effects(dsn, the.id)

    posted_while_playing, again, done = run(go())
    assert posted_while_playing
    assert again.kind == "done" and sum(1 for e in done if e["type"] == "meet.send_message") == 1


def test_the_person_answered_talking_over_valor_stops_him_and_side_talk_does_not(dsn, team):
    """Barge-in by the addressed speaker cuts the say, which is done with
    what was spoken; the rest of the answer is stale and never played."""
    loud = np.full(9600, 0.5, np.float32)

    async def go():
        page = Page(hold=True)
        _, the = await a_call_of(dsn, team, page, Front([["One two three four.", "Five six."]]),
                                     judged("asked_valor"))  # fmt: skip
        running = lanes(the)
        await the._turn(turn(LOGAN, "Valor, count for me?"))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if page.playing is not None:
                break
        the.hear(TOMS, loud, 48000)  # side talk
        await asyncio.sleep(0.05)
        side = page.stops
        the.hear(LOGAN, loud, 48000)  # the person answered
        await settle(the)
        for job in running:
            job.cancel()
        return side, page, await effects(dsn, the.id)

    side, page, done = run(go())
    assert side == 0 and page.stops == 1 and len(page.plays) == 1
    (say,) = [e for e in done if e["type"] == "meet.say" and e["kind"] == "done"]
    assert say["result"] == {**say["result"], "cut": True, "spoken_text": "One two"}
    assert not [e for e in done if e["payload"]["text"] == "Five six." and e["kind"] == "done"]


def test_a_newer_turn_of_the_person_answered_drops_the_rest_and_silence_says_nothing(dsn, team):
    """The answer to a turn its speaker went on from is never played; a turn
    `meet.speak` abstains on, or calls someone else's, gets nothing, and
    its draft's tools do nothing."""
    gate = asyncio.Event

    async def go():
        page = Page()
        the_gate = gate()
        front = Front([["Stale one.", "Stale two."], ["Asked about the worker."]],
                      tools=[[], [("ask_worker", {"question": "What broke?"})]], gate=the_gate)  # fmt: skip
        _, the = await a_call_of(dsn, team, page, front, judged("asked_valor", None, "someone_else"))
        running = lanes(the)
        first = asyncio.create_task(the._turn(turn(LOGAN, "Valor, what about the build?")))
        await asyncio.sleep(0.1)
        await the._turn(turn(LOGAN, "Actually never mind."))
        the_gate.set()
        await first
        await the._turn(turn(TOMS, "Logan, what broke?"))
        await settle(the)
        for job in running:
            job.cancel()
        return the, page, await effects(dsn, the.id)

    the, page, done = run(go())
    assert page.plays == [] and [e for e in done if e["kind"] == "done"] == []
    assert the.questions.empty() and the.asking == []


def test_valors_own_audio_and_chat_are_not_heard(dsn, team):
    async def go():
        page = Page()
        judge = judged()
        _, the = await a_call_of(dsn, team, page, Front([]), judge)
        the.me = "devices/9"
        the.hear("devices/9", np.full(4800, 0.5, np.float32), 48000)
        the.chat("devices/9", "Valor here, keeping a transcript for notes.")
        await asyncio.sleep(0.1)
        return the, judge

    the, judge = run(go())
    assert the.speakers == {} and judge.asked == []


def test_each_persons_turn_is_heard_apart_though_their_audio_shares_the_frames(dsn, team):
    """Three people talking in turn over interleaved frames: three turns,
    each transcribed once its pause ends it."""
    loud = np.full(480, 0.5, np.float32)
    quiet = np.zeros(480, np.float32)

    async def go():
        page = Page()
        clock = [0.0]
        _, the = await a_call_of(dsn, team, page, Front([]), judged(), said=["one", "two", "three"])
        the.clock = lambda: clock[0]
        for who in (LOGAN, TOMS, "devices/3"):
            for _ in range(120):  # past `SHORT_S`, so Smart Turn ends it
                clock[0] += 0.01
                the.hear(who, loud, 48000)
                the.hear("devices/4", quiet, 48000)
            clock[0] += call.PAUSE_S + 0.01
            for speaker in list(the.speakers.values()):
                audio = speaker.pause(the.ears, clock[0])
                if audio is not None:
                    await the._spoke(speaker.id, audio)
        rows = await bridges.rows(dsn, the.id)
        return [(r["payload"]["who"], r["payload"]["text"]) for r in rows if r["type"] == "call.heard"]

    assert run(go()) == [(LOGAN, "one"), (TOMS, "two"), ("devices/3", "three")]


def test_down_to_16k_keeps_a_tone_across_frames():
    down = call.Down()
    t = np.arange(48000) / 48000
    tone = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    out = np.concatenate([down(tone[i : i + 480], 48000) for i in range(0, 48000, 480)])
    assert abs(len(out) - 16000) <= 1
    want = np.sin(2 * np.pi * 440 * (np.arange(len(out)) * 3 - 24) / 48000)
    assert np.abs(out[100:] - want[100:]).max() < 0.05


def test_the_workers_answer_is_posted_naming_its_question_and_copied(dsn, team):
    async def go():
        page = Page()
        front = Front([["Let me check."]], tools=[[("ask_worker", {"question": "What did we ship?"})]])

        async def worker(question):
            return "The import, on Tuesday."

        _, the = await a_call_of(dsn, team, page, front, judged("asked_valor"), worker=worker)
        running = lanes(the)
        await the._turn(turn(TOMS, "Valor, what did we ship?"))
        await settle(the)
        for job in running:
            job.cancel()
        return page, await effects(dsn, the.id)

    page, done = run(go())
    assert page.posts == ['On "What did we ship?": The import, on Tuesday.']
    kinds = [e["type"] for e in done]  # the answer may come before or after Valor's "let me check"
    assert sorted(kinds) == ["meet.say", "meet.send_message", "slack.send_message"]
    assert kinds.index("slack.send_message") > kinds.index("meet.send_message")


def test_a_readback_posts_with_its_settles_and_is_copied(dsn, team):
    settles = [{"task_id": "t1", "question_id": "q1"}, {"task_id": "t2", "question_id": "q2"}]

    async def go():
        page = Page()
        front = Front([["Got it."]], tools=[[("record_decision", {"readback": "Ship Monday; drop IE.",
                                                                    "settles": settles})]])  # fmt: skip
        _, the = await a_call_of(dsn, team, page, front, judged("asked_valor"))
        running = lanes(the)
        await the._turn(turn(TOMS, "Ship Monday and drop IE."))
        await settle(the)
        for job in running:
            job.cancel()
        return await effects(dsn, the.id)

    done = run(go())
    (post,) = [e for e in done if e["type"] == "meet.send_message"]
    assert post["payload"] == {"text": "Ship Monday; drop IE.", "settles": settles} and post["kind"] == "done"
    assert any(
        e["type"] == "slack.send_message" and e["payload"]["text"] == "Ship Monday; drop IE." for e in done
    )


FAKE_CLAUDE = r"""
import json, sys
replies = json.loads(sys.argv[1])
for line in sys.stdin:
    asked = json.loads(line)["message"]["content"]
    sys.stderr.write(asked + "\n---\n")
    reply = replies.pop(0)
    for chunk in reply:
        print(json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
                          "delta": {"type": "text_delta", "text": chunk}}}), flush=True)
    print(json.dumps({"type": "result", "result": "".join(reply)}), flush=True)
"""


def test_the_front_model_streams_sentences_keeps_directives_unsaid_and_a_dropped_draft_never_skews_the_next(
    tmp_path,
):
    """A Claude Code session reading and writing stream-json (here a fake
    one): each sentence as soon as it is whole, an `ASK:` or `DECIDED:`
    line an action and never said, and a draft abandoned midway read to its
    end before the next is asked, so the next answer is its own."""
    replies = [
        ["Let me", " check that. Two", " are open.\nASK: What shipped", " this week?\n", "DECIDED: t1/q1, t2/q2 | Ship Monday."],
        ["This one", " is dropped.", " Still dropped."],
        ["Second answer."],
    ]  # fmt: skip
    errors = tmp_path / "asked.txt"

    async def go():
        front = call.Front(["sh", "-c", f'{sys.executable} -c "$0" "$1" 2>{errors}', FAKE_CLAUDE, json.dumps(replies)],
                           {}, str(tmp_path))  # fmt: skip
        await front.start()
        actions = []
        got = [s async for s in front.draft("news one", "Tom said: what is open?", actions)]
        dropped = front.draft("", "Logan said: hm", [])
        first = await dropped.__anext__()
        await dropped.aclose()
        second = [s async for s in front.draft("", "Tom said: and then?", [])]
        await front.close()
        return got, actions, first, second

    got, actions, first, second = run(go())
    assert got == ["Let me check that.", "Two are open."]
    assert actions == [("ask_worker", {"question": "What shipped this week?"}),
                       ("record_decision", {"readback": "Ship Monday.", "settles": [
                           {"task_id": "t1", "question_id": "q1"}, {"task_id": "t2", "question_id": "q2"}]})]  # fmt: skip
    assert first == "This one is dropped." and second == ["Second answer."]
    assert errors.read_text().startswith("news one\n\nNow: Tom said: what is open?")


def test_the_front_models_instructions_carry_the_notes_before_the_call():
    front = call.Front.claude("http://127.0.0.1:1/t/x", model="claude-haiku-5-5", project="acme",
                              prep="The launch: two decisions open.", home="/tmp/front")  # fmt: skip
    system = front.argv[front.argv.index("--system-prompt") + 1]
    assert "The launch: two decisions open." in system and "acme" in system
    assert front.argv[front.argv.index("--tools") + 1] == "" and "--include-partial-messages" in front.argv
    assert front.env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:1/t/x"


# -- the call's life -----------------------------------------------------------


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(call, "STATE_S", 0.05)
    monkeypatch.setattr(call, "TICK_S", 0.02)


async def wait_for(check, s=5.0):
    for _ in range(int(s / 0.02)):
        if await check():
            return True
        await asyncio.sleep(0.02)
    return False


def test_a_stop_leaves_within_seconds_drops_the_workers_question_and_ends_the_call_once(dsn, team, quick):
    async def go():
        page = Page()
        hold = asyncio.Event()

        async def worker(question):
            await hold.wait()
            raise tasks.TaskStopped(question)

        _, the = await a_call_of(dsn, team, page, Front([]), judged(), worker=worker)
        running = asyncio.create_task(the.run())
        assert await wait_for(lambda: _async(page.posts == [call.JOIN_LINE]))
        the.questions.put_nowait("What broke?")
        await asyncio.sleep(0.1)
        start = asyncio.get_running_loop().time()
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, the.id, reason="enough", by="tom", via="chat")
        hold.set()
        reason = await asyncio.wait_for(running, 10)
        took = asyncio.get_running_loop().time() - start
        return the, page, reason, took, await bridges.rows(dsn, the.id)

    _the, page, reason, took, rows = run(go())
    assert reason == "the call was stopped" and took < 5 and page.left
    (ended,) = [r["payload"] for r in rows if r["type"] == "call.ended"]
    assert ended["stopped"] and ended["unanswered"] == ["What broke?"]


async def _async(value):
    return value


def test_valor_leave_from_tom_leaves_and_from_anyone_else_does_not(dsn, team, quick):
    async def go():
        page = Page()
        _, the = await a_call_of(dsn, team, page, Front([]), judged())
        running = asyncio.create_task(the.run())
        assert await wait_for(lambda: _async(bool(page.posts)))
        await the._turn(turn(LOGAN, "Valor, leave."))
        await asyncio.sleep(0.1)
        before = the.leaving.is_set()
        await the._turn(turn(TOMS, "Thanks Valor, you can leave now."))
        reason = await asyncio.wait_for(running, 10)
        return before, reason, await bridges.rows(dsn, the.id)

    before, reason, rows = run(go())
    assert before is False and reason == "Tom asked Valor to leave"
    kinds = [r["type"] for r in rows]
    assert kinds.count("call.ended") == 1 and "task.ended" in kinds


def test_a_late_worker_answer_goes_in_the_report_once(dsn, team, quick):
    async def go():
        page = Page()
        gate = asyncio.Event()

        async def worker(question):
            await gate.wait()
            return "The import."

        brief, the = await a_call_of(dsn, team, page, Front([]), judged(), worker=worker)
        running = asyncio.create_task(the.run())
        assert await wait_for(lambda: _async(bool(page.posts)))
        the.questions.put_nowait("What shipped?")
        await asyncio.sleep(0.1)
        page.status = "ended"
        await asyncio.sleep(0.3)
        gate.set()
        await asyncio.wait_for(running, 10)
        rows = await bridges.rows(dsn, brief.id)
        return page, rows

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
        _, the = await a_call_of(dsn, team, page, Front([]), judged())
        return await asyncio.wait_for(the.run(), 10), page

    reason, page = run(go())
    assert reason == "Valor was not let in" and page.posts == []


def test_the_voice_process_hears_a_calls_turn_on_its_own_worker():
    from tools import voice

    async def go():
        app = voice.app(listen=lambda path: "a voice note", heard=lambda samples: f"{len(samples)} samples")
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        import aiohttp

        async with (
            aiohttp.ClientSession() as http,
            http.post(f"http://127.0.0.1:{port}/hear", data=np.zeros(16000, np.float32).tobytes()) as r,
        ):
            got = await r.json()
        await runner.cleanup()
        return got

    assert run(go()) == {"text": "16000 samples"}


def test_a_steer_of_the_call_is_posted_in_its_chat_and_not_copied(dsn, team, quick):
    async def go():
        page = Page()
        _, the = await a_call_of(dsn, team, page, Front([]), judged())
        running = asyncio.create_task(the.run())
        assert await wait_for(lambda: _async(page.posts == [call.JOIN_LINE]))
        async with await db.connect(dsn) as conn:
            await ledger.append(
                conn, the.id, "message.steered", {"text": "Logan is running five minutes late."}
            )
        assert await wait_for(lambda: _async(len(page.posts) == 2))
        the.leave("done")
        await asyncio.wait_for(running, 10)
        return page, await effects(dsn, the.id)

    page, done = run(go())
    assert page.posts[1] == "Logan is running five minutes late."
    assert not any(e["type"] == "slack.send_message" for e in done)


def test_a_stop_before_valor_is_let_in_leaves_within_seconds(dsn, team, quick):
    async def go():
        page = Page()
        page.status = "lobby"
        _, the = await a_call_of(dsn, team, page, Front([]), judged())
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
        _, the = await a_call_of(dsn, team, page, Front([]), judged())
        running = asyncio.create_task(the.run())
        assert await wait_for(lambda: _async(bool(page.posts)))
        page.status = status
        reason = await asyncio.wait_for(running, 10)
        return reason, await bridges.rows(dsn, the.id)

    reason, rows = run(go())
    assert reason == why and [r["type"] for r in rows].count("call.ended") == 1


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


@pytest.mark.parametrize("hear", [True, False])
def test_a_turn_is_heard_by_the_voice_process_old_or_new(tmp_path, hear):
    """`/hear` when the voice process has it; from one started before it,
    `/transcribe` on the turn as a WAV file, removed after."""
    import wave

    from tools import voice

    seen = []

    def listen(path):
        with wave.open(path) as w:
            seen.append((w.getframerate(), w.getnframes()))
        return "from the file"

    async def go():
        app = voice.app(listen=listen, heard=lambda samples: "heard")
        if not hear:
            app = web.Application()

            async def transcribe(request):
                return web.json_response({"text": listen((await request.json())["path"])})

            app.router.add_post("/transcribe", transcribe)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            with bridges.configure(voice_port=port):
                return await call.words(np.zeros(8000, np.float32), tmp_path)
        finally:
            await runner.cleanup()

    got = run(go())
    if hear:
        assert got == "heard" and seen == []
    else:
        assert got == "from the file" and seen == [(16000, 8000)] and list(tmp_path.iterdir()) == []
