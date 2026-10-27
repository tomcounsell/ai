# The plan stage asks its stakeholder first

**Ask.** Tom, valor team chat on Slack, 2026-10-27 05:26-05:27 UTC: "if it
did 20 minutes of progress without raising these questions, that would be
worrying" and "yeah, workers usually have clarifying questions for
stakeholders. you should answer what you can and raise what you can't to
me".

**Incident.** Task 991c42b25472 (Valor in Google Meet) wrote and revised its
plan four times between 05:01 and 05:14 UTC, through three critique rounds
all `revise`, with no `question.asked` row. It settled the stakeholder's
questions itself (call UX, who answers, how Tom is told apart, misheard
answers) and missed two findings Tom held.

**Premise, checked.** A plan turn receives `skills/sdlc/plan.md` (via
`core/tasks.py` `stage_text`) beside `skills/sdlc/channel.md` ("How this
task reaches Tom"), both rendered by `tasks.dispatch`. The plan stage can
already ask: PLAN has the verdict `asked` (to WAITING, back to PLAN on the
answer), and a child task's question goes to the session that started it.
Nothing tells it to. `plan.md` says "A question only Tom can answer";
`channel.md` says "Ask only for vision, priorities, ... or something only
Tom holds; for anything else, ask the advisor", which sorts call UX and
"who answers" to the advisor: how 991c42b25472 settled them alone. A
question carries one `question_id`, and one `task.answer` closes it.

## What is built

1. **`channel.md`: questions go to the stakeholder.** The question bullet
   becomes: a question goes in `.valor/question.md`, to the session that
   started this task, which answers what it can and raises the rest to Tom
   (to Tom when no session did). Ask it what the request leaves open about
   what its users see or hear, who does what, and what happens when it
   goes wrong, as well as what only Tom holds. The advisor stays for a
   second opinion on how to build it. Pricing, positioning and marketing
   stay Valor's.
2. **`plan.md`: ask first.** After "Your prompt holds ...": when the work
   has user-facing behaviour or open product choices, the first plan turn
   writes no plan. It puts every material question in `.valor/question.md`
   as one numbered batch, each with the answer it will assume, then the
   approach in a few lines. It skips asking when its prompt already holds
   the answers or no answer would change the plan. A revision whose
   critique findings would change a product or UX choice asks the same way
   rather than deciding. The exit line becomes "A question goes in
   `.valor/question.md` instead".
3. **`converse.md` item 3: answer what you can, once.** For a task's
   question, answer from the request, the chat and the code what you can.
   When that is every item, `task.answer` at once. Otherwise raise only
   the rest in the chat, with your recommendation and the task's stated
   defaults, and wait; then pass one `task.answer` holding your answers and
   Tom's (his defaults where he left one open). In a board chat, that
   question's post and canvas entry show only the items left for Tom, your
   answers to the rest under them as already settled. `core/board.py`
   needs no change: the session writes both from the question.

Guidance only: no new verdict, state, check or block. A gate on spent
critique rounds was proposed and is not granted; it is not built. No
governance is added.

## Out of scope

`clarify.md` (it already asks in one batch), `critique.md`, the persona's
"Absorb ambiguity", `core/board.py`, the machine.

## Tests

- `tests/test_kernel.py`, beside the channel dispatch test: the full
  `tasks.dispatch` text for a task in PLAN (reached as
  `tests/test_pipeline.py` `at(..., "plan")` does) carries "user-facing
  behaviour or open product choices", "one numbered batch", "before the
  first plan" and, for the revision, "would change a product or UX choice"
  with "asks"; its channel section says the session "answers what it can
  and raises the rest to Tom" and no longer has "for anything else, ask
  the advisor".
- The chat-session converse test also asserts "raise only the rest" and
  "one `task.answer`", and that a board post shows only the items left
  for Tom.
- Full suite under 5 minutes.

**Done** when a real plan stage on a feature asks its parent session its
clarifying questions before writing the plan, and Tom sees it.
