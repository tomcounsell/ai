---
tracking: none
slug: m1-4x-temp-index
type: bugfix
status: passed
critique_rounds: 0
review_rounds: 1
---

# 1.4x: the fresh index of `git.dirty` in the kernel's output directory

Found by the fourth review of [m1-4d-credential.md](m1-4d-credential.md).

## Defect

`git.dirty()` read HEAD into a fresh index in a temporary directory
(prefix `valor-index-`) in `$TMPDIR`. No task profile denies the temp
directory, so a turn, setup command, or service could open, truncate, or
replace that index while the kernel's `read-tree` and `status` used it.

## Fix

The temporary directory is made in `git.output_dir()` (`output/` under
`settings.performing_dir`, mode 0700), which every task profile denies as
written and resolved, the same place the kernel's output files are made.

Other kernel temp files checked (`tempfile`, `mkstemp`,
`NamedTemporaryFile`, `TemporaryDirectory`, `mkdtemp`, `gettempdir`,
`TMPDIR`, `/tmp` in `core/` and `tools/`):

- `core/git.py` `output_file`: already in `output_dir()`.
- `core/backup.py` `start_cluster`: `mkdtemp` in `settings.pg_scratch`,
  inside the kernel key directory, which no profile reaches.
- `tools/look`: a turn's own tool, run inside the turn's sandbox with the
  turn's `TMPDIR`; not a kernel file.
- `core/workspace.py`, `core/fresh.py`: set a session's own `TMPDIR`, or
  deny the temp directories in the fresh profile; no file is made.
- `core/settings.py` `pghost` default `/tmp`: a socket directory setting,
  no file is made.

Nothing else moved.

## Test

`tests/test_workspace.py::test_the_index_dirty_reads_is_made_in_the_output_directory`
watches each git call `dirty` makes with `GIT_INDEX_FILE`: the index's
directory exists under `git.output_dir()` while git runs and is gone after.
It fails without the fix.

## Threat model

A turn owns everything in the temp directory and can swap the kernel's
index mid-call to make a dirty tree read clean. The index now lives where
no task profile can open, list, or write.

## Build record

Built on 9e5090663. Docs: `docs/data.md`, `docs/harnesses.md`, and
`core/README.md` name `output/` as where the fresh index is made.
Full suite: 1099 passed, 21 skipped. `ruff check` and `ruff format --check`
clean.
