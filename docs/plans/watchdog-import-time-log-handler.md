---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-07
tracking: https://github.com/tomcounsell/ai/issues/2643
last_comment_id: none
---

# Watchdog logging configures at the entry point, not at import

## Problem

An engineer investigating a bridge incident opens `logs/watchdog.log` and finds this:

```
2026-08-07 16:42:59,000 [CRITICAL] [Toms-MacBook-Air.local] Bridge recovery failed — levels 1-4 exhausted. Issues: crash pattern detected. Manual intervention required.
2026-08-07 16:43:01,045 [CRITICAL] Non-wedge crash storm — restart circuit open, skipping restart to avoid thrash
```

None of it happened. This machine runs no `com.valor.bridge`. Those lines were emitted by
`tests/unit/test_bridge_watchdog.py` running in this checkout an hour earlier.

The module docstring is explicit that `logs/watchdog.log` plus `log_crash()` *is* the alerting
system: "The watchdog does NOT push a notification anywhere. It records; it does not deliver."
When the record is half fiction, the alerting system is broken. This already cost real time: the
#2418 investigation read 36 circuit-open timestamps spread across 11 days as a recurring production
livelock, and only a cross-reference against `data/crash_history.jsonl` and `launchctl list`
established that everything after 2026-07-27 was test output.

**Current behavior:**

Two side effects fire when `monitoring/bridge_watchdog.py` is imported, before any function runs:

- `monitoring/bridge_watchdog.py:78-81` — `logging.basicConfig(level=INFO, format=...)` mutates the
  **root** logger for the whole importing process.
- `monitoring/bridge_watchdog.py:85-93` — `logs/` is created and a
  `RotatingFileHandler(logs/watchdog.log, 10MB, backupCount=5)` is attached to the module logger.

Eight modules import it, six of them tests. Every pytest process that touches one of those six turns
the handler live, and any test exercising the recovery ladder writes genuine-looking CRITICAL lines
into the operator's incident log. The nightly `com.valor.nightly-tests` job does this on a schedule.

`monitoring/worker_watchdog.py:162` has the same shape: `_configure_logger()` is called at module
scope, so importing it opens `logs/worker_watchdog.log`. Three of its own tests call
`importlib.reload(wwd)`, re-opening the production file each time.

The rotating handler is capped at 10 MB × 5 backups, so synthetic bursts also consume the retention
budget and roll genuine incident history out early.

**Desired outcome:**

Importing either watchdog module has zero logging side effects: no handler on root, no file opened,
no directory created. Configuration happens exactly once, at the script entry point, where launchd
and a human `python monitoring/bridge_watchdog.py --check-only` both reach it. `logs/watchdog.log`
keeps the same path, format, level, and rotation settings it has today, and keeps carrying the same
set of records.

## Freshness Check

**Baseline commit:** `3605ff847`
**Issue filed at:** 2026-08-07T06:19:13Z
**Disposition:** Unchanged

**File:line references re-verified:**

- `monitoring/bridge_watchdog.py:78-81` — `logging.basicConfig(...)` at module scope — still holds,
  exact lines. Verified empirically in a fresh interpreter: `logging.getLogger().handlers` is `[]`
  before the import and `[<StreamHandler <stderr>>]` after.
- `monitoring/bridge_watchdog.py:85-93` — `LOGS_DIR.mkdir()` + `RotatingFileHandler` + `addHandler` —
  still holds. After import, `logging.getLogger("monitoring.bridge_watchdog").handlers` is
  `[<RotatingFileHandler .../logs/watchdog.log>]`, `propagate=True`, `level=0` (NOTSET, inheriting
  root INFO from `basicConfig`).
- `monitoring/worker_watchdog.py:135-160` — `_configure_logger()` reference pattern — still holds.
- `monitoring/worker_watchdog.py:162` — `logger = _configure_logger()` at module scope — still holds.
- `tests/unit/test_bridge_watchdog.py:8` — imports from the module — still holds.
- `monitoring/bridge_watchdog.py:168` — `restart_circuit_open` on `HealthStatus` — still holds.

**Cited sibling issues/PRs re-checked:**

- **#2418** — closed (not planned). The investigation that surfaced this. Its attribution work is the
  reason we know roughly half the alarming lines are synthetic.
- **#2396 / PR #2414** — merged. Introduced `restart_circuit_open` and the CRITICAL line that is now
  the most-forged line in the log. Nothing to revisit; the line itself is correct.
- **#1311 / PR #1315** — closed 2026-05-07. Fixed the worker watchdog's *duplicate-write* problem
  (basicConfig StreamHandler on root + file handler on a propagating named logger = every line
  twice). It produced the `_configure_logger()` shape this plan reuses, but it left the call at
  module scope, which is exactly the defect still open here.

**Commits on main since issue was filed (touching referenced files):**

None. `git log --since=2026-08-07T06:19:13Z -- monitoring/bridge_watchdog.py
monitoring/worker_watchdog.py tests/unit/test_bridge_watchdog.py tests/unit/test_worker_watchdog.py`
is empty at `3605ff847`, despite 78 commits landing on main that day.

**Active plans in `docs/plans/` overlapping this area:** none. The four recently-touched plans
(`redis-flush-hardening`, `suite-failure-rotation-db-ownership`, `flip-steering-writers-to-room-key`,
`durability-room-job-agentrun`) are Redis/Popoto and steering work; none touches `monitoring/`.
`suite-failure-rotation-db-ownership` is adjacent in spirit (test-suite hygiene) but disjoint in
files.

