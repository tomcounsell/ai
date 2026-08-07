---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-07
revised: 2026-08-07
revision_applied: true
revision_applied_at: 2026-08-07T17:41:01Z
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

None of it happened. This machine runs no `com.valor.bridge` and no `com.valor.bridge-watchdog`.
Those lines were emitted by `tests/unit/test_bridge_watchdog.py` running in this checkout an hour
earlier.

The module docstring is explicit that `logs/watchdog.log` plus `log_crash()` *is* the alerting
system: "The watchdog does NOT push a notification anywhere. It records; it does not deliver."
When the record is half fiction, the alerting system is broken. This already cost real time: the
#2418 investigation read 36 circuit-open timestamps spread across 11 days as a recurring production
livelock, and only a cross-reference against `data/crash_history.jsonl` and `launchctl list`
established that everything after 2026-07-27 was test output.

**Current behavior:**

Three side effects fire when `monitoring/bridge_watchdog.py` is imported, before any function runs:

- `monitoring/bridge_watchdog.py:78-81` — `logging.basicConfig(level=INFO, format=...)` mutates the
  **root** logger for the whole importing process.
- `monitoring/bridge_watchdog.py:86` — `LOGS_DIR.mkdir(parents=True, exist_ok=True)` creates `logs/`.
- `monitoring/bridge_watchdog.py:87-93` — a `RotatingFileHandler(logs/watchdog.log, 10 MB,
  backupCount=5)` is constructed (opening the file) and attached to the module logger.

Two files import the module for real — `tests/unit/test_bridge_watchdog.py:8` and
`tests/integration/test_update_loop_wedge_recovery.py:26` — and between them they carry the entire
recovery-ladder and wedge-detection suite. Every pytest process that collects either file turns the
handler live, and any test exercising the recovery ladder writes genuine-looking CRITICAL lines into
the operator's incident log. The nightly `com.valor.nightly-tests` job does this on a schedule.

`test_bridge_watchdog.py:1106` and `:1114` additionally call `importlib.reload(bw)`, and the bridge's
module-scope `addHandler` has no idempotent clearing, so handlers **stack**. The current file shows
the consequence directly: 369 lines, of which 153 are unique and 72 appear at exactly 3x (one initial
import plus two reloads).

`monitoring/worker_watchdog.py:162` has the same root shape: `logger = _configure_logger()` runs at
module scope, so importing it opens `logs/worker_watchdog.log`. Its own tests reload the module at
five sites (lines 47, 52, 58, 1050, 1058), re-opening the production file each time.

The rotating handler is capped at 10 MB × 5 backups, so synthetic bursts also consume the retention
budget and roll genuine incident history out early.

**Desired outcome:**

Importing either watchdog module has zero logging side effects: no handler on root, no handler
holding a production log file, no directory created. Configuration happens exactly once, at the
script entry point, where launchd and a human `python monitoring/bridge_watchdog.py --check-only`
both reach it. `logs/watchdog.log` keeps the same path, formatter, level, and rotation settings it
has today.

## Freshness Check

