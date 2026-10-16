# Plan: a turn's TMPDIR is its own and goes when the turn ends

Task 2b1cca5b0610 (backlog:turn-tmp-leak; first carried by 8426bdd16707,
whose branch did not survive the strip).

## Carried by df8e5c6bb79b

2b1cca5b0610 built, tested, reviewed and documented this; the kernel ended
it at the two-rebase cap (removed on main in abb3f7d54). Its head is
`valor/carry-2b1cca5b0610` (cc89d1303) on GitHub. Rebased onto main
2d1864305 as local branch `carry-rebased`: this plan commit is on the task
branch; the build takes `ff94aaaf3` (code and tests) and `80e658d38` (docs,
with the "As built" section below) as they are. The one conflict was
`test_stop_from_another_connection_kills_the_turn`: main loosened its bound
to `elapsed < 20`, the carry added the tmp check; the resolution keeps both.
The build re-runs the full suite and `tests/test_budget.py` on the rebased
head; nothing else is redone.

## The cause, as it stands on main 17bd5a728

The report names `state/work/tmp`, the per-task TMPDIR the old
provisioning set. Strip B (c86f22c39) deleted that setting with the tmp
redirects, so today no provisioning or check sets `harness["tmpdir"]`
(`core/workspace.py:721`, `:1309`). Both harnesses still honor it
(`harnesses/claude_code.py:116`, `harnesses/pi.py:307`), but it is never
given. A turn therefore writes its scratch (test runs, builds, downloads,
Claude Code's own `CLAUDE_CODE_TMPDIR` files) into the operator's shared
`/var/folders/.../T`, mixed with every other turn, task and program on the
Mac. The workspace no longer grows, but the disk does, and no step can give
a turn's space back because nothing says which files are the turn's.
macOS removes items in T only after three days unused; that is a sweep, not
the turn's end.

**What the next turn of the same session reads from its TMPDIR: nothing.**
Claude Code's session state is in `CLAUDE_CONFIG_DIR` (`state/work/claude`),
Pi's in `state/work/pi`, the uv and npm caches in `cache/`, and background
tasks are off (`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`), so nothing that
outlives a turn is kept. Nothing is kept.

## What will be built

1. **A TMPDIR per turn.** `harness["tmpdir"]` now names a root, and each
   turn's TMPDIR is `<root>/<turn_id>`. Both harnesses set `TMPDIR` (and
   Claude Code's `CLAUDE_CODE_TMPDIR`) to that path and return it as a new
   `TurnCommand.tmpdir`. Provisioning gives the working session
   `<task>/tmp`, and `check_harness` gives a fresh session (critique,
   review, docs, advice) `<check_dir>/tmp`, which `fresh_dir` already makes.
2. **Made at the start, removed at the end.** `runs._run_turn` empties the
   root after `build` and before the spawn, and then makes the turn's
   directory. Only one turn of a session runs at a time, so anything in the
   root at that point was left by an earlier turn: a kernel that died, or a
   delete that failed. `runs.run_turn` removes `<root>/<turn_id>` in a
   `finally` once the slot is released. That runs however the turn ended
   (done, failed, stopped, interrupted, kernel failure), so a multi-GB
   delete does not hold another task's turn back. Both steps use the
   existing `workspace.rmtree`, which follows no link and clears locked and
   read-only entries, imported in the function because `workspace`
   imports `runs`. A failed delete is suppressed: the turn's result stands,
   and the next turn's start-time emptying finishes the job.
3. **Briefs from before the strip** carry `tmpdir = state/work/tmp`. Their
   first turn on this code empties it, which reclaims the old leak too.
4. **The turn is told.** `persona/turn.md` gets one line: `$TMPDIR` is
   emptied when the turn ends, so anything a later turn needs goes in the
   workspace. `docs/workspace.md` and the layout docstring
   (`core/workspace.py:4`) name `tmp/`.

Governance: none added. Setting TMPDIR chooses a location and limits
nothing: the turn can still write anywhere, `/tmp` included.

## Out of scope

- Files a turn writes to `/tmp` or another explicit path: no TMPDIR covers
  them.
- The check suite's pytest basetemp (backlog:check-tmp-leak, 4e22396368f4)
  and a finished task's whole workspace (backlog:workspace-reclaim,
  87fd35370d9c).
- About 11,000 empty `valor-test-*` directories the suite's conftest
  leaves in the shared T (seen this turn, about 0 bytes each). This is a
  separate defect, named in the delivery.

## Tests (tests/test_kernel.py, real Postgres, a direct `TurnCommand`)

- A turn whose process writes into `$TMPDIR` a nested tree, a read-only
  file, a locked (`uchg`) file and a link to a file outside the root. After
  the turn ends **done**, and again after it ends **failed**, the turn's
  directory is gone, the root still exists, the link's target outside is
  intact, and a sibling of the root (standing in for `state/work/claude`)
  is untouched.
- The existing stop test gets a tmpdir: after a **stop**, the directory is
  gone.
- A leftover `<root>/<dead-turn>/` from before the turn is gone once the
  turn has started; the turn's process asserts it.
- `claude_code` build with `tmpdir`: `TMPDIR`, `CLAUDE_CODE_TMPDIR` and
  `command.tmpdir` are all `<root>/<turn_id>`. Without `tmpdir`, none is
  set and `command.tmpdir` is None, and the turn's start and end delete
  nothing.

## Loops

This is a defect fix (CLAUDE.md hard rule 9), so it gets 0 critique rounds
and 0 review rounds. The deletion is confined to a directory named by the
kernel's own turn id under a root the kernel chose, and every case above is
tested.
