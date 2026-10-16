---
tracking: none
slug: idle-turn-yields
type: bug
status: planned
critique_rounds: 0
review_rounds: 0
---

# An idle turn gives up the slot; finished work can hand in

A bug fix (hard rule 9): it adds no check, gate, hook, limit or review step.

## Cause

Task 8ab23532a169 ran about 460 turns after its work was done, and task
3624532d04c2 waited over 30 minutes. Two defects caused this:

1. `session.run` (`core/session.py`) runs the next `Continue.` turn inside
   the same call after an `idle` verdict. So the kernel's step
   (`Kernel._step` → `router.step` → the runner) never returns, and
   `schedule` never gets to pick the next task. The task keeps re-taking
   the machine's turn slot, which other tasks only poll for.
2. A task whose work is done and needs no merge has no signal for it.
   `stop.md` counts only in `patch`, so in `plan` the gap-message task
   could only go idle.

## Fix

- **An idle turn ends the step.** After a finished `idle` turn,
  `session.run` returns `{"status": "idle"}`. A turn that wrote
  `advice.md` is the exception: its run goes on, as today, so the advisor
  is asked and the next turn opens with the answer. `Kernel._step` does
  not mark an `idle` step's rows as seen, so `_ready` returns the task
  again on the next wake. `schedule` already sorts foreground before
  background, then oldest latest row first. The idle task's latest row is
  always the newest, so any other ready task of the same class runs
  before its next turn. `router.run` (`python -m core run`, one task, no
  competitor) steps again on `idle`, as it does on `moved`, so the
  command line behaves as before.
- **No idle bound comes back.** Tom removed `idle_turns` on 2026-10-03
  ("Remove it", `docs/plans/m1-4i-idle-turns.md`). A task that never
  signals still runs on, metered, until it is stopped. With this fix it
  runs only when no other task is waiting.
- **`stop.md` hands in from every working stage.** In `clarify`, `plan`
  and `build`, as in `patch`, a `stop.md` with no other stage signal is
  the verdict `ended`, followed by `task.ended` (kind `decided`).
  `machine.VERDICTS` and `TRANSITIONS` gain `ended` for those three
  states, and `db.migrate` rebuilds the verdict constraint from them.
  `STOP_BESIDE` applies in every state: a question, plan, no-question or
  done signal beside `stop.md` wins. Feedback on an ended task already
  reopens it in `patch`.
- **The ended notice tells finished work apart.** A note that opens with
  `Done:` leads with "This is done; nothing needed merging." instead of
  "I stopped this work without merging it." (`notices.ended_text`).
- **Stage files.** `skills/sdlc/clarify.md`, `plan.md` and `build.md`
  each get one paragraph: when the job was an effect (a message, an email)
  and it has been performed with nothing to merge, write `.valor/stop.md`
  starting `Done:`, naming in one line what was sent. The docs table and
  the `idle` bullet in `docs/sdlc-state-machine.md` get updated to match.

## Out of scope

- An idle step in `clarify` is started at once, outside the harness
  count (`AT_ONCE`). It now ends each step, but it still competes for the
  slot by polling. The incident was in `plan`. A fix would be its own
  commit.
- Any limit on idle turns, for the reason above.

## Tests

- `test_session.py`: an idle turn returns `idle` and the next run's prompt
  is `Continue.`. An idle turn with `advice.md` goes on to the advisor in
  the same run. Update the existing three-idle-turns test to step three
  times.
- `test_session.py`: `stop.md` in clarify, plan and build ends the task
  with `task.ended` kind `decided`. A stop beside a question or plan: the
  other signal counts and the turn records `STOP_BESIDE`.
- `test_serve.py`, the incident's shape: two foreground tasks and one
  slot. A idles and B is ready, so B's step starts before A's next turn.
  A and B both idle, so they alternate. A alone, idle, is stepped again
  (no bound).
- `router.run` on an idle step steps again and ends at a delivery.
- `notices.ended_text`: `Done:` gets the finished lead, any other note
  keeps the stopped lead.
- `test_machine.py`: the constraint accepts `ended` for clarify, plan and
  build on a ledger migrated from before the change. Feedback on a task
  ended from `plan` moves it to `patch`.
- Full suite, then `tests/test_budget.py` (test lines stay at or under
  production lines).
