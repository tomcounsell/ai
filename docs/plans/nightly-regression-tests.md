---
status: Planning
type: feature
appetite: Small
owner: Valor Engels
created: 2026-04-16
tracking: https://github.com/tomcounsell/ai/issues/972
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
5. **Delta check**: `new_failures = current.failed - prev.failed`. If `new_failures > 0`, send Telegram alert.
6. **Save state**: Write current run's counts + timestamp to `data/nightly_tests_last_run.json`.
7. **Telegram alert**: `valor-telegram send --chat "Dev: Valor" "Nightly regression: +{N} new failures ({current.failed} total). Run: pytest tests/unit/ -n auto"`
8. **Logging**: All steps write to `logs/nightly_tests.log` via `log()` helper.

## Architectural Impact

- **New dependencies**: `pytest-json-report` for structured JSON output.
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
| pytest-json-report installed | `python -m pytest --json-report --help 2>&1 \| grep json-report` | Structured result output |
| valor-telegram on PATH | `which valor-telegram` | Telegram notifications |

## Solution

### Key Elements

- **`scripts/nightly_regression_tests.py`**: Runs pytest with `--json-report`, parses JSON result, computes delta against `data/nightly_tests_last_run.json`, sends Telegram only on regression, saves state, logs everything.
- **`com.valor.nightly-tests.plist`**: Template with `__PROJECT_DIR__`, `__HOME_DIR__`, `__SERVICE_LABEL__` placeholders; `StartCalendarInterval` set to `Hour=3, Minute=0`.
- **`scripts/install_nightly_tests.sh`**: Mirrors `install_sdlc_reflection.sh` — reads `.env`, copies plist with `sed` substitution, calls `launchctl bootout` then `launchctl bootstrap`.
- **Delta stored in `data/nightly_tests_last_run.json`**: JSON with `run_at`, `passed`, `failed`, `error`, `total`. No Redis dependency.

### Flow

`launchd 03:00` → `nightly_regression_tests.py` → `pytest tests/unit/ -n auto --json-report` → parse JSON → load `data/nightly_tests_last_run.json` → compute delta → if `new_failures > 0`: `valor-telegram send` → save state → log result → exit 0

### Technical Approach

**1. `scripts/nightly_regression_tests.py`**

```python
PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
LAST_RUN_FILE = DATA_DIR / "nightly_tests_last_run.json"
LOG_FILE = PROJECT_DIR / "logs" / "nightly_tests.log"
TELEGRAM_CHAT = "Dev: Valor"
PYTEST_JSON_TMP = "/tmp/nightly_pytest_report.json"
```

Functions following `sdlc_reflection.py` conventions:
- `log(msg)` — timestamp-prefixed, writes stdout + `LOG_FILE`
- `load_last_run() -> dict` — reads `LAST_RUN_FILE`, returns `{"failed": 0}` on missing/corrupt
- `save_last_run(state: dict)` — atomic write to `LAST_RUN_FILE`
- `run_tests() -> dict` — subprocess pytest with `--json-report`, returns summary dict
- `send_telegram(msg: str)` — `subprocess.run(["valor-telegram", "send", "--chat", TELEGRAM_CHAT, msg])`
- `main() -> int` — orchestrates all steps, supports `--dry-run`

**2. `com.valor.nightly-tests.plist`**

Use `StartCalendarInterval` (Hour=3, Minute=0) matching `com.valor.autoexperiment.plist`.
Use the bash `-c "source .env; exec .venv/bin/python ..."` invocation from `com.valor.sdlc-reflection.plist`.

**3. `scripts/install_nightly_tests.sh`**

Mirror `install_sdlc_reflection.sh`:
- `LABEL="${SERVICE_LABEL_PREFIX}.nightly-tests"`
- `launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"` (modern API)
- Echo label, schedule, log path, manual run and uninstall commands

**4. CLAUDE.md** — three rows in `## Quick Commands` table near the other install script rows:
```
| `./scripts/install_nightly_tests.sh` | Install nightly regression test launchd schedule |
| `python scripts/nightly_regression_tests.py --dry-run` | Preview nightly test run without Telegram |
| `tail -f logs/nightly_tests.log` | Stream nightly test logs |
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
- [ ] Telegram alert sent only when `current.failed > prev.failed`; clean runs are silent
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
**Mitigation:** Install script checks for `python -m pytest --json-report --help` and prints install hint if missing.

### Risk 2: False-positive alert on first run
**Impact:** `prev.failed = 0`, any pre-existing failures trigger an alert.
**Mitigation:** Intentional — first run gives a baseline snapshot. Document in install script output.

### Risk 3: Machine sleep during scheduled window
**Impact:** launchd fires at next wake, not at 03:00 exactly.
**Mitigation:** Standard launchd behavior; acceptable for a dev machine nightly job.

## Update System

No update system changes required. Standalone script; no existing bridge, agent, or worker code modified. Install script is idempotent.

## Agent Integration

No MCP or bridge changes needed. Standalone Python process invoked by launchd. No Redis pub/sub or bridge message routing required.

## Documentation

- [ ] Add three rows to `## Quick Commands` table in `CLAUDE.md` (install, dry-run, tail logs)
- [ ] Create `docs/features/nightly-regression-tests.md` after shipping
- [ ] Add entry to `docs/features/README.md` index after shipping
