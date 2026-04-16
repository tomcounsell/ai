---
status: Ready
type: feature
appetite: Small
owner: Valor Engels
created: 2026-04-16
tracking: https://github.com/tomcounsell/ai/issues/972
revision_applied: true
---

# Nightly Regression Testing with Telegram Delta Reporting

## Problem

There is no automated nightly safety net for the unit test suite. Regressions introduced during the day go undetected until someone manually runs pytest. The only alerting channel is ad hoc — no systematic comparison between runs, and no Telegram notification when tests break overnight.

**Current behavior:**
Unit tests are run manually or during SDLC do-test stages. There is no scheduled runner, no baseline comparison between successive runs, and no automated alert when new test failures appear.

**Desired outcome:**
A launchd job runs `pytest tests/unit/ -n auto` nightly at 03:00. It compares the pass/fail counts from the current run against the previous run, persists the delta in `data/nightly_tests_last_run.json`, and sends a Telegram alert via `valor-telegram send` only when new failures appear (regression delta > 0). Clean runs and pre-existing failures produce no noise.

## Freshness Check

**Baseline commit:** `1c80b587`
**Issue filed at:** 2026-04-15
**Disposition:** Unchanged — no nightly test runner exists

**File:line references re-verified:**
- `scripts/install_sdlc_reflection.sh` — canonical launchd install pattern — present
- `com.valor.sdlc-reflection.plist` — plist template with placeholder conventions — present
- `com.valor.autoexperiment.plist` — `StartCalendarInterval` pattern — present
- `tests/unit/` — 197 unit test files — present, runs with `-n auto`

## Prior Art

- **`scripts/install_sdlc_reflection.sh`** — canonical launchd install pattern with `bootout`/`bootstrap`, `.env` sourcing, and `sed` path substitution into a plist template.
- **`com.valor.sdlc-reflection.plist`** — plist template using `__PROJECT_DIR__`, `__HOME_DIR__`, `__SERVICE_LABEL__` placeholders; bash `-c "source .env; exec .venv/bin/python ..."` invocation.
- **`com.valor.autoexperiment.plist`** — `StartCalendarInterval` (Hour/Minute) for a fixed daily time.
- **`scripts/sdlc_reflection.py`** — script pattern: `LOG_FILE`, `LAST_RUN_FILE` in `data/`, `log()` helper, `load_last_run()` / `save_last_run()` with JSON, `--dry-run` flag, `main()` returning int.
- **`scripts/memory_consolidation.py:328`** — live `valor-telegram send --chat "Dev: Valor" <message>` via `subprocess.run`.

## Data Flow

1. **Entry point**: launchd fires `scripts/nightly_regression_tests.py` at 03:00 daily.
2. **Run tests**: `subprocess.run(["python", "-m", "pytest", "tests/unit/", "-n", "auto", "--tb=no", "-q", "--json-report", "--json-report-file=/tmp/nightly_pytest.json"])` — captures structured output.
3. **Parse results**: Read `/tmp/nightly_pytest.json`; extract `summary.passed`, `summary.failed`, `summary.error`.
4. **Load previous run**: Read `data/nightly_tests_last_run.json`; extract prior `failed` count (defaults to 0 on first run).
5. **Delta check**: `delta = current.failed - prev.failed`; `new_errors = current.error`. Alert condition: `if delta > 0 or new_errors > 0`. First-run (no prior state file): send a distinct baseline message instead of a regression alert.
6. **Save state**: Write current run's counts + timestamp to `data/nightly_tests_last_run.json` (includes `passed`, `failed`, `error`, `total`, `run_at`).
7. **Telegram alert**:
   - Regression: `"Nightly regression: +{delta} new failures ({current.failed} total). Run: pytest tests/unit/ -n auto"`
   - Collection error: `"Nightly tests: collection error ({new_errors} errors). Run: pytest tests/unit/ -n auto"`
   - First run baseline: `"Nightly regression baseline established: {total} tests, {failed} failures."`
8. **Logging**: All steps write to `logs/nightly_tests.log` via `log()` helper.

## Architectural Impact

- **New dependencies**: `pytest-json-report>=1.5` added to `pyproject.toml` `[project.optional-dependencies].dev` for structured JSON output.
- **Interface changes**: None — no bridge, agent, or Redis changes.
- **Data ownership**: `data/nightly_tests_last_run.json` is gitignored (matches other `data/` runtime files).
- **Reversibility**: Fully reversible — `launchctl bootout` + `rm` the plist removes it.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| pytest-xdist installed | `python -m pytest --co -q tests/unit/ -n auto 2>&1 \| head -3` | Parallel test execution |
| pytest-json-report installed | `python -m pytest --json-report --help 2>&1 \| grep json-report` | Structured result output (declared in `pyproject.toml` dev deps; install script enforces presence) |
| valor-telegram on PATH | `which valor-telegram` | Telegram notifications |

