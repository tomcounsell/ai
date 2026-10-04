# 1.4d record: critique, patch, checks, delivery

The record of [m1-4d-credential.md](m1-4d-credential.md).

## Critique round 1 (of 2): revise

Every finding accepted: the credential goes only with a mirror and only
`--project` honours `merge_url` (1); raw bytes as base64, and a failed copy
still records `turn.ended` (2); no size cap (3); the GitHub prefix and the
start-time key check dropped as guards with no incident (4;
`followRedirects` is a transport fix, under Decided by default); the URL's shape checked before it is written (5); the
`start` read given a repository and `remote_head` an exit code (6); the
outline updated and private repositories left out (7); cancellation,
leftovers, a default-branch change, and the cwd encoding handled (8); the
Done evidence is the live push (9); Tom's two items are rollout steps (10).

## Critique round 2 (of 2): revise

Every finding built in:

1. **Hard links.** Transcript files go through 1.4s's `open_turn_file`
   (a regular file with one link); 1.4s is a dependency; hard-link tests
   for the session and a subagent file.
2. **The config root.** The walk starts at the directory holding
   `claude`, which the turn cannot replace, with `O_NOFOLLOW` at each step;
   the kernel chooses the session id (`--session-id`, or the resumed id
   from the fold). Root-symlink test.
3. **Leak tests.** Each transcript document's base64 is decoded before the
   token and the secret are searched for.
4. **Stopped turns.** Copied through the kernel's session id; tested.
5. **`Merge.refuse`.** Built with the Brief's `origin_url` and
   `target_branch`, and refuses a payload that differs.
6. **`start --workspace`.** `resolve_workspace` catches the `GitError` and
   refuses with `WorkspaceRefused`; tested.
7. **The `start` read.** Anonymous, inside `_provision` after `_cache`;
   `targets.check` in `__main__` before `provision`; a missing key never
   refuses `start`.
8. **Loopback.** `merge-target add` accepts only `https`; tests write
   grant rows directly and pass `loopback=True` to the writer.
9. **Small edits.** `read_key` names its command; `docs/data.md` added;
   the outline's transcript text matches; the live test resumes once;
   names are paths under `projects/<dir>`; the `project_name` side effect
   named; the `--workspace` perform-time read runs without the credential.

## Patch round 1

From the review:

1. **Credential refusal** is matched on git's own phrases ("The requested
   URL returned error: 401" or "...: 403", failed authentication), never a
   bare status code, which a SHA can hold.
2. **`merge-target remove`** requires `--by`.
3. **Chunk encoding** (base64 and the JSON dump) runs in the worker thread.

From the test check and the lead:

4. **A cancelled transcript copy leaks nothing.** The thread function that
   opens the files owns them: it hands them over only while the copy still
   awaits, and closes them in its own `finally` otherwise; a copy cancelled
   after the hand-over closes them itself. Test: cancel mid-read, no
   descriptor stays open.
5. **Facts, not ages.** `git_timeout_s` (120 s) and both factors of two
   came from proposed patch 2 of 1.2 (`be1388e3e`), accepted under the
   delegated decision of 2026-10-02 (`f9e629363`); the 120 and the two
   cite no protocol fact or document, and 1.4d's header sweep copied the
   two. Reconcile now reads the remote only once the effect's lock file is
   free (the push process reaped), and settles at once; `reconcile_after_s`
   and its settings check are gone. The header sweep removes a file whose
   writer's PID (in the name) names no process.
6. **No git time limit.** `git_timeout_s`, `git.deadline`, and the mirror
   fetch's limit are gone with their tests: no protocol fact bounds a git
   call. Git runs until it exits; an interrupt, or a cancelled
   `git.threaded` caller (push_branch, merge, `core/__main__.py`'s
   provisioning and removal), kills its process group, as 1.4c does.
7. **Tests write no lock file under the key directory.** `performing_dir`
   is a setting (`VALOR_PERFORMING_DIR`, default the key directory's
   `performing`); `tests/conftest.py` sets it to a temporary directory
   before the settings are built, for every test and process it starts.
