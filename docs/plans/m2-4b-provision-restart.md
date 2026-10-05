---
tracking: none
slug: m2-4b-provision-restart
type: bug
status: built
critique_rounds: 2
review_rounds: 2
governance_grant: none
---

# 2.4b: a kernel killed mid-provision leaves a directory no provisioning gets past

Found on Tom's Mac during the 2.4 rollout. It breaks milestone 2.1's Done
item "Killing it mid-task loses nothing: on restart it resumes from the
ledger" ([valor-rebuild.md](valor-rebuild.md),
[m2-1-resident-kernel.md](m2-1-resident-kernel.md)).

## Incident

The resident kernel (`python -m core serve`) was provisioning the workspace
of a task started by a message: `Kernel._provision` in `core/serve.py` had
called `workspace.reserve` (the task directory under `~/valor-tasks`, with
`ports.json` in it) and then `workspace.provision`, which was fetching the
repository into the shared cache `~/valor-tasks/cache/ai-<hash>.git`. The
kernel was stopped with `launchctl unload` during that fetch.

On restart, recovery printed `kernel recovered: ... swept 0` and did
nothing for the task. The next provisioning wrote

```
workspace.failed {"reason": "[Errno 17] File exists: '/Users/tomcounsell/valor-tasks/<task>'"}
```

Tom steered the task and the retry failed the same way. Removing the task
directory by hand cleared it. The partial bare clone in the cache was also
removed by hand.

## Cause

1. `Kernel._provision` calls `workspace.reserve`, which makes the task
   directory with `mkdir(exist_ok=False)`. A directory left by a
   provisioning that died raises `FileExistsError`, so every later attempt
   for that task fails before it starts, and a steer only repeats it.
   `workspace.provision` itself accepts a directory holding only
   `ports.json`, but the kernel never reaches it. A kill later in
   provisioning (after the clone, during a setup command) leaves a fuller
   directory, which `provision` refuses as `already exists`.
2. `serve` has no SIGTERM handler, so `launchctl unload` ends the process
   with no `finally` run: `provision`'s own cleanup (stop services, remove
   the directory) never runs. The same is true of SIGKILL and of a crash,
   so a handler would not make a kill lose nothing; the restart has to.
   The git call the kernel was waiting on is not stopped either: `_git`
   starts git in its own session and gives it files, not pipes, for its
   output, and serve's provisioning runs under no watch. Checked on this
   Mac: a process run by `launchctl submit` calling `git.trusted(...,
   "fetch", ...)` was removed with `launchctl remove` (SIGTERM, as
   `unload` sends) mid-fetch; the process ended, git ran on in its own
   process group and finished the fetch, writing the ref. So the
   incident's fetch most likely ran to its end after the kernel died; the
   log has no times and the cache was removed by hand, so nothing records
   whether it did. launchd restarts the kernel at once, so a redo can
   start while the dead kernel's fetch, clone, or setup command still
   writes into the shared cache or the task directory.
3. Nothing else clears the directory. `recover` sweeps services only.
   `workspace remove TASK` refuses a task with a row and no
   `workspace.provisioned` row ("has no workspace the kernel provisioned"),
   and the orphan path in `_orphan` takes only a directory with no task
   row. So Tom's own command could not clear it, and its `ports.json`
   keeps its ports reserved (`reserved_ports`) for as long as it exists.

### The cache

A fetch killed mid-transfer heals on the next fetch. Checked on this Mac
with the trusted git: a bare cache whose `git fetch` was SIGKILLed while
`index-pack` was writing kept a `tmp_pack_*` file and no refs; the next
fetch exited 0, wrote the ref, and `git fsck` was clean. Git updates refs
only after the pack is complete, so a killed fetch leaves unreferenced
garbage, never a ref to a missing object. Removing the incident's cache by
hand was not needed. This is also what a reaped fetch (fix 3) leaves.
A dead kernel's clone leaves its `refs/heads/valor-base/<task>` in the
cache; the redo's `update-ref` sets it again and deletes it after its
clone.

