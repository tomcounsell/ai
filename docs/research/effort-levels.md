# Effort levels on Fable 5.2 and Opus 5.5: three replays, 2026-11-17

Tom, 2026-11-17: "keep Fable and less thinking wins a tie. I prefer putting
more into revision skills (eg content editor) than relying on thinking to
smooth out behaviors." Plan: `docs/plans/effort-levels.md`.

## What runs where, measured

No effort was set anywhere before this change. With no settings file, Claude
Code sends `output_config.effort: "medium"` for both `claude-fable-5-2` and
`claude-opus-5-5` (captured from the outgoing request, 2026-11-17), so every
seat ran at medium.

## How the replays ran

`core run` drives a task through the kernel's own Claude login, which a turn
does not hold, so each replay was one `claude -p --model M [--effort E]` run
through the turn's metered gateway, in a fresh clone at the original's base
commit, with origin a throwaway bare repository and no chat, record or steer
variables. Each prompt said: replay, send and push nothing, ask no questions,
stop at a committed candidate. So a replay is the task's plan and build turn,
with no critique, review or docs round: revision rounds were not measured.
Cost is each run's token usage at `settings.PRICES` (Claude Code's own
figure agreed to the cent).

| Task (base) | Original |
|---|---|
| Bug fix: flaky memory-bench test (0727d0d01) | 09a50c824, one line |
| Feature with a plan: acknowledge a call request at once (6cb7449ee) | 393f18efb..8b2a54839 |
| Written answer, proxy for chat: model review, task 8d98f8bafffd (c1dfe6181) | its delivered note |

A blind judge (a fresh agent given the instruction, the original and the
attempts as A-E, shuffled) scored fidelity / correctness / simplicity, 0-5.
The bug fixes were compared by hand: four of five are byte-identical to each
other and all pass the test.

## Results (one run per arm)

| Arm | Bug: $, s | Feature: $, s, F/C/S | Answer: $, s, F/C/S |
|---|---|---|---|
| Fable low | 0.28, 81 | 0.50, 151, 3/3/4 | 0.95, 171, 4/3/4 |
| Fable medium (today's chat) | 0.24, 70 | 0.52, 151, 3/2/4 | 1.03, 199, 4/4/3 |
| Fable high | 0.45, 192 | 1.48, 482, 3/3/3 | 1.84, 380, 4/4/4 |
| Opus medium (today's tasks) | 0.22, 69 | 0.39, 139, 3/3/4 | 0.94, 139, 4/4/3 |
| Opus low | 0.20, 34 | 0.22, 120, 2/2/5 | 0.37, 92, 3/3/3 |

- Every bug fix passed. Every feature's new test passed with lint clean (one
  test fails on the base commit too). No feature met acceptance, the same way
  at every level: the acknowledgement rides in the turn that reads before
  joining, so it is not "within seconds". The original builder made the same
  first attempt; its critique round caught it. That is a revision-round fix.
- Answer shortfalls: none of the five read the metered spending record (the
  original's main argument). Fable low stated the default effort as high
  (wrong); Opus low missed that a Fable chat needs a compaction window entry.
  All five ran over the ~400 words asked: an editing pass (`yudame:de-slop`)
  covers length and wording.

## Decision, by Tom's rule

Two arms tie when suite and acceptance match and each score is within 1.
- Fable: low, medium and high tie on all three tasks. Low wins. High costs
  1.6-3x for nothing. Low saved little against medium here (-8% to +17%).
- Opus: low ties medium on all three by the rule and costs 9-61% less (44-61%
  on the feature and the answer), but it was the lowest scorer on both judged
  tasks (feature 2/2/5 against 3/3/4, answer 3/3/3 against 4/4/3), with one
  run per arm and no revision rounds measured. Not clear enough to set:
  recommended to Tom, not changed.
- Tasks stay on Opus (Tom, 2026-11-16). For Tom: Fable low matched Opus
  medium's results at 0-28% more cost.

Set: `EFFORT = {"chat": "low"}` in `core/settings.py`, a small saving for a
tie. `frontier` stays unset (medium). Critique, docs and review are keyed by
their own stage names and stay unset (medium): they are the revision rounds
that caught what every level missed.

Undo: delete `"chat": "low"`; chat turns go back to medium from their next
turn. To try tasks at low, add `"frontier": "low"` (new tasks only; a running
task keeps the level it started with).

## Not tested

Revision rounds (no critique, review or docs ran in a replay); chat turns
themselves (a written-answer task stands in); review, critique, docs, the call
worker and routines at any level; xhigh and max; more than one run per arm, so
a one-point difference is noise.

Test spend: $10.97 metered over 15 replays and 3 runs of a first launch with a
broken script ($1.33 of it), about $0.15 of probes, and the judge's turn.
