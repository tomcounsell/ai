# Nightly Regression Tests

Automated nightly safety net for the unit test suite. A launchd job runs the
suite each night at 03:00, compares failure counts against the prior run,
runs a post-run TTFT (time-to-first-token) regression gate, and sends
Telegram alerts only when new failures, collection errors, or a cold-start
latency regression appear.

## Status

Shipped (issue #972); TTFT regression gate added (issue #1227)

## What It Does

- Runs `pytest tests/unit/ -n auto --json-report` nightly at 03:00 local time
- Compares `failed` count against the previous run's `data/nightly_tests_last_run.json`
- Runs the TTFT regression gate as a post-test check (see below)
- Sends Telegram alerts to "Eng: Valor" when:
  - `delta > 0` (new failures) or collection errors appear
  - The TTFT gate detects a cold-start latency regression
- Clean runs produce no noise

## Alert Conditions

| Condition | Message |
|-----------|---------|
| First run (no prior state) | `Nightly regression baseline established: {total} tests, {failed} failures.` |
| `delta > 0` (new failures) | `Nightly regression: +{delta} new failures ({current.failed} total). Run: pytest tests/unit/ -n auto` |
| `error > 0` (collection errors) | `Nightly tests: collection error ({new_errors} errors). Run: pytest tests/unit/ -n auto` |
| TTFT regression | `TTFT regression (issue #1227): {detail}` |
| Clean run (delta ≤ 0, no errors, no TTFT regression) | Silent — no Telegram message |

## TTFT Regression Gate (issue #1227)

After the unit suite runs, a post-run gate reads `logs/cold_start_metrics.jsonl`
and compares the last `TTFT_LAST_N` (10) PM-session cold starts against
`TTFT_THRESHOLD_SECONDS` (120s — production target is 90s; the nightly
threshold allows slack for run-to-run noise). A regression is reported as a
Telegram alert without changing the script's exit code. The gate never
crashes the run: a missing log file, a parse failure, or any other exception
is swallowed and logged as non-fatal.

## Files

| File | Purpose |
|------|---------|
| `scripts/nightly_regression_tests.py` | Main script: runs the unit pytest suite, computes the failure delta, runs the TTFT gate, sends Telegram alerts, saves state |
| `com.valor.nightly-tests.plist` | launchd plist template with `__PROJECT_DIR__`, `__HOME_DIR__`, `__SERVICE_LABEL__` placeholders |
| `scripts/install_nightly_tests.sh` | Install script: bridge-role gated, substitutes placeholders, calls `launchctl bootstrap`; skips + removes stale plist on non-bridge machines |
| `data/nightly_tests_last_run.json` | Unit suite delta state: `passed`, `failed`, `error`, `skipped`, `total`, `run_at` (gitignored) |
| `logs/nightly_tests.log` | Per-run log with timestamps and counts |
| `logs/nightly_tests_error.log` | Startup crash log (captured by launchd before `log()` fires) |
| `logs/cold_start_metrics.jsonl` | TTFT samples consumed by the gate |

## Design Decisions

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
machines and removes any stale plist.

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