**Baseline commit:** `1d1830fc4` (re-verified at revision time; original baseline `3605ff847`)
**Issue filed at:** 2026-08-07T06:19:13Z
**Disposition:** Unchanged (code), **Minor drift (this plan's own citations — corrected below)**

**File:line references re-verified:**

- `monitoring/bridge_watchdog.py:78-81` — `logging.basicConfig(...)` at module scope — still holds,
  exact lines. Verified empirically: a fresh interpreter reports `logging.getLogger().handlers == []`
  and `level == 30` before the import.
- `monitoring/bridge_watchdog.py:86-93` — `LOGS_DIR.mkdir()` + `RotatingFileHandler` + `addHandler` —
  still holds. A scope-aware AST scan of `monitoring/*.py` reports module-scope hits at exactly
  `78 logging.basicConfig`, `86 LOGS_DIR.mkdir`, `93 logger.addHandler` and **nowhere else in the
  package**.
- `monitoring/worker_watchdog.py:135-160` — `_configure_logger()` reference pattern — still holds.
- `monitoring/worker_watchdog.py:162` — `logger = _configure_logger()` at module scope — still holds.
- `monitoring/bridge_watchdog.py:1041-1042` — `if __name__ == "__main__": sys.exit(main())` — the
  only entry-point guard; `main()` is at `:968`.
- `monitoring/worker_watchdog.py` — `if __name__ == "__main__": main()` at end of file.

**Corrections to this plan's round-1 citations (the round-1 BLOCKER):**

- The `[INFO] [hibernation] Bridge hibernating: auth required...` line in `logs/watchdog.log` is
  emitted by **`monitoring/bridge_watchdog.py:869-871`** (`logger.info`), not by
  `bridge/hibernation.py`. The hibernation module's own line is at **`bridge/hibernation.py:99`**, is
  `logger.error`, and has different wording ("...to authenticate, then..."). The round-1 plan cited
  `bridge/hibernation.py:100` as proof that submodule records reach `logs/watchdog.log` through root
  propagation. That proof is **refuted**, and the decision it supported is reversed below.
- "Eight modules import it, six of them tests" was a name-mention count, not an import count. The
  correct probe is a `from monitoring.` / `import monitoring.` line match. Real importers:
  `monitoring.bridge_watchdog` ← `tests/unit/test_bridge_watchdog.py`,
  `tests/integration/test_update_loop_wedge_recovery.py`. `monitoring.worker_watchdog` ←
  `tests/unit/test_worker_watchdog.py`, `tests/integration/test_watchdog_recovery.py:67`. Four files
  total, all tests. The last of those four appeared in no round-1 section and is now in Test Impact.

**Cited sibling issues/PRs re-checked:**

- **#2418** — closed (not planned). The investigation that surfaced this. Its attribution work is the
  reason we know the alarming lines are synthetic.
- **#2396 / PR #2414** — merged. Introduced `restart_circuit_open` and the CRITICAL line that is now
  the most-forged line in the log. Nothing to revisit; the line itself is correct.
- **#1311 / PR #1315** — closed 2026-05-07. Fixed the worker watchdog's *duplicate-write* problem and
  produced the `_configure_logger()` shape this plan now adopts on both sides. It left the call at
  module scope, which is the defect still open here.
- **#2658** — the demonstrated-red discipline this plan is a cited instance of. Governs every guard
  below.

**Commits on main since issue was filed (touching referenced files):** none.
`git log --since=2026-08-07T06:19:13Z -- monitoring/bridge_watchdog.py monitoring/worker_watchdog.py
tests/unit/test_bridge_watchdog.py tests/unit/test_worker_watchdog.py` is empty.

**Active plans in `docs/plans/` overlapping this area:** none. Nothing else touches `monitoring/`.

**Bug still reproducible:** yes. `logs/watchdog.log` carries a synthetic burst at
`2026-08-07 16:41:52`–`16:43:01`. Baseline for the No-Go gate:
`sha256 = 2c3c2f2d467de6d3d00f59c39469760548dd96de221b3e07808fca7792df89de`, 41322 bytes,
mtime `Aug 7 16:43`.

## Prior Art

- **#1311 / PR #1315** — "worker-watchdog observes but never recovers". Its logging half replaced
  `basicConfig` + a propagating named logger with `_configure_logger()`: named logger,
  `propagate = False`, one rotating handler, no `basicConfig`. Its docstring names the defect this
  plan now fixes on the bridge side verbatim, including "The plist also redirects stdout/stderr to
  the same log file, compounding the duplication." **It is the direct reference for this work** — but
  it configured at module scope, so it did not stop import-time file opening. This plan finishes that
  thought and extends the topology to the bridge.
- **PR #2414 (#2396)** — split the crash-count signal from the action level and added
  `restart_circuit_open`. Relevant only because it authored the CRITICAL line that dominates the
  forged output. No change needed.
- No prior issue or PR has attempted to stop watchdog test output reaching the production log.

## Research

**Queries used:**

- `Python logging cookbook library should not configure logging basicConfig at import time entry point only`

**Key findings:**

- The stdlib docs state the idiom directly: `logging.basicConfig(...)` belongs inside `main()` under
  `if __name__ == '__main__':`, while library modules do only `logger = logging.getLogger(__name__)`
  and emit records. Configuration is a separate concern from emission.
  Source: [Logging HOWTO](https://docs.python.org/3/howto/logging.html),
  [Logging Cookbook](https://docs.python.org/3/howto/logging-cookbook.html).
  *Informs the plan:* the fix is not a pytest fixture or a filter, it is relocating configuration to
  the entry point.
- "In most cases only the root logger needs configuring, since lower-level module loggers forward
  messages to its handlers." Source: [logging module reference](https://docs.python.org/3/library/logging.html).
  *Round-1 read this as a mandate to configure root here. That read is withdrawn.* The sentence
  describes the common case of a process whose stdout is the log destination. This process's stdout
  and stderr are **already redirected into the very file the handler writes**, which makes root
  configuration a duplication engine rather than a convenience. See Data Flow.
- `logging.lastResort` is a `_StderrHandler` at level **WARNING**, active whenever a record reaches a
  logger with no handlers anywhere in its ancestry. Verified empirically: in a fresh interpreter with
  an empty root, `logging.getLogger("some.submodule").warning(...)` prints the bare message to
  stderr, `.info(...)` prints nothing.
  *Informs the plan:* this is what preserves submodule visibility under `propagate = False`. Because
  the plist redirects stderr into `logs/watchdog.log`, submodule records at WARNING and above still
  land in the operator's log after this change — unformatted, but present. Only submodule
  INFO/DEBUG is lost.
- Libraries that must stay silent when unconfigured attach a `NullHandler`. *Not needed here:* these
  modules are entry points that happen to be importable, and `lastResort` is the documented default.

## Data Flow

### The doubling, stated precisely

`scripts/valor-service.sh:626-630` points **both** `StandardOutPath` and `StandardErrorPath` of the
`com.valor.bridge-watchdog` plist at `${LOG_DIR}/watchdog.log`. Combined with today's module-scope
configuration, a single launchd tick produces:

| Record source | Path to the file today | Copies today | Copies after this change |
|---|---|---|---|
| `monitoring.bridge_watchdog` own records | RotatingFileHandler **and** root StreamHandler → stderr → plist | **2** | **1** (RotatingFileHandler) |
| Submodule records at WARNING+ (`monitoring.crash_tracker`, `scripts.update.service`, …) | root StreamHandler → stderr → plist | 1 (formatted) | 1 (via `lastResort`, **unformatted**) |
| Submodule records at INFO/DEBUG | root StreamHandler → stderr → plist | 1 | **0** |
| Uncaught interpreter tracebacks | interpreter → stderr → plist | 1 | 1 (**unchanged**) |

**Control experiment for the "1 copy" column.** `com.valor.worker-watchdog` **is** installed and
running on this machine, under the same both-streams-to-one-file plist shape
(`scripts/install_worker.sh:243-246`), with `propagate = False` since #1311.
`logs/worker_watchdog.log` has 13,861 lines and a duplicate-multiplicity histogram of **entirely 1**
— zero duplication. That is the target topology proven clean in production under exactly the plist
shape in question.

**Honest limit on the evidence, stated rather than papered over.** `com.valor.bridge-watchdog` is
**not** installed on this machine (`launchctl list` and `~/Library/LaunchAgents/` show only
`com.valor.worker-watchdog`; the bridge is deliberately inactive here). `logs/watchdog.log` therefore
contains **zero launchd-produced lines** — all 369 are test pollution, with a multiplicity histogram
of 153 unique plus 72 lines at exactly 3x (reload stacking, not plist doubling). **No claim of the
form "no submodule record reaches `logs/watchdog.log` in production" can be supported from this
corpus, and this plan makes none.** The doubling in row 1 and the INFO loss in row 3 are derived from
the plist text and the logging semantics, not from this file's contents.

### Production path after the change

1. **Entry point**: launchd fires the plist every 60 s, running
   `.venv/bin/python monitoring/bridge_watchdog.py`.
2. **`__main__` guard**: `_configure_logging()` runs — mkdir `logs/`, attach one
   `RotatingFileHandler(logs/watchdog.log, 10 MB, 5 backups)` with formatter
   `"%(asctime)s [%(levelname)s] %(message)s"` to the **module logger**
   `monitoring.bridge_watchdog`, which is already at INFO with `propagate = False`.
3. **`main()` → `run_health_check()`**: emits through the module logger straight into the file.
4. **Output**: exactly one line per record.

### Test path after the change

1. pytest collects `tests/unit/test_bridge_watchdog.py`.
2. **Import**: module scope binds three inert attributes (`logger`, its level, its `propagate`) and
   two `Path` constants. No mkdir, no handler, no root mutation.
3. Test calls `run_health_check()` with mocked health; `logger.critical(...)` fires.
4. **Output**: the record reaches whatever handler the test attached (`caplog.handler`) and nothing
   else. `logs/watchdog.log` is not opened.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1315 (#1311) | Replaced `basicConfig` + propagating named logger in `monitoring/worker_watchdog.py` with `_configure_logger()`: named logger, `propagate = False`, one rotating handler, idempotent handler clearing | Correct diagnosis, correct topology, correct layer — but it kept the call at module scope. The comment it left ("Idempotent: clear any handlers attached by prior imports/test runs") shows the author saw test-run contamination and treated it as something to *survive* rather than *prevent*. Idempotent re-attachment still opens the production file on every import and reload. |
| No prior fix | `monitoring/bridge_watchdog.py` was never migrated to the #1311 shape at all | #1311 was scoped to the module whose log was visibly doubling. The bridge watchdog's log doubles for the identical reason, but the bridge is inactive on the machine where the work was done, so nobody saw it. |
| Round 1 of **this** plan | Proposed configuring the **root** logger at the bridge's entry point and explicitly rejected `propagate = False` | Its single cited justification — that `logs/watchdog.log` carries a `bridge.hibernation` record via root propagation — was false; that line is `bridge_watchdog.py:869`. With the citation removed, root configuration reduces to "keep writing every own-record twice," which is precisely the defect #1311 named. |

**Root cause pattern:** both modules are launchd *scripts* that are also *importable modules*, and
each one performs application-level logging configuration in the importable half. The repeated
failure is treating the symptom visible in one log file rather than the shared structural mistake:
configuration below the entry point.

## Architectural Impact

- **New dependencies**: none. Stdlib `logging` only.
- **Interface changes**: `monitoring/bridge_watchdog.py` gains a private `_configure_logging()` and a
  module-level `WATCHDOG_LOG_FILE` constant. `monitoring/worker_watchdog.py`'s `_configure_logger()`
  changes return type to `None` and its handler clearing to owned-only; its call site moves. Nothing
  public changes; nothing outside `monitoring/` and the four test files imports either module.
- **Behavior change (intentional, operator-visible)**: `logs/watchdog.log` stops carrying a duplicate
  of every watchdog record, and stops carrying submodule INFO/DEBUG. See Data Flow and Risk 1.
- **Coupling**: decreases. Importing a watchdog stops mutating process-global logging state.
- **Data ownership**: unchanged. Same paths, same rotation budgets.
- **Reversibility**: trivial. ~50 lines across two modules plus test edits and one new test file;
  reverting the commit restores prior behavior exactly.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No external dependencies: no API keys, no services, no new packages.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Pinned interpreter available | `python -c "import sys,pathlib; pin=pathlib.Path('.python-version').read_text().strip(); v='.'.join(map(str,sys.version_info[:2])); assert pin.startswith(v), (pin, v); print('PIN_OK')"` | `scripts/pytest-clean.sh` aborts on an off-pin venv |

Two setup steps are part of Task 1 rather than prerequisites, because the build creates the thing
they check:

- Provision `.worktrees/watchdog-import-time-log-handler/.venv` on the pinned interpreter. A missing
  worktree venv surfaces as a bogus pre-commit "lint" block with no findings listed.
- `export PYTHONPATH=$PWD` in every shell inside the worktree. The shared venv's `.pth` otherwise
  imports the main checkout, which would run the red demo against the production log.

## Solution

### The central decision: `propagate = False`, matching the sibling

**Both watchdogs converge on the #1311 topology**: a pinned-name module logger at INFO with
`propagate = False`, carrying exactly one `RotatingFileHandler`, attached at the entry point. Root is
never touched by either module.

Evidence, in order of weight:

1. **The plist doubles.** `scripts/valor-service.sh:626-630` sends both `StandardOutPath` and
   `StandardErrorPath` to `logs/watchdog.log`. Any root StreamHandler is therefore a second write
   path into the same file. Round 1's shape (`basicConfig` deleted, but the handler on a propagating
   logger with root configured) writes every watchdog record twice, exactly as #1311 described.
2. **The sibling already ruled.** `monitoring/worker_watchdog.py:136-148` names this defect verbatim
   and fixed it with `propagate = False`.
3. **The control is clean.** `com.valor.worker-watchdog` is live on this machine under the identical
   plist shape; 13,861 lines, multiplicity histogram entirely 1.
4. **Round 1's counter-evidence is refuted.** Its only cited submodule record traces to
   `bridge_watchdog.py:869`.
5. **Submodule WARNING+ survives anyway.** `logging.lastResort` (a WARNING-level stderr handler)
   still carries them into the file through the plist redirect. Verified empirically.

**What an operator will see change**, stated here and required to appear in the PR body as a
decision, not a footnote:

- One line where there used to be two, for every record the watchdog emits itself.
- Submodule INFO/DEBUG records disappear from `logs/watchdog.log`. Submodule WARNING/ERROR/CRITICAL
  still appear, but as bare messages without the `YYYY-MM-DD HH:MM:SS,mmm [LEVEL] ` prefix, because
  `lastResort` carries no formatter.
- Uncaught interpreter tracebacks still land in the file via the plist redirect, unchanged.

**Reading of acceptance criterion 3.** #2643's criterion 3 says a real invocation "still logs to
`logs/watchdog.log` with unchanged format, level, path, and rotation settings." This plan reads
"unchanged" as scoped to the four things the criterion names — formatter string, level, path,
rotation (10 MB × 5 for the bridge, 5 MB × 3 for the worker) — and **not** as a claim about line
multiplicity or about the set of loggers whose records reach the file. De-duplication is the point of
the change, not a violation of it. This reading is recorded here because the criterion is genuinely
ambiguous and silence would let a reviewer read it either way.

### Key Elements

- **`_configure_logging()` in `monitoring/bridge_watchdog.py`**: mkdir + rotating handler + formatter,
  attached to the module's own logger, called only from the `__main__` guard. Idempotent via an
  ownership tag.
- **Module-scope logger attributes stay put**: `logger = logging.getLogger("monitoring.bridge_watchdog")`,
  `logger.setLevel(logging.INFO)`, `logger.propagate = False`. These are pure in-memory attribute
  writes: they open no file, create no directory, and touch no root handler, so acceptance criterion
  2 is satisfied. `setLevel` in particular **must** stay at module scope — a logger left at NOTSET
  with no handlers inherits root's WARNING and silently **drops** every `logger.info(...)` record,
  which would break any `caplog` assertion that does not call `set_level`.
- **`monitoring/worker_watchdog.py` gains the same three module-scope lines** and **two body
  changes**: clearing becomes owned-only with `close()`, and the function returns `None`. Its call
  site moves to the `__main__` guard. The `LOG_FILE` module global is read **inside** the function
  body at call time and must stay that way.
- **`tests/unit/test_watchdog_log_isolation.py`**: subprocess import probes per module, a scope-aware
  AST guard over `monitoring/*.py`, and positive tests locking format, level, path, rotation,
  idempotence, and the non-propagating topology.
- **Existing test edits**: three `TestLoggerConfiguration` cases rewritten, and four `caplog` sites
  across two files converted to the explicit-handler pattern.

### Flow

`launchd fires (60 s)` → **`python monitoring/bridge_watchdog.py`** → `__main__` guard calls
`_configure_logging()` → **module logger has one rotating handler, `propagate = False`** → `main()` →
`run_health_check()` emits → **`logs/watchdog.log`, one line per record**

`pytest imports monitoring.bridge_watchdog` → **module scope sets three attributes and opens nothing**
→ test calls `run_health_check()` → record reaches the explicitly attached `caplog.handler` →
**`logs/watchdog.log` never opened**

### Technical Approach

**1. `monitoring/bridge_watchdog.py`**

Delete lines 78-93 outright — the `basicConfig` call, the `LOGS_DIR.mkdir()`, the
`_watchdog_file_handler` construction, and the `addHandler`. `logging.basicConfig(...)` is **deleted,
not relocated**: nothing in the new shape configures root.

Keep at module scope only inert bindings:

```python
LOGS_DIR = PROJECT_DIR / "logs"                      # constant; no mkdir
WATCHDOG_LOG_FILE = LOGS_DIR / "watchdog.log"        # monkeypatch seam, mirrors worker LOG_FILE

logger = logging.getLogger("monitoring.bridge_watchdog")
logger.setLevel(logging.INFO)
logger.propagate = False
```

Add:

```python
def _configure_logging() -> None:
    """Attach the rotating file handler. Call from __main__ ONLY (issue #2643).

    Two constraints a future editor must not break:
      * Called from the `__main__` guard, never at import and never from the top
        of `main()` — five tests call `main()` directly for `--check-only`.
      * `WATCHDOG_LOG_FILE` is read here, at call time, from the module global.
        Do not capture it in a default argument or a closure: tests monkeypatch it.

    No try/except: if `logs/` is unwritable the watchdog must die loudly and let
    launchd retry in 60 s. A watchdog that runs and records nothing is the worst
    outcome for an alerting system.
    """
    WATCHDOG_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    for h in [h for h in logger.handlers if getattr(h, "_watchdog_owned", False)]:
        logger.removeHandler(h)
        h.close()
    fh = logging.handlers.RotatingFileHandler(
        WATCHDOG_LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    fh._watchdog_owned = True
    logger.addHandler(fh)
```

Wire it into the guard only:

```python
if __name__ == "__main__":
    _configure_logging()
    sys.exit(main())
```

**The `__main__`-guard-vs-`main()` tension, resolved.** Round 1's reasoning survives intact and is
compatible with the new shape. `tests/unit/test_bridge_watchdog.py` calls `main()` directly at lines
544, 571, 598, 781, and 805 (all `--check-only`, asserting on `capsys`). Configuring at the top of
`main()` would reopen the production log from the test suite through a new door — the exact defect
this issue is about. The same argument applies to the worker: `tests/unit/test_worker_watchdog.py`
calls `wwd.main()` in several cases (e.g. `test_healthy_tick_logs_info_line`,
`test_main_returns_immediately_after_trip`). **Both modules configure in the `__main__` guard.** No
test that calls `main()` ends up opening a production log.

**Ownership tag, not a blanket sweep.** `logger.handlers` may legitimately contain a `caplog.handler`
that a test attached. `close()`ing or removing it would break pytest's capture for the rest of the
worker. The tag confines both operations to handlers this function created.

**2. `monitoring/worker_watchdog.py`**

Add the same three module-scope lines (pinned name, `setLevel(INFO)`, `propagate = False`), so a
reload or a bare import leaves the logger correctly leveled and non-propagating with zero handlers.

Change `_configure_logger()`:

- Signature becomes `-> None`; it configures the module-level `logger` object directly rather than
  re-`getLogger`-ing and returning it. This removes the last place where a name mismatch could
  matter.
- Keep `LOG_FILE.parent.mkdir(...)` and `RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024,
  backupCount=3)` reading the **module global `LOG_FILE` inside the body**, unchanged. This is the
  seam `tests/unit/test_worker_watchdog.py:34` monkeypatches; capturing it into a default argument or
  closure at import time would silently stop redirecting and send tests back to the real log.
- Replace the blanket clearing loop (`monitoring/worker_watchdog.py:150-152`) with the owned-only
  tag-and-`close()` loop.
- Extend the docstring with the #2643 sentence about the call site.

Delete `logger = _configure_logger()` at line 162; call `_configure_logger()` from the `__main__`
guard.

**Script-mode name pinning is airtight in both modules.** Under launchd `__name__ == "__main__"`, so
`getLogger(__name__)` would return a *different* logger than the one tests address and than the one
`_configure_logging()` configures — producing an **empty** `logs/watchdog.log` in production, a quiet
catastrophic failure. Both modules therefore bind the explicit string once at module scope and both
configure functions operate on that single module-level `logger` object, never constructing their
own reference. Verification greps for a surviving `getLogger(__name__)` in either file.

**3. `tests/unit/test_watchdog_log_isolation.py`** (new)

Every case that calls a configure function runs inside a fixture that snapshots and restores the root
logger's level and handler list plus both named loggers' handlers, `propagate`, and `level`, and
`close()`s every handler it created. This module mutates process-global logging state; leaking it
corrupts sibling tests in the same xdist worker.

| Case | Assertion | The exact statement whose reintroduction/removal it catches |
|---|---|---|
| **TC1** | Fresh subprocess: after `import monitoring.bridge_watchdog`, `logging.getLogger().handlers == []` **and** `logging.getLogger().level == logging.WARNING` | `logging.basicConfig(level=logging.INFO, format=...)` at `bridge_watchdog.py:78-81` |
| **TC2** | Same subprocess: no logger in `logging.root.manager.loggerDict` (plus root) holds a handler whose `baseFilename` ends `watchdog.log` | `logger.addHandler(_watchdog_file_handler)` at `:93` (and the handler construction at `:87-92`) |
| **TC3** | Fresh subprocess: after `import monitoring.worker_watchdog`, root handlers empty and root level WARNING | a `logging.basicConfig(...)` reintroduced in the worker |
| **TC4** | Same subprocess: no handler anywhere has `baseFilename` ending `worker_watchdog.log` | `logger = _configure_logger()` at `worker_watchdog.py:162` |
| **TC5** | Scope-aware AST walk over every `monitoring/*.py`: no module-scope call to `logging.basicConfig`, to any `.addHandler`, to any `.mkdir`, or to `_configure_logger` / `_configure_logging`. Descends into module-level `if`/`try`/`with` bodies; never enters `def`/`class` bodies | all three deleted bridge statements **and** the worker's module-scope configure call. Pre-verified: this walk reports exactly `78 logging.basicConfig`, `86 LOGS_DIR.mkdir`, `93 logger.addHandler` today and **nothing else anywhere in `monitoring/`**, so it passes cleanly the moment the fix lands |
| **TC6** | Fresh subprocess: `bw.logger.propagate is False` and `bw.logger.level == logging.INFO` and `bw.logger.isEnabledFor(logging.INFO)` after a bare import | deletion of `logger.propagate = False`; deletion of `logger.setLevel(logging.INFO)` |
| **TC7** | Monkeypatch `bw.WATCHDOG_LOG_FILE` → `tmp_path/"nested"/"watchdog.log"`; `bw._configure_logging()`; emit one INFO record; flush; assert the file exists and its single line matches `^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \[INFO\] probe$` | formatter-string change; removal of `parents=True` from the mkdir; a level regression |
| **TC8** | Same setup: after `_configure_logging()`, `logging.getLogger().handlers` is unchanged and holds no `_watchdog_owned` handler | attaching to root (`logging.getLogger().addHandler(fh)`) instead of the module logger — i.e. a relapse to round-1's shape |
| **TC9** | Call `bw._configure_logging()` twice, emit once, assert exactly one line in the file **and** exactly one `_watchdog_owned` handler on `bw.logger` | removal of the owned-only clearing loop |
| **TC10** | Worker mirror of TC7 + TC8 + `wwd.logger.propagate is False`, via `wwd.LOG_FILE` and `wwd._configure_logger()`; asserts `maxBytes == 5 * 1024 * 1024` and `backupCount == 3` on the attached handler | worker formatter/rotation regression; a root relapse in the worker |
| **TC11** | Worker mirror of TC9 (idempotence) | removal of the worker's owned-only clearing loop |
| **TC12** | Monkeypatch `bw.WATCHDOG_LOG_FILE`, attach a sentinel unowned `logging.NullHandler()` to `bw.logger`, call `_configure_logging()`, assert the sentinel is still attached and not closed | a relapse from owned-only clearing to a blanket `for h in list(logger.handlers)` sweep, which would strip a test's `caplog.handler` |

TC2 and TC4 deliberately assert on **handler state** rather than on the shared file's byte count, so
they cannot flake when another agent's test run appends to the same file concurrently.

**A guard that no mutation can make fail is removed, not kept.** Round 1's two inline-python
Verification rows re-implemented TC1-TC4 and are deleted (see Verification). Round 1's anti-criterion
`git diff | grep "^logs/"` row could never fire, because `.gitignore:168` (`logs/`) and `:378`
(`logs/*.log`) mean no path under `logs/` can ever appear in a diff — it would have returned 0 even
if the builder had truncated the file. It is replaced by a sha256 baseline check (see No-Gos).

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `monitoring/bridge_watchdog.py` `main()` has one `except Exception: # noqa: S110` around the
      `bridge.hibernation` import in the `--check-only` branch. Out of scope and left untouched;
      `tests/unit/test_bridge_watchdog.py:781` and `:805` already assert the observable
      `Hibernating: True/False` output on both sides of it.
- [ ] `_configure_logging()` and `_configure_logger()` deliberately do **not** catch exceptions. If
      `logs/` is unwritable, the watchdog dies at startup and launchd retries in 60 s — the same
      behavior as today's import-time handler construction. A swallowed failure would produce a
      watchdog that runs and records nothing. Documented in both docstrings; no code change needed.

### Empty/Invalid Input Handling

- [ ] TC7 / TC10 point the configure functions at a **nested path under `tmp_path` that does not yet
      exist**, exercising `mkdir(parents=True, exist_ok=True)` on a fresh tree.
- [ ] Neither configure function takes user input; the only input is a `Path` constant.
- [ ] No agent-output processing in scope, so no empty-output loop risk.

### Error State Rendering

- [ ] `--check-only` renders its report with `print()` to stdout and does not depend on logging
      configuration at all. Verified by re-running the five `main()` tests, which assert on `capsys`.
- [ ] The four `caplog` assertions (`tests/unit/test_bridge_watchdog.py:753`, `:957`;
      `tests/integration/test_update_loop_wedge_recovery.py:156`, `:221`) are the error-state
      rendering checks for the watchdog's warning and critical paths. Under `propagate = False` they
      **will fail unmodified** — this is measured, not assumed (see Test Impact) — and each is
      converted to the explicit-handler pattern.

## Test Impact

**Measured fact behind the `caplog` edits.** pytest's `caplog` handler is attached to the **root**
logger and relies on propagation; `caplog.at_level(..., logger="name")` sets the named logger's level
but does not attach anything to it. A standalone probe confirms: with `propagate = True` caplog sees
the record; with `propagate = False` caplog sees **nothing**; with `propagate = False` plus
`lg.addHandler(caplog.handler)` in a `try/finally`, caplog sees it again. The workaround is already
in-repo at `tests/unit/test_worker_watchdog.py:275` and nine sibling sites. Resolving this by leaving
`propagate = True` is not an option — that reinstates the doubling.

- [ ] `tests/unit/test_worker_watchdog.py::TestLoggerConfiguration::test_logger_no_duplicate_handlers`
      — UPDATE. Today: `importlib.reload(wwd); assert len(wwd.logger.handlers) == 1`. After the fix a
      reload attaches nothing, so the assert flips to **zero** handlers after reload; then, with
      `wwd.LOG_FILE` monkeypatched to `tmp_path` (take `isolated_state`), call `_configure_logger()`
      twice and assert exactly **one** owned handler. **Keep the handler-count and idempotence
      assertions** — they move to an explicit call, they are not deleted. Remove and `close()` the
      handler in teardown. This test is itself a writer to the production log today (it takes no
      `isolated_state`), so the edit is part of the fix, not collateral.
- [ ] `tests/unit/test_worker_watchdog.py::TestLoggerConfiguration::test_logger_does_not_propagate`
      — UPDATE. Add `isolated_state`. Assert `propagate is False` after `importlib.reload(wwd)` (it
      is now a module-scope statement, so reload re-establishes it) **and** still `False` after
      `_configure_logger()`. Same teardown requirement.
- [ ] `tests/unit/test_worker_watchdog.py::TestLoggerConfiguration::test_no_basicconfig_on_root`
      — REPLACE, **with a correction to the round-1 critique's premise**. The test is not
      assert-less: it ends in `assert wwd.logger.propagate is False`. The real defect is that this
      assert is a **verbatim duplicate of the previous test's**, so a case named
      `test_no_basicconfig_on_root`, documented as "Root logger should not have handlers attached by
      this module," never inspects root at all. It is a mis-aimed gate, and it "passed" throughout
      the period this bug was live.
      Replace with a gate that can actually fire **in-process**: snapshot
      `list(logging.getLogger().handlers)`, call `wwd._configure_logger()` under a monkeypatched
      `LOG_FILE`, and assert root's handler list is byte-identical and holds no `_watchdog_owned`
      handler. Mutation it catches: `logging.getLogger().addHandler(fh)` in place of
      `logger.addHandler(fh)`.
      **Why not assert on a reintroduced `basicConfig` here:** `logging.basicConfig()` is a no-op when
      root already has handlers, and under pytest root always does. An in-process basicConfig gate
      therefore cannot fire — the #2658 failure mode. That claim is carried by TC1/TC3, which run in
      a fresh subprocess with a genuinely empty root. Rename the test to
      `test_configure_logger_does_not_touch_root` and note the delegation in a comment.
- [ ] `tests/unit/test_worker_watchdog.py::test_heartbeat_threshold_env_override` (:1050) and
      `::test_heartbeat_threshold_default_is_180` (:1058) — VERIFY, no edit expected. Both
      `importlib.reload(wwd)` and assert only on `HEARTBEAT_THRESHOLD`. They re-open the real log on
      every reload today and stop doing so after the fix. Run and confirm rather than assume.
- [ ] `tests/unit/test_worker_watchdog.py` — the ten `wwd.logger.addHandler(caplog.handler)` sites
      (lines 275, 372, 402, 712, 732, 749, 773, 802, 826, 886) — VERIFY, no edit expected. They keep
      working, and the owned-only clearing change is what protects them from being swept.
- [ ] `tests/unit/test_bridge_watchdog.py::test_hibernating_logs_message` (caplog at :753) — UPDATE:
      wrap with `bw.logger.addHandler(caplog.handler)` / `removeHandler` in `try/finally`, matching
      `test_worker_watchdog.py:275`.
- [ ] `tests/unit/test_bridge_watchdog.py` caplog site at :957 (`[update-release]` CRITICAL) —
      UPDATE: same conversion.
- [ ] `tests/unit/test_bridge_watchdog.py` — the five `main()` calls (544, 571, 598, 781, 805) and
      the two `importlib.reload(bw)` sites (1106, 1114) — VERIFY, no edit expected. All five are
      `--check-only` asserting on `capsys` and never reach `_configure_logging()`; both reload sites
      assert only on `CRASH_STORM_THRESHOLD` / `WEDGE_DOMINANCE_FRACTION`.
- [ ] `tests/integration/test_update_loop_wedge_recovery.py` (caplog at :156 and :221) — UPDATE: same
      explicit-handler conversion against `monitoring.bridge_watchdog`'s logger.
- [ ] `tests/integration/test_watchdog_recovery.py` — VERIFY, no edit expected. It imports
      `monitoring.worker_watchdog` at `:67` and `:138` but uses no `caplog` against `wwd.logger`.
      Absent from round 1 entirely; added here.
- [ ] `tests/conftest.py:834` (`"bridge_watchdog": "monitoring"`) — no change, and **confirmed
      inert**. It is an entry in `FEATURE_MAP`, consumed by `pytest_collection_modifyitems` to attach
      a `monitoring` marker based on the *test file's name*. It performs no import.
- [ ] `tests/unit/test_recovery_respawn_safety.py::TestNoModuleLevelImports::test_bridge_watchdog_has_no_module_level_agent_session_import`
      — no change. It greps the source text for an unrelated `AgentSession` import.
- [ ] `tests/integration/test_notify_isolation.py`, `tests/unit/test_settings.py`,
      `tests/unit/test_nightly_regression_tests.py` — no change. They mention the module by name in a
      comment, docstring, or path literal and never import it.

**Acceptance criterion 4 is met, but round 1's stronger sub-claim is dropped.** #2643 requires that
`tests/unit/test_bridge_watchdog.py` still passes. It will. Round 1 additionally promised "with **no
edits** to that file"; that promise is incompatible with `propagate = False` and is withdrawn. Two
`caplog` sites in that file are edited, and the edits are the in-repo standard pattern.

## Rabbit Holes

- **Re-litigating the worker's *named-logger + `propagate = False`* topology.** That is the deliberate
  #1311 outcome and this plan now adopts it on the bridge too. The two body changes the plan *does*
  make to `_configure_logger()` — owned-only clearing with `close()`, and a `None` return — are
  required and specified; the topology itself is untouched. Do not convert either module to root
  configuration for any reason.
- **Widening the AST guard beyond `monitoring/`.** `bridge/telegram_bridge.py:566` has the same
  module-scope `logging.basicConfig` call, and a repo-wide guard would fail on day one. That module
  belongs to a different workstream. The guard is scoped to `monitoring/*.py`, where a scope-aware
  scan confirms it passes cleanly the moment this change lands.
- **A session-scoped autouse fixture asserting `logs/watchdog.log` never grows.** It asserts on a file
  every concurrent agent in this checkout may write, so it would flake for reasons unrelated to the
  code under test, and it fails at session teardown where attribution is hardest.
- **An env-var override for the log path.** A module-level `Path` constant that tests `monkeypatch`
  gets the same result with no new configuration surface, no `.env` entry, and no
  `config/settings.py` field.
- **Restoring submodule INFO to `logs/watchdog.log` by other means** (a second handler on root gated
  on `__main__`, a `logging.Filter`, a custom `lastResort`). If the lost INFO turns out to matter, the
  honest fix is a follow-up issue that names the specific submodule records an operator needs. Do not
  invent a mechanism inside this two-file change.
- **Cleaning the historical synthetic lines out of `logs/watchdog.log`.** Explicitly forbidden by
  acceptance criterion 5 and by this plan's No-Gos. The file is evidence.

## Risks

### Risk 1: The de-duplication is a real, operator-visible behavior change
**Impact:** An operator reading `logs/watchdog.log` after this ships sees one line where they used to
see two, and stops seeing submodule INFO/DEBUG entirely. Someone grepping for a count, or an eye
accustomed to the doubled shape, could read the change as lost data.
**Mitigation:** State it as a decision, not a side effect. The PR body carries the Data Flow table
verbatim, the explicit reading of acceptance criterion 3, and the note that submodule WARNING+ still
arrives via `logging.lastResort` → stderr → the plist redirect (unformatted), and that uncaught
tracebacks are unchanged. `docs/features/watchdog-log-isolation.md` records the same. Reversal is one
`git revert`.
**Residual, stated plainly:** `com.valor.bridge-watchdog` is not installed on this machine, so
`logs/watchdog.log` holds zero launchd lines and cannot be used to enumerate which submodule INFO
records production actually loses. The plan asserts only what the plist text and logging semantics
support.

### Risk 2: `caplog` assertions break under `propagate = False`
**Impact:** Four existing tests fail. The tempting "fix" is to flip `propagate` back to `True`, which
silently reinstates the doubling this change exists to remove.
**Mitigation:** Measured up front (see Test Impact), not discovered at build time. All four sites are
converted to `logger.addHandler(caplog.handler)` in a `try/finally`, the pattern already used at ten
sites in `tests/unit/test_worker_watchdog.py`. TC6 asserts `propagate is False` after a bare import,
so a future flip-back fails the suite rather than passing quietly.

### Risk 3: Dropping `basicConfig(level=INFO)` changes the process-global root level
**Impact:** Root returns to its WARNING default in every process that imports either module.
Propagation never consults ancestor logger *levels*, so the mechanism that actually matters is the
emitting logger's own effective level. A logger left at NOTSET with no handlers would inherit
WARNING and **discard** `logger.info(...)` at the call site — invisible to any `caplog` assertion
that does not call `set_level`.
**Mitigation:** `logger.setLevel(logging.INFO)` stays at module scope in both files, so the module
loggers are never at the mercy of root's level. TC6 asserts `isEnabledFor(logging.INFO)` after a bare
import. Any INFO assertion elsewhere must use `caplog.at_level(logging.INFO, logger="monitoring.…")`;
the rule is stated in both configure-function docstrings. Because the change removes a process-global
side effect from every xdist worker that imports either module, the build runs `tests/unit/` once in
full before the PR is opened, not only the affected files.

### Risk 4: The new tests leak logging state into sibling tests
**Impact:** A handler left attached by TC7 writes later tests' records into a deleted `tmp_path` file,
or a leaked level floods captured output. Failures appear far from the cause.
**Mitigation:** A fixture that snapshots and restores root's level and handler list plus both named
loggers' handlers, `propagate`, and `level`, and `close()`s every handler it created. Verified by
running the new module together with `tests/unit/test_bridge_watchdog.py` in one process.

### Risk 5: The red demo contaminates the production log it is trying to protect
**Impact:** Running the unfixed suite in the main checkout to demonstrate the bug writes fresh
synthetic CRITICAL lines into `logs/watchdog.log` — reproducing the exact harm.
**Mitigation:** The entire demonstrated-red protocol runs inside
`.worktrees/watchdog-import-time-log-handler/`, whose `logs/` is a separate directory. The main
checkout's `logs/watchdog.log` is read-only for the duration, enforced by the sha256 baseline gate.

### Risk 6: Logger-name divergence between script mode and import mode
Not an active hazard once the pinned name is in place, but the failure it prevents is severe: under
launchd `__name__ == "__main__"`, so a configure function that addressed a hard-coded name while the
module logged through `getLogger(__name__)` would produce an **empty** `logs/watchdog.log`. Both
modules bind the explicit string once at module scope and both configure functions operate on that
object, never constructing their own. Verification greps for a surviving `getLogger(__name__)`.

## Race Conditions

### Race 1: Shared log file during measurement
**Location:** `logs/watchdog.log` — read by the red/green demo commands, written by any concurrent
pytest process in the same checkout.
**Trigger:** another agent imports `monitoring.bridge_watchdog` between the demo's two measurements.
**Data prerequisite:** none — a measurement hazard, not a correctness hazard in the shipped code.
**State prerequisite:** the demo must be the only writer of the file it measures.
**Mitigation:** run the demo in the worktree, whose `logs/` is its own directory; keep the durable
regression gates on handler state rather than byte counts.

No race conditions exist in the shipped change itself. `_configure_logging()` and `_configure_logger()`
run once, synchronously, in the entry-point process before any thread or subprocess is created.
`RotatingFileHandler` rollover is unchanged: the two watchdogs target different files, and each is a
single short-lived process per launchd tick.

## No-Gos (Out of Scope)

- `[DESTRUCTIVE]` **No modification of existing `logs/` content.** `logs/watchdog.log` and
  `logs/worker_watchdog.log` are production evidence and the record that attributed the #2418
  synthetic lines. Deleting, truncating, rewriting, or filtering them is irreversible.
  **Gate:** at the start of Task 1, in the **main** checkout, record
  `shasum -a 256 logs/watchdog.log logs/worker_watchdog.log > /tmp/watchdog-log-baseline.sha`;
  Verification re-runs `shasum -a 256 -c /tmp/watchdog-log-baseline.sha` and requires `OK` on both.
  sha256 rather than `wc -c`, because a same-length rewrite passes a byte count. Main checkout rather
  than the worktree, because the worktree's `logs/` is deliberately written to by the demo. Round 1's
  `git diff --name-only | grep "^logs/"` row is **deleted**: `.gitignore:168` and `:378` make it
  return 0 unconditionally, including after a truncation — the permanently-green shape this plan
  condemns elsewhere.
- **No behavioral change to recovery, escalation, or health-check logic.** Only logging setup moves.
- **No new mechanism to restore submodule INFO records.** See Rabbit Holes.

Nothing else is deferred. Both watchdog modules, their tests, the new guard, and the docs are all in
scope.

## Update System

No update system changes required. The change is two source files, three test files, one new test
module, and docs — all propagated by the ordinary `git pull` in `scripts/remote-update.sh`.

- No new dependencies, config files, secrets, or `config/settings.py` fields.
- The launchd plists are unchanged. `scripts/valor-service.sh:599-635` (bridge, `StartInterval` 60)
  and `scripts/install_worker.sh:214-253` (worker, `StartInterval` 90) keep pointing
  `StandardOutPath` / `StandardErrorPath` at the same log files, and both still invoke the modules as
  scripts, so the `__main__` guard is reached on every tick. No reinstall needed.
- No migration for existing installations: the next launchd tick after the pull picks up the new code.
- Per this run's constraints the build does **not** restart any service. The watchdogs are re-executed
  from scratch every 60 / 90 s by launchd, so a pull is sufficient.
- **Operator note for the deploy:** the first tick after this lands changes what
  `logs/watchdog.log` looks like on any machine where the bridge watchdog is installed. Flag it in
  the deploy notes so nobody reads the halved line count as a stopped watchdog.

## Agent Integration

No agent integration required. Both watchdogs are launchd-run monitoring processes with no CLI entry
point in `pyproject.toml [project.scripts]`, no MCP surface, and no import path from
`bridge/telegram_bridge.py` or the worker. The agent's only interaction with this subsystem is
reading `logs/watchdog.log` — which is exactly the surface this change makes trustworthy, and it
needs no wiring.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/watchdog-log-isolation.md` — the invariant (watchdog logging is
      configured at the entry point, never at import), the shared `propagate = False` topology and
      why the plist's both-streams redirect forces it, the precise Data Flow table of what reaches
      the file before and after, and how the `monitoring/` AST guard enforces the invariant.
- [ ] Add a row for it to the `docs/features/README.md` index table.
- [ ] Update `docs/features/bridge-self-healing.md` — the watchdog section gains: every line in
      `logs/watchdog.log` now comes from a real entry-point invocation, and each record appears once.

### Inline Documentation
- [ ] Docstring on `_configure_logging()` citing #2643, stating the three constraints a future editor
      must not break: called from `__main__` only; `WATCHDOG_LOG_FILE` read from the module global at
      call time; no root configuration.
- [ ] Update the `_configure_logger()` docstring in `monitoring/worker_watchdog.py` — keep the #1311
      explanation, add the #2643 sentence about the call site moving out of module scope and the
      clearing becoming owned-only.
- [ ] Comment in `tests/unit/test_watchdog_log_isolation.py` explaining why TC2 / TC4 assert on
      handler state rather than file size (concurrent-writer flake), and why the basicConfig gates
      must run in a subprocess (basicConfig is a no-op when root already has pytest's handlers).

### Test Suite Index
- [ ] Add `tests/unit/test_watchdog_log_isolation.py` to the `tests/README.md` index with its feature
      marker.

## Success Criteria

- [ ] Running `tests/unit/test_bridge_watchdog.py` in the worktree produces zero new lines in that
      worktree's `logs/watchdog.log` (identical sha256 before and after).
- [ ] `import monitoring.bridge_watchdog` in a fresh interpreter leaves `logging.getLogger().handlers`
      empty, leaves root at WARNING, and opens no file.
- [ ] `import monitoring.worker_watchdog` in a fresh interpreter does the same.
- [ ] After a bare import, both module loggers report `propagate is False`, `level == logging.INFO`,
      and `isEnabledFor(logging.INFO) is True`.
- [ ] `python monitoring/bridge_watchdog.py --check-only` still writes to `logs/watchdog.log` with
      formatter `%(asctime)s [%(levelname)s] %(message)s`, level INFO, 10 MB × 5 rotation.
- [ ] `monitoring/worker_watchdog.py` keeps 5 MB × 3 rotation and `propagate = False` after its
      entry-point configuration runs.
- [ ] `tests/unit/test_bridge_watchdog.py` passes. Exactly two `caplog` sites in it are edited to the
      explicit-handler pattern; nothing else in the file changes.
- [ ] The three `TestLoggerConfiguration` tests are rewritten per Test Impact — handler-count,
      propagate, and idempotence assertions **retained** against explicit `_configure_logger()` calls
      under a monkeypatched `LOG_FILE` — and the mis-aimed `test_no_basicconfig_on_root` now asserts
      something about root that a mutation can break.
- [ ] `tests/integration/test_update_loop_wedge_recovery.py` and
      `tests/integration/test_watchdog_recovery.py` pass.
- [ ] The full `tests/unit/` suite passes once before the PR opens (Risk 3).
- [ ] Demonstrated-red evidence is in the PR body: red command + **verbatim** failing output for the
      new gates against unfixed source, green command + **verbatim** passing output after, and the
      full per-guard mutation → test mapping from Task 4.
- [ ] The PR body states the de-duplication decision explicitly, carrying the Data Flow table and the
      reading of acceptance criterion 3.
- [ ] `shasum -a 256 -c /tmp/watchdog-log-baseline.sha` reports `OK` for both main-checkout logs.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (watchdog-logging)**
  - Name: `watchdog-logging-builder`
  - Role: Write the failing tests first, then the two-module fix, then the test edits and docs
  - Agent Type: builder
  - Resume: true

- **Validator (watchdog-logging)**
  - Name: `watchdog-logging-validator`
  - Role: Verify the red/green evidence is real, run the per-guard mutation check, confirm no
    production log was touched
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Baseline, worktree, and RED isolation tests

- **Task ID**: `build-red-tests`
- **Depends On**: none
- **Validates**: `tests/unit/test_watchdog_log_isolation.py` (create)
- **Assigned To**: `watchdog-logging-builder`
- **Agent Type**: builder
- **Parallel**: false
- **First**, in the MAIN checkout:
  `shasum -a 256 logs/watchdog.log logs/worker_watchdog.log > /tmp/watchdog-log-baseline.sha`.
  Nothing else in this plan may run against the main checkout's `logs/`.
- Create the worktree at `.worktrees/watchdog-import-time-log-handler/` on branch
  `session/watchdog-import-time-log-handler`, provision its `.venv` on the pinned interpreter, and
  `export PYTHONPATH=$PWD` in every shell.
- Write `tests/unit/test_watchdog_log_isolation.py` with TC1-TC12 as tabulated in Technical Approach,
  including the snapshot/restore logging fixture.
- Run the red demo and capture output **verbatim** for the PR body:
  ```bash
  cd /Users/tomcounsell/src/ai/.worktrees/watchdog-import-time-log-handler
  export PYTHONPATH=$PWD
  ./scripts/pytest-clean.sh tests/unit/test_watchdog_log_isolation.py -n0 -q
  ```
  Expect TC1-TC6 and TC8-TC12 to FAIL against the unfixed source. TC7 may pass incidentally; if any
  guard that *should* fail passes, the test does not reach the defect — fix the test before touching
  the source.
- Run the second red probe against the **worktree's** log file:
  ```bash
  mkdir -p logs && touch logs/watchdog.log
  shasum -a 256 logs/watchdog.log
  ./scripts/pytest-clean.sh tests/unit/test_bridge_watchdog.py -n0 -q
  shasum -a 256 logs/watchdog.log
  ```
  Expect the hash to change. Record both hashes and a sample of the synthetic lines.
- Commit the tests alone, red, with the captured output in the commit message.

### 2. Fix `monitoring/bridge_watchdog.py`

- **Task ID**: `build-bridge-fix`
- **Depends On**: `build-red-tests`
- **Validates**: `tests/unit/test_watchdog_log_isolation.py`, `tests/unit/test_bridge_watchdog.py`,
  `tests/integration/test_update_loop_wedge_recovery.py`
- **Assigned To**: `watchdog-logging-builder`
- **Agent Type**: builder
- **Parallel**: false
- Delete lines 78-93. Add `WATCHDOG_LOG_FILE`, the pinned-name `logger` with `setLevel(INFO)` and
  `propagate = False`, and `_configure_logging()` exactly as specified.
- Wire `_configure_logging()` into the `__main__` guard only.
- Convert the two `caplog` sites in `tests/unit/test_bridge_watchdog.py` (:753, :957) and the two in
  `tests/integration/test_update_loop_wedge_recovery.py` (:156, :221) to
  `logger.addHandler(caplog.handler)` in `try/finally`.
- Run both files and confirm green.
- Commit.

### 3. Fix `monitoring/worker_watchdog.py`

- **Task ID**: `build-worker-fix`
- **Depends On**: `build-bridge-fix`
- **Validates**: `tests/unit/test_watchdog_log_isolation.py`, `tests/unit/test_worker_watchdog.py`,
  `tests/integration/test_watchdog_recovery.py`
- **Assigned To**: `watchdog-logging-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add the three module-scope logger lines. Change `_configure_logger()` to return `None`, operate on
  the module-level `logger`, and clear owned-only with `close()`. Keep `LOG_FILE` read inside the
  body. Delete `logger = _configure_logger()` at line 162 and call it from the `__main__` guard.
- Rewrite the three `TestLoggerConfiguration` tests per Test Impact, retaining the handler-count,
  propagate, and idempotence assertions against explicit calls under a monkeypatched `LOG_FILE`.
- Confirm the two `HEARTBEAT_THRESHOLD` reload tests (:1050, :1058) still pass.
- Commit.

### 4. Green demo, mutation check, and real-invocation proof

- **Task ID**: `validate-watchdog-logging`
- **Depends On**: `build-worker-fix`
- **Assigned To**: `watchdog-logging-validator`
- **Agent Type**: validator
- **Parallel**: false
- Re-run both red commands verbatim; capture the green output. The worktree log's sha256 must be
  **identical** before and after `tests/unit/test_bridge_watchdog.py`.
- Real-invocation proof, in the worktree: `python monitoring/bridge_watchdog.py --check-only`, then
  confirm `logs/watchdog.log` grew, the new lines carry the unchanged
  `YYYY-MM-DD HH:MM:SS,mmm [LEVEL] ` prefix, and **each new record appears exactly once**.
- Run the full `tests/unit/` suite once (Risk 3). This is the one run in this plan that is not
  narrowly scoped; run it with `-n0` and expect roughly 20 minutes.
- **Per-guard mutation check (#2658).** One at a time, reintroduce each statement below, confirm the
  named test flips red, then revert. A mutation no test catches means that guard is decorative and
  must be rewritten or removed.

  | Mutation | Must turn red |
  |---|---|
  | `logging.basicConfig(level=logging.INFO, ...)` at bridge module scope | TC1, TC5 |
  | `logger.addHandler(fh)` at bridge module scope | TC2, TC5 |
  | `LOGS_DIR.mkdir(...)` at bridge module scope | TC5 |
  | delete `logger.propagate = False` (bridge) | TC6 |
  | delete `logger.setLevel(logging.INFO)` (bridge) | TC6 |
  | `logging.getLogger().addHandler(fh)` inside `_configure_logging()` | TC8 |
  | delete the owned-only clearing loop (bridge) | TC9 |
  | blanket `for h in list(logger.handlers)` sweep (bridge) | TC12 |
  | restore `logger = _configure_logger()` at worker module scope | TC3, TC4, TC5 |
  | `logging.getLogger().addHandler(fh)` inside `_configure_logger()` | TC10, `test_configure_logger_does_not_touch_root` |
  | delete the owned-only clearing loop (worker) | TC11, `test_logger_no_duplicate_handlers` |
  | change either formatter string | TC7, TC10 |
  | change bridge `maxBytes`/`backupCount` | TC7's handler assertions |
  | change worker `maxBytes`/`backupCount` | TC10 |
- Confirm `shasum -a 256 -c /tmp/watchdog-log-baseline.sha` reports `OK` for both files.

### 5. Documentation

- **Task ID**: `document-feature`
- **Depends On**: `validate-watchdog-logging`
- **Assigned To**: `watchdog-logging-builder`
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/watchdog-log-isolation.md` carrying the Data Flow table; add the
  `docs/features/README.md` row; add the sentence to `docs/features/bridge-self-healing.md`; add the
  test module to `tests/README.md`.
- Commit.

### 6. Final validation and PR body

- **Task ID**: `validate-all`
- **Depends On**: `document-feature`
- **Assigned To**: `watchdog-logging-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table.
- Confirm each Success Criteria checkbox against evidence, not intent.
- Assemble the PR body: red command + verbatim output, green command + verbatim output, the full
  mutation → test mapping table with its measured results, the sha256 pair, **and a top-level
  "Behavior change" section** carrying the Data Flow table, the explicit reading of acceptance
  criterion 3, and the note that uncaught tracebacks are unaffected.

## Verification

Zero-count `grep -c` rows exit 1 on no match. Run them with `|| true`, or outside `set -e`.

| Check | Command | Expected |
|-------|---------|----------|
| No `basicConfig` **call** anywhere in `monitoring/` | `grep -rEn "logging\.basicConfig\(" monitoring/ \| wc -l` | `0`. The open paren is the discriminator: `worker_watchdog.py:138` carries the bare words `logging.basicConfig` in the docstring the plan keeps, so the round-1 form (`grep -rn "logging.basicConfig"`) measured 2 today and 1 after a correct fix, never 0 |
| No module-scope `addHandler` in the bridge | `grep -c "^logger.addHandler\|^_watchdog_file_handler" monitoring/bridge_watchdog.py \|\| true` | `0` |
| No module-scope configure call in the worker | `grep -c "^logger = _configure_logger()" monitoring/worker_watchdog.py \|\| true` | `0` |
| No `getLogger(__name__)` in either watchdog (script-mode name pinning) | `grep -c "getLogger(__name__)" monitoring/bridge_watchdog.py monitoring/worker_watchdog.py \|\| true` | `0` for both |
| Both loggers are non-propagating at module scope | `grep -c "^logger.propagate = False" monitoring/bridge_watchdog.py monitoring/worker_watchdog.py` | `1` for both |
| Isolation tests pass | `PYTHONPATH=$PWD ./scripts/pytest-clean.sh tests/unit/test_watchdog_log_isolation.py -n0 -q` | exit code 0 |
| Bridge watchdog tests pass | `PYTHONPATH=$PWD ./scripts/pytest-clean.sh tests/unit/test_bridge_watchdog.py -n0 -q` | exit code 0 |
| Worker watchdog tests pass | `PYTHONPATH=$PWD ./scripts/pytest-clean.sh tests/unit/test_worker_watchdog.py -n0 -q` | exit code 0 |
| Wedge-recovery caplog tests pass | `PYTHONPATH=$PWD ./scripts/pytest-clean.sh tests/integration/test_update_loop_wedge_recovery.py -n0 -q` | exit code 0 |
| Watchdog-recovery integration passes | `PYTHONPATH=$PWD ./scripts/pytest-clean.sh tests/integration/test_watchdog_recovery.py -n0 -q` | exit code 0 |
| Import is inert (single smoke check outside pytest) | `PYTHONPATH=$PWD python -c "import logging; import monitoring.bridge_watchdog as b, monitoring.worker_watchdog as w; r=logging.getLogger(); assert r.handlers==[] and r.level==logging.WARNING, (r.handlers, r.level); assert all(lg.propagate is False and lg.level==logging.INFO and not lg.handlers for lg in (b.logger, w.logger)); print('IMPORT_INERT')"` | output contains `IMPORT_INERT`. Round 1's two separate inline rows re-implemented TC1-TC4 line for line and are deleted; this is the one standalone smoke check kept |
| Bridge rotation settings preserved | `grep -c "maxBytes=10 \* 1024 \* 1024" monitoring/bridge_watchdog.py` | `> 0` |
| Bridge backup count preserved | `grep -c "backupCount=5" monitoring/bridge_watchdog.py` | `> 0` |
| Worker rotation settings preserved | `grep -c "maxBytes=5 \* 1024 \* 1024" monitoring/worker_watchdog.py` | `> 0` |
| Worker backup count preserved | `grep -c "backupCount=3" monitoring/worker_watchdog.py` | `> 0` |
| Bridge log format preserved | `grep -c "%(asctime)s \[%(levelname)s\] %(message)s" monitoring/bridge_watchdog.py` | `> 0` |
| Worker log format preserved | `grep -c "%(asctime)s \[%(levelname)s\] %(message)s" monitoring/worker_watchdog.py` | `> 0` |
| `--check-only` still works | `PYTHONPATH=$PWD python monitoring/bridge_watchdog.py --check-only` | output contains `Restart circuit open` |
| Anti-criterion: production logs untouched | `shasum -a 256 -c /tmp/watchdog-log-baseline.sha` | `OK` for both files |
| Feature doc exists | `test -f docs/features/watchdog-log-isolation.md && echo DOC_OK` | output contains `DOC_OK` |
| Feature doc indexed | `grep -c "watchdog-log-isolation.md" docs/features/README.md` | `> 0` |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

Round 2 (recorded against plan revision `7897022ec`). Round 1's nine findings were addressed in the
previous revision and are recorded in git history at `7897022ec`. The rows below are round 2 and are
`pending`.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness, History & Consistency | Root is already configured by a module inside the bridge watchdog's own import graph, so every root-state gate in this plan fails on a correct build. `monitoring/bridge_watchdog.py:72` imports `scripts.update.service`, which reaches `scripts/update/__init__.py:10` → `scripts/update/run.py:24` → `scripts/update/log_cleanup.py:17` (`from scripts import log_rotate`) → `scripts/log_rotate.py:62` `logging.basicConfig(level=INFO, stream=sys.stderr)`. That runs BEFORE `bridge_watchdog.py:78`, so today's `basicConfig` at `:78` is already a no-op and deleting it changes nothing about root. Measured: after `import monitoring.bridge_watchdog`, root holds one `StreamHandler <stderr>` whose formatter is log_rotate's `%(asctime)s %(levelname)s %(message)s`, not the watchdog's bracketed format, and root level is 20 (INFO). Consequently TC1, the `IMPORT_INERT` Verification row, and Success Criterion 2 assert `root.handlers == []` and `root.level == WARNING` and will all FAIL after the fix lands. Task 4's mutation row "`logging.basicConfig(...)` at bridge module scope → TC1, TC5 must turn red" is half-decorative: reintroducing `basicConfig` cannot turn TC1 red, because `basicConfig` is a no-op once root has a handler. `monitoring/worker_watchdog.py` is unaffected — a spy probe on its import records zero `basicConfig` calls and a clean root. | pending | Re-aim every bridge-side root assertion from absolute state to a delta this module owns. In TC1, import `scripts.log_rotate` (or `scripts.update.service`) FIRST inside the subprocess, snapshot `list(logging.getLogger().handlers)` and `logging.getLogger().level`, THEN `import monitoring.bridge_watchdog`, and assert both are byte-identical afterwards. Assert additionally that no root handler is `_watchdog_owned` and that no root handler's formatter `_fmt` equals `"%(asctime)s [%(levelname)s] %(message)s"` — that formatter string is the discriminator that survives the `basicConfig` no-op and is what a relapse would actually attach. Drop `root.handlers == []` / `root.level == WARNING` from TC1, `IMPORT_INERT`, and Success Criterion 2 for the bridge; TC3/TC4 may keep the absolute form since the worker's import graph is genuinely clean. In Task 4's mutation table, replace the `basicConfig at bridge module scope → TC1` cell with `→ TC5 only`, and add a mutation that CAN fire in-process: `logging.getLogger().addHandler(fh)` inside `_configure_logging()` → TC8. |
| BLOCKER | History & Consistency, Scope & Value | The No-Go anti-criterion gate `shasum -a 256 -c /tmp/watchdog-log-baseline.sha` records RELATIVE paths and is therefore resolved against the caller's cwd, so it does not protect the checkout the plan names. Task 1 writes the baseline in the MAIN checkout via `shasum -a 256 logs/watchdog.log logs/worker_watchdog.log`, which stores the literal strings `logs/watchdog.log` and `logs/worker_watchdog.log`. Every other Verification row is prefixed `PYTHONPATH=$PWD ./scripts/pytest-clean.sh`, i.e. run from the worktree, where `shasum -c` re-resolves those relative paths against the WORKTREE's `logs/` — the directory Task 1 deliberately writes to (`mkdir -p logs && touch logs/watchdog.log`, then a full `test_bridge_watchdog.py` run). Verified: writing a baseline in one directory and running `shasum -a 256 -c` from a sibling directory reports `FAILED` and exits 1. `logs/worker_watchdog.log` does not exist in a fresh worktree at all, so that entry reports "FAILED open or read". The row fails on a correct build, and the main checkout it claims to protect is never actually checked — the same permanently-misaimed shape as round 1's deleted `git diff \| grep "^logs/"` row. | pending | Record the baseline with ABSOLUTE paths so `shasum -c` is cwd-independent: `shasum -a 256 "$AI_MAIN/logs/watchdog.log" "$AI_MAIN/logs/worker_watchdog.log" > /tmp/watchdog-log-baseline.sha` where `AI_MAIN=$(git -C /Users/tomcounsell/src/ai rev-parse --show-toplevel)` captured in the MAIN checkout before the worktree is created. `shasum` stores whatever path string it is handed, so absolute in means absolute out and the check then resolves to the main checkout from any cwd. Guard against the empty-baseline failure too: assert the `.sha` file has exactly 2 lines before trusting an `OK`, since `shasum -c` on an empty file exits 0 and prints nothing. |
| BLOCKER | Risk & Robustness | The real-invocation proof cannot fire: `--check-only` emits no log record on the machine the build runs on, so `logs/watchdog.log` will not grow. `monitoring/bridge_watchdog.py:982` returns from the `--check-only` branch before `run_health_check()` (`:860`) is ever reached, and every INFO-or-above `logger` call in that branch's only callee `check_bridge_health()` (`:514`) is conditional on state this machine does not have: `:591` fires only when `active_claude_count > SOFT_INSTANCE_LIMIT`, `:604` and the whole `assess_update_flow` block are gated on `if running`, and `kill_zombie_processes()`'s INFO lines at `:482`/`:494`/`:500` require `running and logs_fresh`. With the bridge down (the plan's own Data Flow section states `com.valor.bridge-watchdog` is not installed here) the branch emits at most DEBUG, which INFO filters out. `_configure_logging()` opens the file in append mode and writes zero bytes. So Success Criterion 5 ("`--check-only` still writes to `logs/watchdog.log`") and Task 4's "confirm `logs/watchdog.log` grew ... and each new record appears exactly once" cannot pass. This also contradicts the plan's own Failure Path Test Strategy, which states `--check-only` "renders its report with `print()` to stdout and does not depend on logging configuration at all." Separately, "each new record appears exactly once" proves nothing about the defect even if a record did appear: the second copy comes from the plist's `StandardErrorPath` redirect under launchd, which a shell invocation does not have. | pending | Replace `--check-only` with an invocation that actually reaches the logging path. Use `python -c "import runpy, sys; sys.argv=['bridge_watchdog.py']; runpy.run_path('monitoring/bridge_watchdog.py', run_name='__main__')"` from the worktree, which takes the `else` branch at `:1031` and calls `run_health_check()` → `:932` `logger.warning(f"Bridge unhealthy: ...")` fires unconditionally when the bridge is down, producing at least one formatted line. Assert the new line matches `^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \[(WARNING\|CRITICAL\|INFO)\] `. Keep `--check-only` as a separate no-crash smoke check with no assertion about file growth, and reword Success Criterion 5 to name the entry-point invocation rather than `--check-only`. Drop the "exactly once" clause from the shell-invocation proof and state plainly that plist doubling is only observable under launchd, so the de-duplication claim rests on the plist text plus the `logs/worker_watchdog.log` control, not on a local run. |
| CONCERN | Risk & Robustness | The Data Flow "Copies after this change" column and the whole `logging.lastResort` argument are wrong for the bridge watchdog process, and Task 6 requires that table to ship verbatim in the PR body as the stated behavior change. `lastResort` fires only when a record reaches a logger with NO handler anywhere in its ancestry, but root retains log_rotate's `StreamHandler` at level INFO in this process (see BLOCKER 1). So after the change: submodule WARNING+ records still arrive FORMATTED through root's handler rather than "unformatted via lastResort", and submodule INFO/DEBUG records still arrive (1 copy, not 0) because root stays at INFO. Risk 3's premise ("Root returns to its WARNING default in every process that imports either module") is false for the bridge. The change is therefore strictly smaller and safer than the plan advertises, but the PR body would ship a false table. | pending | Re-derive rows 2 and 3 of the Data Flow table from the measured root state rather than from the assumption of an empty root. Row 2 becomes "1 copy, formatted with log_rotate's `%(asctime)s %(levelname)s %(message)s`"; row 3 becomes "1 copy" rather than 0. Delete the `lastResort` bullet from Research, from Solution evidence item 5, and from Risk 1's mitigation, and replace the "What an operator will see change" second bullet with the single true statement: own-records go from 2 copies to 1, and nothing else about the file changes. Reduce Risk 3 to a worker-only note, or delete it. |
| CONCERN | Risk & Robustness | The TC1-TC4 subprocess probes have no pinned interpreter, cwd, or environment, and `scripts/pytest-clean.sh` sets no `PYTHONPATH` (verified: zero matches in that script). The plan relies on a human `export PYTHONPATH=$PWD` in every worktree shell. A probe launched without it inherits the shared venv's `.pth`, silently imports the MAIN checkout's `monitoring/` package, and reports green about unmodified source — the worktree-isolation failure mode this repo has already been bitten by. | pending | Build the subprocess env explicitly rather than inheriting: `env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}` where `REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]`, and pass `cwd=REPO_ROOT` plus `sys.executable` to `subprocess.run`. Then add a self-check as the probe's first statement so a wrong-checkout import fails loudly instead of passing green: `import monitoring.bridge_watchdog as m; assert m.__file__.startswith(str(REPO_ROOT)), m.__file__`. |
| CONCERN | History & Consistency | The rewritten `test_logger_no_duplicate_handlers` asserts ZERO handlers on `wwd.logger` after `importlib.reload(wwd)`, which couples the test to whole-process ordering. `logging.getLogger("monitoring.worker_watchdog")` is a process-global singleton and `importlib.reload` does not reset its `handlers` list, so any earlier test in the same xdist worker that left a handler attached (a leaked `caplog.handler` from one of the ten sites at `tests/unit/test_worker_watchdog.py:275,372,402,712,732,749,773,802,826,886`, or a `_configure_logger()` call from TC10/TC11 in the new module) makes the count non-zero for reasons unrelated to the code under test. It fails far from its cause, which is the flake shape the plan already rejects in its Rabbit Holes. | pending | Assert on ownership rather than on the total count: `assert not [h for h in wwd.logger.handlers if getattr(h, "_watchdog_owned", False)]` after the reload, and keep the exact-one assertion only for the paired explicit calls (`_configure_logger()` twice under a monkeypatched `LOG_FILE`, then `len([h for h in wwd.logger.handlers if getattr(h, "_watchdog_owned", False)]) == 1`). Apply the same ownership-scoped form to the bridge in TC9. |
| NIT | Scope & Value | TC2's predicate "no handler whose `baseFilename` ends `watchdog.log`" also matches `worker_watchdog.log`, so TC2 and TC4 overlap and TC2 would misattribute a worker-side leak to the bridge. | pending | Compare the basename exactly: `pathlib.Path(h.baseFilename).name == "watchdog.log"` in TC2 and `== "worker_watchdog.log"` in TC4, rather than `str.endswith`. |
| NIT | History & Consistency | The Freshness Check states the scope-aware AST scan "reports module-scope hits at exactly `78 logging.basicConfig`, `86 LOGS_DIR.mkdir`, `93 logger.addHandler` and nowhere else in the package", but TC5's walk also looks for module-scope `_configure_logger` / `_configure_logging` calls, so the walk as specified additionally reports `monitoring/worker_watchdog.py:162`. Independently re-run and confirmed: the scan returns exactly those four hits across all thirteen `monitoring/*.py` files. Harmless (the fix deletes `:162` too, so TC5 still goes green) but the two sections describe different scans. | pending | Add `monitoring/worker_watchdog.py:162 _configure_logger` to the Freshness Check bullet's enumerated hits so it matches TC5's stated predicate. |

