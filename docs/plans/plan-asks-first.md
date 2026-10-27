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

**Premise, checked.** A plan turn's stage text is `skills/sdlc/plan.md`,
rendered by `core/tasks.py` `stage_text`. The plan stage can already ask:
`core/machine.py` gives PLAN the verdict `asked` (to WAITING, and back to
PLAN on the answer), and a child task's question goes to the session that
started it ("How this task reaches Tom"). What is missing is the word to
use it: `plan.md` only says "A question only Tom can answer goes in
`.valor/question.md`", which a plan turn reads as "rarely". The session's
side, `skills/sdlc/converse.md` item 3, says to tell the chat a task's
question, not to answer what it can first.

## What is built

1. **`plan.md`: ask first.** A short paragraph after "Your prompt holds
   ...": when the work has user-facing behaviour or open product choices
   (what its users see or hear, who does what, what happens when it goes
   wrong), the first plan turn writes no plan. It puts every material
   question in `.valor/question.md` as one numbered batch, each with the
   answer it will assume, then the approach in a few lines. The session
   that started the task (or Tom, when none did) answers what it can and
   raises the rest to Tom. It does not ask when its prompt already holds
   the answers or no answer would change the plan. A revision that would
   change a product or UX choice asks the same way rather than deciding.
   The exit line becomes "A question goes in `.valor/question.md`
   instead".
2. **`converse.md` item 3: answer what you can.** For a task's question,
   answer from the request, the chat and the code what you can with
   `task.answer`, and raise in the chat only what you cannot, with your
   recommendation; pass the chat's answer on as now.

Guidance only: no new verdict, state, check or block. A gate on spent
critique rounds was proposed and is not granted; it is not built. No
governance is added.

## Out of scope

`clarify.md` (it already asks in one batch), `critique.md`, the persona's
"Absorb ambiguity" (the general rule stays as it is), any change to the
machine.

## Tests

- `tests/test_kernel.py`: `stage_text(State.PLAN)` carries the ask-first
  rule (user-facing behaviour or open product choices, one numbered batch
  with the assumed answers, before the first plan) and the revision rule.
- The existing chat-session test (`tests/test_kernel.py`, the converse
  text) also asserts the session answers what it can with `task.answer`
  and raises the rest.
- Full suite under 5 minutes.

**Done** when a real plan stage on a feature asks its parent session its
clarifying questions before writing the plan, and Tom sees it.
