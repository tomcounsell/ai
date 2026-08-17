# Watchdog Log Isolation

Issue [#2643](https://github.com/tomcounsell/ai/issues/2643).

## The invariant

Importing `monitoring/bridge_watchdog.py`, `monitoring/worker_watchdog.py`, or
`scripts/log_rotate.py` has **zero logging side effects**: no handler on the
root logger, no handler holding a production log file, no directory created.
Logging configuration happens exactly once, at each script's entry point (its
`if __name__ == "__main__":` guard), where launchd reaches it.

Before this fix, all three modules attached handlers or called
`logging.basicConfig(...)` at **module scope** — i.e. on import. Every pytest
process that collected `tests/unit/test_bridge_watchdog.py` or
`tests/integration/test_update_loop_wedge_recovery.py` turned the bridge's
rotating file handler live and wrote genuine-looking `CRITICAL` lines into the
operator's real `logs/watchdog.log`. The #2418 investigation lost real time to
this: 36 circuit-open timestamps spread across 11 days initially read as a
recurring production livelock, and only a cross-reference against
`data/crash_history.jsonl` and `launchctl list` established that everything
after a given date was test output.

## Why all three modules, not two

`monitoring/bridge_watchdog.py` imports `scripts.update.service`, whose import
chain reaches `scripts/log_rotate.py`, which called `logging.basicConfig()` at
module scope. That call configures the **root** logger — so even after the two
watchdog modules stopped configuring root themselves, importing the bridge
watchdog still dirtied root transitively through `log_rotate`. Fixing two of
three would have made the isolation guard's absolute form
(`root.handlers == []`) permanently unsatisfiable and forced every future
reader back into an attributive proof ("assume the bridge is clean modulo
log_rotate"). All three are fixed in the same PR so the invariant is provable
in its plain form.

## Two different propagation decisions, one plist-driven rule

| | `monitoring/bridge_watchdog.py` + `monitoring/worker_watchdog.py` | `scripts/log_rotate.py` |
|---|---|---|
| What the entry point configures | the module's **own** named logger (a `RotatingFileHandler`) | **root** (`logging.basicConfig`) |
| Plist stream routing | `StandardOutPath` **and** `StandardErrorPath` → the *same* file | `StandardOutPath` → `logs/log_rotate.log`, `StandardErrorPath` → `logs/log_rotate_error.log` — two *different* files |
| Effect of `propagate = True` | a second write path into the same file → every record doubled | the **only** path from the module's records to its handler |
| Decision | `propagate = False` | `propagate = True` (the default, left in place) |

The two decisions look contradictory and are not — they follow directly from
which logger each module configures, which in turn follows from its plist.
`log_rotate` configures root, so its own logger must keep propagating to
reach it; measured directly, adding `propagate = False` to a fixed
`log_rotate` in script mode produces **no output whatsoever** (exit 0, empty
stderr, both INFO lines gone) — the records reach a logger with no handlers,
propagation is off, and `logging.lastResort` sits at WARNING, above every line
`main()` emits on a healthy run. `tests/unit/test_watchdog_log_isolation.py`
(TC14) pins `propagate is True` as a regression guard against exactly this
cargo-cult mistake.

## Data Flow: what changes in `logs/watchdog.log`

The bridge watchdog's plist (`scripts/valor-service.sh`) points both
`StandardOutPath` and `StandardErrorPath` at `logs/watchdog.log`. Combined
with the old module-scope configuration, a single launchd tick produced:

| Record source | Path to `logs/watchdog.log` before | Copies before | After this change |
|---|---|---|---|
| `monitoring.bridge_watchdog` own records | `RotatingFileHandler` **and** (via `propagate = True`) root's `StreamHandler` → stderr → plist | **2** | **1** — `RotatingFileHandler` only, formatter unchanged |
| Submodule records at WARNING+ (`monitoring.crash_tracker`, `scripts.update.service`, …) | root's `StreamHandler` → stderr → plist, formatted `%(asctime)s %(levelname)s %(message)s` | 1 | **1, via `logging.lastResort` → stderr → plist, UNFORMATTED** (bare message, no timestamp, no level tag) |
| Submodule records at INFO | root's `StreamHandler` (root at INFO) → stderr → plist | 1 | **0 — dropped at the call site.** `lastResort` is at WARNING and root is empty |
| Submodule records at DEBUG | dropped by root's INFO level | 0 | 0 (unchanged) |
| Uncaught interpreter tracebacks | interpreter → stderr → plist | 1 | 1 (unchanged) |

**Three rows change, two of them are losses.** The watchdog's own records
halve (the point of the change). Submodule WARNING+ records survive but lose
their timestamp and level prefix. Submodule INFO records stop reaching the
file entirely.

**The INFO-record loss is a retention win, not a regression.** Measured on
this machine's production `logs/watchdog.log` (102,627 lines spanning
2026-07-22 to 2026-08-16): 22,230 of 24,144 submodule-INFO-class lines were
the single record `INFO libssl detected, it will be used for encryption`,
emitted once per 60-second watchdog tick by a transitively imported crypto
library. That one record was the dominant term in the file's ~145 KB/day
growth, which was rolling genuine incident history out of the 5-backup
retention budget. Dropping it is expected to shrink `logs/watchdog.log` by
well over 90% in line count while **increasing** genuine retention — an
operator seeing that drop should read it as intended, not as a stopped
watchdog.

