# Removed Defenses Ledger

**Issue:** #1926 (post-teardown scar-tissue removal)

## Purpose

The granite PTY teardown (#1924, PR #1930, merged 2026-07-07) deleted the PTY
substrate and cut every session over to headless `claude -p` execution. That
teardown also deleted a first wave of PTY-specific failure-handling machinery
outright. This ledger is the durable, ongoing record of *every* defense this
project deletes because its failure mode can no longer occur, so a future
targeted fix has a map back to the original gotcha instead of re-deriving it
from scratch or reaching for blanket machinery.

**Entry format:** defense / gotcha it guarded against / why it is dead under
headless / the Sentry signature that would justify a targeted re-apply if the
gotcha ever resurfaces.

## Entries removed by THIS PR (#1926)

### 1. `FEATURES__STALL_RECOVERY_ENABLED` dry-run gate (closes #1855)

- **Defense:** a feature flag (`config/settings.py`) gating the stall-advisory
  action-mode (`reflections/stall_advisory.py::_maybe_recover`) between a
  dry-run "log what we would do" path and a live kill+re-enqueue path.
  Default `False` since PR #1773 shipped it (issue #1768).
- **Gotcha it guarded against:** stall-recovery kill+catchup had never
  actuated in production; the flag was PTY-era caution scaffolding so an
  operator could observe classification behavior before trusting the
  classifier to kill real sessions.
- **Why it is dead:** the classifier (`agent/session_stall_classifier.py`)
  has run in observe-only mode long enough, and the real safety mechanism —
  the consecutive-observation counter (`stall_recovery_consecutive_observations`,
  default 3, ≈15 min of sustained stall) plus the run/per-session kill budgets
  — is the actual gate that matters. Per operator decision (Tom, 2026-07-02),
  stall recovery is now the always-on behavior, not a dry-run-gated feature.
  The dry-run branch and the flag were removed; `stall_recovery_run_budget`
  was relaxed from `ge=1` to `ge=0` so `FEATURES__STALL_RECOVERY_RUN_BUDGET=0`
  remains a no-deploy break-glass kill-switch (the existing run-budget gate
  already short-circuits every candidate to `skipped_run_budget` at 0).
- **Sentry signature to watch for a targeted re-apply:** a spike in
  `StatusConflictError` (VALOR-DZ class) or exit-143 events correlated with
  `stall_recovery_action` kill events in the `session_events` stream — i.e. a
  live actuation killing a session that was actually healthy. If that
  recurs, the fix is tightening the gate ladder (consec threshold, budgets),
  not reintroducing a global dry-run flag.

## Entries removed by #1927 (AgentSession schema diet)

### 1. `agent/crash_signature.py` `ceiling` / `ceiling_timeout` signature class

- **Defense:** a `startup_failure_kind == "ceiling"` branch inside
  `_extract_signature_inner` / `_derive_signature_class` that prefixed a
  crash signature with `ceiling`/`ceiling_timeout` for sessions that hit the
  600s startup ceiling.
- **Gotcha it guarded against:** #1926 kept this branch specifically so
  pre-#1924-teardown historical rows (which still carried a stamped
  `startup_failure_kind` value) would keep classifying correctly, even
  though nothing produced the field anymore.
- **Why it is dead:** the schema diet (#1927) deleted `startup_failure_kind`
  and `startup_captured_frame` from the `AgentSession` model outright — the
  historical rows #1926 was preserving compatibility for are themselves
  gone (or, for terminal rows, stripped by `scripts/migrate_schema_diet_fields.py`).
  With no field left to read, the `ceiling` branch had no reachable input;
  it and its docstring references were removed entirely rather than kept
  as unreachable dead code.
- **Sentry signature to watch for a targeted re-apply:** none expected —
  this classifies historical startup-diagnostic data, not a live failure
  mode. If a future startup-diagnostic feature needs a similar prefix, it
  should be a fresh field/branch, not a resurrection of `startup_failure_kind`.

## Baseline: removed by the #1930 teardown (reference only)

The classes below were already deleted from the codebase by PR #1930 (the
granite PTY teardown itself), **not by this PR**. They are recorded here as a
historical map-back so a future engineer investigating "why did this Sentry
class stop firing" has one place to look, not as deletions #1926 performed.

