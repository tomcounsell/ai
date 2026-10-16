# Plan: a turn's TMPDIR is its own and goes when the turn ends

backlog:turn-tmp-leak. Built, tested, reviewed and documented by
2b1cca5b0610, which the kernel ended at the two-rebase cap (removed on main
in abb3f7d54). Carried by df8e5c6bb79b.

## The carry

The finished head is `valor/carry-2b1cca5b0610` (cc89d1303) on GitHub
(`tomcounsell/ai`), not in this task's origin mirror. It is rebased onto main
2d1864305 as the local branch `carry-rebased` in this repo: the plan commit,
then `ff94aaaf3` (code and tests), then `80e658d38` (docs). The one conflict
was in `test_stop_from_another_connection_kills_the_turn`: main loosened the
bound to `elapsed < 20` and the carry added `not (tmp_path / "tmp").exists()`.
The resolution keeps both. If `carry-rebased` is missing, the build redoes
this rebase from cc89d1303 with the same resolution.

## The cause, on main 2d1864305

Strip B (c86f22c39) deleted the per-task `state/work/tmp` setting. No
provisioning (`core/workspace.py:721-727`) or fresh session (`check_harness`,
`:1314-1319`) sets `harness["tmpdir"]`, though both harnesses honor it
(`harnesses/claude_code.py:116`, `harnesses/pi.py:307`). So every turn's
scratch goes into the shared `/var/folders/.../T`, and nothing can tell
which files are a turn's, so nothing gives them back.

**What the next turn of the same session reads from its TMPDIR: nothing.**
Session state is in `state/work/claude` and `state/work/pi`, the caches are
in `cache/`, and background tasks are off. Nothing is kept.

## What is built (as in ff94aaaf3)

1. **One TMPDIR per session.** A working session's TMPDIR is `<task>/tmp`.
   `_turn_for` gives it to briefs that carry no `tmpdir`, so live tasks need
   no migration. A fresh session's is `<check_dir>/tmp`. Both harnesses set
   `TMPDIR` (and Claude Code's `CLAUDE_CODE_TMPDIR`) and return it as
   `TurnCommand.tmpdir`. It is the root itself, not `<root>/<turn_id>`:
   one turn of a session runs at a time, and the short path keeps sockets
   under macOS's 104-byte limit.
2. **Emptied at the start, removed at the end.** `runs._run_turn` removes
   and remakes the root before the spawn, which clears anything a dead
   kernel or a failed delete left. `runs.run_turn` removes it in a
   `finally` once the slot is released, however the turn ended. Both steps
   use `workspace.rmtree`, which follows no link and clears locked and
   read-only entries. A failed delete is suppressed and the next turn's
   start finishes the job. Pre-strip briefs (`state/work/tmp`) are emptied
   on their first turn.
3. **The review's pgpass leaves tmp/.** `check_services` wrote it to
   `<check_dir>/tmp/pgpass` (`core/workspace.py:1612`), which the review
   turn's start would delete. The carry writes it to `<check_dir>/pgpass`,
   and the docstring at `:1596` says so.
4. **The turn is told.** One line in `persona/turn.md` says `$TMPDIR` is
   emptied when the turn ends. `docs/workspace.md`, `docs/harnesses.md` and
   the layout docstring name `tmp/`.

Governance: none added. Setting TMPDIR picks a location and limits nothing.

## Tests (tests/test_kernel.py, real Postgres)

Carried:
- done and failed turns: a nested tree, a read-only file, a `uchg` file and
  a link out of the root. Afterwards the root is gone, the link's target
  and a sibling directory are intact, and a leftover from before the turn
  was gone at its start.
- stop: the root is gone.
- harness build, with and without `tmpdir`.

Added by this build (the critique's missed case): a fresh session with
services. The test runs the real `check_services` for a Postgres-only
project, then `check_harness(..., services=True)` with a `tmpdir`, then a
turn whose process checks that `$PGPASSFILE` exists after the start-time
emptying. Afterwards the passfile still exists and the TMPDIR is gone. The
full suite and `tests/test_budget.py` run on the rebased head.

## As built

- TMPDIR is the root itself, not `<root>/<turn_id>`: one turn of a session
  runs at a time, and the shorter path keeps socket paths under macOS's
  104-byte limit (a check's `tmp/` is about 76 characters, the length it had
  before the strip). The turn's start removes and remakes the root; its end
  removes it. `run_turn` collects the path through a list `_run_turn`
  appends to, so the end-of-turn removal runs on the cut-short paths too.
- Briefs provisioned since the strip carry no `tmpdir`; `_turn_for` gives a
  workspace task `<task>/tmp`, so live tasks are covered with no migration.
- A review session's `pgpass` moved from `<check_dir>/tmp/` to
  `<check_dir>/`, since the turn's start would empty it.

## Out of scope

Files written to `/tmp` directly; the check suite's pytest basetemp
(4e22396368f4); a finished task's workspace (87fd35370d9c); the roughly
11,000 empty `valor-test-*` directories the conftest leaves in T, named in
the delivery.

## Loops

Defect fix (CLAUDE.md rule 9): 0 critique and 0 review rounds.
