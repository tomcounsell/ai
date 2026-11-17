# Call voice: lead a 1:1, and more detail means more (P-038, Tom's scope of 2026-11-17)

Tom, #valor-product, 2026-11-17 04:25 UTC: don't fix the pauses now; in a
1:1 call Valor leads as PM, once an item is settled it moves to the next
item or open question; asked for more detail it should not repeat itself
("thinking like a teacher", as in the present skill), with background prep
on upcoming questions during the call. Value high, appetite small. No
governance: no check, gate or guard; no change to end-of-speech detection,
the 2-second nudge, memory trimming or the model.

**Builds on task babffe7623c2, not instead of it** (the product session's
correction, 2026-11-17). Its commit 3143cda3e (branch valor/babffe76, not on
main at b9625e07d when this was written) changes no pause or turn setting:
it rewrites "Answer what was asked" as two if-then lines (a fact not held:
say so and ask the worker that turn; asked again or told they're confused:
ask the worker, never say it again in other words), adds Google's English
sentence, `tests/test_live_call_voice.py` and docs on why Live's end of turn
is kept. Critique 2 (Request (c), nothing on turn detection): the build
carries only its persona lines, its wording test and its two ask-the-worker
live tests, as written; its pause measurement, end-of-turn prose and pause
plan stay on its branch, which should not also merge.

## Cases (observed, call records)

- Going quiet once settled, 1:1, 2026-11-16 05:30 call: Valor #117965
  "Understood, I will note ...", Tom #117966 "Okay, what's next?"; Valor
  #118031 agrees, Tom #118032 "Sounds good.", Valor silent, Tom #118033 "All
  right, what else?"; Valor #118046 hands the pick back: "What priority
  should we review next?". 08:12 call: Tom #118640 "What's the next item?",
  #118668 "Anything else?". Reported: Tom's message above.
- Same answer again: 05:30 call #117846-#117875, a Sentry error explained
  five times without its facts; 2026-11-13 group call #113141-#113178, the
  priority criteria asked three times, the goal restated, Tom "Never mind".

## Change

1. **Persona, `persona/conduct.md` "In a call"** (one text for the call and
   every turn). "Short and plain" keeps "Follow what they want to discuss";
   "don't steer the call" moves into a new bullet **Lead a 1:1; wait in a
   group**: with one other person in the call you lead it as the project's
   manager (Tom, 2026-11-17). Once an item is settled (a short question
   confirming it is settled is always fine), the same reply that reads it
   back or answers their confirmation ends by opening the next item or open
   question from your notes and the queue: open it, never ask which to take
   next. With two or more others, don't steer the call: wait to be asked.
   New bullet **More detail means more**, after the if-then lines: asked for
   more, teach it, in the present skill's words: "a taught explanation, not
   the same prose in a nicer font", "a concrete example before the
   abstraction", "one hard concept an analogy and then the mechanism", or the
   next level down (the case, the number, the step), from what your worker
   prepared; when that holds nothing new, the line above holds: ask your
   worker. (The skill has no literal "think like a teacher"; these are its
   lines, present skill of 2026-11-11.)
2. **`core/call.py` `INSTRUCTIONS`.** The one-person sentence adds "and lead
   the call as "In a call" below says". The `settle` line adds: in a 1:1, the
   read-back ends by opening the next item (its tool reply is silent once the
   turn has spoken, `_tools`, so a later turn never comes). A line: a note
   opening "Your worker prepared" is depth for items still to come; use it
   when the call reaches them, never answer or read it out.
3. **The worker prepares ahead, `Call.work`.** A `preparer` beside `reader`,
   wired in `_call` through the same `turn` (one session, serial). `PREPARE`:
   from the call so far and the notes, the next two items or open questions
   not yet reached; for each its case (what, when, where recorded), the
   numbers, and two or three likely follow-ups with answers, each a level
   below the last; short; "nothing ahead" when none. **Events, not a timer:**
   one prep after the reading (or at an invited call's start), then one after
   each `settle`, `backlog` or `open_decisions` call (the voice moving through
   the queue). Events while a prep is pending or running coalesce into one
   more at most; a question already queued goes first. The result goes as a
   `note` (`turn_complete=False`, no reply asked) opening "Your worker
   prepared, for when the call reaches it"; never posted; "nothing ahead"
   sends nothing.
4. **The worker's answer prompt, `_call` `worker`**: when the call so far
   already answered this, give what that answer lacked (an example, another
   angle, the next level down). `skills/sdlc/call.md`: a turn is a question
   or a prep-ahead; the prep's exit (short notes, not posted).
5. `docs/bridges/meet.md` and the `Call.work` docstring say the same.

## Out of scope

Turn detection, cut-offs, the nudge, rocky-after-a-while (Tom); an
extended-thinking model; prep notes carried into a fresh session after a
refused handle (no case). Cost named: a lookup asked during a prep waits for
it; the live test reports that wait.

## Tests

- `tests/test_call.py`, wording: the lead rule with Tom's date, the
  confirming question, "never ask which to take next", the group wait,
  "Follow what they want to discuss", "don't steer the call" only in the
  group sentence; the present skill's quoted lines and "ask your worker";
  3143cda3e's if-then lines still present; the instruction's one-person,
  `settle` and prepared-note lines; worker prompt and `skills/sdlc/call.md`.
- `Call` fakes: first prep after the reading, one note, `turn_complete=False`,
  never posted; invited call preps at start; "nothing ahead" sends nothing;
  lines growing alone start no prep; a `settle` (and a `backlog`) starts one;
  three events during a prep give one more, not three; a queued question is
  answered before the next prep, posted and noted as today.
- Live (`tests/test_live_meet.py`, the call's real settings, stub worker):
  prep holds two items, the stub's prep gives item one two levels of depth
  and item two a named fact. 1:1: the prepared note reaches the session
  before Valor opens item two; item one settled through `settle` and, in a
  second run, by a spoken "yes, that's settled": Valor opens item two
  unprompted within 15 s. With a second device joined (two others) the same
  exchange gets silence for 15 s. "Tell me more" about item one twice: the
  second answer holds content words absent from the first, the first holds
  words beyond the opening answer, and neither restates the one before.
  Printed: each prep turn's length and a lookup's wait during a prep.
- Full suite and ruff.
