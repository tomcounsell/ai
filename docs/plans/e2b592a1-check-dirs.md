# A check's directory goes when the check ends

**Why.** On 2026-11-10 the Captain's disk filled three times between 08:20
and 11:30 UTC (kernel log: `DiskFull` in the kernel's own database, and
`Errno 28` in a dozen tasks' steps at once). Free space went from 12 GB to
5.9 GiB in that window.

**Measured on the Captain, 2026-11-10 11:40 UTC.** The kernel's 24 h
reclaim is running and keeping up: no merged, stopped or ended task older
than 24 h still had a workspace. What grew was the folders under `checks/`.
Each test (base and head), review, docs, critique and advice run makes
`checks/<name>/` with a full checkout, and for test and review a set-up
venv: 0.1-0.8 GB each by `du`. Nothing removes one after its run, so a
workspace grows by a checkout and venv for every candidate, every patch
round and every re-run, and keeps them until it is reclaimed a day after
it finishes. The 29 check folders on disk were all made in that window,
8.4 GB by `du`, and one task with two candidates held 5.3 GB of them.
Deleting the folders of five finished tasks gave back 4.1 GB of real disk
(`df`). The figure is lower than `du`'s because venvs are APFS clones. After
that, check folders hold 0.36 GB and the other 59 workspaces about 0.45 GB
each.

**Nothing reads a check's folder after its run.** The transcript is copied
into the store before `turn.ended` (`core/transcripts.py`), the JUnit file
is read inside `checks.suite`, and the docs commit is fetched into the
mirror inside the docs runner. Every run calls `fresh_dir`, which wipes
the folder before making it, so nothing is resumed from one. The
`<name>.<step>.out` logs sit beside the folder in `checks/` and stay.

## What will be built

1. `workspace.fresh_dir` becomes a context manager, `workspace.fresh(path)`.
   It removes the path, makes the session folders, yields the path, and
   on exit removes the path with `rmtree`, whether the run returned,
   failed, raised or was stopped. It leaves `checks/<name>.*.out` and
   anything else beside the folder alone.
2. Its five callers run their whole check inside it:
   - `checks.suite`, for the test runs at base and head;
   - in `core/fresh.py`, the critique, advice, docs and review runners.

   Each does everything that touches the checkout before the block ends:
   reading the verdict, reading JUnit, `fetch_into_mirror` of the docs
   commit, and stopping the services (`check_services` exits inside the
   block). A worktree whose folder is gone is already pruned by
   `checks.worktree` before its next `add`, so a second run at the same
   commit still works.
3. Docs: the `checks/` rows in `docs/workspace.md` and the layout
   docstring in `core/workspace.py` say a check's folder lasts as long as
   its run, and its logs stay.

No reclaim window change. A merged task's workspace still stays for 24 h,
so feedback on it can resume the session. No check, gate, quota or limit is
added.

**Out of scope:** the 24 h window itself; the client monorepo's
`kernel.git` (up to 1.3 GB, from pre-change workspaces and submodule
fetches); tasks that have waited on a question for weeks and keep their
workspace. The report names each of these.

## Tests (real git, no network; `tests/test_kernel.py` and the existing checks and fresh tests)

- `workspace.fresh`: the folder exists inside the block, and is gone after
  a normal exit and after an exception. A sibling `x.suite.out` stays.
- `checks.suite`: after a passing run, after a setup failure, and after a
  stop, `checks/test-head-<sha>` is gone, the `.out` files remain, and the
  payload's `tests` still come from the JUnit file. A second run at the
  same commit checks out again, so the stale worktree is pruned.
- Critique and review runners: the verdict is recorded and the folder is
  gone, including on a no-verdict failure.
- Docs runner: the docs commit is in the mirror after the run, and its
  folder is gone.
- Existing tests that open a check folder after its run are updated to
  read what they need inside it, or from the `.out` file.
- Full suite under 5 minutes.

## Rollout and measure

Merge, then run `core update --wait` on each of the four Macs, over ssh in
each kernel job's launchctl environment; on the Cowboy, through ssh
localhost with `PATH=/opt/homebrew/bin`. On the Captain, after a task's
checks have run, confirm its `checks/` holds only `.out` files, and record
`df` for `/System/Volumes/Data`.

## Report (plain terms, to the chat session)

The report covers:
- what filled the disk and what was freed, with the before and after free
  space;
- which large items are not Valor's: Claude desktop's `vm_bundles`
  (11 GB), `~/.colima` (9.5 GB) and `~/.ollama` (11 GB). For each, it says
  what the item is and whether anything on the Captain uses it, and asks
  before deleting any of them;
- whether the Captain needs more disk, with a rough size and price.
