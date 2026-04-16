# Nightly Regression Tests

Automated nightly safety net for the unit test suite. A launchd job runs `pytest tests/unit/ -n auto` each night at 03:00, compares results against the prior run, and sends a Telegram alert only when new failures appear.

## Status

Shipped (issue #972)

## What It Does

- Runs `pytest tests/unit/ -n auto --json-report` nightly at 03:00 local time
- Compares `failed` count against the previous run's `data/nightly_tests_last_run.json`
- Sends a Telegram alert to "Dev: Valor" only when `delta > 0` (new failures) or collection errors appear
- Clean runs (same or fewer failures) produce no noise
- First run sends a distinct baseline message so the delta context is established

## Alert Conditions

| Condition | Message |
|-----------|---------|
| First run (no prior state) | `Nightly regression baseline established: {total} tests, {failed} failures.` |
| `delta > 0` (new failures) | `Nightly regression: +{delta} new failures ({current.failed} total). Run: pytest tests/unit/ -n auto` |
| `error > 0` (collection errors) | `Nightly tests: collection error ({new_errors} errors). Run: pytest tests/unit/ -n auto` |
| Clean run (delta ≤ 0, no errors) | Silent — no Telegram message |

## Files

| File | Purpose |
|------|---------|
| `scripts/nightly_regression_tests.py` | Main script: runs pytest, computes delta, sends Telegram, saves state |
| `com.valor.nightly-tests.plist` | launchd plist template with `__PROJECT_DIR__`, `__HOME_DIR__`, `__SERVICE_LABEL__` placeholders |
| `scripts/install_nightly_tests.sh` | Install script: substitutes placeholders, calls `launchctl bootstrap` |
| `data/nightly_tests_last_run.json` | Persisted delta state: `passed`, `failed`, `error`, `total`, `run_at` (gitignored) |
| `logs/nightly_tests.log` | Per-run log with timestamps and counts |
| `logs/nightly_tests_error.log` | Startup crash log (captured by launchd before `log()` fires) |

## Design Decisions

**JSON report over text parsing** — `--json-report` gives structured summary data without fragile regex against pytest's output format.

**Local JSON state, not Redis** — Two fields (`failed`, `run_at`) don't justify a Redis dependency. Matches the `sdlc_reflection_last_run.json` and `autoexperiment_last_run.json` patterns.

**Best-effort Telegram** — `send_telegram()` never crashes the script. If `valor-telegram` is missing or the send fails, it logs a warning and continues. The test results are still saved.

**Per-count delta, not per-test delta** — Tracking individual test names is out of scope for Small appetite. The Telegram message includes the exact count and a copy-paste command to reproduce.

## Installation

```bash
./scripts/install_nightly_tests.sh
```

Prerequisite: `pytest-json-report>=1.5` must be installed (`uv sync --extra dev` or `uv pip install pytest-json-report`). The install script performs a hard preflight check and exits with a clear error if missing.

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
- `pytest-xdist` (already present — used for `-n auto` parallelism)
- `valor-telegram` on PATH (best-effort — not required)
