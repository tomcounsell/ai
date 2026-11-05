# A call's front model starts in a directory the kernel never removes

A bug fix (CLAUDE.md rule 9): a commit with a test. This page only fixes
what the builder holds the work to.

## What was wrong

A call asked for on the Captain (2026-11-05 05:15 UTC, session
c9ab8cc8f378) ended at once with `FileNotFoundError(2, 'No such file or
directory')`. `core/__main__.py:_call` makes the front model's Claude Code
home at `<work_dir>/<session>/front`, inside the call session's own task
directory, then loads Kokoro and Parakeet for a few seconds. Meanwhile the
kernel provisions that session's workspace (`serve._provision`), which
treats a task directory it finds as a provisioning that died and removes it
(`workspace.remove`), `front` with it. `Front.start` then runs `claude`
with that deleted directory as its cwd: `FileNotFoundError`. On the Captain
now, `~/valor-tasks/c9ab8cc8f378/` holds the provisioned workspace and no
`front`. The live Meet test passed 3/3 because it gives the front a
`tmp_path` home and provisions nothing. The log had no path because
`repr()` of an `OSError` drops its filename and `Call.run` logged only
the repr.

## What will be built

1. **The front owns its home.** `call.Front` makes its Claude Code home
   (cwd, `HOME`, `CLAUDE_CONFIG_DIR`) as a temporary directory when it
   starts and removes it when it closes; `Front.claude` no longer takes a
   `home`. `_call` stops making `<work_dir>/<session>/front`, so the call
   process writes nothing into the session's task directory. Nothing else
   reads that directory: its only users are `_call` and the live tests,
   which drop their own `home` setup.
2. **A call's failure logs where it failed.** `Call.run`'s handler writes
   the traceback to the call's log (`meet.log`) alongside the line it
   writes now, so a missing file shows its path and caller. That is
   logging, not a new check.

## Tests

- **The incident:** a session's task directory exists under a
  `work_dir` set to `tmp_path`. A `Front` is made for that session with a
  stand-in command (`/bin/cat`). The session's task directory is then
  removed as `serve._provision` removes it. `start()` still succeeds and
  the stand-in runs in a directory outside the task directory. `close()`
  removes that directory. This fails on main with `FileNotFoundError`.
- **Two fronts at once** (two calls' fronts on one Mac, as the evals run
  them) get separate homes.
- **The log:** a `Call` whose page raises `FileNotFoundError` with a
  filename ends the call as now, and its log line carries the filename and
  a traceback.
- Existing: `tests/test_call.py` and the front-model evals in
  `tests/test_live_meet.py` updated for the dropped `home` argument. Full
  suite under 5 minutes.

## Roll-out and the live check

Merge, then update the four Macs over ssh with the kernel job's launchctl
environment, as in earlier rollouts; each call is a fresh `python -m core
call`, so new calls take the fix once the checkout is updated. Then, on the
Captain, run the kernel's own call path end to end without Tom: a call
session in a test database driven through `core.__main__._call` (not a
hand-built `Call`), run as a `launchctl bootstrap gui/$(id -u)` job with
the kernel job's environment and cwd, never Chrome over plain ssh (it signs
the profile out: docs/rollout.md). A second device joins, speaks, and hears
Valor answer. If `_call` cannot be driven in the test database as it
stands, the live test gains a case that runs it. Nothing is posted to any
chat.

## Out of scope

Anything else about calls, such as the earlier no-audio run on the Cowboy.
Asking Tom for a call is the chat session's job once this check passes.

## Stakes

A bug that stops every kernel-started call on a Mac whose call session
gets a workspace. The fix moves one temporary directory and is undone by
reverting one commit, so no critique or review rounds.
