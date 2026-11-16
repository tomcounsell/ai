# A call says one reply per thing heard, and stays in English on a short phrase

Tom, 2026-11-16, after the 08:12 call: Valor talked over him and switched to
Spanish (he heard Portuguese) for a moment, and not for the first time. He
also asked (09:59, reported) whether the voice connection crashed and
restarted. Bug fixes: a commit with tests, no new check, gate or guard.

## Evidence (observed, 2026-11-16, unless tagged)

- **No reconnect.** `meet.log` holds one "the Live session is open" per call
  (748c18ae92b7 at 08:12:30; d5f185f2d10d at 05:30:42). There are no
  resume, drop, stall or going-away lines, and one media connection per call.
  `_say` cuts a playing reply before the next one starts, and every
  `meet.say` outcome comes before the next one is held. So two voices never
  play at once. In this call, Tom's reading is wrong. The resumes he
  remembers were on 2026-11-06 (eleven in one call, reported, fixed by
  5136cdfe334a).
- **Cause A: a tool's response starts a second reply.** Valor speaks a
  readback, calls `post` or `settle` (or `ask`) in the same turn, and
  `_tools` answers with a blocking `FunctionResponse`. Live then generates a
  new turn. Today: #118690 then #118695 (with `post` meet/4b6cf27b8de6/0/post),
  and the empty #118659 then #118667 (with post meet/a2c5c0d1244b/0/post).
  In the 12:30 call: #117642 then #117650, #117739 then #117750, and
  #117885 then #117893, each with an `ask`. A live probe on gemini-3.8-live
  reproduced it: WHEN_IDLE spoke, called `post`, then spoke again. SILENT
  produced no follow-on turn in 4 of 4 runs.
- **Cause B: the "words you did not catch" prompt fires after Valor has
  already answered.** `_speakers`' no-transcript branch sets `unanswered`
  2.3 s after a sound even when a reply began after that sound. In the 12:30
  recording, a 0.6 s sound at 683.3 s came just before the reply at 684.4 s.
  After that reply, `_answer_alone` asked again, and Valor said "Could you
  please say that again?" (#117782, then #117788). A 0.4 s sound at 803.1 s
  set the same prompt, which fired at 807.6 s just as Tom said "Tell me
  more". The result was "say that again" and then the real answer, over him
  (#117833, then #117839 cut). At 12:50:45 Tom said, "Sounds like you're
  talking over yourself. Are there two of you?"
- **Cut-offs** (#118717, #118723 and #118738; seven in the 12:30 call) are
  the barge-in at 0.4 s working as built. Tom spoke over replies that A and
  B had put in his way, and over late answers to his previous line.
- **The language.** In the recording, "¿Qué tal?" was a 0.4 s sound from Tom
  at 288.3 s, made as the unneeded second reply (A) ended. Recognition is
  already hinted `en-US`. The SDK documents `language_codes` as hints and
  has no pin. `SpeechConfig.language_code="en-US"` is ignored: the probe
  answered "¿Qué tal?" in Spanish with it and without it. The model hears
  the audio itself, so its reply language comes from the instruction. The
  persona's line leaves "clearly" to the model's judgement, and to the model
  "¿Qué tal?" is clear: the probe answered in Spanish with the line as
  written. The same probe with the line below answered in English and asked
  Tom to say it again, and it still switched for whole sentences of Spanish
  that asked to switch. The earlier switch, Portuguese on 2026-11-06 in
  another project's call, is reported (task e3b0988bd3d4).

## Fix

1. **`core/call.py` `TOOLS`, `_tools`.** `post`, `settle`, `ask` and `leave`
   are declared `behavior=NON_BLOCKING`, since their result carries nothing
   to say. Their response is `scheduling=SILENT` when the calling turn has
   already said words (`not wordless(reply.text)`), and `WHEN_IDLE`
   otherwise, so a turn that called first still speaks once. The lookups
   (`person`, `backlog`, `open_decisions`) stay blocking, because their
   result is the answer. The `live_config` docstring's language sentence is
   corrected to what the probe showed.
2. **`_begin`** sets `voiced_s = 0`: speech heard before a reply is what the
   reply answers, so it can never later ask for an answer.
3. **`persona/conduct.md` "In a call".** The English line becomes: "**English.**
   A few words that sound like another language (a greeting, a short phrase)
   are English you did not catch: say so in English and ask them to say it
   again. Switch only when someone speaks whole sentences in another
   language, or asks you to." The call reads this section as it starts, so
   there is one source.

Out of scope: the empty-words prompt firing on a 0.4 s sound with nothing
before it (803.1 s). It is the 2026-11-05 "Hello." behaviour working as
built, and tuning it needs more than one case. The barge-in threshold. Any
reconnect change, since none happened.

## Tests (`tests/test_call.py`, the fake Session)

- A turn that speaks, then calls `post`: the response is SILENT, and no
  second say. A turn that calls `ask` before any words: WHEN_IDLE. `person`:
  no scheduling. The declarations carry NON_BLOCKING only on the four.
- `settle` read back aloud, then called: SILENT.
- Tom makes a short sound, Live answers it, and the reply plays out: no
  "words you did not catch" note follows. The existing no-reply test still
  gets its one note.
- The call instruction carries the new English line (updates the line-800
  assert). Full suite under 5 minutes.
- Live, after the build: the probes (`../probe/q.py`, `t.py`) are rerun
  against the built `live_config`, 3 runs each: "¿Qué tal?" gets English, a
  full Spanish request still switches, and a readback followed by `post`
  gives one turn.

Done when Tom's next call has no doubled reply after a settle or an ask, and
no switch on a short phrase.
