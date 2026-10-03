# 1.4u records

The merge record of [m1-4u-caps.md](m1-4u-caps.md).

## Merged

Merged 2026-10-04 by the merge train, fast-forward to 2b5c0c06c.

- **Checks.** review-1-4u-p3 `pass` on fcc92db20 (governance boolean: no;
  round 3 only removes things). test-1-4u-p3 `pass` (base 700 passed, head
  750 passed, 11 skipped each, no regression). docs-1-4u-p3 `no_change`.
- **Rebase.** Squashed from m1-4u-caps at fcc92db20 onto 1.4v's merge
  (fe7dcf02b); the per-commit rebase conflicted in most files 1.4b, 1.4s
  and 1.4v had changed. The folds the lead named:
  - `suite_timeout_s` is gone (1.4b's patch round removed it already);
    `setup_timeout_s` is removed with it.
  - Setup's output: `_setup` writes the whole of each command's output to
    `setup/<n>.log` through 1.4b's `setup_command`; `run_setup`, its only
    caller gone, is removed. A failed turn's status line names its stderr
    file (`turn.ended` carries `stderr`, not `stderr_tail`).
  - The breadth wording at `judgement_sites.py` reads "breadth not judged:
    the change is larger than either provider accepts".
- **Other folds.** `diff_hunks` and `diff_paths` read `b.mirror or
  b.workspace`; the router's interruptible `up()` runs 1.4b's `_start`
  (stop, remove check services, start); git keeps 1.4v's sandbox `prefix`
  and 1.4b's `out`; settings keep 1.4b's port spans and `bytes_per_token`
  of 2. Two tests in test_judgement were resized for 2 bytes per token:
  the input too large for both legs is `OPEN_WEIGHT_CONTEXT * 6` bytes (at
  times 10 the test upstream refused the body before either leg saw it).
  The workspace section moved to docs/workspace.md, with 1.4b's check
  services paragraph at its end.
- **Suite.** 931 passed, 13 skipped; `ruff check` clean; `ruff format
  --check` flags only docs/bridges/telegram.md and docs/plans/m2-1-port.md.
- **Backup.** valor_rebuild-20261003T200432Z.dump.
- **Follow-ups.** `core/checks.py` still keeps its own tails of a check's
  setup and suite output (1500 characters and 4000 bytes) in the
  `test.decided` row; whether those also go whole to files is the lead's
  call.