## Solution

### Key Elements

- **`scripts/nightly_regression_tests.py`**: Runs pytest with `--json-report`, parses JSON result, computes delta against `data/nightly_tests_last_run.json`, sends Telegram only on regression, saves state, logs everything.
- **`com.valor.nightly-tests.plist`**: Template with `__PROJECT_DIR__`, `__HOME_DIR__`, `__SERVICE_LABEL__` placeholders; `StartCalendarInterval` set to `Hour=3, Minute=0`.
- **`scripts/install_nightly_tests.sh`**: Mirrors `install_sdlc_reflection.sh` — reads `.env`, copies plist with `sed` substitution, calls `launchctl bootout` then `launchctl bootstrap`.
- **Delta stored in `data/nightly_tests_last_run.json`**: JSON with `run_at`, `passed`, `failed`, `error`, `total`. No Redis dependency.

### Flow

`launchd 03:00` → `nightly_regression_tests.py` → `pytest tests/unit/ -n auto --json-report` → parse JSON → load `data/nightly_tests_last_run.json` → compute delta+errors → if `first_run OR delta > 0 OR errors > 0`: `.venv/bin/valor-telegram send` → save state → log result → exit 0

### Technical Approach

**1. `scripts/nightly_regression_tests.py`**

```python
PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
LAST_RUN_FILE = DATA_DIR / "nightly_tests_last_run.json"
LOG_FILE = PROJECT_DIR / "logs" / "nightly_tests.log"
TELEGRAM_CHAT = "Dev: Valor"
TELEGRAM_BIN = PROJECT_DIR / ".venv/bin/valor-telegram"  # full path — launchd PATH omits .venv/bin
PYTEST_JSON_TMP = "/tmp/nightly_pytest_report.json"
```

Functions following `sdlc_reflection.py` conventions:
- `log(msg)` — timestamp-prefixed, writes stdout + `LOG_FILE`
- `load_last_run() -> dict` — reads `LAST_RUN_FILE`, returns `{}` (empty dict) on missing/corrupt (empty = first run, not `{"failed": 0}`)
- `save_last_run(state: dict)` — atomic write to `LAST_RUN_FILE` (includes `passed`, `failed`, `error`, `total`, `run_at`)
- `run_tests() -> dict` — subprocess pytest with `--json-report`, returns summary dict
- `send_telegram(msg: str)` — `subprocess.run([str(TELEGRAM_BIN), "send", "--chat", TELEGRAM_CHAT, msg])` with `if TELEGRAM_BIN.exists()` guard (best-effort)
- `main() -> int` — orchestrates all steps, supports `--dry-run`

**Alert logic in `main()`**:
```python
prev = load_last_run()
is_first_run = not prev  # empty dict → first run
delta = current["failed"] - prev.get("failed", 0)
new_errors = current.get("error", 0)

if is_first_run:
    send_telegram(f"Nightly regression baseline established: {current['total']} tests, {current['failed']} failures.")
elif delta > 0:
    send_telegram(f"Nightly regression: +{delta} new failures ({current['failed']} total). Run: pytest tests/unit/ -n auto")
elif new_errors > 0:
    send_telegram(f"Nightly tests: collection error ({new_errors} errors). Run: pytest tests/unit/ -n auto")
# else: clean run — silent
```

**2. `com.valor.nightly-tests.plist`**

Use `StartCalendarInterval` (Hour=3, Minute=0) matching `com.valor.autoexperiment.plist`.
Use the bash `-c "source .env; exec .venv/bin/python ..."` invocation from `com.valor.sdlc-reflection.plist`.

Key plist entries that differ from the template pattern:
- **`EnvironmentVariables.PATH`**: `__PROJECT_DIR__/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/usr/sbin:/bin` — includes `.venv/bin` so venv-installed scripts are resolvable even if the Python code uses subprocess PATH-resolution fallback.
- **`StandardOutPath`**: `__PROJECT_DIR__/logs/nightly_tests.log`
- **`StandardErrorPath`**: `__PROJECT_DIR__/logs/nightly_tests_error.log` — captures startup crashes and unhandled exceptions before the `log()` helper fires.

**3. `scripts/install_nightly_tests.sh`**

