---
tracking: none
slug: m2-4b-provision-restart
type: bug
status: planned
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
hand was not needed.

One window does not heal. `_cache` runs `git init --bare` only when the
cache path does not exist. A kill inside `git init` leaves a directory
that exists and is not a repository; every later fetch then fails with
`not a git repository`, for every task of that repository. Checked: an
empty directory, one holding only `objects/` and `refs/`, and one missing
`HEAD` and `config` all fail the fetch, and each fetches after a second
`git init --bare`.

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

## Fix

1. `core/serve.py` `Kernel._provision`, under `provision:<task>`: read the
   task's Brief. If it has a workspace (a `workspace.provisioned` row),
   return; nothing is cleared or written. Otherwise, if the task's
   directory exists (`lexists`, so a link there is seen), it is the
   kernel's own unfinished provisioning: `workspace.remove(task_id)` in a
   thread, before the `workspace:ports` lock is taken, so the ports the
   dead attempt recorded are free to choose again. Then reserve and
   provision as written.
2. `core/workspace.py` `remove`: stop the task's services (as written),
   reap what its setup commands left running (the mark
   `setup-<task>-<n>` of each `<n>.log` under `setup/`, which `_setup`
   opens before it starts command `n`), then `rmtree(lay.root)` instead of
   `shutil.rmtree`. A setup command and the task's Postgres are started in
   their own sessions and outlive a killed kernel; stopping them first
   means nothing writes into the tree while it is cleared. This is the one
   removal for a stopped or merged task's workspace, an orphan's, and an
   unfinished provisioning's.
3. `core/workspace.py` `_cache`: run `git init -q --bare` every time
   (it is idempotent on a whole repository and makes a half-made one
   whole), after making the cache's parent.
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
- The incident's own state (a task directory holding only `ports.json`,
  an initialized cache with a `tmp_pack_*` file and no refs, and a
  `workspace.failed` reading `[Errno 17] File exists`) is provisioned on
  the next steer.
- `_provision` for a task with a `workspace.provisioned` row leaves its
  directory as it was and writes no row.
- A cache path left as a directory that is not a repository is fetched
  into.
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
- `tests/test_serve.py::test_provisioning_never_clears_a_provisioned_workspace`:
  `Kernel._provision` called for a task whose ledger has
  `workspace.provisioned`; a sentinel file in its directory is still
  there and no row is written.
- `tests/test_workspace.py::test_a_half_made_cache_is_made_whole`,
  parametrized over the three states checked above: `_cache` fetches.
- `tests/test_workspace.py::test_workspace_remove_takes_an_unfinished_provisioning_once_stopped`:
  a message-started task with a leftover directory holding a link out
  and a `uchg` file; `workspace remove` is refused while the task is
  active, then removes it once stopped, writes `workspace.removed`, and
  the link's target survives.

## Docs

`docs/workspace.md` (the redo, the cache init, `workspace remove` for an
unfinished provisioning) and `core/README.md` where it names `workspace
remove`.

## Left out

- The trusted git calls of `serve`'s provisioning run in their own
  sessions with no mark, outside `git.interruptible`, unlike `start
  --project`'s. If one outlives a killed kernel and writes into the
  directory while it is cleared or cloned into, that attempt fails as
  `workspace.failed` and the next steer redoes it. No incident shows one
  outliving the kernel (the incident's fetch stopped partway).
- A SIGTERM handler for `serve`: it would not cover SIGKILL or a crash,
  and the restart redo covers all three.
- `tmp_pack_*` files a killed fetch leaves in the cache: unreferenced
  disk, ignored by git.
- A lock file (`refs/heads/<b>.lock`, `packed-refs.lock`) a kill during a
  ref update leaves in the cache: git's error names it. It is not removed
  by the kernel, since another task's fetch of the same cache may hold it.
- `provision`'s own failure path keeps `shutil.rmtree(ignore_errors=True)`;
  whatever it leaves is cleared by the next attempt's fix 1.
- Removing an unfinished provisioning's directory when Tom stops the task:
  it stays until `workspace remove`, as a stopped task's workspace does.

## Decided by default

- The redo lives in provisioning, not in `recover`: provisioning holds
  `provision:<task>` and the spec, and runs for the restart and for a
  steer alike.
- Fix 3 is in this task although no incident hit the `git init` window:
  a kill there leaves every later task of that repository failing, the
  same loss the 2.1 Done item rules out, and the fix is the existing call
  made unconditional.
- `workspace remove` of an unfinished provisioning writes
  `workspace.removed`, so the ledger shows where its directory went, as it
  does for a provisioned one.
- `remove` uses `workspace.rmtree` for a provisioned workspace too, since
  a turn may have set a flag there; it follows no link either way.
