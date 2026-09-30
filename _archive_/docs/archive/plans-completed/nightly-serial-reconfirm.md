# Nightly detector: serial re-confirmation gate (#2180)

## Root cause

`scripts/nightly_regression_tests.py` runs `pytest tests/unit/ -n auto --json-report`
and alerts whenever the **failure count** rises above the prior run's baseline. `-n auto`
is pytest-xdist parallel execution. The classic xdist failure mode — tests that pass
serially but collide under parallel workers on shared state (Redis keys, temp files,
fixture ordering) — produces a **shifting set** of failures that the count-based detector
cannot distinguish from a genuine regression.

The 07-19 +15/21 spike re-appeared right after a 07-17 "false alarm" fix precisely because
the detector had no way to tell a parallelism artifact from a real regression: it only ever
looked at a scalar count. A count comparison is structurally blind to *which* tests fail, so
a fix that suppressed one symptom left the underlying ambiguity in place.

## Success Criteria

- The nightly detector classifies each parallel failure as a confirmed regression or an
  xdist-parallelism artifact via a serial `-n0` re-run of the failing node IDs.
- Regression alerts fire only for newly-confirmed serial failures; parallel-only artifacts
  are logged, never alerted.
- The state file persists the confirmed failing set so future runs diff sets, not counts.
- New unit tests cover node-ID extraction, serial classification, and set-based new-failure
  detection; scoped tests pass.

## Approach

Add a **serial re-confirmation gate** and make the baseline **set-based** instead of
count-based:

1. Run `pytest tests/unit/ -n auto` as before, but capture the **set of failing node IDs**
   (not just the count) from the JSON report.
2. If any tests failed, re-run **only those node IDs** serially (`-n0`). Tests that fail in
   parallel but pass serially are classified as **xdist-parallelism artifacts** (shared-state
   collisions); tests that fail in both are **confirmed regressions**.
3. Persist the confirmed failing set in the state file. A regression alert fires only for
   **newly-confirmed** failures (confirmed set minus the prior confirmed set) — a shifting
   flaky set no longer trips the alert.
4. Artifacts are logged (with their node IDs) but never sent as a regression alert, so
   parallel-execution noise stops generating spurious pages.

The serial re-run targets only the already-failing node IDs, so it is fast and respects the
narrow-scope test rule — it never re-runs the whole suite.

## No-Gos

- Do not remove `-n auto` from the primary run — parallel execution is the point of the
  nightly gate (speed + catching real parallel-safety regressions).
- Do not run the full suite serially as the re-confirmation step; only re-run the failing
  node IDs.
- Do not touch issue #2173's `test_remote_update_shell.py` assertion (separate lane).

## Update System

No update system changes required — `scripts/nightly_regression_tests.py` is invoked by an
already-installed launchd schedule (`install_nightly_tests.sh`); its CLI surface and state
file location are unchanged.

## Agent Integration

No agent integration required — this is a standalone maintenance script run by launchd, not a
bridge-reachable capability. No new CLI entry point in `pyproject.toml`.

## Failure Path Test Strategy

- Re-confirmation subprocess failure (serial re-run itself errors / can't collect): fail
  safe by treating all parallel failures as confirmed, so a genuine regression is never
  silently hidden.
- Missing/old-format state file (no `failing_tests` key): fall back to count-delta so first
  run after deploy still behaves sanely.

## Test Impact
- [ ] `tests/unit/test_nightly_regression_tests.py` — UPDATE: add coverage for
  `extract_failing_node_ids`, serial classification (confirmed vs artifact), and set-based
  new-failure detection. Existing `TestDeltaLogic`/`TestRunTtftGate` cases remain valid.

## Rabbit Holes

- Do not attempt to reproduce the exact 07-19 environment; the durable fix is making the
  detector self-classify regardless of which tests happen to collide on a given night.
- Do not try to individually fix every parallel-unsafe unit test in this issue; the gate
  neutralizes their alert-noise. Specific collisions can be filed separately.

## Documentation
- [ ] Update the module docstring in `scripts/nightly_regression_tests.py` to describe the
  serial re-confirmation gate, the confirmed/artifact classification, and why the state file
  now stores a failing-test set instead of a scalar count.
- [ ] Update the `python scripts/nightly_regression_tests.py` row context in `CLAUDE.md` /
  the nightly-tests feature note is not required; the docstring is the canonical reference
  for this internal script. No other docs are affected.
