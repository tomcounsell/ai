# Call voice: lead a 1:1, and more detail means more (P-038, Tom's scope of 2026-11-17)

Tom, #valor-product, 2026-11-17 04:25 UTC: don't fix the pauses now; in a
1:1 call Valor leads as PM, once an item is settled it moves to the next
item or open question; asked for more detail it should not repeat itself
("thinking like a teacher", as in the present skill), with background prep
on upcoming questions during the call. This replaces task babffe7623c2
(not merged: its build is uncommitted, so nothing of it is taken out here).
**For the session this reports to:** stop it, `valor-record act stop
babffe7623c2`, by Tom Counsell, why: scope changed by Tom 2026-11-17 04:25 UTC.
Value high, appetite small. No governance: no check, gate or guard is
added; no change to end-of-speech detection, the 2-second nudge, memory
trimming or the model.

## Cases (observed, call records)

- Going quiet once settled (1:1, 2026-11-16 05:30 call): #117965 "Understood,
  I will note that resource management is ..." then Tom #117966 "Okay, what's
  next?"; #118031-#118032 "Sounds good." then #118033 "All right, what
  else?"; #118046 Valor asks Tom "What priority should we review next?".
  08:12 call: #118639 then Tom #118640 "What's the next item?"; #118668
  "Anything else?". Reported: Tom's message above.
- Same answer again: 2026-11-16 05:30 call #117846-#117875, a Sentry error
  explained five times without its facts; 2026-11-13 group call
  #113141-#113178, asked three times for the priority criteria, Valor
  restated the goal and Tom said "Never mind".

## Change

1. **Persona, `persona/conduct.md` "In a call"** (the one text the call and
   every turn read). "Short and plain" keeps "Follow what they want to
   discuss" and loses "don't steer the call", which moves into a new bullet
   **Lead a 1:1; wait in a group**: with one other person in the call you
   lead it as the project's manager (Tom, 2026-11-17): once an item is
   settled (a short question confirming it is settled is always fine), open
   the next item or open question yourself, from your notes and the queue,
   without waiting to be asked what is next. With two or more others, don't
   steer the call: wait to be asked. New bullet **More detail means more**,
   after "Answer what was asked" (which keeps "say plainly you don't know and
   ask your worker"): asked for more, or asked again, never give the same
   answer in other words; teach it, in the present skill's words, "a taught
   explanation, not the same prose in a nicer font": a concrete example
   before the abstraction, one hard concept an analogy and then the
   mechanism, or the next level down (the case, the number, the step). Your
   worker's notes on what is coming hold that depth; when they don't, ask
   your worker instead of rephrasing. (The skill's text has no literal
   "think like a teacher"; these are its lines, quoted, checked in the
   present skill of 2026-11-11.)
2. **The call's own instruction, `core/call.py` `INSTRUCTIONS`.** The
   one-person sentence becomes "...everything they say is to you: answer it,
   and lead the call as "In a call" below says." A line for the new note:
   a note opening "Your worker prepared" is depth for items still to come:
   use it when the call reaches them, never answer or read it out. No second
   copy of the conduct.
3. **The worker prepares ahead, `core/call.py` `Call.work`.** A new
   `preparer` (like `reader`, wired in `core/__main__.py` `_call` through the
   same `turn`, so turns stay serial on one session). `PREPARE`: from the
   call so far and the notes, the next one or two items or open questions not
   yet reached; for each, its case (what, when, where recorded), the numbers,
   and the two or three follow-ups likely asked with answers, each a level
   below the last; "nothing ahead" when none. It runs once after the reading
   (or at the start of an invited call), then whenever no question has waited
   for `PREP_S` (45 s) and the call has new lines since the last one. Its
   answer goes to the Live session as a `note` (`turn_complete=False`: it
   asks no reply) opening "Your worker prepared, for when the call reaches
   it", is not posted in the chat, and "nothing ahead" sends nothing. A
   question asked while a prep runs is answered after it (one lane).
4. **The worker's answers, `_call`'s `worker` prompt**: when the call so far
   already answered this, give what that answer lacked (an example, a
   different angle, the next level down), never the same answer.
   `skills/sdlc/call.md` says a turn is either a question or a prep-ahead,
   and the prep's exit (short notes, not posted).
5. `docs/bridges/meet.md` (notes that reach the voice) and the `Call.work`
   docstring say the same.

## Out of scope

Turn detection, pause cut-offs, the nudge, rocky-after-a-while (Tom), the
English-caps sentence babffe7623c2 planned, an extended-thinking model,
carrying prep notes into a fresh session after a refused handle (no case).
Rabbit hole: a lookup asked mid-prep waits for it; the live test reports
each prep turn's length, and the prompt asks for short notes.

## Tests

- `tests/test_call.py`, wording: the "In a call" section holds the 1:1 lead
  rule with Tom's date, the confirming question, the group wait, "Follow what
  they want to discuss", the present skill's quoted lines and "instead of
  rephrasing"; "don't steer the call" only in the group sentence; the
  instruction's one-person line and prepared-note line; the worker prompt
  and `skills/sdlc/call.md` lines.
- `Call` with the test fakes: the first prep follows the reading and goes as
  one note with `turn_complete=False`, never posted; an invited call preps at
  start; "nothing ahead" sends no note; no second prep without new lines; a
  prep after `PREP_S` idle with new lines; a question queued during a prep
  is answered after it, and its answer is still posted and noted.
- Live (`tests/test_live_meet.py`, the call's real settings, stub worker
  whose prep names a fact for item two): 1:1, prep holding two items; the
  prepared note reaches the session before Valor opens item two; after Tom's
  device confirms item one settled, Valor opens item two unprompted within
  15 s; with a second device joined (two others) the same exchange gets
  silence for 15 s. Asked "tell me more" about item one twice: each answer
  holds content words the first lacks (the prepared fact), and neither
  restates the first. Prep turn lengths printed.
- Full suite and ruff.
