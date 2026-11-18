"""The call's voice on Gemini Live, without Meet (P-038): a pause inside a
thought is waited out on Live's own end of turn; a fact the voice does not
hold is asked of the worker on the first turn; in a 1:1 a settled item's
reply opens the next item unprompted, from the worker's prepared note, while
a group call waits; and asked for more twice, each answer adds what the last
lacked. The session is the call's own (`call.live_config`, the notes the
call sends, its tool scheduling), fed a Kokoro voice in 40 ms chunks as the
call feeds Live.

Runs only when `VALOR_LIVE=1`, with Kokoro's files fetched and the Gemini key
copied (`python -m core google-key`)."""

import asyncio
import os
import re
import time

import numpy as np
import pytest

from core import call, credentials
from core.settings import settings

pytestmark = [
    pytest.mark.spend(usd=0.20),
    pytest.mark.skipif(os.environ.get("VALOR_LIVE") != "1", reason="a live Gemini session: VALOR_LIVE=1"),
]
ALONE = call.Call._here(["Tom Counsell"])
GROUP = call.Call._here(["Tom Counsell", "Ana Ruiz", "Ben Ito"])  # made-up names
# What the voice held in the 2026-11-16 call: the error's title, not what it means.
PREP = (
    "Next on the backlog: a runtime error in Sentry, raised when the system applies a difference while no "
    "difference is active. Medium value, small appetite."
)
# Two items, as a call's notes hold them; the worker's prep gives item one two levels down and item two a fact.
AGENDA = (
    "Tom and Valor review two items, in order: 1. shipping the import on Monday; 2. the onboarding emails, "
    "which are open: whether to send them from the product or from Tom's address."
)
PREPARED = call.PREPARED.format(
    notes=(
        "1. The import, Monday: QA passed 212 of 214 cases on 2026-11-16; the two failures are dates written "
        "day-first, fixed in the parser, retested 2026-11-17. Follow-up, a level down: day-first dates fail "
        "because the parser guessed month-first when both numbers are 12 or less; it now reads the account's "
        "locale. Follow-up, a level further: 31 accounts use a day-first locale, 4 of them with over 10,000 rows.\n"
        "2. Onboarding emails: from the product address, 38 percent opened in the September trial; from Tom's, "
        "61 percent. Recommendation: Tom's address for the first email only."
    )
)
ITEM_ONE = [
    {"role": "user", "parts": [{"text": "Tom Counsell: Let's do the import first. Where is it?"}]},
    {"role": "model", "parts": [{"text": "The import is ready to ship Monday: QA passed, and the two failures "
                                 "are fixed. I recommend we ship it Monday."}]},
]  # fmt: skip
DECISIONS = (
    "Open for Tom:\n- t1/q1: Ship the import on Monday?\n"
    "- t2/q2: Send the onboarding emails from the product address or from Tom's?"
)
CHUNK = call.RATE * 40 // 1000
WORDS = re.compile(r"[a-z0-9][a-z0-9'-]{3,}")
COMMON = {"that", "this", "with", "from", "they", "their", "there", "which", "would", "about", "have", "were",
          "what", "when", "your", "just", "into", "also", "been", "will", "then", "than", "them", "does"}  # fmt: skip