**Bug still reproducible:** yes. `logs/watchdog.log` in this checkout carries a synthetic burst at
`2026-08-07 16:41:52`–`16:43:01` produced by a test run earlier the same day.

**Notes:** No drift. All line numbers in the issue body are accurate as written.

## Prior Art

- **#1311 / PR #1315** — "worker-watchdog observes but never recovers". Its logging half fixed
  double-writing in `logs/worker_watchdog.log` by moving to a named, non-propagating logger with a
  single rotating handler and no `basicConfig`. **Succeeded at what it aimed at, and is the direct
  reference for this work** — but it configured at module scope, so it did not stop import-time file
  opening. This plan finishes that thought.
- **PR #2414 (#2396)** — split the crash-count signal from the action level and added
  `restart_circuit_open`. Relevant only because it authored the CRITICAL line that dominates the
  forged output. No change needed.
- No prior issue or PR has attempted to stop watchdog test output reaching the production log.
  `gh issue list --state closed --search "watchdog logging import handler"` returns nothing relevant.

## Research

**Queries used:**

- `Python logging cookbook library should not configure logging basicConfig at import time entry point only`

**Key findings:**

- The stdlib docs state the idiom directly: `logging.basicConfig(...)` belongs inside `main()` under
  `if __name__ == '__main__':`, while library modules do only `logger = logging.getLogger(__name__)`
  and emit records. Configuration is a separate concern from emission, because only the application
  knows the destination and the CLI arguments.
  Source: [Logging HOWTO](https://docs.python.org/3/howto/logging.html),
  [Logging Cookbook](https://docs.python.org/3/howto/logging-cookbook.html).
  *Informs the plan:* the fix is not a pytest fixture or a filter, it is relocating configuration to
  the entry point. That is the canonical shape, not a local invention.
- "In most cases only the root logger needs configuring, since lower-level module loggers forward
  messages to its handlers." Source: [logging module reference](https://docs.python.org/3/library/logging.html).
  *Informs the plan:* it justifies configuring **root** at the bridge watchdog's entry point rather
  than narrowing to the module's own logger. `logs/watchdog.log` today carries records from
  `bridge.hibernation`, `monitoring.crash_tracker`, and `scripts.update.service` that reach it only
  through root propagation. Narrowing would silently shrink the operator's log.
- Libraries that must stay silent when unconfigured attach a `NullHandler` to their top-level logger.
  *Informs the plan:* not needed here. These modules are entry points that happen to be importable,
  not libraries, and the stdlib default (WARNING+ to `stderr` via `lastResort`) is the documented
  best default for the imported-but-unconfigured case.

## Data Flow

**Production path (unchanged in every observable way except de-duplication):**

1. **Entry point**: launchd fires `~/Library/LaunchAgents/{prefix}.bridge-watchdog.plist` every 60 s
   (`scripts/valor-service.sh:599-635`), running `.venv/bin/python monitoring/bridge_watchdog.py`.
   The plist sets `StandardOutPath` and `StandardErrorPath` to the same `logs/watchdog.log`.
2. **`__main__` guard**: `_configure_logging()` runs — mkdir `logs/`, attach the
   `RotatingFileHandler(logs/watchdog.log, 10 MB, 5 backups)` with formatter
   `"%(asctime)s [%(levelname)s] %(message)s"` to the **root** logger at level INFO.
3. **`main()` → `run_health_check()`**: emits through the module logger; records propagate to root
   and land in the file. Records from `bridge.hibernation`, `monitoring.crash_tracker`, and
   `scripts.update.service` propagate to the same root handler and land in the same file, exactly as
   today.
4. **Output**: one line per record in `logs/watchdog.log`. Today there are *two* — one from the
   rotating handler, one from the root `StreamHandler` that `basicConfig` installs, whose stderr the
   plist redirects into the same file. The fix collapses that pair, matching what #1311 did for the
   worker.

**Test path (the defect):**

1. **Entry point**: pytest collects `tests/unit/test_bridge_watchdog.py`.
2. **Import**: `from monitoring.bridge_watchdog import ...` executes module scope → root gets a
   `StreamHandler`, the module logger gets a live `RotatingFileHandler` on the real
   `logs/watchdog.log`.
3. **Test body**: `run_health_check()` with mocked health emits `logger.critical(...)`.
4. **Output**: a synthetic CRITICAL line in the operator's incident log. After the fix, step 2
   attaches nothing, so step 4 has nowhere to write and the record is captured by `caplog` only.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1315 (#1311) | Replaced `basicConfig` + propagating named logger in `monitoring/worker_watchdog.py` with `_configure_logger()`: named logger, `propagate = False`, one rotating handler, idempotent handler clearing | Correct diagnosis of the *duplication* symptom, correct layer for it — but it kept the call at module scope. The comment it left ("Idempotent: clear any handlers attached by prior imports/test runs") shows the author saw test-run contamination and treated it as something to survive rather than something to prevent. Idempotent re-attachment still opens the production file on every import and reload. |
| No prior fix | `monitoring/bridge_watchdog.py` was never migrated to the #1311 shape at all | The #1311 work was scoped to the module whose log was visibly doubling. The bridge watchdog's log doubles too, but nobody was reading it closely enough at the time to notice. |

**Root cause pattern:** both modules are launchd *scripts* that are also *importable modules*, and
each one performs application-level logging configuration in the importable half. The repeated
failure is treating the symptom visible in one log file rather than the shared structural mistake:
configuration below the entry point.

## Architectural Impact

- **New dependencies**: none. Stdlib `logging` only.
- **Interface changes**: `monitoring/bridge_watchdog.py` gains a private `_configure_logging()` and a
  module-level `WATCHDOG_LOG_FILE` constant. `monitoring/worker_watchdog.py` keeps
  `_configure_logger()` with an unchanged signature; only its call site moves. Nothing public
  changes; nothing outside `monitoring/` imports either module.
- **Coupling**: decreases. Importing a watchdog stops mutating process-global logging state, so the
  modules become safe to import from a script, a test, or a future health-check aggregator.
- **Data ownership**: unchanged. `logs/watchdog.log` and `logs/worker_watchdog.log` stay owned by
  their watchdogs, at the same paths with the same rotation budgets.
- **Reversibility**: trivial. The whole change is ~40 lines across two modules plus a new test file;
  reverting the commit restores prior behavior exactly.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (the acceptance criteria in #2643 are unambiguous)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Worktree venv exists on the pinned interpreter | `.worktrees/watchdog-import-time-log-handler/.venv/bin/python --version` matches `cat .python-version` | `scripts/pytest-clean.sh` aborts on an off-pin venv; a missing worktree venv also surfaces as a bogus pre-commit "lint" block |
| `PYTHONPATH` points at the worktree root | `PYTHONPATH=$PWD python -c "import monitoring.bridge_watchdog as m; print(m.__file__)"` prints a path under `.worktrees/` | The shared venv's `.pth` silently imports the main checkout otherwise, which would run the demo against the production log |
| Worktree has its own `logs/` directory | `test -d .worktrees/watchdog-import-time-log-handler/logs \|\| mkdir -p ...` | The red demo measures a worktree-local `logs/watchdog.log`, never the production one |

## Solution

### Key Elements

- **`_configure_logging()` in `monitoring/bridge_watchdog.py`**: does everything module scope does
  today (mkdir, rotating handler, formatter, INFO level) but is called only from the `__main__`
  guard, and configures the **root** logger so the file keeps carrying submodule records.
- **Relocated `_configure_logger()` call in `monitoring/worker_watchdog.py`**: the function body and
  its #1311 topology are untouched; only the call site moves from module scope to the `__main__`
  guard.
- **Pinned logger names**: both modules bind `logging.getLogger("monitoring.<module>")` explicitly
  rather than `getLogger(__name__)`, so script mode and import mode address the same logger object.
- **`tests/unit/test_watchdog_log_isolation.py`**: a subprocess-based import probe per module, an AST
  guard over the whole `monitoring/` package, and positive tests locking the entry-point behavior
  (format, level, path, rotation, idempotence).

### Flow

`launchd fires (60 s)` → **`python monitoring/bridge_watchdog.py`** → `__main__` guard calls
`_configure_logging()` → **root logger has the rotating handler** → `main()` → `run_health_check()`
emits → **`logs/watchdog.log`, one line per record**

`pytest imports monitoring.bridge_watchdog` → **module scope does nothing to logging** → test calls
`run_health_check()` → record propagates to pytest's root `caplog` handler → **`logs/watchdog.log`
unchanged, byte for byte**

### Technical Approach

**1. `monitoring/bridge_watchdog.py`**

Delete lines 78-93 (the `basicConfig` call, the `LOGS_DIR.mkdir()`, the `_watchdog_file_handler`
construction, and the `addHandler`). Keep at module scope only inert bindings:

- `LOGS_DIR = PROJECT_DIR / "logs"` (constant, no `mkdir`)
- `WATCHDOG_LOG_FILE = LOGS_DIR / "watchdog.log"` (new; the monkeypatch seam tests need, mirroring
  `worker_watchdog.LOG_FILE`)
- `logger = logging.getLogger("monitoring.bridge_watchdog")`

Add `_configure_logging() -> None` that mkdirs `WATCHDOG_LOG_FILE.parent`, builds
`RotatingFileHandler(WATCHDOG_LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5)` with formatter
`"%(asctime)s [%(levelname)s] %(message)s"`, sets the root logger to `logging.INFO`, and attaches the
handler to root. Tag the handler with a marker attribute and remove-and-`close()` any previously
tagged handler first, so a repeated call cannot double-write and cannot leak a file descriptor.

Call it from the `__main__` guard only:

```python
if __name__ == "__main__":
    _configure_logging()
    sys.exit(main())
```

Three decisions worth stating explicitly, because each is a place a reviewer will reasonably push:

- **Root, not the module logger.** Mirroring `worker_watchdog`'s `propagate = False` topology on the
  bridge would drop records that currently reach the operator's log through propagation — the
  `[INFO] [hibernation] Bridge hibernating: auth required...` line comes from `bridge/hibernation.py:100`,
  not from the watchdog's own logger. Acceptance criterion 3 says the production log is unchanged;
  shrinking its coverage would violate that. Root configuration at the entry point is also the
  stdlib's documented shape.
- **Inside the `__main__` guard, not at the top of `main()`.** Five tests in
  `tests/unit/test_bridge_watchdog.py` (lines 544, 571, 598, 781, 805) call `main()` directly for
  `--check-only` coverage. Configuring inside `main()` would reintroduce production-log writes from
  the test suite through a new door.
- **No `StreamHandler`.** The plist already redirects the process's stderr into `logs/watchdog.log`,
  so adding one would restore precisely the double-write #1311 removed. The cost is that a human
  running `python monitoring/bridge_watchdog.py` with no flags in a terminal sees nothing echoed and
  must read the log file. `--check-only`, the diagnostic path humans actually use, writes its report
  with `print()` to stdout and is unaffected.

**2. `monitoring/worker_watchdog.py`**

Bind `logger = logging.getLogger("monitoring.worker_watchdog")` at module scope. Move
`_configure_logger()` from module scope (line 162) into the `__main__` guard. Change its handler
clearing from "remove every handler" to "remove and close only handlers this function attached", so a
test that attaches `caplog.handler` directly (`tests/unit/test_worker_watchdog.py:275`) is not
silently stripped.

Its named-logger + `propagate = False` + 5 MB × 3 topology stays exactly as #1311 left it. The
asymmetry with the bridge is deliberate and documented: the worker's log was scoped to the watchdog's
own records on purpose, and the bridge's has always carried the whole process's records that incident
response reads.

**3. `tests/unit/test_watchdog_log_isolation.py`** (new)

- **TC1 / TC3** — fresh `subprocess` interpreter per module: snapshot `logging.getLogger().handlers`,
  import the module, assert the list is unchanged and empty.
- **TC2 / TC4** — same subprocess: walk `logging.root.manager.loggerDict` plus root and assert no
  `FileHandler` anywhere has a `baseFilename` ending in `watchdog.log` / `worker_watchdog.log`.
  Deliberately asserts on handler state rather than on the shared file's byte count, so it cannot
  flake when another agent's test run appends to the same file concurrently.
- **TC5** — AST guard over every `monitoring/*.py`: no module-scope call to `logging.basicConfig` or
  to any `.addHandler(...)`, including inside module-level `if` / `try` blocks. This is the part that
  generalizes: a future `monitoring/` module cannot reintroduce the pattern.
- **TC6 / TC8** — positive path. Monkeypatch `bw.WATCHDOG_LOG_FILE` (resp. `wwd.LOG_FILE`) to a
  nested path under `tmp_path` that does not exist yet, call the configure function, emit one record,
  and assert the file exists and its line matches the exact
  `YYYY-MM-DD HH:MM:SS,mmm [LEVEL] message` shape. This locks format, level, and the mkdir behavior
  so a later refactor cannot silently change the operator's log shape. TC8 additionally asserts
  `wwd.logger.propagate is False` after the call.
- **TC7 / TC9** — idempotence: call the configure function twice, emit once, assert exactly one line
  in the file.

Every test that calls a configure function runs inside a fixture that snapshots and restores the root
logger's level and handler list and both named loggers' handlers, `propagate`, and `level`, and that
`close()`s any handler it created. This module mutates process-global logging state; leaking it would
corrupt sibling tests in the same xdist worker.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `monitoring/bridge_watchdog.py` `main()` has one `except Exception: # noqa: S110` around the
      `bridge.hibernation` import in the `--check-only` branch. It is outside this change's scope and
      is left untouched; `tests/unit/test_bridge_watchdog.py:781` and `:805` already assert the
      observable `Hibernating: True/False` output on both sides of it.
- [ ] `_configure_logging()` and `_configure_logger()` deliberately do **not** catch exceptions. If
      `logs/` is unwritable, the watchdog dies at startup and launchd retries in 60 s — the same
      behavior as today's import-time handler construction. A swallowed failure would produce a
      watchdog that runs and records nothing, which is the worst outcome for an alerting system.
      Documented in the docstring; no code change needed.

### Empty/Invalid Input Handling

- [ ] TC6 / TC8 point the configure functions at a **nested path under `tmp_path` that does not yet
      exist**, exercising `mkdir(parents=True, exist_ok=True)` on a fresh tree.
- [ ] Neither configure function takes user input; there is no empty-string or `None` surface. The
      only input is a `Path` constant.
- [ ] No agent-output processing in scope, so no empty-output loop risk.

### Error State Rendering

- [ ] `--check-only` renders its report with `print()` to stdout and does not depend on logging
      configuration at all. Verified by re-running `tests/unit/test_bridge_watchdog.py`'s five
      `main()` tests, which assert on `capsys` output.
- [ ] The four `caplog`-based assertions (`tests/unit/test_bridge_watchdog.py:753`, `:957`;
      `tests/integration/test_update_loop_wedge_recovery.py:156`, `:221`) are the error-state
      rendering checks for the watchdog's own warning and critical paths. They must keep passing
      unmodified — that is the proof that records still propagate to root when the process has not
      configured logging.

## Test Impact

- [ ] `tests/unit/test_worker_watchdog.py::TestLoggerConfiguration::test_logger_no_duplicate_handlers`
      — UPDATE: currently `importlib.reload(wwd); assert len(wwd.logger.handlers) == 1`. After the
      fix a reload attaches nothing, so it becomes: assert **zero** handlers after reload, then call
      `_configure_logger()` twice under a monkeypatched `LOG_FILE` and assert exactly one. Must
      remove and `close()` the handler in teardown.
- [ ] `tests/unit/test_worker_watchdog.py::TestLoggerConfiguration::test_logger_does_not_propagate`
      — UPDATE: assert `propagate is True` after a bare import (nothing configured) and `False` after
      `_configure_logger()`. Same monkeypatch + teardown requirement.
- [ ] `tests/unit/test_worker_watchdog.py::TestLoggerConfiguration::test_no_basicconfig_on_root`
      — REPLACE: the test body today is a docstring, an `importlib.reload`, and a comment, with **no
      assert statement at all**. It is a permanently-green gate of exactly the kind #2658 exists to
      eliminate, and it is worth noting that it "passed" throughout the period this bug was live.
      Replace with a real assertion: the root logger's handler list is unchanged across
      `importlib.reload(wwd)`.
- [ ] `tests/unit/test_worker_watchdog.py::test_healthy_tick_logs_info_line` (line 264) — VERIFY, no
      edit expected. It attaches `caplog.handler` to `wwd.logger` and calls `wwd.main()`. With
      configuration moved to the `__main__` guard, `main()` no longer clears handlers, so the
      attached handler survives. The owned-only clearing change is the belt to that braces.
- [ ] `tests/unit/test_bridge_watchdog.py` — VERIFY, no edit expected. The two `caplog` sites (753,
      957) keep working because nothing sets `propagate = False` at import; the five `main()` calls
      (544, 571, 598, 781, 805) are all `--check-only` and never reach `_configure_logging()`. Run
      the file and confirm rather than assuming.
- [ ] `tests/integration/test_update_loop_wedge_recovery.py` (lines 156, 221) — VERIFY, no edit
      expected. Same `caplog.at_level(..., logger="monitoring.bridge_watchdog")` reasoning.
- [ ] `tests/unit/test_recovery_respawn_safety.py::TestNoModuleLevelImports::test_bridge_watchdog_has_no_module_level_agent_session_import`
      — no change. It greps the source text for an unrelated `AgentSession` import.
- [ ] `tests/integration/test_notify_isolation.py`, `tests/unit/test_settings.py`,
      `tests/unit/test_nightly_regression_tests.py` — no change. They reference the module by name or
      import it without asserting on logging.

## Rabbit Holes

- **Re-litigating `worker_watchdog`'s handler topology.** Its named-logger + `propagate = False`
  shape is the deliberate #1311 outcome. Changing it to root configuration for symmetry with the
  bridge would newly pull submodule INFO records into `logs/worker_watchdog.log` — an unrequested
  content expansion in a second production log, for aesthetic consistency. Move the call site, stop.
- **Widening the AST guard beyond `monitoring/`.** `bridge/telegram_bridge.py:566` has the same
  module-scope `logging.basicConfig` call, and a repo-wide guard would fail on day one. That module
  belongs to a different workstream, and pulling it in converts a contained two-file fix into a
  cross-cutting migration. The guard is scoped to `monitoring/*.py`, where it passes cleanly the
  moment this change lands.
- **A session-scoped autouse fixture that asserts `logs/watchdog.log` never grows during a test
  session.** Tempting as a suite-wide guarantee, but it asserts on a file shared by every concurrent
  agent working in this checkout, so it would flake for reasons unrelated to the code under test, and
  it fails at session teardown where the failure is hardest to attribute.
- **An env-var override for the log path.** The issue floated it. A module-level `Path` constant that
  tests `monkeypatch` gets the same result with no new configuration surface, no `.env` entry, and no
  `config/settings.py` field.
- **Cleaning the historical synthetic lines out of `logs/watchdog.log`.** Explicitly forbidden by the
  issue's acceptance criteria and by this plan's No-Gos. The file is evidence.

## Risks

### Risk 1: Narrowing the bridge log by mirroring `propagate = False`
**Impact:** Records from `bridge.hibernation`, `monitoring.crash_tracker`, and
`scripts.update.service` stop appearing in `logs/watchdog.log`. An incident responder loses the
hibernation and crash-tracker context that sits between the watchdog's own lines, and nobody notices
until the next incident.
**Mitigation:** Configure the **root** logger in the bridge watchdog's entry point rather than the
module logger. Evidence this matters: the `[INFO] [hibernation] Bridge hibernating: auth required...`
line in the current log originates at `bridge/hibernation.py:100`.

### Risk 2: `caplog` assertions break
**Impact:** Four existing tests fail, and the temptation is to "fix" them by attaching handlers
directly, which is how `tests/unit/test_worker_watchdog.py:274` already had to be written.
**Mitigation:** Never touch `propagate` at import time. The module logger keeps the default
`propagate = True` with no handlers, so under pytest its records reach the root handler `caplog`
installs. `propagate = False` is set only inside the worker's `_configure_logger()`, which tests do
not call except under an explicit monkeypatch + teardown.

### Risk 3: The new tests leak logging state into sibling tests
**Impact:** A handler left attached to root by TC6 writes every subsequent test's log records into a
`tmp_path` file that has been deleted, or worse, the root level is left at INFO and floods other
tests' captured output. Failures appear far from the cause.
**Mitigation:** A fixture that snapshots and restores root's level and handler list plus both named
loggers' handlers, `propagate`, and `level`, and `close()`s every handler it created. Verified by
running the new module together with `tests/unit/test_bridge_watchdog.py` in one process.

### Risk 4: Logger-name divergence between script mode and import mode
**Impact:** Under launchd, `__name__` is `"__main__"`, so `getLogger(__name__)` returns a different
logger than the one tests address. If `_configure_logging()` configured a hard-coded name while the
module logged through `getLogger(__name__)`, production would silently log to an unconfigured logger
and `logs/watchdog.log` would go empty — a catastrophic, quiet failure of the alerting system.
**Mitigation:** Pin the explicit name `"monitoring.bridge_watchdog"` in the single module-level
`getLogger` call, and have `_configure_logging()` never construct its own logger reference. The
format string carries no `%(name)s`, so pinning the name changes nothing about the output.

### Risk 5: The red demo contaminates the production log it is trying to protect
**Impact:** Running the unfixed test suite in the main checkout to demonstrate the bug writes fresh
synthetic CRITICAL lines into `logs/watchdog.log` — reproducing the exact harm the issue is about.
**Mitigation:** The entire demonstrated-red protocol runs inside
`.worktrees/watchdog-import-time-log-handler/`, whose `logs/watchdog.log` is a separate file. The
main checkout's `logs/watchdog.log` is read-only for the duration of this work.

### Risk 6: A concurrent agent's test run makes a byte-count assertion flake
**Impact:** The `wc -c` growth check is measured against a file another process may be appending to,
producing a red demo that is not attributable to the code under test.
**Mitigation:** The byte-count measurement is confined to the worktree (single-agent) and is used
only as PR-body evidence. The durable in-suite gates (TC1-TC4) assert on handler state, which no
other process can perturb.

## Race Conditions

### Race 1: Shared log file during measurement
**Location:** `logs/watchdog.log` — read by the red/green demo commands, written by any concurrent
pytest process in the same checkout.
**Trigger:** Another agent runs a test that imports `monitoring.bridge_watchdog` between the demo's
two `wc -c` calls.
**Data prerequisite:** none — this is a measurement hazard, not a correctness hazard in the shipped
code.
**State prerequisite:** the demo must be the only writer of the file it measures.
**Mitigation:** run the demo in the worktree, whose `logs/` is its own directory; keep the durable
regression gates on handler state rather than byte counts.

No race conditions exist in the shipped change itself. `_configure_logging()` and
`_configure_logger()` run once, synchronously, in the entry-point process before any thread or
subprocess is created. `RotatingFileHandler` rollover across the bridge and worker watchdogs is
unchanged: they target different files, and each is a single short-lived process per launchd tick.

## No-Gos (Out of Scope)

- `[DESTRUCTIVE]` **No modification of existing `logs/` content.** `logs/watchdog.log` and
  `logs/worker_watchdog.log` are production evidence and the record that attributed the #2418
  synthetic lines. Deleting, truncating, rewriting, or filtering them is irreversible, and reviewing
  before executing is the only safety mechanism. Acceptance criterion 5 of #2643 says the fix is at
  the write site. Anti-criterion row in Verification asserts no file under `logs/` appears in the
  PR's diff.

Nothing else is deferred. Both watchdog modules, their tests, the new guard, and the docs are all in
scope for this plan.

## Update System

No update system changes required. The change is two source files and one new test module, all
propagated by the ordinary `git pull` in `scripts/remote-update.sh`. Specifically:

- No new dependencies, config files, secrets, or `config/settings.py` fields.
- The launchd plists are unchanged. `scripts/valor-service.sh:599-635` (bridge, `StartInterval` 60)
  and `scripts/install_worker.sh:214-253` (worker, `StartInterval` 90) keep pointing
  `StandardOutPath` / `StandardErrorPath` at the same log files, and both still invoke the modules as
  scripts, so the `__main__` guard is reached on every tick. No reinstall needed.
- No migration for existing installations: the next launchd tick after the pull picks up the new code.
- Per this run's constraints the build does **not** restart any service. The watchdogs are re-executed
  from scratch every 60 / 90 s by launchd, so a pull is sufficient and a restart would be a no-op.

## Agent Integration

No agent integration required. Both watchdogs are launchd-run monitoring processes with no CLI entry
point in `pyproject.toml [project.scripts]`, no MCP surface, and no import path from
`bridge/telegram_bridge.py` or the worker. The agent's only interaction with this subsystem is
reading `logs/watchdog.log` — which is exactly the surface this change makes trustworthy, and it
needs no wiring.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/watchdog-log-isolation.md` — the invariant (watchdog logging is
      configured at the entry point, never at import), why the bridge configures root while the
      worker configures its own named logger, and how the `monitoring/` AST guard enforces it.
- [ ] Add a row for it to the `docs/features/README.md` index table.
- [ ] Update `docs/features/bridge-self-healing.md` — one sentence in the watchdog section noting
      that `logs/watchdog.log` is written only by a real entry-point invocation, so every line in it
      is a real event.

### Inline Documentation
- [ ] Docstring on `_configure_logging()` citing #2643 and stating the two constraints a future
      editor must not break: called from `__main__` only, and configures root so submodule records
      reach the operator's log.
- [ ] Update the `_configure_logger()` docstring in `monitoring/worker_watchdog.py` — it currently
      explains #1311; add the #2643 sentence about the call site having moved out of module scope.
- [ ] Comment in `tests/unit/test_watchdog_log_isolation.py` explaining why TC2 / TC4 assert on
      handler state rather than file size (concurrent-writer flake).

### Test Suite Index
- [ ] Add `tests/unit/test_watchdog_log_isolation.py` to the `tests/README.md` index with its feature
      marker.

## Success Criteria

- [ ] Running `tests/unit/test_bridge_watchdog.py` produces zero new lines in `logs/watchdog.log`
      (byte count identical before and after), demonstrated in the worktree.
- [ ] `import monitoring.bridge_watchdog` in a fresh interpreter leaves `logging.getLogger().handlers`
      empty and opens no file.
- [ ] `import monitoring.worker_watchdog` in a fresh interpreter opens no file.
- [ ] `python monitoring/bridge_watchdog.py --check-only` still writes to `logs/watchdog.log` with
      format `%(asctime)s [%(levelname)s] %(message)s`, level INFO, 10 MB × 5 rotation.
- [ ] `monitoring/worker_watchdog.py` keeps 5 MB × 3 rotation and `propagate = False` after its
      entry-point configuration runs.
- [ ] All of `tests/unit/test_bridge_watchdog.py` passes with **no edits to that file**.
- [ ] The three `TestLoggerConfiguration` tests in `tests/unit/test_worker_watchdog.py` are updated
      and pass, and `test_no_basicconfig_on_root` now contains a real assertion.
- [ ] Demonstrated-red evidence is in the PR body: red command + verbatim failing output, green
      command + verbatim passing output, and a per-guard mutation check.
- [ ] No file under `logs/` appears in the PR diff.
- [ ] Tests pass (`/do-test`, scoped to the four affected test files)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (watchdog-logging)**
  - Name: `watchdog-logging-builder`
  - Role: Write the failing tests first, then the two-module fix, then the doc updates
  - Agent Type: builder
  - Resume: true

- **Validator (watchdog-logging)**
  - Name: `watchdog-logging-validator`
  - Role: Verify the red/green evidence is real, run the per-guard mutation check, confirm no
    production log was touched
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Write the isolation tests and demonstrate them RED

- **Task ID**: `build-red-tests`
- **Depends On**: none
- **Validates**: `tests/unit/test_watchdog_log_isolation.py` (create)
- **Assigned To**: `watchdog-logging-builder`
- **Agent Type**: builder
- **Parallel**: false
- Create the worktree at `.worktrees/watchdog-import-time-log-handler/` on branch
  `session/watchdog-import-time-log-handler`, provision its `.venv` on the pinned interpreter, and
  `export PYTHONPATH=$PWD` in every shell.
- Write `tests/unit/test_watchdog_log_isolation.py` with TC1-TC9 as specified in Technical Approach,
  including the snapshot/restore logging fixture.
- Run the red demo and capture output **verbatim** for the PR body:
  ```bash
  cd /Users/tomcounsell/src/ai/.worktrees/watchdog-import-time-log-handler
  export PYTHONPATH=$PWD
  ./scripts/pytest-clean.sh tests/unit/test_watchdog_log_isolation.py -n0 -q
  ```
  Expect TC1-TC5 and TC7 to FAIL against the unfixed source. If any of them passes, the test does not
  reach the defect — fix the test before touching the source.
- Run the second red probe, the issue's own measurement, against the **worktree's** log file:
  ```bash
  mkdir -p logs && touch logs/watchdog.log
  wc -c logs/watchdog.log
  ./scripts/pytest-clean.sh tests/unit/test_bridge_watchdog.py -n0 -q
  wc -c logs/watchdog.log
  ```
  Expect the byte count to grow. Record both numbers and a sample of the synthetic lines.
- Commit the tests alone, red, with the captured output in the commit message.

### 2. Fix `monitoring/bridge_watchdog.py`

- **Task ID**: `build-bridge-fix`
- **Depends On**: `build-red-tests`
- **Validates**: `tests/unit/test_watchdog_log_isolation.py`, `tests/unit/test_bridge_watchdog.py`,
  `tests/integration/test_update_loop_wedge_recovery.py`
- **Assigned To**: `watchdog-logging-builder`
- **Agent Type**: builder
- **Parallel**: false
- Delete lines 78-93; add `WATCHDOG_LOG_FILE`, the pinned-name `logger`, and `_configure_logging()`
  per Technical Approach.
- Wire `_configure_logging()` into the `__main__` guard only.
- Run `tests/unit/test_bridge_watchdog.py` and confirm it passes **with no edits to that file**.
- Commit.

### 3. Fix `monitoring/worker_watchdog.py`

- **Task ID**: `build-worker-fix`
- **Depends On**: `build-bridge-fix`
- **Validates**: `tests/unit/test_watchdog_log_isolation.py`, `tests/unit/test_worker_watchdog.py`
- **Assigned To**: `watchdog-logging-builder`
- **Agent Type**: builder
- **Parallel**: false
- Move the `_configure_logger()` call to the `__main__` guard; bind the module-level `logger` to the
  pinned name; change handler clearing to owned-only with `close()`.
- Update the three `TestLoggerConfiguration` tests per Test Impact, including giving
  `test_no_basicconfig_on_root` a real assertion.
- Commit.

### 4. Green demo, mutation check, and real-invocation proof

- **Task ID**: `validate-watchdog-logging`
- **Depends On**: `build-worker-fix`
- **Assigned To**: `watchdog-logging-validator`
- **Agent Type**: validator
- **Parallel**: false
- Re-run both red commands verbatim; capture the green output. The byte count must be **identical**
  before and after `tests/unit/test_bridge_watchdog.py`.
- Real-invocation proof, in the worktree: `python monitoring/bridge_watchdog.py --check-only`, then
  confirm `logs/watchdog.log` grew and the new lines carry the unchanged
  `YYYY-MM-DD HH:MM:SS,mmm [LEVEL] ` prefix.
- Per-guard mutation check (#2658 discipline, and the "mutation-check each guard" lesson): one at a
  time, reintroduce (a) `logging.basicConfig(...)` at module scope, (b) the module-scope
  `addHandler`, (c) a module-scope `_configure_logger()` call in the worker. After each, confirm the
  specific test that catches it flips red, then revert. Record the mutation → test mapping in the PR
  body. A mutation no test catches means the guard is decorative.
- Confirm `git diff --name-only main...HEAD` contains no path under `logs/`.
- Confirm the main checkout's `logs/watchdog.log` size and mtime are unchanged from the start of the
  work.

### 5. Documentation

- **Task ID**: `document-feature`
- **Depends On**: `validate-watchdog-logging`
- **Assigned To**: `watchdog-logging-builder`
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/watchdog-log-isolation.md`; add the `docs/features/README.md` row; add the
  sentence to `docs/features/bridge-self-healing.md`; add the test module to `tests/README.md`.
- Commit.

### 6. Final validation

- **Task ID**: `validate-all`
- **Depends On**: `document-feature`
- **Assigned To**: `watchdog-logging-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table.
- Confirm each Success Criteria checkbox against evidence, not intent.
- Assemble the PR body: red command + verbatim output, green command + verbatim output, mutation →
  test mapping, and the byte-count pair.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No `basicConfig` anywhere in `monitoring/` | `grep -rn "logging.basicConfig" monitoring/ \| wc -l` | match count == 0 |
| No module-scope `addHandler` in the watchdogs | `grep -c "^logger.addHandler\|^_watchdog_file_handler" monitoring/bridge_watchdog.py` | match count == 0 |
| No module-scope configure call in the worker | `grep -c "^logger = _configure_logger()" monitoring/worker_watchdog.py` | match count == 0 |
| Isolation tests pass | `PYTHONPATH=$PWD ./scripts/pytest-clean.sh tests/unit/test_watchdog_log_isolation.py -n0 -q` | exit code 0 |
| Bridge watchdog tests pass unmodified | `PYTHONPATH=$PWD ./scripts/pytest-clean.sh tests/unit/test_bridge_watchdog.py -n0 -q` | exit code 0 |
| Worker watchdog tests pass | `PYTHONPATH=$PWD ./scripts/pytest-clean.sh tests/unit/test_worker_watchdog.py -n0 -q` | exit code 0 |
| Wedge-recovery caplog tests pass | `PYTHONPATH=$PWD ./scripts/pytest-clean.sh tests/integration/test_update_loop_wedge_recovery.py -n0 -q` | exit code 0 |
| Import leaves root untouched | `PYTHONPATH=$PWD python -c "import logging; h=list(logging.getLogger().handlers); import monitoring.bridge_watchdog; assert logging.getLogger().handlers == h == [], logging.getLogger().handlers; print('ROOT_CLEAN')"` | output contains ROOT_CLEAN |
| Import opens no watchdog log file | `PYTHONPATH=$PWD python -c "import logging; import monitoring.bridge_watchdog, monitoring.worker_watchdog; hs=[h for lg in list(logging.root.manager.loggerDict.values())+[logging.getLogger()] if hasattr(lg,'handlers') for h in lg.handlers if hasattr(h,'baseFilename')]; assert not hs, hs; print('NO_FILE_HANDLERS')"` | output contains NO_FILE_HANDLERS |
| Bridge rotation settings preserved | `grep -c "maxBytes=10 \* 1024 \* 1024" monitoring/bridge_watchdog.py` | output > 0 |
| Bridge backup count preserved | `grep -c "backupCount=5" monitoring/bridge_watchdog.py` | output > 0 |
| Worker rotation settings preserved | `grep -c "maxBytes=5 \* 1024 \* 1024" monitoring/worker_watchdog.py` | output > 0 |
| Log format preserved in both | `grep -c "%(asctime)s \[%(levelname)s\] %(message)s" monitoring/bridge_watchdog.py monitoring/worker_watchdog.py \| grep -c ":0"` | match count == 0 |
| `--check-only` still works | `PYTHONPATH=$PWD python monitoring/bridge_watchdog.py --check-only` | output contains Restart circuit open |
| Anti-criterion: no log file in the diff | `git diff --name-only main...HEAD \| grep -c "^logs/"` | match count == 0 |
| Feature doc exists | `test -f docs/features/watchdog-log-isolation.md && echo DOC_OK` | output contains DOC_OK |
| Feature doc indexed | `grep -c "watchdog-log-isolation.md" docs/features/README.md` | output > 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Terminal echo for manual runs.** The plan attaches no `StreamHandler`, so
   `python monitoring/bridge_watchdog.py` (no flags) in a terminal prints nothing and writes only to
   `logs/watchdog.log`. The alternative is a `StreamHandler` attached only when `sys.stderr.isatty()`
   — under launchd stderr is a file, so no duplication, and a human keeps the current experience. Is
   the conditional worth the environment-dependent behavior, or is "read the log file" fine given
   `--check-only` already prints its report to stdout?
2. **Durable suite-level gate.** The in-suite gates assert on handler state (race-free); the
   "run the watchdog tests, assert the log did not grow" measurement lives only in the PR body as
   demonstrated-red evidence. Should a durable version of that measurement exist — e.g. a test that
   shells out to pytest for the single file and diffs the byte count — accepting both the ~20 s cost
   and the concurrent-writer flake risk? Current answer: no, the handler-state gates plus the AST
   guard cover the mechanism.
3. **Root-vs-named asymmetry between the two watchdogs.** The bridge configures root (to preserve the
   submodule records its log carries today); the worker keeps its #1311 named-logger topology. Both
   end up with zero import-time side effects, which is the invariant the guard enforces. Is that
   asymmetry acceptable, or is uniformity worth losing the `[hibernation]` and crash-tracker lines
   from `logs/watchdog.log`?
