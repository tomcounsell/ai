---
tracking: none
slug: fc598a81-message-seat
type: plan
status: planned
critique_rounds: 1
review_rounds: 1
---

# A task started from a message runs on the frontier seat

Kernel repair, a bug fix (task fc598a81afd9).

**Problem.** `intake._start` (`core/intake.py`) builds the Brief for a task
Tom starts from a chat message with `model=resolve_model("light")`, the
Haiku seat (`claude-haiku-4-5`). Every SDLC stage is a frontier turn
(`docs/sdlc-state-machine.md`, the table of who runs each stage), and the
light seat cannot hold them: on 2026-10-07 task 814d4aa2403e wrote its plan
under `.valor/`, which the workspace excludes from git, so the plan was
refused ("plan.json's path is not committed at HEAD"); it then built during
the plan stage and looped on idle turns until it was stopped.

**Stakes.** A message-started task is the main way Tom gives work; on the
light seat it fails the SDLC and burns turns, and the fix moves those
tasks onto Opus-priced turns.

## What is built

1. `core/intake.py`, `_start`: the Brief takes the frontier seat's harness
   and model, `harness_name, model = resolve_seat("frontier")`, the same
   resolution `python -m core start` does for `--model frontier`
   (`core/__main__.py`), so a later change to the frontier seat's harness
   carries through. The import changes from `resolve_model` to
   `resolve_seat`. Nothing else in `_start` changes: ceiling, project,
   provisioning, and binding stay as they are.
2. Docs that name the seat:
   - `docs/bridges/telegram.md`, the paragraph "A task started from a
     message takes its effect ceiling from settings": add that it runs on
     the frontier seat.
   - `docs/tech-stack.md`, Model seats: the Frontier bullet says it is also
     the seat a task started from a message runs on; add the Light bullet
     the list lacks (Haiku, the `python -m core start` default, not used
     for a message-started task, with the 2026-10-07 incident as the
     reason).
   - `core/README.md`, the `serve` entry's sentence "A message that starts
     a task provisions its workspace": add the seat.
   - `docs/plans/m2-1-resident-kernel.md`, the decision "It runs at ceiling
     `propose` on `resolve_model("light")`": amend in place to the frontier
     seat, with a dated note naming this task and the incident, so the
     record says why it changed.

## Tests

In `tests/test_intake.py`, `test_start_rule` already starts tasks four
ways and reads each Brief back through `tasks.brief`. Extend it to read
`model` and `harness_name` too and assert each equals
`resolve_seat("frontier")`, for every start it makes:

- a chat listed by a project spec (`project`);
- a chat no spec lists, with no `valor` spec (`elsewhere`);
- a reply to a message the kernel does not know (`unknown_reply`), which
  starts rather than steers;
- an email from Tom's verified address (`mailed`).

The cases beyond the obvious one cover each path into `_start`, so no route
to a new task is left on the old seat. Asserting against
`resolve_seat("frontier")` rather than a literal id keeps the test true
when the seat's pin moves; a separate assertion that it is not
`resolve_model("light")` guards the regression itself while the two seats
differ.

Suites: `tests/test_intake.py` in full (macOS-marked tests included, run on
this Mac), then the whole suite, with any failure compared against the
target branch at the same commit to separate pre-existing ones.

## Out of scope

- `python -m core start --model` keeps its default `light`
  (`core/__main__.py`). Tom starts those by hand and can pass a seat; the
  same failure is possible there, so the delivery names it as a product
  note rather than changing it here.
- The plan-stage behavior the incident showed (a plan under `.valor/`,
  building during plan, idle-turn looping) is not guarded against: the fix
  is the seat, and the Brief allows no new check or gate.
- `SEATS` and prices are unchanged.

## Tech debt

None added.

## Loops

One critique and one review: the change is to the kernel, but it is one
line in a single function, reversible by a revert, touches no stored data,
and is covered by an existing test it extends.
