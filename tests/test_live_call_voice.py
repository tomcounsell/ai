"""The call's voice on Gemini Live, without Meet (P-038): a pause inside a
thought is waited out, and a fact the voice does not hold is asked of the
worker on the first turn. The session is the call's own (`call.live_config`,
the one-person note the call sends), fed a Kokoro voice in 40 ms chunks as
the call feeds Live.

Runs only when `VALOR_LIVE=1`, with Kokoro's files fetched and the Gemini key
copied (`python -m core google-key`)."""

import asyncio
import os
import time

import numpy as np
import pytest

from core import call, credentials
from core.settings import settings

pytestmark = [
    pytest.mark.spend(usd=0.10),
    pytest.mark.skipif(os.environ.get("VALOR_LIVE") != "1", reason="a live Gemini session: VALOR_LIVE=1"),
]
ALONE = "Only Tom Counsell is here with you: everything they say is to you, so answer it."
# What the voice held in the 2026-11-16 call: the error's title, not what it means.
PREP = (
    "Next on the backlog: a runtime error in Sentry, raised when the system applies a difference while no "
    "difference is active. Medium value, small appetite."
)
CHUNK = call.RATE * 40 // 1000


@pytest.fixture(scope="module")
def voice():
    from kokoro_onnx import Kokoro

    folder = os.path.expanduser(settings.tts_model_dir)
    kokoro = Kokoro(f"{folder}/kokoro-v1.0.onnx", f"{folder}/voices-v1.0.bin")

    def clip(text: str) -> np.ndarray:
        pcm, rate = kokoro.create(text, voice="af_heart", lang="en-us")
        pcm = pcm[np.linspace(0, len(pcm) - 1, len(pcm) * call.RATE // rate).astype(int)]
        loud = np.flatnonzero(np.abs(pcm) > 0.01)
        return pcm[loud[0] : loud[-1] + 1].astype(np.float32)

    return clip


async def session(
    audio: np.ndarray, before: list[dict] = (), prep: str = "Tom and Valor review the backlog."
) -> dict:
    """Live's first voiced audio, in seconds on `audio`'s clock, and the
    tools it called, with `before` sent as the conversation so far."""
    from google.genai import types

    key = credentials.read_key(settings.google_keyfile, call.GOOGLE_KEY, "google-key")
    out: dict = {"first": None, "tools": [], "said": ""}
    audio = np.concatenate([np.zeros(call.RATE // 2, np.float32), audio, np.zeros(call.RATE * 8, np.float32)])
    async with call.Gemini(key).connect(call.live_config("Valor", prep, None)) as s:
        await s.send_client_content(**call.note(ALONE))
        for turn in before:
            await s.send_client_content(turns=turn, turn_complete=False)
        t0 = time.monotonic()

        async def receive():
            async for m in s.receive():
                for fc in m.tool_call.function_calls if m.tool_call else []:
                    out["tools"].append(fc.name)
                    result = call.ASKED if fc.name == "ask" else "nothing recorded"
                    await s.send_tool_response(
                        function_responses=[
                            types.FunctionResponse(id=fc.id, name=fc.name, response={"result": result})
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
        for i in range(0, len(audio), CHUNK):
            await s.send_realtime_input(
                audio=types.Blob(
                    data=call.pcm16(audio[i : i + CHUNK]), mime_type=f"audio/pcm;rate={call.RATE}"
                )
            )
            await asyncio.sleep(max(0.0, t0 + (i + CHUNK) / call.RATE - time.monotonic()))
        task.cancel()
    return out


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
