"""How the call's ears end a turn (P-042, step 1): recordings of calls,
the others' channel streamed in real time to the transcriber as the call
opens it (`call.Gemini.hear`), every turn end it gives scored against the
recording's own speech, found by Silero (onnx-asr's model).

A pause is mid-thought when the same channel goes on within 3 s and
before any reply of Valor's (the left channel) is over; a turn ends where
Valor replies or 3 s of quiet follow. A turn end inside a pause of 1.5 s
or less is a false end (the call drops a turn when the person goes on);
an effective cut-off is one where they had not gone on by the time
Valor's first sound would have played (`FIRST_SOUND_S`, the 10th
percentile measured, 2026-11-25). Measured on Tom's calls of 2026-11-13
and 2026-11-16 (2026-11-25): no effective cut-off in 435 pauses of 1.5 s
or less, 3 runs; false ends 8% of pauses under 1 s, 40% of 1 to 1.5 s;
turn ends 1.3 s after the last word (median), 1.6 s (p90).

Runs only when `VALOR_LIVE=1` and `VALOR_EARS_DIR` names a folder of
two-channel 16 kHz WAV recordings (Valor left, the others right), kept out
of the repository (client calls)."""

import asyncio
import itertools
import os
import statistics
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from core import call, credentials
from core.settings import settings

pytestmark = [
    pytest.mark.spend(usd=0.50),
    pytest.mark.skipif(
        os.environ.get("VALOR_LIVE") != "1" or not os.environ.get("VALOR_EARS_DIR"),
        reason="live recordings: VALOR_LIVE=1 and VALOR_EARS_DIR",
    ),
]
FRAME = 320  # 20 ms
FIRST_SOUND_S = 2.6
QUIET_CUT_S = 5.0  # longer quiet is cut to this, so a recording streams in less than its length


def speech(x: np.ndarray, *, silero: bool) -> list[tuple[float, float]]:
    """Runs of speech, in seconds, gaps under 0.2 s bridged, runs under 0.1 s dropped."""
    if silero:
        import onnx_asr

        vad, out = onnx_asr.load_vad("silero")._model, []
        state, ctx = np.zeros((2, 1, 128), np.float32), np.zeros(64, np.float32)
        for i in range(0, len(x) - 512, 512):
            f = x[i : i + 512]
            o, state = vad.run(["output", "stateN"], {"input": np.concatenate([ctx, f])[None], "state": state,
                                                      "sr": np.array([call.RATE], np.int64)})  # fmt: skip
            ctx = f[-64:]
            out.append(float(o[0, 0]) >= 0.5)
        on = np.repeat(np.array(out), 512)
        on = on[: len(on) // FRAME * FRAME].reshape(-1, FRAME).mean(1) >= 0.5
    else:
        n = len(x) // FRAME
        on = 20 * np.log10(np.sqrt(np.mean(x[: n * FRAME].reshape(n, FRAME) ** 2, 1)) + 1e-9) > -45
    runs, i = [], 0
    while i < len(on):
        if on[i]:
            j = i
            while j < len(on) and on[j]:
                j += 1
            if runs and (i - runs[-1][1]) * FRAME / call.RATE < 0.2:
                runs[-1][1] = j
            else:
                runs.append([i, j])
            i = j
        else:
            i += 1
    return [
        (a * FRAME / call.RATE, b * FRAME / call.RATE) for a, b in runs if (b - a) * FRAME / call.RATE >= 0.1
    ]


def labelled(path: Path) -> tuple[np.ndarray, list[dict], list[float]]:
    """The others' channel with long quiet cut, its mid-thought pauses and
    its turn ends, on the cut clock."""
    with wave.open(str(path)) as w:
        both = np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, 2).astype(np.float32) / 32768
    them, valor = speech(both[:, 1], silero=True), speech(both[:, 0], silero=False)
    keep, cur = [], 0.0  # (start, end) of the original kept
    for s, e in sorted(them + valor):
        if s - cur > QUIET_CUT_S:
            keep += [(cur, cur + QUIET_CUT_S / 2), (s - QUIET_CUT_S / 2, s)]
        elif s > cur:
            keep.append((cur, s))
        keep.append((max(cur, s), max(cur, e)))
        cur = max(cur, e)
    keep.append((cur, min(cur + QUIET_CUT_S, len(both) / call.RATE)))
    starts = np.cumsum([0.0] + [b - a for a, b in keep])

    def moved(t):
        for (a, b), at in zip(keep, starts, strict=False):
            if a <= t <= b:
                return at + t - a
        return None

    holds, ends = [], []
    for (_, e0), (s1, _) in itertools.pairwise(them):
        gap, replies = s1 - e0, [b for a, b in valor if a < s1 and b > e0]
        if gap < 0.5:
            continue
        if gap <= 3.0 and (not replies or max(replies) > s1):  # they went on before any reply was over
            holds.append({"end": moved(e0), "resume": moved(s1), "gap": gap})
        else:
            ends.append(moved(e0))
    x = np.concatenate([both[int(a * call.RATE) : int(b * call.RATE), 1] for a, b in keep])
    return x, holds, ends


