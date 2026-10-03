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

- A program git starts that calls `setsid` and keeps git's output pipes
  open survives the group kill; after a stop the caller returns at once,
  but the worker thread holds the lock until that program exits.
- A cancelled push whose pack the remote already had settles `failed`
  and the remote may then apply it. A new merge request and one more tap
  re-push and settle `done`.
- `kernel_paths()` does not list `settings.performing_dir`, so a turn is
  kept out of the lock directory only at its default location under the
  key directory. Listing it fixes the sandbox profile.
- Whichever of 1.4d and 2.1 lands second follows the lock-based reconcile
  in `core/bridge.py` and the three tests that read `reconcile_after_s`.