8. **A stop reaches the mirror fetch.** `session.record` reads its verdict
   (and fetches into the mirror) in `git.threaded`, and so does a docs head
   in `verdicts`; the loop runs meanwhile and a stop kills the fetch.
9. **Base.** On `e4b30b78c` (plans only), with no conflict and no timeout.

## Checks after patch round 1, at fc09d585d (review round 2 of 2)

- Test: `pass`. The suite passes with no failures; nothing is written
  under `~/.config/valor-kernel`. Probes: a cancelled transcript copy
  leaks no descriptor; a cancelled push kills git's process group and
  frees the lock; with the kernel killed mid-push, git runs on and the
  effect settles once git exits; the header sweep keeps live PIDs only.
- Review: `pass`; governance boolean no (each refusal limits the
  credential or reads turn-owned state, from m1-4-checks.md and the threat
  model); no invented caps. 590 passed, 9 skipped.
- Docs: `updated`, 52f86c531 on `m1-4d-docs2`.

## Delivery: merge held for Tom's tap

The candidate is `m1-4d-docs2`. Notes from the checks, not blocking:

- A program git starts that calls `setsid` and keeps git's output open no
  longer holds the call: git's output goes to files the kernel holds (patch
  round 3).
- A cancelled push whose pack the remote already had settles `failed`
  and the remote may then apply it. A new merge request and one more tap
  re-push and settle `done`.
- `kernel_paths()` lists `settings.performing_dir`, so a turn is kept out of
  the lock directory wherever it is set (patch round 3).
- Whichever of 1.4d and 2.1 lands second follows the lock-based reconcile
  in `core/bridge.py` and the three tests that read `reconcile_after_s`.

## Patch round 2: onto 460943b8a

1.4s, 1.4v, 1.4u, and 3b landed after the checks; the build is squashed
onto the rebuild branch at 460943b8a as one commit. The lead's calls:

1. **One interrupt mechanism.** `git.threaded` runs the thread under
   1.4u's `git.interruptible`; a cancelled caller calls `git.interrupt`,
   which interrupts the watch (TERM, then KILL to every group) and waits
   for the thread. `git.popen` and the started-process set are gone.
   `core/__main__.py`'s `_provision` turns SIGTERM and SIGHUP into a cancel
   of `git.threaded(workspace.provision, ...)`, `start --project` uses it,
   removal and the docs runner's mirror fetch run in `git.threaded`, the
   router's service start interrupts through `git.interrupt`, and
   `workspace.bounded` starts through `git.start`.
2. **No git time limit.** `git_timeout_s`, `git.deadline`,
   `git.remaining`, `reconcile_after_s`, and `bounded`'s `timeout` are
   gone, as decided by default in the plan; this drops 1.4u's rule that a
   git call outside a watch keeps a time limit.
   `test_provisioning_runs_past_the_git_timeout` became
   `test_no_provisioning_git_call_carries_a_time_limit`, and the mirror
   fetch deadline test went.

Folds: one copy of the turn file helpers (the merged `open_turn_dir`,
`open_turn_file`, `DIR_FLAGS`), with `core/transcripts.py` reading a
missing entry as `(None, None)` and its tests taking the merged wording;
no idle value in `session.py`; `runs.py` keeps `spending.turn_spent`, the
dispatch's retire on failure, and adds `offered`; the command list has
`openai-key`, `github-key`, and `merge-target`; `copy_keys` keeps
`sources`; `remote_head`'s stderr and the transcript failure text are
whole, per 1.4u. The caller of a cancelled `git.threaded` now waits for
the thread; patch round 3 makes that wait end when git exits.

## Patch round 3: output in files, the stop's log, the lock directory

Rebased onto 1.4w (86523576c); one import conflict in tests/test_kernel.py.

