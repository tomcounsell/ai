# Call voice: wait out the pause; specifics or a lookup (P-038)

**Job.** Someone talking with Valor in a call finishes a thought without being
talked over, and gets the fact they asked for. Value high, appetite small.
Research behind it: Google's Live API best-practice pages, read 2026-11-17
(task 0cd8d83cea60).

## (a) Wait out the pause

**First, the sort (no code).** Classify every `spoke (cut off)` row in the
four recorded calls (2026-11-13, two on 2026-11-16 before the double-reply
fix, one after) from the call ledger and the saved `transcript.json`
(segment starts and ends). Causes: *early start* (Valor began in a pause and
the speaker went on with the same thought), *double reply* (a second Valor
turn straight after his first; fixed 2026-11-16 in
23f56a825), *deliberate* (the speaker cut a reply on purpose: "Never mind",
goodbye, "you're talking over yourself"). Report the counts and each row.
Preliminary read at planning: 11 cut-offs, about 4 early starts, 3 double
replies, 3 deliberate, 1 unclear. Early-start replies began about 1.5 s after
the speaker's last sound, the same as every normal reply, so they are Live's
own end-of-turn, not our nudge (`_answer_alone` fires 2 s after the 1.5 s
stretch close, 3.5 s or more). The double-reply fix does not touch this, so
the one clean call since proves nothing either way: it was four minutes of
complete questions.

**Decision rule.** If early starts are 2 or more of the sort, change the
setting below. If not, change nothing in (a) and say so.

**Change.** In `live_config`, `RealtimeInputConfig` gains
`automatic_activity_detection` with `silence_duration_ms=END_SILENCE_MS`
(a named constant in `core/call.py`, starting at 1200; Live's default is about
800). Start sensitivity, prefix padding and `NO_INTERRUPTION` stay as they
are. If the live test still answers a 1 s pause at 1200, add
`end_of_speech_sensitivity=END_SENSITIVITY_LOW`, and re-run. Never 2000 ms or
more (Google: replies feel slow). The `live_config` docstring and
`docs/bridges/meet.md` say the wait and why; `docs/plans/gemini-live.md`'s
stale "start sensitivity LOW, prefix padding 300 ms" line is corrected.

## (b) The specific, or a lookup

`INSTRUCTIONS` in `core/call.py` (the call's own rules, not the persona's)
are rewritten in Google's shape: each rule an "if x, do y" line. The `ask`
line becomes: if asked for a fact (a figure, what an error is, why something
ranks where it does) that your notes and tools do not hold, call `ask` in that
turn and say you are checking; if asked again, or told they are confused,
and your answer would repeat what you said, call `ask`; never say it again in
other words. The cases are the 2026-11-16 Sentry error explained five times
and the 2026-11-13 priority criteria. Google's English sentence ("RESPOND IN
ENGLISH. YOU MUST RESPOND UNMISTAKABLY IN ENGLISH.") is folded into the
persona's "English, always" bullet in `persona/conduct.md`, so the persona stays
the one source for call conduct; `call.py` copies no persona rule.

## Tests

- `tests/test_call.py`: the instruction `live_config` builds contains the
  new `ask` lines and the persona's English line with Google's sentence; the
  config carries `silence_duration_ms == END_SILENCE_MS` while start
  sensitivity stays unset and `NO_INTERRUPTION` stays (if (a) changes).
  Existing assertions on the old wording are updated, not deleted.
- Live, `VALOR_LIVE=1`, one new test in `tests/test_live_meet.py`'s module or a
  sibling, on the call's own `live_config` with the one-person note:
  1. A Kokoro clip "I think the next thing we should look at" + 1.0 s of
     silence + "is the backlog, and then the costs." in 40 ms chunks: no Valor
     audio starts before the second half ends. Run on the old config too; the
     old one answering in the pause shows the test bites.
  2. The gap from a complete question's end to Valor's first audio, median of
     five, old config against new: the added delay, stated in the report.
  3. A question the voice cannot hold ("How many times did the Sentry error
     VALOR-MJ fire this week?"): the first turn calls `ask`.
  4. Asked again "I'm confused, what does that error mean?" after a vague
     answer: that turn calls `ask`.
- Full suite under 5 minutes.

## Not in scope

No speaking a lookup's answer unasked, no progress updates, no
extended-thinking model, no change to compression or the nudge, no new
check, gate or guard. Pitch rabbit hole held: a longer wait slows every
answer, by the measured amount above.
