# 1.5: decided by default

The choices [m1-5-emulator.md](m1-5-emulator.md) made without asking Tom.

- The package name `tests/emulator/` and the short module names
  (`common`, `workspace`, `stand_in`, `judge`).
- `scripts/demo_workspace.sh` is deleted, not moved.
- The emulator task reuses the calibration task (`calibration:
  "emulator"`) with its own `via` and instruction, rather than a new task
  kind, so `machine.fold` and every refusal are unchanged.
- One emulator task per run, not per item or per gate.
- The driver runs its own gateway on an ephemeral port on a background
  event-loop thread, rather than a long-lived one.
- Stand-in and judge stay `claude -p` calls, not direct API calls.
- The stand-in is the `frontier` seat; the judge is `claude-sonnet-5-5`,
  the id Claude Code 2.1.288's catalog gives the baseline's alias `sonnet`.
  The baseline did not record its CLI version, so that is the nearest
  source.
- `head_sha` is read from the `effect.held` row, not added to the fold.
- The judge's diff is the whole diff: the baseline judge excluded nothing,
  so there is no pathspec to make literal. Size and truncation are recorded.
- The hidden-test tree lives at `<task_dir>/checks/verify-<run>/`.
- `MAX_RUNS` and `MAX_FAILED_RUNS` are removed; a failed run, a
  `NO RUNNER` stage, `IDLE`, `LOCK LOST`, `LEGACY`, an unknown answer and
  an awaited grant each exit for a resume; a delivery that did not pass
  and a refused merge end `to tom`. `ALREADY RUNNING` blocks on the run
  lock, not a sleep loop, and takes nothing for itself.
- The push rule compares the record's push URL with `Layout(task_dir)
  .origin`, both the kernel's; a run whose record names another URL leaves
  its pushes held.
- `TMPDIR` alone does not fix the turn's tools on macOS: `mktemp` and the
  `xcrun` shims read the user temp directory from the system, not
  `TMPDIR`. The fix is the `mktemp` in `bin/` and the trusted git's
  directory on `PATH`, the developer tools' own `git` and `python3`.
- The gate waits for review's runner: 1.4c part one registers it
  unconditionally, under Tom's rule against invented safeguards. A stage
  still manual at gate time (docs, while governance's entry check fails)
  is hand-played, and the gate record names it.
- The stand-in's two feedback rounds are kept; spending them at a held
  merge still ends `held`.
- Gate runs use the `routed` arm and are named `<item>-gate`.
- An item passes if either run meets both bars; both are recorded.
- The gate ends at a held merge, scored and never released, as the
  milestone states.
- Inferred key lines are listed in a sidecar; the keys stay
  byte-identical.
- Hidden verification keeps the baseline's commands, profile (temp
  directories shared), services, and timeout; only its source tree moves.
- The working turn profile denies the temp directories outright rather
  than per-task subdirectories of them; verification keeps the baseline's
  shared temp directories.
- Diffs are read with `git.trusted` in the kernel mirror; the
  verification tree is a `git archive` from it; a hand-recorded verdict
  reads a `blind_checkout` from it.
- The #191 trial's held merge is scored once as information.
- The carried session cost is reported, not changed.