The test and review rounds of patch round 2 found that a program git
started which left git's process group with `setsid` and kept git's pipes
open held the call, and a stopped caller, until it exited. The lead's
calls:

1. **Files, not pipes.** `git._git`'s stdout and stderr, `bounded`'s
   stderr, and `_service_run`'s output go to unlinked files the kernel
   holds (`git.output_file`), read by position after the process is
   waited on (`git.read_output`). Turn and setup output keep
   1.4w's pipes. Tests: a fake
   git that starts a `setsid` program holding its stdout and stderr; the
   call returns git's output when git exits, and a cancelled
   `git.threaded` caller returns with git's group killed and the program
   still running. A `bounded` command does the same. On the round 2 code
   the cancel test waited until the program was killed.
2. **No ERROR on a stop.** `git.threaded` waits with `asyncio.wait`, not
   `asyncio.shield`; the cancel test asserts no ERROR record. With
   `shield` it logs "Interrupted exception in shielded future".
3. `kernel_paths()` lists `settings.performing_dir`.
4. The plan's decided-by-default names the case the effect lock and
   `reconcile` both miss and its remedy, and Rollout step 3 is the build
   session's, with `--note` and `--via` citing Tom's feedback of
   2026-10-03.

Suite: 1093 passed, 21 skipped. `ruff check` and `ruff format --check` clean.

## Patch round 4: output files in a kernel path

Review round 3 found that a turn could open and truncate the output
files: `TemporaryFile` on macOS makes a named file in `$TMPDIR` and then
unlinks it, and builder, setup, and service profiles do not deny the temp
directory. A probe under the builder profile blanked 708 of 8,872 kernel
`config --list` reads. The lead's calls:

1. **Output files in a kernel path.** `git.output_file` makes its files in
   `git.output_dir`, `output/` under `settings.performing_dir`, made by
   the kernel with mode 0700. A task profile now denies each kernel path
   both as written and with symlinks resolved: the suite's lock directory
   is under `/var/folders`, which the sandbox matches as `/private/var`,
   so the deny as written covered nothing there. Test: under the builder
   turn profile a file in that directory cannot be read or appended and
   the directory cannot be listed, and 5 MiB from the kernel's git comes
   back whole from files made there.
2. `bounded`'s docstring and the `mirror_fetch_max_bytes` comment say the
   file-size limit covers its stderr file too. No cap added.
3. `performing.in_thread`'s branch for a call cancelled before its thread
   starts stays: `git.threaded` never cancels it on a stop, but the event
   loop's shutdown does, and without the branch the descriptor, and the
   effect's lock, would stay open. Its docstring says so.

Suite: 1098 passed, 21 skipped. `ruff check` and `ruff format --check` clean.

## Merged

Round 4 passed all three checks: test pass, review pass (governance
boolean no), docs updated at 6219a7e38. The lead's suite on 6219a7e38:
1098 passed, 21 skipped; `ruff check` and `ruff format --check` clean.
Backup `valor_rebuild-20261004T002302Z.dump`. `valor-cori-rebuild`
fast-forwarded to 6219a7e38.

Rollout, by the build session under Tom's feedback of 2026-10-03:

1. `uv sync`; no migration.
2. `python -m core github-key`: `GITHUB_PUSH_TOKEN: written`.
3. `merge-target add` granted `valor-cori-rebuild` and `valor/push-check`
   of `https://github.com/tomcounsell/ai.git`, each with `--note` and
   `--via` citing Tom's feedback.
4. The live push test passed (no model spend): `valor/push-check` on
   GitHub is at 905e53e7ac71. `merge-target remove ... valor/push-check
   --by valor --note "live push done"` revoked that grant. The branch
   stays on GitHub.
5. Tom's ruleset on `main` waits for him; nothing depends on it.

Follow-up, outside 1.4d's diff, from review round 4: `git.dirty()` makes
its temporary index in `git.output_dir()`, which no task profile reaches,
as the kernel's output files are (task 1.4x, `m1-4x-temp-index.md`).