The doubling was also measured directly: parsing all 102,627 lines of this
machine's live `logs/watchdog.log` and matching bracketed lines against bare
lines on the exact `(timestamp, level, message)` triple found 38,706
own-records present in **both** formats — the `RotatingFileHandler` copy and
the root-`StreamHandler`-via-plist copy, side by side.

`logs/worker_watchdog.log` and `logs/log_rotate_error.log` are unaffected:
the worker watchdog has been `propagate = False` since #1311 and its table
was already correct; `log_rotate`'s script-mode output is byte-shape-identical
before and after, since `basicConfig`'s `level`, `format`, and `stream` are
preserved verbatim, only relocated.

## The 14-file AST guard, and what it deliberately does not cover

`tests/unit/test_watchdog_log_isolation.py::test_tc5_no_module_scope_logging_side_effects`
walks every `monitoring/*.py` file plus `scripts/log_rotate.py` (a dynamic
glob, asserted for coverage rather than a frozen file count, so a future
clean `monitoring/*.py` addition is covered automatically rather than
convicted for existing) looking for module-scope calls to
`logging.basicConfig`, `.addHandler`, `.mkdir`, `_configure_logger`, or
`_configure_logging`. It descends into module-level `try`/`with` bodies and
into every module-level `if` **except** the `if __name__ == "__main__":`
guard — that guard is where the fix intentionally relocates every one of
those calls, and a companion walker (TC5b) asserts each relocated call is a
**direct child** of that guard, not buried inside `main()`.

The guard does not cover: function-body logging calls (any `logger.info(...)`
inside a function is legitimate emission, not configuration), non-`monitoring`
modules outside `scripts/log_rotate.py`, or configuration performed by a
*caller* of these modules (e.g. `scripts/update/run.py`, deliberately out of
scope — see below).

## What stays out of scope

**`scripts/update/run.py` has no entry-point logging configuration of its
own.** It never called `logging.basicConfig`; its three
`logging.getLogger(__name__).warning(...)` calls were formatted only because
`scripts/log_rotate.py`'s import happened to configure root as a side effect.
After this fix those three warnings reach `logging.lastResort` and print bare
(no timestamp, no level tag) — a real, measured, deliberately-undone
consequence. Giving `run.py` its own entry-point configuration is tracked
separately in [#2678](https://github.com/tomcounsell/ai/issues/2678), which
this PR narrows to exactly that remainder.

No behavioral change was made to recovery, escalation, health-check, or
rotation logic — `LOG_MAX_SIZE`, `LOG_MAX_BACKUPS`, `LOG_BACKUP_HARD_CAP`, and
`SELF_EXCLUDED_FILES` in `scripts/log_rotate.py` are untouched, and rotation
settings (`maxBytes`/`backupCount`) are unchanged on both watchdogs.