async def heard(x: np.ndarray) -> list[dict]:
    """Every turn end the transcriber gives on `x`, streamed in real time in
    40 ms chunks, reopened before `call.EARS_S` as the call does: the time
    it came and where in the audio it puts the end."""
    from google.genai import types

    gemini = call.Gemini(credentials.read_key(settings.google_keyfile, call.GOOGLE_KEY, "google-key"))
    log, chunk, step = [], call.RATE // 25, int((call.EARS_S - call.EARS_EARLY_S) * call.RATE)
    for start in range(0, len(x), step):
        piece = (np.clip(x[start : start + step], -1, 1) * 32767).astype("<i2")
        async with gemini.hear() as ears:
            t0 = time.monotonic()

            async def feed(ears=ears, piece=piece, t0=t0):
                for i in range(0, len(piece) + call.RATE * 3, chunk):
                    data = piece[i : i + chunk] if i < len(piece) else np.zeros(chunk, "<i2")
                    await ears.send_realtime_input(
                        audio=types.Blob(data=data.tobytes(), mime_type="audio/pcm;rate=16000")
                    )
                    await asyncio.sleep(max(0.0, t0 + (i + chunk) / call.RATE - time.monotonic()))

            async def receive(ears=ears, t0=t0, at=start / call.RATE):
                async for m in ears.receive():
                    kind = m.voice_activity.voice_activity_type if m.voice_activity else None
                    if kind == types.VoiceActivityType.ACTIVITY_END:
                        off = m.voice_activity.audio_offset
                        off = float(str(off).rstrip("s")) if off else time.monotonic() - t0
                        log.append({"t": at + time.monotonic() - t0, "off": at + off})

            receiving = asyncio.create_task(receive())
            await feed()
            receiving.cancel()
    return log


def test_the_ears_cut_no_one_off_inside_a_pause_and_end_a_turn_within_a_second_and_a_half():
    short, false, cut, delays = 0, 0, 0, []
    for path in sorted(Path(os.environ["VALOR_EARS_DIR"]).glob("*.wav")):
        x, holds, ends = labelled(path)
        log = asyncio.run(heard(x))
        for h in [h for h in holds if h["gap"] <= 1.5 and h["end"] is not None and h["resume"] is not None]:
            short += 1
            hit = [
                e["t"]
                for e in log
                if h["end"] - 0.5 <= e["off"] <= h["resume"] - 0.05 and e["t"] < h["resume"] + 0.5
            ]
            false += bool(hit)
            cut += bool(hit) and h["resume"] - min(hit) >= FIRST_SOUND_S
        for e0 in [e for e in ends if e is not None]:
            hit = [e["t"] - e0 for e in log if e["off"] >= e0 - 0.5 and e0 - 0.2 <= e["t"] <= e0 + 5]
            delays += [min(hit)] if hit else []
        print(f"\n{path.name}: {len(holds)} pauses, {len(ends)} turn ends, {len(log)} ends heard")
    d = sorted(delays)
    print(f"\npauses of 1.5 s or less: {short}; false ends {false} ({100 * false / max(1, short):.0f}%); "
          f"effective cut-offs {cut}; turn end after the last word: median {statistics.median(d):.2f} s, "
          f"p90 {d[int(0.9 * (len(d) - 1))]:.2f} s")  # fmt: skip
    assert cut <= short / 10 and statistics.median(d) <= 1.5
