# Call voice: wait out the pause; specifics or a lookup (P-038)

**Job.** Someone talking with Valor in a call finishes a thought without being
talked over, and gets the fact they asked for. Value high, appetite small.
Research: Google's Live API best-practice pages, read 2026-11-17 (task
0cd8d83cea60).

## (a) Wait out the pause

**The sort (no code).** Classify every `spoke (cut off)` row in the four
recorded calls (2026-11-13; two on 2026-11-16 before the double-reply fix,
23f56a825; one after) as *early start* (Valor began in a pause and the speaker
went on with the same thought), *double reply* (a second Valor turn to one
thing heard), or *deliberate* ("Never mind", goodbye, "talking over
yourself"). Report counts and each row. Planning read: 11 cut-offs, about 4
early starts, 3 double replies, 3 deliberate, 1 unclear. For each early start,
measure from the call's stereo recording (on Drive, linked from the meeting's
`meta.json`; Tom's channel) how long the speaker had been silent when Valor's
audio began, and how long until they went on. The ledger's stretch timings
only bound it: replies began about 1.5 s after the last sound, at the stretch
close, so not our nudge (3.5 s or more), and not touched by the double-reply
fix; the one clean call since was four minutes of complete questions.

**The unknown.** On 2026-11-05 `silence_duration_ms=500` with
`END_SENSITIVITY_HIGH` (aea5a92d3) moved the reply gap from 2.08 s to 2.25 s,
the wrong way, and was reverted (bdc4c2d12). So `silence_duration_ms` may
barely govern Live's end of turn. The live test tries both knobs.

**Live test first, then the change.** One live test, on `live_config`'s own
settings with the one-person note, plays a Kokoro clip "I think the next
thing we should look at", a pause, then "is the backlog, and then the costs."
in 40 ms chunks, three runs per pause, at 1.0 s (the acceptance) and at the
median measured early-start pause. Configs: today's; `silence_duration_ms=1200`;
`END_SENSITIVITY_LOW`; both. Each config also runs the existing reply-gap
measurement (`tests/test_live_meet.py`, the median printed at line 240) for
the added delay. Branches:
- Today's config does not answer the pause in at least 2 of 3: the test does
  not bite. Report it, change nothing.
- A config answers it in 0 of 3 where today's did: ship the one with the
  smallest added delay, under 2 s total wait. The test then asserts it.
- No config stops it: change nothing, report the numbers.

**The change, if one ships.** `RealtimeInputConfig` in `live_config` gains
`automatic_activity_detection` with the winning setting (a named constant).
Start sensitivity, prefix padding and `NO_INTERRUPTION` stay. The docstring's
"Live's own sensitivity ... is kept" sentence is rewritten to say what is set
and why, with both dates; `docs/bridges/meet.md` likewise; the stale "start
sensitivity LOW, prefix padding 300 ms" line in `docs/plans/gemini-live.md`
is corrected either way.

## (b) The specific, or a lookup

The rule already exists, in the persona's "In a call" ("Answer what was
asked ... When your notes don't hold it, say plainly you don't know and ask
your worker"), and was there for both recorded failures; what changes is its
shape. That bullet in `persona/conduct.md` is rewritten as "if x, do y"
lines from the cases: asked for a fact (a figure, what an error means, why an
item ranks where it does) that your notes and tools don't give, call your
worker in that turn; asked again or told they are confused, and your answer
would repeat what you said, call your worker, never say it again in other
words. The "English, always" bullet takes Google's sentence (RESPOND IN
ENGLISH. YOU MUST RESPOND UNMISTAKABLY IN ENGLISH.). `INSTRUCTIONS` in
`core/call.py` is unchanged: its `ask` line stays tool mechanics, and its other
lines, tuned in earlier live tests, keep their wording.

## Tests

- `tests/test_call.py`: the built instruction holds the persona's new lines
  and Google's English sentence; if (a) ships, the config carries the setting
  with start sensitivity unset and `NO_INTERRUPTION` kept. Assertions on the
  old wording are updated.
- Live, `VALOR_LIVE=1`, beside the pause test: a question the voice cannot
  hold ("How many times did VALOR-MJ fire this week?") calls `ask` on the
  first turn; and a scripted earlier exchange (the question, then a model
  turn with a vague answer, sent through `send_client_content`) followed by
  "I'm confused, what does that error mean?" calls `ask`. Each runs three
  times; a pass is 3 of 3 on the new persona, also run on the old to report.
- Full suite under 5 minutes.

## Not in scope

No speaking a lookup's answer unasked, no progress updates, no
extended-thinking model, no change to compression or the nudge, no new check,
gate or guard. A longer wait slows every answer, by the measured amount.
