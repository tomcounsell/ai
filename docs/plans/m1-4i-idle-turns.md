---
tracking: none
slug: m1-4i-idle-turns
type: bug
status: merged
critique_rounds: 0
review_rounds: 1
---

# 1.4i No idle bound

A deletion in milestone 1 of [valor-rebuild.md](valor-rebuild.md). It adds
no check, gate, hook, or review step, and puts nothing in place of what it
removes.

## Goal

Tom's decision on [m1-4u-caps.md](m1-4u-caps.md), "Tom's feedback
(2026-10-03)", question 1, and row 12 of its table: "Remove it". The
setting `idle_turns` (2) ended a working-session run after that many turns
in a row finished with no stage signal, and handed the task to Tom. Its
incident, pso-a's bare replay ending 2 of 3 turns waiting on killed
background tests (rebuild-baseline.md, Caveats), has its cause fixed in the
code: background tasks are disabled in every turn
(`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`, `harnesses/claude_code.py`).

## What happens to such a turn

A turn that finishes with neither of its stage's signals (or with a signal
that does not count: a plan not committed, a candidate on a dirty tree, a
signal meaning nothing in the state) is still recorded as one
`turn.collected` row with the verdict `idle`, and the state machine leaves
the task where it is (`idle` stays put in every working-session state,
docs/sdlc-state-machine.md). `session.run` then runs the next turn in the
same state, resuming the same session with the prompt `Continue.` and
whatever made a signal not count. The run ends only when a turn moves the
task out of the state (a question to `waiting`, a plan, a candidate to
`checks`), a turn fails, the task is stopped, or the run lock is lost.
Spending stays metered, as for every turn.

## Done, as evidence

| Done item | Evidence |
|---|---|
| The setting is gone | `idle_turns` removed from `core/settings.py`; `grep -rn idle_turns core tests` finds nothing |
| The stop is gone | `core/session.py` `run` has no idle count and returns no `idle` status; `core/router.py` names no such runner status |
| The `idle` cause text is gone | the `idle` entry is out of `core/__main__.py`'s status line table |
| A run whose turns end without their stage's signal keeps going | `test_turns_with_no_signal_do_not_end_the_run_and_the_next_prompt_says_continue`: three build turns with no signal, each next prompt `Continue.`, then a delivery; the run ends at `checks` (no runner), not with Tom |
| A turn whose signal did not count is followed by one told why | `test_a_dirty_tree_is_no_candidate_and_the_next_prompt_says_what_is_uncommitted`, `test_a_plan_turn_without_a_committed_plan_is_no_plan`, `test_plan_counts_outside_zero_to_two_are_no_plan`: the next turn's prompt says why and that turn delivers; `test_a_plan_that_commits_a_valor_entry_is_no_plan_and_nothing_is_written_through_it`, `test_a_refused_mirror_fetch_is_no_plan`: the refused turn writes no plan, and the next plan turn's plan is the only `plan.written` |
| Docs say the status quo | docs/sdlc-state-machine.md (`idle` bullet, the control loop), docs/architecture.md (State, Metered spending), m4-1 and m4-3 plans no longer lean on an idle bound |

The scripted harness (`tests/scripted.py`) takes `turns=N` on a steer: the
steering plays for N turns, then the defaults do, so a test can show the
run going on past turns with no signal.

## Threat model

A stage whose turns never signal now runs on, metered, until Tom stops it;
that is Tom's decision. Nothing in the turn's reach changes: no new input,
no new path, no new authority.

## Stakes

0 for critique: a deletion Tom decided, so no critique round (Decided by
default by the lead). 1 for review.

## Files changed

`core/settings.py`, `core/session.py`, `core/router.py`,
`core/__main__.py`, `tests/scripted.py`, `tests/test_session.py`,
`tests/test_pipeline.py`, `tests/test_fresh.py`, `docs/sdlc-state-machine.md`,
`docs/architecture.md`, `docs/plans/m4-1-objective-tree.md`,
`docs/plans/m4-3-routines.md`.

## Left out

- The `idle` verdict itself: it is what a turn with no counted signal is,
  and the machine keeps it as a stay-put verdict.
- Any replacement bound (a turn count, a deadline, a spending stop).
- The records of other tasks that name `IDLE_TURNS` or `idle_turns`
  (m1-1, m1-4u, valor-rebuild.md's milestone text): they record what those
  tasks did.

## Decided by default

- The scripted harness's `turns=N` steer, the smallest change that lets a
  test show the run continuing and still end.
- The six tests that relied on the stop to end their run (three in
  `test_pipeline.py`, three in `test_fresh.py`; found by a suite run with
  a temporary raise after five idle turns, not committed) now make one
  turn's signal not count and assert that the next turn is told why and
  delivers, instead of asserting the run ended `idle`. The refused mirror
  fetch test removes the planted `alternates` file before the second turn.

## Merged

Merged 2026-10-03 onto valor-cori-rebuild at f3b7c4e1a, after one patch
round's checks passed (test pass, 514 collected at base and head; review
pass; docs no_change). Rebased onto ca620a91f; the conflict in
`docs/architecture.md` took this task's text. The lead's suite on the
rebased head: 696 passed, 11 skipped, 4 failed under load (one
`test_fresh` and three `test_workspace` cases); each passed when rerun, so
none is a regression. Ruff clean. Backed up first with
`python -m core backup`. No rollout step beyond the merge: the setting is
gone from code and docs.