| Class (Sentry signature prefix) | What it guarded against | Why it's dead |
|---|---|---|
| `Watchdog W4/W5 U-state / "kill the PTY master fds"` (VALOR-B7/B8) | The former PTY pool's fd-close path hanging on a wedged PTY master fd | The PTY pool itself was deleted; there are no PTY master fds to close |
| `[pty-pool] slot stuck/spawn failed` (VALOR-BF/A4) | A PTY pool slot failing to spawn or getting stuck mid-spawn | No PTY pool exists; `claude -p` spawns a plain subprocess per turn |
| `[granite-container] startup plateau` (VALOR-AX) | The granite container failing to reach a ready state within a startup window | The granite/PTY container substrate was deleted wholesale |
| `[granite-exit-anomaly]` (VALOR-A3) | Anomalous exit codes from the granite PTY container process | Same — no granite container process exists post-teardown |
| `[deadman] loop beacon stale` (VALOR-BE/BG) | The PTY read-loop's deadman beacon going stale (loop wedged without dying) | No PTY read loop exists; headless turns are bounded by `turn_timeout_for` instead |
| `[executor-guard] refusing empty container message` (VALOR-B5) | An empty message arriving on the granite container's inbound channel | No granite container inbound channel exists |
| `granite_wedged` stall verdict, `GRANITE_WEDGED_PTY_STALE_SECS` / `GRANITE_WEDGED_READLOOP_FRESH_SECS` constants (`agent/session_stall_classifier.py`, issue #1768/#1924) | A granite PTY session wedged in a turn-0 loop, detected by watching `last_pty_read_loop_at` staying fresh while `last_pty_activity_at` went stale | A `claude -p` turn has no persistent screen to stall in a turn-0 loop on; see [Stall Recovery](features/stall-recovery.md#actionable-stall-reasons) |

All of these stopped firing sharply at the #1930 cutover boundary
(~2026-07-07) with no headless-era recurrence as of this PR (2026-07-11,
~4-5 days of clean headless telemetry).

## Explicitly kept (not scar tissue, documented so nobody re-litigates them)

These surfaces were evaluated for removal by #1926 and deliberately kept.
Recorded here so the reasoning survives, not because anything was deleted.

- **`monitoring/worker_watchdog.py` W1-W5 kill ladder.** Confirmed to carry
  no PTY-specific narrative (`grep -ni "pty master|master fd"` == 0). Its
  U-state docstring/log text is the generic issue-#1767 rationale for a
  hung-worker kill ladder — substrate-agnostic: a headless `claude -p`
  subprocess can wedge in uninterruptible sleep on a blocking syscall exactly
  as any process can. No edit made.
- **`agent/output_router.py` `MAX_NUDGE_COUNT = 50`.** A runaway backstop on
  the bridge nudge loop, orthogonal to PTY (it never read a PTY fd). Zero
  runaway-nudge Sentry issues observed in the headless era, so there is no
  evidence justifying removal. Whether the whole nudge loop is vestigial
  under headless (steering-list-only inbound) is flagged as a separate,
  control-flow-traced investigation — see the #1926 Open Questions.
- **`agent/crash_signature.py` `ceiling` / `ceiling_timeout` signature
  class.** Kept by #1926 for backward-compatible classification of
  pre-cutover rows whose `startup_failure_kind == "ceiling"` — per the
  extractor's own docstring, nothing produces `startup_failure_kind` after
  the PTY teardown (#1924), but historical rows still carried the value.
  **Superseded by #1927** (AgentSession schema diet): `startup_failure_kind`
  itself was deleted from the model, and the entire `ceiling`/`ceiling_timeout`
  plumbing chain (`_derive_signature_class`'s keyword param, the two
  `== "ceiling"` branches, and the module docstring's determinism-guardrail
  priority-2 rule) was removed from `crash_signature.py` — see the entry
  below.
- **`monitoring/bridge_watchdog.py` 5-level escalation ladder + revert-commit.**
  Supervises the bridge process (Telethon connectivity, hibernation,
  auto-revert of a bad commit) — orthogonal to PTY and to session execution.

## Adding a new entry

When a future PR deletes reporting/recovery machinery because its failure
mode can no longer occur, add an entry under "Entries removed by THIS PR"
(rename the section per PR, or add a new dated section) following the same
four-field format: defense / gotcha / why-dead / Sentry-signature-to-watch.
