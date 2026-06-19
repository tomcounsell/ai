# Nightly Regression Tests

Automated nightly safety net for the unit test suite AND the granite real-loop
integration test. A launchd job runs both suites each night at 03:00 and sends
Telegram alerts only when new failures or unexpected skips appear.

## Status

Shipped (issue #972); granite real-loop integration added (issue #1740)

## What It Does

- Runs `pytest tests/unit/ -n auto --json-report` nightly at 03:00 local time
- Compares `failed` count against the previous run's `data/nightly_tests_last_run.json`
- Runs `pytest tests/integration/test_granite_container_loop.py` as a **second, isolated
  invocation** with its own JSON report file and its own state key
  (`data/nightly_tests_integration_last_run.json`)
- Sends Telegram alerts to "Eng: Valor" when:
  - Unit suite: `delta > 0` (new failures) or collection errors appear
  - Integration suite: failures, errors, or subprocess crash/timeout
  - Skip-visibility: the integration test was skipped on a model-reachable machine
    (controlled by `NIGHTLY_MODEL_EXPECTED` env var — see below)
- Clean runs produce no noise

## Alert Conditions

### Unit Suite

| Condition | Message |
|-----------|---------|
| First run (no prior state) | `Nightly regression baseline established: {total} tests, {failed} failures.` |
| `delta > 0` (new failures) | `Nightly regression: +{delta} new failures ({current.failed} total). Run: pytest tests/unit/ -n auto` |
| `error > 0` (collection errors) | `Nightly tests: collection error ({new_errors} errors). Run: pytest tests/unit/ -n auto` |
| Clean run (delta ≤ 0, no errors) | Silent — no Telegram message |

### Granite Real-Loop Integration Suite

| Condition | Message |
|-----------|---------|
| First run (no prior state) | `Nightly granite real-loop baseline established: ...` |
| `failed > 0` or `error > 0` | `Nightly granite real-loop regression: {failed} failed, {error} errors. Run: pytest tests/integration/test_granite_container_loop.py -v` |
| Subprocess crash / timeout | `Nightly granite real-loop test: subprocess crashed or timed out.` |
| Skipped on model-reachable machine | `Nightly granite real-loop: N test(s) skipped on a model-reachable machine...` |
| Clean run | Silent — no Telegram message |

## Skip-Visibility Gate (issue #1740)

The granite real-loop integration test is env-gated on `claude --print ping` (model
reachable). On the bridge machine where the model IS reachable, tests should never
skip. The skip-visibility gate catches regressions where the skip condition fires
incorrectly.

**How it works:**
- The `com.valor.nightly-tests.plist` sets `NIGHTLY_MODEL_EXPECTED=1` in
  `EnvironmentVariables` so the launchd job always runs with this flag on the
  bridge machine.
- When `NIGHTLY_MODEL_EXPECTED` is truthy AND the integration report parsed
  successfully AND `summary.skipped > 0`, a Telegram alert is raised.
- The flag is static (not a live ping) — it is set at install time and reflects the
  machine's role, not real-time model availability.
- This gate only fires when the report was actually parsed (not when the subprocess
  crashed or timed out).

## Files

| File | Purpose |
|------|---------|
| `scripts/nightly_regression_tests.py` | Main script: runs unit + integration pytest suites, computes deltas, sends Telegram alerts, saves state |
| `com.valor.nightly-tests.plist` | launchd plist template with `__PROJECT_DIR__`, `__HOME_DIR__`, `__SERVICE_LABEL__` placeholders; includes `NIGHTLY_MODEL_EXPECTED=1` |
| `scripts/install_nightly_tests.sh` | Install script: bridge-role gated, substitutes placeholders, calls `launchctl bootstrap`; skips + removes stale plist on non-bridge machines |
| `data/nightly_tests_last_run.json` | Unit suite delta state: `passed`, `failed`, `error`, `skipped`, `total`, `run_at` (gitignored) |
| `data/nightly_tests_integration_last_run.json` | Integration suite delta state (separate key from unit suite, gitignored) |
| `logs/nightly_tests.log` | Per-run log with timestamps and counts |
| `logs/nightly_tests_error.log` | Startup crash log (captured by launchd before `log()` fires) |

## Design Decisions

**Two isolated invocations** — The integration test runs in a second `subprocess.run`
call with its own `--json-report-file` (distinct from the unit suite's). This guarantees
the two reports never clobber each other and the integration state is tracked
independently. The integration test can be skipped (model unreachable) without
affecting the unit suite's delta tracking.

**JSON report over text parsing** — `--json-report` gives structured summary data without
fragile regex against pytest's output format.

**Local JSON state, not Redis** — Two fields (`failed`, `run_at`) don't justify a Redis
dependency. Matches the `sdlc_reflection_last_run.json` and `autoexperiment_last_run.json`
patterns.

**Best-effort Telegram** — `send_telegram()` never crashes the script. If `valor-telegram`
is missing or the send fails, it logs a warning and continues. The test results are still
saved.

**Per-count delta, not per-test delta** — Tracking individual test names is out of scope.
The Telegram message includes the exact count and a copy-paste command to reproduce.

**Bridge-role gating** — `install_nightly_tests.sh` includes a `has_bridge_role()` function
(mirroring `has_email_role()` in `install_email_bridge.sh`) that skips install on non-bridge
machines and removes any stale plist. This prevents the job from running on skills-only
machines where the granite real-loop test would always skip.

## Installation

Nightly tests are installed automatically by `/update` on bridge machines:

```bash
/update  # or: python scripts/update/run.py --full
```

For manual install:

```bash
./scripts/install_nightly_tests.sh
```

Prerequisite: `pytest-json-report>=1.5` must be installed (`uv sync --extra dev` or
`uv pip install pytest-json-report`). The install script performs a hard preflight check.

Verify installation:

```bash
launchctl list | grep nightly-tests
```

## Manual Testing

```bash
# Dry-run: runs tests, prints what Telegram message would be sent, saves state
python scripts/nightly_regression_tests.py --dry-run

# Stream live output
tail -f logs/nightly_tests.log
```

## Uninstall

```bash
launchctl bootout gui/$(id -u)/com.valor.nightly-tests
rm ~/Library/LaunchAgents/com.valor.nightly-tests.plist
```

## Dependencies

- `pytest-json-report>=1.5` (declared in `pyproject.toml` `[project.optional-dependencies].dev`)
- `pytest-xdist` (already present — used for `-n auto` parallelism in the unit suite)
- `valor-telegram` on PATH (best-effort — not required)