## Threat model

- A provisioning runs the project's setup commands in the clone under the
  turn's sandbox profile, so a directory left by a killed provisioning
  holds content a sandboxed program wrote: links to anywhere, files with
  `uchg` or `uappnd` set, ACLs that deny reading. Turns own their
  workspace contents.
- When the kernel clears a task directory it must never follow a link a
  turn or setup command planted, and must not stop at a flag or ACL it
  set. `workspace.rmtree` does both (it clears flags and ACLs without
  reading them and never follows a link); `shutil.rmtree` follows no link
  here but stops at a flag.
- The kernel clears only its own unfinished work: a task directory whose
  task has no `workspace.provisioned` row, read under `provision:<task>`,
  the lock the only writer of that row holds. A directory the ledger says
  is provisioned is never cleared by provisioning.
- Only a provisioning holding `provision:<task>` starts `provision-<task>`
  git (serve and `core start --project`); fix 1 and fix 4 reap only while
  holding that lock, and `kernel_key` keeps a second kernel from running,
  so what the reap finds is a dead holder's.

## Fix

1. `core/serve.py` `Kernel._provision`, under `provision:<task>`: read the
   task's rows and Brief again and return, clearing and writing nothing,
   unless the condition `_ready` scheduled it on still holds: the task is
   not legacy or calibration, is neither STOPPED nor MERGED, has a project
   and no workspace (no `workspace.provisioned` row), and
   `_provision_due(rows)`. A job scheduled before the lock is stale once
   Tom stopped the task and `workspace remove` cleared it, or once another
   kernel's attempt failed with no steer since. When it holds and the
   task's directory exists (`lexists`, so a link there is seen), it is the
   kernel's own unfinished provisioning: `workspace.remove(task_id)` in a
   thread, before the `workspace:ports` lock is taken, so the ports the
   dead attempt recorded are free to choose again. Then reserve and
   provision, with `workspace.provision` run under `git.threaded` (as CLI
   `_provision` runs it) in place of `asyncio.to_thread`: a cancel of the
   job (the kernel closing) interrupts its git and setup commands and waits
   for its cleanup before the `finally` releases `provision:<task>`, so no
   `provision-<task>` process outlives the lock's holder unless that
   holder's process died.
