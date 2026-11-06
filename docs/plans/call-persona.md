# A call carries the persona's asking rules

**Ask.** Tom, valor team chat on Slack, #makes-good-project, 2026-11-06
06:58 UTC: review his first full Gemini Live call as a project manager.
The review (the chat session's, no client content here) found: items
opened with a bare recommendation; a question on a wrong premise was
repeated when challenged, then "taken offline" with no where; two of four
items were not Tom's (Valor's own product-management work, and a status
item blocked on someone else); a switch to another language after one
unclear phrase; answers cut off mid-sentence.

**Premise, checked** (the Captain's ledger and meet.log, read there):
- `core/call.py` `INSTRUCTIONS` is the Live model's whole brief, written
  apart from `persona/`; it has none of "Ask well".
- The two items that were not Tom's came from the call's `prep`, which the
  room's chat session writes when it requests `meet.join`
  (`skills/sdlc/converse.md`, **Calls**: "what you would recommend"). It
  listed them as "questions to settle". One rests on the product standard
  telling Valor the product's people set the goal (`skills/sdlc/roles/
  product.md`, "Product goal first"; `product-sheet` SKILL.md, Goal tab,
  step 4, check 1); Tom, in the call: that is "entirely on your end as a
  project manager". The other was a queue placement, Valor's own work.
- The wrong-premise item was a task's own question, read through
  `open_decisions`; the call's fix is checking a challenged premise.
- Cut-offs: four of Valor's replies ended `cut: true`, three of them
  before the first stall resume, so 5136cdfe334a's fix does not explain
  them. The cut path is the barge-in (`BARGE_S` = 0.2 s of the answered
  person's sound while Valor speaks).

## What is built

1. **`persona/conduct.md`, a new `### In a call` section**, short, for
   speech, applying the rules above it rather than restating them: per
   item say what is being decided, why now and what it changes, then the
   recommendation; only what only the person holds is asked, and Valor's
   own work and a blocker on someone else are said as done or as status in
   a line; when the premise is challenged, check it (`ask` the worker) or
   drop the question, never repeat it; anything taken offline names where
   it goes (this thread, a task, Tom's DM); speak English unless the person
   speaks another language clearly and on purpose. The conduct lines now in
   `INSTRUCTIONS` (short plain sentences, follow what they want, take
   legal/pricing offline) move into it; `INSTRUCTIONS` keeps the mechanics
   (when to speak, notes, typed lines, the tools).
2. **`core/persona.py` `section(directory, title)`**: one `###` section of
   a persona file. `live_config` puts the identity line and that section
   into the system instruction, read at each call's start. One text, read
   by every turn (the chat session that writes `prep`) and by the call.
3. **English:** the persona line is the lever: a native-audio Live model
   hears raw audio and picks its reply language itself, and
   `speech_config.language_code` would forbid the "unless". Heard speech
   is transcribed with `language_codes=["en-US"]` (Live accepted it, the
   Captain, 2026-11-06), a transcript-quality hint only.
4. **At the source of the list:** `converse.md` **Calls** and the board's
   step 1: `prep`'s questions and the canvas's Open are decisions only Tom
   (or the person in the room) holds, each with what, why now, what it
   changes; Valor's own work is done before the call and listed as done;
   a blocker on someone else is status with who and since when.
   `product.md` and the `product-sheet` skill: Valor drafts the product
   goal and outcome metric from what the product's people have said, sets
   it (`Set by` Valor, with what it rests on) and tells them in a line in
   their terms. Ranking, adding and blocking rows are Valor's and never a
   question. No-go: the product's people still correct or override the
   goal, and their word replaces the draft. The wrong-premise question's
   task had the persona's "premise checked" rule and its own findings
   ruled the question out; rule 4 gains: a question your findings answer
   or show does not arise is not asked.
5. **Cut-offs** (measured: the three stray cuts were 0.22-0.24 s of sound
   on the other channel, the meant one 0.5 s and on; `BARGE_S` 0.4 s.
   Fallback had the recording been unreadable: no change, said plainly): measure the
   other channel of the call's recording (Drive copy, in `$TMPDIR` only,
   energy only) at each cut. If what cut Valor was under ~0.5 s (a
   back-channel or noise), raise `BARGE_S` to what the evidence supports,
   with a test; if it was real speech, change nothing and say so.

Docs: `docs/persona.md`, `persona/README.md` (call.py now reads it),
`docs/plans/meet.md` / `docs/bridges/meet.md` where they describe the
call's instruction.

**Out of scope:** client content anywhere in the repo; a filter on what
`open_decisions` returns (guidance only); rewriting the task's own
wrong-premise question (the call checks it instead).

**Governance:** none added. Prompt text, one config hint, one tuning.

## Tests

- `live_config`'s system instruction holds `persona.section(...,"In a
  call")` byte for byte, from a temp persona dir (proves one source), and
  the moved conduct lines are no longer in `INSTRUCTIONS`.
- The section carries each rule: decided/why now/changes before the
  recommendation, a challenged premise goes to `ask`, offline names where,
  English unless.
- `input_audio_transcription.language_codes == ["en-US"]`; no
  `speech_config.language_code`.
- `persona.render` still includes the section (every turn reads it);
  `section` of a missing title raises `PersonaUnreadable`.
- If `BARGE_S` moves: a back-channel shorter than it does not cut Valor;
  speech longer than it does.
- Full suite under 5 min. Rollout to the four Macs; on the Captain the live
  Meet test through the kernel's call path, with the hint accepted, the
  call answered and no resume. Done when Tom's next call goes context
  first, in English, with only his decisions.