@pytest.fixture(scope="module")
def voice():
    from kokoro_onnx import Kokoro

    folder = os.path.expanduser(settings.tts_model_dir)
    kokoro = Kokoro(f"{folder}/kokoro-v1.0.onnx", f"{folder}/voices-v1.0.bin")

    def clip(text: str, who: str = "af_heart") -> np.ndarray:
        pcm, rate = kokoro.create(text, voice=who, lang="en-us")
        pcm = pcm[np.linspace(0, len(pcm) - 1, len(pcm) * call.RATE // rate).astype(int)]
        loud = np.flatnonzero(np.abs(pcm) > 0.01)
        return pcm[loud[0] : loud[-1] + 1].astype(np.float32)

    return clip


async def session(
    audio: np.ndarray,
    before: list[dict] = (),
    prep: str = "Tom and Valor review the backlog.",
    notes: list[str] = (),
    here: str = ALONE,
    listen_s: int = 8,
    during: list[tuple[float, str]] = (),
    send=None,
) -> dict:
    """Live's first voiced audio, in seconds on `audio`'s clock, what it
    said and the tools it called, with `before` sent as the conversation so
    far and `notes` as the call's notes (who is here, the worker's prep), and
    `during` as notes sent that many seconds into `audio`, as the call sends
    them while people talk (`send`, the call's own `note` by default). A
    non-blocking tool's reply is scheduled as `Call._tools` schedules it."""
    from google.genai import types

    key = credentials.read_key(settings.google_keyfile, call.GOOGLE_KEY, "google-key")
    out: dict = {"first": None, "tools": [], "said": ""}
    audio = np.concatenate(
        [np.zeros(call.RATE // 2, np.float32), audio, np.zeros(call.RATE * listen_s, np.float32)]
    )
    quiet = {t["name"] for t in call.TOOLS if t.get("behavior") == "NON_BLOCKING"}
    async with call.Gemini(key).connect(call.live_config("Valor", prep, None)) as s:
        for text in [here, *notes]:
            await s.send_client_content(**call.note(text))
        for turn in before:
            await s.send_client_content(turns=turn, turn_complete=False)
        t0 = time.monotonic()

        async def receive():
            while True:  # each `receive` ends with its turn, as `Call._receive` reads it
                async for m in s.receive():
                    for fc in m.tool_call.function_calls if m.tool_call else []:
                        out["tools"].append(fc.name)
                        result = {"ask": call.ASKED, "settle": "recorded", "post": "posted",
                                  "open_decisions": DECISIONS}.get(fc.name, "nothing recorded")  # fmt: skip
                        scheduling = None
                        if fc.name in quiet:
                            scheduling = (
                                types.FunctionResponseScheduling.SILENT
                                if out["first"] is not None and result in call.ACKNOWLEDGED
                                else types.FunctionResponseScheduling.WHEN_IDLE
                            )
                        await s.send_tool_response(
                            function_responses=[
                                types.FunctionResponse(
                                    id=fc.id, name=fc.name, response={"result": result}, scheduling=scheduling
                                )
                            ]
                        )
                    sc = m.server_content
                    if sc and sc.output_transcription and sc.output_transcription.text:
                        out["said"] += sc.output_transcription.text
                    for part in sc.model_turn.parts if sc and sc.model_turn else []:
                        data = part.inline_data.data if part.inline_data else b""
                        pcm = np.frombuffer(data, "<i2").astype(np.float32) / 32768
                        if out["first"] is None and len(pcm) and np.sqrt(np.mean(pcm**2)) >= call.SPEAKING:
                            out["first"] = time.monotonic() - t0 - 0.5

        task = asyncio.create_task(receive())
        due = sorted(during)
        for i in range(0, len(audio), CHUNK):
            while due and due[0][0] + 0.5 <= i / call.RATE:
                await (send or (lambda s, text: s.send_client_content(**call.note(text))))(s, due.pop(0)[1])
            await s.send_realtime_input(
                audio=types.Blob(
                    data=call.pcm16(audio[i : i + CHUNK]), mime_type=f"audio/pcm;rate={call.RATE}"
                )
            )
            await asyncio.sleep(max(0.0, t0 + (i + CHUNK) / call.RATE - time.monotonic()))
        task.cancel()
    print(f"\nsaid: {out['said']!r} tools: {out['tools']}")
    return out


def words(text: str) -> set[str]:
    return {w for w in WORDS.findall(text.lower()) if w not in COMMON}


def test_a_pause_of_about_a_second_inside_a_thought_is_not_answered(voice):
    """Tom's cut-offs (2026-11-13 and 2026-11-16): a sentence, a pause, and
    he went on with the same thought while Valor had started. On Live's
    own end of turn a pause of a second is waited out; one of 1.5 s is
    answered (3 of 3, 2026-11-17), and no setting that waits it out keeps
    replies under 2 s (docs/bridges/meet.md)."""
    first = voice("Yeah, we should have a dossier on each person.")
    then = voice("And which things in it are public, because there's a social side to what gets surfaced.")
    for _ in range(3):
        audio = np.concatenate([first, np.zeros(call.RATE, np.float32), then])
        seen = asyncio.run(session(audio))
        assert seen["first"] is None or seen["first"] > len(audio) / call.RATE, seen


def test_a_fact_the_voice_does_not_hold_goes_to_the_worker_on_the_first_turn(voice):
    """The 2026-11-16 call: asked what an error meant, Valor explained it
    five times in other words before asking the worker; the 2026-11-13 call:
    asked for the criteria behind an order, it restated the order. The
    persona's "Answer what was asked" now says it as if-then lines."""
    asked = voice("Tell me more about that error. Is it a real failure, or a no-op?")
    for _ in range(3):
        assert "ask" in asyncio.run(session(asked, prep=PREP))["tools"]


def test_asked_again_after_a_vague_answer_the_voice_asks_the_worker(voice):
    before = [
        {"role": "user", "parts": [{"text": "Tom: Tell me about the import error."}]},
        {"role": "model", "parts": [{"text": "It's an error in the logic that applies changes when the state "
                                     "isn't what the system expects."}]},
    ]  # fmt: skip
    again = voice("I'm so confused. What does that error actually mean?")
    for _ in range(3):
        assert "ask" in asyncio.run(session(again, before, PREP))["tools"]


@pytest.mark.parametrize("confirm", ["Yes, that's settled. Ship it Monday.", "Okay, sounds good."])
def test_in_a_one_to_one_a_settled_item_opens_the_next_from_the_prepared_note(voice, confirm):
    """Tom, 2026-11-17: in a 1:1 he had to ask "what's next?" once an item
    was settled (2026-11-16, #117966, #118033). Tom settles item one, by
    saying so or by agreeing; with nothing more from him, Valor's reply opens
    item two, the onboarding emails, using what only the worker's prepared
    note held (the open rates), so the note reached the voice before."""
    audio = voice(confirm)
    for _ in range(2):
        seen = asyncio.run(session(audio, ITEM_ONE, AGENDA, [PREPARED], listen_s=15))
        said = seen["said"].lower()
        assert "email" in said or "onboarding" in said, seen
        assert not re.search(r"(which|what) (item|priority|one)[^.?]* next", said), (
            seen
        )  # never hands the pick back


def test_in_a_group_call_a_settled_item_waits_to_be_asked(voice):
    """The same settled item with two others in the call: Valor does not
    steer, so item two is not opened (it was in 1 of 1 runs before the
    persona's group line, and 1 of 2 before the who-is-here note said so,
    2026-11-17)."""
    audio = voice("Yes, that's settled. Ship it Monday.")
    for _ in range(3):
        said = asyncio.run(session(audio, ITEM_ONE, AGENDA, [PREPARED], GROUP, 15))["said"].lower()
        assert "email" not in said and "onboarding" not in said, said


def test_asked_for_more_twice_each_answer_adds_what_the_last_lacked(voice):
    """Tom, 2026-11-17: "I ask for more details and you simply repeat
    yourself". Asked for more about item one, then for more again, each
    answer holds words the conversation before it lacked, and neither is
    the one before in other words."""
    more = voice("Tell me more about that.")
    first = asyncio.run(session(more, ITEM_ONE, AGENDA, [PREPARED]))
    assert first["said"].strip(), first
    before = [*ITEM_ONE, {"role": "user", "parts": [{"text": "Tom Counsell: Tell me more about that."}]},
              {"role": "model", "parts": [{"text": first["said"]}]}]  # fmt: skip
    second = asyncio.run(session(voice("Can you go deeper on that?"), before, AGENDA, [PREPARED]))
    assert second["said"].strip(), second
    opening = words(ITEM_ONE[1]["parts"][0]["text"])
    one, two = words(first["said"]), words(second["said"])
    assert len(one - opening) >= 3 and len(two - opening - one) >= 3, (one, two)
    assert len(two & one) / len(two) < 0.6, (one, two)


def a_named_ask_in_a_group(voice, murmur: float = 0.0) -> tuple[np.ndarray, list[tuple[float, str]]]:
    """The 2026-11-18 group call's silence, made up: two people talk, then
    one asks Valor by name for a lookup, while a third murmurs off-mic at
    `murmur` RMS, under `call.SPEAKING`, all along. Each person's sound is
    mixed as the call mixes it (`call.mixed`, 40 ms frames). The call notes
    each change of speaker `CHANGE_S` after the new voice starts, and the
    worker's prep lands while the second person talks."""
    gap = np.zeros(call.RATE * 6 // 10, np.float32)
    parts = [
        ("ana", voice("So the launch numbers look decent so far; the landing page converts better than in October.")),
        ("ben", voice("Right, but I'd like to know how many of those signups actually finished onboarding.", "am_adam")),
        ("ana", voice("Valor, can you look up how many signups we had last week, and put the number in the chat?")),
    ]  # fmt: skip
    names = {"ana": "Ana Ruiz", "ben": "Ben Ito"}
    total = sum(len(p) + len(gap) for _, p in parts) + call.RATE * 10
    tracks = {who: np.zeros(total, np.float32) for who in names}
    during, at = [], 0
    for i, (who, part) in enumerate(parts):
        if i:
            during.append((at / call.RATE + call.CHANGE_S, f"{names[who]} is speaking."))
        if i == 1:
            during.append((at / call.RATE + 1.5, PREPARED))
        tracks[who][at : at + len(part)] = part
        at += len(part) + len(gap)
    if murmur:
        lines = ["We could move the review to Thursday if the others agree.", "Then check the forms again."]
        talk = np.concatenate(
            [voice(line, w) for line, w in zip(lines, ["bm_george", "af_bella"], strict=True)]
        )
        tracks["off"] = np.resize(talk, total) * (murmur / np.sqrt(np.mean(talk**2)))
    spoke_at: dict[str, float] = {}
    frames = [
        call.mixed({w: t[i : i + CHUNK] for w, t in tracks.items()}, CHUNK, spoke_at, i / call.RATE)[0]
        for i in range(0, total, CHUNK)
    ]
    return np.concatenate(frames)[:total], during


def test_in_a_group_a_named_ask_is_answered_with_notes_and_faint_talk_behind_it(voice):
    """The 2026-11-18 group call: asked by name twice, Valor said nothing for
    a minute and a half. Not the notes: sent mid-stream as the call sends
    them, sent none, or one right after the ask, Live answered 12 of 12.
    Faint talk behind the ask, summed into what Live hears: answered 0 of
    8 at 0.005 RMS (under `SPEAKING`), and 3 of 3 once it stopped 2 s after
    the ask, so Live's own end of turn never came. The call now gives Live
    each person's sound only while they speak (`call.mixed`). Real talk
    behind it, at 0.02, is what the group nudge is for (`NAMED_S`)."""
    audio, during = a_named_ask_in_a_group(voice, murmur=0.005)
    for _ in range(3):
        seen = asyncio.run(session(audio, prep=AGENDA, here=GROUP, during=during, listen_s=0))
        assert seen["first"] is not None or "ask" in seen["tools"], seen


def test_in_a_group_talk_behind_a_named_ask_is_answered_once_the_call_asks(voice):
    """Real talk behind the ask (0.02 RMS, over `SPEAKING`) also keeps
    Live's turn open (0 of 3, 2026-11-18): the call's group note, sent
    `NAMED_S` after the stretch closes, gets the answer."""
    audio, during = a_named_ask_in_a_group(voice, murmur=0.02)
    asked = "Valor, can you look up how many signups we had last week, and put the number in the chat?"
    end = len(audio) / call.RATE - 10
    nudge = f"[note] {call.NAMED_NOTE.format(name='Ana Ruiz', text=asked)}"  # the call's own note

    async def send(s, text):
        complete = text == nudge
        await s.send_client_content(turns={"role": "user", "parts": [{"text": text}]}, turn_complete=complete)

    during = [(t, f"[note] {text}") for t, text in during] + [(end + call.QUIET_S + call.NAMED_S, nudge)]
    for _ in range(3):
        seen = asyncio.run(session(audio, prep=AGENDA, here=GROUP, during=during, send=send, listen_s=0))
        assert "ask" in seen["tools"] or (seen["first"] or 0) > end, seen


def test_in_a_group_talk_between_the_others_gets_no_lookup_and_no_word(voice):
    """2026-11-18: twice Valor jumped in on talk not to it, calling the
    worker and starting to speak, and once said a stray "Yes?". Two others
    discussing a figure Valor could look up get neither: with the persona's
    line and the group note, no lookup and no word on it in 9 of 9 (two
    voiced their silence, "(No speech detected)"); before them it spoke in
    3 of 6 ("I'll stay silent since...", 2026-11-18)."""
    gap = np.zeros(call.RATE * 6 // 10, np.float32)
    audio = np.concatenate([
        voice("How many of the trial accounts converted last month, do you remember?"), gap,
        voice("I think it was around forty, but I'd have to check the report.", "am_adam"), gap,
        voice("Okay Ben, can you check it after the call and tell me?"), gap,
    ])  # fmt: skip
    ben = len(voice("How many of the trial accounts converted last month, do you remember?")) + len(gap)
    ana = (
        ben
        + len(voice("I think it was around forty, but I'd have to check the report.", "am_adam"))
        + len(gap)
    )
    during = [(ben / call.RATE + call.CHANGE_S, "Ben Ito is speaking."),
              (ana / call.RATE + call.CHANGE_S, "Ana Ruiz is speaking.")]  # fmt: skip
    for _ in range(3):
        seen = asyncio.run(session(audio, prep=AGENDA, here=GROUP, listen_s=8, during=during))
        # No lookup and nothing on the talk; Live sometimes voices its silence ("(No speech detected)").
        assert not seen["tools"] and not re.search(
            r"forty|check|convert|report|look", seen["said"], re.IGNORECASE
        ), seen


def test_asked_what_happened_to_a_missed_request_the_voice_owns_it_and_asks_the_worker(voice):
    """2026-11-18: asked what happened to a request it missed, Valor said
    "I'm here", then promised a lookup it never started."""
    before = [
        {"role": "user", "parts": [{"text": "Ana Ruiz: Valor, can you find last week's signup numbers by "
                                    "country?"}]},
        {"role": "user", "parts": [{"text": "Ben Ito: Good idea, and split out the paid ones."}]},
    ]  # fmt: skip
    asked = voice("Valor, we had a question for you a minute ago. What happened?")
    for _ in range(3):
        seen = asyncio.run(session(asked, before, AGENDA, here=GROUP))
        assert {"ask", "person", "backlog"} & set(seen["tools"]), seen  # a lookup started in that turn