2. `core/git.py` and `core/workspace.py`: provisioning's trusted git calls
   carry the mark `VALOR_TURN=provision-<task>`, the environment mark
   `runs.reap` finds with `ps -E` (as it finds a turn's processes and,
   with the sandbox mark, a setup command's). `git.marked(mark)` sets a
   contextvar that `_git` adds to git's environment, under any explicit
   `extra_env`; `provision` runs `_provision` inside
   `git.marked(f"provision-{task_id}")`, which sets the mark in the thread
   `provision` runs in. Every program git starts inherits the mark.
   Checked on this Mac: the trusted git (Command Line Tools) shows its
   environment to `ps -E`, and `runs.reap("provision-<x>")` stopped a
   marked fetch whose kernel `launchctl remove` had ended. A platform
   binary hides its environment, so a `/bin/sh` or `/bin/sleep` git
   started would escape the mark (the check's own `--upload-pack="sleep
   20; ..."` child did); provisioning's git calls start only git's own
   programs (`upload-pack`, `index-pack`, `pack-objects`) from the same
   install, which show it.
3. `core/workspace.py` `remove`: stop the task's services (as written),
   reap the dead kernel's provisioning git (`runs.reap(f"provision-{task}")`)
   and what its setup commands left running (the mark `setup-<task>-<n>`
   of each `<n>.log` under `setup/`, which `_setup` opens before it starts
   command `n`), then `rmtree(lay.root)` with no `exists()` test in front
   (that test is false for a dangling link, which would then never be
   cleared; `rmtree` returns on a missing path), in place of
   `shutil.rmtree`. Git, a setup command, and the task's Postgres run in
   their own sessions and outlive a killed kernel; stopping them first
   means nothing writes into the tree or the shared cache while it is
   cleared and made again. This is the one removal for a stopped or merged
   task's workspace, an orphan's, and an unfinished provisioning's.
4. `core/__main__.py` `_workspace`, `workspace remove TASK` for a task with
   no `workspace.provisioned` row whose directory exists: once the task is
   stopped (the rule `remove` already states), with `provision:<task>`
   taken by try-lock (busy: refused, "being provisioned now", as
   `_orphan` says), it is removed with `workspace.remove` and a
   `workspace.removed` row with its path and provenance is written.
   `workspace show` for such a task is unchanged.

Nothing is added to `recover`: on restart `_ready` schedules provisioning
for a task with a project and no workspace and no unsteered
`workspace.failed`, and fix 1 redoes it there. A task already carrying the
incident's `workspace.failed` is redone on Tom's next steer, as written.

## Done, as evidence

Against `VALOR_TEST_DB=valor_rebuild_test_24bbuilder`, ports 6570 to 6579,
real Postgres, the trusted git, and real `sandbox-exec`:

- A kernel process SIGKILLed, and in a second case SIGTERMed (what
  `launchctl unload` sends), while a message-started task's setup command
  runs, then started again, provisions the task: one
  `workspace.provisioned`, no `workspace.failed`, the task's first turn
  ends. The setup command the dead kernel started is gone; a link the
  setup command planted to a file outside the work directory was not
  followed (the file is intact); a `uchg` file it set did not stop the
  clear.
- Every git call `workspace.provision` makes carries
  `VALOR_TURN=provision-<task>`, and a trusted git process carrying that
  mark, in its own session, is gone after `workspace.remove`.
- The incident's own state (a task directory holding only `ports.json`,
  an initialized cache with a `tmp_pack_*` file and no refs, and a
  `workspace.failed` reading `[Errno 17] File exists`) is provisioned on
  the next steer.
- `_provision` for a task with a `workspace.provisioned` row, for a
  stopped task, and for a task whose last `workspace.failed` has no steer
  after it leaves its directory as it was and writes no row.
- `workspace remove` of a stopped task with no `workspace.provisioned`
  row removes its directory, writes `workspace.removed`, and its ports
  leave `taken_ports`; for a task that is not stopped it is refused and
  the directory stays.
- The suite and both ruff checks are green.

On Tom's Mac, once merged: the kernel restarts on the merged code.

## Tests

Each fails on the code as it is.

- `tests/test_serve.py::test_a_kill_mid_provision_is_redone_on_restart`,
  parametrized SIGKILL and SIGTERM: `kernel_child` with `VALOR_PROJECTS`
  holding a toy spec whose setup command plants a link to
  `tmp_path/outside/keep`, makes a file and sets `uchg` on it, touches
  `started`, and execs `sleep 600`. The test waits for `started`, signals
  the kernel by its pid, checks the directory is left and the sleeper
  alive, rewrites the spec's setup to `true`, starts a second kernel, and
  waits for `turn.ended`. Asserts: one `workspace.provisioned`, no
  `workspace.failed`, the sleeper gone, `started` gone (the directory was
  made again), `outside/keep` intact. On the code as it is it fails with `File exists`.
- `tests/test_serve.py::test_the_incident_state_is_provisioned_after_a_steer`:
  in-process `Kernel`, the directory and cache as in the incident, the
  `workspace.failed` row, a `message.steered`; one `schedule` provisions.
- `tests/test_serve.py::test_a_stale_provisioning_job_clears_nothing`,
  parametrized over a task with `workspace.provisioned`, a stopped task,
  and a task whose `workspace.failed` has no steer after it:
  `Kernel._provision` called for it; a sentinel file in its directory is
  still there and no row is written.
- `tests/test_workspace.py::test_provisioning_git_is_marked_and_reaped`:
  `git.start` wrapped to record each environment during a real
  `workspace.provision` of a toy spec; every call has
  `VALOR_TURN=provision-<task>`. Then a trusted `git cat-file --batch`
  started in its own session with that mark, its stdin a pipe the test
  holds, is gone after `workspace.remove(task)`.
- `tests/test_workspace.py::test_workspace_remove_takes_an_unfinished_provisioning_once_stopped`:
  a message-started task with a leftover directory holding a link out
  and a `uchg` file; `workspace remove` is refused while the task is
  active, then removes it once stopped, writes `workspace.removed`, and
  the link's target survives.

## Docs

`docs/workspace.md` (the redo, the provisioning mark and its reap,
`workspace remove` for an unfinished provisioning, and that for a stopped
task it is what stops the processes and frees the ports such a
provisioning left), `core/README.md` where it names `workspace remove`,
and one clause in `docs/architecture.md`'s restart paragraph: a killed
provisioning is redone by provisioning, not by recover.

## Left out

- A SIGTERM handler for `serve`: it would not cover SIGKILL or a crash,
  and the restart redo covers all three.
- `tmp_pack_*` files a killed fetch leaves in the cache: unreferenced
  disk, ignored by git.
- A lock file (`refs/heads/<b>.lock`, `packed-refs.lock`) a kill during a
  ref update leaves in the cache: git's error names it. It is not removed
  by the kernel, since another task's fetch of the same cache may hold it.
- `provision`'s own failure path keeps `shutil.rmtree(ignore_errors=True)`;
  whatever it leaves is cleared by the next attempt's fix 1.
- Removing an unfinished provisioning when Tom stops the task: its
  directory stays until `workspace remove`, as a stopped task's workspace
  does, and so do what it left running (its Postgres or Redis, a surviving
  setup command or git) and the ports its `ports.json` holds. `sweep`
  never sees them (the task has a row, and neither its document nor a
  `workspace.provisioned` row names services) and `settle` stops only a
  handle the kernel holds; `workspace remove` stops them. Nothing new is
  added for it.
- A half-made cache (a kill inside `git init --bare`, a window of
  milliseconds no incident has hit): its fetch error names the path, and
  removing it is the fix. Running `init` every time would rewrite the
  config of a cache two tasks fetch at once, under no shared lock, and
  fail one of them.

## Decided by default

- The redo lives in provisioning, not in `recover`: provisioning holds
  `provision:<task>` and the spec, and runs for the restart and for a
  steer alike.
- `workspace remove` of an unfinished provisioning writes
  `workspace.removed`, so the ledger shows where its directory went, as it
  does for a provisioned one.
- `remove` uses `workspace.rmtree` for a provisioned workspace too, since
  a turn may have set a flag there; it follows no link either way.

## Critique rounds

Round 1 (critic-2-4b-r1), lead's decisions:

1. Taken. The dead kernel's git does outlive it (checked, Cause 2);
   provisioning's git calls carry `VALOR_TURN=provision-<task>` and
   `remove` reaps it (fixes 2 and 3). The "Left out" bullet and the
   "stopped partway" claim are gone.
2. Taken. Fix 1 re-reads `_ready`'s whole condition under the lock; the
   test covers a stopped task and an unsteered failure.
3. Taken. The `git init` change, its test, Done line, docs item, and
   default are cut; the window is under "Left out".
4. Taken. `remove` calls `rmtree` with no `exists()` test.
5. Taken. "Left out" and docs/workspace.md say what a stopped task's
   unfinished provisioning keeps running.
6. Taken. docs/architecture.md gets the restart clause.
7. Not a finding; nothing added.

Round 2 (critic-2-4b-r2): sound. Lead's decisions on its three minor
findings:

1. Taken. Serve's `_provision` runs `workspace.provision` under
   `git.threaded` (fix 1).
2. Taken. The Threat model states the invariant the reap's safety rests on.
3. Taken. Fix 2 says the mark is set in the thread `provision` runs in.

## Build record

Built on `m2-4b-provision-restart` (builder-2-4b-build), against
`VALOR_TEST_DB=valor_rebuild_test_24bbuilder`, ports 6570 to 6579.

- Fix 1: `Kernel._provision` re-reads `_ready`'s condition under
  `provision:<task>`, removes a directory found then (`lexists`) with
  `workspace.remove` in a thread before `workspace:ports`, and runs
  `workspace.provision` under `git.threaded`.
- Fix 2: `git.marked(mark)`; `_git` puts `VALOR_TURN=<mark>` in git's
  environment under `extra_env`; `provision` runs `_provision` inside
  `git.marked(f"provision-{task_id}")`.
- Fix 3: `workspace.remove` stops services, reaps `provision-<task>` and
  each `setup-<task>-<n>` of `setup/<n>.log`, then `rmtree(lay.root)`.
- Fix 4: `workspace remove` on a task with no `workspace.provisioned`
  row whose directory exists (`_unfinished` in `core/__main__.py`): the
  stopped-or-merged rule under the `task:<id>` lock, `provision:<task>`
  try-locked for the transaction, `workspace.remove`, `workspace.removed`.
- `tests/kernel_child.py` takes `KERNEL_RUNNERS=judged` (every scripted
  runner, judge included): a task started by message is judged first, so
  the kill test needs the judge to reach its first turn.
- Every test under Tests was written first and failed on the code as it
  was: the kill test with `workspace.failed` (`File exists`), the incident
  test with no `workspace.provisioned`, the stale test with a new
  `workspace.failed` row, the mark test with unmarked git calls, the
  `workspace remove` test with "has no workspace the kernel provisioned".
  All pass now.
- Full suite: 1473 passed, 55 skipped, 2 failed, 62 errors. The failures
  and errors are the mail tests (no dovecot on this Mac) and
  `test_pi` (no node), which fail at base too. `uvx ruff check .` and
  `uvx ruff format --check .` are clean.
- Docs: `docs/workspace.md`, `docs/architecture.md`, `core/README.md`.

## Patch round 1

Review round 1 (review-2-4b-p0) asked for changes; the test check
(test-2-4b) found gaps. Patched by builder-2-4b-p1 against
`VALOR_TEST_DB=valor_rebuild_test_24bbuilder`, ports 6570 to 6579.

Review findings:

1. `Kernel._provision` cleared the leftover with
   `asyncio.to_thread(workspace.remove, ...)`, so a cancel (the kernel
   closing) let the `finally` release `provision:<task>` while the remove
   thread still reaped and cleared. Fixed: `git.threaded(workspace.remove,
   task_id)`, as every other caller of `remove` runs it, so the lock
   outlives the cleanup as fix 1 says.
2. docs/workspace.md said `workspace remove` reaps only under
   `provision:<task>`, which is false for a provisioned task's removal.
   Fixed: the redo and the removal of an unfinished provisioning or an
   orphan reap under that lock; a provisioned task's removal takes none,
   since no provisioning runs for a task once its `workspace.provisioned`
   row exists.

Test gaps covered, in `tests/test_provision_restart_gaps.py`, each checked
to fail with the behavior it covers taken out:

- `workspace remove` of a merged task's unfinished provisioning removes
  the directory and writes one `workspace.removed`.
- `Kernel._provision` for a legacy task and for a calibration task, each
  with a document naming a project, leaves the directory as it was and
  writes no row.
- The redo frees the dead attempt's ports: with one Postgres port to
  choose from, named in the leftover `ports.json`, the redo chooses it
  (provisioning itself stubbed, since the toy project has no services).

Left untested, as the test check judged: a cancel of `git.threaded` on
kernel close mid-provision, and the reap of a real fetch in a real kernel
kill (covered by the unit test's stand-in `cat-file`).
