---
tracking: none
slug: c9-feedback-merge
type: plan
status: merged
critique_rounds: 0
review_rounds: 0
---

# A feedback round's merge, and a driver that dies unrecorded

Two bug fixes from the emulator sweep, at stakes 1. Neither adds a check,
gate, hook or guard.

## 1. A feedback round's merge is refused as non-fast-forward

**Evidence.** Task `cd72628273ef` (replay pop-a, clarify arm). Its first
merge landed `8102bb0`: candidate `a45cf9c` plus the docs check's commit.
After Tom's feedback the patch round's candidate was `a170cf5`, its docs
head `5844c98`. The second merge's push to `main` was rejected as
non-fast-forward, and the task went to Tom.

**Cause, a kernel bug.** A merge pushes the docs head: the candidate plus
the docs check's commits. Those commits are kept only in the kernel
mirror. The work branch in `repo/` stays at the candidate, so the patch
round builds on the candidate, not on the merged head. Its new docs head
does not descend from the first merge, and the target branch refuses it.
Every task with a mirror whose docs check commits hits this on Tom's first
feedback after a merge.

**Change.** Before each `patch` turn of a task with a done merge,
`session.run` brings the last merge's head into the work branch
(`workspace.bring_merged`):

- From the mirror, the kernel pushes the head to the bare origin's target
  branch. For a task that merges to its own bare origin the branch is
  there already; for one with a `merge_url` the bare origin gets it.
- Under the turn's own profile, git in `repo/` fetches that branch from
  the bare origin and fast-forwards the work branch to the head. A
  workspace whose config the kernel will not run git under is not touched.
- When this cannot be done (the work branch has moved on, or the tree
  holds changes the head touches), the turn's prompt names the head and how
  to bring it in, with git's reason.

**Test.** `tests/test_feedback_merge.py`: on real git, a docs commit kept in
the mirror and merged, then `bring_merged` and a new commit; the new
commit pushes onto the target as a fast-forward, for both kinds of origin.
The session's step does it in `patch` after a merge and tells the turn when
it cannot.

**Docs.** `docs/sdlc-state-machine.md` (patch), `docs/workspace.md` (the
bare origin's row).

## 2. The replay driver exits nonzero after MERGED with no record

**Evidence.** The first driver run of pop-a's routed arm logged `MERGED` at
21:25:38Z and exited nonzero. The ledger holds no row for its emulator task
between that merge and the hand resume at 21:32:15Z, so no stand-in call
reached the gateway. The routine runner kept the output tail only in its
sweep report, which a paused or crashed sweep never writes, so the
exception is lost.

**Cause, two driver bugs.** The exact exception cannot be recovered.

- `replay.step` catches only a failed `core run`. An exception from the
  status read or from the stand-in (its brief, its review range, a
  `claude -p` that returns no JSON) leaves the driver as a traceback, with
  nothing in the run's log or result.
- `routines/emulator/runner._drive` holds a failed driver's output tail in
  memory only.

**Change.** `step` records a failed status read or stand-in in the run's
log as `{"step": "stand-in failed", "error": ...}` and pauses, as a failed
`core run` does; the next invocation resumes. The runner appends a failed
driver's exit code and output to `results/<run>.driver.log`, beside the
run's result file, at once. A separate file, because a driver that dies
before its first save has no result file, and the driver resumes from a
result file only when it holds a whole run.

**Test.** A status read or stand-in that raises leaves a log row and a
paused result; a failed driver's output is in its `.driver.log` while the
sweep is paused.

**Docs.** `docs/emulator.md`, `docs/routines.md`.

## Questions for Tom

None.

## Patch rounds

None yet.