Mirror `install_sdlc_reflection.sh`:
- `LABEL="${SERVICE_LABEL_PREFIX}.nightly-tests"`
- `launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"` (modern API)
- Echo label, schedule, log path, manual run and uninstall commands
- **Prerequisite check** (run before plist installation):
  ```bash
  python -m pytest --json-report --help > /dev/null 2>&1 || {
      echo "ERROR: pytest-json-report not installed. Run: uv pip install pytest-json-report"
      exit 1
  }
  ```
  `pytest-json-report>=1.5` is also declared in `pyproject.toml` `[project.optional-dependencies].dev` so `uv sync --extra dev` installs it automatically.

**4. CLAUDE.md** — four rows in `## Quick Commands` table near the other install script rows:
```
| `./scripts/install_nightly_tests.sh` | Install nightly regression test launchd schedule |
| `python scripts/nightly_regression_tests.py --dry-run` | Preview nightly test run without Telegram |
| `tail -f logs/nightly_tests.log` | Stream nightly test logs |
| `tail -f logs/nightly_tests_error.log` | Stream nightly test error log (startup crashes) |
```

## Failure Path Test Strategy

- pytest subprocess timeout: catch `subprocess.TimeoutExpired`, log error, exit 1 without saving state
- JSON report missing/malformed: catch `(FileNotFoundError, json.JSONDecodeError)`, log, exit 1
- `valor-telegram` not on PATH: log warning, do NOT crash — Telegram is best-effort
- `data/` missing: `DATA_DIR.mkdir(parents=True, exist_ok=True)` before `save_last_run`
- pytest collection error (non-failure non-zero exit): log prominently and always send Telegram

## Test Impact

No existing tests affected — this is a standalone script. Unit tests for `load_last_run`, `save_last_run`, and delta computation can be added to `tests/unit/test_nightly_regression_tests.py` using `tmp_path` fixtures if desired.

## Success Criteria

- [ ] `~/Library/LaunchAgents/com.valor.nightly-tests.plist` installed; `launchctl list | grep nightly-tests` shows the label
- [ ] Runs `pytest tests/unit/ -n auto` nightly at 03:00 local time
- [ ] Telegram alert sent on first run (baseline message), regression (`delta > 0`), or collection error; clean runs are silent
- [ ] Delta persisted to `data/nightly_tests_last_run.json` between runs
- [ ] `scripts/install_nightly_tests.sh` installs cleanly and prints label, schedule, log path
- [ ] CLAUDE.md quick reference table contains install and dry-run commands
- [ ] `logs/nightly_tests.log` captures each run with timestamp, counts, and delta

## No-Gos

- Per-test delta tracking (counts only)
- Running integration or e2e tests nightly (unit only)
- Auto-creating GitHub issues on regression
- Redis key storage (local JSON file is sufficient)
- Retry on failure (this script reports, does not remediate)

## Rabbit Holes

- **Redis for delta storage**: Adds infrastructure dependency for a two-field JSON file. Local JSON matches the `sdlc_reflection_last_run.json` pattern.
- **Sending alerts on every run**: Noise. The value is in delta detection.
- **Parsing pytest text output**: `--json-report` gives structured data without fragile regex.
- **Per-test delta tracking**: Out of scope for Small appetite.

## Risks

### Risk 1: pytest-json-report not installed
**Impact:** Script crashes on flag parse.
**Mitigation:** `pytest-json-report>=1.5` is declared in `pyproject.toml` dev dependencies — `uv sync --extra dev` installs it. Install script performs a hard preflight check and exits with a clear error message if missing.

### Risk 2: Confusing first-run alert
**Impact:** First run has no prior state; the generic `"+N new failures"` message at 03:00 could cause unnecessary alarm.
**Mitigation:** First run is detected by an empty (missing) `LAST_RUN_FILE`. A distinct baseline message is sent: `"Nightly regression baseline established: {total} tests, {failed} failures."` This clearly communicates intent and sets expectations.

### Risk 3: Machine sleep during scheduled window
**Impact:** launchd fires at next wake, not at 03:00 exactly.
**Mitigation:** Standard launchd behavior; acceptable for a dev machine nightly job.

## Update System

No update system changes required. Standalone script; no existing bridge, agent, or worker code modified. Install script is idempotent.

## Agent Integration

No MCP or bridge changes needed. Standalone Python process invoked by launchd. No Redis pub/sub or bridge message routing required.

## Documentation

- [ ] Add four rows to `## Quick Commands` table in `CLAUDE.md` (install, dry-run, tail logs, tail error log)
- [ ] Create `docs/features/nightly-regression-tests.md` after shipping
- [ ] Add entry to `docs/features/README.md` index after shipping
