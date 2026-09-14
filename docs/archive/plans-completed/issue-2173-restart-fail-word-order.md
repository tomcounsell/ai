# Plan: Align RESTART FAILED worker word order (#2173)

## Root Cause
`tests/unit/test_remote_update_shell.py::test_worker_bootstrap_and_kickstart_both_fail_reports_failure`
asserts the substring `RESTART FAILED: worker bootstrap/kickstart failed`, but the loaded-branch
failure line in `scripts/remote-update.sh` emitted `worker kickstart/bootstrap failed` — the two
words transposed. The not-loaded branch (line 354) already emits `bootstrap/kickstart`, so the two
failure lines in the same script were also inconsistent with each other. Order-independent
semantically; the mismatch is a stale/inconsistent string, not a behavior regression.

## Approach
Standardize on the canonical order `bootstrap/kickstart` (already used by the not-loaded branch and
the test). Change the single loaded-branch `echo` line from `kickstart/bootstrap` to
`bootstrap/kickstart`. Both script sites and the test assertion now agree.

## Success Criteria
- The loaded-branch failure line in `scripts/remote-update.sh` reads `worker bootstrap/kickstart failed`.
- `test_worker_bootstrap_and_kickstart_both_fail_reports_failure` passes.
- Both worker RESTART FAILED lines use the same canonical word order.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.

## Update System
Changes `scripts/remote-update.sh`, which the update system runs. Only a log string is altered; no
new dependencies, config files, or migration steps. Behavior is unchanged.

## Agent Integration
No agent integration required — this is a string alignment in a deploy shell script with no CLI or
bridge surface.

## Test Impact
- [x] `tests/unit/test_remote_update_shell.py::test_worker_bootstrap_and_kickstart_both_fail_reports_failure`
  — the word-order fix satisfies the RESTART FAILED assertion.
- [x] `tests/unit/test_remote_update_shell.py` (harness) — UPDATE: add a `pgrep` stub so the #2141
  liveness cross-check no longer leaks the host's real `python -m worker` process into the sandbox,
  which was forcing the loaded branch and making the not-loaded worker tests non-deterministic on
  bridge/worker machines.

## Documentation
No documentation changes needed — this is a one-word string alignment in a shell script with no
user-facing or behavioral surface.
